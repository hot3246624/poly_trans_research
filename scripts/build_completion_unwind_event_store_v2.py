#!/usr/bin/env python3
"""Build completion/unwind event store V2 with L1 delta fields.

The store is intentionally event-level, not strategy-level.  It exposes market
states where an inventory controller could buy, sell, complete, or unwind using
only book/trade facts that were visible at ``ts_ms``.  It does not simulate our
private queue position.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import fcntl
import json
import math
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


DEFAULT_STORE_NAME = "completion_unwind_event_store_v2"
DEFAULT_CLIP_TIERS = (10.0, 25.0, 60.0, 100.0, 250.0)
TRUSTED_START_MS = 1_777_274_700_000
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Market:
    condition_id: str
    slug: str
    start_ms: int
    end_ms: int
    winner_side: str


@dataclass(frozen=True)
class L1Book:
    row_id: int
    recv_ms: int
    capture_seq: int
    yes_bid_px: float | None
    yes_ask_px: float | None
    no_bid_px: float | None
    no_ask_px: float | None
    yes_bid_sz: float | None
    yes_ask_sz: float | None
    no_bid_sz: float | None
    no_ask_sz: float | None


@dataclass(frozen=True)
class L2Book:
    row_id: int
    recv_ms: int
    capture_seq: int
    side: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class TradeEvent:
    row_id: int
    ts_ms: int
    recv_ms: int | None
    capture_seq: int | None
    side: str
    taker_side: str
    price: float
    size: float


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def safe_name(value: float) -> str:
    text = f"{value:g}".replace(".", "_")
    return text


def parse_days(value: str) -> list[str]:
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    return days


def parse_floats(value: str) -> list[float]:
    out = [float(part.strip()) for part in value.split(",") if part.strip()]
    if not out:
        raise ValueError("at least one float value is required")
    return out


def parse_kinds(value: str) -> set[str]:
    out = {part.strip() for part in value.split(",") if part.strip()}
    allowed = {"public_trade", "l1_price_change"}
    unknown = out - allowed
    if unknown:
        raise ValueError(f"unknown event kinds: {sorted(unknown)}")
    if not out:
        raise ValueError("at least one event kind is required")
    return out


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def load_sqlite_sequence(path: Path) -> dict[str, int]:
    with connect_ro(path) as conn:
        rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {str(name): int(seq) for name, seq in rows}


def side_px(book: L1Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_px if kind == "bid" else book.yes_ask_px
    return book.no_bid_px if kind == "bid" else book.no_ask_px


def side_sz(book: L1Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_sz if kind == "bid" else book.yes_ask_sz
    return book.no_bid_sz if kind == "bid" else book.no_ask_sz


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def mid(book: L1Book, side: str) -> float | None:
    bid = side_px(book, side, "bid")
    ask = side_px(book, side, "ask")
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def high_side(book: L1Book) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def side_alignment(book: L1Book, side: str) -> str | None:
    high = high_side(book)
    if high is None:
        return None
    return "high" if side == high else "low"


def l1_delta_fields(prev: L1Book | None, cur: L1Book, side: str) -> dict[str, Any]:
    """Return side-local adjacent-L1 deltas for the current strict L1 row."""
    fields: dict[str, Any] = {
        "prev_side_bid": None,
        "prev_side_bid_sz": None,
        "prev_side_ask": None,
        "prev_side_ask_sz": None,
        "side_bid_delta_qty": None,
        "side_bid_level_drop_qty": None,
        "side_ask_delta_qty": None,
        "side_ask_level_lift_qty": None,
        "book_update_reason": "initial" if prev is None else "unknown",
    }
    if prev is None:
        return fields

    prev_bid = side_px(prev, side, "bid")
    prev_bid_sz = side_sz(prev, side, "bid")
    prev_ask = side_px(prev, side, "ask")
    prev_ask_sz = side_sz(prev, side, "ask")
    cur_bid = side_px(cur, side, "bid")
    cur_bid_sz = side_sz(cur, side, "bid")
    cur_ask = side_px(cur, side, "ask")
    cur_ask_sz = side_sz(cur, side, "ask")

    bid_same_price_decrease = 0.0
    if prev_bid is not None and cur_bid is not None and float(prev_bid) == float(cur_bid):
        bid_same_price_decrease = max(float(prev_bid_sz or 0.0) - float(cur_bid_sz or 0.0), 0.0)

    ask_same_price_decrease = 0.0
    if prev_ask is not None and cur_ask is not None and float(prev_ask) == float(cur_ask):
        ask_same_price_decrease = max(float(prev_ask_sz or 0.0) - float(cur_ask_sz or 0.0), 0.0)

    bid_level_drop_qty = 0.0
    if prev_bid is not None and (cur_bid is None or float(cur_bid) < float(prev_bid)):
        bid_level_drop_qty = float(prev_bid_sz or 0.0)

    ask_level_lift_qty = 0.0
    if prev_ask is not None and (cur_ask is None or float(cur_ask) > float(prev_ask)):
        ask_level_lift_qty = float(prev_ask_sz or 0.0)

    reason = "unknown"
    if bid_level_drop_qty > 0:
        reason = "level_drop"
    elif ask_level_lift_qty > 0:
        reason = "level_lift"
    elif prev_bid != cur_bid or prev_ask != cur_ask:
        reason = "price_change"
    elif (
        prev_bid_sz != cur_bid_sz
        or prev_ask_sz != cur_ask_sz
        or side_sz(prev, other(side), "bid") != side_sz(cur, other(side), "bid")
        or side_sz(prev, other(side), "ask") != side_sz(cur, other(side), "ask")
    ):
        reason = "size_change"

    return {
        "prev_side_bid": prev_bid,
        "prev_side_bid_sz": prev_bid_sz,
        "prev_side_ask": prev_ask,
        "prev_side_ask_sz": prev_ask_sz,
        "side_bid_delta_qty": bid_same_price_decrease,
        "side_bid_level_drop_qty": bid_level_drop_qty,
        "side_ask_delta_qty": ask_same_price_decrease,
        "side_ask_level_lift_qty": ask_level_lift_qty,
        "book_update_reason": reason,
    }


def levels_from_row(row: sqlite3.Row, prefix: str) -> tuple[tuple[float, float], ...]:
    levels: list[tuple[float, float]] = []
    for idx in range(1, 6):
        px = row[f"{prefix}{idx}_px"]
        sz = row[f"{prefix}{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


def sweep_vwap(levels: Iterable[tuple[float, float]], target_size: float) -> tuple[bool, float | None, float, float | None]:
    remaining = float(target_size)
    filled = 0.0
    notional = 0.0
    worst_px: float | None = None
    for px, sz in levels:
        take = min(float(sz), remaining)
        if take <= 0:
            continue
        filled += take
        notional += float(px) * take
        worst_px = float(px)
        remaining -= take
        if remaining <= 1e-9:
            return True, notional / filled, filled, worst_px
    return False, None, filled, worst_px


def latest_by_time(items: list[Any], times: list[int], ts_ms: int, max_age_ms: int | None = None) -> tuple[Any | None, int | None]:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None, None
    item = items[idx]
    recv_ms = int(item.recv_ms)
    age_ms = ts_ms - recv_ms
    if age_ms < 0:
        return None, None
    if max_age_ms is not None and age_ms > max_age_ms:
        return None, age_ms
    return item, age_ms


def fetch_markets(conn: sqlite3.Connection, max_markets: int | None) -> list[Market]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol='BTC'
          AND m.interval_sec=300
          AND s.winner_side IN ('YES', 'NO')
        ORDER BY m.start_ms, m.condition_id
        """
    ).fetchall()
    markets: list[Market] = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        markets.append(
            Market(
                condition_id=str(row["condition_id"]),
                slug=str(row["slug"]),
                start_ms=start_ms,
                end_ms=end_ms,
                winner_side=str(row["winner_side"]),
            )
        )
        if max_markets is not None and len(markets) >= max_markets:
            break
    return markets


def load_l1(conn: sqlite3.Connection, market: Market) -> list[L1Book]:
    rows = conn.execute(
        """
        SELECT id, recv_ms, capture_seq,
               yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, capture_seq, id
        """,
        (market.condition_id, max(market.start_ms, TRUSTED_START_MS), market.end_ms),
    ).fetchall()
    return [
        L1Book(
            row_id=int(row["id"]),
            recv_ms=int(row["recv_ms"]),
            capture_seq=int(row["capture_seq"]),
            yes_bid_px=row["yes_bid_px"],
            yes_ask_px=row["yes_ask_px"],
            no_bid_px=row["no_bid_px"],
            no_ask_px=row["no_ask_px"],
            yes_bid_sz=row["yes_bid_sz"],
            yes_ask_sz=row["yes_ask_sz"],
            no_bid_sz=row["no_bid_sz"],
            no_ask_sz=row["no_ask_sz"],
        )
        for row in rows
    ]


def load_l2(conn: sqlite3.Connection, market: Market) -> dict[str, list[L2Book]]:
    out = {"YES": [], "NO": []}
    rows = conn.execute(
        """
        SELECT id, recv_ms, capture_seq, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms <= ?
          AND market_side IN ('YES', 'NO')
        ORDER BY market_side, recv_ms, capture_seq, id
        """,
        (market.condition_id, max(market.start_ms, TRUSTED_START_MS), market.end_ms),
    )
    for row in rows:
        side = str(row["market_side"])
        out[side].append(
            L2Book(
                row_id=int(row["id"]),
                recv_ms=int(row["recv_ms"]),
                capture_seq=int(row["capture_seq"]),
                side=side,
                bids=levels_from_row(row, "bid"),
                asks=levels_from_row(row, "ask"),
            )
        )
    return out


def load_trades(conn: sqlite3.Connection, market: Market) -> list[TradeEvent]:
    rows = conn.execute(
        """
        SELECT id, trade_ts_ms, recv_ms, capture_seq, market_side, taker_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND market_side IN ('YES', 'NO')
          AND taker_side IN ('BUY', 'SELL')
        ORDER BY trade_ts_ms, id
        """,
        (market.condition_id, max(market.start_ms, TRUSTED_START_MS), market.end_ms),
    ).fetchall()
    return [
        TradeEvent(
            row_id=int(row["id"]),
            ts_ms=int(row["trade_ts_ms"]),
            recv_ms=None if row["recv_ms"] is None else int(row["recv_ms"]),
            capture_seq=None if row["capture_seq"] is None else int(row["capture_seq"]),
            side=str(row["market_side"]),
            taker_side=str(row["taker_side"]),
            price=float(row["price"]),
            size=float(row["size"]),
        )
        for row in rows
    ]


def l1_price_change_events(l1_books: list[L1Book]) -> Iterable[tuple[int, str, L1Book]]:
    previous: L1Book | None = None
    for book in l1_books:
        changed = previous is None
        if previous is not None:
            changed = any(
                side_px(book, side, kind) != side_px(previous, side, kind)
                for side in ("YES", "NO")
                for kind in ("bid", "ask")
            )
        if changed:
            yield book.recv_ms, "YES", book
            yield book.recv_ms, "NO", book
        previous = book


def base_row(
    *,
    day: str,
    market: Market,
    event_kind: str,
    event_id: int | None,
    ts_ms: int,
    side: str,
    l1: L1Book,
    prev_l1: L1Book | None,
    l1_age_ms: int,
    public_trade: TradeEvent | None,
) -> dict[str, Any]:
    opp = other(side)
    side_bid = side_px(l1, side, "bid")
    side_ask = side_px(l1, side, "ask")
    opp_bid = side_px(l1, opp, "bid")
    opp_ask = side_px(l1, opp, "ask")
    side_bid_sz = side_sz(l1, side, "bid")
    side_ask_sz = side_sz(l1, side, "ask")
    opp_bid_sz = side_sz(l1, opp, "bid")
    opp_ask_sz = side_sz(l1, opp, "ask")
    row = {
        "day": day,
        "event_kind": event_kind,
        "event_id": event_id,
        "ts_ms": ts_ms,
        "ts_iso": iso_ms(ts_ms),
        "condition_id": market.condition_id,
        "slug": market.slug,
        "market_start_ms": market.start_ms,
        "market_end_ms": market.end_ms,
        "offset_s": round((ts_ms - market.start_ms) / 1000.0, 3),
        "side": side,
        "opposite_side": opp,
        "winner_side": market.winner_side,
        "side_is_winner": side == market.winner_side,
        "side_alignment": side_alignment(l1, side),
        "high_side": high_side(l1),
        "strict_l1_row_id": l1.row_id,
        "strict_l1_recv_ms": l1.recv_ms,
        "strict_l1_age_ms": l1_age_ms,
        "side_bid": side_bid,
        "side_ask": side_ask,
        "side_bid_sz": side_bid_sz,
        "side_ask_sz": side_ask_sz,
        "opp_bid": opp_bid,
        "opp_ask": opp_ask,
        "opp_bid_sz": opp_bid_sz,
        "opp_ask_sz": opp_ask_sz,
        "l1_pair_ask": None if side_ask is None or opp_ask is None else float(side_ask) + float(opp_ask),
        "l1_pair_bid": None if side_bid is None or opp_bid is None else float(side_bid) + float(opp_bid),
        "public_trade_row_id": None if public_trade is None else public_trade.row_id,
        "public_trade_taker_side": None if public_trade is None else public_trade.taker_side,
        "public_trade_price": None if public_trade is None else public_trade.price,
        "public_trade_size": None if public_trade is None else public_trade.size,
        "public_trade_recv_ms": None if public_trade is None else public_trade.recv_ms,
    }
    row.update(l1_delta_fields(prev_l1, l1, side))
    return row


def add_l2_features(row: dict[str, Any], l2: L2Book | None, l2_age_ms: int | None, clip_tiers: list[float]) -> None:
    row["strict_l2_row_id"] = None if l2 is None else l2.row_id
    row["strict_l2_recv_ms"] = None if l2 is None else l2.recv_ms
    row["strict_l2_age_ms"] = l2_age_ms
    row["buy_best_px"] = None if l2 is None or not l2.asks else l2.asks[0][0]
    row["buy_best_sz"] = None if l2 is None or not l2.asks else l2.asks[0][1]
    row["buy_available_qty"] = None if l2 is None else sum(sz for _px, sz in l2.asks)
    row["sell_best_px"] = None if l2 is None or not l2.bids else l2.bids[0][0]
    row["sell_best_sz"] = None if l2 is None or not l2.bids else l2.bids[0][1]
    row["sell_available_qty"] = None if l2 is None else sum(sz for _px, sz in l2.bids)
    for clip in clip_tiers:
        name = safe_name(clip)
        if l2 is None:
            buy_full, buy_vwap, buy_filled, buy_worst = False, None, 0.0, None
            sell_full, sell_vwap, sell_filled, sell_worst = False, None, 0.0, None
        else:
            buy_full, buy_vwap, buy_filled, buy_worst = sweep_vwap(l2.asks, clip)
            sell_full, sell_vwap, sell_filled, sell_worst = sweep_vwap(l2.bids, clip)
        row[f"buy_full_{name}"] = buy_full
        row[f"buy_vwap_{name}"] = buy_vwap
        row[f"buy_filled_{name}"] = buy_filled
        row[f"buy_worst_px_{name}"] = buy_worst
        row[f"sell_full_{name}"] = sell_full
        row[f"sell_vwap_{name}"] = sell_vwap
        row[f"sell_filled_{name}"] = sell_filled
        row[f"sell_worst_px_{name}"] = sell_worst


def fieldnames(clip_tiers: list[float]) -> list[str]:
    fields = [
        "day",
        "event_kind",
        "event_id",
        "ts_ms",
        "ts_iso",
        "condition_id",
        "slug",
        "market_start_ms",
        "market_end_ms",
        "offset_s",
        "side",
        "opposite_side",
        "winner_side",
        "side_is_winner",
        "side_alignment",
        "high_side",
        "strict_l1_row_id",
        "strict_l1_recv_ms",
        "strict_l1_age_ms",
        "strict_l2_row_id",
        "strict_l2_recv_ms",
        "strict_l2_age_ms",
        "side_bid",
        "side_ask",
        "side_bid_sz",
        "side_ask_sz",
        "prev_side_bid",
        "prev_side_bid_sz",
        "prev_side_ask",
        "prev_side_ask_sz",
        "side_bid_delta_qty",
        "side_bid_level_drop_qty",
        "side_ask_delta_qty",
        "side_ask_level_lift_qty",
        "book_update_reason",
        "opp_bid",
        "opp_ask",
        "opp_bid_sz",
        "opp_ask_sz",
        "l1_pair_ask",
        "l1_pair_bid",
        "buy_best_px",
        "buy_best_sz",
        "buy_available_qty",
        "sell_best_px",
        "sell_best_sz",
        "sell_available_qty",
        "public_trade_row_id",
        "public_trade_taker_side",
        "public_trade_price",
        "public_trade_size",
        "public_trade_recv_ms",
    ]
    for clip in clip_tiers:
        name = safe_name(clip)
        fields.extend(
            [
                f"buy_full_{name}",
                f"buy_vwap_{name}",
                f"buy_filled_{name}",
                f"buy_worst_px_{name}",
                f"sell_full_{name}",
                f"sell_vwap_{name}",
                f"sell_filled_{name}",
                f"sell_worst_px_{name}",
            ]
        )
    return fields


def emit_market_rows(
    *,
    writer: csv.DictWriter[str],
    day: str,
    market: Market,
    l1_books: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    trades: list[TradeEvent],
    event_kinds: set[str],
    clip_tiers: list[float],
    max_l2_age_ms: int,
) -> dict[str, int]:
    counts = {"l1_price_change": 0, "public_trade": 0, "missing_l1": 0, "missing_l2": 0}
    if not l1_books:
        return counts
    l1_times = [book.recv_ms for book in l1_books]
    prev_l1_by_row_id = {book.row_id: (l1_books[idx - 1] if idx > 0 else None) for idx, book in enumerate(l1_books)}
    l2_times = {side: [book.recv_ms for book in books] for side, books in l2_by_side.items()}

    def write_event(event_kind: str, event_id: int | None, ts_ms: int, side: str, l1: L1Book, l1_age_ms: int, trade: TradeEvent | None) -> None:
        l2, l2_age = latest_by_time(l2_by_side[side], l2_times[side], ts_ms, max_l2_age_ms)
        prev_l1 = prev_l1_by_row_id.get(l1.row_id)
        row = base_row(
            day=day,
            market=market,
            event_kind=event_kind,
            event_id=event_id,
            ts_ms=ts_ms,
            side=side,
            l1=l1,
            prev_l1=prev_l1,
            l1_age_ms=l1_age_ms,
            public_trade=trade,
        )
        if l2 is None:
            counts["missing_l2"] += 1
        add_l2_features(row, l2, l2_age, clip_tiers)
        writer.writerow(row)
        counts[event_kind] += 1

    if "l1_price_change" in event_kinds:
        for ts_ms, side, l1 in l1_price_change_events(l1_books):
            write_event("l1_price_change", l1.row_id, ts_ms, side, l1, 0, None)

    if "public_trade" in event_kinds:
        for trade in trades:
            l1, l1_age = latest_by_time(l1_books, l1_times, trade.ts_ms, None)
            if l1 is None:
                counts["missing_l1"] += 1
                continue
            write_event("public_trade", trade.row_id, trade.ts_ms, trade.side, l1, int(l1_age or 0), trade)

    return counts


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"store already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def build_duckdb(tmp_dir: Path, csv_paths: list[Path], threads: int) -> dict[str, Any]:
    db_path = tmp_dir / "event_store.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    list_literal = "[" + ", ".join(quote_literal(path) for path in csv_paths) + "]"
    type_overrides = {
        "day": "VARCHAR",
        "event_kind": "VARCHAR",
        "ts_iso": "VARCHAR",
        "condition_id": "VARCHAR",
        "slug": "VARCHAR",
        "side": "VARCHAR",
        "opposite_side": "VARCHAR",
        "winner_side": "VARCHAR",
        "side_alignment": "VARCHAR",
        "high_side": "VARCHAR",
        "public_trade_taker_side": "VARCHAR",
        "book_update_reason": "VARCHAR",
    }
    type_literal = "{" + ", ".join(f"{quote_literal(key)}: {quote_literal(value)}" for key, value in type_overrides.items()) + "}"
    conn.execute(
        f"""
        CREATE TABLE completion_unwind_events AS
        SELECT *
        FROM read_csv({list_literal}, header=true, union_by_name=true, auto_detect=true, types={type_literal})
        """
    )
    total_rows = int(conn.execute("SELECT COUNT(*) FROM completion_unwind_events").fetchone()[0])
    kind_counts = {
        str(kind): int(count)
        for kind, count in conn.execute(
            "SELECT event_kind, COUNT(*) FROM completion_unwind_events GROUP BY event_kind ORDER BY event_kind"
        ).fetchall()
    }
    day_counts = {
        str(day): int(count)
        for day, count in conn.execute(
            "SELECT day, COUNT(*) FROM completion_unwind_events GROUP BY day ORDER BY day"
        ).fetchall()
    }
    dataset_dir = tmp_dir / "dataset"
    dataset_dir.mkdir(exist_ok=True)
    conn.execute(
        f"""
        COPY (SELECT * FROM completion_unwind_events)
        TO {quote_literal(dataset_dir)}
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day), OVERWRITE_OR_IGNORE TRUE)
        """
    )
    conn.execute("CHECKPOINT")
    conn.close()
    parquet_files = sorted(p.relative_to(tmp_dir).as_posix() for p in dataset_dir.rglob("*.parquet"))
    return {
        "duckdb": "event_store.duckdb",
        "duckdb_table": "completion_unwind_events",
        "parquet_glob": "dataset/**/*.parquet",
        "parquet_files": parquet_files,
        "row_count": total_rows,
        "event_kind_counts": kind_counts,
        "day_counts": day_counts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default=DEFAULT_STORE_NAME)
    parser.add_argument("--days", required=True)
    parser.add_argument("--label")
    parser.add_argument("--event-kinds", default="public_trade,l1_price_change")
    parser.add_argument("--clip-tiers", default=",".join(f"{x:g}" for x in DEFAULT_CLIP_TIERS))
    parser.add_argument("--max-l2-age-ms", type=int, default=3000)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--max-markets-per-day", type=int)
    parser.add_argument("--progress-every-markets", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    days = parse_days(args.days)
    clip_tiers = parse_floats(args.clip_tiers)
    event_kinds = parse_kinds(args.event_kinds)
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    publish_root = args.store_root / args.store_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    publish_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.store_root, args.min_free_gb)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        started_at = utc_now()
        csv_dir = tmp_dir / "csv"
        csv_dir.mkdir()
        fields = fieldnames(clip_tiers)
        csv_paths: list[Path] = []
        source_replay: list[dict[str, Any]] = []
        build_counts: dict[str, Any] = {}
        total_counts = {"l1_price_change": 0, "public_trade": 0, "missing_l1": 0, "missing_l2": 0}
        try:
            for day in days:
                db_path = args.replay_root / day / "crypto_5m.sqlite"
                if not db_path.is_file():
                    raise FileNotFoundError(f"missing replay SQLite for {day}: {db_path}")
                stat = db_path.stat()
                source_replay.append(
                    {
                        "day": day,
                        "path": str(db_path),
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sqlite_sequence": load_sqlite_sequence(db_path),
                    }
                )
                day_csv = csv_dir / f"{day}.csv"
                csv_paths.append(day_csv)
                day_counts = {"markets": 0, "l1_price_change": 0, "public_trade": 0, "missing_l1": 0, "missing_l2": 0}
                with connect_ro(db_path) as conn, day_csv.open("w", newline="", encoding="utf-8") as handle:
                    writer: csv.DictWriter[str] = csv.DictWriter(handle, fieldnames=fields)
                    writer.writeheader()
                    markets = fetch_markets(conn, args.max_markets_per_day)
                    for idx, market in enumerate(markets, start=1):
                        l1_books = load_l1(conn, market)
                        l2_by_side = load_l2(conn, market)
                        trades = load_trades(conn, market) if "public_trade" in event_kinds else []
                        counts = emit_market_rows(
                            writer=writer,
                            day=day,
                            market=market,
                            l1_books=l1_books,
                            l2_by_side=l2_by_side,
                            trades=trades,
                            event_kinds=event_kinds,
                            clip_tiers=clip_tiers,
                            max_l2_age_ms=args.max_l2_age_ms,
                        )
                        day_counts["markets"] += 1
                        for key in total_counts:
                            day_counts[key] += counts[key]
                            total_counts[key] += counts[key]
                        if args.progress_every_markets > 0 and idx % args.progress_every_markets == 0:
                            print(
                                json.dumps(
                                    {
                                        "stage": "build_day",
                                        "day": day,
                                        "markets_done": idx,
                                        "markets_total": len(markets),
                                        "day_counts": day_counts,
                                    },
                                    ensure_ascii=False,
                                ),
                                flush=True,
                            )
                build_counts[day] = day_counts

            outputs = build_duckdb(tmp_dir, csv_paths, args.duckdb_threads)
            manifest = {
                "schema_version": "completion_unwind_event_store_v2",
                "store_name": args.store_name,
                "label": label,
                "days": days,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "event_kinds": sorted(event_kinds),
                "clip_tiers": clip_tiers,
                "max_l2_age_ms": args.max_l2_age_ms,
                "source": "replay_published_sqlite",
                "source_replay": source_replay,
                "build_counts": build_counts,
                "total_build_counts": total_counts,
                "outputs": outputs,
                "truth_policy": (
                    "Events use strict latest L1/L2 with recv_ms <= ts_ms. "
                    "The store is for inventory/completion research and does not simulate private queue priority."
                ),
            }
            (tmp_dir / "EVENT_STORE_MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (tmp_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Completion/Unwind Event Store V2",
                        "",
                        "Event-level BTC 5m inventory research store.",
                        "",
                        "- `public_trade` rows represent public market trades with strict latest L1/L2 context.",
                        "- `l1_price_change` rows represent visible top-of-book price/size-change opportunities with previous-L1 delta fields.",
                        "- Buy columns consume ask-side L2; sell columns consume bid-side L2.",
                        "- `replay_published` SQLite remains the audit source.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "outputs": outputs, "build_counts": build_counts}, indent=2, sort_keys=True))
            return 0
        except Exception:
            if tmp_dir.exists():
                failed_dir = publish_root / f".{label}.failed.{os.getpid()}"
                if failed_dir.exists():
                    shutil.rmtree(failed_dir)
                tmp_dir.rename(failed_dir)
                print(json.dumps({"failed_tmp_dir": str(failed_dir)}, sort_keys=True), file=sys.stderr)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

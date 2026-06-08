#!/usr/bin/env python3
"""BTC 5m maker-first market-side proxy backtest.

This script reads replay SQLite files in read-only mode and uses public market
data only. It is not xuan exact truth and it is not own execution truth.

Proxy semantics:
- open candidate: sampled L1 snapshot inside a BTC 5m round.
- first leg maker fill: future public SELL flow on the selected side whose
  price is <= our simulated maker buy quote and cumulative size reaches clip.
- immediate taker completion: opposite ask at first-fill time.
- maker completion: a future public SELL trade on the opposite side whose price
  is <= the completion maker quote.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_274_700_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Trade:
    ts_ms: int
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class Fill:
    ts_ms: int
    vwap_price: float
    filled_size: float
    event_count: int
    last_trade_price: float


@dataclass(frozen=True)
class Book:
    recv_ms: int
    yes_bid_px: float | None
    yes_ask_px: float | None
    no_bid_px: float | None
    no_ask_px: float | None
    yes_bid_sz: float | None
    yes_ask_sz: float | None
    no_bid_sz: float | None
    no_ask_sz: float | None


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def day_start_ms(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def pct(num: int | float, den: int | float) -> float | None:
    if not den:
        return None
    return round(float(num) / float(den), 6)


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize_values(values: Iterable[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "p95": percentile(vals, 95),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def get_day_data_max_ms(conn: sqlite3.Connection) -> int | None:
    book_max = one(conn, "SELECT MAX(recv_ms) FROM md_book_l1")
    trade_max = one(conn, "SELECT MAX(trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL")
    vals = [int(v) for v in (book_max, trade_max) if v is not None]
    return max(vals) if vals else None


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


def side_px(book: Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_px if kind == "bid" else book.yes_ask_px
    return book.no_bid_px if kind == "bid" else book.no_ask_px


def side_sz(book: Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_sz if kind == "bid" else book.yes_ask_sz
    return book.no_bid_sz if kind == "bid" else book.no_ask_sz


def other_side(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def maker_buy_quote(book: Book, side: str, tick: float, placement: str) -> float | None:
    bid = side_px(book, side, "bid")
    ask = side_px(book, side, "ask")
    if bid is None or ask is None:
        return None
    if placement == "join_bid":
        quote = bid
    elif placement == "improve_to_ask_minus_tick":
        quote = min(ask - tick, 0.99)
        quote = max(quote, bid)
    else:
        raise ValueError(f"unsupported maker placement: {placement}")
    if quote <= 0 or quote >= ask:
        return None
    return round(quote, 6)


def nearest_book(books: list[Book], recv_times: list[int], ts_ms: int, max_age_ms: int) -> Book | None:
    if not books:
        return None
    idx = bisect.bisect_left(recv_times, ts_ms)
    candidates: list[Book] = []
    if idx < len(books):
        candidates.append(books[idx])
    if idx > 0:
        candidates.append(books[idx - 1])
    if not candidates:
        return None
    best = min(candidates, key=lambda b: abs(b.recv_ms - ts_ms))
    return best if abs(best.recv_ms - ts_ms) <= max_age_ms else None


def sell_fill_by_cumulative_size(
    sells_by_side: dict[str, list[Trade]],
    sell_times_by_side: dict[str, list[int]],
    side: str,
    start_ms: int,
    end_ms: int,
    max_price: float,
    target_size: float,
) -> Fill | None:
    if target_size <= 0:
        raise ValueError("target_size must be positive")
    trades = sells_by_side.get(side, [])
    times = sell_times_by_side.get(side, [])
    idx = bisect.bisect_left(times, start_ms)
    filled = 0.0
    notional = 0.0
    event_count = 0
    while idx < len(trades):
        trade = trades[idx]
        if trade.ts_ms > end_ms:
            return None
        if trade.price <= max_price:
            use_size = min(trade.size, target_size - filled)
            filled += use_size
            notional += use_size * trade.price
            event_count += 1
            if filled + 1e-9 >= target_size:
                return Fill(
                    ts_ms=trade.ts_ms,
                    vwap_price=notional / filled,
                    filled_size=filled,
                    event_count=event_count,
                    last_trade_price=trade.price,
                )
        idx += 1
    return None


def load_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    return [
        Book(
            recv_ms=int(row["recv_ms"]),
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


def load_sell_trades(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[Trade]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms < ?
          AND market_side IN ('YES', 'NO')
          AND taker_side='SELL'
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        out[str(row["market_side"])].append(
            Trade(ts_ms=int(row["trade_ts_ms"]), side=str(row["market_side"]), price=float(row["price"]), size=float(row["size"]))
        )
    return out


def sampled_books(books: list[Book], sample_ms: int) -> list[Book]:
    out: list[Book] = []
    next_ms: int | None = None
    for book in books:
        if next_ms is None or book.recv_ms >= next_ms:
            out.append(book)
            next_ms = book.recv_ms + sample_ms
    return out


def bucket_offset_s(offset_s: float) -> str:
    if offset_s < 15:
        return "000_015s"
    if offset_s < 30:
        return "015_030s"
    if offset_s < 60:
        return "030_060s"
    if offset_s < 120:
        return "060_120s"
    if offset_s < 240:
        return "120_240s"
    return "240_300s"


def compute_candidate(
    meta: sqlite3.Row,
    book: Book,
    side: str,
    books: list[Book],
    book_times: list[int],
    sells_by_side: dict[str, list[Trade]],
    sell_times_by_side: dict[str, list[int]],
    args: argparse.Namespace,
    day: str,
    day_status: str,
) -> dict[str, Any]:
    condition_id = str(meta["condition_id"])
    start_ms = int(meta["start_ms"])
    end_ms = int(meta["end_ms"])
    candidate_ts = book.recv_ms
    opp = other_side(side)

    first_quote = maker_buy_quote(book, side, args.tick_size, args.first_maker_placement)
    selected_bid = side_px(book, side, "bid")
    selected_ask = side_px(book, side, "ask")
    opp_bid = side_px(book, opp, "bid")
    opp_ask = side_px(book, opp, "ask")
    base: dict[str, Any] = {
        "date": day,
        "day_status": day_status,
        "condition_id": condition_id,
        "slug": str(meta["slug"]),
        "start_ms": start_ms,
        "end_ms": end_ms,
        "start_iso": iso_ms(start_ms),
        "end_iso": iso_ms(end_ms),
        "candidate_ts_ms": candidate_ts,
        "candidate_iso": iso_ms(candidate_ts),
        "candidate_offset_s": round((candidate_ts - start_ms) / 1000, 3),
        "candidate_offset_bucket": bucket_offset_s((candidate_ts - start_ms) / 1000),
        "first_side": side,
        "first_bid_px": selected_bid,
        "first_ask_px": selected_ask,
        "first_bid_sz": side_sz(book, side, "bid"),
        "first_ask_sz": side_sz(book, side, "ask"),
        "opposite_side": opp,
        "opposite_bid_px": opp_bid,
        "opposite_ask_px": opp_ask,
        "opposite_bid_sz": side_sz(book, opp, "bid"),
        "opposite_ask_sz": side_sz(book, opp, "ask"),
        "l1_pair_bid_sum": round(float(book.yes_bid_px) + float(book.no_bid_px), 6)
        if book.yes_bid_px is not None and book.no_bid_px is not None
        else None,
        "l1_pair_ask_sum": round(float(book.yes_ask_px) + float(book.no_ask_px), 6)
        if book.yes_ask_px is not None and book.no_ask_px is not None
        else None,
        "first_maker_quote_px": first_quote,
        "clip_size": args.clip_size,
        "first_maker_fill": False,
        "first_maker_fill_delay_s": None,
        "first_maker_fill_px": None,
        "first_maker_fill_size": None,
        "first_maker_fill_event_count": None,
        "taker_immediate_pair_cost": None,
        "taker_immediate_pair_cost_lte_0_99": False,
        "taker_immediate_pair_cost_lte_1_00": False,
        "maker_completion_quote_px": None,
        "maker_completion_fill_30s": False,
        "maker_completion_delay_s": None,
        "maker_completion_pair_cost": None,
        "maker_completion_pair_cost_lte_0_99": False,
        "maker_completion_pair_cost_lte_1_00": False,
    }
    if first_quote is None:
        base["block_reason"] = "missing_or_crossing_l1"
        return base

    first_fill = sell_fill_by_cumulative_size(
        sells_by_side=sells_by_side,
        sell_times_by_side=sell_times_by_side,
        side=side,
        start_ms=candidate_ts,
        end_ms=min(candidate_ts + int(args.first_fill_timeout_s * 1000), end_ms),
        max_price=first_quote,
        target_size=args.clip_size,
    )
    if first_fill is None:
        base["block_reason"] = "no_first_maker_fill"
        return base

    fill_book = nearest_book(books, book_times, first_fill.ts_ms, args.max_book_age_ms)
    fill_opp_ask = side_px(fill_book, opp, "ask") if fill_book is not None else None
    taker_pair_cost = round(first_fill.vwap_price + float(fill_opp_ask), 6) if fill_opp_ask is not None else None
    completion_ceiling = round(1.0 - first_fill.vwap_price - args.min_edge, 6)
    maker_completion_quote = None
    if fill_book is not None:
        quote_at_fill = maker_buy_quote(fill_book, opp, args.tick_size, args.completion_maker_placement)
        if quote_at_fill is not None:
            maker_completion_quote = min(quote_at_fill, completion_ceiling)
            if maker_completion_quote <= 0:
                maker_completion_quote = None

    base.update(
        {
            "block_reason": None,
            "first_maker_fill": True,
            "first_maker_fill_ts_ms": first_fill.ts_ms,
            "first_maker_fill_iso": iso_ms(first_fill.ts_ms),
            "first_maker_fill_delay_s": round((first_fill.ts_ms - candidate_ts) / 1000, 3),
            "first_maker_fill_px": round(first_fill.vwap_price, 6),
            "first_maker_fill_size": round(first_fill.filled_size, 6),
            "first_maker_fill_event_count": first_fill.event_count,
            "first_maker_fill_last_trade_px": round(first_fill.last_trade_price, 6),
            "fill_l1_book_recv_ms": fill_book.recv_ms if fill_book else None,
            "fill_l1_book_age_ms": fill_book.recv_ms - first_fill.ts_ms if fill_book else None,
            "fill_l1_opposite_ask_px": fill_opp_ask,
            "taker_immediate_pair_cost": taker_pair_cost,
            "taker_immediate_pair_cost_lte_0_99": bool(taker_pair_cost is not None and taker_pair_cost <= 0.99),
            "taker_immediate_pair_cost_lte_1_00": bool(taker_pair_cost is not None and taker_pair_cost <= 1.0),
            "maker_completion_quote_px": round(maker_completion_quote, 6) if maker_completion_quote is not None else None,
        }
    )

    if maker_completion_quote is None:
        base["completion_block_reason"] = "no_profitable_maker_quote"
        return base

    completion_fill = sell_fill_by_cumulative_size(
        sells_by_side=sells_by_side,
        sell_times_by_side=sell_times_by_side,
        side=opp,
        start_ms=first_fill.ts_ms,
        end_ms=min(first_fill.ts_ms + int(args.completion_timeout_s * 1000), end_ms),
        max_price=maker_completion_quote,
        target_size=args.clip_size,
    )
    if completion_fill is None:
        base["completion_block_reason"] = "no_opposite_maker_fill_30s"
        return base

    maker_pair_cost = round(first_fill.vwap_price + completion_fill.vwap_price, 6)
    base.update(
        {
            "completion_block_reason": None,
            "maker_completion_fill_30s": True,
            "maker_completion_ts_ms": completion_fill.ts_ms,
            "maker_completion_iso": iso_ms(completion_fill.ts_ms),
            "maker_completion_delay_s": round((completion_fill.ts_ms - first_fill.ts_ms) / 1000, 3),
            "maker_completion_px": round(completion_fill.vwap_price, 6),
            "maker_completion_size": round(completion_fill.filled_size, 6),
            "maker_completion_event_count": completion_fill.event_count,
            "maker_completion_last_trade_px": round(completion_fill.last_trade_price, 6),
            "maker_completion_pair_cost": maker_pair_cost,
            "maker_completion_pair_cost_lte_0_99": maker_pair_cost <= 0.99,
            "maker_completion_pair_cost_lte_1_00": maker_pair_cost <= 1.0,
        }
    )
    return base


def status_for_day(day: str) -> str:
    if day == "2026-04-30":
        return "partial_available_window"
    if day == "2026-04-27":
        return "trusted_start_partial_day"
    if day == "2026-04-28":
        return "planned_outage_excluded"
    return "full_day"


def load_day(replay_root: Path, day: str, args: argparse.Namespace) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db_path = replay_root / day / "crypto_5m.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    with connect_ro(db_path) as conn:
        day_start = day_start_ms(day)
        day_end = day_start + 86_400_000
        max_data_ms = get_day_data_max_ms(conn)
        if max_data_ms is None:
            data_end = day_end
        elif 0 <= day_end - max_data_ms <= 1000:
            data_end = day_end
        else:
            data_end = min(day_end, max_data_ms)
        day_status = status_for_day(day)

        metas = conn.execute(
            """
            SELECT condition_id, slug, start_ms, end_ms
            FROM market_meta
            WHERE symbol='BTC'
              AND interval_sec=300
              AND start_ms >= ?
              AND start_ms < ?
              AND end_ms <= ?
              AND end_ms > ?
            ORDER BY start_ms
            """,
            (day_start, day_end, data_end, args.trusted_start_ms),
        ).fetchall()

        rows: list[dict[str, Any]] = []
        excluded_outage = 0
        market_count = 0
        sampled_book_count = 0
        for meta in metas:
            start_ms = int(meta["start_ms"])
            end_ms = int(meta["end_ms"])
            if overlaps(start_ms, end_ms, PLANNED_OUTAGE_START_MS, PLANNED_OUTAGE_END_MS):
                excluded_outage += 1
                continue
            market_count += 1
            open_start_ms = start_ms + int(args.open_start_s * 1000)
            open_end_ms = end_ms - int(args.tail_freeze_s * 1000)
            if open_start_ms >= open_end_ms:
                continue
            books = load_books(conn, str(meta["condition_id"]), open_start_ms, end_ms)
            if not books:
                continue
            book_times = [book.recv_ms for book in books]
            candidate_books = [book for book in sampled_books(books, int(args.sample_interval_s * 1000)) if book.recv_ms < open_end_ms]
            sampled_book_count += len(candidate_books)
            sells_by_side = load_sell_trades(conn, str(meta["condition_id"]), open_start_ms, end_ms)
            sell_times_by_side = {side: [trade.ts_ms for trade in trades] for side, trades in sells_by_side.items()}
            for book in candidate_books:
                for side in ("YES", "NO"):
                    rows.append(
                        compute_candidate(
                            meta=meta,
                            book=book,
                            side=side,
                            books=books,
                            book_times=book_times,
                            sells_by_side=sells_by_side,
                            sell_times_by_side=sell_times_by_side,
                            args=args,
                            day=day,
                            day_status=day_status,
                        )
                    )

        return rows, {
            "date": day,
            "db_path": str(db_path),
            "day_status": day_status,
            "day_start_iso": iso_ms(day_start),
            "day_end_iso": iso_ms(day_end),
            "max_data_ms": max_data_ms,
            "max_data_iso": iso_ms(max_data_ms),
            "data_end_ms": data_end,
            "data_end_iso": iso_ms(data_end),
            "eligible_btc_markets": market_count,
            "excluded_planned_outage_markets": excluded_outage,
            "sampled_l1_books": sampled_book_count,
            "candidate_rows": len(rows),
        }


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first_filled = [r for r in rows if r.get("first_maker_fill")]
    taker_cost_rows = [r for r in first_filled if r.get("taker_immediate_pair_cost") is not None]
    maker_completed = [r for r in first_filled if r.get("maker_completion_fill_30s")]
    return {
        "candidate_count": len(rows),
        "first_maker_fill_count": len(first_filled),
        "first_maker_fill_rate": pct(len(first_filled), len(rows)),
        "taker_immediate_cost_lte_0_99_count": sum(1 for r in taker_cost_rows if r["taker_immediate_pair_cost_lte_0_99"]),
        "taker_immediate_cost_lte_0_99_rate_among_first_fills": pct(
            sum(1 for r in taker_cost_rows if r["taker_immediate_pair_cost_lte_0_99"]), len(first_filled)
        ),
        "taker_immediate_cost_lte_1_00_rate_among_first_fills": pct(
            sum(1 for r in taker_cost_rows if r["taker_immediate_pair_cost_lte_1_00"]), len(first_filled)
        ),
        "maker_completion_30s_count": len(maker_completed),
        "maker_completion_30s_rate_among_first_fills": pct(len(maker_completed), len(first_filled)),
        "maker_completion_30s_rate_among_candidates": pct(len(maker_completed), len(rows)),
        "first_maker_fill_delay_s": summarize_values([r.get("first_maker_fill_delay_s") for r in first_filled]),
        "taker_immediate_pair_cost": summarize_values([r.get("taker_immediate_pair_cost") for r in taker_cost_rows]),
        "maker_completion_delay_s": summarize_values([r.get("maker_completion_delay_s") for r in maker_completed]),
        "maker_completion_pair_cost": summarize_values([r.get("maker_completion_pair_cost") for r in maker_completed]),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = compact(rows)
    out["by_offset_bucket"] = {
        bucket: compact([r for r in rows if r.get("candidate_offset_bucket") == bucket])
        for bucket in ("000_015s", "015_030s", "030_060s", "060_120s", "120_240s", "240_300s")
    }
    out["by_day"] = {day: compact([r for r in rows if r["date"] == day]) for day in sorted({r["date"] for r in rows})}
    out["block_reason_counts"] = count_field(rows, "block_reason")
    out["completion_block_reason_counts"] = count_field([r for r in rows if r.get("first_maker_fill")], "completion_block_reason")
    return out


def count_field(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field) or "none")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    lines = [
        "# BTC 5m Maker-First Proxy Backtest",
        "",
        "## Data Boundary",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- trusted_start: `{report['trusted_start_iso']}`",
        "- included DBs: `2026-04-27`, `2026-04-28`, `2026-04-29`, `2026-04-30`",
        "- excluded DBs: `2026-04-24`, `2026-04-26`",
        "- planned_outage_excluded: `2026-04-28T11:00:00Z` to `2026-04-28T12:00:00Z`",
        "- `2026-04-30` is treated as an available-window partial day.",
        "- This report uses public market replay only. `xuan_episode_ready=false`; `own_execution_truth_ready=false`.",
        "",
        "## Parameters",
        "",
        f"- sample_interval_s: `{report['parameters']['sample_interval_s']}`",
        f"- open_start_s: `{report['parameters']['open_start_s']}`",
        f"- tail_freeze_s: `{report['parameters']['tail_freeze_s']}`",
        f"- first_fill_timeout_s: `{report['parameters']['first_fill_timeout_s']}`",
        f"- completion_timeout_s: `{report['parameters']['completion_timeout_s']}`",
        f"- clip_size: `{report['parameters']['clip_size']}`",
        f"- min_edge: `{report['parameters']['min_edge']}`",
        f"- first_maker_placement: `{report['parameters']['first_maker_placement']}`",
        f"- completion_maker_placement: `{report['parameters']['completion_maker_placement']}`",
        "",
        "## Aggregate",
        "",
        f"- candidates: `{agg['candidate_count']}`",
        f"- first maker fill rate: `{agg['first_maker_fill_rate']}`",
        f"- first maker fill delay p50/p90: `{agg['first_maker_fill_delay_s']['p50']}` / `{agg['first_maker_fill_delay_s']['p90']}`",
        f"- taker-immediate pair cost p50/p90: `{agg['taker_immediate_pair_cost']['p50']}` / `{agg['taker_immediate_pair_cost']['p90']}`",
        f"- taker-immediate cost <=0.99 among first fills: `{agg['taker_immediate_cost_lte_0_99_rate_among_first_fills']}`",
        f"- taker-immediate cost <=1.00 among first fills: `{agg['taker_immediate_cost_lte_1_00_rate_among_first_fills']}`",
        f"- maker completion 30s among first fills: `{agg['maker_completion_30s_rate_among_first_fills']}`",
        f"- maker completion pair cost p50/p90: `{agg['maker_completion_pair_cost']['p50']}` / `{agg['maker_completion_pair_cost']['p90']}`",
        "",
        "## By Offset Bucket",
        "",
        "| bucket | candidates | first fill rate | taker <=0.99 | maker completion 30s | maker pair p50 |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for bucket, row in agg["by_offset_bucket"].items():
        lines.append(
            f"| {bucket} | {row['candidate_count']} | {row['first_maker_fill_rate']} | "
            f"{row['taker_immediate_cost_lte_0_99_rate_among_first_fills']} | "
            f"{row['maker_completion_30s_rate_among_first_fills']} | {row['maker_completion_pair_cost']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## By Day",
            "",
            "| day | candidates | first fill rate | taker <=0.99 | maker completion 30s |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for day, row in agg["by_day"].items():
        lines.append(
            f"| {day} | {row['candidate_count']} | {row['first_maker_fill_rate']} | "
            f"{row['taker_immediate_cost_lte_0_99_rate_among_first_fills']} | "
            f"{row['maker_completion_30s_rate_among_first_fills']} |"
        )
    lines.extend(
        [
            "",
            "## Semantics",
            "",
            "- Maker fill is inferred from cumulative public taker `SELL` flow hitting a simulated buy quote.",
            "- `clip_size` is measured in outcome shares and must be fully covered inside the timeout.",
            "- Queue priority, order size, cancellations, and private user fills are not observable here.",
            "- Taker-immediate completion is a cost upper-bound at first-fill time, not a recommendation to execute taker by default.",
            "- Maker completion uses a profitable ceiling of `1 - first_fill_price - min_edge`.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--output-dir", default="data/exports/btc5m_maker_first_proxy_20260501")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--trusted-start-ms", type=int, default=TRUSTED_START_MS)
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--open-start-s", type=float, default=0.0)
    parser.add_argument("--tail-freeze-s", type=float, default=60.0)
    parser.add_argument("--first-fill-timeout-s", type=float, default=30.0)
    parser.add_argument("--completion-timeout-s", type=float, default=30.0)
    parser.add_argument("--max-book-age-ms", type=int, default=1000)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument("--min-edge", type=float, default=0.01)
    parser.add_argument("--clip-size", type=float, default=1.0)
    parser.add_argument("--first-maker-placement", choices=("join_bid", "improve_to_ask_minus_tick"), default="improve_to_ask_minus_tick")
    parser.add_argument(
        "--completion-maker-placement",
        choices=("join_bid", "improve_to_ask_minus_tick"),
        default="improve_to_ask_minus_tick",
    )
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    output_dir = Path(args.output_dir)
    days = [d.strip() for d in args.days.split(",") if d.strip()]

    all_rows: list[dict[str, Any]] = []
    db_summaries: list[dict[str, Any]] = []
    for day in days:
        rows, summary = load_day(replay_root, day, args)
        all_rows.extend(rows)
        db_summaries.append(summary)

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "trusted_start_ms": args.trusted_start_ms,
        "trusted_start_iso": iso_ms(args.trusted_start_ms),
        "planned_outage": {
            "start_ms": PLANNED_OUTAGE_START_MS,
            "end_ms": PLANNED_OUTAGE_END_MS,
            "start_iso": iso_ms(PLANNED_OUTAGE_START_MS),
            "end_iso": iso_ms(PLANNED_OUTAGE_END_MS),
        },
        "db_summaries": db_summaries,
        "xuan_episode_ready": False,
        "own_execution_truth_ready": False,
        "parameters": {
            "sample_interval_s": args.sample_interval_s,
            "open_start_s": args.open_start_s,
            "tail_freeze_s": args.tail_freeze_s,
            "first_fill_timeout_s": args.first_fill_timeout_s,
            "completion_timeout_s": args.completion_timeout_s,
            "max_book_age_ms": args.max_book_age_ms,
            "tick_size": args.tick_size,
            "min_edge": args.min_edge,
            "clip_size": args.clip_size,
            "first_maker_placement": args.first_maker_placement,
            "completion_maker_placement": args.completion_maker_placement,
        },
        "aggregate": aggregate(all_rows),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_maker_first_candidate_summary.csv", all_rows)
    (output_dir / "btc5m_maker_first_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_maker_first_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "aggregate": report["aggregate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

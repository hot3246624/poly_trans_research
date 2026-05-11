#!/usr/bin/env python3
"""Build a xuan public execution truth/proxy store.

This store is intentionally conservative about the word "truth":

- xuan public trades/activity are public execution truth.
- strict L1/L2 context is replay-published market truth.
- maker/taker role is exact only when public trade/address identifiers allow it;
  otherwise it is an inferred public match or left unknown.
- private order placement, cancellation, and real queue priority are not
  reconstructed because they are not present in public data.
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
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


DEFAULT_STORE_NAME = "xuan_public_execution_truth_v1"
DEFAULT_XUAN_USER = "0xcfb103c37c0234f524c632d964ed31f117b5f694"
DEFAULT_PUBLIC_MATCH_WINDOW_MS = 30_000
DEFAULT_NEXT_BOOK_WINDOW_MS = 3_000
TRUSTED_START_MS = 1_777_274_700_000
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Market:
    condition_id: str
    slug: str
    start_ms: int
    end_ms: int
    winner_side: str | None
    resolution_source: str | None


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
class XuanTrade:
    row_id: int
    trade_ts_ms: int | None
    condition_id: str
    outcome_side: str | None
    action: str | None
    price: float | None
    size: float | None
    proxy_wallet: str | None
    tx_hash: str | None
    trade_id: str | None
    raw_json: str | None


@dataclass(frozen=True)
class PublicTrade:
    row_id: int
    trade_ts_ms: int | None
    recv_ms: int
    condition_id: str
    market_side: str | None
    taker_side: str | None
    price: float
    size: float
    trade_id: str | None
    maker_address: str | None
    taker_address: str | None
    raw_json: str | None


@dataclass
class Lot:
    qty: float
    unit_cost: float
    ts_ms: int
    cycle_id: str


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_days(value: str) -> list[str]:
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    return days


def norm_addr(value: Any) -> str:
    return str(value or "").strip().lower()


def norm_side(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    return text if text in {"YES", "NO"} else None


def norm_action(value: Any) -> str | None:
    text = str(value or "").strip().upper()
    if text in {"BUY", "SELL", "MERGE", "REDEEM", "SETTLEMENT"}:
        return text.lower()
    return None


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def close_num(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


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


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_px(book: L1Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_px if kind == "bid" else book.yes_ask_px
    return book.no_bid_px if kind == "bid" else book.no_ask_px


def side_sz(book: L1Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_sz if kind == "bid" else book.yes_ask_sz
    return book.no_bid_sz if kind == "bid" else book.no_ask_sz


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


def side_alignment(book: L1Book, side: str | None) -> str | None:
    if side not in {"YES", "NO"}:
        return None
    high = high_side(book)
    if high is None:
        return None
    return "high" if side == high else "low"


def levels_from_row(row: sqlite3.Row, prefix: str) -> tuple[tuple[float, float], ...]:
    levels: list[tuple[float, float]] = []
    for idx in range(1, 6):
        px = safe_float(row[f"{prefix}{idx}_px"])
        sz = safe_float(row[f"{prefix}{idx}_sz"])
        if px is None or sz is None or sz <= 0:
            continue
        levels.append((px, sz))
    return tuple(levels)


def level_rank_and_size(levels: Iterable[tuple[float, float]], price: float | None, tol: float) -> tuple[int | None, float | None]:
    if price is None:
        return None, None
    for idx, (px, sz) in enumerate(levels, start=1):
        if close_num(px, price, tol):
            return idx, sz
    return None, None


def latest_by_time(items: list[Any], times: list[int], ts_ms: int) -> tuple[Any | None, int | None]:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None, None
    item = items[idx]
    recv_ms = int(item.recv_ms)
    age_ms = ts_ms - recv_ms
    if age_ms < 0:
        return None, None
    return item, age_ms


def next_by_time(items: list[Any], times: list[int], ts_ms: int, max_wait_ms: int) -> tuple[Any | None, int | None]:
    idx = bisect.bisect_right(times, ts_ms)
    if idx >= len(items):
        return None, None
    item = items[idx]
    delay_ms = int(item.recv_ms) - ts_ms
    if delay_ms < 0 or delay_ms > max_wait_ms:
        return None, delay_ms
    return item, delay_ms


def load_sqlite_sequence(path: Path) -> dict[str, int]:
    with connect_ro(path) as conn:
        rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {str(name): int(seq) for name, seq in rows}


def fetch_markets(conn: sqlite3.Connection) -> dict[str, Market]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms,
               s.winner_side, s.resolution_source
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol='BTC'
          AND m.interval_sec=300
        ORDER BY m.start_ms, m.condition_id
        """
    ).fetchall()
    markets: dict[str, Market] = {}
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        markets[str(row["condition_id"])] = Market(
            condition_id=str(row["condition_id"]),
            slug=str(row["slug"]),
            start_ms=start_ms,
            end_ms=end_ms,
            winner_side=norm_side(row["winner_side"]),
            resolution_source=None if row["resolution_source"] is None else str(row["resolution_source"]),
        )
    return markets


def load_l1(conn: sqlite3.Connection, market: Market, start_ms: int | None = None, end_ms: int | None = None) -> list[L1Book]:
    start = max(market.start_ms, TRUSTED_START_MS) if start_ms is None else max(start_ms, TRUSTED_START_MS)
    end = market.end_ms if end_ms is None else min(end_ms, market.end_ms)
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
        (market.condition_id, start, end),
    ).fetchall()
    return [
        L1Book(
            row_id=int(row["id"]),
            recv_ms=int(row["recv_ms"]),
            capture_seq=int(row["capture_seq"]),
            yes_bid_px=safe_float(row["yes_bid_px"]),
            yes_ask_px=safe_float(row["yes_ask_px"]),
            no_bid_px=safe_float(row["no_bid_px"]),
            no_ask_px=safe_float(row["no_ask_px"]),
            yes_bid_sz=safe_float(row["yes_bid_sz"]),
            yes_ask_sz=safe_float(row["yes_ask_sz"]),
            no_bid_sz=safe_float(row["no_bid_sz"]),
            no_ask_sz=safe_float(row["no_ask_sz"]),
        )
        for row in rows
    ]


def load_l2(conn: sqlite3.Connection, market: Market, start_ms: int | None = None, end_ms: int | None = None) -> dict[str, list[L2Book]]:
    start = max(market.start_ms, TRUSTED_START_MS) if start_ms is None else max(start_ms, TRUSTED_START_MS)
    end = market.end_ms if end_ms is None else min(end_ms, market.end_ms)
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
        (market.condition_id, start, end),
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


def l1_from_row(row: sqlite3.Row) -> L1Book:
    return L1Book(
        row_id=int(row["id"]),
        recv_ms=int(row["recv_ms"]),
        capture_seq=int(row["capture_seq"]),
        yes_bid_px=safe_float(row["yes_bid_px"]),
        yes_ask_px=safe_float(row["yes_ask_px"]),
        no_bid_px=safe_float(row["no_bid_px"]),
        no_ask_px=safe_float(row["no_ask_px"]),
        yes_bid_sz=safe_float(row["yes_bid_sz"]),
        yes_ask_sz=safe_float(row["yes_ask_sz"]),
        no_bid_sz=safe_float(row["no_bid_sz"]),
        no_ask_sz=safe_float(row["no_ask_sz"]),
    )


def l2_from_row(row: sqlite3.Row) -> L2Book:
    return L2Book(
        row_id=int(row["id"]),
        recv_ms=int(row["recv_ms"]),
        capture_seq=int(row["capture_seq"]),
        side=str(row["market_side"]),
        bids=levels_from_row(row, "bid"),
        asks=levels_from_row(row, "ask"),
    )


def query_l1_at(conn: sqlite3.Connection, condition_id: str, ts_ms: int) -> tuple[L1Book | None, int | None]:
    row = conn.execute(
        """
        SELECT id, recv_ms, capture_seq,
               yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, capture_seq DESC, id DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    if row is None:
        return None, None
    book = l1_from_row(row)
    return book, ts_ms - book.recv_ms


def query_l2_at(conn: sqlite3.Connection, condition_id: str, side: str, ts_ms: int) -> tuple[L2Book | None, int | None]:
    row = conn.execute(
        """
        SELECT id, recv_ms, capture_seq, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, capture_seq DESC, id DESC
        LIMIT 1
        """,
        (condition_id, side, ts_ms),
    ).fetchone()
    if row is None:
        return None, None
    book = l2_from_row(row)
    return book, ts_ms - book.recv_ms


def query_l2_after(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    ts_ms: int,
    max_wait_ms: int,
) -> tuple[L2Book | None, int | None]:
    row = conn.execute(
        """
        SELECT id, recv_ms, capture_seq, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms > ?
          AND recv_ms <= ?
        ORDER BY recv_ms ASC, capture_seq ASC, id ASC
        LIMIT 1
        """,
        (condition_id, side, ts_ms, ts_ms + max_wait_ms),
    ).fetchone()
    if row is None:
        return None, None
    book = l2_from_row(row)
    return book, book.recv_ms - ts_ms


def raw_json_value(raw_json: str | None, *keys: str) -> str | None:
    if not raw_json:
        return None
    try:
        payload = json.loads(raw_json)
    except Exception:
        return None
    for key in keys:
        value = payload.get(key) if isinstance(payload, dict) else None
        if value not in (None, ""):
            return str(value)
    return None


def load_xuan_trades(conn: sqlite3.Connection, xuan_user: str) -> dict[str, list[XuanTrade]]:
    rows = conn.execute(
        """
        SELECT id, trade_ts_ms, condition_id, outcome_side, side, price, size,
               proxy_wallet, tx_hash, trade_id, raw_json
        FROM xuan_trades
        WHERE lower(user)=lower(?)
          AND condition_id IS NOT NULL
        ORDER BY condition_id, trade_ts_ms, id
        """,
        (xuan_user,),
    ).fetchall()
    out: dict[str, list[XuanTrade]] = defaultdict(list)
    for row in rows:
        cid = str(row["condition_id"])
        out[cid].append(
            XuanTrade(
                row_id=int(row["id"]),
                trade_ts_ms=None if row["trade_ts_ms"] is None else int(row["trade_ts_ms"]),
                condition_id=cid,
                outcome_side=norm_side(row["outcome_side"]),
                action=norm_action(row["side"]),
                price=safe_float(row["price"]),
                size=safe_float(row["size"]),
                proxy_wallet=None if row["proxy_wallet"] in (None, "") else str(row["proxy_wallet"]),
                tx_hash=None if row["tx_hash"] in (None, "") else str(row["tx_hash"]),
                trade_id=None if row["trade_id"] in (None, "") else str(row["trade_id"]),
                raw_json=row["raw_json"],
            )
        )
    return out


def load_public_trades(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
) -> list[PublicTrade]:
    params: list[Any] = [condition_id]
    time_filter = ""
    if start_ms is not None and end_ms is not None:
        time_filter = "AND COALESCE(trade_ts_ms, recv_ms) >= ? AND COALESCE(trade_ts_ms, recv_ms) <= ?"
        params.extend([start_ms, end_ms])
    rows = conn.execute(
        f"""
        SELECT id, trade_ts_ms, recv_ms, condition_id, market_side, taker_side,
               price, size, trade_id, maker_address, taker_address, raw_json
        FROM md_trades
        WHERE condition_id=?
          AND market_side IN ('YES', 'NO')
          {time_filter}
        ORDER BY COALESCE(trade_ts_ms, recv_ms), id
        """,
        params,
    ).fetchall()
    out: list[PublicTrade] = []
    for row in rows:
        out.append(
            PublicTrade(
                row_id=int(row["id"]),
                trade_ts_ms=None if row["trade_ts_ms"] is None else int(row["trade_ts_ms"]),
                recv_ms=int(row["recv_ms"]),
                condition_id=str(row["condition_id"]),
                market_side=norm_side(row["market_side"]),
                taker_side=norm_action(row["taker_side"]),
                price=float(row["price"]),
                size=float(row["size"]),
                trade_id=None if row["trade_id"] in (None, "") else str(row["trade_id"]),
                maker_address=None if row["maker_address"] in (None, "") else str(row["maker_address"]),
                taker_address=None if row["taker_address"] in (None, "") else str(row["taker_address"]),
                raw_json=row["raw_json"],
            )
        )
    return out


def event_key(row: sqlite3.Row) -> tuple[Any, ...]:
    return (
        row["condition_id"],
        row["activity_type"],
        row["activity_ts_ms"],
        row["outcome_side"],
        row["side"],
        round(float(row["price"] or 0.0), 12),
        round(float(row["size"] or 0.0), 8),
        round(float(row["usdc_size"] or 0.0), 8),
        row["tx_hash"],
    )


def load_xuan_activity(conn: sqlite3.Connection, xuan_user: str, markets: dict[str, Market]) -> dict[str, list[sqlite3.Row]]:
    rows = conn.execute(
        """
        SELECT id, activity_ts_ms, recv_ms, poll_ts_ms, condition_id, slug, activity_type,
               outcome_side, side, price, size, usdc_size, asset, proxy_wallet,
               tx_hash, raw_json
        FROM xuan_activity
        WHERE lower(user)=lower(?)
          AND condition_id IS NOT NULL
          AND activity_ts_ms IS NOT NULL
          AND activity_type IN ('TRADE', 'MERGE', 'REDEEM')
        ORDER BY activity_ts_ms, id
        """,
        (xuan_user,),
    ).fetchall()
    seen: set[tuple[Any, ...]] = set()
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        cid = str(row["condition_id"])
        if cid not in markets:
            continue
        key = event_key(row)
        if key in seen:
            continue
        seen.add(key)
        out[cid].append(row)
    return out


def match_xuan_trade(activity: sqlite3.Row, trades: list[XuanTrade], ts_window_ms: int) -> XuanTrade | None:
    cid = str(activity["condition_id"])
    ts_ms = int(activity["activity_ts_ms"])
    side = norm_side(activity["outcome_side"])
    action = norm_action(activity["side"])
    price = safe_float(activity["price"])
    size = safe_float(activity["size"])
    tx_hash = str(activity["tx_hash"] or "")
    best: tuple[int, XuanTrade] | None = None
    for trade in trades:
        if trade.condition_id != cid:
            continue
        if tx_hash and trade.tx_hash and tx_hash.lower() == trade.tx_hash.lower():
            return trade
        if side is not None and trade.outcome_side is not None and side != trade.outcome_side:
            continue
        if action is not None and trade.action is not None and action != trade.action:
            continue
        if not close_num(price, trade.price, 1e-9):
            continue
        if not close_num(size, trade.size, 1e-6):
            continue
        if trade.trade_ts_ms is None:
            continue
        delta = abs(trade.trade_ts_ms - ts_ms)
        if delta > ts_window_ms:
            continue
        if best is None or delta < best[0]:
            best = (delta, trade)
    return None if best is None else best[1]


def match_public_trade(
    *,
    activity: sqlite3.Row,
    xuan_trade: XuanTrade | None,
    public_trades: list[PublicTrade],
    window_ms: int,
) -> tuple[PublicTrade | None, str | None]:
    cid = str(activity["condition_id"])
    ts_ms = int(activity["activity_ts_ms"])
    side = norm_side(activity["outcome_side"])
    price = safe_float(activity["price"])
    size = safe_float(activity["size"])
    tx_hash = str(activity["tx_hash"] or "")
    trade_id = xuan_trade.trade_id if xuan_trade else None

    if trade_id:
        for trade in public_trades:
            if trade.trade_id and trade.trade_id == trade_id:
                return trade, "trade_id"
            raw_trade_id = raw_json_value(trade.raw_json, "trade_id", "tradeId", "id")
            if raw_trade_id and raw_trade_id == trade_id:
                return trade, "raw_trade_id"

    if tx_hash:
        for trade in public_trades:
            raw_tx = raw_json_value(trade.raw_json, "tx_hash", "txHash", "transactionHash")
            if raw_tx and raw_tx.lower() == tx_hash.lower():
                return trade, "raw_tx_hash"

    best: tuple[int, PublicTrade] | None = None
    for trade in public_trades:
        if trade.condition_id != cid:
            continue
        if side is not None and trade.market_side is not None and side != trade.market_side:
            continue
        if not close_num(price, trade.price, 1e-9):
            continue
        if not close_num(size, trade.size, 1e-6):
            continue
        trade_ts = trade.trade_ts_ms if trade.trade_ts_ms is not None else trade.recv_ms
        delta = abs(trade_ts - ts_ms)
        if delta > window_ms:
            continue
        if best is None or delta < best[0]:
            best = (delta, trade)
    return (None, None) if best is None else (best[1], "time_price_size")


def classify_role(
    *,
    action: str | None,
    xuan_user: str,
    xuan_trade: XuanTrade | None,
    public_trade: PublicTrade | None,
    match_method: str | None,
) -> dict[str, Any]:
    if public_trade is None:
        return {
            "order_type": "unknown",
            "truth_level": "public_xuan_activity_only",
            "classification_method": "no_public_trade_match",
            "match_confidence": "low",
            "is_exact_maker_fill": False,
        }

    addresses = {norm_addr(xuan_user)}
    if xuan_trade and xuan_trade.proxy_wallet:
        addresses.add(norm_addr(xuan_trade.proxy_wallet))

    maker = norm_addr(public_trade.maker_address)
    taker = norm_addr(public_trade.taker_address)
    if maker and maker in addresses:
        return {
            "order_type": "maker",
            "truth_level": "exact_address_match",
            "classification_method": "maker_address",
            "match_confidence": "high",
            "is_exact_maker_fill": True,
        }
    if taker and taker in addresses:
        return {
            "order_type": "taker",
            "truth_level": "exact_address_match",
            "classification_method": "taker_address",
            "match_confidence": "high",
            "is_exact_maker_fill": False,
        }

    inferred: str | None = None
    if action in {"buy", "sell"} and public_trade.taker_side in {"buy", "sell"}:
        inferred = "taker" if action == public_trade.taker_side else "maker"

    if inferred is None:
        return {
            "order_type": "unknown",
            "truth_level": "public_trade_match_unknown_role",
            "classification_method": match_method or "public_trade_match",
            "match_confidence": "medium" if match_method else "low",
            "is_exact_maker_fill": False,
        }

    confidence = "high" if match_method in {"trade_id", "raw_trade_id", "raw_tx_hash"} else "medium"
    truth_level = "exact_trade_id_match" if confidence == "high" else "public_match_inferred_role"
    return {
        "order_type": inferred,
        "truth_level": truth_level,
        "classification_method": match_method or "public_trade_match",
        "match_confidence": confidence,
        "is_exact_maker_fill": inferred == "maker" and confidence == "high",
    }


def consume_fifo(lots: deque[Lot], qty: float) -> tuple[float, float, int | None, int | None, float]:
    remaining = float(qty)
    consumed_qty = 0.0
    consumed_cost = 0.0
    first_ts: int | None = None
    last_ts: int | None = None
    while remaining > 1e-9 and lots:
        lot = lots[0]
        take = min(lot.qty, remaining)
        consumed_qty += take
        consumed_cost += take * lot.unit_cost
        first_ts = lot.ts_ms if first_ts is None else min(first_ts, lot.ts_ms)
        last_ts = lot.ts_ms if last_ts is None else max(last_ts, lot.ts_ms)
        lot.qty -= take
        remaining -= take
        if lot.qty <= 1e-9:
            lots.popleft()
    return consumed_qty, consumed_cost, first_ts, last_ts, max(0.0, remaining)


def lots_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def lots_cost(lots: deque[Lot]) -> float:
    return sum(lot.qty * lot.unit_cost for lot in lots)


def l1_context(row: dict[str, Any], l1: L1Book | None, l1_age_ms: int | None, side: str | None) -> None:
    if l1 is None:
        for key in (
            "strict_l1_row_id",
            "strict_l1_recv_ms",
            "strict_l1_age_ms",
            "side_bid",
            "side_bid_sz",
            "side_ask",
            "side_ask_sz",
            "opp_bid",
            "opp_bid_sz",
            "opp_ask",
            "opp_ask_sz",
            "l1_pair_ask",
            "l1_pair_bid",
            "side_alignment",
            "high_side",
        ):
            row[key] = None
        return
    row["strict_l1_row_id"] = l1.row_id
    row["strict_l1_recv_ms"] = l1.recv_ms
    row["strict_l1_age_ms"] = l1_age_ms
    row["high_side"] = high_side(l1)
    row["side_alignment"] = side_alignment(l1, side)
    if side not in {"YES", "NO"}:
        for key in (
            "side_bid",
            "side_bid_sz",
            "side_ask",
            "side_ask_sz",
            "opp_bid",
            "opp_bid_sz",
            "opp_ask",
            "opp_ask_sz",
            "l1_pair_ask",
            "l1_pair_bid",
        ):
            row[key] = None
        return
    opp = other(side)
    side_bid = side_px(l1, side, "bid")
    side_ask = side_px(l1, side, "ask")
    opp_bid = side_px(l1, opp, "bid")
    opp_ask = side_px(l1, opp, "ask")
    row.update(
        {
            "side_bid": side_bid,
            "side_bid_sz": side_sz(l1, side, "bid"),
            "side_ask": side_ask,
            "side_ask_sz": side_sz(l1, side, "ask"),
            "opp_bid": opp_bid,
            "opp_bid_sz": side_sz(l1, opp, "bid"),
            "opp_ask": opp_ask,
            "opp_ask_sz": side_sz(l1, opp, "ask"),
            "l1_pair_ask": None if side_ask is None or opp_ask is None else float(side_ask) + float(opp_ask),
            "l1_pair_bid": None if side_bid is None or opp_bid is None else float(side_bid) + float(opp_bid),
        }
    )


def execution_levels(action: str | None, order_type: str | None, l2: L2Book | None) -> tuple[str | None, tuple[tuple[float, float], ...]]:
    if l2 is None:
        return None, ()
    if action == "buy" and order_type == "maker":
        return "bid", l2.bids
    if action == "buy" and order_type == "taker":
        return "ask", l2.asks
    if action == "sell" and order_type == "maker":
        return "ask", l2.asks
    if action == "sell" and order_type == "taker":
        return "bid", l2.bids
    return None, ()


def l2_context(
    row: dict[str, Any],
    *,
    l2: L2Book | None,
    l2_age_ms: int | None,
    next_l2: L2Book | None,
    next_l2_delay_ms: int | None,
    side: str | None,
    action: str | None,
    order_type: str | None,
    fill_price: float | None,
    price_tol: float,
) -> None:
    row["strict_l2_row_id"] = None if l2 is None else l2.row_id
    row["strict_l2_recv_ms"] = None if l2 is None else l2.recv_ms
    row["strict_l2_age_ms"] = l2_age_ms
    row["next_l2_row_id"] = None if next_l2 is None else next_l2.row_id
    row["next_l2_recv_ms"] = None if next_l2 is None else next_l2.recv_ms
    row["next_l2_delay_ms"] = next_l2_delay_ms
    row["side_bid_rank_or_level"] = None
    row["side_ask_rank_or_level"] = None
    row["price_level_size_at_place"] = None
    row["price_level_size_before_fill"] = None
    row["price_level_size_after_fill"] = None
    row["level_decrease_since_place"] = None
    row["same_price_depletion_qty_until_fill"] = None
    row["same_price_public_sell_qty_until_fill"] = None
    row["same_price_l1_decrease_qty_until_fill"] = None
    row["bid_price_dropped_before_fill"] = None
    row["ask_crossed_before_fill"] = None
    row["queue_ahead_estimate_at_place"] = None
    row["queue_context_policy"] = "no_private_order_place_available"

    if side not in {"YES", "NO"} or l2 is None:
        return

    bid_rank, bid_size = level_rank_and_size(l2.bids, fill_price, price_tol)
    ask_rank, ask_size = level_rank_and_size(l2.asks, fill_price, price_tol)
    row["side_bid_rank_or_level"] = bid_rank
    row["side_ask_rank_or_level"] = ask_rank

    level_kind, levels = execution_levels(action, order_type, l2)
    rank, size_before = level_rank_and_size(levels, fill_price, price_tol)
    if level_kind is not None:
        row["execution_level_kind"] = level_kind
        row["execution_level_rank"] = rank
        row["price_level_size_before_fill"] = size_before
        if next_l2 is not None:
            next_levels = next_l2.bids if level_kind == "bid" else next_l2.asks
            _next_rank, size_after = level_rank_and_size(next_levels, fill_price, price_tol)
            row["price_level_size_after_fill"] = size_after
            if size_before is not None and size_after is not None:
                row["same_price_depletion_qty_until_fill"] = max(float(size_before) - float(size_after), 0.0)
    else:
        row["execution_level_kind"] = None
        row["execution_level_rank"] = None
        row["price_level_size_before_fill"] = bid_size if bid_size is not None else ask_size


def base_event_row(
    *,
    day: str,
    market: Market,
    event_kind: str,
    event_ts_ms: int,
    activity_row_id: int | None,
    source_table: str,
    xuan_user: str,
    wallet_or_label: str | None,
    side: str | None,
    action: str | None,
    cycle_id: str,
    inventory_before: dict[str, float],
    inventory_after: dict[str, float],
) -> dict[str, Any]:
    return {
        "day": day,
        "market_id": market.condition_id,
        "condition_id": market.condition_id,
        "slug": market.slug,
        "round_start_ts": market.start_ms,
        "round_start_iso": iso_ms(market.start_ms),
        "market_end_ts": market.end_ms,
        "event_ts_ms": event_ts_ms,
        "event_iso": iso_ms(event_ts_ms),
        "event_kind": event_kind,
        "source_table": source_table,
        "source_row_id": activity_row_id,
        "xuan_account": xuan_user,
        "wallet_or_label": wallet_or_label,
        "cycle_id": cycle_id,
        "order_id": None,
        "side": side,
        "action": action,
        "order_type": "unknown",
        "limit_price": None,
        "fill_price": None,
        "fill_qty": None,
        "remaining_qty": None,
        "notional": None,
        "status": None,
        "reason": None,
        "placed_ts_ms": None,
        "first_seen_open_ts_ms": None,
        "first_fill_ts_ms": None,
        "last_fill_ts_ms": None,
        "cancel_ts_ms": None,
        "merge_ts_ms": None,
        "resting_ms_before_first_fill": None,
        "resting_ms_before_full_fill": None,
        "age_s_at_fill": None,
        "offset_s_at_place": None,
        "offset_s_at_fill": round((event_ts_ms - market.start_ms) / 1000.0, 3),
        "offset_s_at_merge": None,
        "inventory_yes_before": round(inventory_before["YES"], 8),
        "inventory_no_before": round(inventory_before["NO"], 8),
        "inventory_yes_after": round(inventory_after["YES"], 8),
        "inventory_no_after": round(inventory_after["NO"], 8),
        "matched_qty": None,
        "pair_cost": None,
        "pair_delay_ms": None,
        "merge_qty": None,
        "merge_cash_received": None,
        "residual_qty_after_cycle": round(inventory_after["YES"] + inventory_after["NO"], 8),
        "residual_cost_after_cycle": None,
        "winner_side": market.winner_side,
        "side_is_winner": side == market.winner_side if side in {"YES", "NO"} and market.winner_side in {"YES", "NO"} else None,
        "truth_level": "public_xuan_activity_only",
        "classification_method": "source_activity",
        "is_private_truth": False,
        "is_exact_maker_fill": False,
        "match_confidence": "low",
        "public_trade_row_id": None,
        "public_trade_ts_ms": None,
        "public_trade_taker_side": None,
        "public_trade_price": None,
        "public_trade_size": None,
        "public_trade_trade_id": None,
        "public_trade_maker_address": None,
        "public_trade_taker_address": None,
        "xuan_trade_row_id": None,
        "xuan_trade_trade_id": None,
        "xuan_trade_tx_hash": None,
        "xuan_activity_tx_hash": None,
    }


def fieldnames() -> list[str]:
    return [
        "day",
        "market_id",
        "condition_id",
        "slug",
        "round_start_ts",
        "round_start_iso",
        "market_end_ts",
        "event_ts_ms",
        "event_iso",
        "event_kind",
        "source_table",
        "source_row_id",
        "xuan_account",
        "wallet_or_label",
        "cycle_id",
        "order_id",
        "side",
        "action",
        "order_type",
        "limit_price",
        "fill_price",
        "fill_qty",
        "remaining_qty",
        "notional",
        "status",
        "reason",
        "placed_ts_ms",
        "first_seen_open_ts_ms",
        "first_fill_ts_ms",
        "last_fill_ts_ms",
        "cancel_ts_ms",
        "merge_ts_ms",
        "resting_ms_before_first_fill",
        "resting_ms_before_full_fill",
        "age_s_at_fill",
        "offset_s_at_place",
        "offset_s_at_fill",
        "offset_s_at_merge",
        "inventory_yes_before",
        "inventory_no_before",
        "inventory_yes_after",
        "inventory_no_after",
        "matched_qty",
        "pair_cost",
        "pair_delay_ms",
        "merge_qty",
        "merge_cash_received",
        "residual_qty_after_cycle",
        "residual_cost_after_cycle",
        "winner_side",
        "side_is_winner",
        "truth_level",
        "classification_method",
        "is_private_truth",
        "is_exact_maker_fill",
        "match_confidence",
        "public_trade_row_id",
        "public_trade_ts_ms",
        "public_trade_taker_side",
        "public_trade_price",
        "public_trade_size",
        "public_trade_trade_id",
        "public_trade_maker_address",
        "public_trade_taker_address",
        "xuan_trade_row_id",
        "xuan_trade_trade_id",
        "xuan_trade_tx_hash",
        "xuan_activity_tx_hash",
        "strict_l1_row_id",
        "strict_l1_recv_ms",
        "strict_l1_age_ms",
        "strict_l2_row_id",
        "strict_l2_recv_ms",
        "strict_l2_age_ms",
        "next_l2_row_id",
        "next_l2_recv_ms",
        "next_l2_delay_ms",
        "side_alignment",
        "high_side",
        "side_bid",
        "side_bid_sz",
        "side_ask",
        "side_ask_sz",
        "opp_bid",
        "opp_bid_sz",
        "opp_ask",
        "opp_ask_sz",
        "l1_pair_ask",
        "l1_pair_bid",
        "execution_level_kind",
        "execution_level_rank",
        "side_bid_rank_or_level",
        "side_ask_rank_or_level",
        "queue_ahead_estimate_at_place",
        "price_level_size_at_place",
        "price_level_size_before_fill",
        "price_level_size_after_fill",
        "level_decrease_since_place",
        "same_price_depletion_qty_until_fill",
        "same_price_public_sell_qty_until_fill",
        "same_price_l1_decrease_qty_until_fill",
        "bid_price_dropped_before_fill",
        "ask_crossed_before_fill",
        "queue_context_policy",
    ]


def build_market_events(
    *,
    conn: sqlite3.Connection,
    day: str,
    market: Market,
    activity_rows: list[sqlite3.Row],
    xuan_trades: list[XuanTrade],
    public_trades: list[PublicTrade],
    l1_books: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    xuan_user: str,
    public_match_window_ms: int,
    next_book_window_ms: int,
    price_tol: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: Counter[str] = Counter()
    rows: list[dict[str, Any]] = []
    inventory = {"YES": deque(), "NO": deque()}
    cycle_idx = 1

    def inv_qty() -> dict[str, float]:
        return {"YES": lots_qty(inventory["YES"]), "NO": lots_qty(inventory["NO"])}

    def inv_cost() -> float:
        return lots_cost(inventory["YES"]) + lots_cost(inventory["NO"])

    for activity in sorted(activity_rows, key=lambda row: (int(row["activity_ts_ms"]), int(row["id"]))):
        typ = str(activity["activity_type"] or "").upper()
        ts_ms = int(activity["activity_ts_ms"])
        side = norm_side(activity["outcome_side"])
        action = norm_action(activity["side"]) or ("merge" if typ == "MERGE" else "redeem" if typ == "REDEEM" else None)
        cycle_id = f"{market.condition_id}:{cycle_idx:04d}"
        before = inv_qty()
        before_cost = inv_cost()
        fill_price = safe_float(activity["price"])
        qty = safe_float(activity["size"]) or 0.0
        notional = safe_float(activity["usdc_size"])
        if typ == "TRADE" and qty > 0 and notional is not None:
            fill_price = notional / qty

        xuan_trade = match_xuan_trade(activity, xuan_trades, public_match_window_ms) if typ == "TRADE" else None
        public_trade, public_match_method = (
            match_public_trade(activity=activity, xuan_trade=xuan_trade, public_trades=public_trades, window_ms=public_match_window_ms)
            if typ == "TRADE"
            else (None, None)
        )
        role = classify_role(
            action=action,
            xuan_user=xuan_user,
            xuan_trade=xuan_trade,
            public_trade=public_trade,
            match_method=public_match_method,
        )

        matched_qty = None
        pair_cost = None
        pair_delay_ms = None
        merge_cash_received = None
        event_kind = "fill" if typ == "TRADE" else typ.lower()
        status = None
        reason = None

        if typ == "TRADE" and side in {"YES", "NO"} and qty > 0:
            if action == "buy":
                unit_cost = fill_price if fill_price is not None else 0.0
                inventory[side].append(Lot(qty=qty, unit_cost=unit_cost, ts_ms=ts_ms, cycle_id=cycle_id))
                status = "filled"
            elif action == "sell":
                consume_fifo(inventory[side], qty)
                status = "filled"
        elif typ == "MERGE" and qty > 0:
            yes_qty, yes_cost, yes_first_ts, yes_last_ts, yes_short = consume_fifo(inventory["YES"], qty)
            no_qty, no_cost, no_first_ts, no_last_ts, no_short = consume_fifo(inventory["NO"], qty)
            matched_qty = min(yes_qty, no_qty)
            consumed_cost = yes_cost + no_cost
            pair_cost = consumed_cost / matched_qty if matched_qty and matched_qty > 0 else None
            first_ts_candidates = [x for x in (yes_first_ts, no_first_ts) if x is not None]
            pair_delay_ms = ts_ms - min(first_ts_candidates) if first_ts_candidates else None
            merge_cash_received = qty
            status = "merged"
            reason = "merge"
            cycle_idx += 1
        elif typ == "REDEEM":
            status = "redeemed"
            reason = "redeem"

        after = inv_qty()
        row = base_event_row(
            day=day,
            market=market,
            event_kind=event_kind,
            event_ts_ms=ts_ms,
            activity_row_id=int(activity["id"]),
            source_table="xuan_activity",
            xuan_user=xuan_user,
            wallet_or_label=str(activity["proxy_wallet"] or "") or (xuan_trade.proxy_wallet if xuan_trade else None),
            side=side,
            action=action,
            cycle_id=cycle_id,
            inventory_before=before,
            inventory_after=after,
        )
        row.update(role)
        row.update(
            {
                "fill_price": fill_price if typ == "TRADE" else None,
                "fill_qty": qty if typ == "TRADE" else None,
                "notional": (notional if notional is not None else (fill_price * qty if fill_price is not None and qty else None)),
                "status": status,
                "reason": reason,
                "first_fill_ts_ms": ts_ms if typ == "TRADE" else None,
                "last_fill_ts_ms": ts_ms if typ == "TRADE" else None,
                "age_s_at_fill": round((ts_ms - market.start_ms) / 1000.0, 3) if typ == "TRADE" else None,
                "merge_ts_ms": ts_ms if typ == "MERGE" else None,
                "offset_s_at_merge": round((ts_ms - market.start_ms) / 1000.0, 3) if typ == "MERGE" else None,
                "matched_qty": matched_qty,
                "pair_cost": pair_cost,
                "pair_delay_ms": pair_delay_ms,
                "merge_qty": qty if typ == "MERGE" else None,
                "merge_cash_received": merge_cash_received,
                "residual_cost_after_cycle": round(inv_cost(), 8),
                "xuan_activity_tx_hash": str(activity["tx_hash"] or "") or None,
                "xuan_trade_row_id": None if xuan_trade is None else xuan_trade.row_id,
                "xuan_trade_trade_id": None if xuan_trade is None else xuan_trade.trade_id,
                "xuan_trade_tx_hash": None if xuan_trade is None else xuan_trade.tx_hash,
            }
        )
        if public_trade is not None:
            row.update(
                {
                    "public_trade_row_id": public_trade.row_id,
                    "public_trade_ts_ms": public_trade.trade_ts_ms,
                    "public_trade_taker_side": public_trade.taker_side,
                    "public_trade_price": public_trade.price,
                    "public_trade_size": public_trade.size,
                    "public_trade_trade_id": public_trade.trade_id,
                    "public_trade_maker_address": public_trade.maker_address,
                    "public_trade_taker_address": public_trade.taker_address,
                }
            )

        l1, l1_age = query_l1_at(conn, market.condition_id, ts_ms)
        l1_context(row, l1, l1_age, side)
        l2 = None
        l2_age = None
        next_l2 = None
        next_l2_delay = None
        if side in {"YES", "NO"}:
            l2, l2_age = query_l2_at(conn, market.condition_id, side, ts_ms)
            next_l2, next_l2_delay = query_l2_after(conn, market.condition_id, side, ts_ms, next_book_window_ms)
        l2_context(
            row,
            l2=l2,
            l2_age_ms=l2_age,
            next_l2=next_l2,
            next_l2_delay_ms=next_l2_delay,
            side=side,
            action=action,
            order_type=str(row.get("order_type") or "unknown"),
            fill_price=fill_price,
            price_tol=price_tol,
        )
        rows.append(row)
        counts[event_kind] += 1
        counts[f"truth_level:{row['truth_level']}"] += 1
        counts[f"order_type:{row['order_type']}"] += 1

    if rows and market.winner_side in {"YES", "NO"}:
        before = inv_qty()
        winner_qty = before[market.winner_side]
        settlement_recv = winner_qty
        settlement_row = base_event_row(
            day=day,
            market=market,
            event_kind="settlement",
            event_ts_ms=market.end_ms,
            activity_row_id=None,
            source_table="settlement_records",
            xuan_user=xuan_user,
            wallet_or_label=xuan_user,
            side=market.winner_side,
            action="settlement",
            cycle_id=f"{market.condition_id}:{cycle_idx:04d}",
            inventory_before=before,
            inventory_after={"YES": 0.0, "NO": 0.0},
        )
        settlement_row.update(
            {
                "status": "settled",
                "reason": market.resolution_source or "settlement",
                "notional": settlement_recv,
                "residual_qty_after_cycle": 0.0,
                "residual_cost_after_cycle": 0.0,
                "truth_level": "settlement_records",
                "classification_method": "settlement_records",
                "match_confidence": "high",
            }
        )
        l1, l1_age = query_l1_at(conn, market.condition_id, market.end_ms)
        l1_context(settlement_row, l1, l1_age, market.winner_side)
        l2_context(
            settlement_row,
            l2=None,
            l2_age_ms=None,
            next_l2=None,
            next_l2_delay_ms=None,
            side=market.winner_side,
            action="settlement",
            order_type="unknown",
            fill_price=None,
            price_tol=price_tol,
        )
        rows.append(settlement_row)
        counts["settlement"] += 1

    return rows, dict(counts)


def build_duckdb(tmp_dir: Path, csv_paths: list[Path], threads: int) -> dict[str, Any]:
    db_path = tmp_dir / "event_store.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    list_literal = "[" + ", ".join(quote_literal(path) for path in csv_paths) + "]"
    string_columns = {
        "day",
        "market_id",
        "condition_id",
        "slug",
        "round_start_iso",
        "event_iso",
        "event_kind",
        "source_table",
        "xuan_account",
        "wallet_or_label",
        "cycle_id",
        "order_id",
        "side",
        "action",
        "order_type",
        "status",
        "reason",
        "winner_side",
        "truth_level",
        "classification_method",
        "match_confidence",
        "public_trade_taker_side",
        "public_trade_trade_id",
        "public_trade_maker_address",
        "public_trade_taker_address",
        "xuan_trade_trade_id",
        "xuan_trade_tx_hash",
        "xuan_activity_tx_hash",
        "side_alignment",
        "high_side",
        "execution_level_kind",
        "queue_context_policy",
    }
    type_literal = "{" + ", ".join(f"{quote_literal(column)}: 'VARCHAR'" for column in sorted(string_columns)) + "}"
    conn.execute(
        f"""
        CREATE TABLE xuan_public_execution_events AS
        SELECT *
        FROM read_csv({list_literal}, header=true, union_by_name=true, auto_detect=true, types={type_literal})
        """
    )
    total_rows = int(conn.execute("SELECT COUNT(*) FROM xuan_public_execution_events").fetchone()[0])
    event_kind_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT event_kind, COUNT(*) FROM xuan_public_execution_events GROUP BY event_kind ORDER BY event_kind"
        ).fetchall()
    }
    truth_level_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT truth_level, COUNT(*) FROM xuan_public_execution_events GROUP BY truth_level ORDER BY truth_level"
        ).fetchall()
    }
    order_type_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT order_type, COUNT(*) FROM xuan_public_execution_events GROUP BY order_type ORDER BY order_type"
        ).fetchall()
    }
    day_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT day, COUNT(*) FROM xuan_public_execution_events GROUP BY day ORDER BY day"
        ).fetchall()
    }
    exact_maker_fill_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM xuan_public_execution_events WHERE event_kind='fill' AND order_type='maker' AND is_exact_maker_fill"
        ).fetchone()[0]
    )
    maker_inferred_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM xuan_public_execution_events WHERE event_kind='fill' AND order_type='maker'"
        ).fetchone()[0]
    )
    dataset_dir = tmp_dir / "dataset"
    dataset_dir.mkdir(exist_ok=True)
    conn.execute(
        f"""
        COPY (SELECT * FROM xuan_public_execution_events)
        TO {quote_literal(dataset_dir)}
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day), OVERWRITE_OR_IGNORE TRUE)
        """
    )
    conn.execute("CHECKPOINT")
    conn.close()
    parquet_files = sorted(path.relative_to(tmp_dir).as_posix() for path in dataset_dir.rglob("*.parquet"))
    return {
        "duckdb": "event_store.duckdb",
        "duckdb_table": "xuan_public_execution_events",
        "parquet_glob": "dataset/**/*.parquet",
        "parquet_files": parquet_files,
        "row_count": total_rows,
        "event_kind_counts": event_kind_counts,
        "truth_level_counts": truth_level_counts,
        "order_type_counts": order_type_counts,
        "day_counts": day_counts,
        "exact_maker_fill_rows": exact_maker_fill_rows,
        "maker_fill_rows_including_inferred": maker_inferred_rows,
    }


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"store already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def write_csv_header(path: Path, fields: list[str]) -> csv.DictWriter[str]:
    handle = path.open("w", newline="", encoding="utf-8")
    writer: csv.DictWriter[str] = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    setattr(writer, "_xuan_handle", handle)
    return writer


def close_writer(writer: csv.DictWriter[str]) -> None:
    handle = getattr(writer, "_xuan_handle", None)
    if handle is not None:
        handle.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default=DEFAULT_STORE_NAME)
    parser.add_argument("--days", required=True)
    parser.add_argument("--label")
    parser.add_argument("--xuan-user", default=DEFAULT_XUAN_USER)
    parser.add_argument("--public-match-window-ms", type=int, default=DEFAULT_PUBLIC_MATCH_WINDOW_MS)
    parser.add_argument("--next-book-window-ms", type=int, default=DEFAULT_NEXT_BOOK_WINDOW_MS)
    parser.add_argument("--price-tol", type=float, default=1e-9)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--progress-every-markets", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    days = parse_days(args.days)
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
        fields = fieldnames()
        csv_paths: list[Path] = []
        source_replay: list[dict[str, Any]] = []
        build_counts: dict[str, Any] = {}
        total_counts: Counter[str] = Counter()
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
                writer = write_csv_header(day_csv, fields)
                day_counts: Counter[str] = Counter()
                try:
                    with connect_ro(db_path) as conn:
                        markets = fetch_markets(conn)
                        xuan_activity = load_xuan_activity(conn, args.xuan_user, markets)
                        xuan_trades_by_condition = load_xuan_trades(conn, args.xuan_user)
                        day_counts["markets"] = len(markets)
                        day_counts["markets_with_xuan_activity"] = len(xuan_activity)
                        for idx, (condition_id, activity_rows) in enumerate(sorted(xuan_activity.items()), start=1):
                            market = markets[condition_id]
                            event_times = [int(row["activity_ts_ms"]) for row in activity_rows]
                            # The store only needs strict context around xuan public events,
                            # plus settlement. Loading full 5-minute dense L2 per market is
                            # materially slower and provides no extra event truth.
                            min_event_ts = min(event_times)
                            max_event_ts = max(event_times)
                            query_start_ms = max(market.start_ms, min_event_ts - args.public_match_window_ms - 5_000)
                            query_end_ms = min(market.end_ms, max_event_ts + args.next_book_window_ms + 5_000)
                            public_trades = load_public_trades(
                                conn,
                                condition_id,
                                query_start_ms - args.public_match_window_ms,
                                query_end_ms + args.public_match_window_ms,
                            )
                            rows, counts = build_market_events(
                                conn=conn,
                                day=day,
                                market=market,
                                activity_rows=activity_rows,
                                xuan_trades=xuan_trades_by_condition.get(condition_id, []),
                                public_trades=public_trades,
                                l1_books=[],
                                l2_by_side={"YES": [], "NO": []},
                                xuan_user=args.xuan_user,
                                public_match_window_ms=args.public_match_window_ms,
                                next_book_window_ms=args.next_book_window_ms,
                                price_tol=args.price_tol,
                            )
                            for row in rows:
                                writer.writerow(row)
                            day_counts.update(counts)
                            total_counts.update(counts)
                            if args.progress_every_markets > 0 and idx % args.progress_every_markets == 0:
                                print(
                                    json.dumps(
                                        {
                                            "stage": "build_day",
                                            "day": day,
                                            "markets_done": idx,
                                            "markets_with_xuan_activity": len(xuan_activity),
                                            "day_counts": dict(day_counts),
                                        },
                                        ensure_ascii=False,
                                        sort_keys=True,
                                    ),
                                    flush=True,
                                )
                finally:
                    close_writer(writer)
                build_counts[day] = dict(day_counts)

            outputs = build_duckdb(tmp_dir, csv_paths, args.duckdb_threads)
            manifest = {
                "schema_version": "xuan_public_execution_truth_v1",
                "store_name": args.store_name,
                "label": label,
                "days": days,
                "xuan_user": args.xuan_user,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "source": "replay_published_sqlite",
                "source_replay": source_replay,
                "build_counts": build_counts,
                "total_build_counts": dict(total_counts),
                "outputs": outputs,
                "truth_policy": {
                    "is_private_truth": False,
                    "public_execution_truth": "xuan_trades/xuan_activity rows are public execution observations.",
                    "strict_market_context": "L1/L2 context uses latest replay rows with recv_ms <= event_ts_ms.",
                    "maker_role_policy": (
                        "Maker/taker is exact only for address or exact trade-id/tx matches; "
                        "time/price/size matches are public-role inference and are labeled with lower confidence."
                    ),
                    "not_reconstructed": [
                        "private order_place",
                        "private cancel",
                        "remaining_qty from xuan private orders",
                        "true queue_ahead",
                        "private resting lifetime",
                    ],
                },
            }
            (tmp_dir / "EVENT_STORE_MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (tmp_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Xuan Public Execution Truth V1",
                        "",
                        "Public xuan execution/activity events with strict replay L1/L2 context.",
                        "",
                        "This is not private order truth. It does not reconstruct xuan order placement, cancellation, or real queue position.",
                        "",
                        "DuckDB table: `xuan_public_execution_events`.",
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

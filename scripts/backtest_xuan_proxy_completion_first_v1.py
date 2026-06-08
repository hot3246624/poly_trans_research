#!/usr/bin/env python3
"""Market-side backtest for Xuan-Proxy Completion-First V1.

This is not a xuan replica. It is a strategy-search backtest that uses the
current research conclusions to test whether a stricter, explainable strategy
can beat xuan's public 5-day reference metrics.

Data rules:
- read replay SQLite in read-only mode;
- do not read raw;
- do not use own execution truth;
- do not use winner_side for live decisions. winner_side is used only for
  ex-post diagnostics.

Execution model:
- first leg is maker-like at the current high-side bid;
- first fill is proxied by public taker SELL flow on that outcome;
- completion is bounded taker via L2 ask sweep on the opposite outcome;
- only one active tranche is allowed per independent mode/clip simulation.
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
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)

XUAN_REFERENCE = {
    "tranches": 4587,
    "first_winner_rate": 0.656856,
    "completion_30s_rate": 0.820798,
    "pair_cost_p50": 0.994604,
    "surplus_per_size": 0.020216,
    "pair_delay_p50_s": 10.0,
}


@dataclass(frozen=True)
class L1Book:
    recv_ms: int
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
    recv_ms: int
    side: str
    bids: tuple[tuple[float, float], ...]
    asks: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Trade:
    ts_ms: int
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class Fill:
    ts_ms: int
    filled_size: float
    proxy_vwap: float
    event_count: int


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(values)
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    w = pos - lo
    return round(xs[lo] * (1.0 - w) + xs[hi] * w, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_value(book: L1Book, side: str, field: str) -> float | None:
    if side == "YES":
        return getattr(book, f"yes_{field}")
    return getattr(book, f"no_{field}")


def side_bid(book: L1Book, side: str) -> float | None:
    return side_value(book, side, "bid_px")


def side_ask(book: L1Book, side: str) -> float | None:
    return side_value(book, side, "ask_px")


def side_bid_size(book: L1Book, side: str) -> float | None:
    return side_value(book, side, "bid_sz")


def mid(book: L1Book, side: str) -> float | None:
    bid = side_bid(book, side)
    ask = side_ask(book, side)
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def high_side(book: L1Book) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def momentum_side(book: L1Book, prev_book: L1Book | None) -> tuple[str | None, float | None]:
    if prev_book is None:
        return None, None
    deltas: list[tuple[float, str]] = []
    for side in ("YES", "NO"):
        current = side_bid(book, side)
        previous = side_bid(prev_book, side)
        if current is None or previous is None:
            continue
        deltas.append((current - previous, side))
    if not deltas:
        return None, None
    deltas.sort(reverse=True)
    return deltas[0][1], deltas[0][0]


def l1_spread_ticks(book: L1Book, side: str) -> float | None:
    bid = side_bid(book, side)
    ask = side_ask(book, side)
    if bid is None or ask is None:
        return None
    return round((ask - bid) * 100.0, 6)


def parse_clip_sizes(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def parse_schedule(stages: list[dict[str, Any]]) -> list[tuple[int, float]]:
    return sorted((int(stage["deadline_s"]), float(stage["pair_cost_ceiling"])) for stage in stages)


def schedule_name(schedule: list[tuple[int, float]]) -> str:
    return "_".join(f"{deadline}s_{ceiling:g}" for deadline, ceiling in schedule)


def load_strategy_modes(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    modes = []
    for mode in data["completion_controller"]["modes"]:
        modes.append({"name": mode["name"], "schedule": parse_schedule(mode["stages"])})
    return modes


def parse_extra_schedules(value: str) -> list[dict[str, Any]]:
    modes = []
    for item in value.split(";"):
        item = item.strip()
        if not item:
            continue
        name, raw_schedule = item.split("=", 1)
        schedule = []
        for part in raw_schedule.split(","):
            if not part.strip():
                continue
            deadline, ceiling = part.split(":", 1)
            schedule.append((int(deadline), float(ceiling)))
        modes.append({"name": name.strip(), "schedule": sorted(schedule)})
    return modes


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT MAX(x) FROM (
          SELECT MAX(recv_ms) AS x FROM md_book_l1
          UNION ALL
          SELECT MAX(recv_ms) AS x FROM md_book_l2
          UNION ALL
          SELECT MAX(trade_ts_ms) AS x FROM md_trades WHERE trade_ts_ms IS NOT NULL
        )
        """
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def load_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC'
          AND m.interval_sec = 300
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if PLANNED_OUTAGE_START_MS <= start_ms < PLANNED_OUTAGE_END_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        out.append(row)
    return out


def load_l1_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[L1Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms, capture_seq
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    return [
        L1Book(
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


def ask_levels(row: sqlite3.Row) -> tuple[tuple[float, float], ...]:
    levels = []
    for idx in range(1, 6):
        px = row[f"ask{idx}_px"]
        sz = row[f"ask{idx}_sz"]
        if px is None or sz is None:
            continue
        if float(sz) <= 0.0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


def bid_levels(row: sqlite3.Row) -> tuple[tuple[float, float], ...]:
    levels = []
    for idx in range(1, 6):
        px = row[f"bid{idx}_px"]
        sz = row[f"bid{idx}_sz"]
        if px is None or sz is None:
            continue
        if float(sz) <= 0.0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


def load_l2_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[L2Book]]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND recv_ms >= ?
          AND recv_ms < ?
          AND market_side IN ('YES', 'NO')
        ORDER BY recv_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        bids = bid_levels(row)
        asks = ask_levels(row)
        if not bids and not asks:
            continue
        side = str(row["market_side"])
        out[side].append(L2Book(recv_ms=int(row["recv_ms"]), side=side, bids=bids, asks=asks))
    return out


def load_sell_trades(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[Trade]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms < ?
          AND taker_side = 'SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        side = str(row["market_side"])
        out[side].append(
            Trade(ts_ms=int(row["trade_ts_ms"]), side=side, price=float(row["price"]), size=float(row["size"]))
        )
    return out


def sample_indices(books: list[L1Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + sample_interval_ms
    return out


def latest_l1_index_at_or_before(times: list[int], ts_ms: int) -> int | None:
    idx = bisect.bisect_right(times, ts_ms) - 1
    return idx if idx >= 0 else None


def latest_l2(books: list[L2Book], times: list[int], ts_ms: int, max_age_ms: int) -> tuple[L2Book | None, int | None]:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None, None
    book = books[idx]
    age = ts_ms - book.recv_ms
    if age < 0 or age > max_age_ms:
        return None, None
    return book, age


def sweep_vwap(book: L2Book, target_size: float) -> tuple[float | None, float, float | None]:
    filled = 0.0
    notional = 0.0
    worst = None
    for px, sz in book.asks:
        use = min(sz, target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * px
        worst = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst
    return None, filled, worst


def bid_sweep_vwap(book: L2Book, target_size: float) -> tuple[float | None, float, float | None]:
    filled = 0.0
    notional = 0.0
    worst = None
    for px, sz in book.bids:
        use = min(sz, target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * px
        worst = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst
    return None, filled, worst


def sell_fill(
    trades: list[Trade],
    times: list[int],
    start_ms: int,
    end_ms: int,
    max_price: float,
    target_size: float,
) -> Fill | None:
    idx = bisect.bisect_left(times, start_ms)
    filled = 0.0
    notional = 0.0
    event_count = 0
    while idx < len(trades):
        trade = trades[idx]
        if trade.ts_ms > end_ms:
            return None
        if trade.price <= max_price:
            use = min(trade.size, target_size - filled)
            filled += use
            notional += use * trade.price
            event_count += 1
            if filled + 1e-9 >= target_size:
                return Fill(ts_ms=trade.ts_ms, filled_size=filled, proxy_vwap=notional / filled, event_count=event_count)
        idx += 1
    return None


def first_completion_by_schedule(
    l2_books: list[L2Book],
    l2_times: list[int],
    start_ms: int,
    end_ms: int,
    target_size: float,
    first_price: float,
    schedule: list[tuple[int, float]],
) -> dict[str, Any] | None:
    segment_start_ms = start_ms
    previous_deadline_s = 0
    for deadline_s, pair_cost_ceiling in schedule:
        if deadline_s <= previous_deadline_s:
            continue
        segment_end_ms = min(start_ms + deadline_s * 1000, end_ms)
        start_idx = bisect.bisect_left(l2_times, segment_start_ms)
        end_idx = bisect.bisect_right(l2_times, segment_end_ms)
        for book in l2_books[start_idx:end_idx]:
            vwap, filled, worst = sweep_vwap(book, target_size)
            if vwap is None:
                continue
            pair_cost = first_price + vwap
            if pair_cost <= pair_cost_ceiling + 1e-9:
                return {
                    "completion_ts_ms": book.recv_ms,
                    "completion_delay_s": (book.recv_ms - start_ms) / 1000.0,
                    "completion_vwap": vwap,
                    "completion_worst_px": worst,
                    "completion_stage_deadline_s": deadline_s,
                    "completion_pair_cost_ceiling": pair_cost_ceiling,
                    "pair_cost": pair_cost,
                    "pair_surplus": 1.0 - pair_cost,
                }
        previous_deadline_s = deadline_s
        segment_start_ms = segment_end_ms
    return None


def min_pair_cost_window(
    l2_books: list[L2Book],
    l2_times: list[int],
    start_ms: int,
    end_ms: int,
    target_size: float,
    first_price: float,
) -> dict[str, Any]:
    start_idx = bisect.bisect_left(l2_times, start_ms)
    end_idx = bisect.bisect_right(l2_times, end_ms)
    best: dict[str, Any] = {"min_pair_cost": None, "min_ts_ms": None, "min_delay_s": None, "min_completion_vwap": None}
    for book in l2_books[start_idx:end_idx]:
        vwap, _filled, _worst = sweep_vwap(book, target_size)
        if vwap is None:
            continue
        pair_cost = first_price + vwap
        if best["min_pair_cost"] is None or pair_cost < float(best["min_pair_cost"]):
            best = {
                "min_pair_cost": pair_cost,
                "min_ts_ms": book.recv_ms,
                "min_delay_s": (book.recv_ms - start_ms) / 1000.0,
                "min_completion_vwap": vwap,
            }
    return best


def first_sell_exit(
    l2_books: list[L2Book],
    l2_times: list[int],
    start_ms: int,
    end_ms: int,
    target_size: float,
    first_price: float,
    max_loss_per_share: float,
    force_deadline: bool,
) -> dict[str, Any] | None:
    min_sell_price = first_price - max_loss_per_share
    start_idx = bisect.bisect_left(l2_times, start_ms)
    end_idx = bisect.bisect_right(l2_times, end_ms)
    last_full: dict[str, Any] | None = None
    for book in l2_books[start_idx:end_idx]:
        vwap, filled, worst = bid_sweep_vwap(book, target_size)
        if vwap is None:
            continue
        item = {
            "exit_ts_ms": book.recv_ms,
            "exit_vwap": vwap,
            "exit_worst_px": worst,
            "exit_filled_size": filled,
            "exit_delay_s": (book.recv_ms - start_ms) / 1000.0,
            "exit_pnl_per_share": vwap - first_price,
            "exit_pnl_usdc": (vwap - first_price) * target_size,
        }
        last_full = item
        if vwap >= min_sell_price - 1e-9:
            item["exit_rule"] = "threshold"
            return item
    if force_deadline and last_full is not None:
        last_full["exit_rule"] = "deadline"
        return last_full
    return None


def decide_open(
    book: L1Book,
    first_side: str,
    first_price: float,
    first_l2_vwap: float,
    offset_s: float,
    base_clip: float,
) -> tuple[str, float, list[str]]:
    edge = first_l2_vwap - first_price
    spread = l1_spread_ticks(book, first_side)
    reasons = []
    if edge <= -0.01:
        return "block", 0.0, ["negative_l2_edge"]
    if offset_s < 30.0 and edge <= -0.01:
        return "block", 0.0, ["early_negative_l2_edge"]
    clip_mult = 0.5
    reasons.append("no_positive_signal_clip_down")
    if first_price < 0.40 and edge <= 0.03:
        clip_mult = min(clip_mult, 0.5)
        reasons.append("very_low_first_price_without_edge")
    if edge > 0.03:
        clip_mult = 1.0
        reasons = ["positive_l2_edge"]
        if spread is not None and spread <= 3.0:
            clip_mult = 1.25
            reasons.append("positive_l2_edge_clean_book")
    return "allow", base_clip * clip_mult, reasons


def should_hold_residual(row: dict[str, Any], args: argparse.Namespace) -> tuple[bool, str | None]:
    policy = args.residual_hold_policy
    if policy == "none":
        return False, None
    if policy == "always":
        return True, "always"
    try:
        min_pair = (
            None
            if row.get("min_pair_cost_seen_in_first_30s") in (None, "")
            else float(row["min_pair_cost_seen_in_first_30s"])
        )
    except (TypeError, ValueError):
        min_pair = None
    try:
        first_price = float(row.get("first_price") or 0.0)
    except (TypeError, ValueError):
        first_price = 0.0
    try:
        offset_s = float(row.get("candidate_offset_s") or 0.0)
    except (TypeError, ValueError):
        offset_s = 0.0

    min30_095_101 = min_pair is not None and 0.95 <= min_pair <= 1.01
    min30_099_101 = min_pair is not None and 0.99 <= min_pair <= 1.01
    price_080_082 = 0.80 <= first_price < 0.82
    price_084_086 = 0.84 <= first_price < 0.86
    offset_120_150 = 120 <= offset_s < 150
    offset_120_180 = 120 <= offset_s < 180

    if policy == "min30_095_101" and min30_095_101:
        return True, "min30_095_101"
    if policy == "min30_099_101" and min30_099_101:
        return True, "min30_099_101"
    if policy == "min30_099_101_or_price_080_082" and (min30_099_101 or price_080_082):
        return True, "min30_099_101_or_price_080_082"
    if policy == "price_084_086_or_offset_120_150" and (price_084_086 or offset_120_150):
        return True, "price_084_086_or_offset_120_150"
    if policy == "min30_095_101_or_offset_120_180" and (min30_095_101 or offset_120_180):
        return True, "min30_095_101_or_offset_120_180"
    return False, None


def candidate_from_l1(
    market: sqlite3.Row,
    book: L1Book,
    prev_book_1s: L1Book | None,
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    base_clip: float,
    args: argparse.Namespace,
) -> dict[str, Any] | None:
    offset_s = (book.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < args.min_offset_s or offset_s >= args.max_offset_s:
        return None
    prev_bid_delta_1s = None
    if args.first_side_mode == "best_prev_bid_momentum":
        first_side, prev_bid_delta_1s = momentum_side(book, prev_book_1s)
    else:
        first_side = high_side(book)
        if first_side is not None and prev_book_1s is not None:
            current_bid = side_bid(book, first_side)
            previous_bid = side_bid(prev_book_1s, first_side)
            if current_bid is not None and previous_bid is not None:
                prev_bid_delta_1s = current_bid - previous_bid
    if first_side is None:
        return None
    if args.min_prev_bid_delta_1s is not None:
        if prev_bid_delta_1s is None or prev_bid_delta_1s < args.min_prev_bid_delta_1s:
            return None
    raw_first_bid = side_bid(book, first_side)
    if raw_first_bid is None:
        return None
    first_price = max(0.0, raw_first_bid - args.first_bid_improvement)
    if first_price < args.min_first_price or first_price >= args.max_first_price:
        return None
    l2, age = latest_l2(l2_by_side[first_side], l2_times_by_side[first_side], book.recv_ms, args.max_l2_age_ms)
    if l2 is None or age is None:
        return {
            "status": "blocked_stale_or_missing_l2",
            "candidate_ts_ms": book.recv_ms,
            "candidate_iso": iso_ms(book.recv_ms),
            "candidate_offset_s": round(offset_s, 3),
            "first_side": first_side,
            "first_price": round(first_price, 6),
            "first_raw_bid_px": round(raw_first_bid, 6),
            "first_bid_improvement": round(args.first_bid_improvement, 6),
            "first_side_mode": args.first_side_mode,
            "prev_bid_delta_1s": None if prev_bid_delta_1s is None else round(prev_bid_delta_1s, 6),
            "base_clip": base_clip,
        }
    first_l2_vwap_base, filled_base, first_l2_worst = sweep_vwap(l2, base_clip)
    if first_l2_vwap_base is None:
        return {
            "status": "blocked_insufficient_first_l2_depth",
            "candidate_ts_ms": book.recv_ms,
            "candidate_iso": iso_ms(book.recv_ms),
            "candidate_offset_s": round(offset_s, 3),
            "first_side": first_side,
            "first_price": round(first_price, 6),
            "first_raw_bid_px": round(raw_first_bid, 6),
            "first_bid_improvement": round(args.first_bid_improvement, 6),
            "first_side_mode": args.first_side_mode,
            "prev_bid_delta_1s": None if prev_bid_delta_1s is None else round(prev_bid_delta_1s, 6),
            "base_clip": base_clip,
            "first_l2_filled_base": round(filled_base, 6),
        }
    decision, clip_size, reasons = decide_open(book, first_side, first_price, first_l2_vwap_base, offset_s, base_clip)
    first_l2_vwap, filled_actual, first_l2_worst_actual = sweep_vwap(l2, clip_size) if clip_size > 0 else (None, 0.0, None)
    if decision == "allow" and first_l2_vwap is None:
        return {
            "status": "blocked_insufficient_first_l2_depth_after_clip",
            "candidate_ts_ms": book.recv_ms,
            "candidate_iso": iso_ms(book.recv_ms),
            "candidate_offset_s": round(offset_s, 3),
            "first_side": first_side,
            "first_price": round(first_price, 6),
            "first_raw_bid_px": round(raw_first_bid, 6),
            "first_bid_improvement": round(args.first_bid_improvement, 6),
            "first_side_mode": args.first_side_mode,
            "prev_bid_delta_1s": None if prev_bid_delta_1s is None else round(prev_bid_delta_1s, 6),
            "base_clip": base_clip,
            "clip_size": round(clip_size, 6),
            "first_l2_filled_actual": round(filled_actual, 6),
        }
    return {
        "status": "open_allowed" if decision == "allow" else "open_blocked",
        "open_gate_decision": decision,
        "open_gate_reason": ",".join(reasons),
        "candidate_ts_ms": book.recv_ms,
        "candidate_iso": iso_ms(book.recv_ms),
        "candidate_offset_s": round(offset_s, 3),
        "first_side": first_side,
        "opposite_side": other(first_side),
        "first_price": round(first_price, 6),
        "first_raw_bid_px": round(raw_first_bid, 6),
        "first_bid_improvement": round(args.first_bid_improvement, 6),
        "first_side_mode": args.first_side_mode,
        "prev_bid_delta_1s": None if prev_bid_delta_1s is None else round(prev_bid_delta_1s, 6),
        "first_bid_size": side_bid_size(book, first_side),
        "first_l1_spread_ticks": l1_spread_ticks(book, first_side),
        "base_clip": base_clip,
        "clip_size": round(clip_size, 6),
        "first_l2_recv_ms": l2.recv_ms,
        "first_l2_age_ms": age,
        "first_l2_vwap": None if first_l2_vwap is None else round(first_l2_vwap, 6),
        "first_l2_worst_px": None if first_l2_worst_actual is None else round(first_l2_worst_actual, 6),
        "first_l2_edge": None if first_l2_vwap is None else round(first_l2_vwap - first_price, 6),
    }


def simulate_allowed_candidate(
    market: sqlite3.Row,
    candidate: dict[str, Any],
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    sells: dict[str, list[Trade]],
    sell_times: dict[str, list[int]],
    completion_mode: dict[str, Any],
    args: argparse.Namespace,
) -> dict[str, Any]:
    row = {
        "completion_mode": completion_mode["name"],
        "completion_schedule": schedule_name(completion_mode["schedule"]),
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_ms": int(market["start_ms"]),
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_ms": int(market["end_ms"]),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        "winner_side": market["winner_side"],
        **candidate,
        "first_fill": False,
        "completion_fill": False,
    }
    if candidate.get("open_gate_decision") != "allow":
        return row

    first_side = str(candidate["first_side"])
    first_price = float(candidate["first_price"])
    clip = float(candidate["clip_size"])
    queue_ahead = max(float(candidate.get("first_bid_size") or 0.0) * args.queue_ahead_fraction, 0.0)
    flow_required = clip + queue_ahead
    fill = sell_fill(
        sells[first_side],
        sell_times[first_side],
        int(candidate["candidate_ts_ms"]),
        min(int(candidate["candidate_ts_ms"]) + args.first_fill_timeout_s * 1000, int(market["end_ms"])),
        first_price,
        flow_required,
    )
    row["first_queue_ahead_size"] = round(queue_ahead, 6)
    row["first_flow_required_size"] = round(flow_required, 6)
    if fill is None:
        row["status"] = "no_first_fill"
        return row

    row.update(
        {
            "first_fill": True,
            "first_fill_ts_ms": fill.ts_ms,
            "first_fill_iso": iso_ms(fill.ts_ms),
            "first_fill_delay_s": round((fill.ts_ms - int(candidate["candidate_ts_ms"])) / 1000.0, 3),
            "first_fill_price": first_price,
            "first_fill_proxy_trade_vwap": round(fill.proxy_vwap, 6),
            "first_fill_event_count": fill.event_count,
            "first_is_winner": first_side == market["winner_side"] if market["winner_side"] in {"YES", "NO"} else None,
        }
    )

    opposite_side = str(candidate["opposite_side"])
    l2_books = l2_by_side[opposite_side]
    l2_times = l2_times_by_side[opposite_side]
    first_fill_ts = int(fill.ts_ms)
    min30 = min_pair_cost_window(
        l2_books,
        l2_times,
        first_fill_ts,
        min(first_fill_ts + 30_000, int(market["end_ms"])),
        clip,
        first_price,
    )
    row.update(
        {
            "min_pair_cost_seen_in_first_30s": None
            if min30["min_pair_cost"] is None
            else round(float(min30["min_pair_cost"]), 6),
            "min_pair_cost_30s_delay_s": None if min30["min_delay_s"] is None else round(float(min30["min_delay_s"]), 3),
        }
    )
    completion = first_completion_by_schedule(
        l2_books,
        l2_times,
        first_fill_ts,
        int(market["end_ms"]),
        clip,
        first_price,
        completion_mode["schedule"],
    )
    min_pair = min30["min_pair_cost"]
    cheap_window_seen = min_pair is not None and float(min_pair) <= args.slow_continuation_threshold
    if completion is None and args.slow_continuation_deadline_s > 30 and cheap_window_seen:
        slow_schedule = [(args.slow_continuation_deadline_s, args.slow_continuation_ceiling)]
        slow = first_completion_by_schedule(
            l2_books,
            l2_times,
            first_fill_ts,
            min(first_fill_ts + args.slow_continuation_deadline_s * 1000, int(market["end_ms"])),
            clip,
            first_price,
            slow_schedule,
        )
        row["slow_gate_decision"] = "allow_slow_continuation"
        row["slow_continuation_reason"] = "cheap_window_seen"
        if slow is not None:
            completion = slow
            row["slow_continuation_fill"] = True
    if completion is None and args.repair_ceiling > 0:
        no_cheap = min_pair is None or float(min_pair) > args.no_cheap_window_threshold
        if no_cheap:
            repair_schedule = [(args.repair_deadline_s, args.repair_ceiling)]
            repair = first_completion_by_schedule(
                l2_books,
                l2_times,
                first_fill_ts + 30_000,
                min(first_fill_ts + args.repair_deadline_s * 1000, int(market["end_ms"])),
                clip,
                first_price,
                repair_schedule,
            )
            if repair is not None:
                completion = repair
                row["repair_forced_reason"] = "no_cheap_window"
                row["slow_gate_decision"] = "force_repair"
    if completion is None:
        no_cheap = min_pair is None or float(min_pair) > args.no_cheap_window_threshold
        if min_pair is None:
            row["slow_gate_decision"] = "force_repair_missing_min_pair"
        elif float(min_pair) <= 0.95:
            row["slow_gate_decision"] = row.get("slow_gate_decision") or "allow_slow_continuation"
        else:
            row["slow_gate_decision"] = "force_repair"
        hold_residual, hold_reason = should_hold_residual(row, args)
        if hold_residual:
            row["residual_hold_policy"] = args.residual_hold_policy
            row["residual_hold_reason"] = hold_reason
            row["status"] = "held_residual"
            return row
        exit_triggered = args.emergency_exit_trigger == "any_unclosed" or no_cheap
        if args.emergency_exit_mode != "none" and exit_triggered:
            exit_start_ms = first_fill_ts + args.emergency_exit_start_s * 1000
            exit_end_ms = min(first_fill_ts + args.emergency_exit_deadline_s * 1000, int(market["end_ms"]))
            exit_result = first_sell_exit(
                l2_by_side[first_side],
                l2_times_by_side[first_side],
                exit_start_ms,
                exit_end_ms,
                clip,
                first_price,
                args.emergency_exit_max_loss,
                args.emergency_exit_mode == "deadline",
            )
            if exit_result is not None:
                row.update(
                    {
                        "exit_fill": True,
                        "exit_ts_ms": exit_result["exit_ts_ms"],
                        "exit_iso": iso_ms(int(exit_result["exit_ts_ms"])),
                        "exit_vwap": round(float(exit_result["exit_vwap"]), 6),
                        "exit_worst_px": None
                        if exit_result["exit_worst_px"] is None
                        else round(float(exit_result["exit_worst_px"]), 6),
                        "exit_delay_s": round(float(exit_result["exit_delay_s"]), 3),
                        "exit_pnl_per_share": round(float(exit_result["exit_pnl_per_share"]), 6),
                        "exit_pnl_usdc": round(float(exit_result["exit_pnl_usdc"]), 6),
                        "exit_rule": exit_result["exit_rule"],
                        "status": "exited_first_leg",
                    }
                )
                return row
        row["status"] = "completion_not_filled"
        return row

    pair_cost = float(completion["pair_cost"])
    row.update(
        {
            "completion_fill": True,
            "completion_ts_ms": completion["completion_ts_ms"],
            "completion_iso": iso_ms(int(completion["completion_ts_ms"])),
            "completion_delay_s": round(float(completion["completion_delay_s"]), 3),
            "completion_vwap": round(float(completion["completion_vwap"]), 6),
            "completion_worst_px": None
            if completion["completion_worst_px"] is None
            else round(float(completion["completion_worst_px"]), 6),
            "completion_stage_deadline_s": completion["completion_stage_deadline_s"],
            "completion_pair_cost_ceiling": completion["completion_pair_cost_ceiling"],
            "pair_cost": round(pair_cost, 6),
            "pair_surplus": round(1.0 - pair_cost, 6),
            "surplus_usdc": round((1.0 - pair_cost) * clip, 6),
            "status": "closed",
        }
    )
    return row


def simulate_market(
    market: sqlite3.Row,
    l1_books: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    sells: dict[str, list[Trade]],
    completion_mode: dict[str, Any],
    base_clip: float,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    l2_times_by_side = {side: [book.recv_ms for book in xs] for side, xs in l2_by_side.items()}
    sell_times = {side: [trade.ts_ms for trade in xs] for side, xs in sells.items()}
    l1_times = [book.recv_ms for book in l1_books]
    sample_idxs = sample_indices(l1_books, int(market["start_ms"]) + args.min_offset_s * 1000, int(args.sample_interval_s * 1000))
    rows: list[dict[str, Any]] = []
    cursor_ms = int(market["start_ms"]) + args.min_offset_s * 1000
    market_stop_ms = int(market["end_ms"]) - args.tail_freeze_s * 1000
    for idx in sample_idxs:
        book = l1_books[idx]
        if book.recv_ms < cursor_ms:
            continue
        if book.recv_ms >= market_stop_ms:
            break
        prev_idx = latest_l1_index_at_or_before(l1_times, book.recv_ms - 1000)
        prev_book_1s = l1_books[prev_idx] if prev_idx is not None else None
        candidate = candidate_from_l1(market, book, prev_book_1s, l2_by_side, l2_times_by_side, base_clip, args)
        if candidate is None:
            continue
        if candidate.get("open_gate_decision") != "allow":
            candidate.update(
                {
                    "completion_mode": completion_mode["name"],
                    "completion_schedule": schedule_name(completion_mode["schedule"]),
                    "slug": market["slug"],
                    "condition_id": market["condition_id"],
                    "round_start_ms": int(market["start_ms"]),
                    "round_end_ms": int(market["end_ms"]),
                    "winner_side": market["winner_side"],
                    "first_fill": False,
                    "completion_fill": False,
                }
            )
            rows.append(candidate)
            cursor_ms = book.recv_ms + int(args.sample_interval_s * 1000)
            continue
        row = simulate_allowed_candidate(
            market, candidate, l2_by_side, l2_times_by_side, sells, sell_times, completion_mode, args
        )
        rows.append(row)
        if row.get("first_fill") is True:
            # One active tranche. If it fails to close, no more opens in this market.
            if row.get("completion_fill") is True:
                cursor_ms = int(row["completion_ts_ms"]) + args.cooldown_s * 1000
                continue
            break
        # A maker-first attempt that does not fill still occupies the open
        # slot until its timeout. Skipping the overlapping window is both more
        # realistic and materially faster.
        cursor_ms = book.recv_ms + args.first_fill_timeout_s * 1000
    return rows


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    open_allowed = [row for row in rows if row.get("open_gate_decision") == "allow"]
    open_blocked = [row for row in rows if row.get("open_gate_decision") == "block"]
    first_fills = [row for row in rows if row.get("first_fill") is True]
    closed = [row for row in rows if row.get("completion_fill") is True]
    exited = [row for row in rows if row.get("exit_fill") is True]
    size_sum = sum(float(row.get("clip_size") or 0.0) for row in closed)
    surplus = sum(float(row.get("surplus_usdc") or 0.0) for row in closed)
    closed30 = [row for row in closed if float(row.get("completion_delay_s") or 999999.0) <= 30.0]
    no_cheap_slow_wait = [
        row
        for row in first_fills
        if row.get("completion_fill") is not True
        and row.get("exit_fill") is not True
        and (
            row.get("min_pair_cost_seen_in_first_30s") in (None, "")
            or float(row.get("min_pair_cost_seen_in_first_30s")) > 0.99
        )
    ]
    first_winner = [row for row in first_fills if row.get("first_is_winner") is True]
    residual = [row for row in first_fills if row.get("completion_fill") is not True and row.get("exit_fill") is not True]
    residual_winner = [row for row in residual if row.get("first_is_winner") is True]
    residual_loser = [row for row in residual if row.get("first_is_winner") is False]
    residual_qty = sum(float(row.get("clip_size") or 0.0) for row in residual)
    residual_cost = sum(
        float(row.get("clip_size") or 0.0)
        * float(row.get("first_fill_price") or row.get("first_price") or 0.0)
        for row in residual
    )
    residual_settlement_pnl = 0.0
    for row in residual:
        clip = float(row.get("clip_size") or 0.0)
        first_price = float(row.get("first_fill_price") or row.get("first_price") or 0.0)
        if row.get("first_is_winner") is True:
            residual_settlement_pnl += (1.0 - first_price) * clip
        elif row.get("first_is_winner") is False:
            residual_settlement_pnl -= first_price * clip
    closed_spend = sum(
        float(row.get("clip_size") or 0.0)
        * (
            float(row.get("first_fill_price") or row.get("first_price") or 0.0)
            + float(row.get("completion_vwap") or 0.0)
        )
        for row in closed
    )
    exit_pnl = sum(float(row.get("exit_pnl_usdc") or 0.0) for row in exited)
    exit_spend = sum(
        float(row.get("clip_size") or 0.0)
        * float(row.get("first_fill_price") or row.get("first_price") or 0.0)
        for row in exited
    )
    residual_adjusted_pnl = surplus + exit_pnl + residual_settlement_pnl
    total_spend_with_residual = closed_spend + exit_spend + residual_cost
    total_trade_value = total_spend_with_residual + residual_adjusted_pnl
    closed_pair_cost_notional = sum(
        float(row.get("clip_size") or 0.0) * float(row.get("pair_cost") or 0.0) for row in closed
    )
    weighted_pair_cost_closed = closed_pair_cost_notional / size_sum if size_sum > 0 else None
    return {
        "attempt_count": len(rows),
        "open_allowed_count": len(open_allowed),
        "open_blocked_count": len(open_blocked),
        "open_allowed_rate": rate(len(open_allowed), len(rows)),
        "first_fill_count": len(first_fills),
        "first_fill_rate_among_allowed": rate(len(first_fills), len(open_allowed)),
        "closed_count": len(closed),
        "exit_count": len(exited),
        "closed_rate_among_first_fills": rate(len(closed), len(first_fills)),
        "flat_rate_among_first_fills": rate(len(closed) + len(exited), len(first_fills)),
        "completion_30s_rate_among_first_fills": rate(len(closed30), len(first_fills)),
        "first_winner_rate": rate(len(first_winner), len(first_fills)),
        "surplus_usdc": round(surplus, 6),
        "surplus_per_size": round(surplus / size_sum, 6) if size_sum else None,
        "closed_roi_on_closed_spend": round(surplus / closed_spend, 6) if closed_spend else None,
        "exit_pnl_usdc": round(exit_pnl, 6),
        "exit_pnl_per_exit": round(exit_pnl / len(exited), 6) if exited else None,
        "strategy_trade_cost_usdc": round(total_spend_with_residual, 6),
        "strategy_trade_value_usdc": round(total_trade_value, 6),
        "strategy_weighted_pair_cost_closed": round(weighted_pair_cost_closed, 6)
        if weighted_pair_cost_closed is not None
        else None,
        "residual_count": len(residual),
        "residual_winner_count": len(residual_winner),
        "residual_loser_count": len(residual_loser),
        "residual_qty": round(residual_qty, 6),
        "residual_cost": round(residual_cost, 6),
        "residual_settlement_pnl_usdc": round(residual_settlement_pnl, 6),
        "residual_adjusted_pnl_usdc": round(residual_adjusted_pnl, 6),
        "residual_adjusted_roi_on_total_spend": round(residual_adjusted_pnl / total_spend_with_residual, 6)
        if total_spend_with_residual
        else None,
        "pair_cost": summarize([row.get("pair_cost") for row in closed]),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.90), len(closed)),
        "pair_cost_lt_0_95_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.95), len(closed)),
        "first_fill_delay_s": summarize([row.get("first_fill_delay_s") for row in first_fills]),
        "completion_delay_s": summarize([row.get("completion_delay_s") for row in closed]),
        "min_pair_cost_seen_in_first_30s": summarize([row.get("min_pair_cost_seen_in_first_30s") for row in first_fills]),
        "no_cheap_window_unclosed_count": len(no_cheap_slow_wait),
        "status_counts": dict(sorted(CounterLike(row.get("status") for row in rows).items())),
    }


def CounterLike(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return out


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_mode_clip": {}, "by_day": {}}
    for key in sorted({(row.get("completion_mode"), float(row.get("base_clip") or 0.0)) for row in rows}):
        mode, clip = key
        xs = [row for row in rows if row.get("completion_mode") == mode and float(row.get("base_clip") or 0.0) == clip]
        out["by_mode_clip"][f"{mode}|base_clip={clip:g}"] = compact(xs)
    for day in sorted({str(row.get("day")) for row in rows}):
        out["by_day"][day] = compact([row for row in rows if str(row.get("day")) == day])
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan-Proxy Completion-First V1 Backtest",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        f"- strategy_config: `{report['strategy_config']}`",
        "- Read-only replay SQLite. No raw data.",
        "- First leg uses maker-bid public SELL-flow fill proxy; completion uses bounded L2 taker sweep.",
        "- This is market-side research, not own execution truth.",
        "",
        "## Xuan Reference",
        "",
        "| metric | xuan 5d |",
        "|---|---:|",
    ]
    for key, value in XUAN_REFERENCE.items():
        lines.append(f"| {key} | `{value}` |")
    lines.extend(
        [
            "",
            "## Mode Results",
            "",
            "| mode/clip | attempts | first fills | closed/fill | flat/fill | 30s/fill | pair p50 | w-pair | surplus/size | net PnL | ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in report["aggregate"]["by_mode_clip"].items():
        lines.append(
            f"| {key} | {item['attempt_count']} | {item['first_fill_count']} | "
            f"{item['closed_rate_among_first_fills']} | {item['flat_rate_among_first_fills']} | "
            f"{item['completion_30s_rate_among_first_fills']} | {item['pair_cost']['p50']} | "
            f"{item['strategy_weighted_pair_cost_closed']} | {item['surplus_per_size']} | "
            f"{item['residual_adjusted_pnl_usdc']} | "
            f"{item['residual_adjusted_roi_on_total_spend']} |"
        )
    lines.extend(
        [
            "",
            "## Residual Risk",
            "",
            "| mode/clip | exits | exit pnl | residual | loser residual | residual qty | closed surplus | residual settle pnl | net pnl | ROI |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in report["aggregate"]["by_mode_clip"].items():
        lines.append(
            f"| {key} | {item['exit_count']} | {item['exit_pnl_usdc']} | {item['residual_count']} | "
            f"{item['residual_loser_count']} | {item['residual_qty']} | {item['surplus_usdc']} | "
            f"{item['residual_settlement_pnl_usdc']} | {item['residual_adjusted_pnl_usdc']} | "
            f"{item['residual_adjusted_roi_on_total_spend']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A mode only matters if it beats xuan on surplus/size while keeping completion risk controlled.",
            "- Low first_fill_rate means the mode depends on queue position and must be validated in own dry-run.",
            "- first_winner_rate is ex-post diagnostics only; it is not used by the strategy.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--strategy-config", default="configs/xuan/xuan_proxy_completion_first_strategy_v1.json")
    parser.add_argument("--mode-names", default="", help="Comma-separated completion mode filter.")
    parser.add_argument(
        "--extra-schedules",
        default="",
        help="Additional modes, e.g. tight=2:0.95,30:0.99;profit=30:0.95",
    )
    parser.add_argument("--output-dir", default="data/exports/xuan_proxy_completion_first_v1_backtest")
    parser.add_argument("--base-clips", default="60,100,140")
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--first-fill-timeout-s", type=int, default=30)
    parser.add_argument("--queue-ahead-fraction", type=float, default=0.0)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--tail-freeze-s", type=int, default=60)
    parser.add_argument("--min-first-price", type=float, default=0.30)
    parser.add_argument("--max-first-price", type=float, default=0.90)
    parser.add_argument("--first-side-mode", choices=("high_side", "best_prev_bid_momentum"), default="high_side")
    parser.add_argument(
        "--min-prev-bid-delta-1s",
        type=float,
        default=None,
        help="Require selected side bid to have increased by at least this amount versus the latest L1 book <=1s earlier.",
    )
    parser.add_argument(
        "--first-bid-improvement",
        type=float,
        default=0.0,
        help="Subtract this amount from current best bid for maker-first entry research.",
    )
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--repair-ceiling", type=float, default=1.02)
    parser.add_argument("--repair-deadline-s", type=int, default=60)
    parser.add_argument("--no-cheap-window-threshold", type=float, default=0.99)
    parser.add_argument("--slow-continuation-threshold", type=float, default=0.95)
    parser.add_argument("--slow-continuation-ceiling", type=float, default=0.95)
    parser.add_argument("--slow-continuation-deadline-s", type=int, default=0)
    parser.add_argument(
        "--residual-hold-policy",
        choices=(
            "none",
            "always",
            "min30_095_101",
            "min30_099_101",
            "min30_099_101_or_price_080_082",
            "price_084_086_or_offset_120_150",
            "min30_095_101_or_offset_120_180",
        ),
        default="none",
    )
    parser.add_argument("--emergency-exit-mode", choices=("none", "threshold", "deadline"), default="none")
    parser.add_argument("--emergency-exit-trigger", choices=("no_cheap", "any_unclosed"), default="no_cheap")
    parser.add_argument("--emergency-exit-start-s", type=int, default=30)
    parser.add_argument("--emergency-exit-deadline-s", type=int, default=120)
    parser.add_argument("--emergency-exit-max-loss", type=float, default=0.08)
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [day.strip() for day in args.days.split(",") if day.strip()]
    modes = load_strategy_modes(Path(args.strategy_config))
    modes.extend(parse_extra_schedules(args.extra_schedules))
    if args.mode_names.strip():
        keep = {name.strip() for name in args.mode_names.split(",") if name.strip()}
        modes = [mode for mode in modes if mode["name"] in keep]
    base_clips = parse_clip_sizes(args.base_clips)
    rows: list[dict[str, Any]] = []
    db_summaries = []
    markets_seen = 0
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            db_summaries.append({"day": day, "exists": False, "markets": 0})
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            db_summaries.append({"day": day, "exists": True, "markets": len(markets)})
            for market in markets:
                if args.max_markets and markets_seen >= args.max_markets:
                    break
                markets_seen += 1
                market_start_ms = int(market["start_ms"])
                market_end_ms = int(market["end_ms"])
                max_schedule_deadline_s = max(
                    [deadline for mode in modes for deadline, _ceiling in mode["schedule"]] + [args.repair_deadline_s]
                )
                candidate_start_ms = market_start_ms + args.min_offset_s * 1000
                candidate_end_ms = min(market_start_ms + args.max_offset_s * 1000, market_end_ms)
                data_start_ms = max(market_start_ms, candidate_start_ms - args.max_l2_age_ms)
                data_end_ms = min(
                    market_end_ms,
                    candidate_end_ms + (args.first_fill_timeout_s + max_schedule_deadline_s) * 1000,
                )
                l1_books = load_l1_books(conn, market["condition_id"], candidate_start_ms, candidate_end_ms)
                if not l1_books:
                    continue
                l2_by_side = load_l2_books(conn, market["condition_id"], data_start_ms, data_end_ms)
                if not l2_by_side["YES"] or not l2_by_side["NO"]:
                    continue
                sells = load_sell_trades(conn, market["condition_id"], candidate_start_ms, data_end_ms)
                for mode in modes:
                    for clip in base_clips:
                        market_rows = simulate_market(market, l1_books, l2_by_side, sells, mode, clip, args)
                        for row in market_rows:
                            row["day"] = day
                        rows.extend(market_rows)
            if args.max_markets and markets_seen >= args.max_markets:
                break
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_proxy_completion_first_v1_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "strategy_config": str(Path(args.strategy_config).resolve()),
        "parameters": {
            "base_clips": base_clips,
            "sample_interval_s": args.sample_interval_s,
            "first_fill_timeout_s": args.first_fill_timeout_s,
            "queue_ahead_fraction": args.queue_ahead_fraction,
            "max_l2_age_ms": args.max_l2_age_ms,
            "min_offset_s": args.min_offset_s,
            "max_offset_s": args.max_offset_s,
            "tail_freeze_s": args.tail_freeze_s,
            "first_bid_improvement": args.first_bid_improvement,
            "first_side_mode": args.first_side_mode,
            "min_prev_bid_delta_1s": args.min_prev_bid_delta_1s,
            "repair_ceiling": args.repair_ceiling,
            "repair_deadline_s": args.repair_deadline_s,
            "slow_continuation_threshold": args.slow_continuation_threshold,
            "slow_continuation_ceiling": args.slow_continuation_ceiling,
            "slow_continuation_deadline_s": args.slow_continuation_deadline_s,
            "residual_hold_policy": args.residual_hold_policy,
            "emergency_exit_mode": args.emergency_exit_mode,
            "emergency_exit_trigger": args.emergency_exit_trigger,
            "emergency_exit_start_s": args.emergency_exit_start_s,
            "emergency_exit_deadline_s": args.emergency_exit_deadline_s,
            "emergency_exit_max_loss": args.emergency_exit_max_loss,
        },
        "xuan_reference": XUAN_REFERENCE,
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "xuan_proxy_completion_first_v1_rows.csv").resolve()),
            "summary_json": str((output_dir / "xuan_proxy_completion_first_v1_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_proxy_completion_first_v1_report.md").resolve()),
        },
    }
    (output_dir / "xuan_proxy_completion_first_v1_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_proxy_completion_first_v1_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "markets_seen": markets_seen}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Causal L2 sweep backtest for BTC 5m bounded-taker completion.

This script reads replay SQLite in read-only mode. It models both legs as
taker-like L2 sweeps:

- first leg crosses the high-side ask for the configured clip;
- completion scans future opposite-side L2 asks and fills at the first schedule
  stage whose sweep VWAP keeps pair cost below the stage ceiling.

It is still market-side replay, not own execution truth.
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
TRUSTED_START_MS = 1_777_274_700_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class L1Book:
    recv_ms: int
    yes_bid_px: float | None
    yes_ask_px: float | None
    no_bid_px: float | None
    no_ask_px: float | None


@dataclass(frozen=True)
class L2Book:
    recv_ms: int
    side: str
    asks: tuple[tuple[float, float], ...]


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
    w = pos - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
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


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_px(book: L1Book, side: str, kind: str) -> float | None:
    if side == "YES":
        return book.yes_bid_px if kind == "bid" else book.yes_ask_px
    return book.no_bid_px if kind == "bid" else book.no_ask_px


def mid(book: L1Book, side: str) -> float | None:
    bid = side_px(book, side, "bid")
    ask = side_px(book, side, "ask")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def high_side(book: L1Book) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def parse_schedule(value: str) -> list[tuple[int, float]]:
    out = []
    for part in value.split(","):
        if not part.strip():
            continue
        deadline_s, ceiling = part.split(":", 1)
        out.append((int(deadline_s), float(ceiling)))
    return sorted(out)


def schedule_name(schedule: list[tuple[int, float]]) -> str:
    return "_".join(f"{deadline}s_{ceiling:g}" for deadline, ceiling in schedule)


def load_modes(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    modes = []
    for mode in data["modes"]:
        modes.append(
            {
                "name": mode["name"],
                "offset_start_s": int(mode["offset_start_s"]),
                "offset_end_s": int(mode["offset_end_s"]),
                "first_price_min": float(mode["first_price_min"]),
                "first_price_max": float(mode["first_price_max"]),
                "clip_size": float(mode["clip_size"]),
                "residual_cap_qty_candidates": [float(x) for x in mode.get("residual_cap_qty_candidates", [])],
            }
        )
    return modes


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT MAX(x) FROM (
          SELECT MAX(recv_ms) AS x FROM md_book_l1
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
        WHERE m.symbol='BTC' AND m.interval_sec=300
        ORDER BY start_ms
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
        if row["winner_side"] not in ("YES", "NO"):
            continue
        out.append(row)
    return out


def load_l1_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[L1Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms
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
        )
        for row in rows
    ]


def ask_levels(row: sqlite3.Row) -> tuple[tuple[float, float], ...]:
    levels: list[tuple[float, float]] = []
    for i in range(1, 6):
        px = row[f"ask{i}_px"]
        sz = row[f"ask{i}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


def load_l2_books(
    conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int
) -> dict[str, list[L2Book]]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
          AND market_side IN ('YES', 'NO')
        ORDER BY recv_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        levels = ask_levels(row)
        if not levels:
            continue
        side = str(row["market_side"])
        out[side].append(L2Book(recv_ms=int(row["recv_ms"]), side=side, asks=levels))
    return out


def load_latest_l2_before(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    ts_ms: int,
    max_age_ms: int,
) -> L2Book | None:
    row = conn.execute(
        """
        SELECT recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (condition_id, side, ts_ms - max_age_ms, ts_ms),
    ).fetchone()
    if row is None:
        return None
    levels = ask_levels(row)
    if not levels:
        return None
    return L2Book(recv_ms=int(row["recv_ms"]), side=str(row["market_side"]), asks=levels)


def load_l2_window(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    start_ms: int,
    end_ms: int,
) -> list[L2Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, id
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    out = []
    for row in rows:
        levels = ask_levels(row)
        if levels:
            out.append(L2Book(recv_ms=int(row["recv_ms"]), side=str(row["market_side"]), asks=levels))
    return out


def sample_indices(books: list[L1Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + sample_interval_ms
    return out


def latest_l2(
    books: list[L2Book], times: list[int], ts_ms: int, max_age_ms: int
) -> tuple[L2Book | None, int | None]:
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
    worst_px = None
    for px, sz in book.asks:
        use = min(sz, target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * px
        worst_px = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst_px
    return None, filled, worst_px


def first_completion_by_schedule(
    l2_books: list[L2Book],
    l2_times: list[int],
    start_ms: int,
    market_end_ms: int,
    target_size: float,
    first_vwap: float,
    schedule: list[tuple[int, float]],
) -> dict[str, Any] | None:
    segment_start_ms = start_ms
    previous_deadline_s = 0
    for deadline_s, pair_cost_ceiling in schedule:
        if deadline_s <= previous_deadline_s:
            continue
        segment_end_ms = min(start_ms + deadline_s * 1000, market_end_ms)
        start_idx = bisect.bisect_left(l2_times, segment_start_ms)
        end_idx = bisect.bisect_right(l2_times, segment_end_ms)
        for book in l2_books[start_idx:end_idx]:
            vwap, filled, worst_px = sweep_vwap(book, target_size)
            if vwap is None:
                continue
            pair_cost = first_vwap + vwap
            if pair_cost <= pair_cost_ceiling + 1e-9:
                return {
                    "completion_ts_ms": book.recv_ms,
                    "completion_vwap": vwap,
                    "completion_worst_px": worst_px,
                    "completion_filled_size": filled,
                    "completion_delay_s": (book.recv_ms - start_ms) / 1000.0,
                    "completion_stage_deadline_s": deadline_s,
                    "completion_pair_cost_ceiling": pair_cost_ceiling,
                    "pair_cost": pair_cost,
                    "pair_surplus": 1.0 - pair_cost,
                }
        previous_deadline_s = deadline_s
        segment_start_ms = segment_end_ms
    return None


def candidate_matches(
    l1: L1Book,
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    market: sqlite3.Row,
    mode: dict[str, Any],
    max_l2_age_ms: int,
) -> dict[str, Any] | None:
    offset_s = (l1.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < mode["offset_start_s"] or offset_s >= mode["offset_end_s"]:
        return None
    first_side = high_side(l1)
    if first_side is None:
        return None
    first_l1_ask = side_px(l1, first_side, "ask")
    if first_l1_ask is None or first_l1_ask < mode["first_price_min"] or first_l1_ask >= mode["first_price_max"]:
        return None
    first_l2, first_l2_age_ms = latest_l2(
        l2_by_side[first_side], l2_times_by_side[first_side], l1.recv_ms, max_l2_age_ms
    )
    if first_l2 is None:
        return None
    clip = float(mode["clip_size"])
    first_vwap, first_filled_size, first_worst_px = sweep_vwap(first_l2, clip)
    if first_vwap is None:
        return None
    return {
        "candidate_ts_ms": l1.recv_ms,
        "candidate_iso": iso_ms(l1.recv_ms),
        "candidate_offset_s": round(offset_s, 3),
        "first_side": first_side,
        "opposite_side": other(first_side),
        "first_l1_ask": round(first_l1_ask, 6),
        "first_l2_age_ms": first_l2_age_ms,
        "first_vwap": round(first_vwap, 6),
        "first_worst_px": None if first_worst_px is None else round(first_worst_px, 6),
        "first_filled_size": round(first_filled_size, 6),
    }


def l1_candidate_matches(l1: L1Book, market: sqlite3.Row, mode: dict[str, Any]) -> dict[str, Any] | None:
    offset_s = (l1.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < mode["offset_start_s"] or offset_s >= mode["offset_end_s"]:
        return None
    first_side = high_side(l1)
    if first_side is None:
        return None
    first_l1_ask = side_px(l1, first_side, "ask")
    if first_l1_ask is None or first_l1_ask < mode["first_price_min"] or first_l1_ask >= mode["first_price_max"]:
        return None
    return {
        "candidate_ts_ms": l1.recv_ms,
        "candidate_iso": iso_ms(l1.recv_ms),
        "candidate_offset_s": round(offset_s, 3),
        "first_side": first_side,
        "opposite_side": other(first_side),
        "first_l1_ask": round(first_l1_ask, 6),
    }


def simulate_candidate_db(
    conn: sqlite3.Connection,
    market: sqlite3.Row,
    l1: L1Book,
    mode: dict[str, Any],
    cap: float,
    schedule: list[tuple[int, float]],
    max_l2_age_ms: int,
) -> dict[str, Any] | None:
    candidate = l1_candidate_matches(l1, market, mode)
    if candidate is None:
        return None
    clip = float(mode["clip_size"])
    condition_id = str(market["condition_id"])
    first_l2 = load_latest_l2_before(
        conn,
        condition_id,
        str(candidate["first_side"]),
        int(candidate["candidate_ts_ms"]),
        max_l2_age_ms,
    )
    if first_l2 is None:
        return None
    first_vwap, first_filled_size, first_worst_px = sweep_vwap(first_l2, clip)
    if first_vwap is None:
        return None

    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "schedule": schedule_name(schedule),
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": condition_id,
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        "winner_side": market["winner_side"],
        **candidate,
        "clip_size": clip,
        "first_is_winner": candidate["first_side"] == market["winner_side"],
        "first_l2_recv_ms": first_l2.recv_ms,
        "first_l2_age_ms": int(candidate["candidate_ts_ms"]) - first_l2.recv_ms,
        "first_vwap": round(first_vwap, 6),
        "first_worst_px": None if first_worst_px is None else round(first_worst_px, 6),
        "first_filled_size": round(first_filled_size, 6),
        "first_fill": clip <= cap,
        "first_fill_ts_ms": candidate["candidate_ts_ms"] if clip <= cap else None,
        "first_fill_iso": candidate["candidate_iso"] if clip <= cap else None,
        "completion_fill": False,
        "completion_delay_s": None,
        "completion_vwap": None,
        "completion_worst_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row

    max_deadline_s = max(deadline_s for deadline_s, _ in schedule)
    window_end_ms = min(int(candidate["candidate_ts_ms"]) + max_deadline_s * 1000, int(market["end_ms"]))
    l2_books = load_l2_window(
        conn,
        condition_id,
        str(candidate["opposite_side"]),
        int(candidate["candidate_ts_ms"]),
        window_end_ms,
    )
    l2_times = [book.recv_ms for book in l2_books]
    completion = first_completion_by_schedule(
        l2_books,
        l2_times,
        int(candidate["candidate_ts_ms"]),
        int(market["end_ms"]),
        clip,
        first_vwap,
        schedule,
    )
    if completion is None:
        row["status"] = "schedule_not_filled"
        return row
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
            "completion_filled_size": round(float(completion["completion_filled_size"]), 6),
            "completion_stage_deadline_s": completion["completion_stage_deadline_s"],
            "completion_pair_cost_ceiling": completion["completion_pair_cost_ceiling"],
            "pair_cost": round(float(completion["pair_cost"]), 6),
            "pair_surplus": round(float(completion["pair_surplus"]), 6),
            "status": "closed",
        }
    )
    return row


def simulate_candidate(
    market: sqlite3.Row,
    l1_books: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    mode: dict[str, Any],
    cap: float,
    sample_idx: int,
    schedule: list[tuple[int, float]],
    max_l2_age_ms: int,
) -> dict[str, Any] | None:
    l1 = l1_books[sample_idx]
    candidate = candidate_matches(l1, l2_by_side, l2_times_by_side, market, mode, max_l2_age_ms)
    if candidate is None:
        return None
    clip = float(mode["clip_size"])
    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "schedule": schedule_name(schedule),
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        "winner_side": market["winner_side"],
        **candidate,
        "clip_size": clip,
        "first_is_winner": candidate["first_side"] == market["winner_side"],
        "first_fill": clip <= cap,
        "first_fill_ts_ms": candidate["candidate_ts_ms"] if clip <= cap else None,
        "first_fill_iso": candidate["candidate_iso"] if clip <= cap else None,
        "completion_fill": False,
        "completion_delay_s": None,
        "completion_vwap": None,
        "completion_worst_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row

    opposite_side = str(candidate["opposite_side"])
    completion = first_completion_by_schedule(
        l2_by_side[opposite_side],
        l2_times_by_side[opposite_side],
        int(candidate["candidate_ts_ms"]),
        int(market["end_ms"]),
        clip,
        float(candidate["first_vwap"]),
        schedule,
    )
    if completion is None:
        row["status"] = "schedule_not_filled"
        return row
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
            "completion_filled_size": round(float(completion["completion_filled_size"]), 6),
            "completion_stage_deadline_s": completion["completion_stage_deadline_s"],
            "completion_pair_cost_ceiling": completion["completion_pair_cost_ceiling"],
            "pair_cost": round(float(completion["pair_cost"]), 6),
            "pair_surplus": round(float(completion["pair_surplus"]), 6),
            "status": "closed",
        }
    )
    return row


def scan_market(
    market: sqlite3.Row,
    l1_books: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    modes: list[dict[str, Any]],
    schedules: list[list[tuple[int, float]]],
    sample_interval_ms: int,
    max_l2_age_ms: int,
) -> list[dict[str, Any]]:
    l2_times_by_side = {side: [book.recv_ms for book in books] for side, books in l2_by_side.items()}
    sample_idxs = sample_indices(l1_books, int(market["start_ms"]), sample_interval_ms)
    out = []
    seen: set[tuple[str, float, str]] = set()
    for mode in modes:
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            for schedule in schedules:
                key = (mode["name"], float(cap), schedule_name(schedule))
                if key in seen:
                    continue
                for idx in sample_idxs:
                    row = simulate_candidate(
                        market,
                        l1_books,
                        l2_by_side,
                        l2_times_by_side,
                        mode,
                        float(cap),
                        idx,
                        schedule,
                        max_l2_age_ms,
                    )
                    if row is None:
                        continue
                    out.append(row)
                    seen.add(key)
                    break
    return out


def scan_market_db(
    conn: sqlite3.Connection,
    market: sqlite3.Row,
    l1_books: list[L1Book],
    modes: list[dict[str, Any]],
    schedules: list[list[tuple[int, float]]],
    sample_interval_ms: int,
    max_l2_age_ms: int,
) -> list[dict[str, Any]]:
    sample_idxs = sample_indices(l1_books, int(market["start_ms"]), sample_interval_ms)
    out = []
    seen: set[tuple[str, float, str]] = set()
    max_schedule_deadline_s = max(deadline_s for schedule in schedules for deadline_s, _ in schedule)
    for mode in modes:
        context: dict[str, Any] | None = None
        opposite_l2_books: list[L2Book] = []
        opposite_l2_times: list[int] = []
        clip = float(mode["clip_size"])
        condition_id = str(market["condition_id"])
        for idx in sample_idxs:
            l1 = l1_books[idx]
            candidate = l1_candidate_matches(l1, market, mode)
            if candidate is None:
                continue
            first_l2 = load_latest_l2_before(
                conn,
                condition_id,
                str(candidate["first_side"]),
                int(candidate["candidate_ts_ms"]),
                max_l2_age_ms,
            )
            if first_l2 is None:
                continue
            first_vwap, first_filled_size, first_worst_px = sweep_vwap(first_l2, clip)
            if first_vwap is None:
                continue
            window_end_ms = min(int(candidate["candidate_ts_ms"]) + max_schedule_deadline_s * 1000, int(market["end_ms"]))
            opposite_l2_books = load_l2_window(
                conn,
                condition_id,
                str(candidate["opposite_side"]),
                int(candidate["candidate_ts_ms"]),
                window_end_ms,
            )
            if not opposite_l2_books:
                continue
            opposite_l2_times = [book.recv_ms for book in opposite_l2_books]
            context = {
                "slug": market["slug"],
                "condition_id": condition_id,
                "round_start_iso": iso_ms(int(market["start_ms"])),
                "round_end_iso": iso_ms(int(market["end_ms"])),
                "winner_side": market["winner_side"],
                **candidate,
                "clip_size": clip,
                "first_is_winner": candidate["first_side"] == market["winner_side"],
                "first_l2_recv_ms": first_l2.recv_ms,
                "first_l2_age_ms": int(candidate["candidate_ts_ms"]) - first_l2.recv_ms,
                "first_vwap": round(first_vwap, 6),
                "first_worst_px": None if first_worst_px is None else round(first_worst_px, 6),
                "first_filled_size": round(first_filled_size, 6),
                "_first_vwap_raw": first_vwap,
            }
            break
        if context is None:
            continue
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            for schedule in schedules:
                key = (mode["name"], float(cap), schedule_name(schedule))
                if key in seen:
                    continue
                row: dict[str, Any] = {
                    "mode": mode["name"],
                    "cap": float(cap),
                    "schedule": schedule_name(schedule),
                    "residual_cap_ok": clip <= float(cap),
                    **{k: v for k, v in context.items() if not k.startswith("_")},
                    "first_fill": clip <= float(cap),
                    "first_fill_ts_ms": context["candidate_ts_ms"] if clip <= float(cap) else None,
                    "first_fill_iso": context["candidate_iso"] if clip <= float(cap) else None,
                    "completion_fill": False,
                    "completion_delay_s": None,
                    "completion_vwap": None,
                    "completion_worst_px": None,
                    "pair_cost": None,
                    "pair_surplus": None,
                    "status": "blocked_by_residual_cap" if clip > float(cap) else None,
                }
                if clip <= float(cap):
                    completion = first_completion_by_schedule(
                        opposite_l2_books,
                        opposite_l2_times,
                        int(context["candidate_ts_ms"]),
                        int(market["end_ms"]),
                        clip,
                        float(context["_first_vwap_raw"]),
                        schedule,
                    )
                    if completion is None:
                        row["status"] = "schedule_not_filled"
                    else:
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
                                "completion_filled_size": round(float(completion["completion_filled_size"]), 6),
                                "completion_stage_deadline_s": completion["completion_stage_deadline_s"],
                                "completion_pair_cost_ceiling": completion["completion_pair_cost_ceiling"],
                                "pair_cost": round(float(completion["pair_cost"]), 6),
                                "pair_surplus": round(float(completion["pair_surplus"]), 6),
                                "status": "closed",
                            }
                        )
                out.append(row)
                seen.add(key)
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first_fills = [row for row in rows if row.get("first_fill") is True]
    closed = [row for row in rows if row.get("completion_fill") is True]
    residual = [row for row in first_fills if row.get("completion_fill") is not True]
    stage_counts = {}
    for row in closed:
        key = f"{row.get('completion_stage_deadline_s')}s@{row.get('completion_pair_cost_ceiling')}"
        stage_counts[key] = stage_counts.get(key, 0) + 1
    closed_surplus = sum(float(row["pair_surplus"]) * float(row["clip_size"]) for row in closed)
    residual_settlement_pnl = 0.0
    residual_cost = 0.0
    closed_cost = 0.0
    for row in closed:
        clip = float(row["clip_size"])
        first_vwap = float(row["first_vwap"])
        completion_vwap = float(row["completion_vwap"])
        closed_cost += clip * (first_vwap + completion_vwap)
    for row in residual:
        clip = float(row["clip_size"])
        first_vwap = float(row["first_vwap"])
        residual_cost += clip * first_vwap
        if row.get("first_is_winner") is True:
            residual_settlement_pnl += (1.0 - first_vwap) * clip
        elif row.get("first_is_winner") is False:
            residual_settlement_pnl -= first_vwap * clip
    total_pnl = closed_surplus + residual_settlement_pnl
    total_cost = closed_cost + residual_cost
    return {
        "candidate_count": len(rows),
        "first_fill_count": len(first_fills),
        "closed_count": len(closed),
        "closed_rate_among_candidates": rate(len(closed), len(rows)),
        "closed_rate_among_first_fills": rate(len(closed), len(first_fills)),
        "first_winner_rate": rate(sum(1 for row in first_fills if row.get("first_is_winner") is True), len(first_fills)),
        "residual_count": len(residual),
        "residual_winner_rate": rate(sum(1 for row in residual if row.get("first_is_winner") is True), len(residual)),
        "completion_delay_s": summarize([row.get("completion_delay_s") for row in closed]),
        "pair_cost": summarize([row.get("pair_cost") for row in closed]),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.90), len(closed)),
        "pair_cost_lt_0_95_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.95), len(closed)),
        "avg_surplus_at_clip": (
            round(closed_surplus / len(closed), 6)
            if closed
            else None
        ),
        "closed_surplus_usdc": round(closed_surplus, 6),
        "residual_settlement_pnl_usdc": round(residual_settlement_pnl, 6),
        "residual_adjusted_pnl_usdc": round(total_pnl, 6),
        "residual_adjusted_roi_on_total_cost": round(total_pnl / total_cost, 6) if total_cost else None,
        "first_vwap": summarize([row.get("first_vwap") for row in rows if row.get("first_fill")]),
        "stage_counts": dict(sorted(stage_counts.items())),
        "status_counts": dict(
            sorted(
                {
                    status: sum(1 for row in rows if row.get("status") == status)
                    for status in {row.get("status") for row in rows}
                }.items()
            )
        ),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_mode_cap_schedule": {}}
    keys = sorted({(row["mode"], float(row["cap"]), row["schedule"]) for row in rows})
    for mode, cap, schedule in keys:
        xs = [row for row in rows if row["mode"] == mode and float(row["cap"]) == cap and row["schedule"] == schedule]
        out["by_mode_cap_schedule"][f"{mode}|cap={cap:g}|schedule={schedule}"] = compact(xs)
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
        "# BTC 5m Bounded Taker L2 Staged Completion",
        "",
        "## Scope",
        "",
        f"- modes_file: `{report['modes_file']}`",
        f"- days: `{report['days']}`",
        f"- schedules: `{report['parameters']['schedules']}`",
        f"- max_l2_age_ms: `{report['parameters']['max_l2_age_ms']}`",
        "- Both first leg and completion use L2 ask sweep VWAP.",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "- This is market-side fillability, not own execution truth.",
        "",
        "## Results",
        "",
        "| mode/cap/schedule | candidates | closed/fill | first winner | residual | residual winner | pair p50 | delay p50 | closed surplus | net PnL | ROI | stages |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, item in report["aggregate"]["by_mode_cap_schedule"].items():
        lines.append(
            f"| {key} | {item['candidate_count']} | {item['closed_rate_among_first_fills']} | "
            f"{item['first_winner_rate']} | {item['residual_count']} | {item['residual_winner_rate']} | "
            f"{item['pair_cost']['p50']} | {item['completion_delay_s']['p50']} | "
            f"{item['closed_surplus_usdc']} | {item['residual_adjusted_pnl_usdc']} | "
            f"{item['residual_adjusted_roi_on_total_cost']} | `{item['stage_counts']}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--modes-file", default="configs/xuan/high_side_wait_taker_shadow_candidates.json")
    parser.add_argument("--output-dir", default="data/exports/btc5m_bounded_taker_l2_schedule")
    parser.add_argument(
        "--schedules",
        default="30:0.90,50:0.95,70:1.00;30:0.90,70:0.95;50:0.90,70:0.95",
    )
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=0)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    schedules = [parse_schedule(x.strip()) for x in args.schedules.split(";") if x.strip()]
    modes = load_modes(Path(args.modes_file))
    rows: list[dict[str, Any]] = []
    db_summaries: list[dict[str, Any]] = []

    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            db_summaries.append({"day": day, "db_path": str(db_path), "markets": len(markets)})
            if args.max_markets > 0:
                markets = markets[: args.max_markets]
            for market_idx, market in enumerate(markets, start=1):
                if args.progress_every > 0 and market_idx % args.progress_every == 0:
                    print(
                        json.dumps(
                            {
                                "day": day,
                                "market_idx": market_idx,
                                "markets": len(markets),
                                "rows": len(rows),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                start_ms = int(market["start_ms"])
                end_ms = int(market["end_ms"])
                l1_books = load_l1_books(conn, str(market["condition_id"]), start_ms, end_ms)
                if not l1_books:
                    continue
                l2_books = load_l2_books(conn, str(market["condition_id"]), start_ms, end_ms)
                market_rows = scan_market(
                    market,
                    l1_books,
                    l2_books,
                    modes,
                    schedules,
                    int(args.sample_interval_s * 1000),
                    args.max_l2_age_ms,
                )
                for row in market_rows:
                    row["day"] = day
                rows.extend(market_rows)
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_bounded_taker_l2_schedule_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "modes_file": str(Path(args.modes_file).resolve()),
        "days": days,
        "parameters": {
            "schedules": args.schedules,
            "sample_interval_s": args.sample_interval_s,
            "max_l2_age_ms": args.max_l2_age_ms,
        },
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_bounded_taker_l2_schedule_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_bounded_taker_l2_schedule_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_bounded_taker_l2_schedule_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_bounded_taker_l2_schedule_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_bounded_taker_l2_schedule_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

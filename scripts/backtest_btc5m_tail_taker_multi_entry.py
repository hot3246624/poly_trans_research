#!/usr/bin/env python3
"""Dense-L2 tail taker multi-entry replay for BTC 5m markets.

This is a read-only market-side replay. It assumes a clean YES/NO pair can be
merged immediately, then allows another entry in the same market. If a first
leg cannot be completed by the schedule, the market stops reopening.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-05-02", "2026-05-03", "2026-05-04", "2026-05-06")
TRUSTED_START_MS = 1_777_274_700_000


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


@dataclass(frozen=True)
class Profile:
    name: str
    max_entries: int
    clips: tuple[float, ...]
    caps: tuple[float, ...]
    schedule: tuple[tuple[int, float], ...]
    reopen_delay_ms: int
    first_min: float
    first_max: float


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


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


def parse_schedule(value: str) -> tuple[tuple[int, float], ...]:
    out = []
    for part in value.split(","):
        if not part.strip():
            continue
        deadline_s, ceiling = part.split(":", 1)
        out.append((int(deadline_s), float(ceiling)))
    return tuple(sorted(out))


def schedule_name(schedule: tuple[tuple[int, float], ...]) -> str:
    return "_".join(f"{deadline}s_{ceiling:g}" for deadline, ceiling in schedule)


def parse_float_list(value: str) -> list[float]:
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def parse_policy_list(value: str) -> list[tuple[float, ...]]:
    out = []
    for item in value.split(";"):
        if not item.strip():
            continue
        out.append(tuple(parse_float_list(item)))
    return out


def policy_name(values: tuple[float, ...]) -> str:
    return "-".join(f"{v:g}" for v in values)


def value_for_entry(values: tuple[float, ...], entry_index: int) -> float:
    return values[min(entry_index - 1, len(values) - 1)]


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


def summarize_values(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p50": percentile(vals, 50),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


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
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms,
               s.winner_side, s.resolution_source
        FROM market_meta m
        JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol='BTC'
          AND m.interval_sec=300
          AND s.winner_side IN ('YES', 'NO')
          AND COALESCE(s.resolution_source, '') != 'inferred'
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        out.append(row)
    return out


def load_l1_tail(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int,
    offset_start_s: int,
    offset_end_s: int,
) -> list[L1Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms
        """,
        (condition_id, start_ms + offset_start_s * 1000, start_ms + offset_end_s * 1000),
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
        if px is None or sz is None or float(sz) <= 0.0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


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
        ORDER BY recv_ms
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    out = []
    for row in rows:
        levels = ask_levels(row)
        if levels:
            out.append(L2Book(recv_ms=int(row["recv_ms"]), side=str(row["market_side"]), asks=levels))
    return out


def latest_l2(
    books: list[L2Book],
    times: list[int],
    ts_ms: int,
    max_age_ms: int,
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
        if use <= 0.0:
            continue
        filled += use
        notional += use * px
        worst_px = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst_px
    return None, filled, worst_px


def sample_l1(books: list[L1Book], interval_ms: int) -> list[L1Book]:
    out = []
    next_sample = None
    for book in books:
        if next_sample is None or book.recv_ms >= next_sample:
            out.append(book)
            next_sample = book.recv_ms + interval_ms
    return out


def find_completion(
    books: list[L2Book],
    times: list[int],
    start_ms: int,
    market_end_ms: int,
    target_size: float,
    first_vwap: float,
    schedule: tuple[tuple[int, float], ...],
) -> dict[str, Any] | None:
    segment_start_ms = start_ms
    previous_deadline_s = 0
    for deadline_s, pair_cost_ceiling in schedule:
        if deadline_s <= previous_deadline_s:
            continue
        segment_end_ms = min(start_ms + deadline_s * 1000, market_end_ms)
        start_idx = bisect.bisect_left(times, segment_start_ms)
        end_idx = bisect.bisect_right(times, segment_end_ms)
        for book in books[start_idx:end_idx]:
            vwap, filled, worst_px = sweep_vwap(book, target_size)
            if vwap is None:
                continue
            pair_cost = first_vwap + vwap
            if pair_cost <= pair_cost_ceiling + 1e-9:
                return {
                    "completion_ts_ms": book.recv_ms,
                    "completion_vwap": vwap,
                    "completion_worst_px": worst_px,
                    "completion_delay_s": (book.recv_ms - start_ms) / 1000.0,
                    "completion_stage_deadline_s": deadline_s,
                    "completion_pair_cost_ceiling": pair_cost_ceiling,
                    "pair_cost": pair_cost,
                    "pair_surplus": 1.0 - pair_cost,
                }
        previous_deadline_s = deadline_s
        segment_start_ms = segment_end_ms
    return None


def eligible_first_leg(
    l1: L1Book,
    market: sqlite3.Row,
    profile: Profile,
    entry_index: int,
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    max_l2_age_ms: int,
) -> dict[str, Any] | None:
    first_side = high_side(l1)
    if first_side is None:
        return None
    opposite_side = other(first_side)
    first_l1_ask = side_px(l1, first_side, "ask")
    opposite_l1_ask = side_px(l1, opposite_side, "ask")
    opposite_l1_bid = side_px(l1, opposite_side, "bid")
    if (
        first_l1_ask is None
        or opposite_l1_ask is None
        or opposite_l1_bid is None
        or first_l1_ask <= 0.0
        or opposite_l1_ask <= 0.0
        or opposite_l1_bid <= 0.0
    ):
        return None
    if first_l1_ask < profile.first_min - 1e-9 or first_l1_ask > profile.first_max + 1e-9:
        return None
    open_pair_cap = value_for_entry(profile.caps, entry_index)
    if first_l1_ask + opposite_l1_ask > open_pair_cap + 1e-9:
        return None
    open_leg_ceiling = max(0.0, min(1.0, open_pair_cap - opposite_l1_bid))
    if open_leg_ceiling <= 0.0 or first_l1_ask > open_leg_ceiling + 1e-9:
        return None

    clip = value_for_entry(profile.clips, entry_index)
    first_l2, first_l2_age_ms = latest_l2(
        l2_by_side[first_side],
        l2_times_by_side[first_side],
        l1.recv_ms,
        max_l2_age_ms,
    )
    if first_l2 is None:
        return None
    first_vwap, first_filled_size, first_worst_px = sweep_vwap(first_l2, clip)
    if first_vwap is None:
        return None
    return {
        "entry_index": entry_index,
        "first_ts_ms": l1.recv_ms,
        "first_iso": iso_ms(l1.recv_ms),
        "offset_s": round((l1.recv_ms - int(market["start_ms"])) / 1000.0, 3),
        "first_side": first_side,
        "opposite_side": opposite_side,
        "clip_size": clip,
        "open_pair_cap": open_pair_cap,
        "first_l1_ask": round(float(first_l1_ask), 6),
        "opposite_l1_ask": round(float(opposite_l1_ask), 6),
        "opposite_l1_bid": round(float(opposite_l1_bid), 6),
        "l1_ask_pair": round(float(first_l1_ask + opposite_l1_ask), 6),
        "open_leg_ceiling": round(float(open_leg_ceiling), 6),
        "first_l2_recv_ms": first_l2.recv_ms,
        "first_l2_age_ms": first_l2_age_ms,
        "first_vwap": first_vwap,
        "first_worst_px": first_worst_px,
        "first_filled_size": first_filled_size,
        "first_is_winner": first_side == market["winner_side"],
    }


def simulate_market_profile(
    market: sqlite3.Row,
    sampled_l1: list[L1Book],
    l2_by_side: dict[str, list[L2Book]],
    l2_times_by_side: dict[str, list[int]],
    profile: Profile,
    max_l2_age_ms: int,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    ready_ms = int(market["start_ms"])
    sample_times = [book.recv_ms for book in sampled_l1]
    for entry_index in range(1, profile.max_entries + 1):
        start_idx = bisect.bisect_left(sample_times, ready_ms)
        first: dict[str, Any] | None = None
        for l1 in sampled_l1[start_idx:]:
            first = eligible_first_leg(
                l1,
                market,
                profile,
                entry_index,
                l2_by_side,
                l2_times_by_side,
                max_l2_age_ms,
            )
            if first is not None:
                break
        if first is None:
            break

        first_side = str(first["first_side"])
        opposite_side = str(first["opposite_side"])
        clip = float(first["clip_size"])
        completion = find_completion(
            l2_by_side[opposite_side],
            l2_times_by_side[opposite_side],
            int(first["first_ts_ms"]),
            int(market["end_ms"]),
            clip,
            float(first["first_vwap"]),
            profile.schedule,
        )
        base = {
            "profile": profile.name,
            "slug": market["slug"],
            "condition_id": market["condition_id"],
            "round_start_ms": int(market["start_ms"]),
            "round_start_iso": iso_ms(int(market["start_ms"])),
            "round_end_iso": iso_ms(int(market["end_ms"])),
            "winner_side": market["winner_side"],
            "resolution_source": market["resolution_source"],
            **{k: (round(v, 6) if isinstance(v, float) else v) for k, v in first.items()},
        }
        if completion is None:
            residual_actual_pnl = (1.0 - float(first["first_vwap"])) * clip if first_side == market["winner_side"] else -float(first["first_vwap"]) * clip
            out.append(
                {
                    **base,
                    "status": "residual",
                    "completion_fill": False,
                    "completion_ts_ms": None,
                    "completion_delay_s": None,
                    "completion_vwap": None,
                    "pair_cost": None,
                    "closed_pnl": 0.0,
                    "residual_cost": round(float(first["first_vwap"]) * clip, 6),
                    "residual_actual_pnl": round(residual_actual_pnl, 6),
                    "actual_pnl": round(residual_actual_pnl, 6),
                    "worst_case_pnl": round(-float(first["first_vwap"]) * clip, 6),
                    "turnover_cost": round(float(first["first_vwap"]) * clip, 6),
                }
            )
            break

        pair_cost = float(completion["pair_cost"])
        closed_pnl = (1.0 - pair_cost) * clip
        completion_cost = float(completion["completion_vwap"]) * clip
        first_cost = float(first["first_vwap"]) * clip
        out.append(
            {
                **base,
                "status": "closed",
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
                "closed_pnl": round(closed_pnl, 6),
                "residual_cost": 0.0,
                "residual_actual_pnl": 0.0,
                "actual_pnl": round(closed_pnl, 6),
                "worst_case_pnl": round(closed_pnl, 6),
                "turnover_cost": round(first_cost + completion_cost, 6),
            }
        )
        ready_ms = int(completion["completion_ts_ms"]) + profile.reopen_delay_ms
    return out


def compact(entries: list[dict[str, Any]], markets_seen: int) -> dict[str, Any]:
    closed = [r for r in entries if r["status"] == "closed"]
    residual = [r for r in entries if r["status"] == "residual"]
    market_keys = {r["slug"] for r in entries}
    residual_markets = {r["slug"] for r in residual}
    turnover = sum(float(r["turnover_cost"]) for r in entries)
    closed_qty = sum(float(r["clip_size"]) for r in closed)
    pair_cost_notional = sum(float(r["pair_cost"]) * float(r["clip_size"]) for r in closed if r["pair_cost"] is not None)
    by_entry: dict[str, dict[str, Any]] = {}
    for idx in sorted({int(r["entry_index"]) for r in entries}):
        xs = [r for r in entries if int(r["entry_index"]) == idx]
        by_entry[str(idx)] = {
            "entries": len(xs),
            "closed": sum(1 for r in xs if r["status"] == "closed"),
            "residual": sum(1 for r in xs if r["status"] == "residual"),
            "actual_pnl": round(sum(float(r["actual_pnl"]) for r in xs), 6),
            "worst_case_pnl": round(sum(float(r["worst_case_pnl"]) for r in xs), 6),
            "pair_cost": summarize_values([r.get("pair_cost") for r in xs if r["status"] == "closed"]),
        }
    return {
        "markets_evaluable": markets_seen,
        "markets_with_entry": len(market_keys),
        "entry_market_rate": rate(len(market_keys), markets_seen),
        "entries": len(entries),
        "closed_entries": len(closed),
        "closed_rate": rate(len(closed), len(entries)),
        "residual_entries": len(residual),
        "residual_markets": len(residual_markets),
        "residual_market_rate": rate(len(residual_markets), len(market_keys)),
        "first_winner_rate": rate(sum(1 for r in entries if r.get("first_is_winner") is True), len(entries)),
        "residual_winner_rate": rate(sum(1 for r in residual if r.get("first_is_winner") is True), len(residual)),
        "weighted_pair_cost": round(pair_cost_notional / closed_qty, 6) if closed_qty else None,
        "actual_pnl": round(sum(float(r["actual_pnl"]) for r in entries), 6),
        "closed_pnl": round(sum(float(r["closed_pnl"]) for r in entries), 6),
        "residual_actual_pnl": round(sum(float(r["residual_actual_pnl"]) for r in entries), 6),
        "residual_worst_cost": round(sum(float(r["residual_cost"]) for r in residual), 6),
        "worst_case_pnl": round(sum(float(r["worst_case_pnl"]) for r in entries), 6),
        "actual_roi_on_turnover": round(sum(float(r["actual_pnl"]) for r in entries) / turnover, 6) if turnover else None,
        "worst_roi_on_turnover": round(sum(float(r["worst_case_pnl"]) for r in entries) / turnover, 6) if turnover else None,
        "completion_delay_s": summarize_values([r.get("completion_delay_s") for r in closed]),
        "pair_cost": summarize_values([r.get("pair_cost") for r in closed]),
        "entries_per_market_with_entry": summarize_values(
            [sum(1 for r in entries if r["slug"] == slug) for slug in market_keys]
        ),
        "by_entry": by_entry,
    }


def build_profiles(args: argparse.Namespace) -> list[Profile]:
    schedules = [parse_schedule(s.strip()) for s in args.schedules.split(";") if s.strip()]
    max_entries_values = parse_int_list(args.max_entries)
    clip_policies = parse_policy_list(args.clip_policies)
    cap_policies = parse_policy_list(args.cap_policies)
    reopen_delays = parse_int_list(args.reopen_delays_ms)
    profiles = []
    for max_entries in max_entries_values:
        for clips in clip_policies:
            for caps in cap_policies:
                for schedule in schedules:
                    for reopen_delay in reopen_delays:
                        name = (
                            f"max{max_entries}_clip{policy_name(clips)}_cap{policy_name(caps)}_"
                            f"sched{schedule_name(schedule)}_delay{reopen_delay}ms"
                        )
                        profiles.append(
                            Profile(
                                name=name,
                                max_entries=max_entries,
                                clips=clips,
                                caps=caps,
                                schedule=schedule,
                                reopen_delay_ms=reopen_delay,
                                first_min=args.first_min,
                                first_max=args.first_max,
                            )
                        )
    return profiles


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
        "# BTC 5m Tail Taker Multi-Entry Dense L2 Backtest",
        "",
        "## Scope",
        "",
        f"- days: `{report['days']}`",
        f"- replay_root: `{report['replay_root']}`",
        f"- profiles: `{report['profile_count']}`",
        f"- first ask band: `{report['parameters']['first_min']}..{report['parameters']['first_max']}`",
        f"- open window offset: `{report['parameters']['offset_start_s']}..{report['parameters']['offset_end_s']}`",
        "- Opens and completions use L2 ask sweep VWAP.",
        "- Clean pairs are assumed mergeable immediately; residual stops same-market reopen.",
        "- Read-only replay SQLite; no raw/rebuild/write to replay.",
        "",
        "## Top Profiles By Worst Case PnL",
        "",
        "| rank | profile | entries | closed | residual | weighted_pair_cost | actual_pnl | worst_pnl | worst_roi | entry_rate | entries/mkt p50 |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, item in enumerate(report["top_by_worst_case"], start=1):
        s = item["summary"]
        lines.append(
            f"| {idx} | `{item['profile']}` | {s['entries']} | {s['closed_entries']} | "
            f"{s['residual_entries']} | {s['weighted_pair_cost']} | {s['actual_pnl']} | "
            f"{s['worst_case_pnl']} | {s['worst_roi_on_turnover']} | {s['entry_market_rate']} | "
            f"{s['entries_per_market_with_entry']['p50']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default=os.environ.get("POLYTRANS_REPLAY_ROOT", "/mnt/poly-replay"))
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", default="/tmp/btc5m_tail_taker_multi_entry")
    parser.add_argument("--offset-start-s", type=int, default=240)
    parser.add_argument("--offset-end-s", type=int, default=265)
    parser.add_argument("--first-min", type=float, default=0.62)
    parser.add_argument("--first-max", type=float, default=0.70)
    parser.add_argument("--sample-interval-ms", type=int, default=1000)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--progress-every", type=int, default=0)
    parser.add_argument("--skip-day-max", action="store_true")
    parser.add_argument("--max-entries", default="1,2,3,4,6")
    parser.add_argument("--clip-policies", default="75;75,50,35;75,60,45,30;60;50")
    parser.add_argument("--cap-policies", default="1.03;1.03,1.02;1.03,1.01;1.03,1.00;1.00")
    parser.add_argument("--reopen-delays-ms", default="0,1000")
    parser.add_argument(
        "--schedules",
        default="30:0.90,50:0.95,70:1.00;20:0.90,35:0.95,50:1.00;20:0.90,40:0.95,55:0.99",
    )
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    profiles = build_profiles(args)
    max_schedule_deadline_s = max(deadline for p in profiles for deadline, _ in p.schedule)
    rows: list[dict[str, Any]] = []
    db_summaries: list[dict[str, Any]] = []
    markets_seen_total = 0

    summaries_by_profile: dict[str, dict[str, Any]] = {}
    market_counts_by_day: dict[str, int] = {}

    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            raise FileNotFoundError(db_path)
        conn = connect_ro(db_path)
        try:
            max_ms = None if args.skip_day_max else day_max_ms(conn)
            markets = load_markets(conn, max_ms)
            if args.max_markets > 0:
                markets = markets[: args.max_markets]
            market_counts_by_day[day] = len(markets)
            db_summaries.append({"day": day, "db_path": str(db_path), "markets": len(markets)})
            markets_seen_total += len(markets)
            for market_idx, market in enumerate(markets, start=1):
                if args.progress_every > 0 and market_idx % args.progress_every == 0:
                    print(
                        json.dumps(
                            {"day": day, "market_idx": market_idx, "markets": len(markets), "rows": len(rows)},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
                start_ms = int(market["start_ms"])
                end_ms = int(market["end_ms"])
                condition_id = str(market["condition_id"])
                l1_books = load_l1_tail(conn, condition_id, start_ms, args.offset_start_s, args.offset_end_s)
                if not l1_books:
                    continue
                sampled = sample_l1(l1_books, args.sample_interval_ms)
                if not sampled:
                    continue
                l2_start_ms = max(start_ms, start_ms + args.offset_start_s * 1000 - args.max_l2_age_ms)
                l2_end_ms = min(end_ms, start_ms + args.offset_end_s * 1000 + max_schedule_deadline_s * 1000)
                l2_by_side = {
                    side: load_l2_window(conn, condition_id, side, l2_start_ms, l2_end_ms)
                    for side in ("YES", "NO")
                }
                l2_times_by_side = {side: [book.recv_ms for book in books] for side, books in l2_by_side.items()}
                if not l2_by_side["YES"] or not l2_by_side["NO"]:
                    continue
                for profile in profiles:
                    market_rows = simulate_market_profile(
                        market,
                        sampled,
                        l2_by_side,
                        l2_times_by_side,
                        profile,
                        args.max_l2_age_ms,
                    )
                    for row in market_rows:
                        row["day"] = day
                    rows.extend(market_rows)
        finally:
            conn.close()

    for profile in profiles:
        xs = [row for row in rows if row["profile"] == profile.name]
        summaries_by_profile[profile.name] = compact(xs, markets_seen_total)

    top = sorted(
        ({"profile": name, "summary": summary} for name, summary in summaries_by_profile.items()),
        key=lambda item: (
            item["summary"]["worst_case_pnl"],
            item["summary"]["actual_pnl"],
            item["summary"]["entries"],
        ),
        reverse=True,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    entries_csv = output_dir / "tail_taker_multi_entry_rows.csv"
    summary_json = output_dir / "tail_taker_multi_entry_summary.json"
    report_md = output_dir / "tail_taker_multi_entry_report.md"
    write_csv(entries_csv, rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "parameters": {
            "offset_start_s": args.offset_start_s,
            "offset_end_s": args.offset_end_s,
            "first_min": args.first_min,
            "first_max": args.first_max,
            "sample_interval_ms": args.sample_interval_ms,
            "max_l2_age_ms": args.max_l2_age_ms,
            "max_entries": args.max_entries,
            "clip_policies": args.clip_policies,
            "cap_policies": args.cap_policies,
            "reopen_delays_ms": args.reopen_delays_ms,
            "schedules": args.schedules,
        },
        "db_summaries": db_summaries,
        "markets_evaluable_by_day": market_counts_by_day,
        "profile_count": len(profiles),
        "entry_rows": len(rows),
        "summaries_by_profile": summaries_by_profile,
        "top_by_worst_case": top[:30],
        "top_by_actual": sorted(
            ({"profile": name, "summary": summary} for name, summary in summaries_by_profile.items()),
            key=lambda item: (item["summary"]["actual_pnl"], item["summary"]["worst_case_pnl"]),
            reverse=True,
        )[:30],
        "outputs": {
            "entries_csv": str(entries_csv.resolve()),
            "summary_json": str(summary_json.resolve()),
            "report_md": str(report_md.resolve()),
        },
    }
    summary_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report_md.write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "profiles": len(profiles)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

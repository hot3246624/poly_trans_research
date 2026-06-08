#!/usr/bin/env python3
"""Executable BTC 5m cycle-merge L1 backtest.

Strategy model:
- scan public L1 books for BTC 5m markets;
- when YES ask + NO ask <= cap, buy equal qty on both sides;
- immediately merge the pair, realizing qty * (1 - pair_cost);
- cooldown, then allow another cycle in the same market;
- no residual inventory is allowed.

This is deliberately simpler than xuanxuan008's observed inventory engine, but
it is causal and public-data only. It gives a deployable lower bound for the
cycle/merge direction before adding maker placement or directional inventory.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = (
    "2026-05-02",
    "2026-05-03",
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-05-07",
)


@dataclass(frozen=True)
class Profile:
    cap: float
    clip: float
    cooldown_ms: int
    min_qty: float

    @property
    def name(self) -> str:
        return f"cap{self.cap:g}_clip{self.clip:g}_cool{self.cooldown_ms}ms_min{self.min_qty:g}"


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def parse_float_list(value: str) -> list[float]:
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def parse_int_list(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def percentile(values: list[float], q: float) -> float | None:
    vals = sorted(v for v in values if math.isfinite(v))
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 6)
    pos = (len(vals) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(vals[lo], 6)
    w = pos - lo
    return round(vals[lo] * (1.0 - w) + vals[hi] * w, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p50": percentile(vals, 50),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def load_markets(conn: sqlite3.Connection, max_markets: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        WHERE symbol='BTC' AND interval_sec=300
        ORDER BY start_ms
        """
    ).fetchall()
    return rows[:max_markets] if max_markets else rows


def load_l1_candidates(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int,
    end_ms: int,
    max_cap: float,
    min_qty: float,
    sample_ms: int,
) -> list[tuple[int, float, float, float, float]]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_ask_px, yes_ask_sz, no_ask_px, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
          AND yes_ask_px IS NOT NULL
          AND no_ask_px IS NOT NULL
          AND yes_ask_sz IS NOT NULL
          AND no_ask_sz IS NOT NULL
        ORDER BY recv_ms
        """,
        (condition_id, start_ms, end_ms),
    )
    out: list[tuple[int, float, float, float, float]] = []
    next_sample_ms = start_ms
    last_key: tuple[float, float, float] | None = None
    for row in rows:
        ts = int(row["recv_ms"])
        if sample_ms > 0 and ts < next_sample_ms:
            continue
        yes_ask = float(row["yes_ask_px"])
        no_ask = float(row["no_ask_px"])
        qty = min(float(row["yes_ask_sz"]), float(row["no_ask_sz"]))
        pair = yes_ask + no_ask
        if pair > max_cap + 1e-12 or qty < min_qty - 1e-12:
            if sample_ms > 0:
                next_sample_ms = ts + sample_ms
            continue
        key = (round(pair, 6), round(qty, 4), round(yes_ask, 6))
        if key != last_key:
            out.append((ts, pair, qty, yes_ask, no_ask))
            last_key = key
        if sample_ms > 0:
            next_sample_ms = ts + sample_ms
    return out


def simulate_market(
    day: str,
    market: sqlite3.Row,
    candidates: list[tuple[int, float, float, float, float]],
    profile: Profile,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    next_ts = int(market["start_ms"])
    rows: list[dict[str, Any]] = []
    qty_total = 0.0
    cost_total = 0.0
    pnl_total = 0.0
    for ts, pair, avail_qty, yes_ask, no_ask in candidates:
        if ts < next_ts or pair > profile.cap + 1e-12:
            continue
        qty = min(profile.clip, avail_qty)
        if qty < profile.min_qty - 1e-12:
            continue
        cost = qty * pair
        pnl = qty * (1.0 - pair)
        qty_total += qty
        cost_total += cost
        pnl_total += pnl
        next_ts = ts + profile.cooldown_ms
        rows.append(
            {
                "profile": profile.name,
                "day": day,
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "ts_ms": ts,
                "iso": iso_ms(ts),
                "offset_s": round((ts - int(market["start_ms"])) / 1000.0, 3),
                "pair_cost": round(pair, 6),
                "yes_ask": round(yes_ask, 6),
                "no_ask": round(no_ask, 6),
                "avail_qty": round(avail_qty, 6),
                "qty": round(qty, 6),
                "cost": round(cost, 6),
                "pnl": round(pnl, 6),
            }
        )
    return (
        {
            "profile": profile.name,
            "day": day,
            "slug": market["slug"],
            "condition_id": market["condition_id"],
            "cycles": len(rows),
            "qty": round(qty_total, 6),
            "cost": round(cost_total, 6),
            "pnl": round(pnl_total, 6),
        },
        rows,
    )


def aggregate(profile: str, market_rows: list[dict[str, Any]], cycle_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in market_rows if r["profile"] == profile]
    cycles = [r for r in cycle_rows if r["profile"] == profile]
    active = [r for r in rows if int(r["cycles"]) > 0]
    cost = sum(float(r["cost"]) for r in rows)
    pnl = sum(float(r["pnl"]) for r in rows)
    return {
        "profile": profile,
        "markets": len(rows),
        "markets_with_cycle": len(active),
        "coverage": round(len(active) / len(rows), 6) if rows else None,
        "cycles": sum(int(r["cycles"]) for r in rows),
        "cycles_per_active_market": round(sum(int(r["cycles"]) for r in rows) / len(active), 6) if active else None,
        "qty": round(sum(float(r["qty"]) for r in rows), 6),
        "cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "roi_on_cost": round(pnl / cost, 6) if cost else None,
        "pair_cost": summarize([r["pair_cost"] for r in cycles]),
        "qty_per_cycle": summarize([r["qty"] for r in cycles]),
        "offset_s": summarize([r["offset_s"] for r in cycles]),
        "by_day": {
            day: {
                "markets": len([r for r in rows if r["day"] == day]),
                "markets_with_cycle": len([r for r in active if r["day"] == day]),
                "cycles": sum(int(r["cycles"]) for r in rows if r["day"] == day),
                "cost": round(sum(float(r["cost"]) for r in rows if r["day"] == day), 6),
                "pnl": round(sum(float(r["pnl"]) for r in rows if r["day"] == day), 6),
            }
            for day in sorted({r["day"] for r in rows})
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("/mnt/poly-replay"))
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--caps", default="0.98,0.99,1.0,1.01,1.02")
    parser.add_argument("--clips", default="25,50,75,100,150,200")
    parser.add_argument("--cooldowns-ms", default="2000,5000,10000,20000")
    parser.add_argument("--min-qtys", default="10,25,50")
    parser.add_argument("--sample-ms", type=int, default=1000)
    parser.add_argument("--max-markets", type=int)
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    profiles = [
        Profile(cap, clip, cooldown, min_qty)
        for cap in parse_float_list(args.caps)
        for clip in parse_float_list(args.clips)
        for cooldown in parse_int_list(args.cooldowns_ms)
        for min_qty in parse_float_list(args.min_qtys)
        if min_qty <= clip
    ]
    max_cap = max(p.cap for p in profiles)
    min_qty = min(p.min_qty for p in profiles)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    market_rows: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    input_days: list[dict[str, Any]] = []

    for day in days:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            input_days.append({"day": day, "exists": False})
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, args.max_markets)
            input_days.append({"day": day, "exists": True, "markets": len(markets)})
            for idx, market in enumerate(markets, 1):
                candidates = load_l1_candidates(
                    conn,
                    str(market["condition_id"]),
                    int(market["start_ms"]),
                    int(market["end_ms"]),
                    max_cap,
                    min_qty,
                    args.sample_ms,
                )
                for profile in profiles:
                    market_row, rows = simulate_market(day, market, candidates, profile)
                    market_rows.append(market_row)
                    cycle_rows.extend(rows)
                if args.progress_every and idx % args.progress_every == 0:
                    print(json.dumps({"day": day, "market_idx": idx, "markets": len(markets), "cycles": len(cycle_rows)}), flush=True)
        finally:
            conn.close()

    summaries = {profile.name: aggregate(profile.name, market_rows, cycle_rows) for profile in profiles}
    report = {
        "days": days,
        "input": input_days,
        "sample_ms": args.sample_ms,
        "profiles": [p.name for p in profiles],
        "summaries_by_profile": summaries,
        "best_by_pnl": sorted(summaries.values(), key=lambda s: (s["pnl"], s["markets_with_cycle"]), reverse=True)[:20],
        "best_by_coverage_positive": sorted(
            [s for s in summaries.values() if float(s["pnl"]) > 0.0],
            key=lambda s: (s["markets_with_cycle"], s["pnl"]),
            reverse=True,
        )[:20],
        "output_dir": str(args.output_dir.resolve()),
    }

    write_csv(args.output_dir / "cycle_merge_l1_market_rows.csv", market_rows)
    write_csv(args.output_dir / "cycle_merge_l1_cycle_rows.csv", cycle_rows)
    (args.output_dir / "cycle_merge_l1_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "profiles": len(profiles), "market_rows": len(market_rows), "cycle_rows": len(cycle_rows)}, indent=2))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Public-trade maker-cycle proxy for BTC 5m buy/merge strategies.

Model:
- Stand as a passive buyer on both YES and NO.
- A public trade with taker_side=SELL is treated as fillable maker-bid flow.
- Inventory is FIFO; whenever YES and NO inventory coexist, merge immediately.
- New unpaired seed inventory is only allowed below seed_max.
- Completion inventory can be bought above seed_max only when its FIFO pair cost
  with existing opposite inventory is <= pair_cap.

This is a proxy, not queue truth. It is designed to explore the xuan-like
inventory/merge family using causal public trades without reading raw data.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict, deque
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


@dataclass
class Lot:
    qty: float
    cost: float


@dataclass(frozen=True)
class Profile:
    seed_max: float
    pair_cap: float
    clip: float
    max_inventory_cost: float
    min_fill: float

    @property
    def name(self) -> str:
        return (
            f"seed{self.seed_max:g}_pair{self.pair_cap:g}_"
            f"clip{self.clip:g}_inv{self.max_inventory_cost:g}_min{self.min_fill:g}"
        )


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


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def lots_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def lots_cost(lots: deque[Lot]) -> float:
    return sum(lot.qty * lot.cost for lot in lots)


def inventory_cost(inv: dict[str, deque[Lot]]) -> float:
    return lots_cost(inv["YES"]) + lots_cost(inv["NO"])


def avg_cost_for_qty(lots: deque[Lot], qty: float) -> float | None:
    remaining = qty
    cost = 0.0
    got = 0.0
    for lot in lots:
        if remaining <= 1e-9:
            break
        take = min(lot.qty, remaining)
        cost += take * lot.cost
        got += take
        remaining -= take
    if got <= 1e-9:
        return None
    return cost / got


def pop_lots(lots: deque[Lot], qty: float) -> tuple[float, float]:
    remaining = qty
    got = 0.0
    cost = 0.0
    while remaining > 1e-9 and lots:
        lot = lots[0]
        take = min(lot.qty, remaining)
        got += take
        cost += take * lot.cost
        lot.qty -= take
        remaining -= take
        if lot.qty <= 1e-9:
            lots.popleft()
    return got, cost


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
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
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        JOIN settlement_records s ON s.condition_id=m.condition_id
        WHERE m.symbol='BTC'
          AND m.interval_sec=300
          AND s.winner_side IN ('YES', 'NO')
          AND COALESCE(s.resolution_source, '') != 'inferred'
        ORDER BY m.start_ms
        """
    ).fetchall()
    return rows[:max_markets] if max_markets else rows


def load_sell_flow(conn: sqlite3.Connection, condition_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND trade_ts_ms IS NOT NULL
          AND taker_side='SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id,),
    ).fetchall()
    return [
        {
            "trade_ts_ms": int(row["trade_ts_ms"]),
            "side": str(row["market_side"]),
            "price": float(row["price"]),
            "size": float(row["size"]),
        }
        for row in rows
    ]


def merge_inventory(inv: dict[str, deque[Lot]], ts_ms: int, market: sqlite3.Row, profile: Profile) -> tuple[list[dict[str, Any]], float, float]:
    rows: list[dict[str, Any]] = []
    total_qty = 0.0
    total_pnl = 0.0
    while True:
        qty = min(lots_qty(inv["YES"]), lots_qty(inv["NO"]))
        if qty <= 1e-9:
            break
        yq, yc = pop_lots(inv["YES"], qty)
        nq, nc = pop_lots(inv["NO"], qty)
        q = min(yq, nq)
        cost = yc + nc
        pnl = q - cost
        total_qty += q
        total_pnl += pnl
        rows.append(
            {
                "profile": profile.name,
                "condition_id": market["condition_id"],
                "slug": market["slug"],
                "ts_ms": ts_ms,
                "iso": iso_ms(ts_ms),
                "offset_s": round((ts_ms - int(market["start_ms"])) / 1000.0, 3),
                "merge_qty": round(q, 6),
                "merge_cost": round(cost, 6),
                "pair_cost": round(cost / q, 6) if q else None,
                "merge_pnl": round(pnl, 6),
            }
        )
    return rows, total_qty, total_pnl


def fill_qty_for_event(inv: dict[str, deque[Lot]], side: str, price: float, available: float, profile: Profile) -> float:
    opp = other(side)
    opp_qty = lots_qty(inv[opp])
    fill_cap = min(available, profile.clip)
    if fill_cap < profile.min_fill:
        return 0.0

    inv_cost = inventory_cost(inv)
    remaining_inv_budget = max(0.0, profile.max_inventory_cost - inv_cost)
    if remaining_inv_budget <= 1e-9:
        return 0.0
    fill_cap = min(fill_cap, remaining_inv_budget / max(price, 1e-9))

    if opp_qty > 1e-9:
        pair_qty = min(fill_cap, opp_qty)
        opp_avg = avg_cost_for_qty(inv[opp], pair_qty)
        paired_allowed = pair_qty if opp_avg is not None and opp_avg + price <= profile.pair_cap + 1e-12 else 0.0
        if price <= profile.seed_max + 1e-12:
            return fill_cap if fill_cap >= profile.min_fill else 0.0
        return paired_allowed if paired_allowed >= profile.min_fill else 0.0

    if price <= profile.seed_max + 1e-12:
        return fill_cap if fill_cap >= profile.min_fill else 0.0
    return 0.0


def simulate_market(day: str, market: sqlite3.Row, events: list[dict[str, Any]], profile: Profile) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    inv = {"YES": deque(), "NO": deque()}
    fill_count = 0
    buy_cost = 0.0
    merge_count = 0
    merge_qty = 0.0
    merge_pnl = 0.0
    max_inv_cost = 0.0
    merge_rows: list[dict[str, Any]] = []

    for event in events:
        price = float(event["price"])
        side = str(event["side"])
        qty = fill_qty_for_event(inv, side, price, float(event["size"]), profile)
        if qty <= 1e-9:
            continue
        inv[side].append(Lot(qty, price))
        fill_count += 1
        buy_cost += qty * price
        max_inv_cost = max(max_inv_cost, inventory_cost(inv))
        rows, q, pnl = merge_inventory(inv, int(event["trade_ts_ms"]), market, profile)
        if rows:
            merge_rows.extend(rows)
            merge_count += len(rows)
            merge_qty += q
            merge_pnl += pnl
        max_inv_cost = max(max_inv_cost, inventory_cost(inv))

    winner = str(market["winner_side"])
    yes_qty = lots_qty(inv["YES"])
    no_qty = lots_qty(inv["NO"])
    residual_cost = lots_cost(inv["YES"]) + lots_cost(inv["NO"])
    settlement = yes_qty if winner == "YES" else no_qty
    residual_pnl = settlement - residual_cost
    total_pnl = merge_pnl + residual_pnl
    return (
        {
            "profile": profile.name,
            "day": day,
            "condition_id": market["condition_id"],
            "slug": market["slug"],
            "winner_side": winner,
            "fill_count": fill_count,
            "merge_count": merge_count,
            "buy_cost": round(buy_cost, 6),
            "merge_qty": round(merge_qty, 6),
            "merge_pnl": round(merge_pnl, 6),
            "yes_residual_qty": round(yes_qty, 6),
            "no_residual_qty": round(no_qty, 6),
            "residual_cost": round(residual_cost, 6),
            "settlement": round(settlement, 6),
            "residual_pnl": round(residual_pnl, 6),
            "total_pnl": round(total_pnl, 6),
            "max_inventory_cost": round(max_inv_cost, 6),
        },
        merge_rows,
    )


def aggregate(profile: str, market_rows: list[dict[str, Any]], merge_rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [r for r in market_rows if r["profile"] == profile]
    merges = [r for r in merge_rows if r["profile"] == profile]
    active = [r for r in rows if int(r["fill_count"]) > 0]
    cost = sum(float(r["buy_cost"]) for r in rows)
    pnl = sum(float(r["total_pnl"]) for r in rows)
    residual = [r for r in active if abs(float(r["yes_residual_qty"]) - float(r["no_residual_qty"])) > 1e-9 or float(r["residual_cost"]) > 1e-9]
    return {
        "profile": profile,
        "markets": len(rows),
        "markets_with_fill": len(active),
        "coverage": round(len(active) / len(rows), 6) if rows else None,
        "fills": sum(int(r["fill_count"]) for r in rows),
        "fills_per_active_market": round(sum(int(r["fill_count"]) for r in rows) / len(active), 6) if active else None,
        "merges": len(merges),
        "merges_per_active_market": round(len(merges) / len(active), 6) if active else None,
        "buy_cost": round(cost, 6),
        "merge_pnl": round(sum(float(r["merge_pnl"]) for r in rows), 6),
        "residual_pnl": round(sum(float(r["residual_pnl"]) for r in rows), 6),
        "total_pnl": round(pnl, 6),
        "roi_on_buy_cost": round(pnl / cost, 6) if cost else None,
        "residual_markets": len(residual),
        "residual_market_rate": round(len(residual) / len(active), 6) if active else None,
        "merge_pair_cost": summarize([r["pair_cost"] for r in merges]),
        "merge_qty": summarize([r["merge_qty"] for r in merges]),
        "max_inventory_cost": summarize([r["max_inventory_cost"] for r in active]),
        "by_day": {
            day: {
                "markets": len([r for r in rows if r["day"] == day]),
                "markets_with_fill": len([r for r in active if r["day"] == day]),
                "fills": sum(int(r["fill_count"]) for r in rows if r["day"] == day),
                "merges": len([r for r in merges if r["slug"] in {x["slug"] for x in rows if x["day"] == day}]),
                "buy_cost": round(sum(float(r["buy_cost"]) for r in rows if r["day"] == day), 6),
                "total_pnl": round(sum(float(r["total_pnl"]) for r in rows if r["day"] == day), 6),
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
    parser.add_argument("--seed-maxs", default="0.35,0.4,0.45,0.5,0.55")
    parser.add_argument("--pair-caps", default="0.98,1.0,1.02")
    parser.add_argument("--clips", default="25,50,100,200")
    parser.add_argument("--max-inventory-costs", default="250,500,1000")
    parser.add_argument("--min-fill", type=float, default=10.0)
    parser.add_argument("--max-markets", type=int)
    parser.add_argument("--progress-every", type=int, default=50)
    args = parser.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    profiles = [
        Profile(seed, cap, clip, inv, args.min_fill)
        for seed in parse_float_list(args.seed_maxs)
        for cap in parse_float_list(args.pair_caps)
        for clip in parse_float_list(args.clips)
        for inv in parse_float_list(args.max_inventory_costs)
    ]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    market_rows: list[dict[str, Any]] = []
    merge_rows: list[dict[str, Any]] = []
    inputs: list[dict[str, Any]] = []

    for day in days:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            inputs.append({"day": day, "exists": False})
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, args.max_markets)
            inputs.append({"day": day, "exists": True, "markets": len(markets)})
            for idx, market in enumerate(markets, 1):
                events = load_sell_flow(conn, str(market["condition_id"]))
                for profile in profiles:
                    market_row, rows = simulate_market(day, market, events, profile)
                    market_rows.append(market_row)
                    merge_rows.extend(rows)
                if args.progress_every and idx % args.progress_every == 0:
                    print(json.dumps({"day": day, "market_idx": idx, "markets": len(markets), "market_rows": len(market_rows), "merge_rows": len(merge_rows)}), flush=True)
        finally:
            conn.close()

    summaries = {profile.name: aggregate(profile.name, market_rows, merge_rows) for profile in profiles}
    report = {
        "days": days,
        "input": inputs,
        "profiles": [p.name for p in profiles],
        "summaries_by_profile": summaries,
        "best_by_pnl": sorted(summaries.values(), key=lambda s: (s["total_pnl"], s["coverage"]), reverse=True)[:30],
        "best_by_coverage_positive": sorted(
            [s for s in summaries.values() if float(s["total_pnl"]) > 0.0],
            key=lambda s: (s["coverage"], s["total_pnl"]),
            reverse=True,
        )[:30],
        "output_dir": str(args.output_dir.resolve()),
    }
    write_csv(args.output_dir / "maker_cycle_merge_market_rows.csv", market_rows)
    write_csv(args.output_dir / "maker_cycle_merge_rows.csv", merge_rows)
    (args.output_dir / "maker_cycle_merge_summary.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "profiles": len(profiles), "market_rows": len(market_rows), "merge_rows": len(merge_rows)}, indent=2))


if __name__ == "__main__":
    main()

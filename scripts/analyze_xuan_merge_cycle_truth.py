#!/usr/bin/env python3
"""Reconstruct xuanxuan008 BTC 5m buy/merge inventory cycles from replay.

This is not a strategy backtest. It is a truth reconstruction from published
replay data:
- read-only SQLite replay;
- xuan_activity TRADE/MERGE/REDEEM only;
- FIFO inventory accounting for merge cost;
- settlement_records.winner_side for remaining inventory value.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import Counter, defaultdict, deque
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
    unit_cost: float
    ts_ms: int


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


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
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def event_key(row: sqlite3.Row) -> tuple[Any, ...]:
    # tx_hash can group multiple activity rows in one transaction; keep fields.
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


def pop_fifo(lots: deque[Lot], qty: float) -> tuple[float, float, bool]:
    remaining = qty
    consumed_qty = 0.0
    consumed_cost = 0.0
    incomplete = False
    while remaining > 1e-9:
        if not lots:
            incomplete = True
            break
        lot = lots[0]
        take = min(lot.qty, remaining)
        consumed_qty += take
        consumed_cost += take * lot.unit_cost
        lot.qty -= take
        remaining -= take
        if lot.qty <= 1e-9:
            lots.popleft()
    return consumed_qty, consumed_cost, incomplete


def lots_qty(lots: deque[Lot]) -> float:
    return sum(lot.qty for lot in lots)


def lots_cost(lots: deque[Lot]) -> float:
    return sum(lot.qty * lot.unit_cost for lot in lots)


def load_day(conn: sqlite3.Connection) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[str, int]]:
    markets: dict[str, dict[str, Any]] = {}
    for row in conn.execute(
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
    ):
        markets[str(row["condition_id"])] = {
            "condition_id": str(row["condition_id"]),
            "slug": row["slug"],
            "start_ms": int(row["start_ms"]),
            "end_ms": int(row["end_ms"]),
            "winner_side": row["winner_side"],
            "resolution_source": row["resolution_source"],
        }

    events_by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[tuple[Any, ...]] = set()
    raw_rows = 0
    for row in conn.execute(
        """
        SELECT id, activity_ts_ms, condition_id, slug, activity_type,
               outcome_side, side, price, size, usdc_size, tx_hash
        FROM xuan_activity
        WHERE condition_id IS NOT NULL
          AND activity_ts_ms IS NOT NULL
          AND activity_type IN ('TRADE', 'MERGE', 'REDEEM')
        ORDER BY activity_ts_ms, id
        """
    ):
        raw_rows += 1
        cid = str(row["condition_id"])
        if cid not in markets:
            continue
        key = event_key(row)
        if key in seen:
            continue
        seen.add(key)
        size = float(row["size"] or 0.0)
        price = float(row["price"] or 0.0)
        usdc_size = float(row["usdc_size"]) if row["usdc_size"] is not None else price * size
        events_by_market[cid].append(
            {
                "activity_ts_ms": int(row["activity_ts_ms"]),
                "event_id": int(row["id"]),
                "activity_type": row["activity_type"],
                "outcome_side": row["outcome_side"],
                "side": row["side"],
                "price": price,
                "size": size,
                "usdc_size": usdc_size,
                "tx_hash": row["tx_hash"],
            }
        )
    return markets, events_by_market, {"activity_rows": raw_rows, "events_dedup": sum(len(v) for v in events_by_market.values())}


def analyze_market(day: str, meta: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    lots = {"YES": deque(), "NO": deque()}
    trade_count = 0
    merge_count = 0
    redeem_count = 0
    buy_cost = 0.0
    merge_recv = 0.0
    merge_cost = 0.0
    merge_pnl = 0.0
    cycle_rows: list[dict[str, Any]] = []
    segment_trade_count = 0
    segment_buy_cost = 0.0
    segment_first_ts: int | None = None
    negative_inventory_events = 0
    max_merge_shortfall = 0.0

    first_trade_ts: int | None = None
    last_trade_ts: int | None = None
    max_inventory_cost = 0.0
    max_gross_qty = 0.0
    price_values: list[float] = []
    size_values: list[float] = []

    for event in sorted(events, key=lambda e: (e["activity_ts_ms"], e.get("event_id", 0))):
        typ = event["activity_type"]
        ts_ms = int(event["activity_ts_ms"])
        size = float(event["size"] or 0.0)
        if typ == "TRADE":
            if event.get("side") != "BUY" or event.get("outcome_side") not in {"YES", "NO"}:
                continue
            side = str(event["outcome_side"])
            unit_cost = float(event["usdc_size"] or 0.0) / size if size > 0 else float(event["price"] or 0.0)
            lots[side].append(Lot(size, unit_cost, ts_ms))
            cost = unit_cost * size
            buy_cost += cost
            trade_count += 1
            segment_trade_count += 1
            segment_buy_cost += cost
            if segment_first_ts is None:
                segment_first_ts = ts_ms
            first_trade_ts = ts_ms if first_trade_ts is None else min(first_trade_ts, ts_ms)
            last_trade_ts = ts_ms if last_trade_ts is None else max(last_trade_ts, ts_ms)
            price_values.append(unit_cost)
            size_values.append(size)
        elif typ == "MERGE":
            merge_count += 1
            q = size
            yes_qty, yes_cost, yes_short = pop_fifo(lots["YES"], q)
            no_qty, no_cost, no_short = pop_fifo(lots["NO"], q)
            consumed = min(yes_qty, no_qty)
            shortfall = max(0.0, q - consumed)
            if yes_short or no_short or shortfall > 1e-9:
                negative_inventory_events += 1
                max_merge_shortfall = max(max_merge_shortfall, shortfall)
            cost = yes_cost + no_cost
            pnl = q - cost
            merge_recv += q
            merge_cost += cost
            merge_pnl += pnl
            cycle_rows.append(
                {
                    "day": day,
                    "condition_id": meta["condition_id"],
                    "slug": meta["slug"],
                    "merge_ts_ms": ts_ms,
                    "merge_iso": iso_ms(ts_ms),
                    "merge_offset_s": round((ts_ms - meta["start_ms"]) / 1000.0, 3),
                    "merge_size": round(q, 6),
                    "merge_cost": round(cost, 6),
                    "merge_pair_cost": round(cost / q, 6) if q > 0 else None,
                    "merge_pnl": round(pnl, 6),
                    "segment_trade_count": segment_trade_count,
                    "segment_buy_cost": round(segment_buy_cost, 6),
                    "segment_duration_s": round((ts_ms - segment_first_ts) / 1000.0, 3) if segment_first_ts else None,
                    "shortfall": round(shortfall, 6),
                }
            )
            segment_trade_count = 0
            segment_buy_cost = 0.0
            segment_first_ts = None
        elif typ == "REDEEM":
            redeem_count += 1

        max_inventory_cost = max(max_inventory_cost, lots_cost(lots["YES"]) + lots_cost(lots["NO"]))
        max_gross_qty = max(max_gross_qty, lots_qty(lots["YES"]) + lots_qty(lots["NO"]))

    winner = meta["winner_side"]
    yes_qty = lots_qty(lots["YES"])
    no_qty = lots_qty(lots["NO"])
    yes_cost = lots_cost(lots["YES"])
    no_cost = lots_cost(lots["NO"])
    settlement_recv = yes_qty if winner == "YES" else no_qty
    residual_cost = yes_cost + no_cost
    residual_pnl = settlement_recv - residual_cost
    total_pnl = merge_pnl + residual_pnl
    residual_qty = yes_qty + no_qty
    residual_side = "YES" if yes_qty > no_qty + 1e-9 else "NO" if no_qty > yes_qty + 1e-9 else "BALANCED"

    market_row = {
        "day": day,
        "condition_id": meta["condition_id"],
        "slug": meta["slug"],
        "winner_side": winner,
        "start_iso": iso_ms(meta["start_ms"]),
        "trade_count": trade_count,
        "merge_count": merge_count,
        "redeem_count": redeem_count,
        "buy_cost": round(buy_cost, 6),
        "merge_recv": round(merge_recv, 6),
        "merge_cost": round(merge_cost, 6),
        "merge_pnl": round(merge_pnl, 6),
        "settlement_recv": round(settlement_recv, 6),
        "residual_cost": round(residual_cost, 6),
        "residual_pnl": round(residual_pnl, 6),
        "total_pnl": round(total_pnl, 6),
        "yes_residual_qty": round(yes_qty, 6),
        "no_residual_qty": round(no_qty, 6),
        "residual_qty": round(residual_qty, 6),
        "residual_side": residual_side,
        "residual_wins": residual_side == winner if residual_side in {"YES", "NO"} else None,
        "max_inventory_cost": round(max_inventory_cost, 6),
        "max_gross_qty": round(max_gross_qty, 6),
        "first_trade_offset_s": round((first_trade_ts - meta["start_ms"]) / 1000.0, 3) if first_trade_ts else None,
        "last_trade_offset_s": round((last_trade_ts - meta["start_ms"]) / 1000.0, 3) if last_trade_ts else None,
        "avg_trade_price": round(sum(price_values) / len(price_values), 6) if price_values else None,
        "avg_trade_size": round(sum(size_values) / len(size_values), 6) if size_values else None,
        "negative_inventory_events": negative_inventory_events,
        "max_merge_shortfall": round(max_merge_shortfall, 6),
    }
    return market_row, cycle_rows


def aggregate(rows: list[dict[str, Any]], cycles: list[dict[str, Any]]) -> dict[str, Any]:
    active = [r for r in rows if int(r["trade_count"]) > 0]
    residual = [r for r in active if float(r["residual_qty"]) > 1e-9]
    return {
        "markets": len(rows),
        "markets_with_trade": len(active),
        "coverage": rate(len(active), len(rows)),
        "trade_count": sum(int(r["trade_count"]) for r in active),
        "merge_count": sum(int(r["merge_count"]) for r in active),
        "cycles": len(cycles),
        "cycles_per_active_market": round(len(cycles) / len(active), 6) if active else None,
        "trades_per_active_market": summarize([r["trade_count"] for r in active]),
        "merges_per_active_market": summarize([r["merge_count"] for r in active]),
        "buy_cost": round(sum(float(r["buy_cost"]) for r in active), 6),
        "merge_recv": round(sum(float(r["merge_recv"]) for r in active), 6),
        "merge_pnl": round(sum(float(r["merge_pnl"]) for r in active), 6),
        "settlement_recv": round(sum(float(r["settlement_recv"]) for r in active), 6),
        "residual_cost": round(sum(float(r["residual_cost"]) for r in active), 6),
        "residual_pnl": round(sum(float(r["residual_pnl"]) for r in active), 6),
        "total_pnl": round(sum(float(r["total_pnl"]) for r in active), 6),
        "roi_on_buy_cost": round(sum(float(r["total_pnl"]) for r in active) / sum(float(r["buy_cost"]) for r in active), 6)
        if sum(float(r["buy_cost"]) for r in active)
        else None,
        "residual_markets": len(residual),
        "residual_market_rate": rate(len(residual), len(active)),
        "residual_win_rate": rate(sum(1 for r in residual if r["residual_wins"] is True), len(residual)),
        "merge_pair_cost": summarize([c["merge_pair_cost"] for c in cycles]),
        "merge_size": summarize([c["merge_size"] for c in cycles]),
        "merge_offset_s": summarize([c["merge_offset_s"] for c in cycles]),
        "segment_trade_count": summarize([c["segment_trade_count"] for c in cycles]),
        "segment_duration_s": summarize([c["segment_duration_s"] for c in cycles]),
        "max_inventory_cost": summarize([r["max_inventory_cost"] for r in active]),
        "first_trade_offset_s": summarize([r["first_trade_offset_s"] for r in active]),
        "last_trade_offset_s": summarize([r["last_trade_offset_s"] for r in active]),
        "negative_inventory_events": sum(int(r["negative_inventory_events"]) for r in active),
        "max_merge_shortfall": summarize([r["max_merge_shortfall"] for r in active]),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def render_report(summary: dict[str, Any]) -> str:
    overall = summary["overall"]
    lines = [
        "# Xuan Merge Cycle Truth",
        "",
        f"days: {', '.join(summary['days'])}",
        f"markets_with_trade: {overall['markets_with_trade']} / {overall['markets']} ({overall['coverage']})",
        f"trade_count: {overall['trade_count']}",
        f"merge_count: {overall['merge_count']}",
        f"cycles_per_active_market: {overall['cycles_per_active_market']}",
        f"total_pnl: {overall['total_pnl']}",
        f"roi_on_buy_cost: {overall['roi_on_buy_cost']}",
        f"merge_pnl: {overall['merge_pnl']}",
        f"residual_pnl: {overall['residual_pnl']}",
        f"residual_market_rate: {overall['residual_market_rate']}",
        f"merge_pair_cost: {overall['merge_pair_cost']}",
        "",
        "## By Day",
        "",
        "| day | active | trades | merges | pnl | roi | residual_rate | merge_pair_p50 | merge_pair_p90 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for day, item in summary["by_day"].items():
        lines.append(
            "| {day} | {active}/{markets} | {trades} | {merges} | {pnl} | {roi} | {rr} | {p50} | {p90} |".format(
                day=day,
                active=item["markets_with_trade"],
                markets=item["markets"],
                trades=item["trade_count"],
                merges=item["merge_count"],
                pnl=item["total_pnl"],
                roi=item["roi_on_buy_cost"],
                rr=item["residual_market_rate"],
                p50=item["merge_pair_cost"]["p50"],
                p90=item["merge_pair_cost"]["p90"],
            )
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("/mnt/poly-replay"))
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    market_rows: list[dict[str, Any]] = []
    cycle_rows: list[dict[str, Any]] = []
    day_inputs: list[dict[str, Any]] = []

    for day in days:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_inputs.append({"day": day, "exists": False})
            continue
        conn = connect_ro(db_path)
        try:
            markets, events_by_market, input_summary = load_day(conn)
        finally:
            conn.close()
        day_inputs.append({"day": day, "exists": True, **input_summary, "markets": len(markets)})
        for cid, meta in markets.items():
            row, cycles = analyze_market(day, meta, events_by_market.get(cid, []))
            market_rows.append(row)
            cycle_rows.extend(cycles)

    by_day: dict[str, Any] = {}
    for day in days:
        day_market_rows = [r for r in market_rows if r["day"] == day]
        day_cycle_rows = [r for r in cycle_rows if r["day"] == day]
        by_day[day] = aggregate(day_market_rows, day_cycle_rows)

    summary = {
        "days": days,
        "input": day_inputs,
        "overall": aggregate(market_rows, cycle_rows),
        "by_day": by_day,
        "top_markets_by_pnl": sorted(market_rows, key=lambda r: float(r["total_pnl"]), reverse=True)[:20],
        "worst_markets_by_pnl": sorted(market_rows, key=lambda r: float(r["total_pnl"]))[:20],
        "output_dir": str(args.output_dir.resolve()),
    }

    write_csv(args.output_dir / "xuan_merge_cycle_market_rows.csv", market_rows)
    write_csv(args.output_dir / "xuan_merge_cycle_rows.csv", cycle_rows)
    (args.output_dir / "xuan_merge_cycle_truth_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    (args.output_dir / "xuan_merge_cycle_truth_report.md").write_text(render_report(summary), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "markets": len(market_rows), "cycles": len(cycle_rows)}, indent=2))


if __name__ == "__main__":
    main()

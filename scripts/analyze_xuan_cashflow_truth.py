#!/usr/bin/env python3
"""Rebuild xuanxuan008 public cash-flow truth from replay SQLite.

This script is intentionally not a strategy backtest. It answers a prerequisite
research question: where does xuan's observable PnL come from?

Data rules:
- read replay SQLite only, opened read-only;
- use xuan_activity for TRADE/MERGE/REDEEM cash flows;
- use settlement_records.winner_side for residual settlement value;
- dedupe events across daily replay DBs by tx hash when possible;
- never read raw data and never modify DBs.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


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
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


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


def event_key(row: sqlite3.Row) -> tuple[Any, ...]:
    if row["tx_hash"]:
        return ("tx", row["tx_hash"])
    return (
        "fields",
        row["condition_id"],
        row["activity_type"],
        row["activity_ts_ms"],
        row["outcome_side"],
        row["side"],
        round(float(row["price"] or 0.0), 10),
        round(float(row["size"] or 0.0), 8),
    )


def load_replay(
    replay_root: Path,
    days: list[str],
    start_ms: int,
    end_ms: int | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    market_meta: dict[str, dict[str, Any]] = {}
    seen_events: set[tuple[Any, ...]] = set()
    day_summaries: list[dict[str, Any]] = []

    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_summaries.append({"day": day, "exists": False, "activity_rows": 0, "new_events": 0})
            continue
        conn = connect_ro(db_path)
        try:
            for row in conn.execute(
                """
                SELECT m.condition_id, m.slug, m.symbol, m.interval_sec, m.start_ms, m.end_ms,
                       s.winner_side, s.settle_ms, s.resolution_source
                FROM market_meta m
                LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
                WHERE m.symbol='BTC' AND m.interval_sec=300
                """
            ):
                market_meta[str(row["condition_id"])] = {
                    "condition_id": row["condition_id"],
                    "slug": row["slug"],
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "winner_side": row["winner_side"],
                    "settle_ms": row["settle_ms"],
                    "resolution_source": row["resolution_source"],
                }
            rows = conn.execute(
                """
                SELECT id, activity_ts_ms, condition_id, slug, activity_type,
                       outcome_side, side, price, size, usdc_size, tx_hash
                FROM xuan_activity
                WHERE condition_id IS NOT NULL
                  AND activity_ts_ms IS NOT NULL
                  AND activity_ts_ms >= ?
                  AND activity_type IN ('TRADE', 'MERGE', 'REDEEM')
                ORDER BY activity_ts_ms, id
                """,
                (start_ms,),
            ).fetchall()
        finally:
            conn.close()

        new_count = 0
        for row in rows:
            ts_ms = int(row["activity_ts_ms"])
            if end_ms is not None and ts_ms >= end_ms:
                continue
            if PLANNED_OUTAGE_START_MS <= ts_ms < PLANNED_OUTAGE_END_MS:
                continue
            if str(row["condition_id"]) not in market_meta:
                continue
            key = event_key(row)
            if key in seen_events:
                continue
            seen_events.add(key)
            new_count += 1
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            usdc_size = float(row["usdc_size"]) if row["usdc_size"] is not None else price * size
            events.append(
                {
                    "activity_ts_ms": ts_ms,
                    "activity_iso": iso_ms(ts_ms),
                    "condition_id": str(row["condition_id"]),
                    "slug": row["slug"],
                    "activity_type": row["activity_type"],
                    "outcome_side": row["outcome_side"],
                    "side": row["side"],
                    "price": price,
                    "size": size,
                    "usdc_size": usdc_size,
                    "tx_hash": row["tx_hash"],
                    "event_day": dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).date().isoformat(),
                }
            )
        day_summaries.append({"day": day, "exists": True, "activity_rows": len(rows), "new_events": new_count})

    events.sort(key=lambda row: (row["activity_ts_ms"], row["condition_id"], row.get("tx_hash") or ""))
    return events, market_meta, day_summaries


def apply_market_ledger(condition_id: str, events: list[dict[str, Any]], meta: dict[str, Any]) -> dict[str, Any]:
    qty = {"YES": 0.0, "NO": 0.0}
    buy_cost = 0.0
    sell_recv = 0.0
    merge_recv = 0.0
    redeem_recv = 0.0
    trade_count = 0
    merge_count = 0
    redeem_count = 0
    prewindow_inventory_dependency = False
    negative_qty_events = 0
    max_negative_qty = 0.0

    for event in sorted(events, key=lambda row: (row["activity_ts_ms"], row.get("tx_hash") or "")):
        typ = event["activity_type"]
        size = float(event["size"] or 0.0)
        side = event.get("outcome_side")
        order_side = event.get("side")
        if typ == "TRADE":
            trade_count += 1
            if side not in {"YES", "NO"}:
                continue
            if order_side == "SELL":
                qty[side] -= size
                sell_recv += float(event["usdc_size"] or 0.0)
            else:
                qty[side] += size
                buy_cost += float(event["usdc_size"] or event["price"] * size)
        elif typ == "MERGE":
            merge_count += 1
            merge_recv += size
            qty["YES"] -= size
            qty["NO"] -= size
        elif typ == "REDEEM":
            redeem_count += 1
            redeem_recv += size
            winner = meta.get("winner_side")
            if winner in {"YES", "NO"}:
                qty[winner] -= size

        for s in ("YES", "NO"):
            if qty[s] < -1e-6:
                prewindow_inventory_dependency = True
                negative_qty_events += 1
                max_negative_qty = max(max_negative_qty, abs(qty[s]))

    # Clamp tiny negatives for value accounting, but keep diagnostics.
    yes_qty = qty["YES"]
    no_qty = qty["NO"]
    winner = meta.get("winner_side")
    residual_settle_value = 0.0
    if winner == "YES":
        residual_settle_value = max(yes_qty, 0.0)
    elif winner == "NO":
        residual_settle_value = max(no_qty, 0.0)
    cash_pnl = -buy_cost + sell_recv + merge_recv + redeem_recv
    total_pnl_with_residual = cash_pnl + residual_settle_value
    total_buy_qty = sum(float(event["size"] or 0.0) for event in events if event["activity_type"] == "TRADE" and event.get("side") != "SELL")
    first_ts = min((int(event["activity_ts_ms"]) for event in events), default=None)
    last_ts = max((int(event["activity_ts_ms"]) for event in events), default=None)
    start_ms = int(meta["start_ms"])
    return {
        "condition_id": condition_id,
        "slug": meta.get("slug"),
        "round_start_ms": start_ms,
        "round_start_iso": iso_ms(start_ms),
        "winner_side": winner,
        "first_event_ms": first_ts,
        "first_event_iso": iso_ms(first_ts),
        "last_event_ms": last_ts,
        "last_event_iso": iso_ms(last_ts),
        "first_event_offset_s": round((first_ts - start_ms) / 1000.0, 3) if first_ts is not None else None,
        "event_count": len(events),
        "trade_count": trade_count,
        "merge_count": merge_count,
        "redeem_count": redeem_count,
        "buy_qty": round(total_buy_qty, 6),
        "buy_cost": round(buy_cost, 6),
        "sell_recv": round(sell_recv, 6),
        "merge_recv": round(merge_recv, 6),
        "redeem_recv": round(redeem_recv, 6),
        "cash_pnl_before_residual": round(cash_pnl, 6),
        "end_yes_qty": round(yes_qty, 6),
        "end_no_qty": round(no_qty, 6),
        "end_winner_qty": round(max(qty.get(winner, 0.0), 0.0), 6) if winner in {"YES", "NO"} else None,
        "end_loser_qty": round(max(qty.get("NO" if winner == "YES" else "YES", 0.0), 0.0), 6)
        if winner in {"YES", "NO"}
        else None,
        "residual_settle_value": round(residual_settle_value, 6),
        "total_pnl_with_residual": round(total_pnl_with_residual, 6),
        "roi_on_buy_cost": round(total_pnl_with_residual / buy_cost, 6) if buy_cost > 0 else None,
        "prewindow_inventory_dependency": prewindow_inventory_dependency,
        "negative_qty_events": negative_qty_events,
        "max_negative_qty": round(max_negative_qty, 6),
    }


def build_ledgers(events: list[dict[str, Any]], market_meta: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_condition[event["condition_id"]].append(event)
    out = []
    for condition_id, xs in by_condition.items():
        meta = market_meta.get(condition_id)
        if not meta:
            continue
        out.append(apply_market_ledger(condition_id, xs, meta))
    return sorted(out, key=lambda row: (row["round_start_ms"], row["condition_id"]))


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    positive = [row for row in rows if float(row["total_pnl_with_residual"]) > 0]
    negative = [row for row in rows if float(row["total_pnl_with_residual"]) < 0]
    prewindow = [row for row in rows if row["prewindow_inventory_dependency"]]
    total_buy_cost = sum(float(row["buy_cost"] or 0.0) for row in rows)
    total_pnl = sum(float(row["total_pnl_with_residual"] or 0.0) for row in rows)
    return {
        "market_count": len(rows),
        "positive_market_count": len(positive),
        "negative_market_count": len(negative),
        "positive_market_rate": rate(len(positive), len(rows)),
        "prewindow_inventory_dependency_count": len(prewindow),
        "prewindow_inventory_dependency_rate": rate(len(prewindow), len(rows)),
        "event_count": sum(int(row["event_count"]) for row in rows),
        "trade_count": sum(int(row["trade_count"]) for row in rows),
        "merge_count": sum(int(row["merge_count"]) for row in rows),
        "redeem_count": sum(int(row["redeem_count"]) for row in rows),
        "buy_qty": round(sum(float(row["buy_qty"] or 0.0) for row in rows), 6),
        "buy_cost": round(total_buy_cost, 6),
        "merge_recv": round(sum(float(row["merge_recv"] or 0.0) for row in rows), 6),
        "redeem_recv": round(sum(float(row["redeem_recv"] or 0.0) for row in rows), 6),
        "residual_settle_value": round(sum(float(row["residual_settle_value"] or 0.0) for row in rows), 6),
        "cash_pnl_before_residual": round(sum(float(row["cash_pnl_before_residual"] or 0.0) for row in rows), 6),
        "total_pnl_with_residual": round(total_pnl, 6),
        "roi_on_buy_cost": round(total_pnl / total_buy_cost, 6) if total_buy_cost > 0 else None,
        "per_market_pnl": summarize([float(row["total_pnl_with_residual"]) for row in rows]),
        "per_market_roi": summarize([row["roi_on_buy_cost"] for row in rows]),
    }


def bucket_tables(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, list[dict[str, Any]]]] = {
        "day": defaultdict(list),
        "has_merge": defaultdict(list),
        "has_redeem": defaultdict(list),
        "prewindow_inventory_dependency": defaultdict(list),
        "first_event_offset": defaultdict(list),
    }
    for row in rows:
        day = dt.datetime.fromtimestamp(int(row["round_start_ms"]) / 1000, tz=dt.timezone.utc).date().isoformat()
        buckets["day"][day].append(row)
        buckets["has_merge"][str(int(row["merge_count"]) > 0)].append(row)
        buckets["has_redeem"][str(int(row["redeem_count"]) > 0)].append(row)
        buckets["prewindow_inventory_dependency"][str(bool(row["prewindow_inventory_dependency"]))].append(row)
        off = row["first_event_offset_s"]
        if off is None:
            key = "missing"
        elif off < 0:
            key = "pre_start"
        elif off < 30:
            key = "000-030s"
        elif off < 120:
            key = "030-120s"
        elif off < 240:
            key = "120-240s"
        else:
            key = "240s+"
        buckets["first_event_offset"][key].append(row)
    return {name: {key: compact(xs) for key, xs in sorted(groups.items())} for name, groups in buckets.items()}


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
    s = report["summary"]
    lines = [
        "# Xuan Public Cash-Flow Truth",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        f"- trusted_start: `{report['trusted_start_iso']}`",
        "- SQLite read-only; source is `xuan_activity` plus `settlement_records`.",
        "- This is public truth reconstruction, not private queue/order truth.",
        "",
        "## Topline",
        "",
        f"- markets: `{s['market_count']}`",
        f"- trades / merges / redeems: `{s['trade_count']}` / `{s['merge_count']}` / `{s['redeem_count']}`",
        f"- buy_cost: `${s['buy_cost']}`",
        f"- merge_recv: `${s['merge_recv']}`",
        f"- redeem_recv: `${s['redeem_recv']}`",
        f"- residual_settle_value: `${s['residual_settle_value']}`",
        f"- cash_pnl_before_residual: `${s['cash_pnl_before_residual']}`",
        f"- total_pnl_with_residual: `${s['total_pnl_with_residual']}`",
        f"- roi_on_buy_cost: `{s['roi_on_buy_cost']}`",
        f"- prewindow_inventory_dependency_rate: `{s['prewindow_inventory_dependency_rate']}`",
        "",
        "## By Day",
        "",
        "| day | markets | buy_cost | merge | redeem | residual | net pnl | roi | prewindow dep |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["bucket_tables"]["day"].items():
        lines.append(
            f"| {key} | {item['market_count']} | {item['buy_cost']} | {item['merge_recv']} | "
            f"{item['redeem_recv']} | {item['residual_settle_value']} | {item['total_pnl_with_residual']} | "
            f"{item['roi_on_buy_cost']} | {item['prewindow_inventory_dependency_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Diagnostics",
            "",
            "| bucket | markets | buy_cost | net pnl | roi | prewindow dep |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for table_name in ["has_merge", "has_redeem", "prewindow_inventory_dependency", "first_event_offset"]:
        lines.append(f"| **{table_name}** |  |  |  |  |  |")
        for key, item in report["bucket_tables"][table_name].items():
            lines.append(
                f"| {key} | {item['market_count']} | {item['buy_cost']} | "
                f"{item['total_pnl_with_residual']} | {item['roi_on_buy_cost']} | "
                f"{item['prewindow_inventory_dependency_rate']} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation Rules",
            "",
            "- `cash_pnl_before_residual` is realized cash from buys/merges/redeems only.",
            "- `total_pnl_with_residual` adds settlement value of remaining winner tokens; loser residuals are valued at zero.",
            "- `prewindow_inventory_dependency=true` means a MERGE/REDEEM consumed inventory not fully observed in this replay window; use those rows cautiously.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--output-dir", default="data/exports/xuan_cashflow_truth")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    start_ms = parse_iso_ms(args.start_iso) or TRUSTED_START_MS
    end_ms = parse_iso_ms(args.end_iso)
    events, market_meta, day_summaries = load_replay(Path(args.replay_root), days, start_ms, end_ms)
    ledgers = build_ledgers(events, market_meta)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_cashflow_events.csv", events)
    write_csv(output_dir / "xuan_cashflow_markets.csv", ledgers)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "trusted_start_ms": start_ms,
        "trusted_start_iso": iso_ms(start_ms),
        "end_ms": end_ms,
        "end_iso": iso_ms(end_ms),
        "day_summaries": day_summaries,
        "summary": compact(ledgers),
        "bucket_tables": bucket_tables(ledgers),
        "outputs": {
            "events_csv": str((output_dir / "xuan_cashflow_events.csv").resolve()),
            "markets_csv": str((output_dir / "xuan_cashflow_markets.csv").resolve()),
            "summary_json": str((output_dir / "xuan_cashflow_truth_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_cashflow_truth_report.md").resolve()),
        },
    }
    (output_dir / "xuan_cashflow_truth_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_cashflow_truth_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "events": len(events), "markets": len(ledgers)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

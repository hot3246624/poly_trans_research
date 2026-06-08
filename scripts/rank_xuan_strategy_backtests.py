#!/usr/bin/env python3
"""Rank xuan strategy backtest outputs by profit-first metrics."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def get(data: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def scan(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in root.rglob("xuan_proxy_completion_first_v1_summary.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        all_item = get(data, "aggregate.all", {})
        by_day = get(data, "aggregate.by_day", {}) or {}
        day_pnls = [as_float(item.get("residual_adjusted_pnl_usdc")) for item in by_day.values()]
        day_pnls = [x for x in day_pnls if x is not None]
        params = data.get("parameters", {})
        rows.append(
            {
                "run": str(path.parent),
                "days": ",".join(data.get("days", [])),
                "base_clips": params.get("base_clips"),
                "min_offset_s": params.get("min_offset_s"),
                "max_offset_s": params.get("max_offset_s"),
                "first_fill_timeout_s": params.get("first_fill_timeout_s"),
                "residual_hold_policy": params.get("residual_hold_policy"),
                "first_fills": all_item.get("first_fill_count"),
                "closed": all_item.get("closed_count"),
                "exits": all_item.get("exit_count"),
                "residuals": all_item.get("residual_count"),
                "first_winner_rate": all_item.get("first_winner_rate"),
                "weighted_pair_cost_closed": all_item.get("strategy_weighted_pair_cost_closed"),
                "surplus_usdc": all_item.get("surplus_usdc"),
                "exit_pnl_usdc": all_item.get("exit_pnl_usdc"),
                "residual_settlement_pnl_usdc": all_item.get("residual_settlement_pnl_usdc"),
                "net_pnl": all_item.get("residual_adjusted_pnl_usdc"),
                "roi": all_item.get("residual_adjusted_roi_on_total_spend"),
                "min_day_pnl": min(day_pnls) if day_pnls else None,
                "positive_day_count": sum(1 for x in day_pnls if x > 0),
                "day_count": len(day_pnls),
            }
        )
    rows.sort(
        key=lambda row: (
            as_float(row.get("net_pnl")) if as_float(row.get("net_pnl")) is not None else -10**18,
            as_float(row.get("min_day_pnl")) if as_float(row.get("min_day_pnl")) is not None else -10**18,
        ),
        reverse=True,
    )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default="data/exports")
    parser.add_argument("--output-csv", default="data/exports/xuan_strategy_backtest_ranking.csv")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args()

    rows = scan(Path(args.root))
    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "run",
        "days",
        "base_clips",
        "first_fill_timeout_s",
        "residual_hold_policy",
        "first_fills",
        "closed",
        "exits",
        "residuals",
        "first_winner_rate",
        "weighted_pair_cost_closed",
        "surplus_usdc",
        "exit_pnl_usdc",
        "residual_settlement_pnl_usdc",
        "net_pnl",
        "roi",
        "min_day_pnl",
        "positive_day_count",
        "day_count",
    ]
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
    print(json.dumps({"output_csv": str(out), "runs": len(rows), "top": rows[: args.top]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

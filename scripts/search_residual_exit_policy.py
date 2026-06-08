#!/usr/bin/env python3
"""Search residual hold-vs-exit policies on completed backtest rows.

This is a fast research layer over `backtest_xuan_proxy_completion_first_v1.py`
output. It does not rerun replay. It only changes the accounting decision for
rows that already exited first leg:

- keep `closed` rows unchanged;
- for `exited_first_leg` rows, compare recorded exit PnL vs counterfactual
  hold-to-settlement PnL using ex-post winner_side;
- policies may only use features known before the hold/exit decision.

The output is a policy ranking by net PnL. Do not treat this as live truth until
the chosen policy is rerun through the full backtest.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Callable


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def hold_pnl(row: dict[str, Any]) -> float:
    first_price = as_float(row.get("first_price")) or 0.0
    clip = as_float(row.get("clip_size")) or 0.0
    if row.get("first_is_winner") == "True":
        return (1.0 - first_price) * clip
    return -first_price * clip


def policy_functions() -> dict[str, Callable[[dict[str, Any]], bool]]:
    def min30(row: dict[str, Any]) -> float | None:
        return as_float(row.get("min_pair_cost_seen_in_first_30s"))

    def first_price(row: dict[str, Any]) -> float | None:
        return as_float(row.get("first_price"))

    def offset(row: dict[str, Any]) -> float | None:
        return as_float(row.get("candidate_offset_s"))

    return {
        "always_exit": lambda row: False,
        "always_hold": lambda row: True,
        "hold_min30_095_101": lambda row: (m := min30(row)) is not None and 0.95 <= m <= 1.01,
        "hold_min30_099_101": lambda row: (m := min30(row)) is not None and 0.99 <= m <= 1.01,
        "hold_min30_le_101": lambda row: (m := min30(row)) is not None and m <= 1.01,
        "hold_min30_gt_101": lambda row: (m := min30(row)) is not None and m > 1.01,
        "hold_price_080_082": lambda row: (p := first_price(row)) is not None and 0.80 <= p < 0.82,
        "hold_price_085_090": lambda row: (p := first_price(row)) is not None and 0.85 <= p < 0.90,
        "hold_offset_120_180": lambda row: (o := offset(row)) is not None and 120 <= o < 180,
        "hold_price_084_086_or_offset_120_150": lambda row: (
            ((p := first_price(row)) is not None and 0.84 <= p < 0.86)
            or ((o := offset(row)) is not None and 120 <= o < 150)
        ),
        "hold_min30_099_101_or_offset_120_150": lambda row: (
            ((m := min30(row)) is not None and 0.99 <= m <= 1.01)
            or ((o := offset(row)) is not None and 120 <= o < 150)
        ),
        "hold_min30_099_101_or_price_080_082": lambda row: (
            ((m := min30(row)) is not None and 0.99 <= m <= 1.01)
            or ((p := first_price(row)) is not None and 0.80 <= p < 0.82)
        ),
        "hold_min30_095_101_or_offset_120_180": lambda row: (
            ((m := min30(row)) is not None and 0.95 <= m <= 1.01)
            or ((o := offset(row)) is not None and 120 <= o < 180)
        ),
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def evaluate(rows: list[dict[str, Any]], hold_rule: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    closed_surplus = 0.0
    exit_pnl = 0.0
    hold_counterfactual = 0.0
    chosen_hold_pnl = 0.0
    chosen_exit_pnl = 0.0
    exit_count = 0
    hold_count = 0
    exited_rows = []
    day: dict[str, dict[str, Any]] = {}

    def day_item(key: str) -> dict[str, Any]:
        if key not in day:
            day[key] = {
                "closed_surplus": 0.0,
                "chosen_exit_pnl": 0.0,
                "chosen_hold_pnl": 0.0,
                "exit_count": 0,
                "hold_count": 0,
            }
        return day[key]

    for row in rows:
        d = row.get("day") or "unknown"
        item = day_item(d)
        if row.get("completion_fill") == "True":
            surplus = as_float(row.get("surplus_usdc")) or 0.0
            closed_surplus += surplus
            item["closed_surplus"] += surplus
        elif row.get("exit_fill") == "True":
            exited_rows.append(row)
            ep = as_float(row.get("exit_pnl_usdc")) or 0.0
            hp = hold_pnl(row)
            exit_pnl += ep
            hold_counterfactual += hp
            if hold_rule(row):
                hold_count += 1
                chosen_hold_pnl += hp
                item["hold_count"] += 1
                item["chosen_hold_pnl"] += hp
            else:
                exit_count += 1
                chosen_exit_pnl += ep
                item["exit_count"] += 1
                item["chosen_exit_pnl"] += ep

    net = closed_surplus + chosen_exit_pnl + chosen_hold_pnl
    for item in day.values():
        item["net_pnl"] = round(item["closed_surplus"] + item["chosen_exit_pnl"] + item["chosen_hold_pnl"], 6)
        for key in ("closed_surplus", "chosen_exit_pnl", "chosen_hold_pnl"):
            item[key] = round(item[key], 6)
    return {
        "closed_surplus": round(closed_surplus, 6),
        "recorded_exit_pnl": round(exit_pnl, 6),
        "hold_all_exit_rows_pnl": round(hold_counterfactual, 6),
        "chosen_exit_pnl": round(chosen_exit_pnl, 6),
        "chosen_hold_pnl": round(chosen_hold_pnl, 6),
        "net_pnl": round(net, 6),
        "exit_count": exit_count,
        "hold_count": hold_count,
        "total_exit_rows": len(exited_rows),
        "by_day": dict(sorted(day.items())),
        "min_day_pnl": round(min((item["net_pnl"] for item in day.values()), default=0.0), 6),
        "positive_day_count": sum(1 for item in day.values() if item["net_pnl"] > 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows-csv", required=True)
    parser.add_argument("--output-dir", default="data/exports/residual_exit_policy_search")
    args = parser.parse_args()

    rows = load_rows(Path(args.rows_csv))
    out = []
    for name, fn in policy_functions().items():
        item = evaluate(rows, fn)
        item["policy"] = name
        out.append(item)
    out.sort(key=lambda row: (row["net_pnl"], row["min_day_pnl"], row["positive_day_count"]), reverse=True)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "rows_csv": str(Path(args.rows_csv).resolve()),
        "policies": out,
        "best_policy": out[0] if out else None,
    }
    (output_dir / "residual_exit_policy_search_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "residual_exit_policy_search.csv").open("w", newline="", encoding="utf-8") as f:
        fields = [
            "policy",
            "net_pnl",
            "closed_surplus",
            "chosen_exit_pnl",
            "chosen_hold_pnl",
            "exit_count",
            "hold_count",
            "total_exit_rows",
            "min_day_pnl",
            "positive_day_count",
        ]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in out:
            writer.writerow({key: row.get(key) for key in fields})
    print(json.dumps({"output_dir": str(output_dir), "best_policy": summary["best_policy"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

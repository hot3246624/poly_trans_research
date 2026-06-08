#!/usr/bin/env python3
"""Search conditional L2 residual-exit delays for fast-cancel selected rows.

This script keeps completed/repair rows at their recorded PnL and only replaces
`residual_settle` rows with L2 bid VWAP exits at candidate delays. It is a small
research layer for deciding whether 180s residual exposure is justified.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_dual_window_fastcancel_combo import first_side_l2_bid_vwap_at, ro_connect  # noqa: E402


def as_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return float(value)


def as_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    return int(float(value))


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def policy_functions() -> dict[str, Callable[[dict[str, Any]], int]]:
    return {
        "fixed_90": lambda row: 90,
        "fixed_120": lambda row: 120,
        "fixed_150": lambda row: 150,
        "fixed_180": lambda row: 180,
        "price_lt_050_180_else_120": lambda row: 180 if (as_float(row, "first_price") or 9.0) < 0.50 else 120,
        "price_lte_050_180_else_120": lambda row: 180 if (as_float(row, "first_price") or 9.0) <= 0.50 else 120,
        "price_lt_047_180_else_120": lambda row: 180 if (as_float(row, "first_price") or 9.0) < 0.47 else 120,
        "offset_lt_030_180_else_120": lambda row: 180 if (as_float(row, "offset_s") or 999.0) < 30 else 120,
        "early_180_late_120": lambda row: 180 if row.get("kind") == "early" else 120,
        "min_pair_cost_lte_101_180_else_120": lambda row: 180 if (as_float(row, "min_pair_cost_seen_30s") or 9.0) <= 1.01 else 120,
        "top_bid_sz_lte_100_180_else_120": lambda row: 180 if (as_float(row, "top_bid_sz") or 9_999.0) <= 100 else 120,
    }


def evaluate_policy(rows: list[dict[str, Any]], replay_root: Path, policy: Callable[[dict[str, Any]], int]) -> dict[str, Any]:
    conns = {}
    daily = defaultdict(float)
    residual_details = []
    path_pnl = defaultdict(float)
    try:
        for row in rows:
            day = str(row["day"])
            path = str(row.get("path"))
            if row.get("first_fill") != "True":
                daily[day] += 0.0
                continue
            if path != "residual_settle":
                pnl = float(row.get("pnl") or 0.0)
                daily[day] += pnl
                path_pnl[path] += pnl
                continue
            if day not in conns:
                conns[day] = ro_connect(replay_root / day / "crypto_5m.sqlite")
            delay_s = policy(row)
            fill_ts = as_int(row, "fill_ts_ms")
            if fill_ts is None:
                pnl = float(row.get("pnl") or 0.0)
                exit_info = None
            else:
                exit_info = first_side_l2_bid_vwap_at(conns[day], row, fill_ts + delay_s * 1000)
                if exit_info is None or exit_info.get("vwap") is None:
                    pnl = float(row.get("pnl") or 0.0)
                else:
                    pnl = (float(exit_info["vwap"]) - float(row["first_price"])) * float(row["clip"])
            daily[day] += pnl
            path_pnl["residual_exit"] += pnl
            residual_details.append(
                {
                    "day": day,
                    "slug": row.get("slug"),
                    "kind": row.get("kind"),
                    "offset_s": as_float(row, "offset_s"),
                    "first_side": row.get("first_side"),
                    "winner_side": row.get("winner_side"),
                    "first_is_winner": row.get("first_is_winner"),
                    "first_price": as_float(row, "first_price"),
                    "min_pair_cost_seen_30s": as_float(row, "min_pair_cost_seen_30s"),
                    "top_bid_sz": as_float(row, "top_bid_sz"),
                    "delay_s": delay_s,
                    "pnl": round(pnl, 6),
                    "vwap": None if exit_info is None else exit_info.get("vwap"),
                    "unfilled_qty": None if exit_info is None else exit_info.get("unfilled_qty"),
                }
            )
    finally:
        for conn in conns.values():
            conn.close()
    return {
        "pnl": round(sum(daily.values()), 6),
        "positive_days": f"{sum(1 for value in daily.values() if value > 0)}/{len(daily)}",
        "min_daily_pnl": round(min(daily.values()), 6) if daily else None,
        "daily_pnl": {day: round(value, 6) for day, value in sorted(daily.items())},
        "path_pnl": {path: round(value, 6) for path, value in sorted(path_pnl.items())},
        "residual_count": len(residual_details),
        "residual_details": residual_details,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selected-rows", required=True)
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--output-dir", default="data/exports/fastcancel_residual_exit_policy")
    args = parser.parse_args()

    rows = load_rows(Path(args.selected_rows))
    out = []
    for name, fn in policy_functions().items():
        item = evaluate_policy(rows, Path(args.replay_root), fn)
        item["policy"] = name
        out.append(item)
    out.sort(key=lambda item: (item["positive_days"], item["min_daily_pnl"], item["pnl"]), reverse=True)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = {"selected_rows": str(Path(args.selected_rows).resolve()), "policies": out, "best_policy": out[0] if out else None}
    (output_dir / "fastcancel_residual_exit_policy_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(
        output_dir / "fastcancel_residual_exit_policy.csv",
        [
            {
                "policy": item["policy"],
                "pnl": item["pnl"],
                "positive_days": item["positive_days"],
                "min_daily_pnl": item["min_daily_pnl"],
                "residual_count": item["residual_count"],
                "daily_pnl": json.dumps(item["daily_pnl"], sort_keys=True),
            }
            for item in out
        ],
    )
    print(json.dumps({"output_dir": str(output_dir), "best_policy": summary["best_policy"]["policy"] if out else None, "best_pnl": out[0]["pnl"] if out else None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

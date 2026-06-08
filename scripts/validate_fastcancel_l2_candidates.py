#!/usr/bin/env python3
"""Batch L2-validate fast-cancel candidates from raw/offline search output.

Raw search is only a candidate generator. This script replays the same selected
candidate rows through L2 completion VWAP and L2 bid forced exits, then ranks by
post-L2 robustness. It reads replay SQLite read-only and never touches raw data.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_dual_window_fastcancel_combo import l2_completion_reprice_summary  # noqa: E402
from search_fastcancel_from_rows import load_rows, select_state_machine, summarize_selected  # noqa: E402


PARAM_KEYS = [
    "offset_start",
    "offset_end",
    "min_delta",
    "min_bid",
    "max_bid",
    "max_spread",
    "max_opp_spread",
    "max_top_bid_sz",
    "max_immediate_pair_cost",
]


def positive_days_count(value: str | None) -> tuple[int, int]:
    if not value or "/" not in value:
        return 0, 0
    left, right = value.split("/", 1)
    return int(left), int(right)


def candidate_params(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in PARAM_KEYS}


def signature(params: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(params.get(key) for key in PARAM_KEYS)


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


def flatten_result(item: dict[str, Any]) -> dict[str, Any]:
    base = {
        **item["params"],
        "raw_attempts": item["raw"]["attempts"],
        "raw_fills": item["raw"]["fills"],
        "raw_pnl": item["raw"]["pnl"],
        "raw_positive_days": item["raw"]["positive_days"],
        "raw_min_daily_pnl": item["raw"]["min_daily_pnl"],
        "raw_paths": json.dumps(item["raw"]["paths"], sort_keys=True),
        "l2_pnl": item["l2_base"]["pnl"],
        "l2_positive_days": item["l2_base"]["positive_days"],
        "l2_min_day": item["l2_min_day"],
        "l2_min_daily_pnl": item["l2_min_daily_pnl"],
        "l2_score": item["l2_score"],
    }
    for slip, summary in item["l2_slippage"].items():
        safe = str(slip).replace(".", "_")
        base[f"l2_slip_{safe}_pnl"] = summary["pnl"]
        base[f"l2_slip_{safe}_positive_days"] = summary["positive_days"]
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True, help="Candidate row CSV produced by maker-fill backtest.")
    parser.add_argument("--search-summary", required=True, help="fastcancel_param_search_summary.json")
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--output-dir", default="data/exports/fastcancel_l2_validation")
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--exit-delay-s", type=int, default=120)
    parser.add_argument("--slippage", default="0,0.005,0.01,0.02")
    parser.add_argument(
        "--residual-exit-policy",
        default="fixed",
        choices=["fixed", "price_lt_050_180_else_default", "min_pair_lte_101_180_else_default"],
    )
    parser.add_argument("--no-fill-block-s", type=int, default=None)
    parser.add_argument("--cooldown-s", type=int, default=None)
    parser.add_argument("--non-clean-exit-delay-s", type=int, default=None)
    args = parser.parse_args()

    row_path = Path(args.rows)
    rows = load_rows([row_path])
    search = json.loads(Path(args.search_summary).read_text(encoding="utf-8"))
    search_params = search.get("parameters", {})
    no_fill_block_s = args.no_fill_block_s if args.no_fill_block_s is not None else int(search_params.get("no_fill_block_s", 15))
    cooldown_s = args.cooldown_s if args.cooldown_s is not None else int(search_params.get("cooldown_s", 10))
    if args.non_clean_exit_delay_s is not None:
        non_clean_exit_delay_s = args.non_clean_exit_delay_s
    else:
        raw_value = int(search_params.get("non_clean_exit_delay_s", -1))
        non_clean_exit_delay_s = None if raw_value < 0 else raw_value
    slippage = [float(item.strip()) for item in args.slippage.split(",") if item.strip()]

    seen: set[tuple[Any, ...]] = set()
    candidates: list[dict[str, Any]] = []
    for row in search.get("top", []):
        params = candidate_params(row)
        sig = signature(params)
        if sig in seen:
            continue
        seen.add(sig)
        candidates.append(params)
        if len(candidates) >= args.top_n:
            break

    results: list[dict[str, Any]] = []
    selected_by_rank: dict[str, list[dict[str, Any]]] = {}
    for idx, params in enumerate(candidates, 1):
        selected = select_state_machine(rows, params, no_fill_block_s, cooldown_s, non_clean_exit_delay_s)
        raw_summary = summarize_selected(selected)
        l2 = l2_completion_reprice_summary(
            Path(args.replay_root),
            selected,
            args.exit_delay_s,
            slippage,
            args.residual_exit_policy,
        )
        l2_base = l2["base"]
        daily = {day: float(value) for day, value in l2_base["daily_pnl"].items()}
        min_day, min_daily = min(daily.items(), key=lambda kv: kv[1]) if daily else (None, 0.0)
        pos, days = positive_days_count(l2_base["positive_days"])
        residual_count = int(raw_summary["paths"].get("residual_settle", 0))
        repair_count = int(raw_summary["paths"].get("repair", 0))
        l2_score = round(float(l2_base["pnl"]) + 5.0 * float(min_daily) - 10.0 * residual_count - 3.0 * repair_count, 6)
        result = {
            "rank_input": idx,
            "params": params,
            "raw": raw_summary,
            "l2_base": l2_base,
            "l2_slippage": l2.get("slippage", {}),
            "l2_second_leg_vwap_delta": l2.get("second_leg_vwap_delta", {}),
            "l2_min_day": min_day,
            "l2_min_daily_pnl": round(min_daily, 6),
            "l2_positive_count": pos,
            "l2_day_count": days,
            "l2_score": l2_score,
        }
        results.append(result)
        selected_by_rank[str(idx)] = selected

    results.sort(
        key=lambda item: (
            item["l2_positive_count"] == item["l2_day_count"],
            item["l2_score"],
            item["l2_base"]["pnl"],
            item["raw"]["fills"],
        ),
        reverse=True,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "rows": str(row_path),
        "search_summary": args.search_summary,
        "row_count": len(rows),
        "candidate_count": len(candidates),
        "parameters": vars(args),
        "results": results,
    }
    (output_dir / "fastcancel_l2_validation_summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(output_dir / "fastcancel_l2_validation_top.csv", [flatten_result(item) for item in results])
    if results:
        best_rank = str(results[0]["rank_input"])
        write_csv(output_dir / "fastcancel_l2_validation_best_selected_rows.csv", selected_by_rank[best_rank])
    print(
        json.dumps(
            {
                "output_dir": str(output_dir),
                "row_count": len(rows),
                "candidate_count": len(candidates),
                "best_l2_pnl": results[0]["l2_base"]["pnl"] if results else None,
                "best_l2_positive_days": results[0]["l2_base"]["positive_days"] if results else None,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

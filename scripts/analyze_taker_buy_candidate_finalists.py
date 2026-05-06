#!/usr/bin/env python3
"""Rank taker-BUY finalists and run simple slippage stress tests.

This script consumes the candidate cache plus a search-results CSV. It is meant
to answer one question: which candidate is closest to strategy freeze, after
day stability and execution buffer are considered?
"""

from __future__ import annotations

import argparse
import ast
import csv
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_search_module(path: Path):
    spec = importlib.util.spec_from_file_location("search_taker_buy_signal_candidate_cache", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_dict(value: str) -> dict[str, Any]:
    if not value:
        return {}
    parsed = ast.literal_eval(value)
    return parsed if isinstance(parsed, dict) else {}


def load_search_results(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row: dict[str, Any] = dict(raw)
            for key in [
                "price_lo",
                "price_hi",
                "size_lo",
                "size_hi",
                "first_lo",
                "first_hi",
                "offset_lo",
                "offset_hi",
                "max_l1_pair",
                "pair_ceiling",
                "rows",
                "closed",
                "closed_rate",
                "first_winner_rate",
                "residual",
                "residual_winner_rate",
                "pnl",
                "roi_on_first_cost",
                "pair_cost_p50",
                "delay_p50_s",
                "min_day_pnl",
            ]:
                row[key] = parse_float(row.get(key))
            row["block_after_residual"] = parse_bool(row.get("block_after_residual"))
            row["cooldown_s"] = int(float(row.get("cooldown_s") or 10))
            row["negative_days"] = ast.literal_eval(row.get("negative_days") or "[]")
            row["by_day_pnl"] = parse_dict(row.get("by_day_pnl") or "")
            row["by_day_rows"] = parse_dict(row.get("by_day_rows") or "")
            rows.append(row)
    return rows


def params_from_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "price_lo": float(row["price_lo"]),
        "price_hi": float(row["price_hi"]),
        "size_lo": float(row["size_lo"]),
        "size_hi": float(row["size_hi"]),
        "first_lo": float(row["first_lo"]),
        "first_hi": float(row["first_hi"]),
        "offset_lo": float(row["offset_lo"]),
        "offset_hi": float(row["offset_hi"]),
        "max_l1_pair": float(row["max_l1_pair"]),
        "pair_ceiling": float(row["pair_ceiling"]),
        "side_alignment": str(row.get("side_alignment") or "high"),
        "block_after_residual": bool(row["block_after_residual"]),
        "cooldown_s": int(row["cooldown_s"]),
    }


def ceiling_key(ceiling: float) -> str:
    return f"ceil_{str(ceiling).replace('.', '_')}"


def stress_row_pnl(row: dict[str, Any], ceiling: float, first_slip: float, completion_slip: float) -> tuple[float, bool, float | None, float | None]:
    key = ceiling_key(ceiling)
    pair_cost = row.get(f"{key}_pair_cost")
    if row.get(f"{key}_hit") and pair_cost is not None:
        adjusted_pair_cost = float(pair_cost) + first_slip + completion_slip
        return (1.0 - adjusted_pair_cost) * row["clip"], True, adjusted_pair_cost, row.get(f"{key}_delay_s")
    first = (row["first_l2_vwap"] or 0.0) + first_slip
    return ((1.0 - first) if row["first_is_winner"] else -first) * row["clip"], False, None, None


def simulate_stress(search_mod: Any, rows: list[dict[str, Any]], params: dict[str, Any], first_slip: float, completion_slip: float) -> dict[str, Any]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if search_mod.matches(row, params):
            by_market[str(row["condition_id"])].append(row)
    selected = []
    for market_rows in by_market.values():
        active_until = 0
        for row in sorted(market_rows, key=lambda r: int(r["trigger_ts_ms"])):
            if row["trigger_ts_ms"] < active_until:
                continue
            pnl, closed, pair_cost, delay_s = stress_row_pnl(row, params["pair_ceiling"], first_slip, completion_slip)
            selected_row = {
                **row,
                "_pnl": pnl,
                "_closed": closed,
                "_pair_cost": pair_cost,
                "_delay_s": delay_s,
            }
            selected.append(selected_row)
            if closed:
                active_until = row["trigger_ts_ms"] + int((delay_s or 0) * 1000) + params["cooldown_s"] * 1000
            elif params["block_after_residual"]:
                active_until = 10**18
            else:
                active_until = row["trigger_ts_ms"] + params["cooldown_s"] * 1000
    pnl = sum(row["_pnl"] for row in selected)
    cost = sum(((row["first_l2_vwap"] or 0.0) + first_slip) * row["clip"] for row in selected)
    by_day: dict[str, float] = defaultdict(float)
    for row in selected:
        by_day[str(row["day"])] += row["_pnl"]
    negative_days = [day for day, value in sorted(by_day.items()) if value < 0]
    return {
        "rows": len(selected),
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / cost, 6) if cost else None,
        "negative_days": negative_days,
        "min_day_pnl": round(min(by_day.values()), 6) if by_day else None,
        "by_day_pnl": {day: round(by_day[day], 6) for day in sorted(by_day)},
    }


def robustness_score(row: dict[str, Any]) -> float:
    min_day = float(row.get("min_day_pnl") or 0.0)
    pnl = float(row.get("pnl") or 0.0)
    roi = float(row.get("roi_on_first_cost") or 0.0)
    rows = float(row.get("rows") or 0.0)
    closed = float(row.get("closed_rate") or 0.0)
    return min_day * 3.0 + pnl * 0.15 + roi * 500.0 + min(rows, 300.0) * 0.05 + closed * 20.0


def rule_label(row: dict[str, Any]) -> str:
    return (
        f"price {row['price_lo']}-{row['price_hi']} size {row['size_lo']}-{row['size_hi']} "
        f"first {row['first_lo']}-{row['first_hi']} l1<={row['max_l1_pair']} "
        f"ceil {row['pair_ceiling']} block={row['block_after_residual']}"
    )


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Taker BUY Candidate Finalists",
        "",
        "## Distance To Freeze",
        "",
        f"- Current stage: `{report['distance_to_freeze']['stage']}`.",
        f"- Estimated distance: `{report['distance_to_freeze']['estimated_distance']}`.",
        f"- Remaining blockers: {', '.join(report['distance_to_freeze']['remaining_blockers'])}.",
        "",
        "## Finalist Ranking",
        "",
        "| rank | rule | rows | pnl | ROI | min day | closed | first winner | residual winner | score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(report["finalists"], start=1):
        lines.append(
            f"| {idx} | `{row['rule']}` | {row['rows']} | {row['pnl']} | {row['roi_on_first_cost']} | "
            f"{row['min_day_pnl']} | {row['closed_rate']} | {row['first_winner_rate']} | "
            f"{row['residual_winner_rate']} | {row['robustness_score']} |"
        )
    lines.extend(["", "## Slippage Stress", "", "| rule | slip bps/leg | pnl | ROI | min day | negative days |", "|---|---:|---:|---:|---:|---|"])
    for item in report["slippage_stress"]:
        lines.append(
            f"| `{item['rule']}` | {item['slip_bps_per_leg']} | {item['pnl']} | {item['roi_on_first_cost']} | "
            f"{item['min_day_pnl']} | {', '.join(item['negative_days']) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A rule is not freeze-ready if 50 bps per leg turns total PnL negative or creates multiple negative days.",
            "- The top in-sample PnL rule is only a finalist if it keeps enough edge after slippage stress.",
            "- Final freeze still requires live/shadow fillability because replay L2 assumes immediate executable size.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-csv", type=Path, required=True)
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--search-script", type=Path, default=Path("scripts/search_taker_buy_signal_candidate_cache.py"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_candidate_finalists"))
    parser.add_argument("--top-n", type=int, default=12)
    args = parser.parse_args()

    search_mod = load_search_module(args.search_script)
    cache_rows = search_mod.load_rows(args.cache_csv)
    search_rows = load_search_results(args.search_results_csv)
    eligible = [
        row
        for row in search_rows
        if float(row.get("rows") or 0) >= 100
        and not row.get("negative_days")
        and float(row.get("roi_on_first_cost") or 0) >= 0.045
        and float(row.get("min_day_pnl") or 0) >= 10
        and float(row.get("first_winner_rate") or 0) >= 0.72
    ]
    eligible.sort(key=robustness_score, reverse=True)
    finalists = []
    for row in eligible[: args.top_n]:
        finalists.append(
            {
                "rule": rule_label(row),
                "robustness_score": round(robustness_score(row), 6),
                **{
                    key: row.get(key)
                    for key in [
                        "rows",
                        "pnl",
                        "roi_on_first_cost",
                        "min_day_pnl",
                        "closed_rate",
                        "first_winner_rate",
                        "residual_winner_rate",
                        "by_day_pnl",
                        "by_day_rows",
                    ]
                },
                "params": params_from_row(row),
            }
        )

    stress_rows = []
    for finalist in finalists[:5]:
        for slip in (0.0, 0.0025, 0.005, 0.01):
            stress = simulate_stress(search_mod, cache_rows, finalist["params"], first_slip=slip, completion_slip=slip)
            stress_rows.append(
                {
                    "rule": finalist["rule"],
                    "slip_bps_per_leg": int(round(slip * 10_000)),
                    **stress,
                }
            )

    distance = {
        "stage": "research_candidate_v1",
        "estimated_distance": "about 65-70% to strategy freeze; not enforce-ready",
        "remaining_blockers": [
            "more out-of-sample replay days",
            "native dynamic sizing replay",
            "public-WS shadow fillability and latency",
            "slippage/partial-fill calibration",
        ],
    }
    report = {
        "inputs": {
            "cache_csv": str(args.cache_csv),
            "search_results_csv": str(args.search_results_csv),
        },
        "distance_to_freeze": distance,
        "eligible_count": len(eligible),
        "finalists": finalists,
        "slippage_stress": stress_rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "taker_buy_candidate_finalists.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_candidate_finalists.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "eligible_count": len(eligible), "finalists": len(finalists)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

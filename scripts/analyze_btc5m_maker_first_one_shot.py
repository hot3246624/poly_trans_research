#!/usr/bin/env python3
"""Analyze one-shot gates from maker-first candidate CSV.

The input is produced by `backtest_btc5m_maker_first_proxy.py`. This analyzer
selects at most one first-leg candidate per market for each gate, then reports
how side-selection rules change fill and completion rates.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


Row = dict[str, Any]


def as_float(row: Row, key: str) -> float | None:
    val = row.get(key)
    if val in (None, "", "None", "null"):
        return None
    return float(val)


def as_bool(row: Row, key: str) -> bool:
    return str(row.get(key)).lower() == "true"


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
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "p95": percentile(vals, 95),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def spread_ticks(row: Row, prefix: str, tick_size: float) -> float | None:
    bid = as_float(row, f"{prefix}_bid_px")
    ask = as_float(row, f"{prefix}_ask_px")
    if bid is None or ask is None:
        return None
    return round((ask - bid) / tick_size, 6)


def quote_extreme(row: Row) -> float:
    quote = as_float(row, "first_maker_quote_px")
    return abs((quote if quote is not None else 0.5) - 0.5)


def build_gates(tick_size: float) -> dict[str, Callable[[Row], bool]]:
    def offset(row: Row, min_s: float) -> bool:
        val = as_float(row, "candidate_offset_s")
        return val is not None and val >= min_s

    def tight(row: Row) -> bool:
        first = spread_ticks(row, "first", tick_size)
        opposite = spread_ticks(row, "opposite", tick_size)
        return first is not None and opposite is not None and first <= 1.1 and opposite <= 1.1

    def pairask(row: Row) -> bool:
        val = as_float(row, "l1_pair_ask_sum")
        return val is not None and val <= 1.01

    def extreme(row: Row) -> bool:
        quote = as_float(row, "first_maker_quote_px")
        return quote is not None and (quote < 0.20 or quote >= 0.80)

    return {
        "first_available": lambda row: True,
        "offset_gte_60": lambda row: offset(row, 60),
        "offset_gte_120": lambda row: offset(row, 120),
        "offset_gte_120_tight": lambda row: offset(row, 120) and tight(row),
        "offset_gte_120_tight_pairask_lte_1_01": lambda row: offset(row, 120) and tight(row) and pairask(row),
        "offset_gte_120_tight_quote_extreme": lambda row: offset(row, 120) and tight(row) and extreme(row),
        "offset_gte_120_tight_pairask_lte_1_01_quote_extreme": lambda row: offset(row, 120)
        and tight(row)
        and pairask(row)
        and extreme(row),
    }


def choose_side(candidates: list[Row], rule: str) -> Row:
    if rule == "yes_first":
        return sorted(candidates, key=lambda r: (r.get("first_side") != "YES", r.get("first_side") or ""))[0]
    if rule == "higher_bid_size":
        return max(candidates, key=lambda r: (as_float(r, "first_bid_sz") or -1, quote_extreme(r)))
    if rule == "more_extreme_quote":
        return max(candidates, key=lambda r: (quote_extreme(r), as_float(r, "first_bid_sz") or -1))
    if rule == "oracle_same_ts":
        return max(
            candidates,
            key=lambda r: (
                as_bool(r, "maker_completion_fill_30s"),
                as_bool(r, "first_maker_fill"),
                quote_extreme(r),
            ),
        )
    raise ValueError(f"unknown side rule: {rule}")


def select_one_shot(rows: list[Row], gate: Callable[[Row], bool], side_rule: str) -> list[Row]:
    by_condition: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        by_condition[str(row["condition_id"])].append(row)

    selected: list[Row] = []
    for condition_rows in by_condition.values():
        passing = [row for row in condition_rows if gate(row)]
        if not passing:
            continue
        first_ts = min(int(float(row["candidate_ts_ms"])) for row in passing)
        same_ts = [row for row in passing if int(float(row["candidate_ts_ms"])) == first_ts]
        selected.append(choose_side(same_ts, side_rule))
    return selected


def compact(rows: list[Row]) -> dict[str, Any]:
    first_filled = [r for r in rows if as_bool(r, "first_maker_fill")]
    completed = [r for r in rows if as_bool(r, "maker_completion_fill_30s")]
    taker99 = [r for r in rows if as_bool(r, "taker_immediate_pair_cost_lte_0_99")]
    taker100 = [r for r in rows if as_bool(r, "taker_immediate_pair_cost_lte_1_00")]
    by_day: dict[str, dict[str, Any]] = {}
    for day in sorted({str(r["date"]) for r in rows}):
        day_rows = [r for r in rows if r["date"] == day]
        by_day[day] = {
            "market_count": len(day_rows),
            "first_fill_rate": rate(sum(as_bool(r, "first_maker_fill") for r in day_rows), len(day_rows)),
            "completion_30s_rate": rate(sum(as_bool(r, "maker_completion_fill_30s") for r in day_rows), len(day_rows)),
        }
    return {
        "market_count": len(rows),
        "first_fill_count": len(first_filled),
        "first_fill_rate": rate(len(first_filled), len(rows)),
        "completion_30s_count": len(completed),
        "completion_30s_rate": rate(len(completed), len(rows)),
        "completion_30s_rate_after_first_fill": rate(len(completed), len(first_filled)),
        "taker_immediate_lte_0_99_rate": rate(len(taker99), len(rows)),
        "taker_immediate_lte_1_00_rate": rate(len(taker100), len(rows)),
        "first_fill_delay_s": summarize([as_float(r, "first_maker_fill_delay_s") for r in first_filled]),
        "completion_delay_s": summarize([as_float(r, "maker_completion_delay_s") for r in completed]),
        "maker_completion_pair_cost": summarize([as_float(r, "maker_completion_pair_cost") for r in completed]),
        "by_day": by_day,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Maker-First One-Shot Gate Analysis",
        "",
        "## Input",
        "",
        f"- candidate_csv: `{report['candidate_csv']}`",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- tick_size: `{report['tick_size']}`",
        "",
        "## Results",
        "",
        "| gate | side rule | markets | first fill | completion 30s | after first fill | taker <=0.99 | pair p50 | delay p50s |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in report["results"]:
        row = item["stats"]
        lines.append(
            f"| {item['gate']} | {item['side_rule']} | {row['market_count']} | {row['first_fill_rate']} | "
            f"{row['completion_30s_rate']} | {row['completion_30s_rate_after_first_fill']} | "
            f"{row['taker_immediate_lte_0_99_rate']} | {row['maker_completion_pair_cost']['p50']} | "
            f"{row['completion_delay_s']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Semantics",
            "",
            "- Each gate selects at most one candidate per market.",
            "- If both YES and NO pass at the same timestamp, `side_rule` chooses the side.",
            "- `oracle_same_ts` is an upper bound, not an implementable rule.",
            "- Metrics remain public-market proxies; queue priority and private fills are not observable.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-csv", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--tick-size", type=float, default=0.01)
    parser.add_argument(
        "--side-rules",
        default="yes_first,higher_bid_size,more_extreme_quote,oracle_same_ts",
        help="Comma-separated side rules.",
    )
    args = parser.parse_args()

    with Path(args.candidate_csv).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    rows.sort(key=lambda r: (str(r["condition_id"]), int(float(r["candidate_ts_ms"])), str(r.get("first_side"))))

    gates = build_gates(args.tick_size)
    side_rules = [item.strip() for item in args.side_rules.split(",") if item.strip()]
    results: list[dict[str, Any]] = []
    for gate_name, gate_fn in gates.items():
        for side_rule in side_rules:
            selected = select_one_shot(rows, gate_fn, side_rule)
            results.append({"gate": gate_name, "side_rule": side_rule, "stats": compact(selected)})

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "candidate_csv": str(Path(args.candidate_csv).resolve()),
        "tick_size": args.tick_size,
        "results": results,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "one_shot_gate_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "one_shot_gate_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "result_count": len(results)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

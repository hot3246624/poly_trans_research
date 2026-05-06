#!/usr/bin/env python3
"""Summarize xuan's 30s completion signal from replay-derived tranche rows.

The input is the row-level output from analyze_xuan_winner_proxy_gate.py.
This script intentionally stays model-free: it builds falsifiable buckets around
open-time L2 execution edge and the first 30s cheap-completion evidence.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Callable


DEFAULT_INPUT = Path(
    "data/exports/xuan_research_runs/replay_20260503_full/"
    "xuan_winner_proxy_gate_5d/xuan_winner_proxy_gate_rows.csv"
)
DEFAULT_OUTPUT_DIR = Path("data/exports/xuan_signal_fingerprint_20260505")


def parse_float(value: str | None) -> float | None:
    if value in (None, "", "None", "nan", "NaN"):
        return None
    try:
        return float(value)
    except ValueError:
        return None


def quantile(values: list[float | None], p: float) -> float | None:
    xs = sorted(v for v in values if v is not None)
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open() as f:
        for raw in csv.DictReader(f):
            first_price = parse_float(raw.get("first_price"))
            first_l2_vwap = parse_float(raw.get("first_l2_vwap"))
            delay = parse_float(raw.get("observed_pair_delay_s"))
            min_pair_cost_30s = parse_float(raw.get("min_pair_cost_30s"))
            size = parse_float(raw.get("size")) or 0.0
            surplus = parse_float(raw.get("surplus_usdc")) or 0.0
            first_l2_edge = (
                first_l2_vwap - first_price
                if first_l2_vwap is not None and first_price is not None
                else None
            )
            raw["_first_l2_edge"] = first_l2_edge
            raw["_min_pair_cost_30s"] = min_pair_cost_30s
            raw["_observed_pair_cost"] = parse_float(raw.get("observed_pair_cost"))
            raw["_observed_pair_delay_s"] = delay
            raw["_size"] = size
            raw["_surplus_usdc"] = surplus
            raw["_first_is_winner"] = raw.get("first_is_winner") in ("1", "true", "True")
            raw["_fast30"] = delay is not None and delay <= 30.0
            rows.append(raw)
    return rows


def summarize(rows: list[dict[str, Any]], name: str, pred: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    xs = [r for r in rows if pred(r)]
    n = len(xs)
    if n == 0:
        return {
            "rule": name,
            "n": 0,
            "select_rate": 0.0,
        }
    total_size = sum(r["_size"] for r in xs)
    total_surplus = sum(r["_surplus_usdc"] for r in xs)
    slow_profit = sum(1 for r in xs if r.get("path_label") == "slow_profit_lt95")
    slow_bad = sum(1 for r in xs if r.get("path_label") == "slow_bad_ge95")
    return {
        "rule": name,
        "n": n,
        "select_rate": n / len(rows),
        "fast30_rate": sum(r["_fast30"] for r in xs) / n,
        "first_winner_rate": sum(r["_first_is_winner"] for r in xs) / n,
        "surplus_usdc": total_surplus,
        "surplus_per_tranche": total_surplus / n,
        "surplus_per_size": total_surplus / total_size if total_size else None,
        "pair_cost_p50": quantile([r["_observed_pair_cost"] for r in xs], 0.50),
        "pair_cost_p90": quantile([r["_observed_pair_cost"] for r in xs], 0.90),
        "pair_delay_p50": quantile([r["_observed_pair_delay_s"] for r in xs], 0.50),
        "pair_delay_p90": quantile([r["_observed_pair_delay_s"] for r in xs], 0.90),
        "min_pair_cost_30s_p50": quantile([r["_min_pair_cost_30s"] for r in xs], 0.50),
        "slow_profit": slow_profit,
        "slow_bad": slow_bad,
        "slow_profit_to_bad": slow_profit / slow_bad if slow_bad else None,
    }


def edge_bucket(edge: float | None) -> str:
    if edge is None:
        return "missing"
    if edge <= -0.01:
        return "<=-1c"
    if edge <= 0.0:
        return "-1c..0"
    if edge <= 0.01:
        return "0..+1c"
    if edge <= 0.03:
        return "+1..+3c"
    return ">+3c"


def min30_bucket(value: float | None) -> str:
    if value is None:
        return "missing"
    if value <= 0.90:
        return "<=0.90"
    if value <= 0.95:
        return "0.90..0.95"
    if value <= 0.99:
        return "0.95..0.99"
    return ">0.99"


def grouped(rows: list[dict[str, Any]], name: str, bucket_fn: Callable[[dict[str, Any]], str]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(bucket_fn(row), []).append(row)
    out = [summarize(rows, bucket, lambda r, bucket=bucket: bucket_fn(r) == bucket) for bucket in buckets]
    for item in out:
        item["group"] = name
    return sorted(out, key=lambda d: (-(d.get("n") or 0), d["rule"]))


def matrix(rows: list[dict[str, Any]], min_n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    keys = sorted(
        {
            (edge_bucket(r["_first_l2_edge"]), min30_bucket(r["_min_pair_cost_30s"]))
            for r in rows
        }
    )
    for edge, min30 in keys:
        item = summarize(
            rows,
            f"{edge} AND {min30}",
            lambda r, edge=edge, min30=min30: edge_bucket(r["_first_l2_edge"]) == edge
            and min30_bucket(r["_min_pair_cost_30s"]) == min30,
        )
        if item["n"] >= min_n:
            item["edge_bucket"] = edge
            item["min_pair_cost_30s_bucket"] = min30
            out.append(item)
    return sorted(out, key=lambda d: (-(d.get("surplus_per_size") or -999), -d["n"]))


def fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2%}"


def fmt_num(value: float | None, digits: int = 4) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def write_markdown(path: Path, payload: dict[str, Any]) -> None:
    def table(items: list[dict[str, Any]]) -> str:
        lines = [
            "| rule | n | fast30 | first winner | surplus/tranche | surplus/size | pair p50 | delay p50 | slow profit/bad |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for item in items:
            lines.append(
                "| "
                + " | ".join(
                    [
                        str(item["rule"]),
                        str(item["n"]),
                        fmt_pct(item.get("fast30_rate")),
                        fmt_pct(item.get("first_winner_rate")),
                        fmt_num(item.get("surplus_per_tranche"), 2),
                        fmt_pct(item.get("surplus_per_size")),
                        fmt_num(item.get("pair_cost_p50")),
                        fmt_num(item.get("pair_delay_p50"), 1),
                        fmt_num(item.get("slow_profit_to_bad"), 2),
                    ]
                )
                + " |"
            )
        return "\n".join(lines)

    selected = payload["selected_rules"]
    matrix_rows = payload["edge_x_min30_matrix"]
    baseline = selected[0]
    text = f"""# Xuan 30s Signal Fingerprint

Source: `{payload['source']}`

Rows: `{payload['row_count']}`

## Conclusion

The replay evidence does not support treating `30s completion` as the edge by itself. Bad cohorts can also pair quickly. The separable edge is a two-stage signal:

1. Open-time signal: `first_l2_edge = first_l2_vwap - first_price`. Strong positive edge (`> +3c`) materially improves pair cost and surplus; negative edge (`<= -1c`) is loss-making despite faster pairing.
2. Post-first-leg evidence: the first 30 seconds must reveal a cheap opposite-completion path. `min_pair_cost_30s <= 0.90` is the dominant continuation signal; `min_pair_cost_30s > 0.99` or missing is a repair/abort signal.

Baseline fast30 is `{fmt_pct(baseline.get('fast30_rate'))}`, but baseline surplus/size is only `{fmt_pct(baseline.get('surplus_per_size'))}`. The strongest interpretable cohort, `l2_gt3c_AND_min30_le_0.90`, has surplus/size `{fmt_pct(selected[7].get('surplus_per_size'))}` and first-winner rate `{fmt_pct(selected[7].get('first_winner_rate'))}`.

## Selected Rules

{table(selected)}

## Edge X 30s Evidence Matrix

{table(matrix_rows)}

## Strategy Implication

This points to a completion-first controller with explicit evidence stages:

- Open first leg only when L2 execution edge is non-negative, preferably `> +1c`, and upsize only when `> +3c`.
- Treat `<= -1c` L2 edge as a hard block unless another stronger signal is proven.
- After first fill, the first 30s are not passive waiting; they are an evidence window.
- If `min_pair_cost_seen_30s <= 0.90`, continue/complete aggressively because the cohort is strongly profitable.
- If no cheap path appears and current pair cost remains `> 0.99`, repair/abort rather than keep making the same inventory.
"""
    path.write_text(text)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--matrix-min-n", type=int, default=50)
    args = parser.parse_args()

    rows = load_rows(args.input)
    selected_rules = [
        summarize(rows, "ALL", lambda r: True),
        summarize(rows, "open_l2_edge_gt_3c", lambda r: r["_first_l2_edge"] is not None and r["_first_l2_edge"] > 0.03),
        summarize(rows, "open_l2_edge_1c_to_3c", lambda r: r["_first_l2_edge"] is not None and 0.01 < r["_first_l2_edge"] <= 0.03),
        summarize(rows, "open_l2_edge_le_neg1c", lambda r: r["_first_l2_edge"] is not None and r["_first_l2_edge"] <= -0.01),
        summarize(rows, "min_pair_cost_30s_le_0.90", lambda r: r["_min_pair_cost_30s"] is not None and r["_min_pair_cost_30s"] <= 0.90),
        summarize(rows, "min_pair_cost_30s_le_0.95", lambda r: r["_min_pair_cost_30s"] is not None and r["_min_pair_cost_30s"] <= 0.95),
        summarize(rows, "min_pair_cost_30s_gt_0.99_or_missing", lambda r: r["_min_pair_cost_30s"] is None or r["_min_pair_cost_30s"] > 0.99),
        summarize(
            rows,
            "l2_gt3c_AND_min30_le_0.90",
            lambda r: r["_first_l2_edge"] is not None
            and r["_first_l2_edge"] > 0.03
            and r["_min_pair_cost_30s"] is not None
            and r["_min_pair_cost_30s"] <= 0.90,
        ),
        summarize(
            rows,
            "l2_gt3c_AND_min30_le_0.95",
            lambda r: r["_first_l2_edge"] is not None
            and r["_first_l2_edge"] > 0.03
            and r["_min_pair_cost_30s"] is not None
            and r["_min_pair_cost_30s"] <= 0.95,
        ),
        summarize(
            rows,
            "l2_le_neg1c_OR_min30_gt0.99",
            lambda r: (r["_first_l2_edge"] is not None and r["_first_l2_edge"] <= -0.01)
            or (r["_min_pair_cost_30s"] is None or r["_min_pair_cost_30s"] > 0.99),
        ),
    ]
    payload = {
        "source": str(args.input),
        "row_count": len(rows),
        "selected_rules": selected_rules,
        "first_l2_edge_buckets": grouped(rows, "first_l2_edge", lambda r: edge_bucket(r["_first_l2_edge"])),
        "min_pair_cost_30s_buckets": grouped(rows, "min_pair_cost_30s", lambda r: min30_bucket(r["_min_pair_cost_30s"])),
        "edge_x_min30_matrix": matrix(rows, args.matrix_min_n),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "signal_fingerprint.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    with (args.output_dir / "selected_rules.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(selected_rules[0].keys()))
        writer.writeheader()
        writer.writerows(selected_rules)
    write_markdown(args.output_dir / "signal_fingerprint.md", payload)
    print(json.dumps({"output_dir": str(args.output_dir), "row_count": len(rows)}, indent=2))


if __name__ == "__main__":
    main()

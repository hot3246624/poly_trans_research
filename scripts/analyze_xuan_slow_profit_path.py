#!/usr/bin/env python3
"""Analyze xuan slow-profit path versus fast risk-control path.

Input is the L2 completion curve rows produced from replay. The goal is not to
fit a black-box model; it is to identify simple, auditable gates for:

- fast_control: observed pair delay <= 30s;
- slow_profit: observed pair delay > 30s and observed pair_cost < 0.95;
- slow_bad: observed pair delay > 30s and observed pair_cost >= 0.95.

This is a research script. It reads exported replay-derived CSV only and writes
reports under the requested output directory.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if v is not None)
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
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        delay = as_float(row.get("observed_pair_delay_s"))
        pair_cost = as_float(row.get("observed_pair_cost"))
        size = as_float(row.get("size")) or 0.0
        if delay is None or pair_cost is None:
            label = "unknown"
        elif delay <= 30:
            label = "fast_control"
        elif pair_cost < 0.95:
            label = "slow_profit_lt95"
        else:
            label = "slow_bad_ge95"
        row["path_label"] = label
        row["observed_surplus_usdc"] = round((1.0 - pair_cost) * size, 6) if pair_cost is not None else None
    return rows


def bucket_first_price(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_price"))
    if x is None:
        return "unknown"
    if x < 0.40:
        return "<0.40"
    if x < 0.55:
        return "0.40-0.55"
    if x < 0.70:
        return "0.55-0.70"
    return ">=0.70"


def bucket_size(row: dict[str, Any]) -> str:
    x = as_float(row.get("size"))
    if x is None:
        return "unknown"
    if x <= 80:
        return "<=80"
    if x <= 160:
        return "80-160"
    return ">160"


def bucket_offset(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_offset_s"))
    if x is None:
        return "unknown"
    if x < 30:
        return "000-030s"
    if x < 120:
        return "030-120s"
    if x < 240:
        return "120-240s"
    return "240-300s"


def bucket_hour(row: dict[str, Any]) -> str:
    ts = as_float(row.get("first_exec_ts_ms")) or as_float(row.get("first_ts_ms"))
    if ts is None:
        return "unknown"
    hour = dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).hour
    return f"{hour:02d}"


def bucket_min_pair_30s(row: dict[str, Any]) -> str:
    x = as_float(row.get("min_pair_cost_30s"))
    if x is None:
        return "missing"
    if x <= 0.90:
        return "<=0.90"
    if x <= 0.95:
        return "0.90-0.95"
    if x <= 1.00:
        return "0.95-1.00"
    if x <= 1.01:
        return "1.00-1.01"
    return ">1.01"


def bucket_min_pair_delay(row: dict[str, Any]) -> str:
    x = as_float(row.get("min_pair_cost_delay_s"))
    if x is None:
        return "missing"
    if x <= 5:
        return "000-005s"
    if x <= 15:
        return "005-015s"
    if x <= 25:
        return "015-025s"
    return "025-030s"


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    labels = Counter(row["path_label"] for row in rows)
    slow = labels["slow_profit_lt95"] + labels["slow_bad_ge95"]
    surplus = sum(as_float(row.get("observed_surplus_usdc")) or 0.0 for row in rows)
    return {
        "n": len(rows),
        "label_counts": dict(sorted(labels.items())),
        "fast_control_rate": rate(labels["fast_control"], len(rows)),
        "slow_profit_rate_all": rate(labels["slow_profit_lt95"], len(rows)),
        "slow_bad_rate_all": rate(labels["slow_bad_ge95"], len(rows)),
        "slow_profit_rate_among_slow": rate(labels["slow_profit_lt95"], slow),
        "surplus_sum": round(surplus, 6),
        "surplus_per_tranche": round(surplus / len(rows), 6) if rows else None,
        "observed_pair_cost": summarize([as_float(row.get("observed_pair_cost")) for row in rows]),
        "observed_pair_delay_s": summarize([as_float(row.get("observed_pair_delay_s")) for row in rows]),
        "min_pair_cost_30s": summarize([as_float(row.get("min_pair_cost_30s")) for row in rows]),
        "size": summarize([as_float(row.get("size")) for row in rows]),
    }


def bucket_table(
    rows: list[dict[str, Any]],
    name: str,
    bucket_fn: Callable[[dict[str, Any]], str],
    min_n: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[bucket_fn(row)].append(row)
    out = []
    for bucket, xs in sorted(grouped.items()):
        if len(xs) < min_n:
            continue
        item = compact(xs)
        item["feature"] = name
        item["bucket"] = bucket
        out.append(item)
    return out


def decision_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    slow_rows = [row for row in rows if row["path_label"] in {"slow_profit_lt95", "slow_bad_ge95"}]
    checks = {}
    for threshold in [0.90, 0.95, 0.99, 1.00, 1.01]:
        eligible = [
            row
            for row in slow_rows
            if as_float(row.get("min_pair_cost_30s")) is not None
            and float(row["min_pair_cost_30s"]) <= threshold
        ]
        blocked = [
            row
            for row in slow_rows
            if as_float(row.get("min_pair_cost_30s")) is None
            or float(row["min_pair_cost_30s"]) > threshold
        ]
        checks[f"slow_path_if_min30_lte_{threshold:g}"] = {
            "eligible": compact(eligible),
            "blocked": compact(blocked),
        }
    return checks


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = [
        "feature",
        "bucket",
        "n",
        "fast_control_rate",
        "slow_profit_rate_all",
        "slow_bad_rate_all",
        "slow_profit_rate_among_slow",
        "surplus_sum",
        "surplus_per_tranche",
        "label_counts",
        "observed_pair_cost",
        "observed_pair_delay_s",
        "min_pair_cost_30s",
        "size",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Xuan Slow-Profit Path Analysis",
        "",
        "## Summary",
        "",
        f"- rows: `{summary['n']}`",
        f"- label_counts: `{summary['label_counts']}`",
        f"- surplus_sum: `{summary['surplus_sum']}`",
        f"- fast_control_rate: `{summary['fast_control_rate']}`",
        f"- slow_profit_rate_all: `{summary['slow_profit_rate_all']}`",
        f"- slow_bad_rate_all: `{summary['slow_bad_rate_all']}`",
        "",
        "## 30s Slow-Path Decision Checks",
        "",
        "| rule | eligible n | eligible slow-profit/slow | eligible surplus | blocked n | blocked slow-profit/slow | blocked surplus |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for rule, item in report["decision_checks"].items():
        eligible = item["eligible"]
        blocked = item["blocked"]
        lines.append(
            f"| {rule} | {eligible['n']} | {eligible['slow_profit_rate_among_slow']} | "
            f"{eligible['surplus_sum']} | {blocked['n']} | {blocked['slow_profit_rate_among_slow']} | "
            f"{blocked['surplus_sum']} |"
        )

    for feature in ["min_pair_cost_30s", "first_price", "size", "offset", "hour"]:
        lines.extend(
            [
                "",
                f"## Buckets: {feature}",
                "",
                "| bucket | n | fast | slow profit | slow bad | slow-profit/slow | surplus/tranche | min30 p50 | pair p50 |",
                "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in report["bucket_tables"][feature]:
            lines.append(
                f"| {row['bucket']} | {row['n']} | {row['fast_control_rate']} | "
                f"{row['slow_profit_rate_all']} | {row['slow_bad_rate_all']} | "
                f"{row['slow_profit_rate_among_slow']} | {row['surplus_per_tranche']} | "
                f"{row['min_pair_cost_30s']['p50']} | {row['observed_pair_cost']['p50']} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `fast_control` is the inventory-risk layer, not the main profit source.",
            "- `slow_profit_lt95` is the profit layer; `slow_bad_ge95` is the dangerous tail.",
            "- If an active tranche has seen a cheap L2 completion opportunity in the first 30s but was not filled, slow continuation has much better expectancy than if no cheap window appeared.",
            "- This supports a two-path controller: near-parity repair for ordinary tranches, slow-profit path only when early cheap-window evidence exists.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--curve-rows",
        default="data/exports/xuan_research_runs/replay_20260502_full/xuan_l2_completion_curve_5d/xuan_l2_completion_curve_rows.csv",
    )
    parser.add_argument("--output-dir", default="data/exports/xuan_slow_profit_path")
    parser.add_argument("--min-bucket-n", type=int, default=50)
    args = parser.parse_args()

    rows = load_rows(Path(args.curve_rows))
    bucket_tables = {
        "min_pair_cost_30s": bucket_table(rows, "min_pair_cost_30s", bucket_min_pair_30s, args.min_bucket_n),
        "min_pair_delay": bucket_table(rows, "min_pair_delay", bucket_min_pair_delay, args.min_bucket_n),
        "first_price": bucket_table(rows, "first_price", bucket_first_price, args.min_bucket_n),
        "size": bucket_table(rows, "size", bucket_size, args.min_bucket_n),
        "offset": bucket_table(rows, "offset", bucket_offset, args.min_bucket_n),
        "hour": bucket_table(rows, "hour", bucket_hour, args.min_bucket_n),
    }
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "curve_rows": str(Path(args.curve_rows).resolve()),
        "summary": compact(rows),
        "decision_checks": decision_checks(rows),
        "bucket_tables": bucket_tables,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "xuan_slow_profit_path_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_slow_profit_path_report.md").write_text(render_report(report), encoding="utf-8")
    flat_bucket_rows = [row for table in bucket_tables.values() for row in table]
    write_csv(output_dir / "xuan_slow_profit_path_buckets.csv", flat_bucket_rows)
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

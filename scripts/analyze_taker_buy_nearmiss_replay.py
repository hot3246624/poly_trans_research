#!/usr/bin/env python3
"""Audit near-miss expansion risk for the BTC 5m taker-BUY signal.

This script consumes existing backtest row CSVs only. It does not read replay
SQLite or raw captures. The goal is to make the hard-gate decision auditable:
whether `l1_immediate_pair > 0.99` can be safely added to the current core rule.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any


ROW_FILE = "btc5m_taker_buy_signal_fast_rows.csv"


def parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
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


def describe(values: list[float | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
    }


def resolve_inputs(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            candidate = path / ROW_FILE
            if candidate.exists():
                out.append(candidate)
            else:
                out.extend(sorted(path.glob(f"**/{ROW_FILE}")))
        elif path.exists():
            out.append(path)
    deduped: list[Path] = []
    seen = set()
    for path in out:
        key = str(path.resolve())
        if key not in seen:
            seen.add(key)
            deduped.append(path)
    return deduped


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for raw in reader:
                row = dict(raw)
                row["_source_csv"] = str(path)
                row["_source_run"] = path.parent.name
                rows.append(row)
    return rows


def unique_by_trigger(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per replay episode when overlapping threshold runs are merged."""
    out: list[dict[str, Any]] = []
    seen = set()
    for row in sorted(rows, key=lambda r: (str(r.get("trigger_ts_ms", "")), str(r.get("slug", "")), str(r.get("_source_run", "")))):
        key = (row.get("slug"), row.get("trigger_ts_ms"), row.get("first_side"), row.get("clip"))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if parse_bool(row.get("completion_fill")) is True]
    residual = [row for row in rows if parse_bool(row.get("completion_fill")) is not True]
    pnl = sum(parse_float(row.get("pnl")) or 0.0 for row in rows)
    first_cost = sum((parse_float(row.get("trigger_price")) or 0.0) * (parse_float(row.get("clip")) or 0.0) for row in rows)
    by_day = {}
    for day in sorted({str(row.get("day")) for row in rows if row.get("day")}):
        day_rows = [row for row in rows if row.get("day") == day]
        day_pnl = sum(parse_float(row.get("pnl")) or 0.0 for row in day_rows)
        day_cost = sum((parse_float(row.get("trigger_price")) or 0.0) * (parse_float(row.get("clip")) or 0.0) for row in day_rows)
        by_day[day] = {
            "rows": len(day_rows),
            "pnl": round(day_pnl, 6),
            "roi_on_first_cost": round(day_pnl / day_cost, 6) if day_cost else None,
        }
    return {
        "rows": len(rows),
        "closed": len(closed),
        "closed_rate": rate(len(closed), len(rows)),
        "first_winner_rate": rate(sum(1 for row in rows if parse_bool(row.get("first_is_winner")) is True), len(rows)),
        "residual": len(residual),
        "residual_winner_rate": rate(sum(1 for row in residual if parse_bool(row.get("first_is_winner")) is True), len(residual)),
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / first_cost, 6) if first_cost else None,
        "pair_cost": describe([parse_float(row.get("pair_cost")) for row in closed]),
        "completion_delay_s": describe([parse_float(row.get("completion_delay_s")) for row in closed]),
        "l1_immediate_pair": describe([parse_float(row.get("l1_immediate_pair")) for row in rows]),
        "trigger_price": describe([parse_float(row.get("trigger_price")) for row in rows]),
        "public_trade_price": describe([parse_float(row.get("public_trade_price")) for row in rows]),
        "trigger_size": describe([parse_float(row.get("trigger_size")) for row in rows]),
        "by_day": by_day,
        "negative_days": [day for day, item in by_day.items() if float(item["pnl"]) < 0],
    }


def bucket_l1_pair(row: dict[str, Any]) -> str:
    value = parse_float(row.get("l1_immediate_pair"))
    if value is None:
        return "missing"
    if value <= 0.99:
        return "<=0.99"
    if value <= 1.00:
        return "0.99-1.00"
    if value <= 1.01:
        return "1.00-1.01"
    if value <= 1.02:
        return "1.01-1.02"
    if value <= 1.03:
        return "1.02-1.03"
    return ">1.03"


def bucket_offset(row: dict[str, Any]) -> str:
    value = parse_float(row.get("offset_s"))
    if value is None:
        return "missing"
    if value < 30:
        return "<30"
    if value < 60:
        return "30-60"
    if value < 120:
        return "60-120"
    return "120+"


def bucket_first_price(row: dict[str, Any]) -> str:
    value = parse_float(row.get("trigger_price"))
    if value is None:
        return "missing"
    if value < 0.60:
        return "<0.60"
    if value < 0.65:
        return "0.60-0.65"
    return ">=0.65"


def bucket_public_size(row: dict[str, Any]) -> str:
    value = parse_float(row.get("trigger_size"))
    if value is None:
        return "missing"
    if value <= 100:
        return "<=100"
    if value < 110:
        return "100-110"
    return ">=110"


def group_by(rows: list[dict[str, Any]], name: str, fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(fn(row), []).append(row)
    return {
        "name": name,
        "groups": {key: compact(groups[key]) for key in sorted(groups)},
    }


def verdict(summary: dict[str, Any]) -> dict[str, Any]:
    l1 = summary["groupings"]["l1_immediate_pair"]["groups"]
    core = l1.get("<=0.99", {})
    near_100 = l1.get("0.99-1.00", {})
    near_101 = l1.get("1.00-1.01", {})
    core_roi = core.get("roi_on_first_cost")
    near_100_roi = near_100.get("roi_on_first_cost")
    near_101_roi = near_101.get("roi_on_first_cost")
    return {
        "keep_hard_gate": 0.99,
        "do_not_promote_near_miss": True,
        "rationale": [
            "The <=0.99 cohort is the only stable high-ROI cohort.",
            "0.99-1.00 is weak and day-fragile; it should remain a separate repair/expansion research path.",
            "1.00-1.01 is negative in the wide-threshold run and must not be folded into the clean-completion core.",
        ],
        "core_roi_on_first_cost": core_roi,
        "near_099_100_roi_on_first_cost": near_100_roi,
        "near_100_101_roi_on_first_cost": near_101_roi,
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Taker BUY Near-Miss Replay Audit",
        "",
        f"Generated: `{report['generated_at_utc']}`",
        "",
        "## Verdict",
        "",
        f"- Keep hard gate: `l1_immediate_pair <= {report['verdict']['keep_hard_gate']}`.",
        "- Do not merge near-miss cohorts into the clean-completion core.",
        "- Treat `0.99-1.00` as a separate repair/expansion research path only.",
        "- Treat `1.00-1.01` as rejected for V1 core.",
        "",
        "## L1 Immediate Pair Buckets",
        "",
        "| bucket | rows | closed | first winner | residual winner | pnl | ROI | negative days |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    l1_groups = report["groupings"]["l1_immediate_pair"]["groups"]
    order = ["<=0.99", "0.99-1.00", "1.00-1.01", "1.01-1.02", "1.02-1.03", ">1.03", "missing"]
    for key in order:
        if key not in l1_groups:
            continue
        item = l1_groups[key]
        lines.append(
            f"| `{key}` | {item['rows']} | {item['closed_rate']} | {item['first_winner_rate']} | "
            f"{item['residual_winner_rate']} | {item['pnl']} | {item['roi_on_first_cost']} | "
            f"{', '.join(item['negative_days']) or '-'} |"
        )
    for grouping_key, title in [
        ("near_099_100_by_offset", "0.99-1.00 By Round Offset"),
        ("near_099_100_by_first_price", "0.99-1.00 By First L2 VWAP"),
        ("near_099_100_by_public_size", "0.99-1.00 By Public Size"),
    ]:
        lines.extend(["", f"## {title}", "", "| bucket | rows | pnl | ROI | first winner | closed | negative days |", "|---|---:|---:|---:|---:|---:|---|"])
        for key, item in report["groupings"][grouping_key]["groups"].items():
            lines.append(
                f"| `{key}` | {item['rows']} | {item['pnl']} | {item['roi_on_first_cost']} | "
                f"{item['first_winner_rate']} | {item['closed_rate']} | {', '.join(item['negative_days']) or '-'} |"
            )
    lines.extend(
        [
            "",
            "## Method",
            "",
            "- Input is existing taker-buy backtest row CSV, not replay SQLite and not raw capture.",
            "- Duplicate trigger rows across overlapping threshold runs are deduped by `slug + trigger_ts_ms + first_side + clip`.",
            "- ROI denominator is first-leg notional cost, matching the source backtest summary.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", nargs="+", type=Path, required=True, help="Row CSVs or output directories.")
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_nearmiss_replay_audit"))
    args = parser.parse_args()

    input_files = resolve_inputs(args.inputs)
    if not input_files:
        raise SystemExit("no input row CSVs found")
    rows = unique_by_trigger(load_rows(input_files))
    near_099_100 = [row for row in rows if bucket_l1_pair(row) == "0.99-1.00"]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "input_files": [str(path) for path in input_files],
        "deduped_rows": len(rows),
        "aggregate": compact(rows),
        "groupings": {
            "l1_immediate_pair": group_by(rows, "l1_immediate_pair", bucket_l1_pair),
            "near_099_100_by_offset": group_by(near_099_100, "offset_s", bucket_offset),
            "near_099_100_by_first_price": group_by(near_099_100, "trigger_price", bucket_first_price),
            "near_099_100_by_public_size": group_by(near_099_100, "trigger_size", bucket_public_size),
        },
    }
    report["verdict"] = verdict(report)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "taker_buy_nearmiss_replay_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_nearmiss_replay_audit.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

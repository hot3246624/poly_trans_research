#!/usr/bin/env python3
"""Run a fixed-rule OOS validation for the taker-BUY top finalist.

This intentionally does not search parameters. It validates the frozen
research finalist on newly available replay days:

  price 0.55-0.70, size 100-150, first_l2_vwap 0.60-0.75,
  high-side, l1<=0.995, completion<=0.95, block_after_residual.

Inputs are replay SQLite DBs opened by downstream scripts in read-only mode.
No raw data is read and no replay DB is modified.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import subprocess
import sys
from pathlib import Path
from typing import Any


TOP_FINALIST = {
    "price_lo": 0.55,
    "price_hi": 0.70,
    "size_lo": 100.0,
    "size_hi": 150.0,
    "first_lo": 0.60,
    "first_hi": 0.75,
    "offset_lo": 0.0,
    "offset_hi": 240.0,
    "max_l1_pair": 0.995,
    "pair_ceiling": 0.95,
    "side_alignment": "high",
    "block_after_residual": True,
    "cooldown_s": 10,
    "clip": 60.0,
}


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


def run_cmd(cmd: list[str]) -> None:
    print("+ " + " ".join(cmd), file=sys.stderr, flush=True)
    subprocess.run(cmd, check=True)


def compact_rows(rows_csv: Path) -> dict[str, Any]:
    rows = []
    with rows_csv.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = dict(raw)
            row["pnl"] = parse_float(row.get("pnl")) or 0.0
            row["trigger_price"] = parse_float(row.get("trigger_price")) or 0.0
            row["clip"] = parse_float(row.get("clip")) or TOP_FINALIST["clip"]
            row["completion_fill"] = parse_bool(row.get("completion_fill"))
            row["first_is_winner"] = parse_bool(row.get("first_is_winner"))
            rows.append(row)
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        day = str(row.get("day"))
        item = by_day.setdefault(day, {"rows": 0, "pnl": 0.0, "cost": 0.0, "closed": 0, "first_winner": 0})
        item["rows"] += 1
        item["pnl"] += row["pnl"]
        item["cost"] += row["trigger_price"] * row["clip"]
        item["closed"] += 1 if row["completion_fill"] else 0
        item["first_winner"] += 1 if row["first_is_winner"] else 0
    total_pnl = sum(float(item["pnl"]) for item in by_day.values())
    total_cost = sum(float(item["cost"]) for item in by_day.values())
    closed = sum(int(item["closed"]) for item in by_day.values())
    first_winner = sum(int(item["first_winner"]) for item in by_day.values())
    for item in by_day.values():
        item["pnl"] = round(float(item["pnl"]), 6)
        item["roi_on_first_cost"] = round(float(item["pnl"]) / float(item["cost"]), 6) if item["cost"] else None
        item["closed_rate"] = round(float(item["closed"]) / float(item["rows"]), 6) if item["rows"] else None
        item["first_winner_rate"] = round(float(item["first_winner"]) / float(item["rows"]), 6) if item["rows"] else None
        item.pop("cost", None)
    return {
        "rows": len(rows),
        "pnl": round(total_pnl, 6),
        "roi_on_first_cost": round(total_pnl / total_cost, 6) if total_cost else None,
        "closed_rate": round(closed / len(rows), 6) if rows else None,
        "first_winner_rate": round(first_winner / len(rows), 6) if rows else None,
        "by_day": {day: by_day[day] for day in sorted(by_day)},
        "negative_days": [day for day, item in sorted(by_day.items()) if float(item["pnl"]) < 0],
        "min_day_pnl": round(min((float(item["pnl"]) for item in by_day.values()), default=0.0), 6),
    }


def gate_verdict(summary: dict[str, Any], args: argparse.Namespace, overlap: dict[str, Any] | None) -> dict[str, Any]:
    day_count = len(summary["by_day"])
    min_rows = args.min_rows_per_day * max(day_count, 1)
    checks = {
        "rows_ok": summary["rows"] >= min_rows,
        "no_negative_days": not summary["negative_days"],
        "roi_ok": (summary["roi_on_first_cost"] or 0.0) >= args.min_roi,
        "closed_rate_ok": (summary["closed_rate"] or 0.0) >= args.min_closed_rate,
        "first_winner_ok": (summary["first_winner_rate"] or 0.0) >= args.min_first_winner_rate,
    }
    if overlap is not None:
        same_near30 = (((overlap.get("coverage") or {}).get("xuan_same_near30") or {}).get("rate")) or 0.0
        checks["xuan_overlap_ok"] = same_near30 >= args.min_xuan_same_near30_rate
    passed = all(checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "decision": "shadow_freeze_candidate" if passed else "do_not_freeze_return_to_research",
        "thresholds": {
            "min_rows_per_day": args.min_rows_per_day,
            "min_roi": args.min_roi,
            "min_closed_rate": args.min_closed_rate,
            "min_first_winner_rate": args.min_first_winner_rate,
            "min_xuan_same_near30_rate": None if overlap is None else args.min_xuan_same_near30_rate,
        },
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = report["policy_summary"]
    verdict = report["verdict"]
    lines = [
        "# Taker BUY Finalist OOS Validation",
        "",
        f"- Decision: `{verdict['decision']}`",
        f"- Passed: `{verdict['passed']}`",
        f"- Days: `{report['days']}`",
        "",
        "## Aggregate",
        "",
        "| rows | pnl | ROI | closed | first winner | min day pnl | negative days |",
        "|---:|---:|---:|---:|---:|---:|---|",
        f"| {summary['rows']} | {summary['pnl']} | {summary['roi_on_first_cost']} | "
        f"{summary['closed_rate']} | {summary['first_winner_rate']} | {summary['min_day_pnl']} | "
        f"{', '.join(summary['negative_days']) or '-'} |",
        "",
        "## By Day",
        "",
        "| day | rows | pnl | ROI | closed | first winner |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for day, item in summary["by_day"].items():
        lines.append(
            f"| {day} | {item['rows']} | {item['pnl']} | {item['roi_on_first_cost']} | "
            f"{item['closed_rate']} | {item['first_winner_rate']} |"
        )
    lines.extend(["", "## Gate Checks", "", "| check | pass |", "|---|---:|"])
    for key, value in verdict["checks"].items():
        lines.append(f"| `{key}` | `{value}` |")
    if report.get("xuan_overlap") is not None:
        coverage = report["xuan_overlap"].get("coverage") or {}
        lines.extend(["", "## Xuan Overlap", "", "| metric | count | rate |", "|---|---:|---:|"])
        for key, item in coverage.items():
            lines.append(f"| `{key}` | {item.get('count')} | {item.get('rate')} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--days", required=True, help="Comma-separated YYYY-MM-DD days.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--skip-cache-build", action="store_true")
    parser.add_argument("--skip-xuan-overlap", action="store_true")
    parser.add_argument("--min-rows-per-day", type=int, default=40)
    parser.add_argument("--min-roi", type=float, default=0.03)
    parser.add_argument("--min-closed-rate", type=float, default=0.70)
    parser.add_argument("--min-first-winner-rate", type=float, default=0.68)
    parser.add_argument("--min-xuan-same-near30-rate", type=float, default=0.45)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.output_dir / "candidate_cache"
    policy_dir = args.output_dir / "policy_rows"
    overlap_dir = args.output_dir / "xuan_overlap"

    if not args.skip_cache_build:
        run_cmd(
            [
                sys.executable,
                "scripts/build_taker_buy_signal_candidate_cache.py",
                "--replay-root",
                str(args.replay_root),
                "--days",
                args.days,
                "--min-trade-price",
                str(TOP_FINALIST["price_lo"]),
                "--max-trade-price",
                str(TOP_FINALIST["price_hi"]),
                "--min-trade-size",
                str(TOP_FINALIST["size_lo"]),
                "--max-trade-size",
                str(TOP_FINALIST["size_hi"]),
                "--clip",
                str(TOP_FINALIST["clip"]),
                "--output-dir",
                str(cache_dir),
            ]
        )

    cache_csv = cache_dir / "taker_buy_signal_candidate_cache.csv"
    run_cmd(
        [
            sys.executable,
            "scripts/export_taker_buy_candidate_policy_rows.py",
            "--cache-csv",
            str(cache_csv),
            "--output-dir",
            str(policy_dir),
            "--price-lo",
            str(TOP_FINALIST["price_lo"]),
            "--price-hi",
            str(TOP_FINALIST["price_hi"]),
            "--size-lo",
            str(TOP_FINALIST["size_lo"]),
            "--size-hi",
            str(TOP_FINALIST["size_hi"]),
            "--first-lo",
            str(TOP_FINALIST["first_lo"]),
            "--first-hi",
            str(TOP_FINALIST["first_hi"]),
            "--max-l1-pair",
            str(TOP_FINALIST["max_l1_pair"]),
            "--pair-ceiling",
            str(TOP_FINALIST["pair_ceiling"]),
            "--side-alignment",
            str(TOP_FINALIST["side_alignment"]),
            "--cooldown-s",
            str(TOP_FINALIST["cooldown_s"]),
            "--block-after-residual",
        ]
    )

    rows_csv = policy_dir / "taker_buy_candidate_policy_rows.csv"
    overlap = None
    if not args.skip_xuan_overlap:
        run_cmd(
            [
                sys.executable,
                "scripts/analyze_taker_buy_signal_xuan_overlap.py",
                "--rows-csv",
                str(rows_csv),
                "--replay-root",
                str(args.replay_root),
                "--output-dir",
                str(overlap_dir),
            ]
        )
        overlap_path = overlap_dir / "taker_buy_signal_xuan_overlap_summary.json"
        if overlap_path.exists():
            overlap = json.loads(overlap_path.read_text(encoding="utf-8"))

    policy_summary = compact_rows(rows_csv)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "days": args.days,
        "rule": TOP_FINALIST,
        "paths": {
            "cache_dir": str(cache_dir),
            "policy_dir": str(policy_dir),
            "rows_csv": str(rows_csv),
            "xuan_overlap_dir": None if args.skip_xuan_overlap else str(overlap_dir),
        },
        "policy_summary": policy_summary,
        "xuan_overlap": overlap,
        "verdict": gate_verdict(policy_summary, args, overlap),
    }
    (args.output_dir / "taker_buy_finalist_oos_validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_finalist_oos_validation.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "decision": report["verdict"]["decision"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

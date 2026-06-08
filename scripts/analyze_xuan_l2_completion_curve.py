#!/usr/bin/env python3
"""Estimate xuan 30s L2 completion threshold curve.

For each xuan tranche, use the public exact-match timestamp when available,
compute first-leg L2 sweep VWAP, then scan opposite-side L2 asks over the next
30s and record the minimum pair cost available for the same size.

This produces a completion-rate curve by pair-cost ceiling without rerunning one
threshold at a time. It reads replay SQLite read-only and does not use raw data.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from analyze_xuan_l2_counterfactual import (
    TRUSTED_START_MS,
    connect_ro,
    iso_ms,
    load_l2_window,
    load_latest_l2_before,
    load_match_index,
    load_tranches,
    rate,
    summarize,
    sweep_vwap,
)


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")


def parse_thresholds(value: str) -> list[float]:
    return [float(x.strip()) for x in value.split(",") if x.strip()]


def run_curve(
    replay_root: Path,
    days: list[str],
    tranches_csv: Path,
    match_csv: Path | None,
    max_l2_age_ms: int,
    window_s: int,
) -> list[dict[str, Any]]:
    day_set = set(days)
    tranches = load_tranches(tranches_csv, day_set)
    match_index = load_match_index(match_csv) if match_csv else {}
    conns = {}
    try:
        for day in days:
            db_path = replay_root / day / "crypto_5m.sqlite"
            if db_path.exists():
                conns[day] = connect_ro(db_path)
        rows = []
        for item in tranches:
            conn = conns.get(item["day"])
            if conn is None:
                continue
            first_match = match_index.get(str(item.get("first_tx") or ""))
            first_exec_ts_ms = int(item["first_ts_ms"])
            first_ts_source = "xuan_data_api_ts"
            if first_match is not None:
                first_exec_ts_ms = int(first_match["match_trade_ts_ms"])
                first_ts_source = "matched_public_trade_ts"
            first_l2 = load_latest_l2_before(
                conn,
                str(item["condition_id"]),
                str(item["first_side"]),
                first_exec_ts_ms,
                max_l2_age_ms,
            )
            row = {
                **item,
                "first_exec_ts_ms": first_exec_ts_ms,
                "first_exec_iso": iso_ms(first_exec_ts_ms),
                "first_ts_source": first_ts_source,
                "first_match_time_diff_ms": None if first_match is None else first_match["match_time_diff_ms"],
                "first_l2_full_size": False,
                "first_l2_vwap": None,
                "first_l2_worst_px": None,
                "min_pair_cost_30s": None,
                "min_pair_cost_ts_ms": None,
                "min_pair_cost_iso": None,
                "min_pair_cost_delay_s": None,
                "min_completion_vwap": None,
                "min_completion_worst_px": None,
                "status": "no_first_l2",
            }
            if first_l2 is None:
                rows.append(row)
                continue
            first_vwap, _, first_worst_px = sweep_vwap(first_l2, float(item["size"]))
            if first_vwap is None:
                row["status"] = "insufficient_first_l2_depth"
                rows.append(row)
                continue
            row["first_l2_full_size"] = True
            row["first_l2_vwap"] = round(first_vwap, 6)
            row["first_l2_worst_px"] = None if first_worst_px is None else round(first_worst_px, 6)
            end_ms = min(first_exec_ts_ms + window_s * 1000, int(item["round_end_ms"]))
            opp_books = load_l2_window(
                conn,
                str(item["condition_id"]),
                str(item["opposite_side"]),
                first_exec_ts_ms,
                end_ms,
            )
            best: dict[str, Any] | None = None
            for book in opp_books:
                completion_vwap, _, completion_worst_px = sweep_vwap(book, float(item["size"]))
                if completion_vwap is None:
                    continue
                pair_cost = first_vwap + completion_vwap
                if best is None or pair_cost < best["pair_cost"]:
                    best = {
                        "pair_cost": pair_cost,
                        "ts_ms": book.recv_ms,
                        "completion_vwap": completion_vwap,
                        "completion_worst_px": completion_worst_px,
                    }
            if best is None:
                row["status"] = "no_full_opposite_l2_in_window"
                rows.append(row)
                continue
            row.update(
                {
                    "min_pair_cost_30s": round(best["pair_cost"], 6),
                    "min_pair_cost_ts_ms": int(best["ts_ms"]),
                    "min_pair_cost_iso": iso_ms(int(best["ts_ms"])),
                    "min_pair_cost_delay_s": round((int(best["ts_ms"]) - first_exec_ts_ms) / 1000.0, 3),
                    "min_completion_vwap": round(float(best["completion_vwap"]), 6),
                    "min_completion_worst_px": None
                    if best["completion_worst_px"] is None
                    else round(float(best["completion_worst_px"]), 6),
                    "status": "ok",
                }
            )
            rows.append(row)
        return rows
    finally:
        for conn in conns.values():
            conn.close()


def threshold_curve(rows: list[dict[str, Any]], thresholds: list[float]) -> list[dict[str, Any]]:
    out = []
    den = len(rows)
    observed_30s = sum(1 for row in rows if float(row["observed_pair_delay_s"]) <= 30)
    for threshold in thresholds:
        hits = [
            row
            for row in rows
            if row.get("min_pair_cost_30s") not in (None, "") and float(row["min_pair_cost_30s"]) <= threshold
        ]
        out.append(
            {
                "threshold": threshold,
                "hit_count": len(hits),
                "hit_rate": rate(len(hits), den),
                "observed_30s_rate": rate(observed_30s, den),
                "median_delay_s": summarize([row.get("min_pair_cost_delay_s") for row in hits])["p50"],
                "median_min_pair_cost": summarize([row.get("min_pair_cost_30s") for row in hits])["p50"],
            }
        )
    return out


def aggregate(rows: list[dict[str, Any]], thresholds: list[float]) -> dict[str, Any]:
    latest = [row for row in rows if row["day"] == "2026-05-01"]
    return {
        "all": {
            "count": len(rows),
            "status_counts": {s: sum(1 for row in rows if row["status"] == s) for s in sorted({r["status"] for r in rows})},
            "first_l2_full_size_rate": rate(sum(1 for row in rows if row["first_l2_full_size"]), len(rows)),
            "observed_30s_rate": rate(sum(1 for row in rows if float(row["observed_pair_delay_s"]) <= 30), len(rows)),
            "min_pair_cost_30s": summarize([row.get("min_pair_cost_30s") for row in rows]),
            "threshold_curve": threshold_curve(rows, thresholds),
        },
        "latest_2026_05_01": {
            "count": len(latest),
            "status_counts": {s: sum(1 for row in latest if row["status"] == s) for s in sorted({r["status"] for r in latest})},
            "first_l2_full_size_rate": rate(sum(1 for row in latest if row["first_l2_full_size"]), len(latest)),
            "observed_30s_rate": rate(sum(1 for row in latest if float(row["observed_pair_delay_s"]) <= 30), len(latest)),
            "min_pair_cost_30s": summarize([row.get("min_pair_cost_30s") for row in latest]),
            "threshold_curve": threshold_curve(latest, thresholds),
        },
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan L2 Completion Curve",
        "",
        "## Scope",
        "",
        f"- days: `{report['days']}`",
        f"- window_s: `{report['parameters']['window_s']}`",
        f"- max_l2_age_ms: `{report['parameters']['max_l2_age_ms']}`",
        "- Uses exact public trade timestamp when available.",
        "- Reads replay SQLite read-only. Does not use raw or own execution truth.",
        "",
    ]
    for cohort in ["all", "latest_2026_05_01"]:
        item = report["aggregate"][cohort]
        lines.extend(
            [
                f"## {cohort}",
                "",
                f"- count: `{item['count']}`",
                f"- observed_30s_rate: `{item['observed_30s_rate']}`",
                f"- first_l2_full_size_rate: `{item['first_l2_full_size_rate']}`",
                f"- min_pair_cost_30s p50/p90: `{item['min_pair_cost_30s']['p50']}` / `{item['min_pair_cost_30s']['p90']}`",
                "",
                "| threshold | hit_rate | observed_30s | median delay | median min pair |",
                "|---:|---:|---:|---:|---:|",
            ]
        )
        for row in item["threshold_curve"]:
            lines.append(
                f"| {row['threshold']} | {row['hit_rate']} | {row['observed_30s_rate']} | "
                f"{row['median_delay_s']} | {row['median_min_pair_cost']} |"
            )
        lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument(
        "--tranches-csv",
        default="data/exports/xuan_research_runs/replay_20260502_full/xuan_tranche_ladder/xuan_tranche_ladder_tranches.csv",
    )
    parser.add_argument("--match-csv")
    parser.add_argument("--output-dir", default="data/exports/xuan_l2_completion_curve")
    parser.add_argument("--thresholds", default="0.99,1.0,1.0025,1.005,1.0075,1.01,1.015,1.02")
    parser.add_argument("--window-s", type=int, default=30)
    parser.add_argument("--max-l2-age-ms", type=int, default=1000)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    thresholds = parse_thresholds(args.thresholds)
    rows = run_curve(
        Path(args.replay_root),
        days,
        Path(args.tranches_csv),
        Path(args.match_csv) if args.match_csv else None,
        args.max_l2_age_ms,
        args.window_s,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_l2_completion_curve_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "parameters": {
            "tranches_csv": str(Path(args.tranches_csv).resolve()),
            "match_csv": args.match_csv,
            "thresholds": thresholds,
            "window_s": args.window_s,
            "max_l2_age_ms": args.max_l2_age_ms,
        },
        "aggregate": aggregate(rows, thresholds),
        "outputs": {
            "rows_csv": str((output_dir / "xuan_l2_completion_curve_rows.csv").resolve()),
            "summary_json": str((output_dir / "xuan_l2_completion_curve_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_l2_completion_curve_report.md").resolve()),
        },
    }
    (output_dir / "xuan_l2_completion_curve_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_l2_completion_curve_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

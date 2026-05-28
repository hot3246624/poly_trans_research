#!/usr/bin/env python3
"""Compare old BTC completion baseline with the BTC V1 completion adapter."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASELINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DEFAULT_BTC_ADAPTER = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
)

METRICS = (
    "candidate_count",
    "selected_candidate_count",
    "pair_actions",
    "gross_buy_cost",
    "pair_pnl",
    "fee_after_pnl",
    "net_roi",
    "residual_cost",
    "residual_cost_rate",
    "residual_qty",
    "residual_qty_rate",
    "official_taker_fee",
)
SEED_BLOCK_METRICS = (
    "seed_block_alignment",
    "seed_block_offset",
    "seed_block_price_band",
    "seed_block_l1_pair_cap",
    "seed_block_cooldown",
    "seed_block_target",
    "seed_block_imbalance_qty",
    "seed_block_imbalance_cost",
    "seed_block_residual_cooldown",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def safe_div(num: float | None, den: float | None) -> float | None:
    if num is None or den in (None, 0.0):
        return None
    return num / den


def metric_delta(metric: str, old_value: Any, new_value: Any) -> dict[str, Any]:
    old_f = to_float(old_value)
    new_f = to_float(new_value)
    delta = None if old_f is None or new_f is None else new_f - old_f
    ratio = safe_div(new_f, old_f)
    return {
        "metric": metric,
        "old_btc_baseline": old_value if old_value is not None else "",
        "btc_v1_completion_adapter": new_value if new_value is not None else "",
        "delta": rounded(delta),
        "ratio_new_over_old": rounded(ratio),
    }


def normalize_core_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    out = dict(metrics)
    if "selected_candidate_count" not in out and "seed_actions" in out:
        out["selected_candidate_count"] = out["seed_actions"]
    if "residual_cost_rate" not in out and "cost_residual_rate" in out:
        out["residual_cost_rate"] = out["cost_residual_rate"]
    if "residual_qty_rate" not in out and "qty_residual_rate" in out:
        out["residual_qty_rate"] = out["qty_residual_rate"]
    return out


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    old_manifest_path = args.old_baseline_dir / "RESULT_SUMMARY_MANIFEST.json"
    new_manifest_path = args.btc_adapter_dir / "RESULT_SUMMARY_MANIFEST.json"
    old_manifest = read_json(old_manifest_path)
    new_manifest = read_json(new_manifest_path)
    old_metrics = normalize_core_metrics(old_manifest.get("core_metrics") or {})
    new_metrics = normalize_core_metrics(new_manifest.get("core_metrics") or {})

    aggregate_rows = [metric_delta(metric, old_metrics.get(metric), new_metrics.get(metric)) for metric in METRICS]
    aggregate_csv = output_dir / "btc_completion_adapter_aggregate_delta.csv"
    write_csv(
        aggregate_csv,
        aggregate_rows,
        ["metric", "old_btc_baseline", "btc_v1_completion_adapter", "delta", "ratio_new_over_old"],
    )
    seed_block_rows = [
        metric_delta(metric, old_metrics.get(metric), new_metrics.get(metric)) for metric in SEED_BLOCK_METRICS
    ]
    seed_block_csv = output_dir / "btc_completion_adapter_seed_block_delta.csv"
    write_csv(
        seed_block_csv,
        seed_block_rows,
        ["metric", "old_btc_baseline", "btc_v1_completion_adapter", "delta", "ratio_new_over_old"],
    )

    old_by_day = {row["day"]: row for row in read_csv(args.old_baseline_dir / "summary_by_day.csv") if row.get("day")}
    new_by_day = {row["day"]: row for row in read_csv(args.btc_adapter_dir / "summary_by_day.csv") if row.get("day")}
    days = sorted(set(old_by_day) | set(new_by_day))
    daily_fields = [
        "day",
        "old_candidate_count",
        "new_candidate_count",
        "candidate_ratio",
        "old_seed_actions",
        "new_seed_actions",
        "seed_action_ratio",
        "old_pair_actions",
        "new_pair_actions",
        "pair_action_ratio",
        "old_gross_buy_cost",
        "new_gross_buy_cost",
        "gross_buy_cost_ratio",
        "old_pair_pnl",
        "new_pair_pnl",
        "pair_pnl_delta",
        "old_fee_after_pnl",
        "new_fee_after_pnl",
        "fee_after_pnl_delta",
        "old_day_roi",
        "new_day_roi",
        "day_roi_delta",
        "old_residual_cost_rate",
        "new_residual_cost_rate",
        "residual_cost_rate_delta",
    ]
    daily_rows: list[dict[str, Any]] = []
    for day in days:
        old = old_by_day.get(day, {})
        new = new_by_day.get(day, {})
        old_cost = to_float(old.get("gross_buy_cost"))
        new_cost = to_float(new.get("gross_buy_cost"))
        old_fee_after = to_float(old.get("fee_after_pnl"))
        new_fee_after = to_float(new.get("fee_after_pnl"))
        old_roi = safe_div(old_fee_after, old_cost)
        new_roi = safe_div(new_fee_after, new_cost)
        daily_rows.append(
            {
                "day": day,
                "old_candidate_count": old.get("candidate_count"),
                "new_candidate_count": new.get("candidate_count"),
                "candidate_ratio": rounded(safe_div(to_float(new.get("candidate_count")), to_float(old.get("candidate_count")))),
                "old_seed_actions": old.get("seed_actions"),
                "new_seed_actions": new.get("seed_actions"),
                "seed_action_ratio": rounded(safe_div(to_float(new.get("seed_actions")), to_float(old.get("seed_actions")))),
                "old_pair_actions": old.get("pair_actions"),
                "new_pair_actions": new.get("pair_actions"),
                "pair_action_ratio": rounded(safe_div(to_float(new.get("pair_actions")), to_float(old.get("pair_actions")))),
                "old_gross_buy_cost": old.get("gross_buy_cost"),
                "new_gross_buy_cost": new.get("gross_buy_cost"),
                "gross_buy_cost_ratio": rounded(safe_div(new_cost, old_cost)),
                "old_pair_pnl": old.get("pair_pnl"),
                "new_pair_pnl": new.get("pair_pnl"),
                "pair_pnl_delta": rounded((to_float(new.get("pair_pnl")) or 0.0) - (to_float(old.get("pair_pnl")) or 0.0)),
                "old_fee_after_pnl": old.get("fee_after_pnl"),
                "new_fee_after_pnl": new.get("fee_after_pnl"),
                "fee_after_pnl_delta": rounded((new_fee_after or 0.0) - (old_fee_after or 0.0)),
                "old_day_roi": rounded(old_roi),
                "new_day_roi": rounded(new_roi),
                "day_roi_delta": rounded((new_roi or 0.0) - (old_roi or 0.0)),
                "old_residual_cost_rate": old.get("cost_residual_rate"),
                "new_residual_cost_rate": new.get("cost_residual_rate"),
                "residual_cost_rate_delta": rounded(
                    (to_float(new.get("cost_residual_rate")) or 0.0)
                    - (to_float(old.get("cost_residual_rate")) or 0.0)
                ),
            }
        )
    daily_csv = output_dir / "btc_completion_adapter_daily_delta.csv"
    write_csv(daily_csv, daily_rows, daily_fields)

    gross_cost_ratio = safe_div(to_float(new_metrics.get("gross_buy_cost")), to_float(old_metrics.get("gross_buy_cost")))
    selected_ratio = safe_div(
        to_float(new_metrics.get("selected_candidate_count")), to_float(old_metrics.get("selected_candidate_count"))
    )
    pair_pnl_ratio = safe_div(to_float(new_metrics.get("pair_pnl")), to_float(old_metrics.get("pair_pnl")))
    fee_after_ratio = safe_div(to_float(new_metrics.get("fee_after_pnl")), to_float(old_metrics.get("fee_after_pnl")))
    roi_delta = (to_float(new_metrics.get("net_roi")) or 0.0) - (to_float(old_metrics.get("net_roi")) or 0.0)
    interpretation = {
        "primary_delta": "The BTC adapter trades a larger candidate/action set than the old baseline.",
        "volume": {
            "selected_action_ratio_new_over_old": rounded(selected_ratio),
            "gross_buy_cost_ratio_new_over_old": rounded(gross_cost_ratio),
        },
        "pnl": {
            "pair_pnl_ratio_new_over_old": rounded(pair_pnl_ratio),
            "fee_after_pnl_ratio_new_over_old": rounded(fee_after_ratio),
            "net_roi_delta": rounded(roi_delta),
        },
        "residual": {
            "old_residual_cost_rate": old_metrics.get("residual_cost_rate"),
            "new_residual_cost_rate": new_metrics.get("residual_cost_rate"),
            "residual_cost_rate_delta": rounded(
                (to_float(new_metrics.get("residual_cost_rate")) or 0.0)
                - (to_float(old_metrics.get("residual_cost_rate")) or 0.0)
            ),
        },
        "not_yet_explained": [
            "candidate source/event-generation semantic difference",
            "strict rescue close opportunity",
            "merge/redeem capital reuse",
            "owner private truth for future live execution only",
        ],
        "seed_block_delta": {
            "old_total_seed_blocks": int(
                sum(to_float(old_metrics.get(metric)) or 0.0 for metric in SEED_BLOCK_METRICS)
            ),
            "new_total_seed_blocks": int(
                sum(to_float(new_metrics.get(metric)) or 0.0 for metric in SEED_BLOCK_METRICS)
            ),
            "largest_new_blockers": sorted(
                [
                    {"metric": metric, "count": int(to_float(new_metrics.get(metric)) or 0.0)}
                    for metric in SEED_BLOCK_METRICS
                ],
                key=lambda item: item["count"],
                reverse=True,
            )[:3],
        },
    }
    manifest = {
        "schema_version": "btc_completion_adapter_delta_report_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_ADAPTER_DELTA_ATTRIBUTION_READY",
        "old_baseline_manifest": str(old_manifest_path),
        "btc_adapter_manifest": str(new_manifest_path),
        "old_status": old_manifest.get("status"),
        "btc_adapter_status": new_manifest.get("status"),
        "outputs": {
            "aggregate_delta_csv": str(aggregate_csv),
            "daily_delta_csv": str(daily_csv),
            "seed_block_delta_csv": str(seed_block_csv),
        },
        "interpretation": interpretation,
    }
    manifest_path = output_dir / "BTC_COMPLETION_ADAPTER_DELTA_REPORT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "outputs": manifest["outputs"], "interpretation": interpretation}, indent=2))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-baseline-dir", type=Path, default=DEFAULT_OLD_BASELINE)
    parser.add_argument("--btc-adapter-dir", type=Path, default=DEFAULT_BTC_ADAPTER)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_adapter_delta_latest",
    )
    args = parser.parse_args()
    args.old_baseline_dir = args.old_baseline_dir.expanduser()
    args.btc_adapter_dir = args.btc_adapter_dir.expanduser()
    args.output_dir = args.output_dir.expanduser()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

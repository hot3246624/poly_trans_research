#!/usr/bin/env python3
"""Build the xuan bridge scorecard for V1 vs BTC completion baseline."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from statistics import mean
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASELINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DEFAULT_OLD_L2_BRIDGE = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/xuan_old_baseline_l2_bridge_latest/XUAN_OLD_BASELINE_L2_BRIDGE_MANIFEST.json"
)
DEFAULT_OLD_PAIR_LOTS = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/xuan_old_baseline_pair_lots_latest/XUAN_OLD_BASELINE_PAIR_LOTS_MANIFEST.json"
)
DEFAULT_OLD_RESIDUAL_L2_RECOVERY = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/xuan_old_baseline_residual_l2_recovery_latest/"
    / "XUAN_OLD_BASELINE_RESIDUAL_L2_RECOVERY_MANIFEST.json"
)
DEFAULT_V1_COMPLETION_STATE_MACHINE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"
)
DEFAULT_BTC_RESCUE_LEDGER = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_rescue_adjusted_capital_ledger_latest/BTC_RESCUE_ADJUSTED_CAPITAL_LEDGER.json"
)
DEFAULT_BTC_MERGE_TURNOVER = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_merge_turnover_latest/BTC_MERGE_TURNOVER_REPORT.json"
)
DEFAULT_BTC_SOURCE_SEMANTICS = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_source_semantics_delta_latest/BTC_SOURCE_SEMANTICS_DELTA_REPORT.json"
)
DEFAULT_MULTIASSET_STRICT_RESCUE = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/multiasset_strict_rescue_opportunity_latest/"
    / "MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
)
DEFAULT_MULTIASSET_MERGE_TURNOVER = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json"
)
DEFAULT_L2_TOP_ALIGNED_MART = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)

SCORECARD_FIELDS = [
    "source",
    "bridge_category",
    "asset",
    "candidate_key",
    "queue_pnl",
    "pair_completion_pnl",
    "strict_rescue_pnl",
    "pair_pnl",
    "paired_mergeable_qty",
    "paired_mergeable_cost",
    "merge_recovered_capital",
    "filled_cost",
    "capital_turnover",
    "roi",
    "roi_per_1000_daily_estimate",
    "residual_cost",
    "residual_qty",
    "market_end_residual_cost",
    "market_end_residual_qty",
    "residual_cost_share",
    "residual_qty_share",
    "actual_settlement_residual_pnl",
    "mature_after_fee_mark_value",
    "marked_pnl_after_residual",
    "zero_stress_residual_loss",
    "mature_after_fee_recovery",
    "dynamic_break_even_recovery",
    "dynamic_research_recovery",
    "residual_age_bucket",
    "recoverable_residual_qty",
    "bad_tail_residual_qty",
    "source_block_count",
    "latency_source_age_stats_json",
    "status",
    "missing_bridge_fields",
]


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


def safe_div(num: Any, den: Any) -> float | None:
    num_f = to_float(num)
    den_f = to_float(den)
    if num_f is None or den_f in (None, 0.0):
        return None
    return num_f / den_f


def residual_stats(rows: list[dict[str, str]]) -> dict[str, Any]:
    costs = [to_float(row.get("cost")) or 0.0 for row in rows]
    qtys = [to_float(row.get("qty")) or 0.0 for row in rows]
    payouts = [to_float(row.get("payout")) or 0.0 for row in rows]
    pnls = [to_float(row.get("pnl")) or 0.0 for row in rows]
    ages = [value for value in (to_float(row.get("age_s")) for row in rows) if value is not None]
    total_cost = sum(costs)
    total_qty = sum(qtys)
    total_payout = sum(payouts)
    zero_stress_loss = sum(min(0.0, pnl) for pnl in pnls)
    recoverable_qty = sum((to_float(row.get("qty")) or 0.0) for row in rows if (to_float(row.get("payout")) or 0.0) > 0)
    bad_tail_qty = sum((to_float(row.get("qty")) or 0.0) for row in rows if (to_float(row.get("payout")) or 0.0) <= 0)
    age_buckets = {
        "lt_30s": sum(1 for age in ages if age < 30),
        "30_120s": sum(1 for age in ages if 30 <= age < 120),
        "120_300s": sum(1 for age in ages if 120 <= age < 300),
        "gte_300s": sum(1 for age in ages if age >= 300),
    }
    return {
        "residual_lot_count": len(rows),
        "residual_cost": rounded(total_cost),
        "residual_qty": rounded(total_qty),
        "zero_stress_residual_loss": rounded(zero_stress_loss),
        "mature_after_fee_recovery": rounded(total_payout / total_cost) if total_cost else None,
        "dynamic_break_even_recovery": rounded(total_cost / total_qty) if total_qty else None,
        "recoverable_residual_qty": rounded(recoverable_qty),
        "bad_tail_residual_qty": rounded(bad_tail_qty),
        "residual_age_bucket": json.dumps(age_buckets, sort_keys=True),
        "age_stats": {
            "count": len(ages),
            "avg": rounded(mean(ages)) if ages else None,
            "min": rounded(min(ages)) if ages else None,
            "max": rounded(max(ages)) if ages else None,
        },
    }


def residual_stats_by_day(rows: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        grouped.setdefault(str(row.get("day") or ""), []).append(row)
    return {day: residual_stats(day_rows) for day, day_rows in grouped.items() if day}


def source_block_count(metrics: dict[str, Any]) -> int:
    total = 0
    for key, value in metrics.items():
        if key.startswith("seed_block_"):
            try:
                total += int(value)
            except (TypeError, ValueError):
                pass
    return total


def bridge_category(source: str) -> str:
    if "audit_pack_search_safe_screener" in source:
        return "queue_screener_search_safe"
    if "completion_residual_state_machine_adapter" in source:
        return "completion_adapter_research"
    return "xuan_compatible_bridge"


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    data_root = args.data_root
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    old_manifest_path = args.old_baseline_dir / "RESULT_SUMMARY_MANIFEST.json"
    old_manifest = read_json(old_manifest_path)
    old_metrics = old_manifest.get("core_metrics") or {}
    old_residual_rows = read_csv(args.old_baseline_dir / "residual_lots.csv")
    old_residual = residual_stats(old_residual_rows)
    old_residual_by_day = residual_stats_by_day(old_residual_rows)
    old_summary_rows = read_csv(args.old_baseline_dir / "summary_by_day.csv")
    old_l2_bridge = read_json(args.old_l2_bridge_manifest) if args.old_l2_bridge_manifest.exists() else {}
    old_pair_lots = read_json(args.old_pair_lots_manifest) if args.old_pair_lots_manifest.exists() else {}
    old_residual_l2_recovery = (
        read_json(args.old_residual_l2_recovery_manifest)
        if args.old_residual_l2_recovery_manifest.exists()
        else {}
    )
    old_l2_summary = old_l2_bridge.get("summary") or {}
    old_recovery_summary = old_residual_l2_recovery.get("summary") or {}
    old_l2_bridge_by_day_rows = read_csv(Path((old_l2_bridge.get("outputs") or {}).get("summary_by_day_csv") or ""))
    old_l2_bridge_by_day = {str(row.get("day")): row for row in old_l2_bridge_by_day_rows if row.get("day")}
    old_recovery_by_day_rows = read_csv(
        Path((old_residual_l2_recovery.get("outputs") or {}).get("summary_by_day_csv") or "")
    )
    old_recovery_by_day = {str(row.get("day")): row for row in old_recovery_by_day_rows if row.get("day")}
    v1_sm_manifest_path = args.v1_completion_state_machine_dir / "RESULT_SUMMARY_MANIFEST.json"
    v1_sm_manifest = read_json(v1_sm_manifest_path)
    v1_sm_metrics = v1_sm_manifest.get("core_metrics") or {}
    v1_sm_residual_rows = read_csv(args.v1_completion_state_machine_dir / "residual_lots.csv")
    v1_sm_residual = residual_stats(v1_sm_residual_rows)
    v1_sm_residual_by_day = residual_stats_by_day(v1_sm_residual_rows)
    v1_sm_summary_rows = read_csv(args.v1_completion_state_machine_dir / "summary_by_day.csv")
    btc_rescue_ledger = read_json(args.btc_rescue_ledger) if args.btc_rescue_ledger.exists() else {}
    btc_rescue_scenarios = btc_rescue_ledger.get("scenarios") or {}
    btc_rescue_all = btc_rescue_scenarios.get("strict_rescue_all_best_quote") or {}
    btc_merge_turnover = read_json(args.btc_merge_turnover_report) if args.btc_merge_turnover_report.exists() else {}
    btc_merge_metrics = btc_merge_turnover.get("metrics") or {}
    btc_source_semantics = read_json(args.btc_source_semantics_report) if args.btc_source_semantics_report.exists() else {}
    multiasset_strict_rescue = (
        read_json(args.multiasset_strict_rescue_report) if args.multiasset_strict_rescue_report.exists() else {}
    )
    multiasset_strict_rescue_summary = multiasset_strict_rescue.get("summary") or {}
    multiasset_merge_turnover = (
        read_json(args.multiasset_merge_turnover_report) if args.multiasset_merge_turnover_report.exists() else {}
    )
    multiasset_merge_metrics = multiasset_merge_turnover.get("metrics") or {}
    l2_top_aligned = read_json(args.l2_top_aligned_mart_manifest) if args.l2_top_aligned_mart_manifest.exists() else {}
    l2_top_aligned_contract = {
        "contract_name": "md_book_l2_top_aligned",
        "top_source": "md_book_l1 canonical top",
        "depth_source": "latest md_book_l2 side snapshot at or before L1 capture sequence",
        "raw_md_book_l2_is_top_of_book_contract": False,
        "accepted_for_v1_l2_evidence": l2_top_aligned.get("status") == "OK",
        "status": "OK" if l2_top_aligned.get("status") == "OK" else "NOT_READY",
    }
    latency_stats = {
        "old_offset_distribution": old_metrics.get("offset_s_distribution") or {},
        "old_action_l2_bridge": old_l2_summary,
    }
    old_score = {
        "source": "old_btc_completion_residual_baseline",
        "asset": "BTC",
        "candidate_key": "old_baseline_aggregate",
        "queue_pnl": "",
        "pair_completion_pnl": old_metrics.get("pair_pnl"),
        "strict_rescue_pnl": "",
        "pair_pnl": old_metrics.get("pair_pnl"),
        "paired_mergeable_qty": old_metrics.get("pair_qty"),
        "paired_mergeable_cost": (
            rounded((to_float(old_metrics.get("pair_qty")) or 0.0) - (to_float(old_metrics.get("pair_pnl")) or 0.0))
            if old_metrics.get("pair_qty") not in (None, "")
            else ""
        ),
        "merge_recovered_capital": old_metrics.get("pair_qty"),
        "filled_cost": old_metrics.get("gross_buy_cost"),
        "capital_turnover": old_metrics.get("rounds_per_market"),
        "roi": old_metrics.get("net_roi"),
        "roi_per_1000_daily_estimate": "",
        "residual_cost": old_metrics.get("residual_cost"),
        "residual_qty": old_metrics.get("residual_qty"),
        "market_end_residual_cost": old_metrics.get("residual_cost"),
        "market_end_residual_qty": old_metrics.get("residual_qty"),
        "residual_cost_share": old_metrics.get("residual_cost_rate") or old_metrics.get("cost_residual_rate"),
        "residual_qty_share": old_metrics.get("residual_qty_rate") or old_metrics.get("qty_residual_rate"),
        "actual_settlement_residual_pnl": old_metrics.get("residual_settle_pnl"),
        "mature_after_fee_mark_value": old_recovery_summary.get("best_after_fee_recovery_value"),
        "marked_pnl_after_residual": old_metrics.get("fee_after_pnl"),
        "zero_stress_residual_loss": old_residual.get("zero_stress_residual_loss"),
        "mature_after_fee_recovery": old_residual.get("mature_after_fee_recovery"),
        "dynamic_break_even_recovery": old_residual.get("dynamic_break_even_recovery"),
        "dynamic_research_recovery": old_recovery_summary.get("best_after_fee_recovery_ratio"),
        "residual_age_bucket": old_residual.get("residual_age_bucket"),
        "recoverable_residual_qty": old_residual.get("recoverable_residual_qty"),
        "bad_tail_residual_qty": old_residual.get("bad_tail_residual_qty"),
        "source_block_count": source_block_count(old_metrics),
        "latency_source_age_stats_json": json.dumps(latency_stats, sort_keys=True),
        "status": (
            "OLD_BASELINE_L2_BRIDGED_RESEARCH_ONLY_NOT_PRIVATE_TRUTH"
            if old_l2_bridge.get("status") == "OK_OLD_BASELINE_L2_BRIDGE_READY"
            else "OLD_BASELINE_AVAILABLE_RESEARCH_ONLY_NOT_PRIVATE_TRUTH"
        ),
        "missing_bridge_fields": "strict_rescue_pnl,roi_per_1000_daily_estimate",
    }

    default_l2_audit_csv = (
        data_root
        / "derived/contract_examples/backtest_candidate_audit_pack_with_l2_evidence_latest/backtest_candidate_audit_pack.csv"
    )
    fallback_audit_csv = data_root / "derived/contract_examples/backtest_candidate_audit_pack_latest/backtest_candidate_audit_pack.csv"
    old_day_scores: list[dict[str, Any]] = []
    for row in old_summary_rows:
        day = str(row.get("day") or "")
        day_residual = old_residual_by_day.get(day, {})
        day_l2 = old_l2_bridge_by_day.get(day, {})
        day_recovery = old_recovery_by_day.get(day, {})
        day_roi = safe_div(row.get("fee_after_pnl"), row.get("gross_buy_cost"))
        old_day_scores.append(
            {
                "source": "old_btc_completion_residual_baseline_by_day",
                "asset": "BTC",
                "candidate_key": f"old_baseline_day_{day}",
                "queue_pnl": "",
                "pair_completion_pnl": row.get("pair_pnl"),
                "strict_rescue_pnl": "",
                "pair_pnl": row.get("pair_pnl"),
                "paired_mergeable_qty": row.get("pair_qty"),
                "paired_mergeable_cost": rounded((to_float(row.get("pair_qty")) or 0.0) - (to_float(row.get("pair_pnl")) or 0.0))
                if row.get("pair_qty") not in (None, "")
                else "",
                "merge_recovered_capital": row.get("pair_qty"),
                "filled_cost": row.get("gross_buy_cost"),
                "capital_turnover": safe_div(row.get("pair_actions"), row.get("active_markets")),
                "roi": rounded(day_roi),
                "roi_per_1000_daily_estimate": rounded(day_roi * 1000.0) if day_roi is not None else "",
                "residual_cost": row.get("residual_cost"),
                "residual_qty": row.get("residual_qty"),
                "market_end_residual_cost": row.get("residual_cost"),
                "market_end_residual_qty": row.get("residual_qty"),
                "residual_cost_share": row.get("cost_residual_rate"),
                "residual_qty_share": row.get("qty_residual_rate"),
                "actual_settlement_residual_pnl": row.get("residual_settle_pnl"),
                "mature_after_fee_mark_value": day_recovery.get("best_after_fee_recovery_value"),
                "marked_pnl_after_residual": row.get("fee_after_pnl"),
                "zero_stress_residual_loss": day_residual.get("zero_stress_residual_loss"),
                "mature_after_fee_recovery": day_residual.get("mature_after_fee_recovery"),
                "dynamic_break_even_recovery": day_residual.get("dynamic_break_even_recovery"),
                "dynamic_research_recovery": day_recovery.get("best_after_fee_recovery_ratio"),
                "residual_age_bucket": day_residual.get("residual_age_bucket"),
                "recoverable_residual_qty": day_residual.get("recoverable_residual_qty"),
                "bad_tail_residual_qty": day_residual.get("bad_tail_residual_qty"),
                "source_block_count": "",
                "latency_source_age_stats_json": json.dumps(day_l2, sort_keys=True),
                "status": (
                    "OLD_BASELINE_DAY_L2_BRIDGED_RESEARCH_ONLY_NOT_PRIVATE_TRUTH"
                    if day_l2
                    else "OLD_BASELINE_DAY_AVAILABLE_RESEARCH_ONLY_NOT_PRIVATE_TRUTH"
                ),
                "missing_bridge_fields": "strict_rescue_pnl",
            }
        )

    audit_csv = args.v1_audit_csv or (default_l2_audit_csv if default_l2_audit_csv.exists() else fallback_audit_csv)
    audit_rows = read_csv(audit_csv)
    v1_sm_scores: list[dict[str, Any]] = []
    if v1_sm_manifest:
        v1_sm_roi = safe_div(v1_sm_metrics.get("fee_after_pnl"), v1_sm_metrics.get("gross_buy_cost"))
        v1_sm_scores.append(
            {
                "source": "multiasset_v1_completion_residual_state_machine_adapter_from_l1_flow",
                "asset": "MULTI",
                "candidate_key": "v1_completion_adapter_aggregate",
                "queue_pnl": "",
                "pair_completion_pnl": v1_sm_metrics.get("pair_pnl"),
                "strict_rescue_pnl": multiasset_merge_metrics.get("strict_rescue_pnl")
                or multiasset_strict_rescue_summary.get("best_after_fee_rescue_pnl"),
                "pair_pnl": v1_sm_metrics.get("pair_pnl"),
                "paired_mergeable_qty": multiasset_merge_metrics.get("paired_mergeable_qty") or v1_sm_metrics.get("pair_qty"),
                "paired_mergeable_cost": multiasset_merge_metrics.get("paired_mergeable_cost"),
                "merge_recovered_capital": multiasset_merge_metrics.get("merge_recovered_capital") or v1_sm_metrics.get("pair_qty"),
                "filled_cost": v1_sm_metrics.get("gross_buy_cost"),
                "capital_turnover": multiasset_merge_metrics.get("capital_turnover") or v1_sm_metrics.get("rounds_per_market"),
                "roi": rounded(v1_sm_roi),
                "roi_per_1000_daily_estimate": rounded(v1_sm_roi * 1000.0) if v1_sm_roi is not None else "",
                "residual_cost": v1_sm_metrics.get("residual_cost"),
                "residual_qty": v1_sm_metrics.get("residual_qty"),
                "market_end_residual_cost": multiasset_merge_metrics.get("market_end_residual_cost")
                or v1_sm_metrics.get("residual_cost"),
                "market_end_residual_qty": multiasset_merge_metrics.get("market_end_residual_qty")
                or v1_sm_metrics.get("residual_qty"),
                "residual_cost_share": v1_sm_metrics.get("residual_cost_rate") or v1_sm_metrics.get("cost_residual_rate"),
                "residual_qty_share": v1_sm_metrics.get("residual_qty_rate") or v1_sm_metrics.get("qty_residual_rate"),
                "actual_settlement_residual_pnl": multiasset_merge_metrics.get("actual_settlement_residual_pnl")
                or v1_sm_metrics.get("residual_settle_pnl"),
                "mature_after_fee_mark_value": v1_sm_metrics.get("residual_settle_payout"),
                "marked_pnl_after_residual": v1_sm_metrics.get("fee_after_pnl"),
                "zero_stress_residual_loss": v1_sm_residual.get("zero_stress_residual_loss"),
                "mature_after_fee_recovery": v1_sm_residual.get("mature_after_fee_recovery"),
                "dynamic_break_even_recovery": v1_sm_residual.get("dynamic_break_even_recovery"),
                "dynamic_research_recovery": safe_div(
                    v1_sm_metrics.get("residual_settle_payout"), v1_sm_metrics.get("residual_cost")
                ),
                "residual_age_bucket": v1_sm_residual.get("residual_age_bucket"),
                "recoverable_residual_qty": v1_sm_residual.get("recoverable_residual_qty"),
                "bad_tail_residual_qty": v1_sm_residual.get("bad_tail_residual_qty"),
                "source_block_count": source_block_count(v1_sm_metrics),
                "latency_source_age_stats_json": json.dumps(
                    {
                        "offset_distribution": v1_sm_metrics.get("offset_s_distribution") or {},
                        "seed_px_distribution": v1_sm_metrics.get("seed_px_distribution") or {},
                    },
                    sort_keys=True,
                ),
                "status": v1_sm_manifest.get("status"),
                "missing_bridge_fields": "owner_private_truth,source_semantics_parity",
            }
        )
        for row in v1_sm_summary_rows:
            day = str(row.get("day") or "")
            day_residual = v1_sm_residual_by_day.get(day, {})
            day_roi = safe_div(row.get("fee_after_pnl"), row.get("gross_buy_cost"))
            v1_sm_scores.append(
                {
                    "source": "multiasset_v1_completion_residual_state_machine_adapter_by_day",
                    "asset": "MULTI",
                    "candidate_key": f"v1_completion_adapter_day_{day}",
                    "queue_pnl": "",
                    "pair_completion_pnl": row.get("pair_pnl"),
                    "strict_rescue_pnl": "",
                    "pair_pnl": row.get("pair_pnl"),
                    "paired_mergeable_qty": row.get("pair_qty"),
                    "paired_mergeable_cost": rounded(
                        (to_float(row.get("pair_qty")) or 0.0) - (to_float(row.get("pair_pnl")) or 0.0)
                    )
                    if row.get("pair_qty") not in (None, "")
                    else "",
                    "merge_recovered_capital": row.get("pair_qty"),
                    "filled_cost": row.get("gross_buy_cost"),
                    "capital_turnover": safe_div(row.get("pair_actions"), row.get("active_markets")),
                    "roi": rounded(day_roi),
                    "roi_per_1000_daily_estimate": rounded(day_roi * 1000.0) if day_roi is not None else "",
                    "residual_cost": row.get("residual_cost"),
                    "residual_qty": row.get("residual_qty"),
                    "market_end_residual_cost": row.get("residual_cost"),
                    "market_end_residual_qty": row.get("residual_qty"),
                    "residual_cost_share": row.get("cost_residual_rate"),
                    "residual_qty_share": row.get("qty_residual_rate"),
                    "actual_settlement_residual_pnl": row.get("residual_settle_pnl"),
                    "mature_after_fee_mark_value": row.get("actual_settle_pnl"),
                    "marked_pnl_after_residual": row.get("fee_after_pnl"),
                    "zero_stress_residual_loss": day_residual.get("zero_stress_residual_loss"),
                    "mature_after_fee_recovery": day_residual.get("mature_after_fee_recovery"),
                    "dynamic_break_even_recovery": day_residual.get("dynamic_break_even_recovery"),
                    "dynamic_research_recovery": safe_div(row.get("actual_settle_pnl"), row.get("residual_cost")),
                    "residual_age_bucket": day_residual.get("residual_age_bucket"),
                    "recoverable_residual_qty": day_residual.get("recoverable_residual_qty"),
                    "bad_tail_residual_qty": day_residual.get("bad_tail_residual_qty"),
                    "source_block_count": "",
                    "latency_source_age_stats_json": "",
                    "status": "PASS_LOCAL_COMPLETION_RESEARCH_ONLY",
                    "missing_bridge_fields": "strict_rescue_pnl,merge_capital_reuse,owner_private_truth",
                }
            )
    btc_bridge_scores: list[dict[str, Any]] = []
    if btc_rescue_ledger:
        btc_bridge_scores.append(
            {
                "source": "btc_v1_rescue_adjusted_capital_ledger",
                "asset": "BTC",
                "candidate_key": "btc_rescue_all_best_quote_research_scenario",
                "queue_pnl": "",
                "pair_completion_pnl": btc_rescue_all.get("pair_pnl"),
                "strict_rescue_pnl": btc_rescue_all.get("incremental_fee_after_pnl_vs_baseline"),
                "pair_pnl": btc_rescue_all.get("pair_pnl"),
                "paired_mergeable_qty": btc_merge_metrics.get("paired_mergeable_qty") or btc_merge_metrics.get("pair_qty"),
                "paired_mergeable_cost": btc_merge_metrics.get("paired_mergeable_cost") or btc_merge_metrics.get("pair_cost_sum"),
                "merge_recovered_capital": btc_merge_metrics.get("merge_recovered_capital")
                or btc_merge_metrics.get("pair_merge_redeem_value"),
                "filled_cost": btc_merge_metrics.get("gross_buy_cost"),
                "capital_turnover": btc_merge_metrics.get("capital_return_strict_rescue_over_gross_cost"),
                "roi": btc_rescue_all.get("net_roi"),
                "roi_per_1000_daily_estimate": (
                    round(float(btc_rescue_all.get("net_roi")) * 1000.0, 6)
                    if btc_rescue_all.get("net_roi") not in (None, "")
                    else ""
                ),
                "residual_cost": btc_merge_metrics.get("residual_cost"),
                "residual_qty": btc_merge_metrics.get("residual_qty"),
                "market_end_residual_cost": btc_merge_metrics.get("market_end_residual_cost")
                or btc_merge_metrics.get("residual_cost"),
                "market_end_residual_qty": btc_merge_metrics.get("market_end_residual_qty")
                or btc_merge_metrics.get("residual_qty"),
                "residual_cost_share": btc_merge_metrics.get("residual_cost_share"),
                "residual_qty_share": "",
                "actual_settlement_residual_pnl": btc_merge_metrics.get("actual_settlement_residual_pnl"),
                "mature_after_fee_mark_value": "",
                "marked_pnl_after_residual": btc_rescue_all.get("fee_after_pnl"),
                "zero_stress_residual_loss": "",
                "mature_after_fee_recovery": "",
                "dynamic_break_even_recovery": "",
                "dynamic_research_recovery": btc_merge_metrics.get("capital_return_strict_rescue_over_gross_cost"),
                "residual_age_bucket": "",
                "recoverable_residual_qty": "",
                "bad_tail_residual_qty": "",
                "source_block_count": "",
                "latency_source_age_stats_json": json.dumps(
                    {
                        "source_semantics": btc_source_semantics.get("summary") or {},
                        "merge_turnover": btc_merge_metrics,
                    },
                    sort_keys=True,
                ),
                "status": "BTC_RESCUE_MERGE_RESEARCH_READY_NOT_PARITY",
                "missing_bridge_fields": "owner_private_truth,source_taker_side_normalization",
            }
        )
    v1_scores: list[dict[str, Any]] = []
    for row in audit_rows:
        missing = [
            "pair_completion_pnl",
            "strict_rescue_pnl",
            "pair_pnl",
            "filled_cost",
            "capital_turnover",
            "ROI",
            "residual_FIFO",
            "mature_after_fee_mark_value",
            "merge_capital_reuse",
            "latency/source_age_stats",
        ]
        v1_scores.append(
            {
                "source": "multiasset_v1_audit_pack_search_safe_screener",
                "asset": row.get("asset"),
                "candidate_key": row.get("candidate_key"),
                "queue_pnl": row.get("best_queue_pnl"),
                "pair_completion_pnl": "",
                "strict_rescue_pnl": "",
                "pair_pnl": "",
                "paired_mergeable_qty": "",
                "paired_mergeable_cost": "",
                "merge_recovered_capital": "",
                "filled_cost": "",
                "capital_turnover": "",
                "roi": "",
                "roi_per_1000_daily_estimate": "",
                "residual_cost": "",
                "residual_qty": "",
                "market_end_residual_cost": "",
                "market_end_residual_qty": "",
                "residual_cost_share": "",
                "residual_qty_share": "",
                "actual_settlement_residual_pnl": "",
                "mature_after_fee_mark_value": "",
                "marked_pnl_after_residual": "",
                "zero_stress_residual_loss": "",
                "mature_after_fee_recovery": "",
                "dynamic_break_even_recovery": "",
                "dynamic_research_recovery": "",
                "residual_age_bucket": "",
                "recoverable_residual_qty": "",
                "bad_tail_residual_qty": "",
                "source_block_count": "",
                "latency_source_age_stats_json": "",
                "status": "BLOCKED_XUAN_BRIDGE_FIELDS_MISSING",
                "missing_bridge_fields": ",".join(missing),
            }
        )

    scorecard_rows = [old_score] + old_day_scores + v1_sm_scores + btc_bridge_scores + v1_scores
    for row in scorecard_rows:
        row.setdefault("bridge_category", bridge_category(str(row.get("source") or "")))
        for field in SCORECARD_FIELDS:
            row.setdefault(field, "")
    category_counts: dict[str, int] = {}
    for row in scorecard_rows:
        category = str(row.get("bridge_category") or "uncategorized")
        category_counts[category] = category_counts.get(category, 0) + 1
    scorecard_csv = output_dir / "xuan_bridge_scorecard.csv"
    write_csv(scorecard_csv, scorecard_rows, SCORECARD_FIELDS)

    missing_rows = [
        {
            "field": "queue_pnl",
            "old_baseline": "not primary output",
            "multiasset_v1": "present",
            "unlock": "Keep as screener metric only.",
        },
        {
            "field": "pair_completion_pnl",
            "old_baseline": "present as pair_pnl aggregate",
            "multiasset_v1": "present in completion adapter if RESULT_SUMMARY_MANIFEST is available",
            "unlock": "Run BTC-only same-window parity and explain remaining deltas.",
        },
        {
            "field": "strict_rescue_pnl",
            "old_baseline": "not isolated",
            "multiasset_v1": "present as a multiasset top-aligned L2 research scenario when MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT is OK",
            "unlock": "Promote only after source semantics/parity and owner private truth boundaries are accepted.",
        },
        {
            "field": "residual_dynamic_recovery",
            "old_baseline": "partial via settlement residual lots",
            "multiasset_v1": "present in completion adapter as FIFO residual and settlement recovery",
            "unlock": "Add L2 mature mark curve and recoverable/bad-tail split by source age.",
        },
        {
            "field": "merge_capital_turnover",
            "old_baseline": "partial rounds_per_market",
            "multiasset_v1": "present as paired_mergeable/merge_recovered_capital/market_end_residual split when MULTIASSET_MERGE_TURNOVER_REPORT is OK",
            "unlock": "Keep merge capital reuse separate from residual settlement attribution.",
        },
    ]
    missing_csv = output_dir / "xuan_bridge_missing_fields.csv"
    write_csv(missing_csv, missing_rows, ["field", "old_baseline", "multiasset_v1", "unlock"])

    summary = {
        "scorecard_row_count": len(scorecard_rows),
        "old_baseline_day_score_count": len(old_day_scores),
        "v1_completion_state_machine_score_count": len(v1_sm_scores),
        "v1_audit_screener_score_count": len(v1_scores),
        "btc_bridge_score_count": len(btc_bridge_scores),
        "old_baseline_l2_bridge_status": old_l2_bridge.get("status") if old_l2_bridge else "MISSING",
        "old_baseline_pair_lots_status": old_pair_lots.get("status") if old_pair_lots else "MISSING",
        "old_baseline_residual_l2_recovery_status": (
            old_residual_l2_recovery.get("status") if old_residual_l2_recovery else "MISSING"
        ),
        "v1_completion_state_machine_status": v1_sm_manifest.get("status") if v1_sm_manifest else "MISSING",
        "btc_rescue_adjusted_capital_ledger_status": (
            btc_rescue_ledger.get("status") if btc_rescue_ledger else "MISSING"
        ),
        "btc_merge_turnover_status": btc_merge_turnover.get("status") if btc_merge_turnover else "MISSING",
        "btc_source_semantics_delta_status": (
            btc_source_semantics.get("status") if btc_source_semantics else "MISSING"
        ),
        "multiasset_strict_rescue_status": (
            multiasset_strict_rescue.get("status") if multiasset_strict_rescue else "MISSING"
        ),
        "multiasset_merge_turnover_status": (
            multiasset_merge_turnover.get("status") if multiasset_merge_turnover else "MISSING"
        ),
        "queue_screener_search_safe_count": category_counts.get("queue_screener_search_safe", 0),
        "completion_adapter_research_count": category_counts.get("completion_adapter_research", 0),
        "xuan_compatible_bridge_count": category_counts.get("xuan_compatible_bridge", 0),
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
        "queue_pnl_is_strategy_pnl": False,
    }

    manifest = {
        "schema_version": "xuan_bridge_scorecard_v1",
        "created_utc": utc_now(),
        "status": (
            "PARTIAL_XUAN_BRIDGE_COMPLETION_ADAPTER_READY"
            if v1_sm_manifest.get("status") == "PASS_LOCAL_COMPLETION_RESEARCH_ONLY"
            else "BLOCKED_XUAN_BRIDGE_NOT_COMPLETE"
        ),
        "data_root": str(data_root),
        "output_dir": str(output_dir),
        "summary": summary,
        "bridge_categories": {
            "queue_screener_search_safe": "Search-safe queue candidates only; not xuan strategy PnL.",
            "completion_adapter_research": "Pair/residual state-machine research rows; useful for bridge analysis but not parity/promotion.",
            "xuan_compatible_bridge": "Rows that are closest to the xuan completion/residual audit frame; still historical public/proxy unless private truth exists.",
            "counts": category_counts,
        },
        "l2_top_aligned_contract": l2_top_aligned_contract,
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
        "outputs": {
            "scorecard_csv": str(scorecard_csv),
            "missing_fields_csv": str(missing_csv),
        },
        "old_baseline_manifest": str(old_manifest_path),
        "old_baseline_l2_bridge_manifest": str(args.old_l2_bridge_manifest),
        "old_baseline_l2_bridge_status": old_l2_bridge.get("status") if old_l2_bridge else "MISSING",
        "old_baseline_l2_bridge_summary": old_l2_summary,
        "old_baseline_pair_lots_manifest": str(args.old_pair_lots_manifest),
        "old_baseline_pair_lots_status": old_pair_lots.get("status") if old_pair_lots else "MISSING",
        "old_baseline_pair_lots_metrics": old_pair_lots.get("metrics") if old_pair_lots else {},
        "old_baseline_residual_l2_recovery_manifest": str(args.old_residual_l2_recovery_manifest),
        "old_baseline_residual_l2_recovery_status": (
            old_residual_l2_recovery.get("status") if old_residual_l2_recovery else "MISSING"
        ),
        "old_baseline_residual_l2_recovery_summary": old_recovery_summary,
        "v1_completion_state_machine_manifest": str(v1_sm_manifest_path),
        "v1_completion_state_machine_status": v1_sm_manifest.get("status") if v1_sm_manifest else "MISSING",
        "v1_completion_state_machine_core_metrics": v1_sm_metrics,
        "v1_completion_state_machine_score_count": len(v1_sm_scores),
        "btc_rescue_adjusted_capital_ledger": {
            "manifest": str(args.btc_rescue_ledger),
            "status": btc_rescue_ledger.get("status") if btc_rescue_ledger else "MISSING",
            "scenarios": btc_rescue_scenarios,
        },
        "btc_merge_turnover_report": {
            "manifest": str(args.btc_merge_turnover_report),
            "status": btc_merge_turnover.get("status") if btc_merge_turnover else "MISSING",
            "metrics": btc_merge_metrics,
        },
        "btc_source_semantics_delta_report": {
            "manifest": str(args.btc_source_semantics_report),
            "status": btc_source_semantics.get("status") if btc_source_semantics else "MISSING",
            "summary": btc_source_semantics.get("summary") if btc_source_semantics else {},
        },
        "multiasset_strict_rescue_opportunity_report": {
            "manifest": str(args.multiasset_strict_rescue_report),
            "status": multiasset_strict_rescue.get("status") if multiasset_strict_rescue else "MISSING",
            "summary": multiasset_strict_rescue_summary,
        },
        "multiasset_merge_turnover_report": {
            "manifest": str(args.multiasset_merge_turnover_report),
            "status": multiasset_merge_turnover.get("status") if multiasset_merge_turnover else "MISSING",
            "metrics": multiasset_merge_metrics,
        },
        "btc_bridge_score_count": len(btc_bridge_scores),
        "v1_audit_csv": str(audit_csv),
        "v1_audit_source": "l2_top_aligned_audit_pack" if audit_csv == default_l2_audit_csv else "search_safe_audit_pack",
        "old_baseline_residual_stats": old_residual,
        "old_baseline_day_score_count": len(old_day_scores),
        "scorecard_row_count": len(scorecard_rows),
        "interpretation": {
            "v1_negative_queue_pnl": "Negative V1 queue PnL only rejects the current queue-only screener candidates; it is not evidence that the old xuan completion/residual strategy is dead.",
            "private_truth_policy": "Historical shadow/no-order remains public/proxy evidence only; private_promotion_ready must stay false.",
        },
        "anchor_windows_required": [
            "20260522T1705 old_best",
            "20260525T2041 cap25_high_roi",
            "20260526T1757 capped_cap25",
            "20260527T0407 tail_mark_snapshot",
        ],
    }
    manifest_path = output_dir / "XUAN_BRIDGE_SCORECARD_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: manifest[k] for k in ("status", "summary", "outputs", "scorecard_row_count", "interpretation")},
            indent=2,
            sort_keys=True,
        )
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--old-baseline-dir", type=Path, default=DEFAULT_OLD_BASELINE)
    parser.add_argument("--old-l2-bridge-manifest", type=Path, default=DEFAULT_OLD_L2_BRIDGE)
    parser.add_argument("--old-pair-lots-manifest", type=Path, default=DEFAULT_OLD_PAIR_LOTS)
    parser.add_argument("--old-residual-l2-recovery-manifest", type=Path, default=DEFAULT_OLD_RESIDUAL_L2_RECOVERY)
    parser.add_argument("--v1-completion-state-machine-dir", type=Path, default=DEFAULT_V1_COMPLETION_STATE_MACHINE)
    parser.add_argument("--btc-rescue-ledger", type=Path, default=DEFAULT_BTC_RESCUE_LEDGER)
    parser.add_argument("--btc-merge-turnover-report", type=Path, default=DEFAULT_BTC_MERGE_TURNOVER)
    parser.add_argument("--btc-source-semantics-report", type=Path, default=DEFAULT_BTC_SOURCE_SEMANTICS)
    parser.add_argument("--multiasset-strict-rescue-report", type=Path, default=DEFAULT_MULTIASSET_STRICT_RESCUE)
    parser.add_argument("--multiasset-merge-turnover-report", type=Path, default=DEFAULT_MULTIASSET_MERGE_TURNOVER)
    parser.add_argument("--l2-top-aligned-mart-manifest", type=Path, default=DEFAULT_L2_TOP_ALIGNED_MART)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--v1-audit-csv", type=Path)
    args = parser.parse_args()
    args.data_root = args.data_root.expanduser()
    args.old_baseline_dir = args.old_baseline_dir.expanduser()
    args.old_l2_bridge_manifest = args.old_l2_bridge_manifest.expanduser()
    args.old_pair_lots_manifest = args.old_pair_lots_manifest.expanduser()
    args.old_residual_l2_recovery_manifest = args.old_residual_l2_recovery_manifest.expanduser()
    args.v1_completion_state_machine_dir = args.v1_completion_state_machine_dir.expanduser()
    args.btc_rescue_ledger = args.btc_rescue_ledger.expanduser()
    args.btc_merge_turnover_report = args.btc_merge_turnover_report.expanduser()
    args.btc_source_semantics_report = args.btc_source_semantics_report.expanduser()
    args.multiasset_strict_rescue_report = args.multiasset_strict_rescue_report.expanduser()
    args.multiasset_merge_turnover_report = args.multiasset_merge_turnover_report.expanduser()
    args.l2_top_aligned_mart_manifest = args.l2_top_aligned_mart_manifest.expanduser()
    if args.v1_audit_csv:
        args.v1_audit_csv = args.v1_audit_csv.expanduser()
    if args.output_dir is None:
        args.output_dir = args.data_root / "derived/contract_examples/xuan_bridge_scorecard_latest"
    args.output_dir = args.output_dir.expanduser()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

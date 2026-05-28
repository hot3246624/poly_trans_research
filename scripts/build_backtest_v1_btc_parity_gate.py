#!/usr/bin/env python3
"""Build a BTC parity gate between multiasset V1 screener and old BTC baseline."""

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
DEFAULT_V1_BTC_COMPLETION_STATE_MACHINE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
)
DEFAULT_BTC_DELTA_REPORT = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_adapter_delta_latest/BTC_COMPLETION_ADAPTER_DELTA_REPORT.json"
)
DEFAULT_BTC_STRICT_RESCUE_REPORT = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_strict_rescue_opportunity_latest/BTC_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
)
DEFAULT_BTC_RESCUE_ADJUSTED_LEDGER = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_rescue_adjusted_capital_ledger_latest/BTC_RESCUE_ADJUSTED_CAPITAL_LEDGER.json"
)
DEFAULT_BTC_MERGE_TURNOVER_REPORT = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_merge_turnover_latest/BTC_MERGE_TURNOVER_REPORT.json"
)
DEFAULT_BTC_SOURCE_SEMANTICS_REPORT = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_source_semantics_delta_latest/BTC_SOURCE_SEMANTICS_DELTA_REPORT.json"
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


def count_asset(rows: list[dict[str, Any]], asset: str) -> int:
    return sum(1 for row in rows if str(row.get("asset") or "").upper() == asset)


def query_btc_search_safe_rows(db_path: Path) -> int | None:
    if not db_path.exists():
        return None
    import duckdb  # type: ignore

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return int(
            con.execute(
                "select count(*) from l1_taker_buy_events_search_safe where market_symbol = 'BTC'"
            ).fetchone()[0]
        )
    finally:
        con.close()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["metric", "old_btc_baseline", "multiasset_v1", "status", "notes"]
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
    old_summary = read_csv(args.old_baseline_dir / "summary_by_day.csv")
    old_l2_bridge = read_json(args.old_l2_bridge_manifest) if args.old_l2_bridge_manifest.exists() else {}
    old_pair_lots = read_json(args.old_pair_lots_manifest) if args.old_pair_lots_manifest.exists() else {}
    old_residual_l2_recovery = (
        read_json(args.old_residual_l2_recovery_manifest)
        if args.old_residual_l2_recovery_manifest.exists()
        else {}
    )
    old_l2_summary = old_l2_bridge.get("summary") or {}
    v1_sm_manifest_path = args.v1_completion_state_machine_dir / "RESULT_SUMMARY_MANIFEST.json"
    v1_sm_manifest = read_json(v1_sm_manifest_path)
    v1_sm_metrics = v1_sm_manifest.get("core_metrics") or {}
    v1_btc_sm_manifest_path = args.v1_btc_completion_state_machine_dir / "RESULT_SUMMARY_MANIFEST.json"
    v1_btc_sm_manifest = read_json(v1_btc_sm_manifest_path)
    v1_btc_sm_metrics = v1_btc_sm_manifest.get("core_metrics") or {}
    btc_delta_report = read_json(args.btc_delta_report) if args.btc_delta_report.exists() else {}
    btc_strict_rescue_report = (
        read_json(args.btc_strict_rescue_report) if args.btc_strict_rescue_report.exists() else {}
    )
    btc_strict_rescue_summary = btc_strict_rescue_report.get("summary") or {}
    btc_rescue_adjusted_ledger = (
        read_json(args.btc_rescue_adjusted_ledger) if args.btc_rescue_adjusted_ledger.exists() else {}
    )
    btc_rescue_scenarios = btc_rescue_adjusted_ledger.get("scenarios") or {}
    btc_merge_turnover_report = (
        read_json(args.btc_merge_turnover_report) if args.btc_merge_turnover_report.exists() else {}
    )
    btc_merge_turnover_metrics = btc_merge_turnover_report.get("metrics") or {}
    btc_source_semantics_report = (
        read_json(args.btc_source_semantics_report) if args.btc_source_semantics_report.exists() else {}
    )
    btc_source_semantics_summary = btc_source_semantics_report.get("summary") or {}

    search_manifest_path = (
        data_root
        / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_SEARCH_SAFE_VIEW_MANIFEST.json"
    )
    search_manifest = read_json(search_manifest_path)
    search_db = search_manifest_path.parent / str((search_manifest.get("outputs") or {}).get("duckdb") or "event_store.duckdb")
    btc_search_rows = query_btc_search_safe_rows(search_db)

    contract = data_root / "derived/contract_examples"
    matrix_rows = read_csv(contract / "search_multiasset_l1_flow_matrix_formal_v1/search_matrix_results.csv")
    catalog_rows = read_csv(contract / "backtest_result_catalog_deep_v1/backtest_result_catalog.csv")
    shortlist_rows = read_csv(contract / "backtest_candidate_shortlist_deep_v1/backtest_candidate_shortlist.csv")
    validation_rows = read_csv(contract / "backtest_validation_result_catalog_deep_v1/backtest_validation_result_catalog.csv")
    l2_audit_csv = contract / "backtest_candidate_audit_pack_with_l2_evidence_latest/backtest_candidate_audit_pack.csv"
    audit_csv = l2_audit_csv if l2_audit_csv.exists() else contract / "backtest_candidate_audit_pack_latest/backtest_candidate_audit_pack.csv"
    audit_rows = read_csv(audit_csv)
    l2_parity_path = contract / "l1_from_l2_parity_latest/L1_FROM_L2_PARITY_REPORT.json"
    l2_parity = read_json(l2_parity_path)
    l2_parity_status = l2_parity.get("status") or "MISSING"
    l2_top_path = contract / "l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
    l2_top = read_json(l2_top_path)
    l2_top_status = l2_top.get("status") or "MISSING"
    l2_top_aligned_contract = {
        "contract_name": "md_book_l2_top_aligned",
        "top_source": "md_book_l1 canonical top",
        "depth_source": "latest md_book_l2 side snapshot at or before L1 capture sequence",
        "raw_md_book_l2_is_top_of_book_contract": False,
        "accepted_for_v1_l2_evidence": l2_top_status == "OK",
        "status": "OK" if l2_top_status == "OK" else "NOT_READY",
    }
    private_truth_policy = {
        "historical_shadow_owner_private_truth_available": False,
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
        "reason": "Historical shadow/no-order/public/proxy evidence cannot be promoted to owner private truth.",
    }
    source_semantics_explanation = {
        "new_btc_normalized_adapter": "core replay md_trades taker_side normalized to BUY runner events",
        "old_btc_baseline": "legacy SELL/mixed public_trade/l1_price_change source-event semantics",
        "equivalence_status": "NOT_PROVEN",
        "parity_policy": "Treat the BTC adapter as a research bridge until source-event semantics are explicitly accepted or both baselines are migrated to the same normalized source contract.",
    }

    v1_metrics = {
        "btc_search_safe_rows": btc_search_rows,
        "btc_matrix_result_rows": count_asset(matrix_rows, "BTC"),
        "btc_catalog_rows": count_asset(catalog_rows, "BTC"),
        "btc_shortlist_rows": count_asset(shortlist_rows, "BTC"),
        "btc_validation_rows": count_asset(validation_rows, "BTC"),
        "btc_audit_rows": count_asset(audit_rows, "BTC"),
        "completion_adapter_status": v1_sm_manifest.get("status") if v1_sm_manifest else "MISSING",
        "completion_adapter_candidate_count": v1_sm_metrics.get("candidate_count"),
        "completion_adapter_selected_candidate_count": v1_sm_metrics.get("selected_candidate_count"),
        "completion_adapter_pair_actions": v1_sm_metrics.get("pair_actions"),
        "completion_adapter_pair_pnl": v1_sm_metrics.get("pair_pnl"),
        "completion_adapter_fee_after_pnl": v1_sm_metrics.get("fee_after_pnl"),
        "completion_adapter_net_roi": v1_sm_metrics.get("net_roi"),
        "completion_adapter_residual_cost_rate": v1_sm_metrics.get("residual_cost_rate")
        or v1_sm_metrics.get("cost_residual_rate"),
        "btc_completion_adapter_status": v1_btc_sm_manifest.get("status") if v1_btc_sm_manifest else "MISSING",
        "btc_completion_adapter_candidate_count": v1_btc_sm_metrics.get("candidate_count"),
        "btc_completion_adapter_selected_candidate_count": v1_btc_sm_metrics.get("selected_candidate_count"),
        "btc_completion_adapter_pair_actions": v1_btc_sm_metrics.get("pair_actions"),
        "btc_completion_adapter_pair_pnl": v1_btc_sm_metrics.get("pair_pnl"),
        "btc_completion_adapter_fee_after_pnl": v1_btc_sm_metrics.get("fee_after_pnl"),
        "btc_completion_adapter_net_roi": v1_btc_sm_metrics.get("net_roi"),
        "btc_completion_adapter_residual_cost_rate": v1_btc_sm_metrics.get("residual_cost_rate")
        or v1_btc_sm_metrics.get("cost_residual_rate"),
        "btc_strict_rescue_status": btc_strict_rescue_report.get("status") if btc_strict_rescue_report else "MISSING",
        "btc_strict_rescue_best_after_fee_rescue_pnl": btc_strict_rescue_summary.get("best_after_fee_rescue_pnl"),
        "btc_strict_rescue_break_even_after_fee_lot_rate": btc_strict_rescue_summary.get("break_even_after_fee_lot_rate"),
        "btc_strict_rescue_beats_settlement_lot_rate": btc_strict_rescue_summary.get("rescue_beats_settlement_lot_rate"),
        "btc_rescue_adjusted_ledger_status": (
            btc_rescue_adjusted_ledger.get("status") if btc_rescue_adjusted_ledger else "MISSING"
        ),
        "btc_rescue_all_best_quote_fee_after_pnl": (
            (btc_rescue_scenarios.get("strict_rescue_all_best_quote") or {}).get("fee_after_pnl")
        ),
        "btc_rescue_all_best_quote_net_roi": (
            (btc_rescue_scenarios.get("strict_rescue_all_best_quote") or {}).get("net_roi")
        ),
        "btc_merge_turnover_status": btc_merge_turnover_report.get("status") if btc_merge_turnover_report else "MISSING",
        "btc_merge_rounds_per_market": btc_merge_turnover_metrics.get("rounds_per_market"),
        "btc_merge_capital_return_strict_rescue_over_gross_cost": btc_merge_turnover_metrics.get(
            "capital_return_strict_rescue_over_gross_cost"
        ),
        "btc_pair_merge_value_over_gross_cost": btc_merge_turnover_metrics.get("pair_merge_value_over_gross_cost"),
        "btc_source_semantics_status": (
            btc_source_semantics_report.get("status") if btc_source_semantics_report else "MISSING"
        ),
        "btc_source_runner_candidate_ratio_new_over_old": btc_source_semantics_summary.get(
            "runner_candidate_ratio_new_over_old"
        ),
        "best_btc_queue_pnl": max(
            [value for value in (to_float(row.get("pnl")) for row in catalog_rows if str(row.get("asset") or "").upper() == "BTC") if value is not None],
            default=None,
        ),
    }

    metrics_rows = [
        {
            "metric": "source_or_search_rows",
            "old_btc_baseline": old_metrics.get("candidate_count"),
            "multiasset_v1": v1_metrics["btc_search_safe_rows"],
            "status": "NOT_COMPARABLE",
            "notes": "Old candidate_count is completion candidate base; V1 value is search-safe L1 event rows.",
        },
        {
            "metric": "selected_actions_or_audit_candidates",
            "old_btc_baseline": old_metrics.get("selected_candidate_count") or old_manifest.get("row_count"),
            "multiasset_v1": v1_metrics["btc_completion_adapter_selected_candidate_count"],
            "status": "BTC_ADAPTER_PRESENT",
            "notes": "BTC-only completion adapter selected actions; audit-pack screener still selected no BTC candidates.",
        },
        {
            "metric": "pair_pnl",
            "old_btc_baseline": old_metrics.get("pair_pnl"),
            "multiasset_v1": v1_metrics["btc_completion_adapter_pair_pnl"],
            "status": "BTC_ADAPTER_DELTA_NEEDS_ATTRIBUTION",
            "notes": "BTC-only adapter has pair PnL; remaining work is explaining deltas from candidate source/filter semantics.",
        },
        {
            "metric": "fee_after_pnl",
            "old_btc_baseline": old_metrics.get("fee_after_pnl"),
            "multiasset_v1": v1_metrics["btc_completion_adapter_fee_after_pnl"],
            "status": "BTC_ADAPTER_DELTA_NEEDS_ATTRIBUTION",
            "notes": "Adapter includes after-fee pair/residual/settlement metrics.",
        },
        {
            "metric": "net_roi",
            "old_btc_baseline": old_metrics.get("net_roi"),
            "multiasset_v1": v1_metrics["btc_completion_adapter_net_roi"],
            "status": "BTC_ADAPTER_DELTA_NEEDS_ATTRIBUTION",
            "notes": "ROI exists in BTC adapter; parity requires component-delta explanation.",
        },
        {
            "metric": "residual_cost_rate",
            "old_btc_baseline": old_metrics.get("residual_cost_rate") or old_metrics.get("cost_residual_rate"),
            "multiasset_v1": v1_metrics["btc_completion_adapter_residual_cost_rate"],
            "status": "BTC_ADAPTER_DELTA_NEEDS_ATTRIBUTION",
            "notes": "BTC adapter has FIFO residual lots; remaining gap is source/filter attribution.",
        },
        {
            "metric": "best_btc_queue_pnl",
            "old_btc_baseline": "",
            "multiasset_v1": v1_metrics["best_btc_queue_pnl"],
            "status": "SCREENER_ONLY",
            "notes": "Queue PnL does not include pair-completion, rescue, residual, or merge capital reuse.",
        },
        {
            "metric": "strict_rescue_best_after_fee_pnl",
            "old_btc_baseline": "",
            "multiasset_v1": v1_metrics["btc_strict_rescue_best_after_fee_rescue_pnl"],
            "status": "BTC_STRICT_RESCUE_RESEARCH_READY",
            "notes": "Strict rescue report scans BTC adapter residual lots against top-aligned L2 quotes.",
        },
        {
            "metric": "strict_rescue_break_even_after_fee_lot_rate",
            "old_btc_baseline": "",
            "multiasset_v1": v1_metrics["btc_strict_rescue_break_even_after_fee_lot_rate"],
            "status": "BTC_STRICT_RESCUE_RESEARCH_READY",
            "notes": "Rate of residual lots with an after-fee bid recovery at or above lot cost in the rescue window.",
        },
        {
            "metric": "rescue_adjusted_fee_after_pnl_all_best_quote",
            "old_btc_baseline": "",
            "multiasset_v1": v1_metrics["btc_rescue_all_best_quote_fee_after_pnl"],
            "status": "BTC_RESCUE_ADJUSTED_LEDGER_READY",
            "notes": "Research scenario that closes each residual lot at the best top-aligned after-fee bid in the rescue window.",
        },
        {
            "metric": "merge_turnover_rounds_per_market",
            "old_btc_baseline": old_metrics.get("rounds_per_market"),
            "multiasset_v1": v1_metrics["btc_merge_rounds_per_market"],
            "status": "BTC_MERGE_TURNOVER_READY",
            "notes": "Pair merge/redeem turnover proxy from BTC adapter pair_qty and active markets.",
        },
        {
            "metric": "capital_return_strict_rescue_over_gross_cost",
            "old_btc_baseline": "",
            "multiasset_v1": v1_metrics["btc_merge_capital_return_strict_rescue_over_gross_cost"],
            "status": "BTC_MERGE_TURNOVER_READY",
            "notes": "Pair merge value plus strict-rescue residual recovery divided by gross buy cost.",
        },
        {
            "metric": "source_runner_candidate_ratio_new_over_old",
            "old_btc_baseline": btc_source_semantics_summary.get("old_runner_candidate_rows"),
            "multiasset_v1": btc_source_semantics_summary.get("new_runner_candidate_rows"),
            "status": "BTC_SOURCE_SEMANTICS_DELTA_READY",
            "notes": "New adapter uses core md_trades taker-side normalization and BUY runner events; old baseline uses SELL/mixed source-event semantics.",
        },
        {
            "metric": "old_selected_actions_l2_bridge_match_rate",
            "old_btc_baseline": old_l2_summary.get("both_side_match_rate"),
            "multiasset_v1": "",
            "status": "OLD_BASELINE_L2_BRIDGED"
            if old_l2_bridge.get("status") == "OK_OLD_BASELINE_L2_BRIDGE_READY"
            else "MISSING_OLD_BASELINE_L2_BRIDGE",
            "notes": "Old selected actions are aligned to the local md_book_l2_top_aligned mart for source-age/depth evidence.",
        },
        {
            "metric": "old_selected_actions_l2_bridge_p95_event_age_ms",
            "old_btc_baseline": old_l2_summary.get("p95_side_event_age_ms"),
            "multiasset_v1": "",
            "status": "OLD_BASELINE_L2_BRIDGED"
            if old_l2_bridge.get("status") == "OK_OLD_BASELINE_L2_BRIDGE_READY"
            else "MISSING_OLD_BASELINE_L2_BRIDGE",
            "notes": "P95 lag from action timestamp to matched canonical L1 top row.",
        },
    ]
    metrics_csv = output_dir / "btc_parity_gate_metrics.csv"
    write_csv(metrics_csv, metrics_rows)

    blockers = [
        "btc_source_taker_side_semantics_not_normalized_to_old_baseline",
        "btc_audit_pack_screener_candidates_are_zero",
        "btc_merge_turnover_not_yet_matched_to_old_source_semantics",
        "owner_private_truth_remains_unavailable_for_historical_shadow",
    ]
    if v1_sm_manifest.get("status") != "PASS_LOCAL_COMPLETION_RESEARCH_ONLY":
        blockers.append("v1_completion_state_machine_adapter_missing")
    if v1_btc_sm_manifest.get("status") != "PASS_LOCAL_COMPLETION_RESEARCH_ONLY":
        blockers.append("v1_btc_completion_state_machine_adapter_missing")
    if btc_delta_report.get("status") != "OK_BTC_ADAPTER_DELTA_ATTRIBUTION_READY":
        blockers.append("btc_completion_adapter_delta_report_missing")
    if btc_strict_rescue_report.get("status") != "OK_BTC_STRICT_RESCUE_OPPORTUNITY_READY":
        blockers.append("btc_strict_rescue_report_missing")
    if btc_rescue_adjusted_ledger.get("status") != "OK_BTC_RESCUE_ADJUSTED_LEDGER_READY":
        blockers.append("btc_rescue_adjusted_capital_ledger_missing")
    if btc_merge_turnover_report.get("status") != "OK_BTC_MERGE_TURNOVER_READY":
        blockers.append("btc_merge_turnover_report_missing")
    if btc_source_semantics_report.get("status") != "OK_BTC_SOURCE_SEMANTICS_DELTA_READY":
        blockers.append("btc_source_semantics_delta_report_missing")
    if old_l2_bridge.get("status") != "OK_OLD_BASELINE_L2_BRIDGE_READY":
        blockers.append("old_selected_actions_not_l2_bridged")
    if (old_l2_bridge.get("anchor_policy") or {}).get("status") != "SURROGATE_ANCHORS_CONSTRUCTED_SOURCE_LABELS_NOT_FOUND":
        blockers.append("old_anchor_windows_not_replayed_in_v1_bridge")
    if old_pair_lots.get("status") != "OK_OLD_BASELINE_PAIR_LOTS_RECONSTRUCTED":
        blockers.append("old_baseline_pair_lots_not_reconstructed")
    if l2_top_status != "OK":
        blockers.append("local_l2_top_aligned_mart_not_ready")
    elif l2_parity_status != "OK_L1_TOP_OVERLAY_REQUIRED":
        blockers.append("l1_from_l2_parity_status_unexpected")

    summary = {
        "status": "BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN",
        "blocker_count": len(blockers),
        "btc_search_safe_rows": v1_metrics["btc_search_safe_rows"],
        "btc_audit_rows": v1_metrics["btc_audit_rows"],
        "btc_completion_adapter_status": v1_metrics["btc_completion_adapter_status"],
        "btc_completion_adapter_candidate_count": v1_metrics["btc_completion_adapter_candidate_count"],
        "btc_completion_adapter_selected_candidate_count": v1_metrics[
            "btc_completion_adapter_selected_candidate_count"
        ],
        "btc_completion_adapter_fee_after_pnl": v1_metrics["btc_completion_adapter_fee_after_pnl"],
        "btc_completion_adapter_net_roi": v1_metrics["btc_completion_adapter_net_roi"],
        "btc_rescue_all_best_quote_fee_after_pnl": v1_metrics["btc_rescue_all_best_quote_fee_after_pnl"],
        "btc_rescue_all_best_quote_net_roi": v1_metrics["btc_rescue_all_best_quote_net_roi"],
        "btc_merge_rounds_per_market": v1_metrics["btc_merge_rounds_per_market"],
        "btc_source_runner_candidate_ratio_new_over_old": v1_metrics[
            "btc_source_runner_candidate_ratio_new_over_old"
        ],
        "l2_top_aligned_mart_status": l2_top_status,
        "l1_from_l2_parity_status": l2_parity_status,
        "owner_private_truth_ready": False,
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
    }
    manifest = {
        "schema_version": "backtest_v1_btc_parity_gate_v1",
        "created_utc": utc_now(),
        "status": "BLOCKED_BTC_BASELINE_PARITY_NOT_PROVEN",
        "summary": summary,
        "l2_top_aligned_contract": l2_top_aligned_contract,
        "private_truth_policy": private_truth_policy,
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
        "source_semantics_explanation": source_semantics_explanation,
        "data_root": str(data_root),
        "output_dir": str(output_dir),
        "old_baseline_manifest": str(old_manifest_path),
        "old_baseline_status": old_manifest.get("status"),
        "old_baseline_core_metrics": old_metrics,
        "old_baseline_summary_by_day_rows": len(old_summary),
        "old_baseline_l2_bridge": {
            "manifest": str(args.old_l2_bridge_manifest),
            "status": old_l2_bridge.get("status") if old_l2_bridge else "MISSING",
            "summary": old_l2_summary,
            "anchor_policy": old_l2_bridge.get("anchor_policy") if old_l2_bridge else None,
        },
        "old_baseline_pair_lots": {
            "manifest": str(args.old_pair_lots_manifest),
            "status": old_pair_lots.get("status") if old_pair_lots else "MISSING",
            "metrics": old_pair_lots.get("metrics") if old_pair_lots else {},
        },
        "old_baseline_residual_l2_recovery": {
            "manifest": str(args.old_residual_l2_recovery_manifest),
            "status": old_residual_l2_recovery.get("status") if old_residual_l2_recovery else "MISSING",
            "summary": old_residual_l2_recovery.get("summary") if old_residual_l2_recovery else {},
        },
        "v1_completion_state_machine": {
            "manifest": str(v1_sm_manifest_path),
            "status": v1_sm_manifest.get("status") if v1_sm_manifest else "MISSING",
            "core_metrics": v1_sm_metrics,
        },
        "v1_btc_completion_state_machine": {
            "manifest": str(v1_btc_sm_manifest_path),
            "status": v1_btc_sm_manifest.get("status") if v1_btc_sm_manifest else "MISSING",
            "core_metrics": v1_btc_sm_metrics,
        },
        "btc_completion_adapter_delta_report": {
            "manifest": str(args.btc_delta_report),
            "status": btc_delta_report.get("status") if btc_delta_report else "MISSING",
            "interpretation": btc_delta_report.get("interpretation") if btc_delta_report else {},
        },
        "btc_strict_rescue_opportunity_report": {
            "manifest": str(args.btc_strict_rescue_report),
            "status": btc_strict_rescue_report.get("status") if btc_strict_rescue_report else "MISSING",
            "summary": btc_strict_rescue_summary,
        },
        "btc_rescue_adjusted_capital_ledger": {
            "manifest": str(args.btc_rescue_adjusted_ledger),
            "status": btc_rescue_adjusted_ledger.get("status") if btc_rescue_adjusted_ledger else "MISSING",
            "scenarios": btc_rescue_scenarios,
        },
        "btc_merge_turnover_report": {
            "manifest": str(args.btc_merge_turnover_report),
            "status": btc_merge_turnover_report.get("status") if btc_merge_turnover_report else "MISSING",
            "metrics": btc_merge_turnover_metrics,
        },
        "btc_source_semantics_delta_report": {
            "manifest": str(args.btc_source_semantics_report),
            "status": btc_source_semantics_report.get("status") if btc_source_semantics_report else "MISSING",
            "summary": btc_source_semantics_summary,
            "interpretation": btc_source_semantics_report.get("interpretation") if btc_source_semantics_report else {},
        },
        "multiasset_v1_metrics": v1_metrics,
        "multiasset_v1_audit_csv": str(audit_csv),
        "l1_from_l2_parity": {
            "manifest": str(l2_parity_path),
            "status": l2_parity_status,
            "failed_assets": l2_parity.get("failed_assets") or [],
        },
        "l2_top_aligned_mart": {
            "manifest": str(l2_top_path),
            "status": l2_top_status,
            "row_count": l2_top.get("row_count"),
            "missing_depth_rows": l2_top.get("missing_depth_rows"),
            "top_overlay_required_rate": l2_top.get("top_overlay_required_rate"),
            "contract": l2_top_aligned_contract,
        },
        "outputs": {"metrics_csv": str(metrics_csv)},
        "blockers": blockers,
        "resolved_requirements": [
            "Local multiasset L2 tier is built." if l2_top_status == "OK" else "Local multiasset L2 tier is not ready.",
            "L1-top-aligned L2 mart is the accepted legacy replay parity model."
            if l2_parity_status == "OK_L1_TOP_OVERLAY_REQUIRED" and l2_top_status == "OK"
            else "L1/L2 parity model still needs attention.",
            "Old BTC selected actions are aligned to the local L2 mart."
            if old_l2_bridge.get("status") == "OK_OLD_BASELINE_L2_BRIDGE_READY"
            else "Old BTC selected actions still need L2 bridge evidence.",
            "Old BTC pair lots are reconstructed and match the baseline manifest."
            if old_pair_lots.get("status") == "OK_OLD_BASELINE_PAIR_LOTS_RECONSTRUCTED"
            else "Old BTC pair lots still need reconstruction.",
            "Old BTC residual lots have L2 dynamic recovery evidence."
            if old_residual_l2_recovery.get("status") == "OK_OLD_BASELINE_RESIDUAL_L2_RECOVERY_READY"
            else "Old BTC residual lots still need L2 recovery evidence.",
            "V1 now has a multiasset completion/residual research adapter."
            if v1_sm_manifest.get("status") == "PASS_LOCAL_COMPLETION_RESEARCH_ONLY"
            else "V1 completion/residual research adapter is missing.",
            "V1 now has a BTC-only completion/residual research adapter."
            if v1_btc_sm_manifest.get("status") == "PASS_LOCAL_COMPLETION_RESEARCH_ONLY"
            else "V1 BTC-only completion/residual adapter is missing.",
            "BTC adapter vs old baseline delta attribution report is available."
            if btc_delta_report.get("status") == "OK_BTC_ADAPTER_DELTA_ATTRIBUTION_READY"
            else "BTC adapter delta attribution report is missing.",
            "BTC strict rescue opportunity report is available."
            if btc_strict_rescue_report.get("status") == "OK_BTC_STRICT_RESCUE_OPPORTUNITY_READY"
            else "BTC strict rescue opportunity report is missing.",
            "BTC rescue-adjusted capital ledger scenarios are available."
            if btc_rescue_adjusted_ledger.get("status") == "OK_BTC_RESCUE_ADJUSTED_LEDGER_READY"
            else "BTC rescue-adjusted capital ledger scenarios are missing.",
            "BTC merge/redeem capital turnover report is available."
            if btc_merge_turnover_report.get("status") == "OK_BTC_MERGE_TURNOVER_READY"
            else "BTC merge/redeem capital turnover report is missing.",
            "BTC source semantics delta report is available."
            if btc_source_semantics_report.get("status") == "OK_BTC_SOURCE_SEMANTICS_DELTA_READY"
            else "BTC source semantics delta report is missing.",
        ],
        "minimum_remaining_requirements": [
            "Normalize or explicitly accept BTC taker-side/source-event semantics.",
            "Match merge/redeem turnover to old source/event-generation semantics after taker-side normalization.",
        ],
    }
    manifest_path = output_dir / "BACKTEST_V1_BTC_PARITY_GATE.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {k: manifest[k] for k in ("status", "summary", "multiasset_v1_metrics", "outputs", "blockers")},
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
    parser.add_argument("--v1-btc-completion-state-machine-dir", type=Path, default=DEFAULT_V1_BTC_COMPLETION_STATE_MACHINE)
    parser.add_argument("--btc-delta-report", type=Path, default=DEFAULT_BTC_DELTA_REPORT)
    parser.add_argument("--btc-strict-rescue-report", type=Path, default=DEFAULT_BTC_STRICT_RESCUE_REPORT)
    parser.add_argument("--btc-rescue-adjusted-ledger", type=Path, default=DEFAULT_BTC_RESCUE_ADJUSTED_LEDGER)
    parser.add_argument("--btc-merge-turnover-report", type=Path, default=DEFAULT_BTC_MERGE_TURNOVER_REPORT)
    parser.add_argument("--btc-source-semantics-report", type=Path, default=DEFAULT_BTC_SOURCE_SEMANTICS_REPORT)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    args.data_root = args.data_root.expanduser()
    args.old_baseline_dir = args.old_baseline_dir.expanduser()
    args.old_l2_bridge_manifest = args.old_l2_bridge_manifest.expanduser()
    args.old_pair_lots_manifest = args.old_pair_lots_manifest.expanduser()
    args.old_residual_l2_recovery_manifest = args.old_residual_l2_recovery_manifest.expanduser()
    args.v1_completion_state_machine_dir = args.v1_completion_state_machine_dir.expanduser()
    args.v1_btc_completion_state_machine_dir = args.v1_btc_completion_state_machine_dir.expanduser()
    args.btc_delta_report = args.btc_delta_report.expanduser()
    args.btc_strict_rescue_report = args.btc_strict_rescue_report.expanduser()
    args.btc_rescue_adjusted_ledger = args.btc_rescue_adjusted_ledger.expanduser()
    args.btc_merge_turnover_report = args.btc_merge_turnover_report.expanduser()
    args.btc_source_semantics_report = args.btc_source_semantics_report.expanduser()
    if args.output_dir is None:
        args.output_dir = args.data_root / "derived/contract_examples/backtest_v1_btc_parity_latest"
    args.output_dir = args.output_dir.expanduser()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

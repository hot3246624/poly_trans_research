#!/usr/bin/env python3
"""Build the top-level xuan strategy readiness gate for Backtest V1."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_INSTALL_GATE = DEFAULT_CONTRACT / "multiasset_backtest_v1_local_install_validation_latest.json"
DEFAULT_L2_TOP = (
    DEFAULT_CONTRACT / "l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)
DEFAULT_BTC_PARITY = DEFAULT_CONTRACT / "backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json"
DEFAULT_XUAN_BRIDGE = DEFAULT_CONTRACT / "xuan_bridge_scorecard_latest/XUAN_BRIDGE_SCORECARD_MANIFEST.json"
DEFAULT_MULTIASSET_COMPLETION = (
    DEFAULT_CONTRACT / "multiasset_completion_state_machine_from_l1_flow_v1/RESULT_SUMMARY_MANIFEST.json"
)
DEFAULT_MULTIASSET_STRICT_RESCUE = (
    DEFAULT_CONTRACT
    / "multiasset_strict_rescue_opportunity_latest/MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
)
DEFAULT_MULTIASSET_MERGE_TURNOVER = (
    DEFAULT_CONTRACT / "multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json"
)
DEFAULT_COVERAGE_SCORECARD = (
    DEFAULT_CONTRACT / "multiasset_backtest_coverage_scorecard_latest/MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json"
)
DEFAULT_CANDIDATE_RESCORE = (
    DEFAULT_CONTRACT / "xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json"
)
DEFAULT_CAPITAL_LEDGER = DEFAULT_CONTRACT / "xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json"
DEFAULT_BTC_OVERLAP_DECOMPOSITION = (
    DEFAULT_CONTRACT
    / "btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json"
)
DEFAULT_BTC_TINY_CANARY_PREFLIGHT = (
    DEFAULT_CONTRACT / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json"
)
DEFAULT_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL = (
    DEFAULT_CONTRACT
    / "xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json"
)
DEFAULT_SAME_WINDOW_HANDOFF_TIERED = (
    DEFAULT_CONTRACT
    / "xuan_same_window_handoff_tiered_scorecard_latest/XUAN_SAME_WINDOW_HANDOFF_TIERED_SCORECARD_MANIFEST.json"
)
DEFAULT_SHADOW_DESIGN_PACKET = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_design_packet_latest/XUAN_SAME_WINDOW_SHADOW_DESIGN_PACKET_MANIFEST.json"
)
DEFAULT_SHADOW_READINESS_GATE = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_readiness_gate_latest/XUAN_SAME_WINDOW_SHADOW_READINESS_GATE.json"
)
DEFAULT_SHADOW_START_PREFLIGHT = (
    DEFAULT_CONTRACT
    / "xuan_same_window_shadow_start_preflight_spec_latest/XUAN_SAME_WINDOW_SHADOW_START_PREFLIGHT_SPEC.json"
)
DEFAULT_NO_ORDER_SHADOW_START_PREFLIGHT = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json"
)
DEFAULT_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_manual_approval_packet_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json"
)
DEFAULT_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_manual_approval_decision_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json"
)
DEFAULT_REAL_NO_ORDER_SHADOW_EVAL = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_real_ws_runner_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json"
)
DEFAULT_REAL_NO_ORDER_SHADOW_START_SCOPE_EVAL = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json"
)

REAL_NO_ORDER_SHADOW_MIN_ROWS_FOR_RESEARCH_EVIDENCE = 100
REAL_NO_ORDER_SHADOW_MIN_CANDIDATES_FOR_RESEARCH_EVIDENCE = 25
REAL_NO_ORDER_SHADOW_MIN_MARKETS_FOR_RESEARCH_EVIDENCE = 10
PUBLIC_L2_PROXY_NO_ORDER_SHADOW_PASS_STATUS = (
    "KEEP_XUAN_BTC_TINY_CANARY_PUBLIC_L2_PROXY_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
)
REAL_READONLY_WS_NO_ORDER_SHADOW_PASS_STATUS = (
    "KEEP_XUAN_BTC_TINY_CANARY_REAL_READONLY_WS_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
)
SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_PASS_STATUS = (
    "KEEP_XUAN_SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
)
SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_START_SCOPE_STATUS = (
    "KEEP_XUAN_SAME_WINDOW_REAL_WS_START_SCOPE_VALIDATED_APPROVAL_REQUIRED"
)
LEGACY_REAL_NO_ORDER_SHADOW_PASS_STATUS = (
    "KEEP_XUAN_SAME_WINDOW_PUBLIC_BOOK_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-gate", type=Path, default=DEFAULT_INSTALL_GATE)
    parser.add_argument("--l2-top-manifest", type=Path, default=DEFAULT_L2_TOP)
    parser.add_argument("--btc-parity-gate", type=Path, default=DEFAULT_BTC_PARITY)
    parser.add_argument("--xuan-bridge-scorecard", type=Path, default=DEFAULT_XUAN_BRIDGE)
    parser.add_argument("--multiasset-completion-manifest", type=Path, default=DEFAULT_MULTIASSET_COMPLETION)
    parser.add_argument("--multiasset-strict-rescue-report", type=Path, default=DEFAULT_MULTIASSET_STRICT_RESCUE)
    parser.add_argument("--multiasset-merge-turnover-report", type=Path, default=DEFAULT_MULTIASSET_MERGE_TURNOVER)
    parser.add_argument("--coverage-scorecard", type=Path, default=DEFAULT_COVERAGE_SCORECARD)
    parser.add_argument("--xuan-candidate-rescore", type=Path, default=DEFAULT_CANDIDATE_RESCORE)
    parser.add_argument("--xuan-capital-ledger", type=Path, default=DEFAULT_CAPITAL_LEDGER)
    parser.add_argument("--btc-overlap-decomposition", type=Path, default=DEFAULT_BTC_OVERLAP_DECOMPOSITION)
    parser.add_argument("--btc-tiny-canary-preflight", type=Path, default=DEFAULT_BTC_TINY_CANARY_PREFLIGHT)
    parser.add_argument(
        "--btc-tiny-canary-no-order-shadow-eval",
        type=Path,
        default=DEFAULT_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL,
    )
    parser.add_argument("--same-window-handoff-tiered", type=Path, default=DEFAULT_SAME_WINDOW_HANDOFF_TIERED)
    parser.add_argument("--shadow-design-packet", type=Path, default=DEFAULT_SHADOW_DESIGN_PACKET)
    parser.add_argument("--shadow-readiness-gate", type=Path, default=DEFAULT_SHADOW_READINESS_GATE)
    parser.add_argument("--shadow-start-preflight", type=Path, default=DEFAULT_SHADOW_START_PREFLIGHT)
    parser.add_argument("--no-order-shadow-start-preflight", type=Path, default=DEFAULT_NO_ORDER_SHADOW_START_PREFLIGHT)
    parser.add_argument(
        "--no-order-shadow-manual-approval-packet",
        type=Path,
        default=DEFAULT_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET,
    )
    parser.add_argument(
        "--no-order-shadow-manual-approval-decision",
        type=Path,
        default=DEFAULT_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION,
    )
    parser.add_argument(
        "--real-no-order-shadow-eval",
        type=Path,
        default=DEFAULT_REAL_NO_ORDER_SHADOW_EVAL,
    )
    parser.add_argument(
        "--real-no-order-shadow-start-scope-eval",
        type=Path,
        default=DEFAULT_REAL_NO_ORDER_SHADOW_START_SCOPE_EVAL,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_CONTRACT / "xuan_backtest_v1_strategy_readiness_latest",
    )
    parser.add_argument(
        "--accept-source-semantics",
        action="store_true",
        help="Explicitly mark current BTC normalized adapter source semantics as accepted for research comparison.",
    )
    args = parser.parse_args()

    install = read_json(args.install_gate.expanduser())
    l2_top = read_json(args.l2_top_manifest.expanduser())
    btc_parity = read_json(args.btc_parity_gate.expanduser())
    xuan_bridge = read_json(args.xuan_bridge_scorecard.expanduser())
    multiasset_completion = read_json(args.multiasset_completion_manifest.expanduser())
    multiasset_strict_rescue = read_json(args.multiasset_strict_rescue_report.expanduser())
    multiasset_merge_turnover = read_json(args.multiasset_merge_turnover_report.expanduser())
    coverage_scorecard = read_json(args.coverage_scorecard.expanduser())
    candidate_rescore = read_json(args.xuan_candidate_rescore.expanduser())
    capital_ledger = read_json(args.xuan_capital_ledger.expanduser())
    btc_overlap_decomposition = read_json(args.btc_overlap_decomposition.expanduser())
    btc_tiny_canary_preflight = read_json(args.btc_tiny_canary_preflight.expanduser())
    btc_tiny_canary_no_order_shadow_eval = read_json(args.btc_tiny_canary_no_order_shadow_eval.expanduser())
    same_window_handoff_tiered = read_json(args.same_window_handoff_tiered.expanduser())
    shadow_design_packet = read_json(args.shadow_design_packet.expanduser())
    shadow_readiness_gate = read_json(args.shadow_readiness_gate.expanduser())
    shadow_start_preflight = read_json(args.shadow_start_preflight.expanduser())
    no_order_shadow_start_preflight = read_json(args.no_order_shadow_start_preflight.expanduser())
    no_order_shadow_manual_approval_packet = read_json(args.no_order_shadow_manual_approval_packet.expanduser())
    no_order_shadow_manual_approval_decision = read_json(
        args.no_order_shadow_manual_approval_decision.expanduser()
    )
    real_no_order_shadow_eval = read_json(args.real_no_order_shadow_eval.expanduser())
    real_no_order_shadow_start_scope_eval = read_json(
        args.real_no_order_shadow_start_scope_eval.expanduser()
    )

    install_summary = install.get("summary") or {}
    bridge_summary = xuan_bridge.get("summary") or {}
    l2_contract = {
        "contract_name": "md_book_l2_top_aligned",
        "top_source": "md_book_l1 canonical top",
        "depth_source": "latest md_book_l2 side snapshot at or before L1 capture sequence",
        "raw_md_book_l2_is_top_of_book_contract": False,
        "status": "OK" if l2_top.get("status") == "OK" else "NOT_READY",
    }

    install_ok = install.get("status") == "OK" and int(install_summary.get("fail_count") or 0) == 0
    l2_top_aligned_contract_ok = l2_contract["status"] == "OK"
    btc_parity_proven = btc_parity.get("status") in {
        "OK_BTC_BASELINE_PARITY_PROVEN",
        "PASS_BTC_BASELINE_PARITY_PROVEN",
    }
    source_semantics_accepted = bool(args.accept_source_semantics)
    completion_adapter_ready = multiasset_completion.get("status") == "PASS_LOCAL_COMPLETION_RESEARCH_ONLY"
    strict_rescue_ready = multiasset_strict_rescue.get("status") == "OK_MULTIASSET_STRICT_RESCUE_OPPORTUNITY_READY"
    merge_turnover_ready = multiasset_merge_turnover.get("status") == "OK_MULTIASSET_MERGE_TURNOVER_READY"
    residual_risk_ready = completion_adapter_ready and merge_turnover_ready
    coverage_scorecard_ready = (
        coverage_scorecard.get("status") == "OK_MULTIASSET_BACKTEST_COVERAGE_SCORECARD_READY"
    )
    xuan_candidate_rescore_ready = (
        candidate_rescore.get("status") == "OK_XUAN_COMPLETION_CANDIDATE_RESCORE_READY"
    )
    capital_ledger_ready = capital_ledger.get("status") == "OK_XUAN_CAPITAL_LEDGER_READY"
    btc_overlap_decomposition_ready = (
        btc_overlap_decomposition.get("status")
        == "OK_BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_READY_RESEARCH_ONLY"
    )
    btc_tiny_canary_preflight_ready = bool(btc_tiny_canary_preflight.get("canary_preflight_ready"))
    btc_tiny_canary_no_order_shadow_eval_passed = (
        btc_tiny_canary_no_order_shadow_eval.get("status") == PUBLIC_L2_PROXY_NO_ORDER_SHADOW_PASS_STATUS
        and bool((btc_tiny_canary_no_order_shadow_eval.get("summary") or {}).get("evaluation_passed"))
        and (btc_tiny_canary_no_order_shadow_eval.get("summary") or {}).get("public_l2_proxy_evaluated")
        is True
        and (btc_tiny_canary_no_order_shadow_eval.get("promotion_gate") or {}).get("private_truth_ready") is False
        and (btc_tiny_canary_no_order_shadow_eval.get("promotion_gate") or {}).get("strategy_promotion_ready")
        is False
        and (btc_tiny_canary_no_order_shadow_eval.get("promotion_gate") or {}).get("live_orders_allowed")
        is False
    )
    same_window_handoff_tiered_ready = (
        same_window_handoff_tiered.get("status")
        == "KEEP_XUAN_SAME_WINDOW_HANDOFF_TIERED_SCORECARD_READY_RESEARCH_ONLY"
    )
    shadow_design_packet_ready = (
        shadow_design_packet.get("status") == "KEEP_XUAN_SAME_WINDOW_SHADOW_DESIGN_PACKET_READY_RESEARCH_ONLY"
    )
    shadow_design_gate_ready = (
        shadow_readiness_gate.get("status")
        == "KEEP_XUAN_SAME_WINDOW_SHADOW_DESIGN_READY_START_APPROVAL_REQUIRED"
    )
    shadow_start_preflight_spec_ready = (
        shadow_start_preflight.get("status")
        == "KEEP_XUAN_SAME_WINDOW_SHADOW_START_PREFLIGHT_SPEC_READY_APPROVAL_REQUIRED"
    )
    no_order_shadow_start_engineering_ready = bool(
        no_order_shadow_start_preflight.get("engineering_preflight_ready")
    )
    no_order_shadow_start_conflict_check_passed = bool(
        (no_order_shadow_start_preflight.get("active_runner_conflict_check") or {}).get("passed")
    )
    no_order_shadow_manual_approval_packet_gate = (
        no_order_shadow_manual_approval_packet.get("promotion_gate")
        or no_order_shadow_manual_approval_packet.get("policy")
        or {}
    )
    no_order_shadow_manual_approval_packet_strategy_ready = no_order_shadow_manual_approval_packet_gate.get(
        "strategy_promotion_ready",
        no_order_shadow_manual_approval_packet_gate.get("promotion_ready"),
    )
    no_order_shadow_manual_approval_packet_ready = (
        no_order_shadow_manual_approval_packet.get("status")
        == "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET_READY_APPROVAL_REQUIRED"
        and no_order_shadow_manual_approval_packet.get("approval_packet_ready") is True
        and no_order_shadow_manual_approval_packet.get("manual_approval_granted") is False
        and no_order_shadow_manual_approval_packet.get("shadow_start_ready") is False
        and no_order_shadow_manual_approval_packet_gate.get("private_truth_ready") is False
        and no_order_shadow_manual_approval_packet_strategy_ready is False
        and no_order_shadow_manual_approval_packet_gate.get("live_orders_allowed") is False
        and no_order_shadow_manual_approval_packet_gate.get("deployable") is False
    )
    manual_approval_granted = (
        no_order_shadow_manual_approval_decision.get("status")
        == "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_GRANTED_NOT_STARTED"
        and no_order_shadow_manual_approval_decision.get("manual_approval_granted") is True
        and no_order_shadow_manual_approval_decision.get("runner_start_allowed_by_approval_gate") is True
        and no_order_shadow_manual_approval_decision.get("runner_started") is False
        and not (no_order_shadow_manual_approval_decision.get("remaining_blockers") or [])
        and (no_order_shadow_manual_approval_decision.get("promotion_gate") or {}).get("private_truth_ready")
        is False
        and (no_order_shadow_manual_approval_decision.get("promotion_gate") or {}).get(
            "strategy_promotion_ready"
        )
        is False
        and (no_order_shadow_manual_approval_decision.get("promotion_gate") or {}).get("live_orders_allowed")
        is False
    )
    real_no_order_shadow_eval_summary = real_no_order_shadow_eval.get("summary") or {}
    real_no_order_shadow_eval_policy = real_no_order_shadow_eval.get("policy") or {}
    real_no_order_shadow_promotion_gate = real_no_order_shadow_eval.get("promotion_gate") or {}
    real_no_order_shadow_contract_passed = (
        real_no_order_shadow_eval.get("status")
        in {
            REAL_READONLY_WS_NO_ORDER_SHADOW_PASS_STATUS,
            SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_PASS_STATUS,
            LEGACY_REAL_NO_ORDER_SHADOW_PASS_STATUS,
        }
        and real_no_order_shadow_eval_summary.get("evaluation_passed") is True
        and real_no_order_shadow_eval_summary.get("no_order_shadow_real_runner_evaluated") is True
        and real_no_order_shadow_eval.get("report_kind")
        in {"real_readonly_ws_no_order_observer", None}
        and real_no_order_shadow_eval_summary.get("public_l2_proxy_evaluated") is not True
        and real_no_order_shadow_eval_policy.get("orders_allowed") is False
        and real_no_order_shadow_eval_policy.get("candidate_import_allowed") is False
        and real_no_order_shadow_eval_policy.get("import_enabled") is False
        and real_no_order_shadow_eval_policy.get("private_truth_ready") is False
        and real_no_order_shadow_eval_policy.get("strategy_promotion_ready") is False
        and real_no_order_shadow_eval_policy.get("live_orders_allowed") is False
        and real_no_order_shadow_promotion_gate.get("private_truth_ready") is False
        and real_no_order_shadow_promotion_gate.get("strategy_promotion_ready") is False
        and real_no_order_shadow_promotion_gate.get("live_orders_allowed") is False
    )
    real_no_order_shadow_row_count = as_int(real_no_order_shadow_eval_summary.get("row_count"))
    real_no_order_shadow_candidate_count = as_int(real_no_order_shadow_eval_summary.get("candidate_count"))
    real_no_order_shadow_market_count = as_int(real_no_order_shadow_eval_summary.get("market_count"))
    real_no_order_shadow_sample_sufficient = bool(
        real_no_order_shadow_contract_passed
        and real_no_order_shadow_row_count >= REAL_NO_ORDER_SHADOW_MIN_ROWS_FOR_RESEARCH_EVIDENCE
        and real_no_order_shadow_candidate_count >= REAL_NO_ORDER_SHADOW_MIN_CANDIDATES_FOR_RESEARCH_EVIDENCE
        and real_no_order_shadow_market_count >= REAL_NO_ORDER_SHADOW_MIN_MARKETS_FOR_RESEARCH_EVIDENCE
    )
    real_no_order_shadow_start_scope_summary = real_no_order_shadow_start_scope_eval.get("summary") or {}
    real_no_order_shadow_start_scope_policy = real_no_order_shadow_start_scope_eval.get("policy") or {}
    real_no_order_shadow_start_scope_promotion_gate = (
        real_no_order_shadow_start_scope_eval.get("promotion_gate") or {}
    )
    real_no_order_shadow_start_scope_validated = (
        real_no_order_shadow_start_scope_eval.get("status")
        == SAME_WINDOW_REAL_WS_NO_ORDER_SHADOW_START_SCOPE_STATUS
        and real_no_order_shadow_start_scope_eval.get("scope_kind") == "start_scope"
        and real_no_order_shadow_start_scope_summary.get("evaluation_passed") is True
        and real_no_order_shadow_start_scope_summary.get("ws_start_scope_validated") is True
        and real_no_order_shadow_start_scope_summary.get("start_authorizing") is False
        and real_no_order_shadow_start_scope_policy.get("orders_allowed") is False
        and real_no_order_shadow_start_scope_policy.get("candidate_import_allowed") is False
        and real_no_order_shadow_start_scope_policy.get("import_enabled") is False
        and real_no_order_shadow_start_scope_policy.get("private_truth_ready") is False
        and real_no_order_shadow_start_scope_policy.get("strategy_promotion_ready") is False
        and real_no_order_shadow_start_scope_policy.get("live_orders_allowed") is False
        and real_no_order_shadow_start_scope_promotion_gate.get("private_truth_ready") is False
        and real_no_order_shadow_start_scope_promotion_gate.get("strategy_promotion_ready") is False
        and real_no_order_shadow_start_scope_promotion_gate.get("live_orders_allowed") is False
    )
    no_order_shadow_eval_contract_passed = (
        btc_tiny_canary_no_order_shadow_eval_passed or real_no_order_shadow_contract_passed
    )
    manual_approval_material_ready = no_order_shadow_manual_approval_packet_ready or manual_approval_granted
    xuan_bridge_complete = xuan_bridge.get("status") == "OK_XUAN_BRIDGE_COMPLETE"
    private_truth_ready = False

    blockers: list[str] = []
    warnings: list[str] = []
    if not install_ok:
        blockers.append("install_gate_not_ok")
    if not l2_top_aligned_contract_ok:
        blockers.append("l2_top_aligned_contract_not_ok")
    if not completion_adapter_ready:
        blockers.append("multiasset_completion_adapter_not_ready")
    if not strict_rescue_ready:
        blockers.append("multiasset_strict_rescue_not_ready")
    if not merge_turnover_ready:
        blockers.append("multiasset_merge_turnover_not_ready")
    if not residual_risk_ready:
        blockers.append("residual_risk_split_not_ready")
    if not coverage_scorecard_ready:
        blockers.append("multiasset_coverage_scorecard_not_ready")
    if not xuan_candidate_rescore_ready:
        blockers.append("xuan_completion_candidate_rescore_not_ready")
    if not capital_ledger_ready:
        blockers.append("xuan_capital_ledger_not_ready")
    if not btc_overlap_decomposition_ready:
        blockers.append("btc_v1_old_baseline_overlap_decomposition_not_ready")
    if not btc_parity_proven:
        warnings.append("btc_baseline_parity_not_proven")
    if not source_semantics_accepted:
        warnings.append("btc_source_semantics_not_explicitly_accepted")
    if not xuan_bridge_complete:
        warnings.append("xuan_bridge_still_partial")
    if not same_window_handoff_tiered_ready:
        warnings.append("same_window_handoff_tiered_scorecard_not_ready")
    if not shadow_design_packet_ready:
        warnings.append("shadow_design_packet_not_ready")
    if not shadow_design_gate_ready:
        warnings.append("shadow_design_gate_not_ready")
    if not shadow_start_preflight_spec_ready:
        warnings.append("shadow_start_preflight_spec_not_ready")
    if not no_order_shadow_start_engineering_ready:
        warnings.append("no_order_shadow_start_engineering_preflight_not_ready")
    if no_order_shadow_start_engineering_ready and not no_order_shadow_start_conflict_check_passed:
        warnings.append("no_order_shadow_active_runner_conflict_check_not_passed")
    if not btc_tiny_canary_preflight_ready:
        warnings.append("btc_tiny_canary_preflight_not_ready")
    if not manual_approval_material_ready:
        warnings.append("no_order_shadow_manual_approval_material_not_ready")
    if not manual_approval_granted:
        warnings.append("manual_shadow_start_approval_missing")
    if real_no_order_shadow_eval:
        if not real_no_order_shadow_contract_passed:
            warnings.append("real_no_order_shadow_contract_eval_not_passed")
        elif not real_no_order_shadow_sample_sufficient:
            warnings.append("real_no_order_shadow_sample_below_research_evidence_floor")
    else:
        warnings.append("real_no_order_shadow_eval_not_present")
    if real_no_order_shadow_start_scope_eval:
        if not real_no_order_shadow_start_scope_validated:
            warnings.append("real_no_order_shadow_start_scope_not_validated")
    else:
        warnings.append("real_no_order_shadow_start_scope_eval_not_present")
    if not private_truth_ready:
        warnings.append("historical_owner_private_truth_unavailable")

    strategy_research_ready = bool(
        install_ok
        and l2_top_aligned_contract_ok
        and completion_adapter_ready
        and strict_rescue_ready
        and merge_turnover_ready
        and residual_risk_ready
        and coverage_scorecard_ready
        and xuan_candidate_rescore_ready
        and capital_ledger_ready
        and btc_overlap_decomposition_ready
    )
    strategy_research_readiness_level = (
        "partial"
        if strategy_research_ready and (not btc_parity_proven or not source_semantics_accepted or not xuan_bridge_complete)
        else "ready"
        if strategy_research_ready
        else "blocked"
    )
    shadow_design_ready = bool(
        strategy_research_ready
        and same_window_handoff_tiered_ready
        and shadow_design_packet_ready
        and shadow_design_gate_ready
        and capital_ledger_ready
    )
    shadow_start_blockers = (
        no_order_shadow_start_preflight.get("remaining_blockers")
        if isinstance(no_order_shadow_start_preflight.get("remaining_blockers"), list)
        else []
    )
    if manual_approval_granted:
        shadow_start_blockers = [
            blocker for blocker in shadow_start_blockers if blocker != "manual_shadow_start_approval_missing"
        ]
    if not shadow_start_preflight_spec_ready and "shadow_start_preflight_spec_not_ready" not in shadow_start_blockers:
        shadow_start_blockers.append("shadow_start_preflight_spec_not_ready")
    if (
        not no_order_shadow_start_engineering_ready
        and "no_order_shadow_start_engineering_preflight_not_ready" not in shadow_start_blockers
    ):
        shadow_start_blockers.append("no_order_shadow_start_engineering_preflight_not_ready")
    if (
        not manual_approval_material_ready
        and "no_order_shadow_manual_approval_material_not_ready" not in shadow_start_blockers
    ):
        shadow_start_blockers.append("no_order_shadow_manual_approval_material_not_ready")
    if (
        not real_no_order_shadow_start_scope_validated
        and "real_no_order_shadow_start_scope_not_validated" not in shadow_start_blockers
    ):
        shadow_start_blockers.append("real_no_order_shadow_start_scope_not_validated")
    if not manual_approval_granted and "manual_shadow_start_approval_missing" not in shadow_start_blockers:
        shadow_start_blockers.append("manual_shadow_start_approval_missing")
    shadow_start_preconditions_met = bool(
        shadow_design_ready
        and shadow_start_preflight_spec_ready
        and no_order_shadow_start_engineering_ready
        and manual_approval_material_ready
        and real_no_order_shadow_start_scope_validated
        and manual_approval_granted
        and no_order_shadow_start_conflict_check_passed
        and not shadow_start_blockers
    )
    shadow_start_ready = False
    if shadow_start_preconditions_met:
        warnings.append("shadow_start_preconditions_met_but_runner_start_held_by_control_plane")
    strategy_promotion_ready = False
    status = (
        "KEEP_XUAN_BACKTEST_V1_REAL_NO_ORDER_SHADOW_SAMPLE_SUFFICIENT_PROMOTION_BLOCKED_OWNER_TRUTH"
        if real_no_order_shadow_sample_sufficient
        else "KEEP_XUAN_BACKTEST_V1_REAL_NO_ORDER_SHADOW_CONTRACT_PASS_SAMPLE_THIN_PROMOTION_BLOCKED_OWNER_TRUTH"
        if real_no_order_shadow_contract_passed
        else "PARTIAL_XUAN_BACKTEST_V1_SHADOW_DESIGN_READY_PROMOTION_BLOCKED_OWNER_TRUTH"
        if shadow_design_ready
        else
        "PARTIAL_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_READY_NOT_PROMOTION"
        if strategy_research_ready and strategy_research_readiness_level == "partial"
        else "OK_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_READY"
        if strategy_research_ready
        else "BLOCKED_XUAN_BACKTEST_V1_STRATEGY_RESEARCH_NOT_READY"
    )

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "xuan_backtest_v1_strategy_readiness_gate_v1",
        "created_utc": utc_now(),
        "status": status,
        "install_ok": install_ok,
        "l2_top_aligned_contract_ok": l2_top_aligned_contract_ok,
        "btc_parity_proven": btc_parity_proven,
        "source_semantics_accepted": source_semantics_accepted,
        "completion_adapter_ready": completion_adapter_ready,
        "strict_rescue_ready": strict_rescue_ready,
        "merge_turnover_ready": merge_turnover_ready,
        "residual_risk_ready": residual_risk_ready,
        "coverage_scorecard_ready": coverage_scorecard_ready,
        "xuan_candidate_rescore_ready": xuan_candidate_rescore_ready,
        "capital_ledger_ready": capital_ledger_ready,
        "xuan_bridge_complete": xuan_bridge_complete,
        "btc_tiny_canary_no_order_shadow_eval_passed": btc_tiny_canary_no_order_shadow_eval_passed,
        "no_order_shadow_manual_approval_packet_ready": no_order_shadow_manual_approval_packet_ready,
        "manual_approval_granted": manual_approval_granted,
        "no_order_shadow_eval_contract_passed": no_order_shadow_eval_contract_passed,
        "manual_approval_material_ready": manual_approval_material_ready,
        "real_no_order_shadow_contract_passed": real_no_order_shadow_contract_passed,
        "real_no_order_shadow_sample_sufficient": real_no_order_shadow_sample_sufficient,
        "real_no_order_shadow_start_scope_validated": real_no_order_shadow_start_scope_validated,
        "private_truth_ready": private_truth_ready,
        "private_promotion_ready_count": 0,
        "strategy_research_ready": strategy_research_ready,
        "strategy_research_readiness_level": strategy_research_readiness_level,
        "shadow_design_ready": shadow_design_ready,
        "shadow_start_ready": shadow_start_ready,
        "shadow_start_preconditions_met": shadow_start_preconditions_met,
        "strategy_promotion_ready": strategy_promotion_ready,
        "readiness_layers": {
            "strategy_research_ready": {
                "ready": strategy_research_ready,
                "level": strategy_research_readiness_level,
                "requires_private_truth": False,
                "requirements": {
                    "install_ok": install_ok,
                    "l2_top_aligned_contract_ok": l2_top_aligned_contract_ok,
                    "completion_adapter_ready": completion_adapter_ready,
                    "strict_rescue_ready": strict_rescue_ready,
                    "merge_turnover_ready": merge_turnover_ready,
                    "residual_risk_ready": residual_risk_ready,
                    "coverage_scorecard_ready": coverage_scorecard_ready,
                    "xuan_candidate_rescore_ready": xuan_candidate_rescore_ready,
                    "capital_ledger_ready": capital_ledger_ready,
                    "btc_overlap_decomposition_ready": btc_overlap_decomposition_ready,
                },
            },
            "shadow_design_ready": {
                "ready": shadow_design_ready,
                "requires_private_truth": False,
                "requirements": {
                    "strategy_research_ready": strategy_research_ready,
                    "same_window_handoff_tiered_ready": same_window_handoff_tiered_ready,
                    "shadow_design_packet_ready": shadow_design_packet_ready,
                    "shadow_design_gate_ready": shadow_design_gate_ready,
                    "capital_ledger_ready": capital_ledger_ready,
                },
                "allowed_scope": "Design shadow/no-order strategy from search-safe plus same-window handoff and capital ledger.",
            },
            "no_order_shadow_proxy_evaluated": {
                "ready": btc_tiny_canary_no_order_shadow_eval_passed,
                "requires_private_truth": False,
                "requirements": {
                    "btc_tiny_canary_preflight_ready": btc_tiny_canary_preflight_ready,
                    "strict_three_file_report_schema_and_safety": btc_tiny_canary_no_order_shadow_eval_passed,
                    "private_truth_ready": False,
                    "strategy_promotion_ready": False,
                    "live_orders_allowed": False,
                },
                "allowed_scope": "Research-only public L2 proxy validation of no-order shadow report contract.",
            },
            "shadow_start_ready": {
                "ready": shadow_start_ready,
                "preconditions_met": shadow_start_preconditions_met,
                "requires_private_truth": False,
                "preflight_spec_ready": shadow_start_preflight_spec_ready,
                "engineering_preflight_ready": no_order_shadow_start_engineering_ready,
                "no_order_shadow_proxy_eval_passed": btc_tiny_canary_no_order_shadow_eval_passed,
                "post_run_real_no_order_shadow_contract_passed": real_no_order_shadow_contract_passed,
                "real_ws_start_scope_validated": real_no_order_shadow_start_scope_validated,
                "manual_approval_packet_ready": no_order_shadow_manual_approval_packet_ready,
                "manual_approval_material_ready": manual_approval_material_ready,
                "manual_approval_granted": manual_approval_granted,
                "active_runner_conflict_check_passed": no_order_shadow_start_conflict_check_passed,
                "requires_user_approval": True,
                "requires_active_runner_conflict_check": True,
                "dry_run_only": True,
                "orders_sent_initially": False,
                "live_orders_allowed": False,
                "blockers": shadow_start_blockers,
                "runner_config": (no_order_shadow_start_preflight.get("runner_config") or {}),
            },
            "manual_approval_packet_ready": {
                "ready": no_order_shadow_manual_approval_packet_ready,
                "requires_private_truth": False,
                "manual_approval_granted": manual_approval_granted,
                "shadow_start_ready": False,
                "requirements": {
                    "start_preflight_engineering_ready": no_order_shadow_start_engineering_ready,
                    "public_l2_proxy_no_order_eval_passed": btc_tiny_canary_no_order_shadow_eval_passed,
                    "active_runner_conflict_check_passed": no_order_shadow_start_conflict_check_passed,
                    "promotion_private_live_gates_false": manual_approval_material_ready,
                },
                "post_run_real_no_order_shadow_contract_passed": real_no_order_shadow_contract_passed,
                "allowed_scope": "Human review packet only; it cannot start a runner or grant approval.",
            },
            "manual_approval_decision": {
                "ready": manual_approval_granted,
                "requires_private_truth": False,
                "approval_decision_status": no_order_shadow_manual_approval_decision.get("status")
                or "MISSING",
                "runner_start_allowed_by_approval_gate": no_order_shadow_manual_approval_decision.get(
                    "runner_start_allowed_by_approval_gate"
                )
                is True,
                "runner_started": False,
                "required_approval_text_available": bool(
                    no_order_shadow_manual_approval_decision.get("required_approval_text")
                ),
                "allowed_scope": "Approval gate only; the decision artifact does not start a runner.",
            },
            "real_no_order_shadow_start_scope_validated": {
                "ready": real_no_order_shadow_start_scope_validated,
                "requires_private_truth": False,
                "approval_required": True,
                "observed": {
                    "status": real_no_order_shadow_start_scope_eval.get("status") or "MISSING",
                    "scope_kind": real_no_order_shadow_start_scope_eval.get("scope_kind"),
                    "row_count": as_int(real_no_order_shadow_start_scope_summary.get("row_count")),
                    "candidate_count": as_int(
                        real_no_order_shadow_start_scope_summary.get("candidate_count")
                    ),
                    "market_count": as_int(real_no_order_shadow_start_scope_summary.get("market_count")),
                    "ws_start_scope_validated": real_no_order_shadow_start_scope_summary.get(
                        "ws_start_scope_validated"
                    ),
                    "start_authorizing": real_no_order_shadow_start_scope_summary.get(
                        "start_authorizing"
                    ),
                    "threshold_failure_count": real_no_order_shadow_start_scope_summary.get(
                        "threshold_failure_count"
                    ),
                    "failed_thresholds": real_no_order_shadow_start_scope_summary.get(
                        "failed_thresholds"
                    )
                    or [],
                },
                "requirements": {
                    "start_scope_eval_passed": real_no_order_shadow_start_scope_validated,
                    "start_authorizing": False,
                    "private_truth_ready": False,
                    "strategy_promotion_ready": False,
                    "live_orders_allowed": False,
                },
                "allowed_scope": (
                    "Validates the BTC [1,2,3] read-only/no-order WS start scope package only. "
                    "It does not authorize start by itself and cannot set private truth, promotion, live, or deployable gates."
                ),
            },
            "real_no_order_shadow_contract_evaluated": {
                "ready": real_no_order_shadow_contract_passed,
                "sample_sufficient": real_no_order_shadow_sample_sufficient,
                "requires_private_truth": False,
                "evidence_floor": {
                    "min_rows": REAL_NO_ORDER_SHADOW_MIN_ROWS_FOR_RESEARCH_EVIDENCE,
                    "min_candidates": REAL_NO_ORDER_SHADOW_MIN_CANDIDATES_FOR_RESEARCH_EVIDENCE,
                    "min_markets": REAL_NO_ORDER_SHADOW_MIN_MARKETS_FOR_RESEARCH_EVIDENCE,
                },
                "observed": {
                    "status": real_no_order_shadow_eval.get("status") or "MISSING",
                    "row_count": real_no_order_shadow_row_count,
                    "candidate_count": real_no_order_shadow_candidate_count,
                    "market_count": real_no_order_shadow_market_count,
                    "threshold_failure_count": real_no_order_shadow_eval_summary.get(
                        "threshold_failure_count"
                    ),
                    "failed_thresholds": real_no_order_shadow_eval_summary.get("failed_thresholds")
                    or [],
                },
                "requirements": {
                    "strict_three_file_report_schema_and_safety": real_no_order_shadow_contract_passed,
                    "no_order_shadow_real_runner_evaluated": real_no_order_shadow_eval_summary.get(
                        "no_order_shadow_real_runner_evaluated"
                    )
                    is True,
                    "private_truth_ready": False,
                    "strategy_promotion_ready": False,
                    "live_orders_allowed": False,
                },
                "allowed_scope": (
                    "Real read-only public book/no-order runner contract validation. "
                    "Sample sufficiency is a separate research-evidence floor and still cannot set promotion/private truth."
                ),
            },
            "strategy_promotion_ready": {
                "ready": strategy_promotion_ready,
                "requires_private_truth": True,
                "owner_private_truth_ready": private_truth_ready,
                "historical_shadow_or_v1_is_private_truth": False,
                "requirements": {
                    "future_owner_orders_fills_inventory_redeem_fee_pnl_reconciled": False,
                    "private_truth_ready": private_truth_ready,
                    "strategy_research_ready": strategy_research_ready,
                    "shadow_design_ready": shadow_design_ready,
                    "shadow_start_ready": shadow_start_ready,
                },
            },
        },
        "deployable": False,
        "live_orders_allowed": False,
        "l2_top_aligned_contract": l2_contract,
        "summary": {
            "install_gate_status": install.get("status") or "MISSING",
            "install_fail_count": install_summary.get("fail_count"),
            "external_polydata_runtime_ref_count": install_summary.get("external_polydata_runtime_ref_count"),
            "btc_parity_status": btc_parity.get("status") or "MISSING",
            "xuan_bridge_status": xuan_bridge.get("status") or "MISSING",
            "xuan_bridge_category_counts": {
                "queue_screener_search_safe_count": bridge_summary.get("queue_screener_search_safe_count"),
                "completion_adapter_research_count": bridge_summary.get("completion_adapter_research_count"),
                "xuan_compatible_bridge_count": bridge_summary.get("xuan_compatible_bridge_count"),
            },
            "multiasset_completion_status": multiasset_completion.get("status") or "MISSING",
            "multiasset_strict_rescue_status": multiasset_strict_rescue.get("status") or "MISSING",
            "multiasset_merge_turnover_status": multiasset_merge_turnover.get("status") or "MISSING",
            "coverage_scorecard_status": coverage_scorecard.get("status") or "MISSING",
            "xuan_candidate_rescore_status": candidate_rescore.get("status") or "MISSING",
            "positive_xuan_candidate_count": (candidate_rescore.get("summary") or {}).get(
                "positive_xuan_candidate_count"
            ),
            "capital_ledger_status": capital_ledger.get("status") or "MISSING",
            "max_capital_tied": (capital_ledger.get("summary") or {}).get("max_capital_tied"),
            "daily_capacity_estimate_at_1000": (capital_ledger.get("summary") or {}).get(
                "daily_capacity_estimate_at_notional"
            ),
            "btc_overlap_decomposition_status": btc_overlap_decomposition.get("status") or "MISSING",
            "btc_tiny_canary_preflight_status": btc_tiny_canary_preflight.get("status") or "MISSING",
            "btc_tiny_canary_preflight_ready": btc_tiny_canary_preflight_ready,
            "btc_tiny_canary_no_order_shadow_eval_status": btc_tiny_canary_no_order_shadow_eval.get("status")
            or "MISSING",
            "btc_tiny_canary_no_order_shadow_eval_passed": btc_tiny_canary_no_order_shadow_eval_passed,
            "btc_tiny_canary_no_order_shadow_eval_summary": btc_tiny_canary_no_order_shadow_eval.get("summary")
            or {},
            "no_order_shadow_eval_contract_passed": no_order_shadow_eval_contract_passed,
            "same_window_handoff_tiered_status": same_window_handoff_tiered.get("status") or "MISSING",
            "shadow_design_packet_status": shadow_design_packet.get("status") or "MISSING",
            "shadow_readiness_gate_status": shadow_readiness_gate.get("status") or "MISSING",
            "shadow_start_preflight_status": shadow_start_preflight.get("status") or "MISSING",
            "no_order_shadow_start_preflight_status": no_order_shadow_start_preflight.get("status") or "MISSING",
            "no_order_shadow_start_engineering_preflight_ready": no_order_shadow_start_engineering_ready,
            "no_order_shadow_start_remaining_blockers": shadow_start_blockers,
            "no_order_shadow_manual_approval_packet_status": no_order_shadow_manual_approval_packet.get("status")
            or "MISSING",
            "no_order_shadow_manual_approval_packet_ready": no_order_shadow_manual_approval_packet_ready,
            "manual_approval_material_ready": manual_approval_material_ready,
            "no_order_shadow_manual_approval_decision_status": no_order_shadow_manual_approval_decision.get(
                "status"
            )
            or "MISSING",
            "manual_approval_granted": manual_approval_granted,
            "runner_start_allowed_by_approval_gate": no_order_shadow_manual_approval_decision.get(
                "runner_start_allowed_by_approval_gate"
            ),
            "real_no_order_shadow_eval_status": real_no_order_shadow_eval.get("status") or "MISSING",
            "real_no_order_shadow_contract_passed": real_no_order_shadow_contract_passed,
            "real_no_order_shadow_sample_sufficient": real_no_order_shadow_sample_sufficient,
            "real_no_order_shadow_eval_summary": real_no_order_shadow_eval_summary,
            "real_no_order_shadow_start_scope_eval_status": real_no_order_shadow_start_scope_eval.get(
                "status"
            )
            or "MISSING",
            "real_no_order_shadow_start_scope_validated": real_no_order_shadow_start_scope_validated,
            "real_no_order_shadow_start_scope_eval_summary": real_no_order_shadow_start_scope_summary,
        },
        "blockers": blockers,
        "warnings": warnings,
        "policy": {
            "queue_pnl_is_strategy_pnl": False,
            "same_window_handoff_is_research_material_only": True,
            "research_ranking_is_promotion_gate": False,
            "residual_settlement_pnl_is_strategy_edge": False,
            "redeem_is_settlement_action_not_strategy_edge": True,
            "historical_shadow_private_truth_can_be_inferred": False,
            "historical_public_or_shadow_can_set_private_truth_ready": False,
            "promotion_requires_future_owner_execution_truth": True,
            "candidate_handoff_flow": [
                "search-safe candidate",
                "same-window L2/top-aligned validation",
                "xuan completion/residual rescore",
                "future owner canary/live-small execution",
                "owner orders/fills/inventory/redeem/fee reconciliation",
                "private truth gate",
            ],
            "historical_shadow_can_skip_owner_truth_gate": False,
        },
        "inputs": {
            "install_gate": str(args.install_gate.expanduser()),
            "l2_top_manifest": str(args.l2_top_manifest.expanduser()),
            "btc_parity_gate": str(args.btc_parity_gate.expanduser()),
            "xuan_bridge_scorecard": str(args.xuan_bridge_scorecard.expanduser()),
            "multiasset_completion_manifest": str(args.multiasset_completion_manifest.expanduser()),
            "multiasset_strict_rescue_report": str(args.multiasset_strict_rescue_report.expanduser()),
            "multiasset_merge_turnover_report": str(args.multiasset_merge_turnover_report.expanduser()),
            "coverage_scorecard": str(args.coverage_scorecard.expanduser()),
            "xuan_candidate_rescore": str(args.xuan_candidate_rescore.expanduser()),
            "xuan_capital_ledger": str(args.xuan_capital_ledger.expanduser()),
            "btc_overlap_decomposition": str(args.btc_overlap_decomposition.expanduser()),
            "btc_tiny_canary_preflight": str(args.btc_tiny_canary_preflight.expanduser()),
            "btc_tiny_canary_no_order_shadow_eval": str(
                args.btc_tiny_canary_no_order_shadow_eval.expanduser()
            ),
            "same_window_handoff_tiered": str(args.same_window_handoff_tiered.expanduser()),
            "shadow_design_packet": str(args.shadow_design_packet.expanduser()),
            "shadow_readiness_gate": str(args.shadow_readiness_gate.expanduser()),
            "shadow_start_preflight": str(args.shadow_start_preflight.expanduser()),
            "no_order_shadow_start_preflight": str(args.no_order_shadow_start_preflight.expanduser()),
            "no_order_shadow_manual_approval_packet": str(
                args.no_order_shadow_manual_approval_packet.expanduser()
            ),
            "no_order_shadow_manual_approval_decision": str(
                args.no_order_shadow_manual_approval_decision.expanduser()
            ),
            "real_no_order_shadow_eval": str(args.real_no_order_shadow_eval.expanduser()),
            "real_no_order_shadow_start_scope_eval": str(
                args.real_no_order_shadow_start_scope_eval.expanduser()
            ),
        },
    }
    manifest_path = output_dir / "XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "strategy_research_ready": manifest["strategy_research_ready"],
                "strategy_research_readiness_level": manifest["strategy_research_readiness_level"],
                "shadow_design_ready": manifest["shadow_design_ready"],
                "shadow_start_ready": manifest["shadow_start_ready"],
                "strategy_promotion_ready": manifest["strategy_promotion_ready"],
                "blockers": blockers,
                "warnings": warnings,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if strategy_research_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

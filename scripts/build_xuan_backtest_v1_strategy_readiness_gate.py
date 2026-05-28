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


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-gate", type=Path, default=DEFAULT_INSTALL_GATE)
    parser.add_argument("--l2-top-manifest", type=Path, default=DEFAULT_L2_TOP)
    parser.add_argument("--btc-parity-gate", type=Path, default=DEFAULT_BTC_PARITY)
    parser.add_argument("--xuan-bridge-scorecard", type=Path, default=DEFAULT_XUAN_BRIDGE)
    parser.add_argument("--multiasset-completion-manifest", type=Path, default=DEFAULT_MULTIASSET_COMPLETION)
    parser.add_argument("--multiasset-strict-rescue-report", type=Path, default=DEFAULT_MULTIASSET_STRICT_RESCUE)
    parser.add_argument("--multiasset-merge-turnover-report", type=Path, default=DEFAULT_MULTIASSET_MERGE_TURNOVER)
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
    if not btc_parity_proven:
        warnings.append("btc_baseline_parity_not_proven")
    if not source_semantics_accepted:
        warnings.append("btc_source_semantics_not_explicitly_accepted")
    if not xuan_bridge_complete:
        warnings.append("xuan_bridge_still_partial")
    if not private_truth_ready:
        warnings.append("historical_owner_private_truth_unavailable")

    strategy_research_ready = bool(
        install_ok
        and l2_top_aligned_contract_ok
        and completion_adapter_ready
        and strict_rescue_ready
        and merge_turnover_ready
        and residual_risk_ready
    )
    strategy_research_readiness_level = (
        "partial"
        if strategy_research_ready and (not btc_parity_proven or not source_semantics_accepted or not xuan_bridge_complete)
        else "ready"
        if strategy_research_ready
        else "blocked"
    )
    strategy_promotion_ready = False
    status = (
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
        "xuan_bridge_complete": xuan_bridge_complete,
        "private_truth_ready": private_truth_ready,
        "private_promotion_ready_count": 0,
        "strategy_research_ready": strategy_research_ready,
        "strategy_research_readiness_level": strategy_research_readiness_level,
        "strategy_promotion_ready": strategy_promotion_ready,
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
        },
        "blockers": blockers,
        "warnings": warnings,
        "policy": {
            "queue_pnl_is_strategy_pnl": False,
            "redeem_is_settlement_action_not_strategy_edge": True,
            "historical_shadow_private_truth_can_be_inferred": False,
            "promotion_requires_future_owner_execution_truth": True,
        },
        "inputs": {
            "install_gate": str(args.install_gate.expanduser()),
            "l2_top_manifest": str(args.l2_top_manifest.expanduser()),
            "btc_parity_gate": str(args.btc_parity_gate.expanduser()),
            "xuan_bridge_scorecard": str(args.xuan_bridge_scorecard.expanduser()),
            "multiasset_completion_manifest": str(args.multiasset_completion_manifest.expanduser()),
            "multiasset_strict_rescue_report": str(args.multiasset_strict_rescue_report.expanduser()),
            "multiasset_merge_turnover_report": str(args.multiasset_merge_turnover_report.expanduser()),
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

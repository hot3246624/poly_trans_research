#!/usr/bin/env python3
"""Build CE25 target_qty=8 evidence-policy review packet."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STRATEGY_INPUT = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604/"
    "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json"
)
DEFAULT_SOURCE_BRIDGE_SUMMARY = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_source_bridge_20260604/"
    "CE25_TARGET_QTY8_SOURCE_BRIDGE_SUMMARY.json"
)
DEFAULT_SOURCE_BRIDGE_MANIFEST = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_source_bridge_20260604/"
    "CE25_TARGET_QTY8_SOURCE_BRIDGE_HASH_MANIFEST.json"
)
DEFAULT_ATTRIBUTION_SUMMARY = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604/"
    "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_SUMMARY.json"
)
DEFAULT_ATTRIBUTION_MANIFEST = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604/"
    "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_HASH_MANIFEST.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_evidence_policy_packet_20260604"
)

STATUS = "KEEP_CE25_TARGET_QTY8_CANONICAL_L1_EVIDENCE_POLICY_ACCEPTED_REVIEW_ONLY_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"
EVIDENCE_POLICY_ID = "CANONICAL_L1_TOP1_DEPTH_ACCEPTED_FOR_LOCAL_REVIEW_ONLY_V1"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "canary_authorized": False,
        "orders_authorized": False,
    }


def assert_preconditions(strategy: dict[str, Any], bridge: dict[str, Any], attribution: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if strategy.get("strategy_id") != STRATEGY_ID:
        errors.append("strategy_id_mismatch")
    if strategy.get("strategy_owner_line") != OWNER_LINE:
        errors.append("owner_line_mismatch")
    if strategy.get("candidate_count") != 134:
        errors.append("candidate_count_not_134")
    if strategy.get("expected_market_count") != 129:
        errors.append("market_count_not_129")
    if bridge.get("status") != "KEEP_CE25_TARGET_QTY8_SOURCE_BRIDGE_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY":
        errors.append("source_bridge_not_keep")
    if bridge.get("failed_row_count") != 0 or bridge.get("row_error_count") != 0:
        errors.append("source_bridge_row_errors")
    if bridge.get("candidate_base_rows_loaded") != 134:
        errors.append("candidate_base_row_count_mismatch")
    if bridge.get("l2_mart_rows_loaded") != 268:
        errors.append("l2_mart_row_count_mismatch")
    if attribution.get("status") != "KEEP_CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_REVIEW_REQUIRED_NOT_OOS_READY":
        errors.append("attribution_not_keep")
    if attribution.get("all_l1_top_pair_pass") is not True:
        errors.append("l1_top_pair_not_all_pass")
    if attribution.get("all_top1_depth_pair_fillable") is not True:
        errors.append("top1_depth_not_all_fillable")
    if attribution.get("all_l2_fail_reason_pass") is not True:
        errors.append("l2_fail_reason_not_all_pass")
    counts = attribution.get("dependency_category_counts") or {}
    if counts.get("NO_OVERLAY_RAW_L2_OK") != 5:
        errors.append("strict_raw_l2_clean_count_not_5")
    if any(strategy.get("non_claims", {}).get(k) is not False for k in non_claims()):
        errors.append("strategy_non_claims_not_false")
    if any(bridge.get("non_claims", {}).get(k) is not False for k in non_claims()):
        errors.append("bridge_non_claims_not_false")
    if any(attribution.get("non_claims", {}).get(k) is not False for k in non_claims()):
        errors.append("attribution_non_claims_not_false")
    return errors


def build_packet(args: argparse.Namespace) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    strategy_path = args.strategy_input.expanduser().resolve()
    bridge_summary_path = args.source_bridge_summary.expanduser().resolve()
    bridge_manifest_path = args.source_bridge_manifest.expanduser().resolve()
    attribution_summary_path = args.attribution_summary.expanduser().resolve()
    attribution_manifest_path = args.attribution_manifest.expanduser().resolve()

    strategy = read_json(strategy_path)
    bridge = read_json(bridge_summary_path)
    attribution = read_json(attribution_summary_path)
    errors = assert_preconditions(strategy, bridge, attribution)
    if errors:
        raise SystemExit(json.dumps({"ok": False, "errors": errors}, indent=2, sort_keys=True))

    created_at = ""
    try:
        created_at = read_json(Path(strategy["source_artifacts"]["l2_validation_manifest"])).get("created_at", "")
    except Exception:
        created_at = ""

    evidence_policy = {
        "schema_version": 1,
        "status": STATUS,
        "evidence_policy_id": EVIDENCE_POLICY_ID,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "decision": "ACCEPT_CANONICAL_L1_TOP1_DEPTH_FOR_LOCAL_REVIEW_ONLY",
        "decision_scope": {
            "allowed": [
                "local_replay_research_review",
                "historical_replay_bound_strategy_packet_preparation",
                "source_bridge_and_attribution_review",
            ],
            "not_allowed": [
                "OOS_pass",
                "runner_or_observer_start",
                "private_truth_ready",
                "strategy_promotion_ready",
                "live_ready",
                "deployable",
                "canary_or_live_orders",
                "private_key_or_candidate_import",
            ],
        },
        "evidence_basis": {
            "candidate_count": strategy["candidate_count"],
            "market_count": strategy["expected_market_count"],
            "source_bridge_status": bridge["status"],
            "source_bridge_failed_rows": bridge["failed_row_count"],
            "l1_top_pair_pass_all": attribution["all_l1_top_pair_pass"],
            "top1_depth_pair_fillable_all": attribution["all_top1_depth_pair_fillable"],
            "l2_fail_reason_pass_all": attribution["all_l2_fail_reason_pass"],
            "dependency_category_counts": attribution["dependency_category_counts"],
            "raw_l2_age_ok_pair_count": attribution["raw_l2_age_ok_pair_count"],
            "top_overlay_required_count": attribution["top_overlay_required_count"],
        },
        "evidence_policy_constraints": {
            "canonical_l1_top_is_primary_review_evidence": True,
            "raw_l2_top5_depth_is_supporting_review_evidence": True,
            "top_overlay_required_is_review_blocker_for_oos": True,
            "raw_l2_stale_is_review_blocker_for_oos": True,
            "strict_no_overlay_raw_l2_fresh_subset_action_count": attribution["dependency_category_counts"].get(
                "NO_OVERLAY_RAW_L2_OK"
            ),
            "strict_no_overlay_raw_l2_fresh_subset_interpretation": "too_low_coverage_for_full_strategy",
        },
        "fail_closed_rules": [
            "strategy_input_hash_or_candidate_count_drift",
            "source_bridge_status_not_keep",
            "source_bridge_failed_row_count_nonzero",
            "l1_top_pair_pass_not_all_true",
            "top1_depth_pair_fillable_not_all_true",
            "l2_fail_reason_not_all_pass",
            "non_claims_not_all_false",
            "any_attempt_to_treat_packet_as_oos_live_private_truth_or_deployable",
        ],
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    threshold_spec = {
        "schema_version": 1,
        "status": "REVIEW_ONLY_THRESHOLDS_NOT_OOS",
        "strategy_id": STRATEGY_ID,
        "required_local_review_gates": {
            "candidate_count": 134,
            "market_count": 129,
            "source_bridge_failed_row_count": 0,
            "source_bridge_row_error_count": 0,
            "l1_top_pair_pass_all": True,
            "top1_depth_pair_fillable_all": True,
            "l2_fail_reason_pass_all": True,
            "fee_3pct_roi_min": 0.0,
            "profitable_active_days_min": 15,
        },
        "explicit_non_gates_for_oos": {
            "no_overlay_raw_l2_fresh_subset_action_count": 5,
            "top_overlay_required_count": 84,
            "raw_l2_stale_pair_count": 46,
            "reason": "coverage/freshness insufficient for OOS-style evidence",
        },
        "non_claims": non_claims(),
    }

    strategy_packet = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version": strategy["strategy_version"],
        "strategy_family": strategy["strategy_family"],
        "evidence_policy_id": EVIDENCE_POLICY_ID,
        "candidate_input_mode": "EXTERNAL_FILE",
        "candidate_count": strategy["candidate_count"],
        "expected_market_count": strategy["expected_market_count"],
        "candidate_csv_sha256": strategy["candidate_csv_sha256"],
        "strategy_input_sha256": sha256_file(strategy_path),
        "source_bridge_summary_sha256": sha256_file(bridge_summary_path),
        "source_bridge_manifest_sha256": sha256_file(bridge_manifest_path),
        "attribution_summary_sha256": sha256_file(attribution_summary_path),
        "attribution_manifest_sha256": sha256_file(attribution_manifest_path),
        "policy_contract": strategy["policy_contract"],
        "evidence_policy_decision": evidence_policy["decision"],
        "next_review_step": "historical_replay_bound_strategy_packet_review_or_tighter_raw_l2_subset_research",
        "execution_approval": "NOT_ISSUED",
        "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    return evidence_policy, threshold_spec, strategy_packet, created_at or datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-input", type=Path, default=DEFAULT_STRATEGY_INPUT)
    parser.add_argument("--source-bridge-summary", type=Path, default=DEFAULT_SOURCE_BRIDGE_SUMMARY)
    parser.add_argument("--source-bridge-manifest", type=Path, default=DEFAULT_SOURCE_BRIDGE_MANIFEST)
    parser.add_argument("--attribution-summary", type=Path, default=DEFAULT_ATTRIBUTION_SUMMARY)
    parser.add_argument("--attribution-manifest", type=Path, default=DEFAULT_ATTRIBUTION_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    evidence_policy, threshold_spec, strategy_packet, created_at = build_packet(args)

    decision_path = output_dir / "CE25_TARGET_QTY8_EVIDENCE_POLICY_DECISION.json"
    threshold_path = output_dir / "CE25_TARGET_QTY8_THRESHOLD_SPEC.json"
    packet_path = output_dir / "CE25_TARGET_QTY8_REVIEW_ONLY_STRATEGY_PACKET.json"
    command_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    boundary_path = output_dir / "CE25_TARGET_QTY8_BOUNDARY_NOTE.md"
    manifest_path = output_dir / "CE25_TARGET_QTY8_EVIDENCE_POLICY_PACKET_HASH_MANIFEST.json"

    write_json(decision_path, evidence_policy)
    write_json(threshold_path, threshold_spec)
    write_json(packet_path, strategy_packet)
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 target_qty8 packet is review-only; no OOS/live/runner command is issued.' >&2",
                "exit 66",
                "",
            ]
        )
    )
    boundary_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Evidence Policy Boundary",
                "",
                f"Status: `{STATUS}`",
                "",
                "Canonical L1 top1 depth evidence is accepted only for local historical replay review.",
                "This packet does not authorize OOS, runner/observer start, private key loading, import, order/cancel/redeem, canary/live/deploy/funding, or latest pointer updates.",
                "The raw-L2-fresh and no-overlay strict subset has only 5 actions, so this packet cannot be interpreted as full OOS-ready evidence.",
                "",
            ]
        )
    )

    artifacts = [
        decision_path,
        threshold_path,
        packet_path,
        command_path,
        boundary_path,
        args.strategy_input.expanduser().resolve(),
        args.source_bridge_summary.expanduser().resolve(),
        args.source_bridge_manifest.expanduser().resolve(),
        args.attribution_summary.expanduser().resolve(),
        args.attribution_manifest.expanduser().resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "created_at": created_at,
        "status": STATUS,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "non_claims": non_claims(),
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(output_dir),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

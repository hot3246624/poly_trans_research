#!/usr/bin/env python3
"""Build CE25 target_qty=8 historical replay-bound strategy review packet.

This packet intentionally stops before OOS. It binds the replay candidate ledger,
source bridge, overlay/freshness attribution, and evidence-policy decision into
one review artifact with an explicit OOS gap assessment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"

DEFAULT_LEDGER_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604"
DEFAULT_SOURCE_BRIDGE_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_source_bridge_20260604"
DEFAULT_ATTRIBUTION_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604"
DEFAULT_POLICY_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_evidence_policy_packet_20260604"
DEFAULT_OFFICIAL_FEE_RECALC_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
DEFAULT_OUTPUT_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_historical_replay_review_packet_20260604"

STATUS = "KEEP_CE25_TARGET_QTY8_HISTORICAL_REPLAY_REVIEW_PACKET_ACCEPTED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"
POLICY_ID = "CANONICAL_L1_TOP1_DEPTH_ACCEPTED_FOR_LOCAL_REVIEW_ONLY_V1"


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
        "oos_authorized": False,
    }


def assert_false_claims(payload: dict[str, Any], label: str, errors: list[str]) -> None:
    claims = payload.get("non_claims") or {}
    for key in non_claims():
        if claims.get(key) is not False and key in claims:
            errors.append(f"{label}_{key}_not_false")


def ledger_stats(path: Path) -> dict[str, Any]:
    with path.open(newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    candidate_ids = [row.get("candidate_id", "") for row in rows]
    markets = [row.get("condition_id", "") for row in rows]
    required_bool_cols = [
        "l1_top_pair_pass",
        "l2_top_aligned_vwap_pass",
        "top1_depth_pair_fillable",
        "top5_depth_pair_fillable",
    ]
    return {
        "row_count": len(rows),
        "unique_candidate_count": len(set(candidate_ids)),
        "unique_market_count": len(set(markets)),
        "duplicate_candidate_count": len(candidate_ids) - len(set(candidate_ids)),
        "all_required_bool_cols_true": all(
            str(row.get(col, "")).lower() == "true"
            for row in rows
            for col in required_bool_cols
        ),
        "all_fail_reason_pass": all(row.get("l2_top_aligned_fail_reason") == "PASS" for row in rows),
        "all_non_claim_flags_false": all(
            str(row.get(col, "")).lower() == "false"
            for row in rows
            for col in ["private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable"]
        ),
        "first_day": min(row.get("day", "") for row in rows),
        "last_day": max(row.get("day", "") for row in rows),
    }


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    ledger_dir = args.ledger_dir.expanduser().resolve()
    source_bridge_dir = args.source_bridge_dir.expanduser().resolve()
    attribution_dir = args.attribution_dir.expanduser().resolve()
    policy_dir = args.policy_dir.expanduser().resolve()

    paths = {
        "candidate_csv": ledger_dir / "ce25_high_price_top1_qty_target_qty8_candidate_ledger.csv",
        "strategy_input": ledger_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json",
        "ledger_manifest": ledger_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_HASH_MANIFEST.json",
        "ledger_review_note": ledger_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_REVIEW_NOTE.md",
        "source_bridge_summary": source_bridge_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_SUMMARY.json",
        "source_bridge_manifest": source_bridge_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_HASH_MANIFEST.json",
        "source_bridge_row_audit": source_bridge_dir / "ce25_target_qty8_source_bridge_row_audit.csv",
        "source_bridge_note": source_bridge_dir / "CE25_TARGET_QTY8_SOURCE_BRIDGE_REVIEW_NOTE.md",
        "attribution_summary": attribution_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_SUMMARY.json",
        "attribution_manifest": attribution_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_HASH_MANIFEST.json",
        "attribution_audit": attribution_dir / "ce25_target_qty8_overlay_freshness_action_audit.csv",
        "attribution_note": attribution_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_NOTE.md",
        "evidence_policy_decision": policy_dir / "CE25_TARGET_QTY8_EVIDENCE_POLICY_DECISION.json",
        "evidence_policy_threshold": policy_dir / "CE25_TARGET_QTY8_THRESHOLD_SPEC.json",
        "evidence_policy_packet": policy_dir / "CE25_TARGET_QTY8_REVIEW_ONLY_STRATEGY_PACKET.json",
        "evidence_policy_manifest": policy_dir / "CE25_TARGET_QTY8_EVIDENCE_POLICY_PACKET_HASH_MANIFEST.json",
        "evidence_policy_boundary": policy_dir / "CE25_TARGET_QTY8_BOUNDARY_NOTE.md",
        "official_fee_recalc_summary": args.official_fee_recalc_dir.expanduser().resolve()
        / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json",
        "official_fee_recalc_manifest": args.official_fee_recalc_dir.expanduser().resolve()
        / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_HASH_MANIFEST.json",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise SystemExit(json.dumps({"ok": False, "errors": ["missing_inputs"], "missing": missing}, indent=2))

    payloads = {
        "strategy": read_json(paths["strategy_input"]),
        "ledger_manifest": read_json(paths["ledger_manifest"]),
        "source_bridge": read_json(paths["source_bridge_summary"]),
        "source_bridge_manifest": read_json(paths["source_bridge_manifest"]),
        "attribution": read_json(paths["attribution_summary"]),
        "attribution_manifest": read_json(paths["attribution_manifest"]),
        "policy": read_json(paths["evidence_policy_decision"]),
        "policy_threshold": read_json(paths["evidence_policy_threshold"]),
        "policy_packet": read_json(paths["evidence_policy_packet"]),
        "policy_manifest": read_json(paths["evidence_policy_manifest"]),
        "official_fee_recalc": read_json(paths["official_fee_recalc_summary"]),
        "official_fee_recalc_manifest": read_json(paths["official_fee_recalc_manifest"]),
        "ledger_stats": ledger_stats(paths["candidate_csv"]),
        "paths": paths,
    }
    return payloads


def validate_inputs(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    strategy = data["strategy"]
    bridge = data["source_bridge"]
    attribution = data["attribution"]
    policy = data["policy"]
    fee_recalc = data["official_fee_recalc"]
    stats = data["ledger_stats"]

    if strategy.get("strategy_id") != STRATEGY_ID:
        errors.append("strategy_id_mismatch")
    if strategy.get("strategy_owner_line") != OWNER_LINE:
        errors.append("owner_line_mismatch")
    if strategy.get("candidate_count") != 134 or stats["row_count"] != 134:
        errors.append("candidate_count_mismatch")
    if strategy.get("expected_market_count") != 129 or stats["unique_market_count"] != 129:
        errors.append("market_count_mismatch")
    if stats["duplicate_candidate_count"] != 0:
        errors.append("duplicate_candidate_ids")
    if stats["all_required_bool_cols_true"] is not True:
        errors.append("ledger_required_bool_cols_not_all_true")
    if stats["all_fail_reason_pass"] is not True:
        errors.append("ledger_fail_reason_not_all_pass")
    if stats["all_non_claim_flags_false"] is not True:
        errors.append("ledger_non_claim_flags_not_false")
    if sha256_file(data["paths"]["candidate_csv"]) != strategy.get("candidate_csv_sha256"):
        errors.append("candidate_csv_hash_mismatch")

    if bridge.get("status") != "KEEP_CE25_TARGET_QTY8_SOURCE_BRIDGE_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY":
        errors.append("source_bridge_status_not_keep")
    if bridge.get("failed_row_count") != 0 or bridge.get("row_error_count") != 0:
        errors.append("source_bridge_errors_nonzero")
    if bridge.get("candidate_base_rows_loaded") != 134 or bridge.get("l2_mart_rows_loaded") != 268:
        errors.append("source_bridge_source_counts_mismatch")

    if attribution.get("status") != "KEEP_CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_REVIEW_REQUIRED_NOT_OOS_READY":
        errors.append("attribution_status_not_keep")
    if attribution.get("all_l1_top_pair_pass") is not True:
        errors.append("attribution_l1_top_pair_not_all_pass")
    if attribution.get("all_top1_depth_pair_fillable") is not True:
        errors.append("attribution_top1_depth_not_all_fillable")
    if attribution.get("all_l2_fail_reason_pass") is not True:
        errors.append("attribution_l2_fail_reason_not_all_pass")
    counts = attribution.get("dependency_category_counts") or {}
    if counts.get("NO_OVERLAY_RAW_L2_OK") != 5:
        errors.append("strict_no_overlay_raw_l2_count_unexpected")

    if policy.get("status") != "KEEP_CE25_TARGET_QTY8_CANONICAL_L1_EVIDENCE_POLICY_ACCEPTED_REVIEW_ONLY_NOT_OOS_READY":
        errors.append("policy_status_not_keep")
    if policy.get("evidence_policy_id") != POLICY_ID:
        errors.append("policy_id_mismatch")
    if policy.get("decision") != "ACCEPT_CANONICAL_L1_TOP1_DEPTH_FOR_LOCAL_REVIEW_ONLY":
        errors.append("policy_decision_mismatch")
    if fee_recalc.get("status") != "KEEP_CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALCULATED_REVIEW_REQUIRED_NOT_OOS_READY":
        errors.append("official_fee_recalc_status_not_keep")
    if fee_recalc.get("official_crypto_taker_fee_rate") != 0.07:
        errors.append("official_fee_rate_not_0p07")
    if fee_recalc.get("candidate_count") != 134:
        errors.append("official_fee_candidate_count_mismatch")

    for label in ["strategy", "source_bridge", "attribution", "policy"]:
        assert_false_claims(data[label], label, errors)
    return errors


def build_outputs(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    strategy = data["strategy"]
    bridge = data["source_bridge"]
    attribution = data["attribution"]
    policy = data["policy"]
    fee_recalc = data["official_fee_recalc"]
    stats = data["ledger_stats"]
    paths = data["paths"]

    accepted_gates = {
        "historical_replay_candidate_ledger_bound": True,
        "candidate_count": strategy["candidate_count"],
        "market_count": strategy["expected_market_count"],
        "candidate_id_unique": stats["duplicate_candidate_count"] == 0,
        "source_bridge_row_errors": bridge["row_error_count"],
        "source_bridge_failed_rows": bridge["failed_row_count"],
        "canonical_l1_top_pair_pass_all": attribution["all_l1_top_pair_pass"],
        "top1_depth_pair_fillable_all": attribution["all_top1_depth_pair_fillable"],
        "l2_fail_reason_pass_all": attribution["all_l2_fail_reason_pass"],
        "evidence_policy_id": POLICY_ID,
        "evidence_policy_scope": "LOCAL_HISTORICAL_REPLAY_REVIEW_ONLY",
    }

    oos_blockers = [
        {
            "blocker": "historical_replay_binding_not_current_future_oos_targets",
            "why": "candidate rows are bound to historical replay actions, not fresh target windows.",
            "required_to_clear": "projection/materialization or direct current/future target binding with stale_target_count=0 before OOS start",
        },
        {
            "blocker": "canonical_l1_evidence_policy_not_oos_grade_top_depth_contract",
            "why": "canonical L1 top1 depth is accepted only for local review; top overlay/raw-L2 stale dependencies remain OOS blockers.",
            "required_to_clear": "fresh public observation contract with server/direct top-depth evidence, no inferred/overlay-only top book, and token-side completeness",
        },
        {
            "blocker": "strict_no_overlay_raw_l2_fresh_subset_too_small",
            "why": "only 5 of 134 actions are NO_OVERLAY_RAW_L2_OK; the full branch relies on overlay or stale raw-L2 support.",
            "required_to_clear": "new OOS-grade evidence capture over the full candidate family, or a separate scoped subset with honest reduced interpretation",
        },
        {
            "blocker": "no_public_oos_runner_contract_or_fresh_dir_hash_bound_execution_packet",
            "why": "there is no authorized runner/observer command, fresh-dir guard, source/runtime hash binding, or exact approval.",
            "required_to_clear": "separate OOS review packet with NOT_AUTHORIZED preview first, then explicit exact approval if accepted",
        },
        {
            "blocker": "private_truth_and_live_claims_absent",
            "why": "public-only replay cannot prove owner/private execution, fees, queue position, or live deployability.",
            "required_to_clear": "own authenticated telemetry/canary truth packet after public OOS, if live path is later considered",
        },
    ]

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version": strategy["strategy_version"],
        "strategy_family": strategy["strategy_family"],
        "candidate_granularity": "HISTORICAL_REPLAY_ACTION",
        "binding_status": "REPLAY_BOUND_NOT_OOS_READY",
        "asset_universe": strategy["asset_universe"],
        "timeframe": strategy["timeframe"],
        "branch_id": strategy["policy_contract"]["branch_id"],
        "candidate_count": strategy["candidate_count"],
        "expected_market_count": strategy["expected_market_count"],
        "historical_window": {
            "first_day": stats["first_day"],
            "last_day": stats["last_day"],
            "active_days": strategy["validation_summary"]["active_days"],
        },
        "performance_summary_public_replay": {
            "legacy_0p03_buy_actual_est": strategy["validation_summary"]["buy_actual_est"],
            "legacy_0p03_cash_pnl_est": strategy["validation_summary"]["cash_pnl_est"],
            "legacy_0p03_roi_est": strategy["validation_summary"]["roi_est"],
            "official_crypto_0p07_buy_actual_est": fee_recalc["buy_actual_official_fee_0p07"],
            "official_crypto_0p07_cash_pnl_est": fee_recalc["cash_pnl_official_fee_0p07"],
            "official_crypto_0p07_turnover_roi_est": fee_recalc["turnover_roi_official_fee_0p07"],
            "official_crypto_0p07_capital_300_roi_est": fee_recalc["capital_300_roi_official_fee_0p07"],
            "fee_model": "official_crypto_taker",
            "fee_rate": 0.07,
            "fee_formula": fee_recalc["fee_formula"],
            "fee_source_url": fee_recalc["fee_source_url"],
        },
        "accepted_review_gates": accepted_gates,
        "source_artifact_hashes": {
            "candidate_csv": sha256_file(paths["candidate_csv"]),
            "strategy_input": sha256_file(paths["strategy_input"]),
            "candidate_ledger_manifest": sha256_file(paths["ledger_manifest"]),
            "source_bridge_manifest": sha256_file(paths["source_bridge_manifest"]),
            "overlay_freshness_manifest": sha256_file(paths["attribution_manifest"]),
            "evidence_policy_manifest": sha256_file(paths["evidence_policy_manifest"]),
            "official_fee_recalc_manifest": sha256_file(paths["official_fee_recalc_manifest"]),
        },
        "evidence_policy": {
            "id": POLICY_ID,
            "decision": policy["decision"],
            "scope": "LOCAL_REPLAY_REVIEW_ONLY",
            "canonical_l1_top1_depth_accepted_for_local_review": True,
            "raw_l2_or_direct_top_depth_required_before_oos": True,
        },
        "next_packet_recommendation": "CE25_PUBLIC_OOS_PROJECTION_OR_DIRECT_OBSERVATION_PREP_PACKET_REVIEW_ONLY",
        "execution_approval": "NOT_ISSUED",
        "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    gap_assessment = {
        "schema_version": 1,
        "status": "BLOCKED_CE25_TARGET_QTY8_NOT_OOS_READY_GAP_ASSESSMENT_REVIEW_ONLY",
        "strategy_id": STRATEGY_ID,
        "distance_to_public_oos": {
            "stage_now": "historical_replay_bound_strategy_review_packet",
            "gates_cleared": [
                "candidate_ledger_normalized",
                "source_bridge_validated_no_drift",
                "canonical_l1_top1_depth_policy_accepted_for_local_review",
                "public_replay_profitability_positive",
            ],
            "minimum_remaining_gates": [
                "current_future_target_binding_or_projection",
                "oos_grade_top_depth_observation_contract",
                "bounded_no_order_public_oos_runner_packet_review",
                "fresh exact approval and one clean OOS run",
                "postrun artifact review",
            ],
            "not_required_for_public_oos_but_required_before_live": [
                "owner_private_truth",
                "canary risk packet",
                "private key custody and order telemetry",
            ],
            "plain_language": "本地历史 replay 包基本完成；public-only OOS 至少还差 fresh target/observation 设计和一次独立 clean OOS run。live/canary 更远。",
        },
        "oos_blockers": oos_blockers,
        "coverage_risk": {
            "full_candidate_count": strategy["candidate_count"],
            "strict_no_overlay_raw_l2_fresh_action_count": attribution["dependency_category_counts"]["NO_OVERLAY_RAW_L2_OK"],
            "top_overlay_required_count": attribution["top_overlay_required_count"],
            "raw_l2_stale_pair_count": attribution["raw_l2_stale_pair_count"],
            "interpretation": "full branch cannot be called OOS-ready until fresh direct/public top-depth evidence replaces overlay/stale dependencies",
        },
        "official_fee_recalc": {
            "fee_source_url": fee_recalc["fee_source_url"],
            "official_crypto_taker_fee_rate": fee_recalc["official_crypto_taker_fee_rate"],
            "cash_pnl_official_fee_0p07": fee_recalc["cash_pnl_official_fee_0p07"],
            "turnover_roi_official_fee_0p07": fee_recalc["turnover_roi_official_fee_0p07"],
            "capital_300_roi_official_fee_0p07": fee_recalc["capital_300_roi_official_fee_0p07"],
            "participation_rate_by_round": fee_recalc["participation_rate_by_round"],
        },
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    thresholds = {
        "schema_version": 1,
        "status": "REVIEW_ONLY_HISTORICAL_REPLAY_THRESHOLDS_NOT_OOS",
        "strategy_id": STRATEGY_ID,
        "required_historical_review_gates": {
            "candidate_count": 134,
            "expected_market_count": 129,
            "duplicate_candidate_count": 0,
            "source_bridge_failed_rows": 0,
            "source_bridge_row_errors": 0,
            "candidate_base_rows_loaded": 134,
            "l2_mart_rows_loaded": 268,
            "l1_top_pair_pass_all": True,
            "top1_depth_pair_fillable_all": True,
            "l2_fail_reason_pass_all": True,
            "cash_pnl_est_min": 0.0,
            "roi_est_min": 0.0,
            "official_crypto_fee_rate": 0.07,
            "official_crypto_fee_cash_pnl_min": 0.0,
        },
        "public_oos_future_clean_path_floor_draft": {
            "candidate_scope": "must be separately defined; cannot reuse historical windows as fresh targets",
            "stale_target_count": 0,
            "rest_book_allowed": False,
            "token_side_top_depth_complete": True,
            "ws_disconnect_count": 0,
            "ws_reconnect_count": 0,
            "recovered_round_count": 0,
            "safety_counters_nonzero": 0,
            "readiness_flags_all_false": True,
        },
        "fail_closed_rules": [
            "any_hash_drift",
            "candidate_or_market_count_drift",
            "source_bridge_error_nonzero",
            "canonical_l1_top_checks_not_all_true",
            "attempt_to_treat_historical_replay_windows_as_current_oos_targets",
            "attempt_to_claim_private_truth_promotion_live_or_deployable",
        ],
        "non_claims": non_claims(),
    }
    return packet, gap_assessment, thresholds


def write_outputs(args: argparse.Namespace, data: dict[str, Any]) -> dict[str, Any]:
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    packet, gap_assessment, thresholds = build_outputs(data)
    packet_path = output_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_REVIEW_PACKET.json"
    gap_path = output_dir / "CE25_TARGET_QTY8_OOS_GAP_ASSESSMENT.json"
    threshold_path = output_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_THRESHOLD_SPEC.json"
    command_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    boundary_path = output_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_BOUNDARY_NOTE.md"
    manifest_path = output_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_PACKET_HASH_MANIFEST.json"

    write_json(packet_path, packet)
    write_json(gap_path, gap_assessment)
    write_json(threshold_path, thresholds)
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 target_qty8 historical replay packet is review-only; no OOS/live/runner command is issued.' >&2",
                "exit 66",
                "",
            ]
        )
    )
    command_path.chmod(0o755)
    boundary_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Historical Replay Boundary",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet binds historical replay evidence only. It accepts canonical L1 top1 depth evidence for local review, not for OOS/live deployment.",
                "",
                "OOS remains blocked until a separate current/future target binding and public top-depth observation contract is reviewed. The historical rows must not be treated as fresh windows.",
                "",
                "No private key, import, order, cancel, redeem, canary, live, deploy, funding, latest pointer update, private truth, promotion, live-ready, or deployable claim is authorized.",
                "",
            ]
        )
    )
    artifacts = [
        packet_path,
        gap_path,
        threshold_path,
        command_path,
        boundary_path,
        *data["paths"].values(),
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "packet_sha256": sha256_file(packet_path),
        "gap_assessment_sha256": sha256_file(gap_path),
        "threshold_spec_sha256": sha256_file(threshold_path),
        "non_claims": non_claims(),
    }
    write_json(manifest_path, manifest)
    return {
        "ok": True,
        "status": STATUS,
        "output_dir": str(output_dir),
        "packet_sha256": sha256_file(packet_path),
        "gap_assessment_sha256": sha256_file(gap_path),
        "manifest_sha256": sha256_file(manifest_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger-dir", type=Path, default=DEFAULT_LEDGER_DIR)
    parser.add_argument("--source-bridge-dir", type=Path, default=DEFAULT_SOURCE_BRIDGE_DIR)
    parser.add_argument("--attribution-dir", type=Path, default=DEFAULT_ATTRIBUTION_DIR)
    parser.add_argument("--policy-dir", type=Path, default=DEFAULT_POLICY_DIR)
    parser.add_argument("--official-fee-recalc-dir", type=Path, default=DEFAULT_OFFICIAL_FEE_RECALC_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    data = load_inputs(args)
    errors = validate_inputs(data)
    if errors:
        raise SystemExit(json.dumps({"ok": False, "errors": errors}, ensure_ascii=False, indent=2, sort_keys=True))
    result = write_outputs(args, data)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build CE25 target_qty=8 public OOS preparation review packet.

This packet is the bridge from historical replay review to a future no-order
public OOS packet. It defines the required current/future target semantics and
observation contract, but remains review-only and exits 66.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
DEFAULT_HISTORICAL_PACKET_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_historical_replay_review_packet_20260604"
DEFAULT_OUTPUT_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_public_oos_prep_packet_20260604"

STATUS = "KEEP_CE25_TARGET_QTY8_PUBLIC_OOS_PREP_PACKET_REVIEWED_NOT_EXECUTION_READY"
STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"


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
        "exact_approval_issued": False,
    }


def load_inputs(args: argparse.Namespace) -> dict[str, Any]:
    packet_dir = args.historical_packet_dir.expanduser().resolve()
    paths = {
        "historical_packet": packet_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_REVIEW_PACKET.json",
        "gap_assessment": packet_dir / "CE25_TARGET_QTY8_OOS_GAP_ASSESSMENT.json",
        "historical_thresholds": packet_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_THRESHOLD_SPEC.json",
        "historical_manifest": packet_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_PACKET_HASH_MANIFEST.json",
        "historical_boundary": packet_dir / "CE25_TARGET_QTY8_HISTORICAL_REPLAY_BOUNDARY_NOTE.md",
    }
    missing = [str(path) for path in paths.values() if not path.exists()]
    if missing:
        raise SystemExit(json.dumps({"ok": False, "errors": ["missing_inputs"], "missing": missing}, indent=2))
    historical = read_json(paths["historical_packet"])
    gap = read_json(paths["gap_assessment"])
    if historical.get("status") != "KEEP_CE25_TARGET_QTY8_HISTORICAL_REPLAY_REVIEW_PACKET_ACCEPTED_NOT_OOS_READY":
        raise SystemExit(json.dumps({"ok": False, "errors": ["historical_packet_not_accepted"]}, indent=2))
    if historical.get("strategy_id") != STRATEGY_ID or historical.get("strategy_owner_line") != OWNER_LINE:
        raise SystemExit(json.dumps({"ok": False, "errors": ["strategy_or_owner_mismatch"]}, indent=2))
    return {"paths": paths, "historical": historical, "gap": gap}


def build_packet(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    historical = data["historical"]
    gap = data["gap"]
    historical_hashes = historical["source_artifact_hashes"]

    prep_packet = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version_source": historical["strategy_version"],
        "strategy_family": historical["strategy_family"],
        "source_historical_packet_sha256": sha256_file(data["paths"]["historical_packet"]),
        "source_gap_assessment_sha256": sha256_file(data["paths"]["gap_assessment"]),
        "source_candidate_count": historical["candidate_count"],
        "source_market_count": historical["expected_market_count"],
        "source_artifact_hashes": historical_hashes,
        "oos_target_semantics": {
            "mode": "CURRENT_FUTURE_POLICY_OBSERVATION_NOT_HISTORICAL_REUSE",
            "asset_universe": ["BTC"],
            "timeframe": "5m",
            "historical_condition_ids_allowed_as_oos_targets": False,
            "fresh_current_future_window_required": True,
            "stale_target_count_required": 0,
            "policy_projection": {
                "entry_high_price_leg_band": [0.65, 0.80],
                "pair_cost_cap": 0.970,
                "target_qty": 8.0,
                "requires_opposite_yes_no_top1_depth": True,
                "fee_model": "official_taker",
                "fee_rate": 0.07,
                "fee_source_url": "https://docs.polymarket.com/trading/fees",
                "fee_formula": "fee = C * feeRate * p * (1 - p)",
            },
        },
        "public_oos_observation_contract": {
            "orders_allowed": False,
            "private_key_allowed": False,
            "candidate_import_allowed": False,
            "book_source": "PUBLIC_CLOB_WS_OR_DIRECT_PUBLIC_BOOK_STREAM_REQUIRED",
            "shared_ingress_allowed_for_new_work": False,
            "rest_book_as_evidence_allowed": False,
            "token_side_top_depth_complete_required": True,
            "disconnect_count_required": 0,
            "reconnect_count_required": 0,
            "recovered_round_count_required_for_clean_pass": 0,
            "zero_valid_snapshot_rounds_required": 0,
            "safety_counters_nonzero_required": 0,
        },
        "candidate_event_contract": {
            "event_grain": "observed_policy_opportunity",
            "minimum_report_columns": [
                "strategy_owner_line",
                "strategy_id",
                "candidate_id",
                "market_id",
                "slug",
                "token_id_yes",
                "token_id_no",
                "window_start_ts_ms",
                "window_end_ts_ms",
                "observed_ts_ms",
                "high_price_leg_side",
                "opposite_leg_side",
                "yes_top1_price",
                "yes_top1_qty",
                "no_top1_price",
                "no_top1_qty",
                "pair_cost",
                "target_qty",
                "top1_depth_pair_fillable",
                "latency_ms",
                "book_age_ms",
                "orders_sent",
                "cancels_sent",
                "redeems_sent",
                "live_orders_allowed",
            ],
            "candidate_id_rule": "deterministic_hash(strategy_id,market_id,observed_ts_ms,high_price_leg_side,target_qty)",
        },
        "review_path": {
            "next_packet": "CE25_PUBLIC_OOS_RUNNER_IMPLEMENTATION_OR_SMOKE_REVIEW_PACKET",
            "recommended_sequence": [
                "local_runner_contract_patch_or_existing_runner_mapping_review",
                "isolated_no_order_smoke_packet_review",
                "bounded_public_oos_packet_review",
                "exact_approval_only_after_review",
                "postrun_artifact_review",
            ],
        },
        "execution_approval": "NOT_ISSUED",
        "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    thresholds = {
        "schema_version": 1,
        "status": "PUBLIC_OOS_PREP_THRESHOLDS_DRAFT_REVIEW_ONLY",
        "strategy_id": STRATEGY_ID,
        "smoke_scope_draft": {
            "purpose": "validate runner/observer/report contract only; cannot be clean OOS pass",
            "duration_min": 30,
            "min_observed_candidates": 1,
            "readiness_claim_allowed": False,
        },
        "full_public_oos_clean_path_draft": {
            "purpose": "future draft; requires separate review and exact approval",
            "duration_hours_min": 24,
            "min_observed_candidates": 50,
            "min_observed_markets": 40,
            "stale_target_count": 0,
            "rest_book_allowed": False,
            "token_side_top_depth_complete": True,
            "ws_disconnect_count": 0,
            "ws_reconnect_count": 0,
            "recovered_round_count": 0,
            "observer_nonzero_rounds": 0,
            "safety_counters_nonzero": 0,
            "readiness_flags_all_false": True,
        },
        "fail_closed_rules": [
            "historical_window_reuse_as_oos_target",
            "shared_ingress_dependency_for_new_work",
            "rest_book_used_as_book_evidence",
            "token_side_top_depth_incomplete",
            "ws_disconnect_or_reconnect_nonzero",
            "recovered_round_in_clean_path",
            "safety_counter_nonzero",
            "private_order_import_live_latest_path_touched",
            "readiness_flag_true",
        ],
        "non_claims": non_claims(),
    }

    gap_update = {
        "schema_version": 1,
        "status": "CE25_TARGET_QTY8_DISTANCE_TO_OOS_ASSESSMENT_AFTER_PREP_REVIEW",
        "stage_now": "public_oos_prep_contract_review_only",
        "historical_replay_packet_status": historical["status"],
        "remaining_to_first_public_oos_run": [
            "implement_or_bind no-order public observer/runner to this policy contract",
            "run isolated smoke under separate approval",
            "prepare bounded OOS packet with fresh target semantics",
            "collect clean public-only OOS evidence",
            "review OOS artifacts before any stronger claim",
        ],
        "rough_distance": {
            "engineering_packets_remaining_before_run": "at_least_2",
            "data_collection_window_for_meaningful_oos": "at_least_24h_after_runner_is_ready",
            "live_or_canary_distance": "not_on_current_path; requires public OOS plus owner/private truth and canary risk packet",
        },
        "why_not_oos_now": gap["oos_blockers"],
        "non_claims": non_claims(),
    }
    return prep_packet, thresholds, gap_update


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-packet-dir", type=Path, default=DEFAULT_HISTORICAL_PACKET_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    data = load_inputs(args)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    prep_packet, thresholds, gap_update = build_packet(data)
    packet_path = output_dir / "CE25_TARGET_QTY8_PUBLIC_OOS_PREP_PACKET.json"
    threshold_path = output_dir / "CE25_TARGET_QTY8_PUBLIC_OOS_PREP_THRESHOLD_SPEC.json"
    gap_path = output_dir / "CE25_TARGET_QTY8_DISTANCE_TO_OOS_AFTER_PREP.json"
    command_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    boundary_path = output_dir / "CE25_TARGET_QTY8_PUBLIC_OOS_PREP_BOUNDARY_NOTE.md"
    manifest_path = output_dir / "CE25_TARGET_QTY8_PUBLIC_OOS_PREP_PACKET_HASH_MANIFEST.json"

    write_json(packet_path, prep_packet)
    write_json(threshold_path, thresholds)
    write_json(gap_path, gap_update)
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 public OOS prep packet is review-only; no OOS/live/runner command is issued.' >&2",
                "exit 66",
                "",
            ]
        )
    )
    command_path.chmod(0o755)
    boundary_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Public OOS Prep Boundary",
                "",
                f"Status: `{STATUS}`",
                "",
                "This is a review-only OOS preparation contract. It does not authorize an OOS run.",
                "",
                "The historical replay rows are source evidence only. A future public OOS run must observe current/future BTC 5m markets with no orders and no shared-ingress dependency.",
                "",
                "No private key, import, order, cancel, redeem, canary, live, deploy, funding, latest pointer update, private truth, promotion, live-ready, or deployable claim is authorized.",
                "",
            ]
        )
    )
    artifacts = [
        packet_path,
        threshold_path,
        gap_path,
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
        "threshold_spec_sha256": sha256_file(threshold_path),
        "distance_assessment_sha256": sha256_file(gap_path),
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
                "distance_assessment_sha256": sha256_file(gap_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

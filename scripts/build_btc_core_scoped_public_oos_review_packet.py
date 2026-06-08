#!/usr/bin/env python3
"""Build a review-only scoped public OOS packet over resolved BTC core targets."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_scoped_public_oos_review_packet_20260605"
TARGET_BUNDLE = EXPORTS / "btc_core_projected_targets_review_bundle_20260605/BTC_CORE_PROJECTED_TARGETS_REVIEW_BUNDLE.json"
TARGET_CSV = EXPORTS / "btc_core_current_future_targets_materialized_20260605/BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
TARGET_AUDIT = (
    EXPORTS
    / "btc_core_current_future_targets_materialized_20260605/BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"
)
RUNTIME_RESULT = (
    EXPORTS / "btc_core_completion_runtime_v1_local_replay_20260605/BTC_CORE_COMPLETION_RUNTIME_V1_RESULT.json"
)
STATUS = "KEEP_BTC_CORE_SCOPED_PUBLIC_OOS_REVIEW_PACKET_PREPARED_EXACT_APPROVAL_REQUIRED_NOT_EXECUTED_NOT_LIVE_READY"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def non_claims() -> dict[str, bool]:
    return {
        "exact_approval_issued": False,
        "oos_authorized": False,
        "ws_started": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "orders_authorized": False,
        "private_key_loaded": False,
        "candidate_import_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "latest_pointer_update_authorized": False,
    }


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: scoped public OOS review packet only; no WS/OOS execution is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    bundle = read_json(TARGET_BUNDLE)
    audit = read_json(TARGET_AUDIT)
    runtime = read_json(RUNTIME_RESULT)
    bound_count = int(audit["bound_count"])
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "review_only_scoped_public_oos_contract_for_bound_targets",
        "target_binding": {
            "target_csv": str(TARGET_CSV),
            "target_csv_sha256": sha256_file(TARGET_CSV),
            "target_market_count": bound_count,
            "source_projection_created_ts_utc": bundle["summary"]["projection_created_ts_utc"],
            "full_288_market_clean_oos_packet_allowed": False,
            "scope_interpretation": f"Scoped OOS can cover only the {bound_count} BOUND targets; it cannot claim full 288-market OOS pass.",
        },
        "runtime_basis": {
            "runtime_status": runtime["status"],
            "historical_seed_actions": runtime["metrics"]["seed_actions"],
            "historical_fee_after_pnl": runtime["metrics"]["fee_after_pnl"],
            "manifest_metric_mismatch_count": runtime["manifest_metric_mismatch_count"],
            "fee_mismatch_count": runtime["fee_mismatch_count"],
        },
        "public_oos_observation_contract": {
            "book_transport": "direct_public_clob_ws_required",
            "shared_ingress_allowed": False,
            "rest_book_as_evidence_allowed": False,
            "orders_allowed": False,
            "private_key_allowed": False,
            "candidate_import_allowed": False,
            "target_market_count_required": bound_count,
            "stale_target_count_required_before_start": 0,
            "stale_target_count_required_inside_runner_before_first_observation": 0,
            "observed_target_market_count_required": bound_count,
            "token_side_top_depth_complete_required": True,
            "ws_disconnect_count_required": 0,
            "ws_reconnect_count_required": 0,
            "recovered_round_count_required": 0,
            "stale_only_round_count_required": 0,
            "zero_valid_snapshot_rounds_required": 0,
            "observer_nonzero_rounds_required": 0,
            "safety_counters_nonzero_required": 0,
            "readiness_flags_all_false_required": True,
        },
        "report_package_contract": {
            "required_outputs": [
                "BTC_CORE_PUBLIC_OOS_REPORT.csv",
                "BTC_CORE_PUBLIC_OOS_AUDIT_MANIFEST.json",
                "BTC_CORE_PUBLIC_OOS_GATE_SUMMARY.json",
                "BTC_CORE_PUBLIC_OOS_EVAL.json",
            ],
            "must_report": [
                "target_market_count",
                "observed_target_market_count",
                "runtime_seed_action_count",
                "runtime_pair_actions",
                "gross_buy_cost_proxy",
                "official_taker_fee_proxy",
                "fee_after_pnl_proxy_without_owner_truth",
                "latency_p50_p95_max_ms",
                "book_age_p50_p95_max_ms",
                "token_side_top_depth_completeness",
                "disconnect/reconnect counts",
                "safety counters",
                "readiness flags false",
            ],
            "forbidden_claims": [
                "owner_private_truth",
                "actual_fill_truth",
                "strategy_promotion_ready",
                "live_ready",
                "deployable",
            ],
        },
        "future_execution_packet_requirements": [
            "concrete direct public CLOB WS observer source/runtime hashes",
            "exact command body",
            "fresh target stale preflight",
            "fresh output/log/cache dir guards",
            "owner-line lock",
            "connection budget and attribution",
            "stop/kill procedure",
            "no private/order/live/latest boundaries",
        ],
        "fail_closed_if": [
            "target CSV hash drift",
            "stale_target_count > 0 before start or inside runner pre-observation",
            "observed target market count < bound target count",
            "REST book used as evidence",
            "shared-ingress/shared-WS dependency",
            "token-side top-depth incomplete",
            "WS disconnect/reconnect",
            "recovered round in clean path",
            "observer nonzero round",
            "safety counter nonzero",
            "any readiness/private/live/deploy flag true",
        ],
        "execution_approval": "NOT_ISSUED",
        "non_claims": non_claims(),
    }
    packet_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_OOS_REVIEW_PACKET.json"
    threshold_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_OOS_THRESHOLD_SPEC.json"
    note_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_OOS_BOUNDARY_NOTE.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_OOS_HASH_MANIFEST.json"
    write_json(packet_path, packet)
    write_json(
        threshold_path,
        {
            "schema_version": 1,
            "status": "BTC_CORE_SCOPED_PUBLIC_OOS_THRESHOLDS_REVIEW_ONLY",
            "target_market_count": bound_count,
            "clean_requirements": packet["public_oos_observation_contract"],
            "fail_closed_if": packet["fail_closed_if"],
            "non_claims": non_claims(),
        },
    )
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Scoped Public OOS Review",
                "",
                f"Status: `{STATUS}`",
                "",
                f"This packet binds `{bound_count}` resolved BTC 5m target markets. It is not an execution approval and cannot claim full 288-market OOS pass.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)
    files = [packet_path, threshold_path, note_path, preview_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    packet["outputs"] = {
        "packet": str(packet_path),
        "threshold_spec": str(threshold_path),
        "hash_manifest": str(hash_manifest_path),
        "hash_manifest_sha256": sha256_file(hash_manifest_path),
    }
    write_json(packet_path, packet)
    hash_manifest["files"][packet_path.name] = {
        "path": str(packet_path),
        "sha256": sha256_file(packet_path),
        "size": packet_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)
    print(f"status={STATUS}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"target_market_count={bound_count}")
    print("execution_approval=NOT_ISSUED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a review-only OOS prep packet for BTC core completion V1."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_completion_oos_prep_packet_20260605"
STRATEGY_PACKET = (
    EXPORTS
    / "btc_core_completion_strategy_review_packet_20260605/BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET.json"
)
RUNTIME_RESULT = (
    EXPORTS
    / "btc_core_completion_runtime_v1_local_replay_20260605/BTC_CORE_COMPLETION_RUNTIME_V1_RESULT.json"
)
RUNTIME_CONTRACT = (
    EXPORTS
    / "btc_core_completion_runtime_v1_local_replay_20260605/BTC_CORE_COMPLETION_RUNTIME_V1_INPUT_CONTRACT.json"
)
RESIDUAL_AUDIT = (
    EXPORTS
    / "btc_core_residual_risk_profile_20260605/BTC_CORE_RESIDUAL_RISK_PROFILE_AUDIT.json"
)
LOCAL_REPLAY_VERIFIER = (
    EXPORTS
    / "btc_core_local_replay_verifier_20260605/BTC_CORE_LOCAL_REPLAY_VERIFIER_SUMMARY.json"
)
STATUS = "KEEP_BTC_CORE_COMPLETION_V1_OOS_PREP_PACKET_REVIEW_READY_RUNTIME_MATCHED_EXECUTION_NOT_AUTHORIZED"


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


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: BTC core OOS prep packet is review-only; no OOS/runner/server/live path is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def non_claims() -> dict[str, bool]:
    return {
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "server_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "orders_authorized": False,
        "latest_pointer_update_authorized": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    strategy_packet = read_json(STRATEGY_PACKET)
    runtime_result = read_json(RUNTIME_RESULT)
    runtime_contract = read_json(RUNTIME_CONTRACT)
    residual_audit = read_json(RESIDUAL_AUDIT)
    replay_verifier = read_json(LOCAL_REPLAY_VERIFIER)

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "scope": "review_only_oos_preparation_no_execution",
        "accepted_local_runtime_basis": {
            "runtime_status": runtime_result["status"],
            "seed_actions": runtime_result["metrics"]["seed_actions"],
            "active_markets": runtime_result["metrics"]["active_markets"],
            "fee_after_pnl": runtime_result["metrics"]["fee_after_pnl"],
            "manifest_metric_mismatch_count": runtime_result["manifest_metric_mismatch_count"],
            "fee_mismatch_count": runtime_result["fee_mismatch_count"],
            "forbidden_present": runtime_contract["forbidden_present"],
            "missing_required": runtime_contract["missing_required"],
            "terminal_replay_verifier_status": replay_verifier["status"],
            "residual_profile_status": residual_audit["status"],
        },
        "oos_readiness_decision": {
            "can_prepare_oos_review_packet_next": True,
            "can_execute_oos_now": False,
            "reason": "local runtime is matched, but current/future target projection, public book fillability/top-depth, and owner truth are not yet bound",
            "highest_possible_next_status_before_execution": "KEEP_BTC_CORE_COMPLETION_V1_OOS_REVIEW_PACKET_PREPARED_EXACT_APPROVAL_REQUIRED_NOT_LIVE_READY",
        },
        "required_next_packets": [
            {
                "packet": "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_PACKET",
                "purpose": "materialize fresh BTC 5m current/future target markets and windows; historical ledger windows cannot be treated as live OOS targets",
                "must_include": [
                    "projection_created_ts_ms and UTC provenance",
                    "market_id/token_id_yes/token_id_no/subscribed_asset_ids/window_start/window_end",
                    "stale_target_count=0 preflight",
                    "full-scope target row/market floors",
                    "reject/coverage/collision manifest",
                ],
            },
            {
                "packet": "BTC_CORE_PUBLIC_FILLABILITY_TOP_DEPTH_OOS_PACKET",
                "purpose": "observe projected targets with public book/top-depth contract before any private/live path",
                "must_include": [
                    "server/direct or local observer source/runtime hashes",
                    "no REST book if WS/top-depth is required",
                    "token-side top-depth completeness",
                    "no disconnect/reconnect in clean path",
                    "recovered_round_count=0",
                    "book_age/freshness thresholds fail closed",
                    "safety counters zero",
                ],
            },
            {
                "packet": "BTC_CORE_OWNER_TRUTH_CANARY_PLAN_PACKET",
                "purpose": "only after public OOS review, define tiny owner-truth collection plan; not execution approval",
                "must_include": [
                    "capital cap and per-market/per-round limits",
                    "order/fill/fee/inventory/merge/redeem/cancel/error log schema",
                    "private key custody/signing boundary",
                    "new exact approval requirement",
                ],
            },
        ],
        "clean_oos_fail_closed_rules": [
            "runtime input contains forbidden outcome/post-action/private/live fields",
            "official fee formula mismatch",
            "stale_target_count > 0 before OOS start or inside runner before first observation",
            "candidate/market/token/window/subscribed_asset drift",
            "REST book substitution when WS/top-depth contract is required",
            "token-side top-depth incomplete",
            "WS disconnect/reconnect or unknown WS attribution",
            "recovered rounds in clean path",
            "safety counters nonzero",
            "private_truth_ready/strategy_promotion_ready/live_ready/deployable set true",
        ],
        "inputs": {
            "strategy_packet": {"path": str(STRATEGY_PACKET), "sha256": sha256_file(STRATEGY_PACKET)},
            "runtime_result": {"path": str(RUNTIME_RESULT), "sha256": sha256_file(RUNTIME_RESULT)},
            "runtime_contract": {"path": str(RUNTIME_CONTRACT), "sha256": sha256_file(RUNTIME_CONTRACT)},
            "residual_audit": {"path": str(RESIDUAL_AUDIT), "sha256": sha256_file(RESIDUAL_AUDIT)},
            "local_replay_verifier": {"path": str(LOCAL_REPLAY_VERIFIER), "sha256": sha256_file(LOCAL_REPLAY_VERIFIER)},
        },
        "non_claims": non_claims(),
    }

    packet_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_OOS_PREP_PACKET.json"
    note_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_OOS_PREP_NOTE.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_OOS_PREP_HASH_MANIFEST.json"
    write_json(packet_path, packet)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Completion V1 OOS Prep",
                "",
                f"Status: `{STATUS}`",
                "",
                "The local runtime replay is matched, but this packet does not authorize OOS. The next work is target projection and public fillability/top-depth review, not live execution.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)
    files = [packet_path, note_path, preview_path]
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
    print("can_execute_oos_now=false")
    print("next=BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_PACKET")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

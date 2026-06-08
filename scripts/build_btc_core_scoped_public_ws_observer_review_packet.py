#!/usr/bin/env python3
"""Build review-only packet for BTC_CORE scoped direct public WS observer."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_scoped_public_ws_observer_review_packet_20260605"
SCOPED_OOS_PACKET = (
    EXPORTS
    / "btc_core_scoped_public_oos_review_packet_20260605/"
    "BTC_CORE_SCOPED_PUBLIC_OOS_REVIEW_PACKET.json"
)
TARGET_CSV = EXPORTS / "btc_core_current_future_targets_materialized_20260605/BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
TARGET_AUDIT = (
    EXPORTS
    / "btc_core_current_future_targets_materialized_20260605/"
    "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"
)
OBSERVER = ROOT / "scripts/run_btc_core_scoped_public_ws_no_order_observer.py"
STATUS = "KEEP_BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_REVIEW_PACKET_READY_EXACT_APPROVAL_REQUIRED_NOT_EXECUTED"


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
        "ws_started": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "shared_ingress_used": False,
        "rest_book_evidence_used": False,
        "orders_authorized": False,
        "private_key_loaded": False,
        "candidate_import_authorized": False,
        "latest_pointer_update_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
    }


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: BTC_CORE scoped public WS observer review packet only; no WS/OOS execution is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def exact_approval_draft(path: Path, *, target_count: int, target_sha: str) -> None:
    path.write_text(
        "\n".join(
            [
                "DRAFT_NOT_ISSUED",
                "",
                "I authorize exactly one BTC_CORE_COMPLETION_V1 scoped public-only/no-order OOS observation run, "
                f"limited to the {target_count} reviewed BOUND target markets from "
                f"`{TARGET_CSV}` with sha256 `{target_sha}`, using review packet "
                f"`{OUTPUT_DIR}`.",
                "",
                "This approval would authorize only direct public CLOB market WebSocket observation, report/audit/gate/eval "
                "artifact generation, and fail-closed evaluation. It would not authorize full 288-market OOS claims, "
                "shared-ingress/shared-WS use, REST book evidence, candidate import, private key loading, order/cancel/redeem, "
                "canary/live/deploy/funding, latest pointer update, private_truth_ready, strategy_promotion_ready, live_ready, "
                "deployable, or any readiness/promotion/private-truth claim.",
                "",
                "The run must use a fresh output directory, verify observer and target CSV hashes before any WS connection, "
                "fail closed if stale_target_count is nonzero before start or inside runner before first observation, use exactly "
                "1 direct public CLOB WS connection for this thread, observe exactly the scoped target token_id_yes/token_id_no assets, require "
                "observed_target_market_count equal target_market_count, token-side top-depth complete, no REST book, no shared-ingress, "
                "no WS disconnect/reconnect, recovered_round_count=0, stale_only_round_count=0, zero_valid_snapshot_rounds=0, "
                "observer_nonzero_rounds=0, safety counters all zero, and readiness flags all false.",
                "",
                "Highest future success is capped at "
                "`KEEP_BTC_CORE_SCOPED_PUBLIC_OOS_EVIDENCE_REVIEW_REQUIRED_PROMOTION_BLOCKED_OWNER_TRUTH`.",
                "",
            ]
        ),
        encoding="utf-8",
    )


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scoped_packet = read_json(SCOPED_OOS_PACKET)
    target_audit = read_json(TARGET_AUDIT)
    target_count = int(target_audit["bound_count"])
    if target_count != int(scoped_packet["target_binding"]["target_market_count"]):
        raise SystemExit("target_count mismatch between scoped packet and target audit")

    planned_run_dir = EXPORTS / "btc_core_scoped_public_oos_observer_run_REVIEWED_TIMESTAMP"
    command_body = {
        "status": "DRAFT_NOT_AUTHORIZED",
        "sequence": [
            "verify observer source hash and target CSV hash",
            "fail if output dir exists",
            "run stale-target preflight inside observer before any WS connection",
            "open exactly 1 direct public CLOB market WS connection for this thread",
            "subscribe only target CSV token_id_yes/token_id_no assets",
            "collect public book/top-depth evidence for bounded duration",
            "write report/audit/gate/eval package",
            "fail closed if thresholds are not clean",
        ],
        "not_authorized_example": (
            f"uv run --with requests --with websockets python {OBSERVER} "
            f"--target-csv {TARGET_CSV} "
            f"--expected-target-csv-sha256 {sha256_file(TARGET_CSV)} "
            f"--expected-target-count {target_count} "
            f"--output-dir {planned_run_dir} "
            "--duration-sec 900 --book-max-age-ms 60000 --min-top-levels 1 --max-ws-connections 1"
        ),
    }
    source_runtime_hashes = {
        "observer": {
            "path": str(OBSERVER),
            "sha256": sha256_file(OBSERVER),
            "role": "direct_public_clob_ws_no_order_observer",
        },
        "target_csv": {
            "path": str(TARGET_CSV),
            "sha256": sha256_file(TARGET_CSV),
            "row_count": target_count,
        },
        "scoped_oos_review_packet": {
            "path": str(SCOPED_OOS_PACKET),
            "sha256": sha256_file(SCOPED_OOS_PACKET),
        },
    }
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "review_only_direct_public_clob_ws_observer_for_215_bound_targets",
        "execution_approval": "NOT_ISSUED",
        "source_runtime_hashes": source_runtime_hashes,
        "target_binding": {
            "target_market_count": target_count,
            "full_288_market_clean_oos_packet_allowed": False,
            "scope_interpretation": (
                f"This observer packet can only evaluate the {target_count} resolved BOUND targets. "
                "It cannot claim full 288-market / 24h clean OOS pass."
            ),
        },
        "connection_policy": {
            "transport": "direct_public_clob_ws",
            "max_ws_connections": 1,
            "one_ws_per_thread_required": True,
            "shared_ingress_allowed": False,
            "rest_book_evidence_allowed": False,
            "unknown_ws_identity_fail_closed": True,
        },
        "future_command_body": command_body,
        "required_outputs": [
            "BTC_CORE_PUBLIC_OOS_REPORT.csv",
            "BTC_CORE_PUBLIC_OOS_AUDIT_MANIFEST.json",
            "BTC_CORE_PUBLIC_OOS_GATE_SUMMARY.json",
            "BTC_CORE_PUBLIC_OOS_EVAL.json",
        ],
        "clean_thresholds": {
            "target_market_count": target_count,
            "observed_target_market_count": target_count,
            "stale_target_count_before_start": 0,
            "stale_target_count_inside_runner_before_first_observation": 0,
            "book_ws_used": True,
            "transport": "direct_public_clob_ws",
            "rest_book_used": False,
            "shared_ingress_used": False,
            "token_side_top_depth_complete": True,
            "ws_disconnect_count": 0,
            "ws_reconnect_count": 0,
            "recovered_round_count": 0,
            "stale_only_round_count": 0,
            "zero_valid_snapshot_rounds": 0,
            "observer_nonzero_rounds": 0,
            "safety_counters_nonzero": 0,
            "readiness_flags_all_false": True,
        },
        "fail_closed_if": [
            "observer source hash drift",
            "target CSV hash drift",
            "output dir exists",
            "stale target before WS start",
            "direct public CLOB WS connection count != 1",
            "REST book used as evidence",
            "shared-ingress/shared-WS dependency",
            "observed target count less than scoped target count",
            "token-side top-depth incomplete",
            "WS disconnect/reconnect",
            "recovered/stale-only/zero-valid-snapshot round",
            "observer nonzero round",
            "safety counter nonzero",
            "readiness/private/live/deploy flag true",
        ],
        "non_claims": non_claims(),
    }
    packet_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_REVIEW_PACKET.json"
    hash_expectations_path = OUTPUT_DIR / "SOURCE_RUNTIME_HASH_EXPECTATIONS.json"
    command_body_path = OUTPUT_DIR / "FUTURE_COMMAND_BODY_NOT_AUTHORIZED.json"
    threshold_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_THRESHOLD_SPEC.json"
    exact_approval_path = OUTPUT_DIR / "EXACT_APPROVAL_TEXT_DRAFT_NOT_ISSUED.txt"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    note_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_BOUNDARY_NOTE.md"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_HASH_MANIFEST.json"

    write_json(packet_path, packet)
    write_json(hash_expectations_path, source_runtime_hashes)
    write_json(command_body_path, command_body)
    write_json(
        threshold_path,
        {
            "schema_version": 1,
            "status": "BTC_CORE_SCOPED_PUBLIC_WS_OBSERVER_THRESHOLDS_REVIEW_ONLY",
            "clean_thresholds": packet["clean_thresholds"],
            "fail_closed_if": packet["fail_closed_if"],
            "non_claims": non_claims(),
        },
    )
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Scoped Public WS Observer Review",
                "",
                f"Status: `{STATUS}`",
                "",
                f"This packet binds a direct public CLOB WS observer for `{target_count}` scoped targets.",
                "It is not execution approval, does not start WS, and cannot claim full 288-market OOS.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)
    exact_approval_draft(exact_approval_path, target_count=target_count, target_sha=sha256_file(TARGET_CSV))
    files = [
        packet_path,
        hash_expectations_path,
        command_body_path,
        threshold_path,
        exact_approval_path,
        preview_path,
        note_path,
    ]
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
        "source_runtime_hash_expectations": str(hash_expectations_path),
        "future_command_body": str(command_body_path),
        "threshold_spec": str(threshold_path),
        "exact_approval_text_draft": str(exact_approval_path),
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
    print(f"target_market_count={target_count}")
    print("execution_approval=NOT_ISSUED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

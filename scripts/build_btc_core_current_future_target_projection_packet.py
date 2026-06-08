#!/usr/bin/env python3
"""Build the BTC core current/future target projection review packet.

The packet is review-only. It defines how a future projection materializer
should resolve fresh BTC 5m target markets for public OOS, but it does not
resolve live markets, start OOS, or authorize execution.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_current_future_target_projection_packet_20260605"
OOS_PREP = EXPORTS / "btc_core_completion_oos_prep_packet_20260605/BTC_CORE_COMPLETION_V1_OOS_PREP_PACKET.json"
RUNTIME_RESULT = (
    EXPORTS / "btc_core_completion_runtime_v1_local_replay_20260605/BTC_CORE_COMPLETION_RUNTIME_V1_RESULT.json"
)
RUNTIME_CONTRACT = (
    EXPORTS
    / "btc_core_completion_runtime_v1_local_replay_20260605/BTC_CORE_COMPLETION_RUNTIME_V1_INPUT_CONTRACT.json"
)
STRATEGY_PACKET = (
    EXPORTS / "btc_core_completion_strategy_review_packet_20260605/BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET.json"
)
STATUS = "KEEP_BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_PACKET_REVIEW_READY_INPUT_REQUIRED_NOT_EXECUTION_READY"
STRATEGY_ID = "BTC_CORE_COMPLETION_V1"
OWNER_LINE = "xuan_research_local"
MIN_TARGET_START_DELAY_MS = 21_600_000
START_ROUND_OFFSET = 73
TARGET_ROUND_COUNT = 288


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
        "projection_materialization_authorized": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "server_authorized": False,
        "orders_authorized": False,
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
                "echo 'NOT_AUTHORIZED: target projection packet is review-only; no resolver/materializer/OOS path is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    oos_prep = read_json(OOS_PREP)
    runtime_result = read_json(RUNTIME_RESULT)
    runtime_contract = read_json(RUNTIME_CONTRACT)
    strategy_packet = read_json(STRATEGY_PACKET)

    target_schema = {
        "schema_version": 1,
        "dataset": "btc_core_current_future_projected_market_targets_v1",
        "row_grain": "one projected BTC 5m market window",
        "required_columns": [
            "strategy_owner_line",
            "strategy_id",
            "projection_id",
            "projection_created_ts_ms",
            "projection_created_ts_utc",
            "projection_round_index",
            "start_round_offset",
            "slug",
            "asset",
            "timeframe",
            "market_id",
            "condition_id",
            "token_id_yes",
            "token_id_no",
            "subscribed_asset_ids",
            "window_start_ts_ms",
            "window_end_ts_ms",
            "binding_status",
            "resolver_source",
            "resolver_audit_row_hash",
        ],
        "binding_status_values": ["BOUND", "REJECTED_FAIL_CLOSED"],
        "bound_row_requirements": [
            "market_id and condition_id are present and equal if resolver uses condition_id as market_id",
            "token_id_yes and token_id_no are present, distinct, and side-labelled",
            "subscribed_asset_ids contains exactly token_id_yes/token_id_no unless resolver contract explicitly adds audited extras",
            "window_start_ts_ms >= projection_created_ts_ms + min_target_start_delay_ms",
            "window_end_ts_ms = window_start_ts_ms + 300000",
            "slug matches btc-updown-5m-{window_start_epoch_seconds}",
        ],
        "rejected_row_requirements": [
            "reject_reason and reject_category must be present in reject manifest",
            "rejected rows cannot enter OOS target CSV used by observer",
        ],
    }

    reject_schema = {
        "schema_version": 1,
        "dataset": "btc_core_current_future_projection_reject_manifest_v1",
        "row_grain": "one rejected projected BTC 5m market window",
        "required_columns": [
            "strategy_owner_line",
            "strategy_id",
            "projection_id",
            "projection_created_ts_ms",
            "projection_round_index",
            "start_round_offset",
            "slug",
            "window_start_ts_ms",
            "window_end_ts_ms",
            "reject_category",
            "reject_reason",
            "resolver_source",
            "resolver_audit_row_hash",
        ],
        "allowed_reject_categories": [
            "MISSING_RESOLVER_OUTPUT",
            "AMBIGUOUS_RESOLVER_OUTPUT",
            "TOKEN_SIDE_MISSING",
            "TOKEN_SIDE_DUPLICATE_OR_SWAPPED",
            "SUBSCRIBED_ASSET_MISMATCH",
            "STALE_OR_INSUFFICIENT_DELAY",
            "SLUG_WINDOW_DRIFT",
            "RESOLVER_SOURCE_HASH_MISMATCH",
        ],
    }

    projection_contract = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "projection_mode": "CURRENT_FUTURE_BTC_5M_MARKET_TARGETS_NOT_HISTORICAL_REUSE",
        "historical_windows_allowed_as_oos_targets": False,
        "target_universe": {
            "asset": "BTC",
            "timeframe": "5m",
            "slug_prefix": "btc-updown-5m-",
            "target_round_count": TARGET_ROUND_COUNT,
            "start_round_offset": START_ROUND_OFFSET,
            "min_target_start_delay_ms": MIN_TARGET_START_DELAY_MS,
            "offset_delay_proof": (
                "Producer must use base_sec=floor(projection_created_ts_ms/1000/300)*300 and "
                "target_sec=base_sec+300*start_round_offset. start_round_offset=73 guarantees "
                "target_start >= projection_created_ts_ms + 21600000 for all creation times."
            ),
        },
        "resolver_source_policy": {
            "preferred": "future packet must bind concrete public resolver source/runtime hash",
            "allowed_sources_review_required": [
                "public Gamma metadata resolver for market/condition/tokens",
                "public CLOB metadata resolver if it provides side-labelled asset ids",
                "local reviewed metadata cache with explicit sha256/provenance",
            ],
            "not_allowed": [
                "historical backtest condition_id reuse",
                "REST book snapshots as fillability evidence",
                "shared-ingress/shared-WS for new work",
                "private key or account-specific endpoints",
            ],
        },
        "coverage_collision_requirements": {
            "target_round_count": TARGET_ROUND_COUNT,
            "bound_count_min": 1,
            "bound_count_plus_reject_count_equals_target_round_count": True,
            "duplicate_slug_count": 0,
            "duplicate_market_id_count": 0,
            "duplicate_token_id_pair_count": 0,
            "window_collision_count": 0,
            "stale_target_count": 0,
        },
        "future_oos_scope_note": {
            "target_market_count_is_known_after_projection": True,
            "candidate_action_count_is_not_known_before_public_observation": True,
            "candidate_generation": (
                "OOS runner observes public trade/book events inside projected market windows and applies the "
                "BTC core runtime policy; projected market targets are not pre-filled action rows."
            ),
        },
        "non_claims": non_claims(),
    }

    threshold_spec = {
        "schema_version": 1,
        "status": "BTC_CORE_TARGET_PROJECTION_THRESHOLDS_REVIEW_ONLY",
        "projection_clean_requirements": {
            "projection_row_count": TARGET_ROUND_COUNT,
            "bound_row_count_min": 1,
            "bound_plus_reject_equals_projection_row_count": True,
            "stale_target_count": 0,
            "duplicate_slug_count": 0,
            "duplicate_market_id_count": 0,
            "duplicate_token_id_pair_count": 0,
            "window_collision_count": 0,
            "readiness_flags_all_false": True,
        },
        "fail_closed_if": [
            "projection_created_ts_ms missing or not parseable",
            "projection_created_ts_utc missing or not UTC",
            "start_round_offset below 73 while min_target_start_delay_ms remains 21600000",
            "slug does not match projected window_start",
            "market_id/condition_id missing or ambiguous",
            "token_id_yes/token_id_no missing, duplicate, or side-swapped",
            "subscribed_asset_ids mismatch",
            "bound+reject count mismatch",
            "stale_target_count > 0",
            "private/order/live/latest/readiness path touched",
        ],
        "non_claims": non_claims(),
    }

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "scope": "review_only_current_future_target_projection_contract",
        "source_runtime_basis": {
            "oos_prep_status": oos_prep["status"],
            "runtime_status": runtime_result["status"],
            "runtime_manifest_metric_mismatch_count": runtime_result["manifest_metric_mismatch_count"],
            "runtime_fee_mismatch_count": runtime_result["fee_mismatch_count"],
            "runtime_forbidden_present": runtime_contract["forbidden_present"],
            "strategy_packet_status": strategy_packet["status"],
        },
        "projection_contract": projection_contract,
        "target_schema_file": "BTC_CORE_PROJECTED_MARKET_TARGET_ROW_SCHEMA.json",
        "reject_schema_file": "BTC_CORE_PROJECTION_REJECT_ROW_SCHEMA.json",
        "threshold_spec_file": "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_THRESHOLD_SPEC.json",
        "next_step_after_review": {
            "packet": "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_MATERIALIZER_PACKET",
            "status": "NOT_PREPARED",
            "requires": [
                "concrete resolver/source/runtime hashes",
                "exact command body",
                "projection_created_ts runtime generation",
                "fresh output dirs",
                "coverage/collision/reject manifest",
                "no OOS/runner/observer execution in materialization-only packet",
            ],
        },
        "execution_approval": "NOT_ISSUED",
        "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    packet_path = OUTPUT_DIR / "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_PACKET.json"
    target_schema_path = OUTPUT_DIR / "BTC_CORE_PROJECTED_MARKET_TARGET_ROW_SCHEMA.json"
    reject_schema_path = OUTPUT_DIR / "BTC_CORE_PROJECTION_REJECT_ROW_SCHEMA.json"
    contract_path = OUTPUT_DIR / "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_CONTRACT.json"
    threshold_path = OUTPUT_DIR / "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_THRESHOLD_SPEC.json"
    note_path = OUTPUT_DIR / "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_BOUNDARY_NOTE.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_HASH_MANIFEST.json"

    write_json(packet_path, packet)
    write_json(target_schema_path, target_schema)
    write_json(reject_schema_path, reject_schema)
    write_json(contract_path, projection_contract)
    write_json(threshold_path, threshold_spec)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Current/Future Target Projection",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet is review-only. It defines fresh BTC 5m market-target projection semantics for a future materializer.",
                "",
                "It does not resolve current markets, start OOS, start runner/observer, touch private/live/order paths, or update any latest pointer.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)

    files = [
        packet_path,
        target_schema_path,
        reject_schema_path,
        contract_path,
        threshold_path,
        note_path,
        preview_path,
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
        "target_schema": str(target_schema_path),
        "reject_schema": str(reject_schema_path),
        "projection_contract": str(contract_path),
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
    print(f"start_round_offset={START_ROUND_OFFSET}")
    print(f"target_round_count={TARGET_ROUND_COUNT}")
    print("execution_approval=NOT_ISSUED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

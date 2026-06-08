#!/usr/bin/env python3
"""Build review-only packet for BTC core target projection materializer runtime."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_target_projection_materializer_review_packet_20260605"
PROJECTION_PACKET = (
    EXPORTS
    / "btc_core_current_future_target_projection_packet_20260605/"
    "BTC_CORE_CURRENT_FUTURE_TARGET_PROJECTION_PACKET.json"
)
PRODUCER = ROOT / "scripts/produce_btc_core_current_future_targets.py"
VALIDATOR = ROOT / "scripts/validate_btc_core_current_future_targets.py"
STATUS = "KEEP_BTC_CORE_TARGET_PROJECTION_MATERIALIZER_REVIEW_PACKET_READY_INPUT_REQUIRED_NOT_EXECUTION_READY"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: materializer review packet only; no resolver/materializer/OOS execution is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def non_claims() -> dict[str, bool]:
    return {
        "exact_approval_issued": False,
        "resolver_execution_authorized": False,
        "projection_materialization_authorized": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "orders_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "latest_pointer_update_authorized": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    projection_packet = read_json(PROJECTION_PACKET)
    runtime_hashes = {
        "projection_packet": {"path": str(PROJECTION_PACKET), "sha256": sha256_file(PROJECTION_PACKET)},
        "producer": {"path": str(PRODUCER), "sha256": sha256_file(PRODUCER)},
        "validator": {"path": str(VALIDATOR), "sha256": sha256_file(VALIDATOR)},
    }
    future_command_body = {
        "status": "DRAFT_NOT_AUTHORIZED",
        "environment": {
            "packet_root": str(OUTPUT_DIR),
            "fresh_output_dir_required": True,
            "resolver_metadata_json": "REQUIRED_REVIEWED_INPUT_PATH_NOT_BOUND",
            "resolver_metadata_sha256": "REQUIRED_REVIEWED_INPUT_SHA256_NOT_BOUND",
        },
        "sequence": [
            "generate PROJECTION_CREATED_TS_MS immediately before producer",
            "generate PROJECTION_CREATED_TS_UTC in UTC from the same instant",
            "verify source/runtime hashes for producer, validator, projection packet, and reviewed resolver metadata",
            "fail if output dir exists",
            "run produce_btc_core_current_future_targets.py with --start-round-offset 73 --target-round-count 288 --min-target-start-delay-ms 21600000",
            "run validate_btc_core_current_future_targets.py on produced targets/reject/audit",
            "require validator ok=true before any future OOS packet can reference produced targets",
        ],
        "example_not_authorized_command": (
            "python scripts/produce_btc_core_current_future_targets.py "
            "--resolver-metadata-json $REVIEWED_RESOLVER_METADATA_JSON "
            "--output-dir $FRESH_OUTPUT_DIR "
            "--projection-created-ts-ms $PROJECTION_CREATED_TS_MS "
            "--projection-created-ts-utc $PROJECTION_CREATED_TS_UTC "
            "--start-round-offset 73 --target-round-count 288 --min-target-start-delay-ms 21600000"
        ),
    }
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_owner_line": "xuan_research_local",
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "scope": "review_only_materializer_runtime_binding_no_execution",
        "projection_packet_status": projection_packet["status"],
        "source_runtime_hashes": runtime_hashes,
        "input_blocker": {
            "reviewed_resolver_metadata_json_bound": False,
            "reason": "A concrete resolver metadata JSON path/hash/provenance is required before materialization approval.",
        },
        "future_command_body": future_command_body,
        "materializer_clean_requirements": {
            "target_round_count": 288,
            "start_round_offset": 73,
            "min_target_start_delay_ms": 21_600_000,
            "bound_plus_reject_equals_target_round_count": True,
            "stale_target_count": 0,
            "duplicate_slug_count": 0,
            "duplicate_market_id_count": 0,
            "duplicate_token_id_pair_count": 0,
            "window_collision_count": 0,
            "validator_ok_required": True,
        },
        "fail_closed_rules": [
            "reviewed resolver metadata path/hash/provenance missing",
            "source/runtime hash mismatch",
            "output dir exists before materializer",
            "producer exits nonzero",
            "validator ok is false or missing",
            "stale_target_count > 0",
            "token side/subscribed asset mismatch",
            "private/order/live/OOS/latest/readiness path touched",
        ],
        "execution_approval": "NOT_ISSUED",
        "non_claims": non_claims(),
    }
    packet_path = OUTPUT_DIR / "BTC_CORE_TARGET_PROJECTION_MATERIALIZER_REVIEW_PACKET.json"
    hash_expectations_path = OUTPUT_DIR / "SOURCE_RUNTIME_HASH_EXPECTATIONS.json"
    command_body_path = OUTPUT_DIR / "FUTURE_COMMAND_BODY_NOT_AUTHORIZED.json"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    note_path = OUTPUT_DIR / "BTC_CORE_TARGET_PROJECTION_MATERIALIZER_BOUNDARY_NOTE.md"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_TARGET_PROJECTION_MATERIALIZER_HASH_MANIFEST.json"
    write_json(packet_path, packet)
    write_json(hash_expectations_path, runtime_hashes)
    write_json(command_body_path, future_command_body)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Target Projection Materializer Review",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet binds producer/validator source hashes but does not execute resolver/materializer/OOS.",
                "",
                "The current blocker is a reviewed resolver metadata JSON path/hash/provenance.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)
    files = [packet_path, hash_expectations_path, command_body_path, preview_path, note_path]
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
    print("reviewed_resolver_metadata_json_bound=false")
    print("execution_approval=NOT_ISSUED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

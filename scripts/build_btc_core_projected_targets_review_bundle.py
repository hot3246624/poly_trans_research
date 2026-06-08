#!/usr/bin/env python3
"""Bundle BTC core projected target materialization artifacts for review."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
RESOLVER_DIR = EXPORTS / "btc_core_gamma_metadata_resolver_20260605"
TARGET_DIR = EXPORTS / "btc_core_current_future_targets_materialized_20260605"
OOS_PREP = EXPORTS / "btc_core_completion_oos_prep_packet_20260605/BTC_CORE_COMPLETION_V1_OOS_PREP_PACKET.json"
OUTPUT_DIR = EXPORTS / "btc_core_projected_targets_review_bundle_20260605"
STATUS = "KEEP_BTC_CORE_PROJECTED_TARGETS_PARTIAL_REVIEW_BUNDLE_READY_SCOPED_OOS_PACKET_PREP_ALLOWED_NOT_OOS_READY"


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
        "oos_ready": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "ws_started": False,
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
                "echo 'NOT_AUTHORIZED: projected target review bundle only; no OOS/WS/runner execution is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    resolver_summary = read_json(RESOLVER_DIR / "BTC_CORE_GAMMA_METADATA_RESOLVER_SUMMARY.json")
    target_audit = read_json(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json")
    target_validation = read_json(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_VALIDATION_RESULT_STANDALONE.json")
    oos_prep = read_json(OOS_PREP)
    bound_count = int(target_audit["bound_count"])
    target_count = int(target_audit["target_round_count"])
    reject_count = int(target_audit["reject_count"])
    full_clean_ready = bound_count == target_count and reject_count == 0 and bool(target_validation.get("ok"))
    scoped_prep_allowed = bound_count > 0 and bool(target_validation.get("ok"))
    bundle = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS if scoped_prep_allowed and not full_clean_ready else "KEEP_BTC_CORE_PROJECTED_TARGETS_FULL_REVIEW_BUNDLE_READY_NOT_OOS_READY",
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "scope": "local_public_gamma_metadata_projection_review_bundle_not_oos",
        "summary": {
            "projection_created_ts_utc": resolver_summary["projection_created_ts_utc"],
            "target_round_count": target_count,
            "bound_market_count": bound_count,
            "reject_count": reject_count,
            "validator_ok": bool(target_validation.get("ok")),
            "stale_target_count": target_audit["stale_target_count"],
            "duplicate_slug_count": target_audit["duplicate_slug_count"],
            "duplicate_market_id_count": target_audit["duplicate_market_id_count"],
            "duplicate_token_id_pair_count": target_audit["duplicate_token_id_pair_count"],
            "window_collision_count": target_audit["window_collision_count"],
        },
        "decision": {
            "full_288_market_clean_oos_packet_allowed": full_clean_ready,
            "scoped_215_market_oos_review_packet_allowed": scoped_prep_allowed and not full_clean_ready,
            "can_execute_oos_now": False,
            "reason": (
                "Current public Gamma metadata resolved 215 of 288 future BTC 5m targets. "
                "Full 288-target clean OOS is blocked until all targets resolve; a separately reviewed scoped OOS packet may bind the 215 BOUND targets with reduced interpretation."
            )
            if not full_clean_ready
            else "All target markets resolved, but OOS still requires separate public fillability/top-depth packet and exact approval.",
        },
        "next_allowed_packets": [
            {
                "packet": "BTC_CORE_PUBLIC_FILLABILITY_TOP_DEPTH_SCOPED_OOS_REVIEW_PACKET",
                "allowed": scoped_prep_allowed and not full_clean_ready,
                "scope": f"{bound_count} bound markets only; cannot claim full 288-market OOS pass",
            },
            {
                "packet": "BTC_CORE_PUBLIC_FILLABILITY_TOP_DEPTH_FULL_OOS_REVIEW_PACKET",
                "allowed": full_clean_ready,
                "scope": "all 288 projected BTC 5m targets",
            },
            {
                "packet": "BTC_CORE_TARGET_PROJECTION_REFRESH_PACKET",
                "allowed": True,
                "scope": "rerun public metadata resolver later when Gamma has published more future markets",
            },
        ],
        "inputs": {
            "resolver_summary": {
                "path": str(RESOLVER_DIR / "BTC_CORE_GAMMA_METADATA_RESOLVER_SUMMARY.json"),
                "sha256": sha256_file(RESOLVER_DIR / "BTC_CORE_GAMMA_METADATA_RESOLVER_SUMMARY.json"),
            },
            "resolver_metadata": {
                "path": str(RESOLVER_DIR / "BTC_CORE_REVIEWED_RESOLVER_METADATA.json"),
                "sha256": sha256_file(RESOLVER_DIR / "BTC_CORE_REVIEWED_RESOLVER_METADATA.json"),
            },
            "target_csv": {
                "path": str(TARGET_DIR / "BTC_CORE_PROJECTED_MARKET_TARGETS.csv"),
                "sha256": sha256_file(TARGET_DIR / "BTC_CORE_PROJECTED_MARKET_TARGETS.csv"),
            },
            "reject_manifest": {
                "path": str(TARGET_DIR / "BTC_CORE_PROJECTION_REJECT_MANIFEST.jsonl"),
                "sha256": sha256_file(TARGET_DIR / "BTC_CORE_PROJECTION_REJECT_MANIFEST.jsonl"),
            },
            "coverage_audit": {
                "path": str(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"),
                "sha256": sha256_file(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"),
            },
            "standalone_validation": {
                "path": str(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_VALIDATION_RESULT_STANDALONE.json"),
                "sha256": sha256_file(TARGET_DIR / "BTC_CORE_TARGET_PROJECTION_VALIDATION_RESULT_STANDALONE.json"),
            },
            "oos_prep_packet": {"path": str(OOS_PREP), "sha256": sha256_file(OOS_PREP), "status": oos_prep["status"]},
        },
        "non_claims": non_claims(),
    }
    bundle_path = OUTPUT_DIR / "BTC_CORE_PROJECTED_TARGETS_REVIEW_BUNDLE.json"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    note_path = OUTPUT_DIR / "BTC_CORE_PROJECTED_TARGETS_REVIEW_NOTE.md"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_PROJECTED_TARGETS_REVIEW_HASH_MANIFEST.json"
    write_json(bundle_path, bundle)
    command_preview(preview_path)
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Projected Targets Review",
                "",
                f"Status: `{bundle['status']}`",
                "",
                f"Resolved `{bound_count}` of `{target_count}` future BTC 5m target markets from public Gamma metadata.",
                "",
                "No WS/OOS/runner/live path is authorized by this bundle.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    files = [bundle_path, preview_path, note_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": bundle["status"],
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    bundle["outputs"] = {
        "bundle": str(bundle_path),
        "hash_manifest": str(hash_manifest_path),
        "hash_manifest_sha256": sha256_file(hash_manifest_path),
    }
    write_json(bundle_path, bundle)
    hash_manifest["files"][bundle_path.name] = {
        "path": str(bundle_path),
        "sha256": sha256_file(bundle_path),
        "size": bundle_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)
    print(f"status={bundle['status']}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"bound_market_count={bound_count}")
    print(f"reject_count={reject_count}")
    print(f"full_288_market_clean_oos_packet_allowed={full_clean_ready}")
    print(f"scoped_oos_review_packet_allowed={scoped_prep_allowed and not full_clean_ready}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

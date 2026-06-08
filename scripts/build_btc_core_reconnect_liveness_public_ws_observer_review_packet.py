#!/usr/bin/env python3
"""Build review packet for BTC_CORE public WS V3 sequential reconnect liveness."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_reconnect_liveness_public_ws_observer_review_packet_20260605"
TARGET_CSV = EXPORTS / "btc_core_current_future_targets_materialized_20260605/BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
TARGET_AUDIT = (
    EXPORTS
    / "btc_core_current_future_targets_materialized_20260605/"
    "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"
)
OBSERVER = ROOT / "scripts/run_btc_core_scoped_public_ws_no_order_observer.py"
V2_ATTRIBUTION = (
    EXPORTS
    / "btc_core_long_lived_public_oos_postrun_attribution_20260605/"
    "BTC_CORE_LONG_LIVED_PUBLIC_OOS_V2_POSTRUN_ATTRIBUTION.json"
)
STATUS = "KEEP_BTC_CORE_RECONNECT_LIVENESS_PUBLIC_WS_OBSERVER_V3_REVIEW_PACKET_READY_STANDING_RESEARCH_APPROVAL_APPLIES"


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
                "echo 'AUTHORIZED_BY_STANDING_RESEARCH_BOUNDARY: V3 may run public-only/no-order with one WS at a time; this preview does not execute.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def non_claims() -> dict[str, bool]:
    return {
        "full_288_market_oos_pass": False,
        "clean_scoped_oos_pass_if_reconnects_occur": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "orders_authorized": False,
        "private_key_loaded": False,
        "latest_pointer_update_authorized": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_count = int(read_json(TARGET_AUDIT)["bound_count"])
    target_sha = sha256_file(TARGET_CSV)
    duration_sec = 14_400
    warmup_sec = 1_800
    max_reconnects = 12
    reconnect_backoff_sec = 5.0
    planned_run_dir = EXPORTS / "btc_core_reconnect_liveness_public_oos_observer_run_REVIEWED_TIMESTAMP"
    command_body = {
        "status": "STANDING_RESEARCH_APPROVAL_CAN_EXECUTE_NO_LIVE_NO_ORDER",
        "not_authorized_for": [
            "orders",
            "private_key",
            "canary",
            "live",
            "deploy",
            "funding",
            "latest pointer",
            "shared-ingress",
            "REST book evidence",
        ],
        "command": (
            f"uv run --with requests --with websockets python {OBSERVER} "
            f"--target-csv {TARGET_CSV} "
            f"--expected-target-csv-sha256 {target_sha} "
            f"--expected-target-count {target_count} "
            f"--output-dir {planned_run_dir} "
            f"--duration-sec {duration_sec} --warmup-sec {warmup_sec} "
            "--require-live-fresh-after-warmup "
            f"--allow-sequential-reconnects --max-reconnects {max_reconnects} "
            f"--reconnect-backoff-sec {reconnect_backoff_sec} "
            "--book-max-age-ms 60000 --min-top-levels 1 --max-ws-connections 1"
        ),
    }
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "public_only_no_order_single_ws_sequential_reconnect_liveness",
        "standing_research_approval_boundary": {
            "applies": True,
            "reason": "User granted all permissions for non-canary/non-live research-only work.",
            "forbidden": [
                "canary",
                "live",
                "private key",
                "order/cancel/redeem",
                "deploy",
                "funding",
                "latest pointer",
                "service mutation",
            ],
        },
        "target_binding": {
            "target_csv": str(TARGET_CSV),
            "target_csv_sha256": target_sha,
            "target_market_count": target_count,
            "full_288_market_clean_oos_packet_allowed": False,
        },
        "source_runtime_hashes": {
            "observer": {"path": str(OBSERVER), "sha256": sha256_file(OBSERVER)},
            "target_csv": {"path": str(TARGET_CSV), "sha256": target_sha, "row_count": target_count},
            "v2_postrun_attribution": {"path": str(V2_ATTRIBUTION), "sha256": sha256_file(V2_ATTRIBUTION)},
        },
        "connection_policy": {
            "max_simultaneous_ws_connections": 1,
            "sequential_reconnects_allowed": True,
            "max_reconnects": max_reconnects,
            "reconnect_backoff_sec": reconnect_backoff_sec,
            "reconnects_do_not_count_as_clean_oos_pass_without_separate_review": True,
        },
        "freshness_policy": {
            "duration_sec": duration_sec,
            "warmup_sec": warmup_sec,
            "book_max_age_ms": 60_000,
            "require_live_fresh_after_warmup": True,
        },
        "future_command_body": command_body,
        "expected_interpretation": {
            "if_reconnects_zero_and_all_thresholds_clean": "clean scoped public OOS evidence review candidate only; still not full 288/private/live",
            "if_reconnects_nonzero_but_coverage_improves": "degraded liveness evidence; prepare reconnect taxonomy review, not clean pass",
            "if_coverage_remains_low": "public liveness/market activity blocker; consider active subset or projection refresh",
        },
        "non_claims": non_claims(),
    }
    paths = {
        "packet": OUTPUT_DIR / "BTC_CORE_RECONNECT_LIVENESS_PUBLIC_WS_OBSERVER_V3_REVIEW_PACKET.json",
        "hashes": OUTPUT_DIR / "SOURCE_RUNTIME_HASH_EXPECTATIONS.json",
        "command": OUTPUT_DIR / "FUTURE_COMMAND_BODY_STANDING_RESEARCH_APPROVAL.json",
        "preview": OUTPUT_DIR / "COMMAND_PREVIEW_NOT_EXECUTED.sh",
        "manifest": OUTPUT_DIR / "BTC_CORE_RECONNECT_LIVENESS_PUBLIC_WS_OBSERVER_V3_HASH_MANIFEST.json",
    }
    write_json(paths["packet"], packet)
    write_json(paths["hashes"], packet["source_runtime_hashes"])
    write_json(paths["command"], command_body)
    command_preview(paths["preview"])
    files = [paths["packet"], paths["hashes"], paths["command"], paths["preview"]]
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(paths["manifest"], manifest)
    packet["outputs"] = {
        "packet": str(paths["packet"]),
        "hash_manifest": str(paths["manifest"]),
        "hash_manifest_sha256": sha256_file(paths["manifest"]),
    }
    write_json(paths["packet"], packet)
    manifest["files"][paths["packet"].name] = {
        "path": str(paths["packet"]),
        "sha256": sha256_file(paths["packet"]),
        "size": paths["packet"].stat().st_size,
    }
    write_json(paths["manifest"], manifest)
    print(f"status={STATUS}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"target_market_count={target_count}")
    print(f"max_simultaneous_ws_connections=1")
    print(f"max_reconnects={max_reconnects}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

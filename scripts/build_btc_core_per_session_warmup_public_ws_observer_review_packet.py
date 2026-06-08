#!/usr/bin/env python3
"""Build V4 packet for per-session warmup public WS liveness."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_per_session_warmup_public_ws_observer_review_packet_20260605"
TARGET_CSV = EXPORTS / "btc_core_current_future_targets_materialized_20260605/BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
OBSERVER = ROOT / "scripts/run_btc_core_scoped_public_ws_no_order_observer.py"
V3_ATTRIBUTION = (
    EXPORTS
    / "btc_core_reconnect_liveness_public_oos_v3_partial_attribution_20260605/"
    "BTC_CORE_RECONNECT_LIVENESS_V3_PARTIAL_ATTRIBUTION.json"
)
STATUS = "KEEP_BTC_CORE_PER_SESSION_WARMUP_PUBLIC_WS_OBSERVER_V4_READY_STANDING_RESEARCH_APPROVAL_APPLIES"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    target_sha = sha256_file(TARGET_CSV)
    duration_sec = 7_200
    warmup_sec = 300
    command = (
        f"uv run --with requests --with websockets python {OBSERVER} "
        f"--target-csv {TARGET_CSV} "
        f"--expected-target-csv-sha256 {target_sha} "
        "--expected-target-count 215 "
        "--output-dir DATA_EXPORTS/btc_core_per_session_warmup_public_oos_observer_run_TIMESTAMP "
        f"--duration-sec {duration_sec} --warmup-sec {warmup_sec} "
        "--require-live-fresh-after-warmup --per-session-warmup "
        "--allow-sequential-reconnects --max-reconnects 12 --reconnect-backoff-sec 5 "
        "--book-max-age-ms 60000 --min-top-levels 1 --max-ws-connections 1"
    )
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "public_only_no_order_single_ws_per_session_warmup_liveness",
        "standing_research_approval_applies": True,
        "target_binding": {
            "target_csv": str(TARGET_CSV),
            "target_csv_sha256": target_sha,
            "target_market_count": 215,
            "full_288_market_clean_oos_packet_allowed": False,
        },
        "source_runtime_hashes": {
            "observer": {"path": str(OBSERVER), "sha256": sha256_file(OBSERVER)},
            "target_csv": {"path": str(TARGET_CSV), "sha256": target_sha, "row_count": 215},
            "v3_partial_attribution": {"path": str(V3_ATTRIBUTION), "sha256": sha256_file(V3_ATTRIBUTION)},
        },
        "runtime_contract": {
            "max_simultaneous_ws_connections": 1,
            "sequential_reconnects_allowed": True,
            "per_session_warmup": True,
            "duration_sec": duration_sec,
            "warmup_sec": warmup_sec,
            "max_reconnects": 12,
            "reconnects_do_not_create_clean_pass_without_separate_review": True,
        },
        "command_template": command,
        "non_claims": {
            "full_288_market_oos_pass": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "orders_authorized": False,
            "private_key_loaded": False,
        },
    }
    packet_path = OUTPUT_DIR / "BTC_CORE_PER_SESSION_WARMUP_PUBLIC_WS_OBSERVER_V4_PACKET.json"
    hashes_path = OUTPUT_DIR / "SOURCE_RUNTIME_HASH_EXPECTATIONS.json"
    write_json(packet_path, packet)
    write_json(hashes_path, packet["source_runtime_hashes"])
    manifest = {"schema_version": 1, "created_at": utc_now(), "status": STATUS, "files": {}}
    for path in [packet_path, hashes_path]:
        manifest["files"][path.name] = {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
    manifest_path = OUTPUT_DIR / "BTC_CORE_PER_SESSION_WARMUP_PUBLIC_WS_OBSERVER_V4_HASH_MANIFEST.json"
    write_json(manifest_path, manifest)
    print(f"status={STATUS}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"duration_sec={duration_sec}")
    print(f"warmup_sec={warmup_sec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

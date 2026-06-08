#!/usr/bin/env python3
"""Build a read-only capacity packet for CE25 BTC5M matching-source execution.

The current matching-source preapproval is blocked by archive availability and
local free space. This packet inventories large local backtest directories and
records non-destructive options for a later cleanup/archive approval.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_btc5m_matching_source_capacity_packet_20260606"
PREAPPROVAL_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_matching_source_execution_preapproval_packet_20260606"
    / "CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL_PACKET.json"
)
CHAIN_VALIDATOR = ROOT / "scripts" / "validate_ce25_btc5m_research_packet_chain.py"

STATUS = "KEEP_CE25_BTC5M_MATCHING_SOURCE_CAPACITY_REVIEWED_CLEANUP_OR_EXTERNAL_STORE_APPROVAL_REQUIRED_NOT_RUN"
STORE_MIN_FREE_GB = 250
PREFERRED_HEADROOM_GB = 285


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def disk_free_gib(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        return None
    stat = os.statvfs(probe)
    return round(stat.f_bavail * stat.f_frsize / (1024 ** 3), 3)


def du_gib(path: Path) -> float | None:
    if not path.exists():
        return None
    out = subprocess.check_output(["du", "-sk", str(path)], text=True)
    kib = int(out.split()[0])
    return round(kib / (1024 ** 2), 3)


def inventory() -> list[dict[str, Any]]:
    candidates = [
        BACKTEST_ROOT / "verification_store",
        BACKTEST_ROOT / "verification_store" / "replay_store_multiasset_l2_v1",
        BACKTEST_ROOT / "verification_store" / "replay_store_multiasset_l2_v1" / "20260502_20260518_l2",
        BACKTEST_ROOT / "verification_store" / "completion_unwind_event_store_v2",
        BACKTEST_ROOT / "verification_store" / "replay_store_v2",
        BACKTEST_ROOT / "derived",
        BACKTEST_ROOT / "derived" / "contract_examples",
        BACKTEST_ROOT / "derived" / "contract_examples" / "l2_top_aligned_mart_20260502_20260518_l2",
        BACKTEST_ROOT / "derived" / "ce25_nagi_shadow_policy_autoresearch_v0",
        BACKTEST_ROOT / "derived" / "completion_candidate_pipeline_v1",
    ]
    rows = []
    for path in candidates:
        size_gib = du_gib(path)
        if size_gib is None:
            continue
        rows.append(
            {
                "path": str(path),
                "size_gib": size_gib,
                "exists": path.exists(),
                "cleanup_authorized": False,
                "recommended_action": "REVIEW_ARCHIVE_OR_MOVE_ONLY_NO_DELETE_AUTHORIZED",
            }
        )
    rows.sort(key=lambda row: row["size_gib"], reverse=True)
    return rows


def preview_script(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M capacity packet is read-only review; cleanup/move requires separate approval' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# CE25 BTC5M Matching Source Capacity Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Current Blocker",
        "",
        f"- current free: {packet['capacity']['current_free_gib']} GiB",
        f"- required minimum: {packet['capacity']['store_min_free_gb']} GiB",
        f"- preferred headroom: {packet['capacity']['preferred_headroom_gb']} GiB",
        f"- gap to minimum: {packet['capacity']['gap_to_minimum_gib']} GiB",
        "",
        "## Largest Review Candidates",
        "",
    ]
    for row in packet["inventory"][:8]:
        lines.append(f"- {row['size_gib']} GiB: `{row['path']}`")
    lines.extend(
        [
            "",
            "No cleanup, move, compression, or deletion is authorized by this packet.",
            "",
            "## Recommended Approval Options",
            "",
            "1. Mount/use an external store/output root with sufficient free space, then regenerate the matching-source packet with that root.",
            "2. Separately approve archive/move of old reviewed stores to external storage, preserving manifests and rollback notes.",
            "3. Separately approve threshold revision only if replay builder memory/temp behavior is reviewed.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    current_free = disk_free_gib(BACKTEST_ROOT)
    gap_min = round(max(0.0, STORE_MIN_FREE_GB - (current_free or 0.0)), 3)
    gap_headroom = round(max(0.0, PREFERRED_HEADROOM_GB - (current_free or 0.0)), 3)
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only capacity inventory for CE25 BTC5M matching-source preapproval blocker",
        "capacity": {
            "backtest_root": str(BACKTEST_ROOT),
            "current_free_gib": current_free,
            "store_min_free_gb": STORE_MIN_FREE_GB,
            "preferred_headroom_gb": PREFERRED_HEADROOM_GB,
            "gap_to_minimum_gib": gap_min,
            "gap_to_headroom_gib": gap_headroom,
            "capacity_ready": gap_min <= 0,
        },
        "inventory": inventory(),
        "source_bindings": {
            "preapproval_packet": {"path": str(PREAPPROVAL_PACKET), "sha256": sha256_file(PREAPPROVAL_PACKET)},
            "chain_validator": {"path": str(CHAIN_VALIDATOR), "sha256": sha256_file(CHAIN_VALIDATOR)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_matching_source_capacity_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_matching_source_capacity_packet.py"),
            },
        },
        "allowed_next_steps": [
            "review external output-root option",
            "prepare separate archive/move approval packet",
            "prepare separate cleanup approval only after owner confirms targets",
        ],
        "non_claims": {
            "cleanup_authorized": False,
            "delete_authorized": False,
            "move_authorized": False,
            "replay_authorized": False,
            "oos_authorized": False,
            "orders_authorized": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    packet_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_CAPACITY_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_CAPACITY_REPORT.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    preview_script(preview_path)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_MATCHING_SOURCE_CAPACITY_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    write_json(OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_CAPACITY_HASH_MANIFEST.json", manifest)
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "current_free_gib": current_free,
                "gap_to_minimum_gib": gap_min,
                "top_candidate_gib": packet["inventory"][0]["size_gib"] if packet["inventory"] else None,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

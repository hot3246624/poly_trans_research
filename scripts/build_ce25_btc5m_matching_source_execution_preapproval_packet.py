#!/usr/bin/env python3
"""Build a review-only execution preapproval packet for CE25 BTC5M matching source.

This packet is intentionally not executable. It binds the reviewed matching
source build packet, dynamic sizing overrides, and current local preflight
state so the next approval discussion has concrete blockers instead of prose.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_matching_source_execution_preapproval_packet_20260606"

MATCHING_SOURCE_PACKET = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
)
MATCHING_SOURCE_MANIFEST = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_HASH_MANIFEST.json"
)
DYNAMIC_SIZING_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "ce25_btc5m_residual_execution_rule_matrix_packet_20260606"
    / "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_PACKET.json"
)
CHAIN_VALIDATOR = ROOT / "scripts" / "validate_ce25_btc5m_research_packet_chain.py"

ARCHIVE_ROOT = Path("/Volumes/PolyData/poly_replay_archive/_archives")
STORE_MIN_FREE_GB = 250
TEMP_MIN_FREE_GB = 250
COMPLETION_MIN_FREE_GB = 120
STATUS_PREFIX = "BLOCKED_CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL"
HIGHEST_SUCCESS = "KEEP_CE25_BTC5M_MATCHING_SOURCE_BUILT_REVIEW_REQUIRED_NOT_OOS_READY"


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


def disk_free_gib(path: Path) -> float | None:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    if not probe.exists():
        return None
    stat = os.statvfs(probe)
    return round(stat.f_bavail * stat.f_frsize / (1024 ** 3), 3)


def preview_script(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M matching-source execution preapproval is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def exact_approval_draft(packet_root: Path, matching_packet: dict[str, Any], blockers: list[str]) -> str:
    blocker_text = ", ".join(blockers) if blockers else "none"
    return "\n".join(
        [
            "DRAFT_NOT_ISSUED",
            "I authorize exactly one CE25_BTC5M matching-source build run, limited to local cold replay/L2 source build, completion event-store build, candidate-base build, official-fee dynamic-sizing state-machine replay, and source-crosswalk artifact generation only.",
            f"The run must use preapproval packet `{packet_root}` and matching-source build packet `{MATCHING_SOURCE_PACKET}`.",
            f"This draft is not issuable while blockers remain: {blocker_text}.",
            "Before any run, the operator must re-run the packet chain validator, confirm archive_root_available=true, confirm local store/temp free space satisfies the reviewed thresholds, verify all bound sha256 hashes, and confirm all planned output directories are fresh.",
            "The run must preserve the bound dynamic sizing override CSV, official fee rate 0.07, BTC-only 2026-05-28..2026-06-04 window, replay -> completion -> candidate-base -> state-machine -> source-crosswalk order, and fail closed on any manifest/hash/schema/count/source-crosswalk drift.",
            "This does not authorize WS, OOS, runner/observer, shared-ingress, private key, candidate import, order/cancel/redeem, canary/live/deploy/funding/latest pointer, private_truth_ready, strategy_promotion_ready, live_ready, deployable, or any readiness/private-truth/promotion claim.",
            f"Highest future success is capped at `{HIGHEST_SUCCESS}`.",
            "",
            "# Bound future command body follows from matching-source packet:",
            *matching_packet["planned_command_body"],
            "",
        ]
    )


def render_report(packet: dict[str, Any]) -> str:
    blockers = packet["execution_readiness"]["blockers"]
    lines = [
        "# CE25 BTC5M Matching Source Execution Preapproval",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "Do not execute. This is a review-only preapproval draft.",
        "",
        "Current blockers:",
        *[f"- {item}" for item in blockers],
        "",
        "## Bound Work",
        "",
        "- local cold replay/L2 source build",
        "- completion event-store build",
        "- BTC 5m candidate-base build",
        "- official-fee dynamic-sizing state-machine replay",
        "- source-crosswalk artifact generation",
        "",
        "## Explicit Non-Claims",
        "",
        "- no WS/OOS/runner/observer",
        "- no shared-ingress",
        "- no private key/import/order/live/canary/deploy/latest",
        "- no private truth/promotion/readiness claim",
    ]
    return "\n".join(lines) + "\n"


def status_for_blockers(blockers: list[str]) -> str:
    has_archive = "ARCHIVE_ROOT_UNAVAILABLE" in blockers
    has_disk = any(item.startswith("STORE_FREE_GB_BELOW_") or item.startswith("TEMP_FREE_GB_BELOW_") for item in blockers)
    if has_archive and has_disk:
        reason = "ARCHIVE_AND_DISK_REQUIRED"
    elif has_archive:
        reason = "ARCHIVE_REQUIRED"
    elif has_disk:
        reason = "DISK_REQUIRED"
    elif blockers:
        reason = "PRECHECK_REQUIRED"
    else:
        reason = "READY_FOR_EXACT_APPROVAL_DISCUSSION"
    return f"{STATUS_PREFIX}_{reason}_NOT_AUTHORIZED_NOT_RUN"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    matching_packet = read_json(MATCHING_SOURCE_PACKET)
    overrides_packet = read_json(DYNAMIC_SIZING_OVERRIDES_PACKET)
    residual_packet = read_json(RESIDUAL_MATRIX_PACKET)
    override_csv = Path(overrides_packet["outputs"]["overrides_csv"])
    backtest_free_gib = disk_free_gib(BACKTEST_ROOT)
    temp_free_gib = disk_free_gib(BACKTEST_ROOT / "tmp")

    blockers: list[str] = []
    if not ARCHIVE_ROOT.is_dir():
        blockers.append("ARCHIVE_ROOT_UNAVAILABLE")
    if backtest_free_gib is None or backtest_free_gib < STORE_MIN_FREE_GB:
        blockers.append(f"STORE_FREE_GB_BELOW_{STORE_MIN_FREE_GB}")
    if temp_free_gib is None or temp_free_gib < TEMP_MIN_FREE_GB:
        blockers.append(f"TEMP_FREE_GB_BELOW_{TEMP_MIN_FREE_GB}")
    status = status_for_blockers(blockers)

    packet = {
        "schema_version": 1,
        "status": status,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "review-only execution preapproval draft for CE25 BTC5M matching-source build",
        "execution_readiness": {
            "exact_approval_issued": False,
            "execution_authorized": False,
            "execution_ready": False,
            "blockers": blockers,
            "archive_root_available": ARCHIVE_ROOT.is_dir(),
            "store_free_gib": backtest_free_gib,
            "temp_free_gib": temp_free_gib,
            "store_min_free_gb": STORE_MIN_FREE_GB,
            "temp_min_free_gb": TEMP_MIN_FREE_GB,
            "completion_min_free_gb": COMPLETION_MIN_FREE_GB,
        },
        "source_bindings": {
            "matching_source_packet": {"path": str(MATCHING_SOURCE_PACKET), "sha256": sha256_file(MATCHING_SOURCE_PACKET)},
            "matching_source_manifest": {
                "path": str(MATCHING_SOURCE_MANIFEST),
                "sha256": sha256_file(MATCHING_SOURCE_MANIFEST),
            },
            "dynamic_sizing_overrides_packet": {
                "path": str(DYNAMIC_SIZING_OVERRIDES_PACKET),
                "sha256": sha256_file(DYNAMIC_SIZING_OVERRIDES_PACKET),
                "row_count": overrides_packet["override_contract"]["row_count"],
            },
            "dynamic_sizing_overrides_csv": {
                "path": str(override_csv),
                "sha256": sha256_file(override_csv),
                "row_count": overrides_packet["override_contract"]["row_count"],
            },
            "residual_matrix_packet": {"path": str(RESIDUAL_MATRIX_PACKET), "sha256": sha256_file(RESIDUAL_MATRIX_PACKET)},
            "chain_validator": {"path": str(CHAIN_VALIDATOR), "sha256": sha256_file(CHAIN_VALIDATOR)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_matching_source_execution_preapproval_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_matching_source_execution_preapproval_packet.py"),
            },
        },
        "planned_command_body": matching_packet["planned_command_body"],
        "required_fresh_preflight": [
            "python3 scripts/validate_ce25_btc5m_research_packet_chain.py returns ok=true and archive_available=true",
            f"df free space at {BACKTEST_ROOT} >= {STORE_MIN_FREE_GB} GiB before replay store build",
            f"df free space at {BACKTEST_ROOT / 'tmp'} >= {TEMP_MIN_FREE_GB} GiB before retained temp sqlite build",
            "all planned output directories must not exist",
            "all source/runtime/override hashes must match packet bindings",
        ],
        "fail_closed_conditions": [
            "archive unavailable",
            "disk free below reviewed threshold",
            "hash drift",
            "output dir exists without separate cleanup approval",
            "dynamic sizing override CSV omitted",
            "any manifest missing after a build phase",
            "source crosswalk overlap remains zero",
            "private/order/live/OOS/shared-ingress/latest path touched",
        ],
        "highest_allowed_status": HIGHEST_SUCCESS,
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
            "execution_authorized": False,
        },
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL_REPORT.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    approval_path = OUTPUT_DIR / "EXACT_APPROVAL_CANDIDATE_DRAFT_NOT_ISSUED.txt"

    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    preview_script(preview_path)
    approval_path.write_text(exact_approval_draft(OUTPUT_DIR, matching_packet, blockers), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    write_json(OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_EXECUTION_PREAPPROVAL_HASH_MANIFEST.json", manifest)

    print(
        json.dumps(
            {
                "status": status,
                "output_dir": str(OUTPUT_DIR),
                "blockers": blockers,
                "store_free_gib": backtest_free_gib,
                "temp_free_gib": temp_free_gib,
                "exact_approval_issued": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

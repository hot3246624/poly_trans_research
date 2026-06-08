#!/usr/bin/env python3
"""Build a review-only local storage cleanup inventory packet.

The packet separates local storage into safe-delete cache/build artifacts,
archive-first evidence stores, and keep-only source/control paths. It does not
delete, move, compress, or mutate any target.
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
WEB3_ROOT = Path("/Users/hot/web3Scientist")
BACKTEST_ROOT = WEB3_ROOT / "poly_backtest_data"
OUTPUT_DIR = ROOT / "data" / "exports" / "local_storage_cleanup_inventory_packet_20260606"
STATUS = "KEEP_LOCAL_STORAGE_CLEANUP_INVENTORY_REVIEWED_EXACT_CLEANUP_APPROVAL_REQUIRED_NOT_MUTATED"


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
    return round(stat.f_bavail * stat.f_frsize / (1024**3), 3)


def du_gib(path: Path) -> float | None:
    if not path.exists():
        return None
    out = subprocess.check_output(["du", "-sk", str(path)], text=True)
    return round(int(out.split()[0]) / (1024**2), 3)


def command_output(args: list[str]) -> str:
    try:
        return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()
    except subprocess.CalledProcessError as exc:
        return exc.output.strip()


def row(
    category: str,
    path: Path,
    reason: str,
    next_action: str,
    risk: str,
    approval_required: bool,
) -> dict[str, Any] | None:
    size = du_gib(path)
    if size is None:
        return None
    return {
        "category": category,
        "path": str(path),
        "size_gib": size,
        "reason": reason,
        "next_action": next_action,
        "risk": risk,
        "approval_required": approval_required,
        "mutation_performed": False,
    }


def build_inventory() -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    specs = [
        (
            "archive_first",
            BACKTEST_ROOT / "verification_store",
            "Replay/verification source-truth store used by historical validation.",
            "Archive or move with manifest preservation before any deletion.",
            "high",
            True,
        ),
        (
            "archive_first",
            BACKTEST_ROOT / "derived",
            "Derived marts, reports, audit packs, strategy gates, and current CE25 outputs.",
            "Archive or prune only by dated packet/output namespace after review.",
            "high",
            True,
        ),
        (
            "archive_first",
            WEB3_ROOT / "pm_as_ofi-xuan-frontier" / ".tmp_xuan" / "local_verifier_artifacts",
            "Large local verifier packets and evidence bundles from B/C/Xuan review lanes.",
            "Archive first; do not delete unreviewed hash/evidence artifacts directly.",
            "high",
            True,
        ),
        (
            "archive_first",
            WEB3_ROOT / "pm_as_ofi-xuan-frontier" / ".tmp_xuan" / "review_worktrees",
            "Temporary review worktrees may contain packet/source snapshots.",
            "Verify git worktree ownership and archive or remove stale closed review trees separately.",
            "medium",
            True,
        ),
        (
            "archive_first",
            WEB3_ROOT / "pm_as_ofi" / "data",
            "Legacy pm_as_ofi data/log/cache tree; may include old local-agg/challenger evidence.",
            "Archive local-agg legacy data before pruning by service/strategy namespace.",
            "medium",
            True,
        ),
        (
            "keep",
            ROOT,
            "Active CE25/BTC5M research repo with dirty worktree and current packet builders.",
            "Keep; do not clean source files through storage cleanup.",
            "high",
            False,
        ),
        (
            "keep",
            WEB3_ROOT / "pm_as_ofi",
            "Legacy repo still contains source/evidence and service history.",
            "Keep source repo; only separately prune data after archive approval.",
            "medium",
            False,
        ),
        (
            "keep",
            WEB3_ROOT / "pm_as_ofi-xuan-frontier",
            "Historical B/C/Xuan source repo and verifier context.",
            "Keep source repo; only separately archive/prune .tmp_xuan namespaces.",
            "medium",
            False,
        ),
        (
            "safe_delete",
            BACKTEST_ROOT / "backtest_cache",
            "Regenerable local cache; already expected to be small or absent after cleanup.",
            "Can be deleted in a separate safe-cache cleanup if present.",
            "low",
            True,
        ),
        (
            "safe_delete",
            WEB3_ROOT / "pm_as_ofi-xuan-frontier" / "target",
            "Regenerable Rust build output; already expected removed.",
            "Can be deleted in a separate safe-cache cleanup if recreated.",
            "low",
            True,
        ),
    ]
    for spec in specs:
        item = row(*spec)
        if item is not None:
            candidates.append(item)
    candidates.sort(key=lambda item: (item["category"] != "archive_first", -item["size_gib"]))
    return candidates


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: local storage cleanup inventory is review-only; no rm/mv/tar is authorized' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# Local Storage Cleanup Inventory Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Summary",
        "",
        f"- filesystem free: {packet['disk']['free_gib']} GiB",
        f"- CE25 matching-source minimum: {packet['disk']['ce25_matching_source_min_free_gib']} GiB",
        f"- gap to CE25 minimum: {packet['disk']['gap_to_ce25_minimum_gib']} GiB",
        f"- safe-delete total currently visible: {packet['totals_by_category'].get('safe_delete', 0.0)} GiB",
        f"- archive-first total currently visible: {packet['totals_by_category'].get('archive_first', 0.0)} GiB",
        "",
        "## Decision Layers",
        "",
    ]
    for category in ["safe_delete", "archive_first", "keep"]:
        rows = [item for item in packet["inventory"] if item["category"] == category]
        if not rows:
            continue
        lines.extend([f"### {category}", ""])
        for item in rows:
            lines.append(f"- {item['size_gib']} GiB `{item['path']}`")
            lines.append(f"  - reason: {item['reason']}")
            lines.append(f"  - next: {item['next_action']}")
        lines.append("")
    lines.extend(
        [
            "## Recommended Next Approval",
            "",
            "1. Do not delete evidence/source-truth stores directly.",
            "2. If freeing local space is required, first archive `archive_first` targets to external storage with sha256 manifests.",
            "3. After archive verification, approve a bounded prune packet by exact path and namespace.",
            "4. If the immediate goal is CE25 matching-source, external output/temp root is cleaner than deleting evidence.",
            "",
            "## Non-Claims",
            "",
            "- cleanup_authorized=false",
            "- delete_authorized=false",
            "- move_authorized=false",
            "- compression_authorized=false",
            "- replay_authorized=false",
            "- oos_authorized=false",
            "- live_ready=false",
            "- deployable=false",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    free = disk_free_gib(WEB3_ROOT)
    min_free = 250.0
    inventory = build_inventory()
    totals: dict[str, float] = {}
    for item in inventory:
        totals[item["category"]] = round(totals.get(item["category"], 0.0) + item["size_gib"], 3)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "read-only local storage cleanup decision inventory",
        "disk": {
            "probe_root": str(WEB3_ROOT),
            "free_gib": free,
            "ce25_matching_source_min_free_gib": min_free,
            "gap_to_ce25_minimum_gib": round(max(0.0, min_free - (free or 0.0)), 3),
        },
        "inventory": inventory,
        "totals_by_category": totals,
        "process_audit": {
            "command": "ps ax -o pid,command",
            "matching_terms": ["build_replay_store", "run_completion_candidate_state_machine", "duckdb", "poly_backtest"],
            "note": "Inventory packet performs no process kill or mutation.",
        },
        "git_worktree_audit": {
            "pm_as_ofi_xuan_frontier": command_output(
                ["git", "-C", str(WEB3_ROOT / "pm_as_ofi-xuan-frontier"), "worktree", "list"]
            ),
        },
        "source_bindings": {
            "build_script": {
                "path": str(ROOT / "scripts" / "build_local_storage_cleanup_inventory_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_local_storage_cleanup_inventory_packet.py"),
            },
            "ce25_chain_validator": {
                "path": str(ROOT / "scripts" / "validate_ce25_btc5m_research_packet_chain.py"),
                "sha256": sha256_file(ROOT / "scripts" / "validate_ce25_btc5m_research_packet_chain.py"),
            },
        },
        "non_claims": {
            "cleanup_authorized": False,
            "delete_authorized": False,
            "move_authorized": False,
            "compression_authorized": False,
            "replay_authorized": False,
            "oos_authorized": False,
            "orders_authorized": False,
            "live_ready": False,
            "deployable": False,
        },
    }

    packet_path = OUTPUT_DIR / "LOCAL_STORAGE_CLEANUP_INVENTORY_PACKET.json"
    report_path = OUTPUT_DIR / "LOCAL_STORAGE_CLEANUP_INVENTORY_REPORT.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    manifest_path = OUTPUT_DIR / "LOCAL_STORAGE_CLEANUP_INVENTORY_HASH_MANIFEST.json"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview_path)

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path == manifest_path or not path.is_file():
            continue
        manifest["files"][path.name] = {
            "path": str(path),
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "free_gib": free,
                "gap_to_ce25_minimum_gib": packet["disk"]["gap_to_ce25_minimum_gib"],
                "totals_by_category": totals,
                "mutation_performed": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

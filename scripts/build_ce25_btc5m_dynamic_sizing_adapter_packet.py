#!/usr/bin/env python3
"""Build a review-only packet for CE25 BTC5M dynamic sizing replay adapter.

The adapter lets the completion state machine replay per-market sizing schedules
from a CSV override file. It does not execute replay by itself.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_dynamic_sizing_adapter_packet_20260606"
STATE_MACHINE = ROOT / "scripts" / "run_completion_candidate_state_machine.py"
STATE_MACHINE_TEST = ROOT / "tests" / "test_completion_candidate_state_machine_schema.py"
SIZING_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_sizing_grid_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_PACKET.json"
)
STATUS = "KEEP_CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_REVIEWED_REPLAY_SOURCE_REQUIRED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M dynamic sizing adapter packet is review-only'\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# CE25 BTC5M Dynamic Sizing Adapter Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## What Changed",
        "",
        "The completion candidate state machine now accepts an optional `--sizing-overrides-csv`.",
        "The CSV can bind `target_qty` and/or `max_open_cost` overrides by `candidate_row_id`, `condition_id`, or `slug`.",
        "Missing override fields inherit the global CLI defaults, and disabled rows fail closed before selection.",
        "",
        "## Replay Boundary",
        "",
        "This packet only reviews adapter implementation shape. It does not authorize matching-source build, replay execution, OOS, WS, canary, live, or orders.",
        "",
        "## Required Before Replay",
        "",
        "- `/Volumes/PolyData/poly_replay_archive/_archives` or an equivalent reviewed source must be available.",
        "- The matching source build packet must be regenerated/reviewed with archive availability true.",
        "- A separate execution approval must bind candidate source, sizing override CSV, runtime hashes, and output namespace.",
        "",
        "## Non-Claims",
        "",
        "- private_truth_ready=false",
        "- strategy_promotion_ready=false",
        "- live_ready=false",
        "- deployable=false",
        "- oos_authorized=false",
        "- replay_authorized=false",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    fixture_dir = OUTPUT_DIR / "fixtures"
    fixture_dir.mkdir(parents=True, exist_ok=True)
    valid_fixture = fixture_dir / "valid_sizing_overrides.csv"
    invalid_fixture = fixture_dir / "invalid_sizing_overrides.csv"
    write_csv(
        valid_fixture,
        [
            {
                "sizing_override_id": "condition_low_cap",
                "candidate_row_id": "",
                "condition_id": "0xabc",
                "slug": "",
                "target_qty": 7,
                "max_open_cost": "",
                "enabled": "true",
            },
            {
                "sizing_override_id": "candidate_tight_cap",
                "candidate_row_id": 42,
                "condition_id": "",
                "slug": "",
                "target_qty": 3,
                "max_open_cost": 12,
                "enabled": "true",
            },
            {
                "sizing_override_id": "disabled_market",
                "candidate_row_id": "",
                "condition_id": "0xdisabled",
                "slug": "",
                "target_qty": "",
                "max_open_cost": "",
                "enabled": "false",
            },
        ],
        ["sizing_override_id", "candidate_row_id", "condition_id", "slug", "target_qty", "max_open_cost", "enabled"],
    )
    write_csv(
        invalid_fixture,
        [
            {
                "sizing_override_id": "bad_negative",
                "candidate_row_id": "",
                "condition_id": "0xabc",
                "slug": "",
                "target_qty": -1,
                "max_open_cost": 10,
                "enabled": "true",
            }
        ],
        ["sizing_override_id", "candidate_row_id", "condition_id", "slug", "target_qty", "max_open_cost", "enabled"],
    )
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "adapter_id": "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_V1",
        "adapter_contract": {
            "cli_arg": "--sizing-overrides-csv",
            "key_priority": ["candidate_row_id", "condition_id", "slug"],
            "supported_override_fields": ["target_qty", "max_open_cost", "enabled"],
            "missing_field_policy": "INHERIT_GLOBAL_DEFAULT",
            "duplicate_key_policy": "FAIL_CLOSED",
            "disabled_row_policy": "FAIL_CLOSED_BEFORE_SELECTION",
            "candidate_id_policy": "CONFIG_NAME_HASH_SUFFIX_FROM_OVERRIDE_CSV_SHA256",
            "output_audit_fields": [
                "target_qty_effective",
                "max_open_cost_effective",
                "sizing_override_id",
                "sizing_override_key_type",
                "sizing_override_key",
            ],
        },
        "source_bindings": {
            "state_machine_script": {"path": str(STATE_MACHINE), "sha256": sha256_file(STATE_MACHINE)},
            "state_machine_tests": {"path": str(STATE_MACHINE_TEST), "sha256": sha256_file(STATE_MACHINE_TEST)},
            "sizing_grid_packet": {"path": str(SIZING_GRID_PACKET), "sha256": sha256_file(SIZING_GRID_PACKET)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_dynamic_sizing_adapter_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_dynamic_sizing_adapter_packet.py"),
            },
        },
        "fixtures": {
            "valid_sizing_overrides_csv": {"path": str(valid_fixture), "sha256": sha256_file(valid_fixture)},
            "invalid_sizing_overrides_csv": {"path": str(invalid_fixture), "sha256": sha256_file(invalid_fixture)},
        },
        "local_verification_commands": [
            "python3 -m py_compile scripts/run_completion_candidate_state_machine.py tests/test_completion_candidate_state_machine_schema.py",
            "uv run --with duckdb python tests/test_completion_candidate_state_machine_schema.py",
            "git diff --check -- scripts/run_completion_candidate_state_machine.py tests/test_completion_candidate_state_machine_schema.py",
        ],
        "matching_source_gate": {
            "status": "BLOCKED_MATCHING_SOURCE_REQUIRED",
            "archive_root": "/Volumes/PolyData/poly_replay_archive/_archives",
            "archive_execution_authorized": False,
        },
        "outputs": {
            "packet": "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_PACKET.json",
            "report": "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_REPORT.md",
            "command_preview_not_authorized": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "replay_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
        },
    }
    packet_path = OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_REPORT.md"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")

    manifest_files = [packet_path, report_path, preview, valid_fixture, invalid_fixture]
    sums_path = OUTPUT_DIR / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUTPUT_DIR)}\n" for path in manifest_files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet": str(packet_path),
                "manifest_count": len(manifest_files),
                "sha256sums": str(sums_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

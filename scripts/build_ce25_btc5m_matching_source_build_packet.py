#!/usr/bin/env python3
"""Prepare a review-only packet for building CE25 BTC 5m matching sources.

The packet is a bridge from public-profile research to replay-backed research.
It does not execute any source build. It records the exact intended labels,
commands, gates, and non-claims needed before a heavy local replay/completion
build can be authorized separately.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_btc5m_matching_source_build_packet_20260605"

GAP_INVENTORY = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_replay_source_gap_inventory_20260605"
    / "CE25_BTC5M_REPLAY_SOURCE_GAP_INVENTORY.json"
)
GAP_MANIFEST = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_replay_source_gap_inventory_20260605"
    / "CE25_BTC5M_REPLAY_SOURCE_GAP_HASH_MANIFEST.json"
)
SOURCE_CROSSWALK_SCRIPT = ROOT / "scripts" / "build_ce25_btc5m_replay_source_crosswalk.py"
REPLAY_STORE_SCRIPT = ROOT / "scripts" / "build_replay_store_v2.py"
COMPLETION_STORE_SCRIPT = ROOT / "scripts" / "build_completion_unwind_event_store_v2.py"
CANDIDATE_BASE_SCRIPT = ROOT / "scripts" / "build_completion_candidate_base.py"
STATE_MACHINE_SCRIPT = ROOT / "scripts" / "run_completion_candidate_state_machine.py"
DYNAMIC_SIZING_ADAPTER_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_dynamic_sizing_adapter_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_PACKET.json"
)
DYNAMIC_SIZING_OVERRIDES_PACKET = (
    ROOT
    / "data"
    / "exports"
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)

ARCHIVE_ROOT = Path("/Volumes/PolyData/poly_replay_archive/_archives")
REPLAY_LABEL = "20260528_20260604_btc_l2"
COMPLETION_LABEL = "20260528_20260604"
CANDIDATE_RUN_NAME = "ce25_btc5m_matching_source_20260528_20260604"
STATE_MACHINE_RUN_NAME = "ce25_btc5m_matching_source_officialfee_dynamic_sizing_v1_20260528_20260604"
DAYS = [
    "2026-05-28",
    "2026-05-29",
    "2026-05-30",
    "2026-05-31",
    "2026-06-01",
    "2026-06-02",
    "2026-06-03",
    "2026-06-04",
]

STATUS = "KEEP_CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET_PREPARED_ARCHIVE_REQUIRED_NOT_RUN_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "orders_authorized": False,
        "canary_authorized": False,
    }


def shell_join_days() -> str:
    return ",".join(DAYS)


def replay_builder_allows_target_days() -> bool:
    text = REPLAY_STORE_SCRIPT.read_text(encoding="utf-8")
    return all(day in text for day in DAYS)


def command_lines() -> list[str]:
    verification_root = BACKTEST_ROOT / "verification_store"
    pipeline_root = BACKTEST_ROOT / "derived" / "completion_candidate_pipeline_v1"
    replay_out = verification_root / "replay_store_multiasset_l2_v1" / REPLAY_LABEL
    completion_out = verification_root / "completion_unwind_event_store_v2" / COMPLETION_LABEL
    candidate_out = pipeline_root / CANDIDATE_RUN_NAME
    state_out = pipeline_root / STATE_MACHINE_RUN_NAME
    temp_parent = BACKTEST_ROOT / "tmp"
    retained_sqlite_root = temp_parent / REPLAY_LABEL
    crosswalk_out = ROOT / "data" / "exports" / "ce25_btc5m_replay_source_crosswalk_matching_source_20260605"
    dynamic_overrides_packet = read_json(DYNAMIC_SIZING_OVERRIDES_PACKET)
    dynamic_overrides_csv = Path(dynamic_overrides_packet["outputs"]["overrides_csv"])
    dynamic_overrides_sha256 = sha256_file(dynamic_overrides_csv)

    return [
        "set -euo pipefail",
        f"test -d {ARCHIVE_ROOT}",
        f"test -f {DYNAMIC_SIZING_OVERRIDES_PACKET}",
        f"test -f {dynamic_overrides_csv}",
        f"printf '%s  %s\\n' {dynamic_overrides_sha256} {dynamic_overrides_csv} | sha256sum -c -",
        f"test ! -e {replay_out}",
        f"test ! -e {completion_out}",
        f"test ! -e {candidate_out}",
        f"test ! -e {state_out}",
        f"test ! -e {retained_sqlite_root}",
        f"test ! -e {crosswalk_out}",
        "python3 - <<'PY'",
        "from pathlib import Path",
        f"script = Path({json.dumps(str(REPLAY_STORE_SCRIPT))}).read_text()",
        f"missing = [day for day in {json.dumps(DAYS)} if day not in script]",
        "raise SystemExit('BLOCKED_REPLAY_BUILDER_TARGET_DAYS_NOT_ALLOWLISTED: ' + ','.join(missing) if missing else 0)",
        "PY",
        f"python3 {REPLAY_STORE_SCRIPT} \\",
        f"  --archive-root {ARCHIVE_ROOT} \\",
        f"  --store-root {verification_root} \\",
        "  --store-name replay_store_multiasset_l2_v1 \\",
        f"  --days {shell_join_days()} \\",
        "  --assets BTC \\",
        "  --tables all \\",
        f"  --label {REPLAY_LABEL} \\",
        f"  --temp-root {temp_parent} \\",
        "  --min-store-free-gb 250 \\",
        "  --min-temp-free-gb 250 \\",
        "  --duckdb-threads 6 \\",
        "  --parallel-days 1 \\",
        "  --keep-temp",
        f"python3 {COMPLETION_STORE_SCRIPT} \\",
        f"  --replay-root {retained_sqlite_root} \\",
        f"  --store-root {verification_root} \\",
        "  --store-name completion_unwind_event_store_v2 \\",
        f"  --days {shell_join_days()} \\",
        f"  --label {COMPLETION_LABEL} \\",
        "  --event-kinds l1_price_change,public_trade \\",
        "  --max-l2-age-ms 3000 \\",
        "  --min-free-gb 120 \\",
        "  --duckdb-threads 6",
        f"python3 {CANDIDATE_BASE_SCRIPT} \\",
        f"  --data-root {BACKTEST_ROOT} \\",
        f"  --store-root {verification_root / 'completion_unwind_event_store_v2'} \\",
        f"  --label {COMPLETION_LABEL} \\",
        "  --market-prefix btc-updown-5m- \\",
        "  --offset-min-s 0 \\",
        "  --offset-max-s 300 \\",
        "  --max-pair-cost 1.01 \\",
        f"  --output-dir {pipeline_root} \\",
        f"  --run-name {CANDIDATE_RUN_NAME} \\",
        "  --duckdb-threads 6",
        f"python3 {STATE_MACHINE_SCRIPT} \\",
        f"  --candidate-base-dir {candidate_out} \\",
        f"  --output-dir {state_out} \\",
        "  --mode passive_redeem \\",
        "  --fee-model official_taker \\",
        "  --official-fee-rate 0.07 \\",
        "  --target-qty 5 \\",
        f"  --sizing-overrides-csv {dynamic_overrides_csv} \\",
        "  --alignment all \\",
        "  --seed-offset-max-s 300 \\",
        "  --seed-l1-pair-cap 1.01 \\",
        "  --cooldown-s 10 \\",
        "  --imbalance-qty-cap 125 \\",
        "  --residual-cooldown-age-s 30 \\",
        "  --residual-cooldown-cost-cap 0.50",
        f"python3 {SOURCE_CROSSWALK_SCRIPT} \\",
        f"  --ledger {ROOT / 'data' / 'exports' / 'ce25_btc5m_broad_profile_candidate_ledger_20260604' / 'ce25_btc5m_broad_profile_candidate_ledger.csv'} \\",
        f"  --replay-actions {state_out / 'actions.csv'} \\",
        f"  --replay-result-manifest {state_out / 'RESULT_SUMMARY_MANIFEST.json'} \\",
        f"  --replay-compliance-manifest {state_out / 'COMPLIANCE_MANIFEST.json'} \\",
        f"  --completion-store-root {BACKTEST_ROOT / 'verification_store' / 'completion_unwind_event_store_v2'} \\",
        f"  --output-dir {crosswalk_out}",
    ]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    gap = read_json(GAP_INVENTORY)
    dynamic_adapter_packet = read_json(DYNAMIC_SIZING_ADAPTER_PACKET)
    dynamic_overrides_packet = read_json(DYNAMIC_SIZING_OVERRIDES_PACKET)
    dynamic_overrides_csv = Path(dynamic_overrides_packet["outputs"]["overrides_csv"])
    archive_root_available = ARCHIVE_ROOT.is_dir()
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "review-only source build packet for CE25 BTC5M profile-window replay/completion source",
        "backtest_v1_boundary": {
            "default_runtime_root": str(BACKTEST_ROOT),
            "normal_research_should_not_require_external_polydata": True,
            "external_polydata_use_case": "cold replay/L2 rebuild only because local Backtest V1 does not contain 2026-05-28..2026-06-04 matching source labels",
            "not_a_default_health_check_or_control_plane_refresh_path": True,
        },
        "input_gap_inventory": {
            "path": str(GAP_INVENTORY),
            "sha256": sha256_file(GAP_INVENTORY),
            "status": gap.get("status"),
        },
        "target_window": {
            "days_utc": DAYS,
            "profile_window_start_utc": "2026-05-28T03:45:00Z",
            "profile_window_end_utc": "2026-06-04T03:45:00Z",
            "asset": "BTC",
            "market_prefix": "btc-updown-5m-",
        },
        "planned_labels": {
            "replay_store_multiasset_l2_v1": REPLAY_LABEL,
            "retained_replay_sqlite_root": str(BACKTEST_ROOT / "tmp" / REPLAY_LABEL),
            "completion_unwind_event_store_v2": COMPLETION_LABEL,
            "candidate_base_run_name": CANDIDATE_RUN_NAME,
            "state_machine_run_name": STATE_MACHINE_RUN_NAME,
        },
        "environment_preflight": {
            "archive_root": str(ARCHIVE_ROOT),
            "archive_root_available_now": archive_root_available,
            "requires_archive_root_available_before_run": True,
            "replay_builder_target_days_allowlisted_now": replay_builder_allows_target_days(),
            "requires_replay_builder_target_days_allowlisted_before_run": True,
            "fresh_output_dirs_required": True,
            "rm_rf_authorized": False,
        },
        "source_scripts": {
            "build_replay_store_v2": {"path": str(REPLAY_STORE_SCRIPT), "sha256": sha256_file(REPLAY_STORE_SCRIPT)},
            "build_completion_unwind_event_store_v2": {
                "path": str(COMPLETION_STORE_SCRIPT),
                "sha256": sha256_file(COMPLETION_STORE_SCRIPT),
            },
            "build_completion_candidate_base": {
                "path": str(CANDIDATE_BASE_SCRIPT),
                "sha256": sha256_file(CANDIDATE_BASE_SCRIPT),
            },
            "run_completion_candidate_state_machine": {
                "path": str(STATE_MACHINE_SCRIPT),
                "sha256": sha256_file(STATE_MACHINE_SCRIPT),
            },
            "dynamic_sizing_adapter_packet": {
                "path": str(DYNAMIC_SIZING_ADAPTER_PACKET),
                "sha256": sha256_file(DYNAMIC_SIZING_ADAPTER_PACKET),
                "status": dynamic_adapter_packet["status"],
            },
            "dynamic_sizing_overrides_packet": {
                "path": str(DYNAMIC_SIZING_OVERRIDES_PACKET),
                "sha256": sha256_file(DYNAMIC_SIZING_OVERRIDES_PACKET),
                "status": dynamic_overrides_packet["status"],
            },
            "dynamic_sizing_overrides_csv": {
                "path": str(dynamic_overrides_csv),
                "sha256": sha256_file(dynamic_overrides_csv),
                "row_count": dynamic_overrides_packet["override_contract"]["row_count"],
            },
            "build_ce25_btc5m_replay_source_crosswalk": {
                "path": str(SOURCE_CROSSWALK_SCRIPT),
                "sha256": sha256_file(SOURCE_CROSSWALK_SCRIPT),
            },
        },
        "planned_command_body": command_lines(),
        "acceptance_gates": {
            "replay_store_manifest_exists": True,
            "completion_v2_manifest_exists": True,
            "candidate_base_manifest_exists": True,
            "state_machine_manifest_exists": True,
            "source_crosswalk_required": True,
            "minimum_crosswalk_condition_overlap_rate": ">0 and review-required; exact threshold not pre-claimed",
            "official_fee_rate": 0.07,
            "dynamic_sizing_overrides_required": True,
            "dynamic_sizing_override_row_count": dynamic_overrides_packet["override_contract"]["row_count"],
            "oos_allowed": False,
        },
        "fail_closed_conditions": [
            "build_replay_store_v2.py valid-day allowlist does not include every target day",
            "archive root unavailable or missing any required day archive",
            "any planned output dir already exists without separate cleanup approval",
            "manifest missing or malformed after any source stage",
            "days/labels/assets drift from packet",
            "raw/collector scan used as readiness without manifest",
            "source crosswalk still has zero shared condition ids",
            "private/order/live/canary/deploy/funding/latest pointer path touched",
        ],
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    preview_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 matching source build is prepared for review only.' >&2",
                "echo 'A separate approval is required before building replay/completion/candidate/state-machine artifacts.' >&2",
                "exit 66",
                "",
                "# Future reviewed command body:",
                *[f"# {line}" for line in command_lines()],
                "",
            ]
        ),
        encoding="utf-8",
    )
    preview_path.chmod(0o755)

    note_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Matching Source Build Packet",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet prepares the next heavy local step, but does not run it.",
                "",
                "Boundary: normal Backtest V1 research uses `/Users/hot/web3Scientist/poly_backtest_data` only. This packet is only for a separately approved cold replay/L2 rebuild if the 2026-05-28..2026-06-04 source window is required and not locally present.",
                "",
                f"- Required window: `{', '.join(DAYS)}`.",
                f"- Required archive root: `{ARCHIVE_ROOT}`.",
                f"- Archive root available during packet preparation: `{archive_root_available}`.",
                "- Intended build chain: replay L2 store -> completion V2 store -> BTC 5m candidate base -> official-fee dynamic-sizing state machine -> source crosswalk.",
                f"- Dynamic sizing override rows bound: `{dynamic_overrides_packet['override_contract']['row_count']}`.",
                "- Highest allowed result remains review-only and not OOS-ready.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        packet_path,
        preview_path,
        note_path,
        GAP_INVENTORY,
        GAP_MANIFEST,
        REPLAY_STORE_SCRIPT,
        COMPLETION_STORE_SCRIPT,
        CANDIDATE_BASE_SCRIPT,
        STATE_MACHINE_SCRIPT,
        DYNAMIC_SIZING_ADAPTER_PACKET,
        DYNAMIC_SIZING_OVERRIDES_PACKET,
        dynamic_overrides_csv,
        SOURCE_CROSSWALK_SCRIPT,
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "artifact_count": len(artifacts),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
            if path.exists()
        ],
        "packet_sha256": sha256_file(packet_path),
        "command_preview_sha256": sha256_file(preview_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_MATCHING_SOURCE_BUILD_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(manifest_path),
                "archive_root_available_now": archive_root_available,
                "days": DAYS,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

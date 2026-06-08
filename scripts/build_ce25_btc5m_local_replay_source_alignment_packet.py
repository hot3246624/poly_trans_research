#!/usr/bin/env python3
"""Build a review-only packet for CE25 BTC5M local replay source alignment.

This corrects the source boundary for the current CE25 BTC5M mainline: local
replay/source-truth coverage is 2026-05-02..2026-05-18. Later public-profile
windows are proxy evidence unless a separate cold archive rebuild is requested.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_local_replay_source_alignment_packet_20260607"

REPLAY_L2_MANIFEST = (
    BT_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/20260502_20260518_l2/REPLAY_STORE_V2_MANIFEST.json"
)
COMPLETION_ROOT = BT_ROOT / "verification_store/completion_unwind_event_store_v2"
COMPLETION_LABELS = [
    "20260502_20260508",
    "20260509",
    "20260510",
    "20260511",
    "20260512",
    "20260513",
    "20260516",
    "20260517",
    "20260518",
]
CANDIDATE_BASE_MANIFEST = (
    BT_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/CANDIDATE_BASE_MANIFEST.json"
)
OFFICIAL_FEE_REPLAY_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DYNAMIC_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606/CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
DYNAMIC_OVERRIDES_CSV = (
    EXPORTS / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606/ce25_btc5m_dynamic_sizing_overrides.csv"
)
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "ce25_btc5m_residual_execution_rule_matrix_packet_20260606/"
    "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_PACKET.json"
)

STATUS = "KEEP_CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_REVIEWED_RESIDUAL_REPLAY_PACKET_PREP_ALLOWED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def csv_row_count(path: Path) -> int:
    with path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def json_binding(path: Path) -> dict[str, Any]:
    return {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 local replay source alignment packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    lines = [
        "# CE25 BTC5M Local Replay Source Alignment Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Corrected Boundary",
        "",
        "- Replay/source-truth mainline: local `2026-05-02..2026-05-18` stores.",
        "- Public-profile proxy windows such as `2026-05-28..2026-06-06` are not replay/source truth.",
        "- `/Volumes/PolyData` cold rebuild is optional future work only, not the current blocker.",
        "",
        "## Bound Local Inputs",
        "",
        f"- L2 replay manifest: `{packet['source_bindings']['replay_l2_manifest']['path']}`",
        f"- completion labels: `{', '.join(packet['local_source_window']['completion_labels'])}`",
        f"- candidate base rows: `{packet['candidate_base']['candidate_row_count']}`",
        f"- existing official-fee replay action rows: `{packet['existing_official_fee_replay']['action_row_count']}`",
        f"- dynamic sizing override rows: `{packet['dynamic_sizing']['override_row_count']}`",
        "",
        "## Next Step",
        "",
        "Prepare or run a local residual-rule replay review using the existing local candidate base plus the bound dynamic sizing override CSV. Do not wait for PolyData unless the user separately requests cold rebuild for newer public-profile windows.",
        "",
        "## Non-Claims",
        "",
        "- replay_execution_authorized=false",
        "- oos_authorized=false",
        "- private_truth_ready=false",
        "- strategy_promotion_ready=false",
        "- live_ready=false",
        "- deployable=false",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    completion_bindings = {}
    missing = []
    for label in COMPLETION_LABELS:
        manifest = COMPLETION_ROOT / label / "EVENT_STORE_MANIFEST.json"
        if not manifest.exists():
            missing.append(label)
            continue
        completion_bindings[label] = json_binding(manifest)

    actions_csv = OFFICIAL_FEE_REPLAY_DIR / "actions.csv"
    result_manifest = OFFICIAL_FEE_REPLAY_DIR / "RESULT_SUMMARY_MANIFEST.json"
    compliance_manifest = OFFICIAL_FEE_REPLAY_DIR / "COMPLIANCE_MANIFEST.json"

    candidate_base_manifest = json.loads(CANDIDATE_BASE_MANIFEST.read_text(encoding="utf-8"))
    candidate_row_count = candidate_base_manifest.get("candidate_row_count", candidate_base_manifest.get("row_count"))

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "scope": "review-only CE25 BTC5M local replay source alignment",
        "local_source_window": {
            "valid_days_utc": [
                "2026-05-02",
                "2026-05-03",
                "2026-05-04",
                "2026-05-05",
                "2026-05-06",
                "2026-05-07",
                "2026-05-08",
                "2026-05-09",
                "2026-05-10",
                "2026-05-11",
                "2026-05-12",
                "2026-05-13",
                "2026-05-16",
                "2026-05-17",
                "2026-05-18",
            ],
            "completion_labels": COMPLETION_LABELS,
            "missing_completion_labels": missing,
            "public_profile_windows_after_2026_05_18_are_proxy_only": True,
            "polydata_cold_rebuild_required_for_current_mainline": False,
        },
        "source_bindings": {
            "replay_l2_manifest": json_binding(REPLAY_L2_MANIFEST),
            "completion_event_store_manifests": completion_bindings,
            "candidate_base_manifest": json_binding(CANDIDATE_BASE_MANIFEST),
            "dynamic_sizing_overrides_packet": json_binding(DYNAMIC_OVERRIDES_PACKET),
            "dynamic_sizing_overrides_csv": json_binding(DYNAMIC_OVERRIDES_CSV),
            "residual_matrix_packet": json_binding(RESIDUAL_MATRIX_PACKET),
            "build_script": json_binding(ROOT / "scripts/build_ce25_btc5m_local_replay_source_alignment_packet.py"),
        },
        "candidate_base": {
            "manifest_path": str(CANDIDATE_BASE_MANIFEST),
            "candidate_row_count": candidate_row_count,
            "source_row_count": candidate_base_manifest.get("source_row_count"),
        },
        "existing_official_fee_replay": {
            "dir": str(OFFICIAL_FEE_REPLAY_DIR),
            "actions_csv": json_binding(actions_csv),
            "result_summary_manifest": json_binding(result_manifest),
            "compliance_manifest": json_binding(compliance_manifest),
            "action_row_count": csv_row_count(actions_csv),
            "dynamic_sizing_overrides_applied": False,
            "interpretation": "existing official-fee baseline; dynamic-sizing replay remains separate next step",
        },
        "dynamic_sizing": {
            "override_csv": str(DYNAMIC_OVERRIDES_CSV),
            "override_row_count": csv_row_count(DYNAMIC_OVERRIDES_CSV),
            "must_bind_for_next_residual_replay": True,
        },
        "recommended_next_steps": [
            "stop treating /Volumes/PolyData archive availability as current mainline blocker",
            "prepare local residual-rule replay packet over existing 2026-05-02..2026-05-18 candidate base",
            "run dynamic-sizing state-machine replay only with separate local execution/review step",
            "keep 2026-05-28..2026-06-06 public profiles as proxy stability/decay evidence only",
        ],
        "highest_allowed_status": STATUS,
        "non_claims": {
            "replay_execution_authorized": False,
            "oos_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_REPORT.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_HASH_MANIFEST.json"
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
        manifest["files"][path.name] = json_binding(path)
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "missing_completion_labels": missing,
                "candidate_row_count": packet["candidate_base"]["candidate_row_count"],
                "action_row_count": packet["existing_official_fee_replay"]["action_row_count"],
                "dynamic_override_row_count": packet["dynamic_sizing"]["override_row_count"],
                "polydata_cold_rebuild_required_for_current_mainline": False,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

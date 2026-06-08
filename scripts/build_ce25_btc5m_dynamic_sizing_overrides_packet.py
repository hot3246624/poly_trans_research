#!/usr/bin/env python3
"""Materialize CE25 BTC5M public-profile sizing schedule as override CSV.

This bridges the public-profile sizing grid to the replay state machine's
`--sizing-overrides-csv` adapter. It is review-only and does not run replay.
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
OUTPUT_DIR = EXPORTS / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
LEDGER_CSV = (
    EXPORTS
    / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
    / "ce25_btc5m_broad_profile_candidate_ledger.csv"
)
SIZING_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_sizing_grid_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_PACKET.json"
)
DYNAMIC_ADAPTER_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_adapter_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_PACKET.json"
)

STATUS = "KEEP_CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_REVIEWED_MATCHING_SOURCE_REQUIRED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def is_last60(row: dict[str, str]) -> bool:
    return row.get("source_last_delta_bucket") == "last_60s"


def is_20_35(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "20-35"


def is_65_80(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "65-80"


def is_down(row: dict[str, str]) -> bool:
    return row.get("source_first_side") == "DOWN"


def is_mid_up(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") in {"35-50", "50-65"} and row.get("source_first_side") == "UP"


def cap_for(row: dict[str, str], schedule: dict[str, Any]) -> float:
    value = float(schedule["base_cap"])
    if is_last60(row):
        value += float(schedule["last60_boost"])
    if is_20_35(row):
        value += float(schedule["low_20_35_boost"])
    if is_65_80(row):
        value += float(schedule["high_65_80_boost"])
    if is_down(row):
        value += float(schedule["down_side_boost"])
    if is_mid_up(row):
        value += float(schedule["mid_up_boost"])
    return min(float(schedule["per_market_cap"]), max(0.0, value))


def write_preview(path: Path, overrides_csv: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M dynamic sizing overrides are review-only'\n"
        "exit 66\n\n"
        "# Future reviewed state-machine argument only after matching source approval:\n"
        f"#   --sizing-overrides-csv {overrides_csv}\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    schedule = packet["schedule"]
    lines = [
        "# CE25 BTC5M Dynamic Sizing Overrides",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Bound Schedule",
        "",
        f"- schedule: `{schedule['schedule_id']}`",
        f"- latest participation: {schedule['latest_window_participation_rate'] * 100:.2f}%",
        f"- public-profile scaled ROI on buy: {schedule['scaled_roi_on_buy'] * 100:.2f}%",
        f"- weighted residual: {schedule['weighted_resid_rate_by_buy'] * 100:.2f}%",
        f"- bad pair-cost >=1 buy share: {schedule['bad_pair_cost_ge_1_buy_share'] * 100:.2f}%",
        "",
        "## Output Contract",
        "",
        "The CSV is keyed by `condition_id=source_condition_id` and sets `max_open_cost` only; `target_qty` is intentionally blank so replay commands can bind a reviewed global target-qty policy.",
        "",
        "## Boundary",
        "",
        "This is a public-profile-derived replay input candidate, not replay/OOS/live evidence. It requires matching source review before use.",
        "",
        "## Non-Claims",
        "",
        "- private_truth_ready=false",
        "- strategy_promotion_ready=false",
        "- live_ready=false",
        "- deployable=false",
        "- replay_authorized=false",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_rows = read_csv(LEDGER_CSV)
    sizing_packet = read_json(SIZING_GRID_PACKET)
    schedule = sizing_packet["decision"]["best_high_coverage_review_candidate"]
    if not schedule:
        raise SystemExit("missing best high-coverage schedule")

    overrides: list[dict[str, Any]] = []
    seen_conditions: set[str] = set()
    for row in ledger_rows:
        condition_id = row["source_condition_id"]
        if condition_id in seen_conditions:
            raise SystemExit(f"duplicate source_condition_id in ledger: {condition_id}")
        seen_conditions.add(condition_id)
        cap = cap_for(row, schedule)
        overrides.append(
            {
                "sizing_override_id": f"{schedule['schedule_id']}:{row['candidate_id']}",
                "candidate_row_id": "",
                "condition_id": condition_id,
                "slug": row["slug"],
                "target_qty": "",
                "max_open_cost": round(cap, 6),
                "enabled": str(cap > 0).lower(),
                "source_candidate_id": row["candidate_id"],
                "source_profile_label": row["source_profile_label"],
                "source_first_price_bucket": row["source_first_price_bucket"],
                "source_last_delta_bucket": row["source_last_delta_bucket"],
                "source_first_side": row["source_first_side"],
            }
        )

    overrides_csv = OUTPUT_DIR / "ce25_btc5m_dynamic_sizing_overrides.csv"
    fieldnames = [
        "sizing_override_id",
        "candidate_row_id",
        "condition_id",
        "slug",
        "target_qty",
        "max_open_cost",
        "enabled",
        "source_candidate_id",
        "source_profile_label",
        "source_first_price_bucket",
        "source_last_delta_bucket",
        "source_first_side",
    ]
    write_csv(overrides_csv, overrides, fieldnames)
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview, overrides_csv)

    max_open_values = [float(row["max_open_cost"]) for row in overrides]
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "adapter_id": "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_V1",
        "schedule": schedule,
        "override_contract": {
            "row_count": len(overrides),
            "unique_condition_count": len(seen_conditions),
            "key_field": "condition_id",
            "target_qty_policy": "BLANK_INHERIT_GLOBAL_REPLAY_DEFAULT",
            "max_open_cost_policy": "PUBLIC_PROFILE_SCHEDULE_CAP_USDC",
            "min_max_open_cost": min(max_open_values) if max_open_values else None,
            "max_max_open_cost": max(max_open_values) if max_open_values else None,
            "enabled_false_count": sum(1 for row in overrides if row["enabled"] == "false"),
        },
        "source_bindings": {
            "ledger_csv": {"path": str(LEDGER_CSV), "sha256": sha256_file(LEDGER_CSV)},
            "sizing_grid_packet": {"path": str(SIZING_GRID_PACKET), "sha256": sha256_file(SIZING_GRID_PACKET)},
            "dynamic_adapter_packet": {"path": str(DYNAMIC_ADAPTER_PACKET), "sha256": sha256_file(DYNAMIC_ADAPTER_PACKET)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_dynamic_sizing_overrides_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_dynamic_sizing_overrides_packet.py"),
            },
        },
        "outputs": {
            "overrides_csv": str(overrides_csv),
            "command_preview_not_authorized": str(preview),
            "report_md": "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_REPORT.md",
            "hash_manifest": "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_HASH_MANIFEST.json",
        },
        "matching_source_gate": {
            "status": "BLOCKED_MATCHING_SOURCE_REQUIRED",
            "archive_root": "/Volumes/PolyData/poly_replay_archive/_archives",
            "replay_authorized": False,
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
    packet_path = OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
    report_path = OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_REPORT.md"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    write_json(OUTPUT_DIR / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_HASH_MANIFEST.json", manifest)
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "override_rows": len(overrides),
                "unique_conditions": len(seen_conditions),
                "overrides_csv": str(overrides_csv),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

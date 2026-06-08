#!/usr/bin/env python3
"""Build CE25 BTC5m residual execution-rule matrix packet.

The public-profile sizing grid showed that high participation cannot get to a
clean residual target by size overlays alone. This packet prepares the next
matching-replay layer: completion, pair-cap, cooldown, imbalance, merge/reuse,
and residual-unwind rules to evaluate once source truth is available.

It is review-only and does not execute replay, OOS, WS, or live paths.
"""

from __future__ import annotations

import csv
import hashlib
import itertools
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_residual_execution_rule_matrix_packet_20260606"

SIZING_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_sizing_grid_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_SIZING_GRID_PACKET.json"
)
CONTROLLER_V1_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_controller_v1_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_PACKET.json"
)
MATCHING_SOURCE_PACKET = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
)
DYNAMIC_SIZING_ADAPTER_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_adapter_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_ADAPTER_PACKET.json"
)
DYNAMIC_SIZING_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_dynamic_sizing_overrides_packet_20260606"
    / "CE25_BTC5M_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
STATE_MACHINE_SCRIPT = ROOT / "scripts" / "run_completion_candidate_state_machine.py"
CANDIDATE_BASE_SCRIPT = ROOT / "scripts" / "build_completion_candidate_base.py"
CAPITAL_LEDGER_SCRIPT = ROOT / "scripts" / "build_xuan_capital_ledger_report.py"
MERGE_TURNOVER_SCRIPT = ROOT / "scripts" / "build_btc_merge_turnover_report.py"

STATUS = "KEEP_CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_REVIEW_ONLY_MATCHING_SOURCE_REQUIRED_NOT_OOS_READY"
OFFICIAL_FEE_RATE = 0.07


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def matrix_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    target_qtys = [2, 5, 8]
    pair_caps = [0.94, 0.96, 0.98, 1.0]
    cooldowns = [5, 10, 20]
    imbalance_qty_caps = [1.25, 5, 15]
    residual_age_s = [5, 15, 30, 60]
    residual_cost_caps = [0.05, 0.10, 0.25, 0.50]
    offset_windows = [
        ("full_5m", 0, 300),
        ("last_60s", 0, 60),
        ("last_120s", 0, 120),
        ("one_to_five_min", 60, 300),
    ]
    run_idx = 0
    for (
        target_qty,
        pair_cap,
        cooldown_s,
        imbalance_qty_cap,
        residual_age,
        residual_cost_cap,
        (offset_label, offset_min, offset_max),
    ) in itertools.product(
        target_qtys,
        pair_caps,
        cooldowns,
        imbalance_qty_caps,
        residual_age_s,
        residual_cost_caps,
        offset_windows,
    ):
        run_idx += 1
        rule_id = (
            f"r{run_idx:04d}_qty{target_qty}_pc{pair_cap:.2f}_cd{cooldown_s}_"
            f"imb{imbalance_qty_cap:g}_rage{residual_age}_rcost{residual_cost_cap:.2f}_{offset_label}"
        ).replace(".", "p")
        rows.append(
            {
                "rule_id": rule_id,
                "implementation_status": "EXISTING_STATE_MACHINE_COMPATIBLE",
                "target_qty": target_qty,
                "seed_l1_pair_cap": pair_cap,
                "cooldown_s": cooldown_s,
                "imbalance_qty_cap": imbalance_qty_cap,
                "residual_cooldown_age_s": residual_age,
                "residual_cooldown_cost_cap": residual_cost_cap,
                "offset_window": offset_label,
                "offset_min_s": offset_min,
                "offset_max_s": offset_max,
                "fee_model": "official_taker",
                "official_fee_rate": OFFICIAL_FEE_RATE,
                "expected_effect": "test whether execution controls reduce residual without killing high-participation economics",
                "future_output_dir": str(
                    BACKTEST_ROOT
                    / "derived"
                    / "completion_candidate_pipeline_v1"
                    / "ce25_btc5m_residual_rule_matrix_20260528_20260604"
                    / rule_id
                ),
            }
        )
    return rows


def priority_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def score(row: dict[str, Any]) -> tuple[Any, ...]:
        return (
            row["offset_window"] == "full_5m",
            row["seed_l1_pair_cap"] in {0.96, 0.98},
            row["residual_cooldown_cost_cap"] in {0.05, 0.10},
            row["target_qty"] in {2, 5},
            -row["cooldown_s"],
        )

    return sorted(rows, key=score, reverse=True)[:48]


def command_preview_lines(priority: list[dict[str, Any]]) -> list[str]:
    base_dir = (
        BACKTEST_ROOT
        / "derived"
        / "completion_candidate_pipeline_v1"
        / "ce25_btc5m_matching_source_20260528_20260604"
    )
    overrides_packet = read_json(DYNAMIC_SIZING_OVERRIDES_PACKET)
    overrides_csv = Path(overrides_packet["outputs"]["overrides_csv"])
    overrides_csv_sha256 = sha256_file(overrides_csv)
    lines = [
        "set -euo pipefail",
        "# NOT AUTHORIZED: exact commands below are future review targets only.",
        f"test -d {base_dir}",
        f"test -f {base_dir / 'CANDIDATE_BASE_MANIFEST.json'}",
        f"test -f {overrides_csv}",
        f"printf '%s  %s\\n' {overrides_csv_sha256} {overrides_csv} | sha256sum -c -",
    ]
    for row in priority[:12]:
        lines.extend(
            [
                f"test ! -e {row['future_output_dir']}",
                f"uv run --with duckdb python {STATE_MACHINE_SCRIPT} \\",
                f"  --candidate-base-dir {base_dir} \\",
                f"  --output-dir {row['future_output_dir']} \\",
                "  --mode passive_redeem \\",
                "  --fee-model official_taker \\",
                f"  --official-fee-rate {OFFICIAL_FEE_RATE} \\",
                f"  --target-qty {row['target_qty']} \\",
                f"  --sizing-overrides-csv {overrides_csv} \\",
                "  --alignment all \\",
                f"  --seed-offset-max-s {row['offset_max_s']} \\",
                f"  --offset-min-s {row['offset_min_s']} \\",
                f"  --offset-max-s {row['offset_max_s']} \\",
                f"  --seed-l1-pair-cap {row['seed_l1_pair_cap']} \\",
                f"  --cooldown-s {row['cooldown_s']} \\",
                f"  --imbalance-qty-cap {row['imbalance_qty_cap']} \\",
                f"  --residual-cooldown-age-s {row['residual_cooldown_age_s']} \\",
                f"  --residual-cooldown-cost-cap {row['residual_cooldown_cost_cap']}",
                f"test -f {Path(row['future_output_dir']) / 'RESULT_SUMMARY_MANIFEST.json'}",
                f"test -f {Path(row['future_output_dir']) / 'COMPLIANCE_MANIFEST.json'}",
            ]
        )
    return lines


def write_not_authorized(path: Path, future_lines: list[str]) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M residual execution matrix is review-only'\n"
        "exit 66\n\n"
        "# Future reviewed command preview:\n"
        + "\n".join(f"# {line}" for line in future_lines)
        + "\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    best = packet["input_sizing_grid_decision"]["best_high_coverage_review_candidate"]
    lines = [
        "# CE25 BTC5M Residual Execution Rule Matrix",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Why This Exists",
        "",
        "The public-profile sizing grid found no high-participation schedule that gets residual below the desired 12%-14% band. This packet moves the next work from sizing labels to execution mechanics: pair caps, cooldown, imbalance controls, residual cooldown, merge/reuse, and official-fee replay.",
        "",
        "## Bound Public-Profile Candidate",
        "",
        f"- schedule: `{best['schedule_id']}`",
        f"- latest participation: {best['latest_window_participation_rate'] * 100:.2f}%",
        f"- public-profile scaled ROI on buy: {best['scaled_roi_on_buy'] * 100:.2f}%",
        f"- weighted residual: {best['weighted_resid_rate_by_buy'] * 100:.2f}%",
        f"- bad pair-cost >=1 share: {best['bad_pair_cost_ge_1_buy_share'] * 100:.2f}%",
        "",
        "## Matrix",
        "",
        f"- total rule rows: {packet['matrix']['rule_count']}",
        f"- priority first-pass rows: {packet['matrix']['priority_rule_count']}",
        "- implementation-compatible rows use the existing `run_completion_candidate_state_machine.py`.",
        "- dynamic public-profile sizing schedule replay has reviewed adapter and override packets, but still needs matching source and a separate execution approval before replay.",
        f"- current best schedule override rows: {packet['matrix']['dynamic_sizing_override_row_count']}",
        "",
        "## Blocking Gate",
        "",
        "Matching source for 2026-05-28..2026-06-04 is still required before running this matrix. Current archive availability is recorded in the packet; no replay/OOS/live path is authorized.",
        "",
        "## Acceptance Targets For Future Replay",
        "",
        "- source crosswalk overlap > 0 and reviewed",
        "- official fee model `fee = C * feeRate * p * (1-p)` with feeRate=0.07",
        "- residual_qty_rate target <=14% for high-participation candidate, <=12% preferred",
        "- bad pair-cost >=1 buy share must improve materially versus public-profile proxy",
        "- fee_after_pnl positive, worst day non-catastrophic, capital ledger review required",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = matrix_rows()
    priority = priority_rows(rows)
    dynamic_overrides_packet = read_json(DYNAMIC_SIZING_OVERRIDES_PACKET)
    dynamic_overrides_csv = Path(dynamic_overrides_packet["outputs"]["overrides_csv"])

    matrix_csv = OUTPUT_DIR / "ce25_btc5m_residual_execution_rule_matrix.csv"
    priority_csv = OUTPUT_DIR / "ce25_btc5m_residual_execution_rule_matrix_priority48.csv"
    fields = list(rows[0].keys())
    write_csv(matrix_csv, rows, fields)
    write_csv(priority_csv, priority, fields)

    future_lines = command_preview_lines(priority)
    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_not_authorized(preview, future_lines)

    sizing_packet = read_json(SIZING_GRID_PACKET)
    matching_packet = read_json(MATCHING_SOURCE_PACKET)
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "strategy_owner_line": "CE25_BROAD_RESEARCH",
        "input_sizing_grid_decision": sizing_packet["decision"],
        "matrix": {
            "rule_count": len(rows),
            "priority_rule_count": len(priority),
            "state_machine_compatible": True,
            "dynamic_sizing_schedule_adapter_required": True,
            "dynamic_sizing_schedule_adapter_status": "IMPLEMENTED_REVIEW_PACKET_READY_MATCHING_SOURCE_REQUIRED",
            "dynamic_sizing_overrides_status": "REVIEW_PACKET_READY_MATCHING_SOURCE_REQUIRED",
            "dynamic_sizing_override_row_count": dynamic_overrides_packet["override_contract"]["row_count"],
        },
        "future_replay_acceptance_targets": {
            "high_participation_min_latest_participation_rate": 0.80,
            "residual_qty_rate_review_target": 0.14,
            "residual_qty_rate_preferred": 0.12,
            "bad_pair_cost_ge_1_buy_share_must_improve": True,
            "fee_after_pnl_positive_required": True,
            "capital_ledger_required": True,
            "source_crosswalk_overlap_gt_zero_required": True,
        },
        "source_bindings": {
            "sizing_grid_packet": {"path": str(SIZING_GRID_PACKET), "sha256": sha256_file(SIZING_GRID_PACKET)},
            "dynamic_sizing_adapter_packet": {
                "path": str(DYNAMIC_SIZING_ADAPTER_PACKET),
                "sha256": sha256_file(DYNAMIC_SIZING_ADAPTER_PACKET),
            },
            "dynamic_sizing_overrides_packet": {
                "path": str(DYNAMIC_SIZING_OVERRIDES_PACKET),
                "sha256": sha256_file(DYNAMIC_SIZING_OVERRIDES_PACKET),
            },
            "dynamic_sizing_overrides_csv": {
                "path": str(dynamic_overrides_csv),
                "sha256": sha256_file(dynamic_overrides_csv),
                "row_count": dynamic_overrides_packet["override_contract"]["row_count"],
            },
            "controller_v1_packet": {"path": str(CONTROLLER_V1_PACKET), "sha256": sha256_file(CONTROLLER_V1_PACKET)},
            "matching_source_packet": {"path": str(MATCHING_SOURCE_PACKET), "sha256": sha256_file(MATCHING_SOURCE_PACKET)},
            "state_machine_script": {"path": str(STATE_MACHINE_SCRIPT), "sha256": sha256_file(STATE_MACHINE_SCRIPT)},
            "candidate_base_script": {"path": str(CANDIDATE_BASE_SCRIPT), "sha256": sha256_file(CANDIDATE_BASE_SCRIPT)},
            "capital_ledger_script": {"path": str(CAPITAL_LEDGER_SCRIPT), "sha256": sha256_file(CAPITAL_LEDGER_SCRIPT)},
            "merge_turnover_script": {"path": str(MERGE_TURNOVER_SCRIPT), "sha256": sha256_file(MERGE_TURNOVER_SCRIPT)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_residual_execution_rule_matrix_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_residual_execution_rule_matrix_packet.py"),
            },
        },
        "matching_source_gate": {
            "status": matching_packet.get("status"),
            "archive_root_available_now": matching_packet.get("environment_preflight", {}).get("archive_root_available_now"),
            "replay_builder_target_days_allowlisted_now": matching_packet.get("environment_preflight", {}).get("replay_builder_target_days_allowlisted_now"),
        },
        "official_fee_contract": {
            "fee_rate": OFFICIAL_FEE_RATE,
            "formula": "fee = C * feeRate * p * (1 - p)",
            "source": "https://docs.polymarket.com/trading/fees",
        },
        "outputs": {
            "matrix_csv": str(matrix_csv),
            "priority_csv": str(priority_csv),
            "command_preview_not_authorized": str(preview),
        },
        "future_command_preview_lines": future_lines,
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
        },
        "highest_allowed_status": STATUS,
    }
    report = OUTPUT_DIR / "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_REPORT.md"
    report.write_text(render_report(packet), encoding="utf-8")
    packet["outputs"]["report_md"] = str(report)
    packet_path = OUTPUT_DIR / "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_PACKET.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_RESIDUAL_EXECUTION_RULE_MATRIX_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "rule_count": len(rows),
                "priority_rule_count": len(priority),
                "archive_root_available_now": packet["matching_source_gate"]["archive_root_available_now"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

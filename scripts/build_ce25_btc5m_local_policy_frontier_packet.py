#!/usr/bin/env python3
"""Build a CE25 BTC5M local policy frontier review packet.

This packet compares completed local Backtest V1 state-machine runs with
explicitly labeled partial diagnostics from interrupted runs. It does not start
replay, WS, OOS, or live paths.
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
OUT = EXPORTS / "ce25_btc5m_local_policy_frontier_packet_20260607"

RESULT_ROOT = BT_ROOT / "derived/completion_candidate_pipeline_v1"
SOURCE_ALIGNMENT_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_replay_source_alignment_packet_20260607"
    / "CE25_BTC5M_LOCAL_REPLAY_SOURCE_ALIGNMENT_PACKET.json"
)
LOCAL_RESIDUAL_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_residual_replay_smoke_packet_20260607"
    / "CE25_BTC5M_LOCAL_RESIDUAL_REPLAY_SMOKE_PACKET.json"
)
LOCAL_DYNAMIC_OVERRIDES_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_dynamic_sizing_overrides_packet_20260607"
    / "CE25_BTC5M_LOCAL_DYNAMIC_SIZING_OVERRIDES_PACKET.json"
)
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"
BUILDER = ROOT / "scripts/build_ce25_btc5m_local_policy_frontier_packet.py"
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"

FULL_RUNS = [
    {
        "run_id": "baseline_offset120_imb125",
        "frontier_role": "old shorter-window reference",
        "path": RESULT_ROOT
        / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2",
    },
    {
        "run_id": "broad_offset300_imb125",
        "frontier_role": "stable lower residual complete run",
        "path": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "broad_qty5_pc102_seed300_cd5_imb125_rage30_rcost050_full_5m",
    },
    {
        "run_id": "broad_offset300_imb250",
        "frontier_role": "current primary complete run",
        "path": RESULT_ROOT
        / "ce25_btc5m_local_residual_rule_replay_20260607"
        / "broad_qty5_pc102_seed300_cd5_imb250_rage30_rcost050_full_5m",
    },
    {
        "run_id": "dynamic_no_last60_imb250",
        "frontier_role": "max_open_cost override no-op proof",
        "path": RESULT_ROOT
        / "ce25_btc5m_local_dynamic_sizing_replay_20260607"
        / "no_last60_base8_l6020_low5_hi10_down3_midup5_cap30_seed300_pc102_imb250",
    },
]

PARTIAL_DIAGNOSTICS = [
    {
        "run_id": "partial_corrected_imb175",
        "frontier_role": "interrupted diagnostic, corrected imbalance unit",
        "path": RESULT_ROOT
        / "ce25_btc5m_local_policy_matrix_20260607"
        / "imb175_cd5_age30_rc050_corrected",
        "parameter_note": "imbalance_qty_cap=1.75; no RESULT_SUMMARY_MANIFEST because run was stopped during tail write/compliance stage",
        "usable_for": "directional frontier only",
    },
    {
        "run_id": "abandoned_wrong_imb75",
        "frontier_role": "abandoned parameter-unit mistake",
        "path": RESULT_ROOT
        / "ce25_btc5m_local_policy_matrix_20260607"
        / "imb75_cd5_age30_rc050",
        "parameter_note": "invalid: imbalance_qty_cap was passed as 75 instead of 0.75",
        "usable_for": "not usable except as operator error audit",
    },
]

STATUS = (
    "KEEP_CE25_BTC5M_LOCAL_POLICY_FRONTIER_REVIEWED_"
    "IMB250_PRIMARY_FAST_EVALUATOR_REQUIRED_NOT_OOS_READY"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


def fnum(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def binding(path: Path, required: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    elif required:
        out["missing_required"] = True
    return out


def full_run_row(item: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(item["path"])
    result_path = run_dir / "RESULT_SUMMARY_MANIFEST.json"
    compliance_path = run_dir / "COMPLIANCE_MANIFEST.json"
    result = read_json(result_path)
    core = result.get("core_metrics") or {}
    config = result.get("config") or {}
    compliance = result.get("compliance_summary") or {}
    return {
        "run_id": item["run_id"],
        "frontier_role": item["frontier_role"],
        "evidence_level": "COMPLETE_RESULT_MANIFEST",
        "path": str(run_dir),
        "result_manifest_sha256": sha256_file(result_path),
        "compliance_manifest_sha256": sha256_file(compliance_path) if compliance_path.exists() else None,
        "status": result.get("status"),
        "target_qty": config.get("target_qty"),
        "seed_offset_max_s": config.get("seed_offset_max_s"),
        "seed_l1_pair_cap": config.get("seed_l1_pair_cap"),
        "cooldown_s": config.get("cooldown_s"),
        "imbalance_qty_cap": config.get("imbalance_qty_cap"),
        "residual_cooldown_age_s": config.get("residual_cooldown_age_s"),
        "residual_cooldown_cost_cap": config.get("residual_cooldown_cost_cap"),
        "selected_candidate_count": core.get("selected_candidate_count"),
        "active_markets": core.get("active_markets"),
        "gross_buy_cost": core.get("gross_buy_cost"),
        "official_taker_fee": core.get("official_taker_fee"),
        "net_pnl": core.get("net_pnl"),
        "net_roi": core.get("net_roi"),
        "weighted_pair_cost": core.get("weighted_pair_cost"),
        "pair_share_rate": core.get("pair_share_rate"),
        "residual_qty_rate": core.get("residual_qty_rate"),
        "residual_cost_rate": core.get("residual_cost_rate"),
        "seed_block_cooldown": core.get("seed_block_cooldown"),
        "seed_block_imbalance_qty": core.get("seed_block_imbalance_qty"),
        "seed_block_residual_cooldown": core.get("seed_block_residual_cooldown"),
        "legacy_strict_cache_pass": compliance.get("strict_cache_pass"),
        "usable_for_ranking": True,
    }


def partial_row(item: dict[str, Any]) -> dict[str, Any]:
    run_dir = Path(item["path"])
    summary_path = run_dir / "summary_by_day.csv"
    sums: dict[str, float] = {
        "candidate_count": 0.0,
        "active_markets": 0.0,
        "seed_actions": 0.0,
        "pair_actions": 0.0,
        "gross_buy_qty": 0.0,
        "gross_buy_cost": 0.0,
        "pair_qty": 0.0,
        "pair_pnl": 0.0,
        "actual_settle_pnl": 0.0,
        "official_taker_fee": 0.0,
        "fee_after_pnl": 0.0,
        "residual_qty": 0.0,
        "residual_cost": 0.0,
    }
    day_rows = 0
    if summary_path.exists():
        with summary_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                day_rows += 1
                for key in sums:
                    sums[key] += fnum(row.get(key))
    buy_qty = sums["gross_buy_qty"]
    buy_cost = sums["gross_buy_cost"]
    pair_qty = sums["pair_qty"]
    return {
        "run_id": item["run_id"],
        "frontier_role": item["frontier_role"],
        "evidence_level": "PARTIAL_SUMMARY_BY_DAY_INTERRUPTED_NO_MANIFEST",
        "path": str(run_dir),
        "summary_by_day_sha256": sha256_file(summary_path) if summary_path.exists() else None,
        "status": "PARTIAL_DIAGNOSTIC_NOT_COMPLIANCE_RANKABLE",
        "parameter_note": item["parameter_note"],
        "usable_for": item["usable_for"],
        "day_rows": day_rows,
        "selected_candidate_count": int(sums["seed_actions"]),
        "active_markets": int(sums["active_markets"]),
        "gross_buy_cost": round(sums["gross_buy_cost"], 6),
        "official_taker_fee": round(sums["official_taker_fee"], 6),
        "net_pnl": round(sums["fee_after_pnl"], 6),
        "net_roi": round(sums["fee_after_pnl"] / buy_cost, 9) if buy_cost else None,
        "weighted_pair_cost": round((pair_qty - sums["pair_pnl"]) / pair_qty, 9) if pair_qty else None,
        "pair_share_rate": round((2 * pair_qty) / buy_qty, 9) if buy_qty else None,
        "residual_qty_rate": round(sums["residual_qty"] / buy_qty, 9) if buy_qty else None,
        "residual_cost_rate": round(sums["residual_cost"] / buy_cost, 9) if buy_cost else None,
        "legacy_strict_cache_pass": None,
        "usable_for_ranking": False,
    }


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M local policy frontier packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    complete = [row for row in rows if row.get("evidence_level") == "COMPLETE_RESULT_MANIFEST"]
    best_complete = max(complete, key=lambda row: fnum(row.get("net_pnl"))) if complete else None
    partial_175 = next((row for row in rows if row["run_id"] == "partial_corrected_imb175"), None)
    lines = [
        "# CE25 BTC5M Local Policy Frontier Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "The current complete-run primary remains `broad_offset300_imb250`. The corrected `imbalance_qty_cap=1.75` partial diagnostic sits between 1.25 and 2.50, but it is not rankable because the run was interrupted before result/compliance manifests were written.",
        "",
        "| run | evidence | imb cap | actions | markets | net pnl | roi | residual qty | residual cost | pair share | usable for ranking |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for row in rows:
        lines.append(
            "| {run} | {evidence} | {imb} | {actions} | {markets} | {pnl} | {roi} | {residq} | {residc} | {pairshare} | {usable} |".format(
                run=row["run_id"],
                evidence=row.get("evidence_level"),
                imb=row.get("imbalance_qty_cap", ""),
                actions=row.get("selected_candidate_count", ""),
                markets=row.get("active_markets", ""),
                pnl=round(fnum(row.get("net_pnl")), 6) if row.get("net_pnl") is not None else "",
                roi=f"{100 * fnum(row.get('net_roi')):.2f}%" if row.get("net_roi") is not None else "",
                residq=f"{100 * fnum(row.get('residual_qty_rate')):.2f}%"
                if row.get("residual_qty_rate") is not None
                else "",
                residc=f"{100 * fnum(row.get('residual_cost_rate')):.2f}%"
                if row.get("residual_cost_rate") is not None
                else "",
                pairshare=f"{100 * fnum(row.get('pair_share_rate')):.2f}%"
                if row.get("pair_share_rate") is not None
                else "",
                usable=row.get("usable_for_ranking"),
            )
        )
    lines.extend(
        [
            "",
            "## Findings",
            "",
        ]
    )
    if best_complete:
        lines.extend(
            [
                f"- Best complete run by fee-after PnL: `{best_complete['run_id']}`.",
                f"- Complete-run net PnL / ROI: `{best_complete.get('net_pnl')}` / `{best_complete.get('net_roi')}`.",
                f"- Complete-run residual qty/cost rate: `{best_complete.get('residual_qty_rate')}` / `{best_complete.get('residual_cost_rate')}`.",
            ]
        )
    if partial_175:
        lines.extend(
            [
                f"- `imbalance_qty_cap=1.75` partial diagnostic PnL / ROI: `{partial_175.get('net_pnl')}` / `{partial_175.get('net_roi')}`.",
                f"- `imbalance_qty_cap=1.75` partial residual qty/cost rate: `{partial_175.get('residual_qty_rate')}` / `{partial_175.get('residual_cost_rate')}`.",
                "- Because the run lacks `RESULT_SUMMARY_MANIFEST.json` and `COMPLIANCE_MANIFEST.json`, it is directional only.",
            ]
        )
    lines.extend(
        [
            "- The attempted broad shell matrix was stopped because the heavy state-machine writes full actions/registry/duckdb/compliance per variant; it is too slow for autoresearch iteration.",
            "- The `abandoned_wrong_imb75` row records a parameter-unit mistake (`75` instead of `0.75`) and is not strategy evidence.",
            "",
            "## Next Step",
            "",
            "Build a fast in-memory/local evaluator or batched state-machine mode that can run the corrected policy grid without writing full per-variant artifacts. The next grid should test imbalance caps `1.50, 1.75, 2.00, 2.25, 2.50`, cooldown `0, 2, 5, 10`, and residual cooldown caps only after the fast evaluator reproduces the complete `imb250` metrics within tolerance.",
            "",
            "## Non-Claims",
            "",
            "- oos_authorized=false",
            "- runner_authorized=false",
            "- private_truth_ready=false",
            "- strategy_promotion_ready=false",
            "- live_ready=false",
            "- deployable=false",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = [full_run_row(item) for item in FULL_RUNS]
    rows.extend(partial_row(item) for item in PARTIAL_DIAGNOSTICS)

    preview = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)
    fields = [
        "run_id",
        "frontier_role",
        "evidence_level",
        "status",
        "target_qty",
        "seed_offset_max_s",
        "seed_l1_pair_cap",
        "cooldown_s",
        "imbalance_qty_cap",
        "residual_cooldown_age_s",
        "residual_cooldown_cost_cap",
        "selected_candidate_count",
        "active_markets",
        "gross_buy_cost",
        "official_taker_fee",
        "net_pnl",
        "net_roi",
        "weighted_pair_cost",
        "pair_share_rate",
        "residual_qty_rate",
        "residual_cost_rate",
        "legacy_strict_cache_pass",
        "usable_for_ranking",
        "path",
    ]
    csv_path = OUT / "ce25_btc5m_local_policy_frontier_summary.csv"
    write_csv(csv_path, rows, fields)

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "review-only local Backtest V1 CE25 BTC5M policy frontier over 2026-05-02..2026-05-18",
        "source_bindings": {
            "source_alignment_packet": binding(SOURCE_ALIGNMENT_PACKET),
            "local_residual_packet": binding(LOCAL_RESIDUAL_PACKET),
            "local_dynamic_overrides_packet": binding(LOCAL_DYNAMIC_OVERRIDES_PACKET),
            "state_machine_script": binding(STATE_MACHINE),
            "validator": binding(VALIDATOR),
            "builder": binding(BUILDER),
        },
        "runs": rows,
        "decision": {
            "primary_complete_run": "broad_offset300_imb250",
            "primary_reason": "highest complete-run fee-after PnL while residual qty remains near 3.33% and residual cost near 1.35%",
            "partial_imb175_interpretation": "directionally useful frontier midpoint; not compliance-rankable without result/compliance manifests",
            "abandoned_wrong_imb75_interpretation": "operator parameter-unit error; must not be used as evidence",
            "max_open_cost_caps_not_binding": True,
            "next_engineering_step": "fast in-memory or batched evaluator before larger policy grid",
        },
        "next_grid_after_fast_reproducer": {
            "imbalance_qty_cap": [1.50, 1.75, 2.00, 2.25, 2.50],
            "cooldown_s": [0, 2, 5, 10],
            "residual_cooldown_cost_cap": [0.25, 0.50, 0.75, 1.00],
            "require_reproduce_primary_imb250_before_sweep": True,
        },
        "outputs": {
            "packet": "CE25_BTC5M_LOCAL_POLICY_FRONTIER_PACKET.json",
            "report": "CE25_BTC5M_LOCAL_POLICY_FRONTIER_REPORT.md",
            "summary_csv": "ce25_btc5m_local_policy_frontier_summary.csv",
            "command_preview": "COMMAND_PREVIEW_NOT_AUTHORIZED.sh",
            "sha256sums": "SHA256SUMS.txt",
        },
        "non_claims": {
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "highest_allowed_status": STATUS,
    }
    packet_path = OUT / "CE25_BTC5M_LOCAL_POLICY_FRONTIER_PACKET.json"
    report_path = OUT / "CE25_BTC5M_LOCAL_POLICY_FRONTIER_REPORT.md"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet, rows), encoding="utf-8")

    manifest_files = [packet_path, report_path, csv_path, preview]
    sums_path = OUT / "SHA256SUMS.txt"
    sums_path.write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUT)}\n" for path in manifest_files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUT),
                "packet": str(packet_path),
                "report": str(report_path),
                "summary_csv": str(csv_path),
                "primary_complete_run": packet["decision"]["primary_complete_run"],
                "sha256sums": str(sums_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

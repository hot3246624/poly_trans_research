#!/usr/bin/env python3
"""Run a fast in-memory CE25 BTC5M local policy grid.

The heavy state-machine runner writes full action/registry/compliance artifacts
per variant. This packet uses the same state-machine core in memory, verifies
that the primary complete run is reproduced, and then runs a bounded policy
grid without creating per-variant execution artifacts.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_local_fast_policy_grid_packet_20260607"

CANDIDATE_BASE_DIR = (
    BT_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
PRIMARY_COMPLETE_RUN = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_residual_rule_replay_20260607"
    / "broad_qty5_pc102_seed300_cd5_imb250_rage30_rcost050_full_5m"
    / "RESULT_SUMMARY_MANIFEST.json"
)
POLICY_FRONTIER_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_policy_frontier_packet_20260607"
    / "CE25_BTC5M_LOCAL_POLICY_FRONTIER_PACKET.json"
)
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"
BUILDER = ROOT / "scripts/build_ce25_btc5m_local_fast_policy_grid_packet.py"
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"


def load_state_machine_module() -> Any:
    spec = importlib.util.spec_from_file_location("run_completion_candidate_state_machine", STATE_MACHINE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load state-machine module from {STATE_MACHINE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sm = load_state_machine_module()

STATUS = (
    "KEEP_CE25_BTC5M_LOCAL_FAST_POLICY_GRID_REVIEWED_CD0_WATCH_IMB250_PRIMARY_"
    "FULL_ARTIFACT_AND_THROUGHPUT_REVIEW_REQUIRED_NOT_OOS_READY"
)
OFFICIAL_FEE_RATE = 0.07
TOLERANCE = 1e-9


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


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def fnum(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def base_args(**overrides: Any) -> argparse.Namespace:
    values: dict[str, Any] = {
        "candidate_base_dir": CANDIDATE_BASE_DIR,
        "output_dir": None,
        "mode": "passive_redeem",
        "edge": 0.055,
        "target_qty": 5.0,
        "sizing_overrides_csv": None,
        "sizing_overrides": {},
        "sizing_overrides_sha256": None,
        "sizing_overrides_key_count": 0,
        "alignment": "all",
        "seed_px_lo": 0.05,
        "seed_px_hi": 0.90,
        "fill_haircut": 0.25,
        "max_seed_qty": 60.0,
        "max_open_cost": 250.0,
        "min_seed_px": 0.01,
        "seed_offset_max_s": 300.0,
        "seed_l1_pair_cap": 1.02,
        "cooldown_s": 5.0,
        "imbalance_qty_cap": 2.5,
        "imbalance_cost_cap": 1_000_000_000.0,
        "residual_cooldown_age_s": 30.0,
        "residual_cooldown_cost_cap": 0.5,
        "fee_model": "official_taker",
        "official_fee_rate": OFFICIAL_FEE_RATE,
        "flat_notional_fee_rate": 0.0,
        "dust_qty": 1.0,
        "offset_min_s": 0.0,
        "offset_max_s": 300.0,
        "public_trade_taker_side": "SELL",
        "public_audit_window_ms": 1000,
        "force": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def variants() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"variant_id": "reproducer_imb250_cd5_rc050_age30", "group": "reproducer"},
    ]
    for imb in (1.50, 1.75, 2.00, 2.25, 2.50, 3.00, 3.50):
        rows.append({"variant_id": f"imb{int(imb * 100):03d}_cd5_rc050_age30", "group": "imbalance", "imbalance_qty_cap": imb})
    for cooldown in (0.0, 2.0, 5.0, 10.0):
        rows.append({"variant_id": f"imb250_cd{int(cooldown):02d}_rc050_age30", "group": "cooldown", "cooldown_s": cooldown})
    for rcost in (0.25, 0.50, 0.75, 1.00):
        rows.append(
            {
                "variant_id": f"imb250_cd5_rc{int(rcost * 100):03d}_age30",
                "group": "residual_cost_cap",
                "residual_cooldown_cost_cap": rcost,
            }
        )
    for age in (15.0, 30.0, 60.0):
        rows.append({"variant_id": f"imb250_cd5_rc050_age{int(age):03d}", "group": "residual_age", "residual_cooldown_age_s": age})

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[float, float, float, float]] = set()
    for row in rows:
        args = base_args(**{k: v for k, v in row.items() if k not in {"variant_id", "group"}})
        key = (
            float(args.imbalance_qty_cap),
            float(args.cooldown_s),
            float(args.residual_cooldown_age_s),
            float(args.residual_cooldown_cost_cap),
        )
        if key in seen:
            continue
        seen.add(key)
        row["parameters"] = {
            "imbalance_qty_cap": args.imbalance_qty_cap,
            "cooldown_s": args.cooldown_s,
            "residual_cooldown_age_s": args.residual_cooldown_age_s,
            "residual_cooldown_cost_cap": args.residual_cooldown_cost_cap,
        }
        deduped.append(row)
    return deduped


def run_variant(con: duckdb.DuckDBPyConnection, manifest: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    args = base_args(**variant["parameters"])
    started = time.perf_counter()
    actions, metrics, daily_rows, residual_rows = sm.run_passive_redeem(args, con, manifest)
    elapsed = time.perf_counter() - started
    return {
        "variant_id": variant["variant_id"],
        "group": variant["group"],
        **variant["parameters"],
        "elapsed_s": round(elapsed, 3),
        "action_count": len(actions),
        "daily_row_count": len(daily_rows),
        "residual_lot_row_count": len(residual_rows),
        "selected_candidate_count": metrics.get("selected_candidate_count"),
        "active_markets": metrics.get("active_markets"),
        "gross_buy_cost": metrics.get("gross_buy_cost"),
        "official_taker_fee": metrics.get("official_taker_fee"),
        "net_pnl": metrics.get("net_pnl"),
        "net_roi": metrics.get("net_roi"),
        "weighted_pair_cost": metrics.get("weighted_pair_cost"),
        "pair_share_rate": metrics.get("pair_share_rate"),
        "residual_qty_rate": metrics.get("residual_qty_rate"),
        "residual_cost_rate": metrics.get("residual_cost_rate"),
        "seed_block_cooldown": metrics.get("seed_block_cooldown"),
        "seed_block_imbalance_qty": metrics.get("seed_block_imbalance_qty"),
        "seed_block_residual_cooldown": metrics.get("seed_block_residual_cooldown"),
        "metrics": metrics,
    }


def metric_delta(row: dict[str, Any], primary: dict[str, Any]) -> dict[str, Any]:
    deltas: dict[str, Any] = {}
    for key in (
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
    ):
        deltas[key] = round(fnum(row.get(key)) - fnum(primary.get(key)), 9)
    return deltas


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
        "echo 'NOT_AUTHORIZED: CE25 BTC5M fast policy grid packet is review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    primary = packet["primary_reproducer"]
    ranked = sorted(rows, key=lambda row: fnum(row.get("net_pnl")), reverse=True)
    lines = [
        "# CE25 BTC5M Local Fast Policy Grid Packet",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        f"The in-memory evaluator reproduced the complete `imb250` primary within tolerance: `{packet['reproducer_check']['ok']}`.",
        "The fast grid found that reducing seed cooldown is the strongest local replay lever. The current complete-run primary remains `imb250` until the selected watch variant is rerun with full artifact/compliance output and a throughput/queue feasibility audit.",
        "",
        "| rank | variant | group | imb | cd | rc age | rc cap | pnl | roi | residual qty | residual cost | pair share | actions |",
        "| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for idx, row in enumerate(ranked[:12], start=1):
        lines.append(
            "| {idx} | {variant} | {group} | {imb} | {cd} | {age} | {rcost} | {pnl} | {roi} | {residq} | {residc} | {pairshare} | {actions} |".format(
                idx=idx,
                variant=row["variant_id"],
                group=row["group"],
                imb=row["imbalance_qty_cap"],
                cd=row["cooldown_s"],
                age=row["residual_cooldown_age_s"],
                rcost=row["residual_cooldown_cost_cap"],
                pnl=round(fnum(row.get("net_pnl")), 6),
                roi=f"{100 * fnum(row.get('net_roi')):.2f}%",
                residq=f"{100 * fnum(row.get('residual_qty_rate')):.2f}%",
                residc=f"{100 * fnum(row.get('residual_cost_rate')):.2f}%",
                pairshare=f"{100 * fnum(row.get('pair_share_rate')):.2f}%",
                actions=row.get("selected_candidate_count"),
            )
        )
    lines.extend(
        [
            "",
            "## Primary Reference",
            "",
            f"- primary variant: `{primary['variant_id']}`",
            f"- net PnL / ROI: `{primary['net_pnl']}` / `{primary['net_roi']}`",
            f"- residual qty/cost: `{primary['residual_qty_rate']}` / `{primary['residual_cost_rate']}`",
            "",
            "## Next Step",
            "",
            f"Run one full heavy artifact/compliance rerun only for `{packet['decision']['watch_variant']}` if the user wants to validate the cooldown watch path. Add a throughput/queue feasibility audit before any OOS discussion. Do not run the broad heavy matrix.",
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
    manifest = sm.load_candidate_manifest(CANDIDATE_BASE_DIR)
    db_path = CANDIDATE_BASE_DIR / str(manifest.get("outputs", {}).get("duckdb", "candidate_base.duckdb"))
    con = duckdb.connect(str(db_path), read_only=True)
    rows = [run_variant(con, manifest, variant) for variant in variants()]
    con.close()

    primary_complete = read_json(PRIMARY_COMPLETE_RUN)
    primary_metrics = primary_complete["core_metrics"]
    primary = next(row for row in rows if row["variant_id"] == "reproducer_imb250_cd5_rc050_age30")
    repro_deltas = metric_delta(primary, primary_metrics)
    repro_ok = all(abs(float(value)) <= TOLERANCE for value in repro_deltas.values())
    for row in rows:
        row["delta_vs_primary"] = metric_delta(row, primary)

    ranked = sorted(rows, key=lambda row: fnum(row.get("net_pnl")), reverse=True)
    watch = ranked[0]
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "review-only fast in-memory local Backtest V1 policy grid over 2026-05-02..2026-05-18",
        "source_bindings": {
            "candidate_base_manifest": binding(CANDIDATE_BASE_DIR / "CANDIDATE_BASE_MANIFEST.json"),
            "primary_complete_result_manifest": binding(PRIMARY_COMPLETE_RUN),
            "policy_frontier_packet": binding(POLICY_FRONTIER_PACKET),
            "state_machine_script": binding(STATE_MACHINE),
            "validator": binding(VALIDATOR),
            "builder": binding(BUILDER),
        },
        "evaluator": {
            "mode": "in_memory_same_run_passive_redeem_core_no_actions_artifact_no_compliance_artifact",
            "reproducer_tolerance": TOLERANCE,
            "variant_count": len(rows),
            "writes_full_variant_artifacts": False,
        },
        "reproducer_check": {
            "ok": repro_ok,
            "deltas_vs_primary_complete_result": repro_deltas,
        },
        "primary_reproducer": primary,
        "ranked_variants": ranked,
        "decision": {
            "current_primary_complete_run": "broad_offset300_imb250",
            "watch_variant": watch["variant_id"],
            "watch_variant_family": watch["group"],
            "watch_variant_requires_full_artifact_rerun": True,
            "watch_variant_requires_throughput_queue_feasibility_audit": watch["group"] == "cooldown",
            "broad_heavy_matrix_disallowed_by_default": True,
            "interpretation": (
                "fast grid is research-only. A cooldown watch variant can be promoted only to a "
                "full local artifact/compliance rerun plus executable throughput and queue feasibility review, not OOS/live."
            ),
        },
        "outputs": {
            "packet": "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_PACKET.json",
            "report": "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_REPORT.md",
            "summary_csv": "ce25_btc5m_local_fast_policy_grid_summary.csv",
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

    packet_path = OUT / "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_PACKET.json"
    report_path = OUT / "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_REPORT.md"
    csv_path = OUT / "ce25_btc5m_local_fast_policy_grid_summary.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet, rows), encoding="utf-8")
    fields = [
        "variant_id",
        "group",
        "imbalance_qty_cap",
        "cooldown_s",
        "residual_cooldown_age_s",
        "residual_cooldown_cost_cap",
        "elapsed_s",
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
        "seed_block_cooldown",
        "seed_block_imbalance_qty",
        "seed_block_residual_cooldown",
    ]
    write_csv(csv_path, rows, fields)
    write_preview(preview_path)
    manifest_files = [packet_path, report_path, csv_path, preview_path]
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
                "variant_count": len(rows),
                "reproducer_ok": repro_ok,
                "watch_variant": watch["variant_id"],
                "watch_variant_net_pnl": watch["net_pnl"],
                "sha256sums": str(sums_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

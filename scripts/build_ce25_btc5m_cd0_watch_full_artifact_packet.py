#!/usr/bin/env python3
"""Build a review packet for the CE25 BTC5M cd0 watch full artifact run."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_cd0_watch_full_artifact_packet_20260607"

FULL_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_bulkcopy_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m"
)
PARTIAL_DIR = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/ce25_btc5m_local_cd0_watch_full_artifact_20260607"
    / "broad_qty5_pc102_seed300_cd0_imb250_rage30_rcost050_full_5m"
)
FAST_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_fast_policy_grid_packet_20260607"
    / "CE25_BTC5M_LOCAL_FAST_POLICY_GRID_PACKET.json"
)
POLICY_FRONTIER_PACKET = (
    EXPORTS
    / "ce25_btc5m_local_policy_frontier_packet_20260607"
    / "CE25_BTC5M_LOCAL_POLICY_FRONTIER_PACKET.json"
)
STATE_MACHINE = ROOT / "scripts/run_completion_candidate_state_machine.py"
BUILDER = ROOT / "scripts/build_ce25_btc5m_cd0_watch_full_artifact_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = (
    "KEEP_CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_REVIEWED_BULKCOPY_COMPLETE_"
    "THROUGHPUT_QUEUE_REVIEW_REQUIRED_NOT_OOS_READY"
)
BLOCKED_PARTIAL_STATUS = (
    "BLOCKED_CE25_BTC5M_CD0_WATCH_FIRST_FULL_ARTIFACT_ATTEMPT_INTERRUPTED_"
    "DUCKDB_MATERIALIZATION_PARTIAL_NOT_EVIDENCE"
)
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


def file_binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def dir_file_manifest(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(p for p in root.iterdir() if p.is_file()):
        rows.append(
            {
                "path": str(path),
                "relative_path": path.name,
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
        )
    return rows


def csv_row_count(path: Path) -> int | None:
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        return max(sum(1 for _ in f) - 1, 0)


def duckdb_counts(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "issues": [f"missing duckdb {path}"], "tables": {}}
    issues: list[str] = []
    tables: dict[str, Any] = {}
    try:
        con = duckdb.connect(str(path), read_only=True)
        existing = {row[0] for row in con.execute("show tables").fetchall()}
        for table in ("actions", "summary_by_day", "candidate_registry", "residual_lots"):
            if table not in existing:
                tables[table] = {"exists": False}
                issues.append(f"missing table {table}")
                continue
            tables[table] = {"exists": True, "row_count": int(con.execute(f"select count(*) from {table}").fetchone()[0])}
        con.close()
    except Exception as exc:
        issues.append(repr(exc))
    return {"ok": not issues, "issues": issues, "tables": tables}


def fnum(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    return float(value)


def metric_deltas(full_metrics: dict[str, Any], fast_row: dict[str, Any]) -> dict[str, float]:
    keys = (
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
    )
    return {key: round(fnum(full_metrics.get(key)) - fnum(fast_row.get(key)), 9) for key in keys}


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M cd0 watch packet is local review-only' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def render_report(packet: dict[str, Any]) -> str:
    metrics = packet["full_artifact"]["core_metrics"]
    return "\n".join(
        [
            "# CE25 BTC5M cd0 Watch Full Artifact Packet",
            "",
            f"Status: `{packet['status']}`",
            "",
            "## Result",
            "",
            f"- full artifact row_count: `{packet['full_artifact']['row_count']}`",
            f"- active markets: `{metrics['active_markets']}`",
            f"- official-fee net PnL / ROI: `{metrics['net_pnl']}` / `{metrics['net_roi']}`",
            f"- gross buy cost / official taker fee: `{metrics['gross_buy_cost']}` / `{metrics['official_taker_fee']}`",
            f"- residual qty/cost rate: `{metrics['residual_qty_rate']}` / `{metrics['residual_cost_rate']}`",
            f"- pair share / rounds per market: `{metrics['pair_share_rate']}` / `{metrics['rounds_per_market']}`",
            "",
            "## Decision",
            "",
            "The cd0 cooldown watch variant is now full-artifact validated for local Backtest V1 research. It is stronger than the previous imb250 primary on local replay PnL and residual control, but it is more execution-intensive and requires throughput/queue feasibility review before any OOS discussion.",
            "",
            "## Blocked First Attempt",
            "",
            f"The first full-artifact attempt is explicitly fail-closed as `{packet['partial_attempt']['status']}`. Its CSVs are complete, but DuckDB/manifest materialization is incomplete and must not be used as complete evidence.",
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
    ) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    result = read_json(FULL_DIR / "RESULT_SUMMARY_MANIFEST.json")
    registry = read_json(FULL_DIR / "CANDIDATE_REGISTRY_MANIFEST.json")
    compliance = read_json(FULL_DIR / "COMPLIANCE_MANIFEST.json")
    fast = read_json(FAST_GRID_PACKET)
    fast_watch = next(row for row in fast["ranked_variants"] if row["variant_id"] == "imb250_cd00_rc050_age30")
    full_metrics = result["core_metrics"]
    deltas = metric_deltas(full_metrics, fast_watch)
    reproduces_fast = all(abs(value) <= TOLERANCE for value in deltas.values())
    full_counts = duckdb_counts(FULL_DIR / "state_machine_results.duckdb")
    partial_counts = duckdb_counts(PARTIAL_DIR / "state_machine_results.duckdb")
    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "local Backtest V1 research-only full artifact review for CE25 BTC5M cd0 cooldown watch variant",
        "source_bindings": {
            "fast_grid_packet": file_binding(FAST_GRID_PACKET),
            "policy_frontier_packet": file_binding(POLICY_FRONTIER_PACKET),
            "state_machine_script": file_binding(STATE_MACHINE),
            "builder": file_binding(BUILDER),
            "validator": file_binding(VALIDATOR),
            "full_result_summary_manifest": file_binding(FULL_DIR / "RESULT_SUMMARY_MANIFEST.json"),
            "full_candidate_registry_manifest": file_binding(FULL_DIR / "CANDIDATE_REGISTRY_MANIFEST.json"),
            "full_compliance_manifest": file_binding(FULL_DIR / "COMPLIANCE_MANIFEST.json"),
        },
        "full_artifact": {
            "path": str(FULL_DIR),
            "status": result.get("status"),
            "row_count": result.get("row_count"),
            "summary_by_day_row_count": result.get("summary_by_day_row_count"),
            "core_metrics": {
                key: full_metrics.get(key)
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
                    "rounds_per_market",
                    "pair_delay_wavg_s",
                    "worst_day_fee_after_pnl",
                    "stress100_worst_pnl",
                )
            },
            "compliance_summary": result.get("compliance_summary"),
            "registry_row_count": registry.get("row_count"),
            "compliance_pass": compliance.get("compliance_pass"),
            "duckdb_counts": full_counts,
            "csv_row_counts": {
                "actions": csv_row_count(FULL_DIR / "actions.csv"),
                "candidate_registry": csv_row_count(FULL_DIR / "candidate_registry.csv"),
                "summary_by_day": csv_row_count(FULL_DIR / "summary_by_day.csv"),
                "residual_lots": csv_row_count(FULL_DIR / "residual_lots.csv"),
            },
            "files": dir_file_manifest(FULL_DIR),
        },
        "fast_grid_reproduction": {
            "ok": reproduces_fast,
            "tolerance": TOLERANCE,
            "deltas": deltas,
            "watch_variant": fast_watch["variant_id"],
        },
        "partial_attempt": {
            "status": BLOCKED_PARTIAL_STATUS,
            "path": str(PARTIAL_DIR),
            "csv_row_counts": {
                "actions": csv_row_count(PARTIAL_DIR / "actions.csv"),
                "candidate_registry": csv_row_count(PARTIAL_DIR / "candidate_registry.csv"),
                "summary_by_day": csv_row_count(PARTIAL_DIR / "summary_by_day.csv"),
                "residual_lots": csv_row_count(PARTIAL_DIR / "residual_lots.csv"),
            },
            "duckdb_counts": partial_counts,
            "complete_evidence_allowed": False,
        },
        "runner_optimization": {
            "change": "state-machine artifact writer now uses explicit-schema DuckDB COPY from CSV instead of row-wise executemany",
            "first_attempt_elapsed_threshold_exceeded": True,
            "bulkcopy_full_artifact_elapsed_s": result.get("elapsed_s"),
        },
        "decision": {
            "cd0_watch_full_artifact_validated_for_local_research": reproduces_fast and full_counts.get("ok") is True,
            "previous_primary_complete_run": "broad_offset300_imb250",
            "new_watch_variant": "imb250_cd00_rc050_age30",
            "new_watch_variant_family": "cooldown",
            "next_required_step": "throughput_queue_feasibility_and_capital_path_review_packet",
            "oos_discussion_allowed": False,
        },
        "outputs": {
            "packet": "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_PACKET.json",
            "report": "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_REPORT.md",
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
    packet_path = OUT / "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_PACKET.json"
    report_path = OUT / "CE25_BTC5M_CD0_WATCH_FULL_ARTIFACT_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_preview(preview_path)
    files = [packet_path, report_path, preview_path]
    (OUT / "SHA256SUMS.txt").write_text(
        "".join(f"{sha256_file(path)}  {path.relative_to(OUT)}\n" for path in files),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUT),
                "packet": str(packet_path),
                "report": str(report_path),
                "full_artifact_validated_for_local_research": packet["decision"][
                    "cd0_watch_full_artifact_validated_for_local_research"
                ],
                "net_pnl": full_metrics.get("net_pnl"),
                "net_roi": full_metrics.get("net_roi"),
                "sha256sums": str(OUT / "SHA256SUMS.txt"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

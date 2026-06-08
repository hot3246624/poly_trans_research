#!/usr/bin/env python3
"""Summarize the current BTC-only completion candidate pipeline.

Reads only the published local completion pipeline manifests and its
state_machine_results.duckdb small result tables. This is research-only output;
it is not private truth and not a promotion gate.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_RESULT_DIR = Path(
    "/Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/"
    "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def query_all(con: Any, sql: str) -> list[dict[str, Any]]:
    return [dict(zip([d[0] for d in con.description], row)) for row in con.execute(sql).fetchall()]


def render_report(report: dict[str, Any]) -> str:
    metrics = report["core_metrics"]
    lines = [
        "# BTC-only Completion Candidate Summary",
        "",
        "## Scope",
        "",
        f"- result_dir: `{report['result_dir']}`",
        f"- days: `{report['day_count']}`",
        f"- status: `{report['status']}`",
        f"- research_only: `{report['research_only']}`",
        f"- private_truth_ready: `{report['private_truth_ready']}`",
        f"- promotion_gate_pass: `{report['promotion_gate_pass']}`",
        f"- raw/replay/collector scanned: `{report['raw_scanned']}/{report['replay_scanned']}/{report['collector_scanned']}`",
        "",
        "## Core Metrics",
        "",
        f"- candidate_count: `{metrics.get('candidate_count')}`",
        f"- pair_actions: `{metrics.get('pair_actions')}`",
        f"- pair_qty: `{metrics.get('pair_qty')}`",
        f"- net_pair_cost_wavg: `{metrics.get('net_pair_cost_wavg')}`",
        f"- fee_after_pnl: `{metrics.get('fee_after_pnl')}`",
        f"- stress100_worst_pnl: `{metrics.get('stress100_worst_pnl')}`",
        f"- qty_residual_rate: `{metrics.get('qty_residual_rate')}`",
        "",
        "## Weak Days",
        "",
        "| day | fee_after_pnl | stress100_worst_pnl | pair_cost_wavg | residual_rate | pair_actions |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in report["weak_days"]:
        lines.append(
            f"| {row['day']} | {row['fee_after_pnl']} | {row['stress100_worst_pnl']} | "
            f"{row['pair_cost_wavg']} | {row['qty_residual_rate']} | {row['pair_actions']} |"
        )
    lines.extend(
        [
            "",
            "## Offset Buckets",
            "",
            "| offset_bucket | actions | avg_seed_px | avg_l1_pair_ask | avg_pair_cost_after_seed | public_audit_nearby_fill_rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["offset_buckets"]:
        lines.append(
            f"| {row['offset_bucket']} | {row['actions']} | {row['avg_seed_px']} | "
            f"{row['avg_l1_pair_ask']} | {row['avg_pair_cost_after_seed']} | {row['public_audit_nearby_fill_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Seed Price Buckets",
            "",
            "| seed_px_bucket | actions | avg_l1_pair_ask | avg_pair_cost_after_seed | public_audit_nearby_fill_rate |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["seed_price_buckets"]:
        lines.append(
            f"| {row['seed_px_bucket']} | {row['actions']} | {row['avg_l1_pair_ask']} | "
            f"{row['avg_pair_cost_after_seed']} | {row['public_audit_nearby_fill_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Residual Pressure",
            "",
            "| day | side | lots | qty | cost | pnl | avg_age_s |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["residual_pressure"]:
        lines.append(
            f"| {row['day']} | {row['side']} | {row['lots']} | {row['qty']} | "
            f"{row['cost']} | {row['pnl']} | {row['avg_age_s']} |"
        )
    lines.extend(
        [
            "",
            "## Public Audit Proxy By Day",
            "",
            "| day | actions | public_audit_nearby_fill_rate | avg_seed_px | avg_pair_cost_after_seed |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["public_audit_proxy_by_day"]:
        lines.append(
            f"| {row['day']} | {row['actions']} | {row['public_audit_nearby_fill_rate']} | "
            f"{row['avg_seed_px']} | {row['avg_pair_cost_after_seed']} |"
        )
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- This is BTC-only local backtest research, not deployable evidence.",
            "- Public-account audit is proxy evidence only and private_truth_ready remains false.",
            "- Promotion remains blocked until owner private truth and live-safe gates exist.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, default=DEFAULT_RESULT_DIR)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    try:
        import duckdb  # type: ignore
    except ModuleNotFoundError as exc:
        raise SystemExit("duckdb is required; run with `uv run python`") from exc

    result_dir = args.result_dir.resolve()
    result_manifest = read_json(result_dir / "RESULT_SUMMARY_MANIFEST.json")
    compliance_manifest = read_json(result_dir / "COMPLIANCE_MANIFEST.json")
    db_path = result_dir / "state_machine_results.duckdb"
    if not db_path.exists():
        raise SystemExit(f"missing duckdb result: {db_path}")

    if args.output_dir is None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path.cwd() / ".tmp" / f"btc_only_completion_candidate_summary_{stamp}"
    else:
        output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        weak_days = query_all(
            con,
            """
            SELECT day,
                   round(fee_after_pnl, 6) AS fee_after_pnl,
                   round(stress100_worst_pnl, 6) AS stress100_worst_pnl,
                   round(pair_cost_wavg, 6) AS pair_cost_wavg,
                   round(qty_residual_rate, 6) AS qty_residual_rate,
                   pair_actions
            FROM summary_by_day
            ORDER BY fee_after_pnl ASC
            LIMIT 5
            """,
        )
        offset_buckets = query_all(
            con,
            """
            SELECT CASE
                     WHEN offset_s < 30 THEN '000-030'
                     WHEN offset_s < 60 THEN '030-060'
                     WHEN offset_s < 120 THEN '060-120'
                     WHEN offset_s < 180 THEN '120-180'
                     ELSE '180-240'
                   END AS offset_bucket,
                   count(*) AS actions,
                   round(avg(seed_px), 6) AS avg_seed_px,
                   round(avg(l1_pair_ask), 6) AS avg_l1_pair_ask,
                   round(avg(pair_cost_wavg_after_seed), 6) AS avg_pair_cost_after_seed,
                   round(avg(CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END), 6)
                     AS public_audit_nearby_fill_rate
            FROM candidate_registry
            GROUP BY 1
            ORDER BY 1
            """,
        )
        seed_price_buckets = query_all(
            con,
            """
            SELECT CASE
                     WHEN seed_px < 0.20 THEN '0.00-0.20'
                     WHEN seed_px < 0.35 THEN '0.20-0.35'
                     WHEN seed_px < 0.50 THEN '0.35-0.50'
                     WHEN seed_px < 0.65 THEN '0.50-0.65'
                     ELSE '0.65-0.90'
                   END AS seed_px_bucket,
                   count(*) AS actions,
                   round(avg(l1_pair_ask), 6) AS avg_l1_pair_ask,
                   round(avg(pair_cost_wavg_after_seed), 6) AS avg_pair_cost_after_seed,
                   round(avg(CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END), 6)
                     AS public_audit_nearby_fill_rate
            FROM candidate_registry
            GROUP BY 1
            ORDER BY 1
            """,
        )
        residual_pressure = query_all(
            con,
            """
            SELECT day,
                   side,
                   count(*) AS lots,
                   round(sum(qty), 6) AS qty,
                   round(sum(cost), 6) AS cost,
                   round(sum(pnl), 6) AS pnl,
                   round(avg(age_s), 6) AS avg_age_s
            FROM residual_lots
            GROUP BY 1, 2
            ORDER BY cost DESC
            LIMIT 10
            """,
        )
        public_audit_proxy_by_day = query_all(
            con,
            """
            SELECT day,
                   count(*) AS actions,
                   round(avg(CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END), 6)
                     AS public_audit_nearby_fill_rate,
                   round(avg(seed_px), 6) AS avg_seed_px,
                   round(avg(pair_cost_wavg_after_seed), 6) AS avg_pair_cost_after_seed
            FROM candidate_registry
            GROUP BY 1
            ORDER BY public_audit_nearby_fill_rate ASC, day
            """,
        )
    finally:
        con.close()

    report = {
        "generated_at_utc": utc_now(),
        "result_dir": str(result_dir),
        "status": result_manifest.get("status"),
        "research_only": result_manifest.get("status") == "PASS_LOCAL_COMPLETION_RESEARCH_ONLY",
        "private_truth_ready": False,
        "deployable": False,
        "promotion_gate_pass": compliance_manifest.get("promotion_gate_pass"),
        "raw_scanned": result_manifest.get("raw_scanned"),
        "replay_scanned": result_manifest.get("replay_scanned"),
        "collector_scanned": result_manifest.get("collector_scanned"),
        "day_count": len(result_manifest.get("days") or []),
        "days": result_manifest.get("days") or [],
        "labels": result_manifest.get("labels") or [],
        "core_metrics": result_manifest.get("core_metrics") or {},
        "weak_days": weak_days,
        "offset_buckets": offset_buckets,
        "seed_price_buckets": seed_price_buckets,
        "residual_pressure": residual_pressure,
        "public_audit_proxy_by_day": public_audit_proxy_by_day,
    }
    report["decision"] = "KEEP_BTC_ONLY_COMPLETION_CANDIDATE_SUMMARY_READY_RESEARCH_ONLY"
    report["outputs"] = {
        "summary_json": str((output_dir / "BTC_ONLY_COMPLETION_CANDIDATE_SUMMARY.json").resolve()),
        "report_md": str((output_dir / "BTC_ONLY_COMPLETION_CANDIDATE_SUMMARY.md").resolve()),
    }
    (output_dir / "BTC_ONLY_COMPLETION_CANDIDATE_SUMMARY.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "BTC_ONLY_COMPLETION_CANDIDATE_SUMMARY.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

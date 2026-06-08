#!/usr/bin/env python3
"""Analyze residual-risk frontiers for the BTC-only completion candidate.

This reads only the existing BTC-only state_machine_results.duckdb and local
manifests. It does not rerun the state machine and does not scan raw/replay
stores. The output is a research-only map of where residual risk concentrates.
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


def query(con: Any, sql: str) -> list[dict[str, Any]]:
    rows = con.execute(sql).fetchall()
    fields = [desc[0] for desc in con.description]
    return [dict(zip(fields, row)) for row in rows]


def assert_research_only(result_manifest: dict[str, Any], compliance_manifest: dict[str, Any]) -> None:
    if result_manifest.get("assets") != ["BTC"]:
        raise SystemExit("expected BTC-only result manifest")
    if result_manifest.get("market_prefix") != ["btc-updown-5m-"]:
        raise SystemExit("expected btc-updown-5m- market prefix")
    if result_manifest.get("raw_scanned") or result_manifest.get("replay_scanned") or result_manifest.get("collector_scanned"):
        raise SystemExit("result manifest unexpectedly reports raw/replay/collector scan")
    if result_manifest.get("can_support_strategy_promotion") is not False:
        raise SystemExit("result manifest unexpectedly supports strategy promotion")
    if compliance_manifest.get("promotion_gate_pass") is not False:
        raise SystemExit("compliance manifest unexpectedly passes promotion gate")


def render_table(lines: list[str], headers: list[str], rows: list[dict[str, Any]]) -> None:
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(h, "")) for h in headers) + " |")


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC-only Completion Risk Frontier",
        "",
        "## Decision",
        "",
        f"- decision: `{report['decision']}`",
        f"- research_only: `{report['research_only']}`",
        f"- private_truth_ready: `{report['private_truth_ready']}`",
        f"- promotion_gate_pass: `{report['promotion_gate_pass']}`",
        f"- residual_join_rate: `{report['residual_join_coverage']['join_rate']}`",
        "",
        "## Main Finding",
        "",
        "- Residual risk is not evenly distributed. It is concentrated in later 60-120s actions, high seed price, and high post-seed pair-cost states.",
        "- This does not prove a filter will improve final PnL because the state machine is path-dependent; it identifies the next bounded reruns to test.",
        "",
        "## Residual By Pair Cost After Seed",
        "",
    ]
    render_table(
        lines,
        ["bucket", "actions", "residual_lots", "residual_cost", "residual_cost_per_1k_actions", "audit_fill_rate"],
        report["pair_cost_frontier"],
    )
    lines.extend(["", "## Residual By Seed Price", ""])
    render_table(
        lines,
        ["bucket", "actions", "residual_lots", "residual_cost", "residual_cost_per_1k_actions", "audit_fill_rate"],
        report["seed_px_frontier"],
    )
    lines.extend(["", "## Residual By Offset", ""])
    render_table(
        lines,
        ["bucket", "actions", "residual_lots", "residual_cost", "residual_cost_per_1k_actions", "audit_fill_rate"],
        report["offset_frontier"],
    )
    lines.extend(["", "## Residual By Alignment And Side", ""])
    render_table(
        lines,
        ["side_alignment", "side", "actions", "residual_lots", "residual_cost", "residual_cost_per_1k_actions", "audit_fill_rate"],
        report["alignment_side_frontier"],
    )
    lines.extend(
        [
            "",
            "## Next Bounded Reruns",
            "",
        ]
    )
    for item in report["next_bounded_reruns"]:
        lines.append(f"- `{item['label']}`: {item['command']}")
    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            "- These are research-only local reruns over the existing candidate base.",
            "- Do not use public audit proxy as private owner truth.",
            "- Promotion remains false until owner private truth and live-safe gates exist.",
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
    assert_research_only(result_manifest, compliance_manifest)

    if args.output_dir is None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path.cwd() / ".tmp" / f"btc_only_completion_risk_frontier_{stamp}"
    else:
        output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(result_dir / "state_machine_results.duckdb"), read_only=True)
    try:
        residual_join = query(
            con,
            """
            SELECT count(*) AS residual_lots,
                   sum(CASE WHEN c.action_id IS NOT NULL THEN 1 ELSE 0 END) AS joined_lots,
                   round(avg(CASE WHEN c.action_id IS NOT NULL THEN 1.0 ELSE 0.0 END), 6) AS join_rate
            FROM residual_lots r
            LEFT JOIN candidate_registry c ON cast(c.action_id AS varchar) = r.source_seed_action_id
            """,
        )[0]
        pair_cost_frontier = query(
            con,
            """
            WITH action_bucket AS (
              SELECT action_id,
                     CASE
                       WHEN pair_cost_wavg_after_seed IS NULL THEN 'null'
                       WHEN pair_cost_wavg_after_seed < 0.86 THEN '<0.86'
                       WHEN pair_cost_wavg_after_seed < 0.89 THEN '0.86-0.89'
                       WHEN pair_cost_wavg_after_seed < 0.92 THEN '0.89-0.92'
                       ELSE '>=0.92'
                     END AS bucket,
                     CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END AS audit_fill
              FROM candidate_registry
            ),
            action_counts AS (
              SELECT bucket, count(*) AS actions, round(avg(audit_fill), 6) AS audit_fill_rate
              FROM action_bucket GROUP BY 1
            ),
            residual_counts AS (
              SELECT b.bucket,
                     count(*) AS residual_lots,
                     round(sum(r.cost), 6) AS residual_cost
              FROM residual_lots r
              JOIN action_bucket b ON cast(b.action_id AS varchar) = r.source_seed_action_id
              GROUP BY 1
            )
            SELECT a.bucket,
                   a.actions,
                   coalesce(r.residual_lots, 0) AS residual_lots,
                   coalesce(r.residual_cost, 0.0) AS residual_cost,
                   round(coalesce(r.residual_cost, 0.0) * 1000.0 / nullif(a.actions, 0), 6) AS residual_cost_per_1k_actions,
                   a.audit_fill_rate
            FROM action_counts a
            LEFT JOIN residual_counts r USING (bucket)
            ORDER BY residual_cost_per_1k_actions DESC
            """,
        )
        seed_px_frontier = query(
            con,
            """
            WITH action_bucket AS (
              SELECT action_id,
                     CASE
                       WHEN seed_px < 0.20 THEN '0.00-0.20'
                       WHEN seed_px < 0.35 THEN '0.20-0.35'
                       WHEN seed_px < 0.50 THEN '0.35-0.50'
                       WHEN seed_px < 0.65 THEN '0.50-0.65'
                       ELSE '0.65-0.90'
                     END AS bucket,
                     CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END AS audit_fill
              FROM candidate_registry
            ),
            action_counts AS (
              SELECT bucket, count(*) AS actions, round(avg(audit_fill), 6) AS audit_fill_rate
              FROM action_bucket GROUP BY 1
            ),
            residual_counts AS (
              SELECT b.bucket,
                     count(*) AS residual_lots,
                     round(sum(r.cost), 6) AS residual_cost
              FROM residual_lots r
              JOIN action_bucket b ON cast(b.action_id AS varchar) = r.source_seed_action_id
              GROUP BY 1
            )
            SELECT a.bucket,
                   a.actions,
                   coalesce(r.residual_lots, 0) AS residual_lots,
                   coalesce(r.residual_cost, 0.0) AS residual_cost,
                   round(coalesce(r.residual_cost, 0.0) * 1000.0 / nullif(a.actions, 0), 6) AS residual_cost_per_1k_actions,
                   a.audit_fill_rate
            FROM action_counts a
            LEFT JOIN residual_counts r USING (bucket)
            ORDER BY residual_cost_per_1k_actions DESC
            """,
        )
        offset_frontier = query(
            con,
            """
            WITH action_bucket AS (
              SELECT action_id,
                     CASE
                       WHEN offset_s < 30 THEN '000-030'
                       WHEN offset_s < 60 THEN '030-060'
                       WHEN offset_s < 120 THEN '060-120'
                       ELSE '120+'
                     END AS bucket,
                     CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END AS audit_fill
              FROM candidate_registry
            ),
            action_counts AS (
              SELECT bucket, count(*) AS actions, round(avg(audit_fill), 6) AS audit_fill_rate
              FROM action_bucket GROUP BY 1
            ),
            residual_counts AS (
              SELECT b.bucket,
                     count(*) AS residual_lots,
                     round(sum(r.cost), 6) AS residual_cost
              FROM residual_lots r
              JOIN action_bucket b ON cast(b.action_id AS varchar) = r.source_seed_action_id
              GROUP BY 1
            )
            SELECT a.bucket,
                   a.actions,
                   coalesce(r.residual_lots, 0) AS residual_lots,
                   coalesce(r.residual_cost, 0.0) AS residual_cost,
                   round(coalesce(r.residual_cost, 0.0) * 1000.0 / nullif(a.actions, 0), 6) AS residual_cost_per_1k_actions,
                   a.audit_fill_rate
            FROM action_counts a
            LEFT JOIN residual_counts r USING (bucket)
            ORDER BY residual_cost_per_1k_actions DESC
            """,
        )
        alignment_side_frontier = query(
            con,
            """
            WITH action_bucket AS (
              SELECT action_id,
                     side_alignment,
                     side,
                     CASE WHEN public_audit_nearby_fill_count > 0 THEN 1.0 ELSE 0.0 END AS audit_fill
              FROM candidate_registry
            ),
            action_counts AS (
              SELECT side_alignment, side, count(*) AS actions, round(avg(audit_fill), 6) AS audit_fill_rate
              FROM action_bucket GROUP BY 1, 2
            ),
            residual_counts AS (
              SELECT b.side_alignment,
                     b.side,
                     count(*) AS residual_lots,
                     round(sum(r.cost), 6) AS residual_cost
              FROM residual_lots r
              JOIN action_bucket b ON cast(b.action_id AS varchar) = r.source_seed_action_id
              GROUP BY 1, 2
            )
            SELECT a.side_alignment,
                   a.side,
                   a.actions,
                   coalesce(r.residual_lots, 0) AS residual_lots,
                   coalesce(r.residual_cost, 0.0) AS residual_cost,
                   round(coalesce(r.residual_cost, 0.0) * 1000.0 / nullif(a.actions, 0), 6) AS residual_cost_per_1k_actions,
                   a.audit_fill_rate
            FROM action_counts a
            LEFT JOIN residual_counts r USING (side_alignment, side)
            ORDER BY residual_cost_per_1k_actions DESC
            """,
        )
    finally:
        con.close()

    candidate_base = str(Path(result_manifest["candidate_base_manifest"]).parent)
    base_cmd = (
        "uv run --with duckdb python scripts/run_completion_candidate_state_machine.py "
        f"--candidate-base {candidate_base} "
        "--mode passive_redeem --edge 0.055 --target-qty 5 --alignment all "
        "--seed-px-lo 0.05 --fill-haircut 0.25 --seed-l1-pair-cap 1.02 "
        "--cooldown-s 5 --imbalance-qty-cap 1.25 --residual-cooldown-age-s 30 "
        "--residual-cooldown-cost-cap 0.5 --fee-model official_taker --official-fee-rate 0.07 "
        "--force"
    )
    report = {
        "generated_at_utc": utc_now(),
        "result_dir": str(result_dir),
        "decision": "KEEP_BTC_ONLY_COMPLETION_RISK_FRONTIER_READY_RESEARCH_ONLY",
        "research_only": True,
        "private_truth_ready": False,
        "deployable": False,
        "promotion_gate_pass": False,
        "raw_scanned": False,
        "replay_scanned": False,
        "collector_scanned": False,
        "residual_join_coverage": residual_join,
        "pair_cost_frontier": pair_cost_frontier,
        "seed_px_frontier": seed_px_frontier,
        "offset_frontier": offset_frontier,
        "alignment_side_frontier": alignment_side_frontier,
        "next_bounded_reruns": [
            {
                "label": "seed_px_hi_065",
                "command": base_cmd + " --seed-px-hi 0.65 --output-dir /Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/experiment_seedpxhi065_officialfee_e055_t5_imb125_rc30_050_btc_only",
            },
            {
                "label": "seed_px_hi_050",
                "command": base_cmd + " --seed-px-hi 0.50 --output-dir /Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/experiment_seedpxhi050_officialfee_e055_t5_imb125_rc30_050_btc_only",
            },
            {
                "label": "offset_30_120_only",
                "command": base_cmd + " --offset-min-s 30 --seed-px-hi 0.90 --output-dir /Users/hot/web3Scientist/poly_backtest_data/derived/completion_candidate_pipeline_v1/experiment_offset030_120_officialfee_e055_t5_imb125_rc30_050_btc_only",
            },
        ],
    }
    report["outputs"] = {
        "summary_json": str((output_dir / "BTC_ONLY_COMPLETION_RISK_FRONTIER.json").resolve()),
        "report_md": str((output_dir / "BTC_ONLY_COMPLETION_RISK_FRONTIER.md").resolve()),
    }
    (output_dir / "BTC_ONLY_COMPLETION_RISK_FRONTIER.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "BTC_ONLY_COMPLETION_RISK_FRONTIER.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "outputs": report["outputs"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

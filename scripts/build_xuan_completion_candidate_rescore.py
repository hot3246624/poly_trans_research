#!/usr/bin/env python3
"""Rescore V1 markets with xuan completion/residual/merge semantics."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_ADAPTER = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"
DEFAULT_MERGE = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_merge_turnover_latest/MULTIASSET_MERGE_TURNOVER_REPORT.json"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def clean_value(value: Any) -> Any:
    if isinstance(value, float):
        return rounded(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--merge-turnover-report", type=Path, default=DEFAULT_MERGE)
    parser.add_argument("--top-n", type=int, default=250)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_completion_candidate_rescore_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    adapter_dir = args.adapter_dir.expanduser()
    merge_report = read_json(args.merge_turnover_report.expanduser())
    adapter_manifest = read_json(adapter_dir / "RESULT_SUMMARY_MANIFEST.json")
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_db = output_dir / "xuan_completion_candidate_rescore.duckdb"

    con = duckdb.connect(str(out_db))
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE actions AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              slug,
              min(ts_ms) OVER (PARTITION BY condition_id) AS first_action_ts_ms,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed,
              CAST(coalesce(official_taker_fee, fee, 0) AS DOUBLE) AS fee
            FROM read_csv_auto({quote(adapter_dir / 'actions.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE residuals AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              count(*) AS residual_lot_count,
              sum(CAST(qty AS DOUBLE)) AS residual_qty,
              sum(CAST(cost AS DOUBLE)) AS residual_cost,
              sum(CAST(payout AS DOUBLE)) AS residual_settlement_payout,
              sum(CAST(pnl AS DOUBLE)) AS residual_settlement_pnl,
              avg(CAST(age_s AS DOUBLE)) AS avg_residual_age_s
            FROM read_csv_auto({quote(adapter_dir / 'residual_lots.csv')}, HEADER=TRUE)
            GROUP BY 1, 2, 3
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE xuan_completion_rescore AS
            WITH market_actions AS (
              SELECT
                asset,
                day,
                condition_id,
                any_value(slug) AS slug,
                min(first_action_ts_ms) AS first_action_ts_ms,
                count(*) AS selected_seed_actions,
                sum(seed_cost) AS gross_buy_cost,
                sum(pair_qty_after_seed) AS paired_mergeable_qty,
                sum(pair_actions_after_seed) AS pair_actions,
                sum(fee) AS official_taker_fee
              FROM actions
              GROUP BY 1, 2, 3
            )
            SELECT
              a.asset,
              a.day,
              a.condition_id,
              a.slug,
              a.first_action_ts_ms,
              a.selected_seed_actions,
              a.gross_buy_cost,
              a.paired_mergeable_qty,
              a.gross_buy_cost - coalesce(r.residual_cost, 0.0) AS paired_mergeable_cost,
              a.paired_mergeable_qty AS merge_recovered_capital,
              a.pair_actions,
              a.official_taker_fee,
              coalesce(r.residual_lot_count, 0) AS residual_lot_count,
              coalesce(r.residual_qty, 0.0) AS market_end_residual_qty,
              coalesce(r.residual_cost, 0.0) AS market_end_residual_cost,
              coalesce(r.residual_settlement_payout, 0.0) AS actual_settlement_residual_payout,
              coalesce(r.residual_settlement_pnl, 0.0) AS actual_settlement_residual_pnl,
              coalesce(r.avg_residual_age_s, NULL) AS avg_residual_age_s,
              a.paired_mergeable_qty - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0)) AS pair_pnl,
              (
                a.paired_mergeable_qty
                - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0))
                + coalesce(r.residual_settlement_pnl, 0.0)
                - a.official_taker_fee
              ) AS xuan_after_fee_pnl,
              CASE WHEN a.gross_buy_cost > 0 THEN (
                a.paired_mergeable_qty
                - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0))
                + coalesce(r.residual_settlement_pnl, 0.0)
                - a.official_taker_fee
              ) / a.gross_buy_cost ELSE NULL END AS gross_cost_roi,
              CASE WHEN a.gross_buy_cost > 0 THEN a.paired_mergeable_qty / a.gross_buy_cost ELSE NULL END AS capital_turnover,
              CASE WHEN a.gross_buy_cost > 0 THEN coalesce(r.residual_cost, 0.0) / a.gross_buy_cost ELSE NULL END AS residual_cost_share,
              -coalesce(r.residual_cost, 0.0) AS residual_zero_stress_loss,
              CASE
                WHEN (
                  a.paired_mergeable_qty
                  - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0))
                  + coalesce(r.residual_settlement_pnl, 0.0)
                  - a.official_taker_fee
                ) > 0
                THEN TRUE ELSE FALSE
              END AS positive_xuan_completion_candidate
            FROM market_actions a
            LEFT JOIN residuals r USING (asset, day, condition_id)
            """
        )
        summary_rows = con.execute(
            """
            SELECT
              coalesce(asset, 'ALL') AS asset,
              count(*) AS market_candidate_count,
              count(*) FILTER (WHERE positive_xuan_completion_candidate) AS positive_xuan_candidate_count,
              sum(selected_seed_actions) AS selected_seed_actions,
              sum(gross_buy_cost) AS gross_buy_cost,
              sum(paired_mergeable_qty) AS paired_mergeable_qty,
              sum(merge_recovered_capital) AS merge_recovered_capital,
              sum(market_end_residual_qty) AS market_end_residual_qty,
              sum(market_end_residual_cost) AS market_end_residual_cost,
              sum(actual_settlement_residual_pnl) AS actual_settlement_residual_pnl,
              sum(official_taker_fee) AS official_taker_fee,
              sum(pair_pnl) AS pair_pnl,
              sum(xuan_after_fee_pnl) AS xuan_after_fee_pnl,
              avg(gross_cost_roi) AS avg_market_roi,
              min(xuan_after_fee_pnl) AS worst_market_after_fee_pnl,
              max(xuan_after_fee_pnl) AS best_market_after_fee_pnl
            FROM xuan_completion_rescore
            GROUP BY GROUPING SETS ((asset), ())
            ORDER BY asset
            """
        ).fetchall()
        summary_columns = [item[0] for item in con.description]
        summaries = [dict(zip(summary_columns, row)) for row in summary_rows]
        con.execute(
            f"""
            COPY (
              SELECT *
              FROM xuan_completion_rescore
              ORDER BY xuan_after_fee_pnl DESC, residual_cost_share ASC NULLS LAST
              LIMIT {int(args.top_n)}
            ) TO {quote(output_dir / 'xuan_completion_candidate_rescore_top.csv')} (HEADER, DELIMITER ',')
            """
        )
        con.execute(
            f"COPY xuan_completion_rescore TO {quote(output_dir / 'xuan_completion_candidate_rescore_all.parquet')} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    by_asset = {str(row["asset"]): row for row in summaries if row.get("asset") != "ALL"}
    summary = next((row for row in summaries if row.get("asset") == "ALL"), {})
    gross = float(summary.get("gross_buy_cost") or 0.0)
    summary["net_roi"] = (float(summary.get("xuan_after_fee_pnl") or 0.0) / gross) if gross else None
    for row in [summary, *by_asset.values()]:
        gross_row = float(row.get("gross_buy_cost") or 0.0)
        row["net_roi"] = (float(row.get("xuan_after_fee_pnl") or 0.0) / gross_row) if gross_row else None
    clean_summary = {key: clean_value(value) for key, value in summary.items()}
    clean_by_asset = {
        asset: {key: clean_value(value) for key, value in row.items()}
        for asset, row in by_asset.items()
    }
    manifest = {
        "schema_version": "xuan_completion_candidate_rescore_v1",
        "created_utc": utc_now(),
        "status": "OK_XUAN_COMPLETION_CANDIDATE_RESCORE_READY",
        "adapter_manifest": str(adapter_dir / "RESULT_SUMMARY_MANIFEST.json"),
        "adapter_status": adapter_manifest.get("status"),
        "merge_turnover_report": str(args.merge_turnover_report.expanduser()),
        "merge_turnover_status": merge_report.get("status") if merge_report else "MISSING",
        "summary": clean_summary,
        "by_asset": clean_by_asset,
        "outputs": {
            "duckdb": str(out_db),
            "table": "xuan_completion_rescore",
            "top_csv": str(output_dir / "xuan_completion_candidate_rescore_top.csv"),
            "all_parquet": str(output_dir / "xuan_completion_candidate_rescore_all.parquet"),
        },
        "semantics": {
            "score": "xuan_after_fee_pnl = pair_pnl + actual_settlement_residual_pnl - official_taker_fee at market level",
            "queue_pnl_used": False,
            "private_truth_ready": False,
            "deployable": False,
            "live_orders_allowed": False,
        },
    }
    (output_dir / "XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": manifest["status"], "summary": clean_summary}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

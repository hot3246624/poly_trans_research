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
    parser.add_argument("--handoff-top-n", type=int, default=100)
    parser.add_argument("--handoff-window-pad-ms", type=int, default=1000)
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
              candidate_id,
              CAST(action_id AS BIGINT) AS action_id,
              CAST(candidate_row_id AS VARCHAR) AS candidate_row_id,
              source_label,
              day,
              condition_id,
              slug,
              min(ts_ms) OVER (PARTITION BY condition_id) AS first_action_ts_ms,
              CAST(ts_ms AS BIGINT) AS ts_ms,
              ts_iso,
              CAST(offset_s AS DOUBLE) AS offset_s,
              CASE
                WHEN lower(CAST(side AS VARCHAR)) IN ('true', 'yes') THEN 'YES'
                WHEN lower(CAST(side AS VARCHAR)) IN ('false', 'no') THEN 'NO'
                ELSE CAST(side AS VARCHAR)
              END AS side,
              CASE
                WHEN lower(CAST(opposite_side AS VARCHAR)) IN ('true', 'yes') THEN 'YES'
                WHEN lower(CAST(opposite_side AS VARCHAR)) IN ('false', 'no') THEN 'NO'
                ELSE CAST(opposite_side AS VARCHAR)
              END AS opposite_side,
              CASE
                WHEN lower(CAST(winner_side AS VARCHAR)) IN ('true', 'yes') THEN 'YES'
                WHEN lower(CAST(winner_side AS VARCHAR)) IN ('false', 'no') THEN 'NO'
                ELSE CAST(winner_side AS VARCHAR)
              END AS winner_side,
              side_alignment,
              candidate_reason,
              CAST(public_trade_price AS DOUBLE) AS public_trade_price,
              CAST(public_trade_size AS DOUBLE) AS public_trade_size,
              CAST(l1_pair_ask AS DOUBLE) AS l1_pair_ask,
              CAST(edge AS DOUBLE) AS edge,
              CAST(seed_px AS DOUBLE) AS seed_px,
              CAST(seed_qty AS DOUBLE) AS seed_qty,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed,
              CAST(pair_cost_wavg_after_seed AS DOUBLE) AS pair_cost_wavg_after_seed,
              CAST(inventory_yes_qty_after AS DOUBLE) AS inventory_yes_qty_after,
              CAST(inventory_no_qty_after AS DOUBLE) AS inventory_no_qty_after,
              CAST(inventory_yes_cost_after AS DOUBLE) AS inventory_yes_cost_after,
              CAST(inventory_no_cost_after AS DOUBLE) AS inventory_no_cost_after,
              CAST(coalesce(official_taker_fee, fee, 0) AS DOUBLE) AS fee,
              blocked_by,
              deployable
            FROM read_csv_auto({quote(adapter_dir / 'actions.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE residual_lot_details AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              slug,
              CASE
                WHEN lower(CAST(winner_side AS VARCHAR)) IN ('true', 'yes') THEN 'YES'
                WHEN lower(CAST(winner_side AS VARCHAR)) IN ('false', 'no') THEN 'NO'
                ELSE CAST(winner_side AS VARCHAR)
              END AS winner_side,
              CASE
                WHEN lower(CAST(side AS VARCHAR)) IN ('true', 'yes') THEN 'YES'
                WHEN lower(CAST(side AS VARCHAR)) IN ('false', 'no') THEN 'NO'
                ELSE CAST(side AS VARCHAR)
              END AS side,
              CAST(qty AS DOUBLE) AS qty,
              CAST(px AS DOUBLE) AS px,
              CAST(cost AS DOUBLE) AS cost,
              CAST(payout AS DOUBLE) AS payout,
              CAST(pnl AS DOUBLE) AS pnl,
              CAST(source_seed_action_id AS VARCHAR) AS source_seed_action_id,
              CAST(candidate_row_id AS VARCHAR) AS candidate_row_id,
              CAST(age_s AS DOUBLE) AS age_s
            FROM read_csv_auto({quote(adapter_dir / 'residual_lots.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE residuals AS
            SELECT
              asset,
              day,
              condition_id,
              count(*) AS residual_lot_count,
              sum(qty) AS residual_qty,
              sum(cost) AS residual_cost,
              sum(payout) AS residual_settlement_payout,
              sum(pnl) AS residual_settlement_pnl,
              avg(age_s) AS avg_residual_age_s
            FROM residual_lot_details
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
        handoff_top_n = min(int(args.handoff_top_n), int(args.top_n))
        con.execute(
            f"""
            CREATE OR REPLACE TABLE xuan_completion_handoff_ranked_markets AS
            SELECT
              row_number() OVER (
                ORDER BY xuan_after_fee_pnl DESC, residual_cost_share ASC NULLS LAST
              ) AS handoff_rank,
              *
            FROM xuan_completion_rescore
            ORDER BY xuan_after_fee_pnl DESC, residual_cost_share ASC NULLS LAST
            LIMIT {handoff_top_n}
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE xuan_completion_same_window_handoff AS
            WITH action_stats AS (
              SELECT
                condition_id,
                count(*) AS same_window_action_rows,
                min(ts_ms) AS first_selected_action_ts_ms,
                max(ts_ms) AS last_selected_action_ts_ms,
                arg_min(ts_iso, ts_ms) AS first_selected_action_ts_iso,
                arg_max(ts_iso, ts_ms) AS last_selected_action_ts_iso,
                min(offset_s) AS first_offset_s,
                max(offset_s) AS last_offset_s,
                max(ts_ms) - min(ts_ms) AS same_window_duration_ms,
                count(*) FILTER (WHERE side = 'YES') AS yes_action_count,
                count(*) FILTER (WHERE side = 'NO') AS no_action_count,
                count(DISTINCT side) AS side_count,
                arg_min(side, ts_ms) AS first_action_side,
                arg_max(side, ts_ms) AS last_action_side,
                min(seed_px) AS min_seed_px,
                max(seed_px) AS max_seed_px,
                avg(seed_px) AS avg_seed_px,
                min(l1_pair_ask) AS min_l1_pair_ask,
                max(l1_pair_ask) AS max_l1_pair_ask,
                sum(seed_qty) AS gross_seed_qty,
                sum(seed_cost) AS action_seed_cost_sum,
                sum(fee) AS action_fee_sum,
                max(pair_qty_after_seed) AS final_pair_qty_after_seed,
                max(pair_actions_after_seed) AS final_pair_actions_after_seed,
                arg_max(inventory_yes_qty_after, ts_ms) AS final_inventory_yes_qty,
                arg_max(inventory_no_qty_after, ts_ms) AS final_inventory_no_qty
              FROM actions
              GROUP BY condition_id
            ),
            residual_stats AS (
              SELECT
                condition_id,
                count(*) AS handoff_residual_lot_rows,
                string_agg(DISTINCT side, '|') AS residual_sides,
                min(age_s) AS min_residual_age_s,
                max(age_s) AS max_residual_age_s,
                min(pnl) AS worst_residual_lot_pnl,
                max(pnl) AS best_residual_lot_pnl
              FROM residual_lot_details
              GROUP BY condition_id
            )
            SELECT
              h.handoff_rank,
              'RESEARCH_HANDOFF_PRIVATE_BLOCKED' AS handoff_status,
              h.asset,
              h.day,
              h.condition_id,
              h.slug,
              {int(args.handoff_window_pad_ms)} AS handoff_window_pad_ms,
              a.first_selected_action_ts_ms - {int(args.handoff_window_pad_ms)} AS handoff_window_start_ms,
              a.last_selected_action_ts_ms + {int(args.handoff_window_pad_ms)} AS handoff_window_end_ms,
              a.first_selected_action_ts_ms,
              a.last_selected_action_ts_ms,
              a.first_selected_action_ts_iso,
              a.last_selected_action_ts_iso,
              round(a.same_window_duration_ms / 1000.0, 6) AS same_window_duration_s,
              a.same_window_action_rows,
              a.first_offset_s,
              a.last_offset_s,
              a.yes_action_count,
              a.no_action_count,
              a.side_count,
              a.first_action_side,
              a.last_action_side,
              a.min_seed_px,
              a.max_seed_px,
              a.avg_seed_px,
              a.min_l1_pair_ask,
              a.max_l1_pair_ask,
              a.gross_seed_qty,
              a.action_seed_cost_sum,
              a.action_fee_sum,
              a.final_pair_qty_after_seed,
              a.final_pair_actions_after_seed,
              a.final_inventory_yes_qty,
              a.final_inventory_no_qty,
              coalesce(r.handoff_residual_lot_rows, 0) AS handoff_residual_lot_rows,
              r.residual_sides,
              r.min_residual_age_s,
              r.max_residual_age_s,
              r.worst_residual_lot_pnl,
              r.best_residual_lot_pnl,
              h.selected_seed_actions,
              h.gross_buy_cost,
              h.paired_mergeable_qty,
              h.paired_mergeable_cost,
              h.merge_recovered_capital,
              h.pair_actions,
              h.official_taker_fee,
              h.market_end_residual_qty,
              h.market_end_residual_cost,
              h.actual_settlement_residual_payout,
              h.actual_settlement_residual_pnl,
              h.pair_pnl,
              h.xuan_after_fee_pnl,
              h.gross_cost_roi,
              h.capital_turnover,
              h.residual_cost_share,
              h.residual_zero_stress_loss,
              h.positive_xuan_completion_candidate,
              FALSE AS private_truth_ready,
              FALSE AS deployable,
              FALSE AS live_orders_allowed,
              TRUE AS requires_owner_private_truth_for_promotion
            FROM xuan_completion_handoff_ranked_markets h
            LEFT JOIN action_stats a USING (condition_id)
            LEFT JOIN residual_stats r USING (condition_id)
            ORDER BY h.handoff_rank
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE xuan_completion_same_window_handoff_actions AS
            SELECT
              h.handoff_rank,
              row_number() OVER (
                PARTITION BY a.condition_id ORDER BY a.ts_ms, a.action_id
              ) AS same_window_action_seq,
              h.asset,
              h.day,
              h.condition_id,
              h.slug,
              a.action_id,
              a.candidate_id,
              a.candidate_row_id,
              a.source_label,
              a.ts_ms,
              a.ts_iso,
              a.offset_s,
              a.side,
              a.opposite_side,
              a.winner_side,
              a.side_alignment,
              a.candidate_reason,
              a.public_trade_price,
              a.public_trade_size,
              a.l1_pair_ask,
              a.edge,
              a.seed_px,
              a.seed_qty,
              a.seed_cost,
              a.fee,
              a.pair_qty_after_seed,
              a.pair_actions_after_seed,
              a.pair_cost_wavg_after_seed,
              a.inventory_yes_qty_after,
              a.inventory_no_qty_after,
              a.inventory_yes_cost_after,
              a.inventory_no_cost_after,
              a.blocked_by,
              a.deployable
            FROM xuan_completion_handoff_ranked_markets h
            JOIN actions a USING (condition_id)
            ORDER BY h.handoff_rank, same_window_action_seq
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE xuan_completion_same_window_handoff_residual_lots AS
            SELECT
              h.handoff_rank,
              row_number() OVER (
                PARTITION BY r.condition_id ORDER BY r.age_s DESC, r.candidate_row_id
              ) AS residual_lot_seq,
              h.asset,
              h.day,
              h.condition_id,
              h.slug,
              r.winner_side,
              r.side,
              r.qty,
              r.px,
              r.cost,
              r.payout,
              r.pnl,
              r.source_seed_action_id,
              r.candidate_row_id,
              r.age_s
            FROM xuan_completion_handoff_ranked_markets h
            JOIN residual_lot_details r USING (condition_id)
            ORDER BY h.handoff_rank, residual_lot_seq
            """
        )
        handoff_summary_row = con.execute(
            """
            SELECT
              count(*) AS handoff_market_count,
              sum(same_window_action_rows) AS handoff_action_rows,
              sum(handoff_residual_lot_rows) AS handoff_residual_lot_rows,
              count(*) FILTER (WHERE positive_xuan_completion_candidate) AS positive_handoff_market_count,
              min(xuan_after_fee_pnl) AS worst_handoff_after_fee_pnl,
              max(xuan_after_fee_pnl) AS best_handoff_after_fee_pnl,
              avg(same_window_duration_s) AS avg_same_window_duration_s,
              max(same_window_duration_s) AS max_same_window_duration_s
            FROM xuan_completion_same_window_handoff
            """
        ).fetchone()
        handoff_summary_columns = [item[0] for item in con.description]
        handoff_summary = dict(zip(handoff_summary_columns, handoff_summary_row))
        con.execute(
            f"""
            COPY xuan_completion_same_window_handoff
            TO {quote(output_dir / 'xuan_completion_candidate_same_window_handoff.csv')}
            (HEADER, DELIMITER ',')
            """
        )
        con.execute(
            f"""
            COPY xuan_completion_same_window_handoff_actions
            TO {quote(output_dir / 'xuan_completion_candidate_same_window_handoff_actions.csv')}
            (HEADER, DELIMITER ',')
            """
        )
        con.execute(
            f"""
            COPY xuan_completion_same_window_handoff_residual_lots
            TO {quote(output_dir / 'xuan_completion_candidate_same_window_handoff_residual_lots.csv')}
            (HEADER, DELIMITER ',')
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
    clean_handoff_summary = {key: clean_value(value) for key, value in handoff_summary.items()}
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
            "same_window_handoff_csv": str(output_dir / "xuan_completion_candidate_same_window_handoff.csv"),
            "same_window_handoff_actions_csv": str(
                output_dir / "xuan_completion_candidate_same_window_handoff_actions.csv"
            ),
            "same_window_handoff_residual_lots_csv": str(
                output_dir / "xuan_completion_candidate_same_window_handoff_residual_lots.csv"
            ),
        },
        "same_window_handoff": clean_handoff_summary,
        "semantics": {
            "score": "xuan_after_fee_pnl = pair_pnl + actual_settlement_residual_pnl - official_taker_fee at market level",
            "same_window_handoff": (
                "Top ranked market candidates with selected-action time windows, action-level replay rows, "
                "and residual-lot details for manual handoff. This is research evidence, not owner private truth."
            ),
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

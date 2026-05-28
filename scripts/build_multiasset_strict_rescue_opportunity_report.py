#!/usr/bin/env python3
"""Scan multiasset adapter residual lots for strict L2 rescue-close opportunities."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_ADAPTER = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"
DEFAULT_L2_TOP = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--l2-top-manifest", type=Path, default=DEFAULT_L2_TOP)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_strict_rescue_opportunity_latest",
    )
    parser.add_argument("--fee-rate", type=float, default=0.07)
    parser.add_argument("--lookahead-s", type=int, default=300)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    args = parser.parse_args()

    import duckdb  # type: ignore

    adapter_dir = args.adapter_dir.expanduser()
    adapter_manifest_path = adapter_dir / "RESULT_SUMMARY_MANIFEST.json"
    adapter_manifest = read_json(adapter_manifest_path)
    l2_manifest_path = args.l2_top_manifest.expanduser()
    l2_manifest = read_json(l2_manifest_path)
    l2_db = Path(str(l2_manifest.get("output_duckdb") or "")).expanduser()
    l2_table = str(l2_manifest.get("table") or "md_book_l2_top_aligned")
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "multiasset_strict_rescue_opportunity.duckdb"
    residual_csv = adapter_dir / "residual_lots.csv"
    actions_csv = adapter_dir / "actions.csv"
    lookahead_ms = int(args.lookahead_s * 1000)

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
        con.execute(f"ATTACH {quote(l2_db)} AS l2 (READ_ONLY)")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE residual_lots AS
            SELECT * FROM read_csv_auto({quote(residual_csv)}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE actions AS
            SELECT * FROM read_csv_auto({quote(actions_csv)}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE residual_enriched AS
            SELECT
              upper(split_part(r.slug, '-', 1)) AS asset,
              r.condition_id,
              r.day,
              r.slug,
              r.winner_side,
              r.side,
              CAST(r.qty AS DOUBLE) AS qty,
              CAST(r.px AS DOUBLE) AS px,
              CAST(r.cost AS DOUBLE) AS cost,
              CAST(r.payout AS DOUBLE) AS settlement_payout,
              CAST(r.pnl AS DOUBLE) AS settlement_pnl,
              CAST(r.source_seed_action_id AS BIGINT) AS source_seed_action_id,
              CAST(r.candidate_row_id AS BIGINT) AS candidate_row_id,
              CAST(r.age_s AS DOUBLE) AS age_s,
              CAST(a.ts_ms AS BIGINT) AS seed_ts_ms
            FROM residual_lots r
            LEFT JOIN actions a
              ON CAST(r.source_seed_action_id AS BIGINT) = CAST(a.action_id AS BIGINT)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE rescue_candidates AS
            SELECT
              r.asset,
              r.source_seed_action_id,
              r.candidate_row_id,
              r.condition_id,
              r.day,
              r.slug,
              r.side,
              r.qty,
              r.px,
              r.cost,
              r.seed_ts_ms,
              t.recv_ms,
              t.bid1_px,
              t.bid1_sz,
              t.bid1_px - ({float(args.fee_rate)} * t.bid1_px * (1.0 - t.bid1_px)) AS after_fee_bid_px,
              (t.bid1_px - ({float(args.fee_rate)} * t.bid1_px * (1.0 - t.bid1_px))) * r.qty
                AS after_fee_recovery_value,
              t.recv_ms - r.seed_ts_ms AS rescue_delay_ms
            FROM residual_enriched r
            JOIN l2.main.{l2_table} t
              ON t.day = r.day
             AND t.asset = r.asset
             AND t.condition_id = r.condition_id
             AND t.market_side = r.side
             AND t.recv_ms BETWEEN r.seed_ts_ms AND r.seed_ts_ms + {lookahead_ms}
             AND t.bid1_sz >= r.qty
            WHERE r.seed_ts_ms IS NOT NULL
              AND t.bid1_px IS NOT NULL
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE strict_rescue_by_lot AS
            WITH ranked AS (
              SELECT
                *,
                row_number() OVER (
                  PARTITION BY source_seed_action_id, candidate_row_id, condition_id, side
                  ORDER BY after_fee_bid_px DESC, rescue_delay_ms ASC
                ) AS rn
              FROM rescue_candidates
            )
            SELECT
              r.asset,
              r.condition_id,
              r.day,
              r.slug,
              r.winner_side,
              r.side,
              r.qty,
              r.px,
              r.cost,
              r.settlement_payout,
              r.settlement_pnl,
              r.source_seed_action_id,
              r.candidate_row_id,
              r.age_s,
              r.seed_ts_ms,
              b.recv_ms AS best_rescue_recv_ms,
              b.rescue_delay_ms AS best_rescue_delay_ms,
              b.bid1_px AS best_bid_px,
              b.bid1_sz AS best_bid_sz,
              b.after_fee_bid_px AS best_after_fee_bid_px,
              b.after_fee_recovery_value AS best_after_fee_recovery_value,
              b.after_fee_recovery_value - r.cost AS best_after_fee_rescue_pnl,
              b.after_fee_bid_px >= r.px AS break_even_after_fee_seen,
              b.after_fee_recovery_value > r.settlement_payout AS rescue_beats_settlement
            FROM residual_enriched r
            LEFT JOIN ranked b
              ON r.source_seed_action_id = b.source_seed_action_id
             AND r.candidate_row_id = b.candidate_row_id
             AND r.condition_id = b.condition_id
             AND r.side = b.side
             AND b.rn = 1
            """
        )
        summary_rows = con.execute(
            """
            SELECT
              coalesce(asset, 'ALL') AS asset,
              count(*) AS residual_lot_count,
              count(*) FILTER (WHERE best_rescue_recv_ms IS NOT NULL) AS lots_with_l2_rescue_quote,
              count(*) FILTER (WHERE break_even_after_fee_seen) AS break_even_after_fee_lots,
              count(*) FILTER (WHERE rescue_beats_settlement) AS rescue_beats_settlement_lots,
              sum(cost) AS residual_cost,
              sum(qty) AS residual_qty,
              sum(settlement_payout) AS settlement_payout,
              sum(settlement_pnl) AS settlement_pnl,
              sum(best_after_fee_recovery_value) AS best_after_fee_recovery_value,
              sum(best_after_fee_rescue_pnl) AS best_after_fee_rescue_pnl,
              avg(best_after_fee_bid_px) AS avg_best_after_fee_bid_px,
              max(best_after_fee_bid_px) AS max_best_after_fee_bid_px,
              quantile_cont(best_rescue_delay_ms, 0.5) AS p50_rescue_delay_ms,
              quantile_cont(best_rescue_delay_ms, 0.9) AS p90_rescue_delay_ms
            FROM strict_rescue_by_lot
            GROUP BY GROUPING SETS ((asset), ())
            ORDER BY asset
            """
        ).fetchall()
        names = [item[0] for item in con.description]
        summaries = [dict(zip(names, row)) for row in summary_rows]
        for row in summaries:
            residual_count = int(row.get("residual_lot_count") or 0)
            row["break_even_after_fee_lot_rate"] = (
                int(row.get("break_even_after_fee_lots") or 0) / residual_count if residual_count else None
            )
            row["rescue_beats_settlement_lot_rate"] = (
                int(row.get("rescue_beats_settlement_lots") or 0) / residual_count if residual_count else None
            )
        summary = next((row for row in summaries if row["asset"] == "ALL"), summaries[0] if summaries else {})
        by_asset = {row["asset"]: row for row in summaries if row.get("asset") != "ALL"}
        con.execute(
            f"COPY strict_rescue_by_lot TO {quote(output_dir / 'multiasset_strict_rescue_by_lot.csv')} "
            "(HEADER, DELIMITER ',')"
        )
        con.execute(
            f"COPY strict_rescue_by_lot TO {quote(output_dir / 'multiasset_strict_rescue_by_lot.parquet')} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    manifest = {
        "schema_version": "multiasset_strict_rescue_opportunity_report_v1",
        "created_utc": utc_now(),
        "status": "OK_MULTIASSET_STRICT_RESCUE_OPPORTUNITY_READY",
        "adapter_manifest": str(adapter_manifest_path),
        "adapter_status": adapter_manifest.get("status"),
        "l2_top_manifest": str(l2_manifest_path),
        "l2_top_status": l2_manifest.get("status"),
        "l2_top_contract": {
            "contract_name": "md_book_l2_top_aligned",
            "top_source": "md_book_l1 canonical top",
            "depth_source": "latest md_book_l2 side snapshot at or before L1 capture sequence",
            "raw_md_book_l2_is_top_of_book_contract": False,
            "status": "OK" if l2_manifest.get("status") == "OK" else "NOT_READY",
        },
        "lookahead_s": args.lookahead_s,
        "fee_rate": args.fee_rate,
        "outputs": {
            "duckdb": str(output_db),
            "table": "strict_rescue_by_lot",
            "csv": str(output_dir / "multiasset_strict_rescue_by_lot.csv"),
            "parquet": str(output_dir / "multiasset_strict_rescue_by_lot.parquet"),
        },
        "summary": summary,
        "by_asset": by_asset,
        "private_truth_ready": False,
        "private_promotion_ready_count": 0,
        "deployable": False,
        "live_orders_allowed": False,
        "semantics": {
            "strict_rescue": "same asset/condition_id/side, after seed timestamp, within lookahead window, top-aligned bid1 size covers residual qty, after-fee bid recovery computed with official fee formula",
            "research_only": True,
            "private_truth_ready": False,
            "deployable": False,
            "live_orders_allowed": False,
        },
    }
    manifest_path = output_dir / "MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "summary": summary, "outputs": manifest["outputs"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

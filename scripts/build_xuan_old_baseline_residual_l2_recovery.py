#!/usr/bin/env python3
"""Compute L2 recovery evidence for old BTC baseline residual lots."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASELINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DEFAULT_L2_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def fetch_one_dict(con: Any, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    row = cur.fetchone()
    if row is None:
        return {}
    names = [item[0] for item in cur.description]
    return dict(zip(names, row))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-baseline-dir", type=Path, default=DEFAULT_OLD_BASELINE)
    parser.add_argument("--l2-top-aligned-mart-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_old_baseline_residual_l2_recovery_latest",
    )
    parser.add_argument("--fee-rate", type=float, default=0.07)
    parser.add_argument("--lookahead-ms", type=int, default=300_000)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    args = parser.parse_args()

    import duckdb  # type: ignore

    old_dir = args.old_baseline_dir.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    l2_manifest = read_json(args.l2_top_aligned_mart_manifest.expanduser())
    l2_db = Path(l2_manifest["output_duckdb"]).expanduser()
    output_db = output_dir / "xuan_old_baseline_residual_l2_recovery.duckdb"
    residual_csv = old_dir / "residual_lots.csv"
    actions_csv = old_dir / "actions.csv"

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
        con.execute(f"ATTACH {quote(l2_db)} AS l2 (READ_ONLY)")
        con.execute(
            """
            CREATE OR REPLACE TABLE residual_lots AS
            SELECT
              row_number() OVER () AS residual_id,
              condition_id,
              day,
              slug,
              winner_side,
              side,
              CAST(qty AS DOUBLE) AS qty,
              CAST(px AS DOUBLE) AS px,
              CAST(cost AS DOUBLE) AS cost,
              CAST(payout AS DOUBLE) AS payout,
              CAST(pnl AS DOUBLE) AS settle_pnl,
              CAST(source_seed_action_id AS BIGINT) AS source_seed_action_id,
              CAST(candidate_row_id AS VARCHAR) AS candidate_row_id,
              CAST(age_s AS DOUBLE) AS age_s
            FROM read_csv_auto(?)
            """,
            [str(residual_csv)],
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE actions AS
            SELECT
              CAST(action_id AS BIGINT) AS action_id,
              CAST(ts_ms AS BIGINT) AS source_ts_ms,
              ts_iso AS source_ts_iso,
              condition_id,
              side,
              CAST(seed_px AS DOUBLE) AS seed_px,
              CAST(seed_qty AS DOUBLE) AS seed_qty
            FROM read_csv_auto(?)
            """,
            [str(actions_csv)],
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE residual_l2_recovery AS
            WITH base AS (
              SELECT
                r.*,
                a.source_ts_ms,
                a.source_ts_iso,
                r.cost / NULLIF(r.qty, 0) AS break_even_px
              FROM residual_lots r
              LEFT JOIN actions a
                ON r.source_seed_action_id = a.action_id
            ),
            joined AS (
              SELECT
                b.residual_id,
                max(m.bid1_px) AS best_bid_px,
                min(m.recv_ms) FILTER (WHERE m.bid1_px >= b.cost / NULLIF(b.qty, 0)) AS first_break_even_recv_ms,
                max(m.raw_l2_age_ms) AS max_raw_l2_age_ms,
                quantile_cont(m.raw_l2_age_ms, 0.95) AS p95_raw_l2_age_ms,
                count(*) AS l2_rows_seen
              FROM base b
              LEFT JOIN l2.main.md_book_l2_top_aligned m
                ON b.condition_id = m.condition_id
               AND b.side = m.market_side
               AND m.recv_ms >= b.source_ts_ms
               AND m.recv_ms <= b.source_ts_ms + {int(args.lookahead_ms)}
              GROUP BY b.residual_id
            )
            SELECT
              b.*,
              j.best_bid_px,
              j.first_break_even_recv_ms,
              CASE
                WHEN j.first_break_even_recv_ms IS NULL THEN NULL
                ELSE j.first_break_even_recv_ms - b.source_ts_ms
              END AS first_break_even_delay_ms,
              j.l2_rows_seen,
              j.max_raw_l2_age_ms,
              j.p95_raw_l2_age_ms,
              b.qty * coalesce(j.best_bid_px, 0) AS best_gross_recovery_value,
              b.qty * coalesce(j.best_bid_px, 0) * {float(args.fee_rate)} * (1 - coalesce(j.best_bid_px, 0)) AS best_recovery_fee,
              b.qty * coalesce(j.best_bid_px, 0)
                - b.qty * coalesce(j.best_bid_px, 0) * {float(args.fee_rate)} * (1 - coalesce(j.best_bid_px, 0))
                AS best_after_fee_recovery_value,
              CASE
                WHEN b.cost = 0 THEN NULL
                ELSE (
                  b.qty * coalesce(j.best_bid_px, 0)
                  - b.qty * coalesce(j.best_bid_px, 0) * {float(args.fee_rate)} * (1 - coalesce(j.best_bid_px, 0))
                ) / b.cost
              END AS best_after_fee_recovery_ratio,
              (
                b.qty * coalesce(j.best_bid_px, 0)
                - b.qty * coalesce(j.best_bid_px, 0) * {float(args.fee_rate)} * (1 - coalesce(j.best_bid_px, 0))
              ) - b.cost AS best_after_fee_mark_pnl,
              j.best_bid_px >= b.cost / NULLIF(b.qty, 0) AS gross_break_even_seen,
              CASE
                WHEN (
                  b.qty * coalesce(j.best_bid_px, 0)
                  - b.qty * coalesce(j.best_bid_px, 0) * {float(args.fee_rate)} * (1 - coalesce(j.best_bid_px, 0))
                ) >= b.cost THEN TRUE
                ELSE FALSE
              END AS after_fee_break_even_seen
            FROM base b
            LEFT JOIN joined j USING (residual_id)
            ORDER BY residual_id
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE residual_l2_recovery_by_day AS
            SELECT
              day,
              count(*) AS residual_lot_count,
              round(sum(qty), 6) AS residual_qty,
              round(sum(cost), 6) AS residual_cost,
              round(sum(best_after_fee_recovery_value), 6) AS best_after_fee_recovery_value,
              round(sum(best_after_fee_mark_pnl), 6) AS best_after_fee_mark_pnl,
              round(sum(best_after_fee_recovery_value) / NULLIF(sum(cost), 0), 6) AS best_after_fee_recovery_ratio,
              round(avg(CASE WHEN after_fee_break_even_seen THEN 1.0 ELSE 0.0 END), 6) AS after_fee_break_even_seen_rate,
              round(avg(CASE WHEN gross_break_even_seen THEN 1.0 ELSE 0.0 END), 6) AS gross_break_even_seen_rate,
              quantile_cont(best_after_fee_recovery_ratio, 0.5) AS p50_lot_after_fee_recovery_ratio,
              quantile_cont(best_after_fee_recovery_ratio, 0.95) AS p95_lot_after_fee_recovery_ratio,
              max(max_raw_l2_age_ms) AS max_raw_l2_age_ms
            FROM residual_l2_recovery
            GROUP BY day
            ORDER BY day
            """
        )
        recovery_csv = output_dir / "xuan_old_baseline_residual_l2_recovery.csv"
        day_csv = output_dir / "xuan_old_baseline_residual_l2_recovery_by_day.csv"
        con.execute(f"COPY residual_l2_recovery TO {quote(recovery_csv)} (HEADER, DELIMITER ',')")
        con.execute(f"COPY residual_l2_recovery_by_day TO {quote(day_csv)} (HEADER, DELIMITER ',')")
        summary = fetch_one_dict(
            con,
            """
            SELECT
              count(*) AS residual_lot_count,
              round(sum(qty), 6) AS residual_qty,
              round(sum(cost), 6) AS residual_cost,
              round(sum(best_after_fee_recovery_value), 6) AS best_after_fee_recovery_value,
              round(sum(best_after_fee_mark_pnl), 6) AS best_after_fee_mark_pnl,
              round(sum(best_after_fee_recovery_value) / NULLIF(sum(cost), 0), 6) AS best_after_fee_recovery_ratio,
              round(avg(CASE WHEN after_fee_break_even_seen THEN 1.0 ELSE 0.0 END), 6) AS after_fee_break_even_seen_rate,
              round(avg(CASE WHEN gross_break_even_seen THEN 1.0 ELSE 0.0 END), 6) AS gross_break_even_seen_rate,
              quantile_cont(best_after_fee_recovery_ratio, 0.5) AS p50_lot_after_fee_recovery_ratio,
              quantile_cont(best_after_fee_recovery_ratio, 0.95) AS p95_lot_after_fee_recovery_ratio,
              max(max_raw_l2_age_ms) AS max_raw_l2_age_ms
            FROM residual_l2_recovery
            """,
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    status = "OK_OLD_BASELINE_RESIDUAL_L2_RECOVERY_READY" if summary.get("residual_lot_count") else "FAIL_NO_RESIDUAL_LOTS"
    manifest = {
        "schema_version": "xuan_old_baseline_residual_l2_recovery_v1",
        "created_utc": utc_now(),
        "status": status,
        "old_baseline_dir": str(old_dir),
        "l2_top_aligned_mart_manifest": str(args.l2_top_aligned_mart_manifest.expanduser()),
        "output_dir": str(output_dir),
        "output_duckdb": str(output_db),
        "outputs": {
            "recovery_csv": str(recovery_csv),
            "summary_by_day_csv": str(day_csv),
        },
        "summary": summary,
        "params": {
            "fee_rate": args.fee_rate,
            "lookahead_ms": args.lookahead_ms,
        },
        "semantics": {
            "recovery_model": "best same-side bid within lookahead window, after official taker-style fee formula",
            "not_private_truth": True,
        },
        "sha256": {
            "recovery_csv": sha256_file(recovery_csv),
            "summary_by_day_csv": sha256_file(day_csv),
            "output_duckdb": sha256_file(output_db),
        },
    }
    manifest_path = output_dir / "XUAN_OLD_BASELINE_RESIDUAL_L2_RECOVERY_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "summary": summary, "outputs": manifest["outputs"]}, indent=2, sort_keys=True))
    return 0 if status.startswith("OK") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Align the old BTC completion/residual baseline actions to the local L2 mart."""

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


def quote(path: Path | str) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def fetch_one_dict(con: Any, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    row = cur.fetchone()
    if row is None:
        return {}
    names = [item[0] for item in cur.description]
    return dict(zip(names, row))


def build_bridge(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb  # type: ignore

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "xuan_old_baseline_l2_bridge.duckdb"
    old_actions_csv = args.old_baseline_dir / "actions.csv"
    old_summary_csv = args.old_baseline_dir / "summary_by_day.csv"
    old_manifest_path = args.old_baseline_dir / "RESULT_SUMMARY_MANIFEST.json"
    l2_manifest = read_json(args.l2_top_aligned_mart_manifest)
    l2_db = Path(l2_manifest["output_duckdb"]).expanduser()
    if not old_actions_csv.exists():
        raise SystemExit(f"old baseline actions missing: {old_actions_csv}")
    if not l2_db.exists():
        raise SystemExit(f"L2 top-aligned DuckDB missing: {l2_db}")

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
        con.execute(f"ATTACH {quote(l2_db)} AS l2 (READ_ONLY)")
        con.execute(
            """
            CREATE OR REPLACE TABLE old_actions AS
            SELECT
              CAST(action_id AS BIGINT) AS action_id,
              candidate_id,
              CAST(candidate_row_id AS VARCHAR) AS candidate_row_id,
              day,
              condition_id,
              slug,
              CAST(ts_ms AS BIGINT) AS ts_ms,
              ts_iso,
              CAST(offset_s AS DOUBLE) AS offset_s,
              side,
              opposite_side,
              winner_side,
              side_alignment,
              candidate_reason,
              CAST(public_trade_price AS DOUBLE) AS public_trade_price,
              CAST(public_trade_size AS DOUBLE) AS public_trade_size,
              CAST(l1_pair_ask AS DOUBLE) AS l1_pair_ask,
              CAST(edge AS DOUBLE) AS edge,
              CAST(seed_px AS DOUBLE) AS seed_px,
              CAST(seed_qty AS DOUBLE) AS seed_qty,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(official_taker_fee AS DOUBLE) AS official_taker_fee,
              CAST(fee AS DOUBLE) AS fee,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed,
              CAST(pair_cost_wavg_after_seed AS DOUBLE) AS pair_cost_wavg_after_seed
            FROM read_csv_auto(?)
            ORDER BY condition_id, side, ts_ms
            """,
            [str(old_actions_csv)],
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE side_match AS
            SELECT
              a.*,
              m.l1_source_row_id AS side_l1_source_row_id,
              m.recv_ms AS side_recv_ms,
              m.capture_seq AS side_capture_seq,
              m.source_kind AS side_source_kind,
              m.ask1_px AS side_ask1_px,
              m.ask1_sz AS side_ask1_sz,
              m.raw_l2_ask1_px AS side_raw_l2_ask1_px,
              m.raw_l2_ask1_sz AS side_raw_l2_ask1_sz,
              m.raw_l2_ask2_px AS side_raw_l2_ask2_px,
              m.raw_l2_ask2_sz AS side_raw_l2_ask2_sz,
              m.raw_l2_ask3_px AS side_raw_l2_ask3_px,
              m.raw_l2_ask3_sz AS side_raw_l2_ask3_sz,
              m.raw_l2_ask4_px AS side_raw_l2_ask4_px,
              m.raw_l2_ask4_sz AS side_raw_l2_ask4_sz,
              m.raw_l2_ask5_px AS side_raw_l2_ask5_px,
              m.raw_l2_ask5_sz AS side_raw_l2_ask5_sz,
              m.raw_l2_source_row_id AS side_raw_l2_source_row_id,
              m.raw_l2_recv_ms AS side_raw_l2_recv_ms,
              m.raw_l2_capture_seq AS side_raw_l2_capture_seq,
              m.raw_l2_age_ms AS side_raw_l2_age_ms,
              m.top_overlay_required AS side_top_overlay_required
            FROM old_actions a ASOF LEFT JOIN l2.main.md_book_l2_top_aligned m
              ON a.condition_id = m.condition_id
             AND a.side = m.market_side
             AND a.ts_ms >= m.recv_ms
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE action_l2_bridge AS
            SELECT
              s.*,
              o.l1_source_row_id AS opp_l1_source_row_id,
              o.recv_ms AS opp_recv_ms,
              o.capture_seq AS opp_capture_seq,
              o.source_kind AS opp_source_kind,
              o.ask1_px AS opp_ask1_px,
              o.ask1_sz AS opp_ask1_sz,
              o.raw_l2_source_row_id AS opp_raw_l2_source_row_id,
              o.raw_l2_recv_ms AS opp_raw_l2_recv_ms,
              o.raw_l2_capture_seq AS opp_raw_l2_capture_seq,
              o.raw_l2_age_ms AS opp_raw_l2_age_ms,
              o.top_overlay_required AS opp_top_overlay_required,
              s.ts_ms - s.side_recv_ms AS side_event_age_ms,
              s.ts_ms - o.recv_ms AS opp_event_age_ms,
              s.side_ask1_px + o.ask1_px AS l2_pair_ask,
              abs((s.side_ask1_px + o.ask1_px) - s.l1_pair_ask) AS l2_pair_ask_abs_delta,
              (
                CASE WHEN s.side_raw_l2_ask1_px <= s.seed_px THEN coalesce(s.side_raw_l2_ask1_sz, 0) ELSE 0 END
                + CASE WHEN s.side_raw_l2_ask2_px <= s.seed_px THEN coalesce(s.side_raw_l2_ask2_sz, 0) ELSE 0 END
                + CASE WHEN s.side_raw_l2_ask3_px <= s.seed_px THEN coalesce(s.side_raw_l2_ask3_sz, 0) ELSE 0 END
                + CASE WHEN s.side_raw_l2_ask4_px <= s.seed_px THEN coalesce(s.side_raw_l2_ask4_sz, 0) ELSE 0 END
                + CASE WHEN s.side_raw_l2_ask5_px <= s.seed_px THEN coalesce(s.side_raw_l2_ask5_sz, 0) ELSE 0 END
              ) AS l2_top5_fillable_qty_at_seed_px,
              l2_top5_fillable_qty_at_seed_px >= s.seed_qty AS l2_top5_seed_qty_fillable,
              abs(s.side_ask1_px - s.seed_px) AS side_top_ask_seed_abs_delta
            FROM side_match s ASOF LEFT JOIN l2.main.md_book_l2_top_aligned o
              ON s.condition_id = o.condition_id
             AND s.opposite_side = o.market_side
             AND s.ts_ms >= o.recv_ms
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bridge_summary_by_day AS
            SELECT
              day,
              count(*) AS action_count,
              count(side_recv_ms) AS side_l2_match_count,
              count(opp_recv_ms) AS opp_l2_match_count,
              round(avg(CASE WHEN side_recv_ms IS NOT NULL AND opp_recv_ms IS NOT NULL THEN 1.0 ELSE 0.0 END), 6) AS both_side_match_rate,
              round(avg(CASE WHEN side_top_overlay_required THEN 1.0 ELSE 0.0 END), 6) AS side_top_overlay_required_rate,
              round(avg(CASE WHEN opp_top_overlay_required THEN 1.0 ELSE 0.0 END), 6) AS opp_top_overlay_required_rate,
              round(avg(side_event_age_ms), 6) AS avg_side_event_age_ms,
              quantile_cont(side_event_age_ms, 0.5) AS p50_side_event_age_ms,
              quantile_cont(side_event_age_ms, 0.95) AS p95_side_event_age_ms,
              max(side_event_age_ms) AS max_side_event_age_ms,
              round(avg(side_raw_l2_age_ms), 6) AS avg_side_raw_l2_age_ms,
              quantile_cont(side_raw_l2_age_ms, 0.95) AS p95_side_raw_l2_age_ms,
              max(side_raw_l2_age_ms) AS max_side_raw_l2_age_ms,
              round(avg(l2_pair_ask_abs_delta), 8) AS avg_l2_pair_ask_abs_delta,
              quantile_cont(l2_pair_ask_abs_delta, 0.95) AS p95_l2_pair_ask_abs_delta,
              max(l2_pair_ask_abs_delta) AS max_l2_pair_ask_abs_delta,
              round(avg(CASE WHEN l2_top5_seed_qty_fillable THEN 1.0 ELSE 0.0 END), 6) AS l2_top5_seed_qty_fillable_rate,
              quantile_cont(l2_top5_fillable_qty_at_seed_px, 0.5) AS p50_l2_top5_fillable_qty_at_seed_px
            FROM action_l2_bridge
            GROUP BY day
            ORDER BY day
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bridge_anchor_surrogates AS
            WITH day_scores AS (
              SELECT *
              FROM read_csv_auto(?)
            ),
            best_day AS (
              SELECT 'old_best_day_by_fee_after_pnl' AS anchor_label, day, fee_after_pnl, stress100_worst_pnl, cost_residual_rate
              FROM day_scores
              ORDER BY fee_after_pnl DESC
              LIMIT 1
            ),
            worst_tail_day AS (
              SELECT 'tail_mark_day_by_residual_cost_rate' AS anchor_label, day, fee_after_pnl, stress100_worst_pnl, cost_residual_rate
              FROM day_scores
              ORDER BY cost_residual_rate DESC
              LIMIT 1
            ),
            stress_day AS (
              SELECT 'stress_day_by_stress100_worst_pnl' AS anchor_label, day, fee_after_pnl, stress100_worst_pnl, cost_residual_rate
              FROM day_scores
              ORDER BY stress100_worst_pnl ASC
              LIMIT 1
            )
            SELECT * FROM best_day
            UNION ALL SELECT * FROM worst_tail_day
            UNION ALL SELECT * FROM stress_day
            """,
            [str(old_summary_csv)],
        )
        bridge_csv = output_dir / "xuan_old_baseline_l2_action_bridge.csv"
        day_csv = output_dir / "xuan_old_baseline_l2_bridge_by_day.csv"
        anchor_csv = output_dir / "xuan_old_baseline_l2_anchor_surrogates.csv"
        con.execute(f"COPY action_l2_bridge TO {quote(bridge_csv)} (HEADER, DELIMITER ',')")
        con.execute(f"COPY bridge_summary_by_day TO {quote(day_csv)} (HEADER, DELIMITER ',')")
        con.execute(f"COPY bridge_anchor_surrogates TO {quote(anchor_csv)} (HEADER, DELIMITER ',')")
        summary = fetch_one_dict(
            con,
            """
            SELECT
              count(*) AS action_count,
              count(side_recv_ms) AS side_l2_match_count,
              count(opp_recv_ms) AS opp_l2_match_count,
              round(avg(CASE WHEN side_recv_ms IS NOT NULL AND opp_recv_ms IS NOT NULL THEN 1.0 ELSE 0.0 END), 6) AS both_side_match_rate,
              round(avg(CASE WHEN side_top_overlay_required THEN 1.0 ELSE 0.0 END), 6) AS side_top_overlay_required_rate,
              round(avg(CASE WHEN opp_top_overlay_required THEN 1.0 ELSE 0.0 END), 6) AS opp_top_overlay_required_rate,
              round(avg(side_event_age_ms), 6) AS avg_side_event_age_ms,
              quantile_cont(side_event_age_ms, 0.5) AS p50_side_event_age_ms,
              quantile_cont(side_event_age_ms, 0.95) AS p95_side_event_age_ms,
              max(side_event_age_ms) AS max_side_event_age_ms,
              round(avg(side_raw_l2_age_ms), 6) AS avg_side_raw_l2_age_ms,
              quantile_cont(side_raw_l2_age_ms, 0.95) AS p95_side_raw_l2_age_ms,
              max(side_raw_l2_age_ms) AS max_side_raw_l2_age_ms,
              round(avg(l2_pair_ask_abs_delta), 8) AS avg_l2_pair_ask_abs_delta,
              quantile_cont(l2_pair_ask_abs_delta, 0.95) AS p95_l2_pair_ask_abs_delta,
              max(l2_pair_ask_abs_delta) AS max_l2_pair_ask_abs_delta,
              round(avg(CASE WHEN l2_top5_seed_qty_fillable THEN 1.0 ELSE 0.0 END), 6) AS l2_top5_seed_qty_fillable_rate
            FROM action_l2_bridge
            """,
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    status = (
        "OK_OLD_BASELINE_L2_BRIDGE_READY"
        if summary.get("action_count") == summary.get("side_l2_match_count") == summary.get("opp_l2_match_count")
        else "BLOCKED_OLD_BASELINE_L2_MATCH_GAPS"
    )
    manifest = {
        "schema_version": "xuan_old_baseline_l2_bridge_v1",
        "created_utc": utc_now(),
        "status": status,
        "old_baseline_manifest": str(old_manifest_path),
        "old_actions_csv": str(old_actions_csv),
        "l2_top_aligned_mart_manifest": str(args.l2_top_aligned_mart_manifest),
        "l2_top_aligned_duckdb": str(l2_db),
        "output_dir": str(output_dir),
        "output_duckdb": str(output_db),
        "outputs": {
            "action_bridge_csv": str(bridge_csv),
            "summary_by_day_csv": str(day_csv),
            "anchor_surrogates_csv": str(anchor_csv),
        },
        "summary": summary,
        "anchor_policy": {
            "status": "SURROGATE_ANCHORS_CONSTRUCTED_SOURCE_LABELS_NOT_FOUND",
            "requested_labels": [
                "20260522T1705 old_best",
                "20260525T2041 cap25_high_roi",
                "20260526T1757 capped_cap25",
                "20260527T0407 tail_mark_snapshot",
            ],
            "surrogate_outputs": str(anchor_csv),
        },
        "semantics": {
            "action_source": "old BTC completion/residual baseline selected actions",
            "top_source": "md_book_l1 canonical top from md_book_l2_top_aligned",
            "depth_source": "latest md_book_l2 side snapshot at or before the matched L1 top row",
            "not_private_truth": True,
        },
        "sha256": {
            "output_duckdb": sha256_file(output_db),
            "action_bridge_csv": sha256_file(bridge_csv),
            "summary_by_day_csv": sha256_file(day_csv),
            "anchor_surrogates_csv": sha256_file(anchor_csv),
        },
    }
    manifest_path = output_dir / "XUAN_OLD_BASELINE_L2_BRIDGE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "summary": summary, "outputs": manifest["outputs"]}, indent=2, sort_keys=True))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-baseline-dir", type=Path, default=DEFAULT_OLD_BASELINE)
    parser.add_argument("--l2-top-aligned-mart-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_old_baseline_l2_bridge_latest",
    )
    parser.add_argument("--duckdb-threads", type=int, default=2)
    args = parser.parse_args()
    args.old_baseline_dir = args.old_baseline_dir.expanduser()
    args.l2_top_aligned_mart_manifest = args.l2_top_aligned_mart_manifest.expanduser()
    args.output_dir = args.output_dir.expanduser()
    build_bridge(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

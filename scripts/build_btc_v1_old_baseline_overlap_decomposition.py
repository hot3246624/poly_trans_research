#!/usr/bin/env python3
"""Decompose BTC V1 normalized actions into old-overlap/new-only buckets.

The old and new BTC baselines use different source/taker-side semantics. This
report does not prove parity. It attributes V1 normalized selected actions by
whether their day/condition/side/time bucket exists in the old BTC baseline.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_STATE_MACHINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DEFAULT_NEW_STATE_MACHINE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
)
DEFAULT_BTC_SEMANTIC_ALIGNMENT = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json"
)
DEFAULT_BTC_SOURCE_SEMANTICS = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_source_semantics_delta_latest/BTC_SOURCE_SEMANTICS_DELTA_REPORT.json"
)

SOURCE_SEMANTICS_CONTRACT_ID = "btc_v1_normalized_buy_adapter_canonical_research_v1"
L2_TOP_OVERLAY_CONTRACT_ID = "md_book_l2_top_aligned_l1_canonical_top_raw_l2_depth_asof_v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rounded(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def clean(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if isinstance(value, float):
            out[key] = rounded(value)
        elif isinstance(value, (dt.date, dt.datetime)):
            out[key] = value.isoformat()
        else:
            out[key] = value
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-state-machine-dir", type=Path, default=DEFAULT_OLD_STATE_MACHINE)
    parser.add_argument("--new-state-machine-dir", type=Path, default=DEFAULT_NEW_STATE_MACHINE)
    parser.add_argument("--btc-semantic-alignment", type=Path, default=DEFAULT_BTC_SEMANTIC_ALIGNMENT)
    parser.add_argument("--btc-source-semantics", type=Path, default=DEFAULT_BTC_SOURCE_SEMANTICS)
    parser.add_argument("--bucket-ms", type=int, default=5000)
    parser.add_argument("--capacity-notional", type=float, default=1000.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    old_dir = args.old_state_machine_dir.expanduser()
    new_dir = args.new_state_machine_dir.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_db = output_dir / "btc_v1_old_baseline_overlap_decomposition.duckdb"
    semantic_alignment = read_json(args.btc_semantic_alignment.expanduser())
    source_semantics = read_json(args.btc_source_semantics.expanduser())

    con = duckdb.connect(str(out_db))
    try:
        con.execute(f"ATTACH {quote(old_dir / 'state_machine_results.duckdb')} AS old_sm (READ_ONLY)")
        con.execute(f"ATTACH {quote(new_dir / 'state_machine_results.duckdb')} AS new_sm (READ_ONLY)")
        con.execute(
            f"""
            CREATE OR REPLACE TABLE old_action_buckets AS
            SELECT DISTINCT
              CAST(day AS VARCHAR) AS day,
              condition_id,
              side,
              floor(ts_ms / {int(args.bucket_ms)})::BIGINT AS bucket_id
            FROM old_sm.main.actions
            WHERE side IN ('YES', 'NO')
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE new_action_metrics AS
            WITH ordered AS (
              SELECT
                *,
                lag(pair_qty_after_seed, 1, 0.0) OVER (PARTITION BY condition_id ORDER BY ts_ms, action_id)
                  AS prev_pair_qty_after_seed,
                lag(CAST(inventory_yes_cost_after AS DOUBLE), 1, 0.0) OVER (
                  PARTITION BY condition_id ORDER BY ts_ms, action_id
                ) AS prev_yes_cost_after,
                lag(CAST(inventory_no_cost_after AS DOUBLE), 1, 0.0) OVER (
                  PARTITION BY condition_id ORDER BY ts_ms, action_id
                ) AS prev_no_cost_after
              FROM new_sm.main.actions
            )
            SELECT
              candidate_id,
              action_id,
              CAST(day AS VARCHAR) AS day,
              condition_id,
              slug,
              ts_ms,
              side,
              floor(ts_ms / {int(args.bucket_ms)})::BIGINT AS bucket_id,
              seed_qty,
              seed_cost,
              coalesce(official_taker_fee, fee, 0.0) AS fee_drag,
              greatest(0.0, pair_qty_after_seed - prev_pair_qty_after_seed) AS pair_qty_delta,
              greatest(
                0.0,
                prev_yes_cost_after
                + prev_no_cost_after
                + seed_cost
                - CAST(inventory_yes_cost_after AS DOUBLE)
                - CAST(inventory_no_cost_after AS DOUBLE)
              ) AS paired_cost_delta,
              greatest(0.0, pair_qty_after_seed - prev_pair_qty_after_seed)
                - greatest(
                    0.0,
                    prev_yes_cost_after
                    + prev_no_cost_after
                    + seed_cost
                    - CAST(inventory_yes_cost_after AS DOUBLE)
                    - CAST(inventory_no_cost_after AS DOUBLE)
                  ) AS pair_pnl_delta,
              CASE
                WHEN EXISTS (
                  SELECT 1
                  FROM old_action_buckets o
                  WHERE o.day = CAST(ordered.day AS VARCHAR)
                    AND o.condition_id = ordered.condition_id
                    AND o.side = ordered.side
                    AND o.bucket_id = floor(ordered.ts_ms / {int(args.bucket_ms)})::BIGINT
                )
                THEN 'old_baseline_overlap'
                ELSE 'v1_normalized_new_only'
              END AS decomposition_bucket
            FROM ordered
            WHERE side IN ('YES', 'NO')
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucketed_actions AS
            SELECT 'v1_normalized_all' AS decomposition_bucket, * EXCLUDE(decomposition_bucket)
            FROM new_action_metrics
            UNION ALL
            SELECT decomposition_bucket, * EXCLUDE(decomposition_bucket)
            FROM new_action_metrics
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucketed_residuals AS
            SELECT
              b.decomposition_bucket,
              r.condition_id,
              CAST(r.day AS VARCHAR) AS day,
              r.slug,
              r.side,
              r.qty,
              r.cost,
              r.payout,
              r.pnl,
              r.source_seed_action_id,
              b.action_id
            FROM new_sm.main.residual_lots r
            JOIN bucketed_actions b
              ON b.condition_id = r.condition_id
             AND CAST(b.action_id AS VARCHAR) = CAST(r.source_seed_action_id AS VARCHAR)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucket_day_summary AS
            WITH action_day AS (
              SELECT
                decomposition_bucket,
                day,
                count(DISTINCT candidate_id) AS candidate_count,
                count(*) AS selected_action_count,
                count(DISTINCT condition_id) AS market_count,
                sum(seed_qty) AS gross_buy_qty,
                sum(seed_cost) AS gross_buy_cost,
                sum(pair_qty_delta) AS pair_qty,
                sum(paired_cost_delta) AS paired_cost,
                sum(pair_pnl_delta) AS pair_pnl,
                sum(fee_drag) AS fee_drag
              FROM bucketed_actions
              GROUP BY 1, 2
            ),
            residual_day AS (
              SELECT
                decomposition_bucket,
                day,
                sum(qty) AS residual_qty,
                sum(cost) AS residual_cost,
                sum(pnl) AS residual_settle_pnl,
                -sum(cost) AS residual_zero_stress_pnl
              FROM bucketed_residuals
              GROUP BY 1, 2
            )
            SELECT
              a.*,
              coalesce(r.residual_qty, 0.0) AS residual_qty,
              coalesce(r.residual_cost, 0.0) AS residual_cost,
              coalesce(r.residual_settle_pnl, 0.0) AS residual_settle_pnl,
              coalesce(r.residual_zero_stress_pnl, 0.0) AS residual_zero_stress_pnl,
              a.pair_pnl - a.fee_drag AS pair_pnl_after_fee,
              a.pair_pnl + coalesce(r.residual_settle_pnl, 0.0) - a.fee_drag AS fee_after_pnl,
              (a.pair_pnl + coalesce(r.residual_settle_pnl, 0.0) - a.fee_drag)
                / NULLIF(a.gross_buy_cost, 0.0) AS net_roi,
              coalesce(r.residual_qty, 0.0) / NULLIF(a.gross_buy_qty, 0.0) AS qty_residual_rate
            FROM action_day a
            LEFT JOIN residual_day r USING (decomposition_bucket, day)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucket_pair_cost_distribution AS
            SELECT
              decomposition_bucket,
              count(*) FILTER (WHERE paired_cost_delta > 0) AS paired_action_count,
              min(paired_cost_delta) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_min,
              quantile_cont(paired_cost_delta, 0.25) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_p25,
              quantile_cont(paired_cost_delta, 0.50) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_p50,
              quantile_cont(paired_cost_delta, 0.90) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_p90,
              quantile_cont(paired_cost_delta, 0.95) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_p95,
              max(paired_cost_delta) FILTER (WHERE paired_cost_delta > 0) AS pair_cost_max
            FROM bucketed_actions
            GROUP BY 1
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE bucket_capital_events AS
            SELECT
              decomposition_bucket,
              day,
              ts_ms,
              seed_cost - least(seed_cost, paired_cost_delta) AS capital_tied_delta
            FROM bucketed_actions
            UNION ALL
            SELECT
              decomposition_bucket,
              day,
              CAST(regexp_extract(slug, '([0-9]+)$', 1) AS BIGINT) * 1000 + 300000 AS ts_ms,
              -sum(cost) AS capital_tied_delta
            FROM bucketed_residuals
            GROUP BY 1, 2, 3
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucket_capital_curve AS
            SELECT
              decomposition_bucket,
              day,
              ts_ms,
              sum(capital_tied_delta) OVER (
                PARTITION BY decomposition_bucket ORDER BY ts_ms, day
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
              ) AS capital_tied
            FROM bucket_capital_events
            ORDER BY decomposition_bucket, ts_ms, day
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE bucket_capital_summary AS
            SELECT
              decomposition_bucket,
              max(capital_tied) AS max_capital_tied,
              avg(capital_tied) AS avg_capital_tied,
              quantile_cont(capital_tied, 0.95) AS p95_capital_tied
            FROM bucket_capital_curve
            GROUP BY 1
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TABLE bucket_summary AS
            SELECT
              d.decomposition_bucket,
              count(DISTINCT d.day) AS valid_day_count,
              sum(d.candidate_count) AS candidate_count,
              sum(d.selected_action_count) AS selected_action_count,
              sum(d.market_count) AS market_day_count,
              sum(d.gross_buy_qty) AS gross_buy_qty,
              sum(d.gross_buy_cost) AS gross_buy_cost,
              sum(d.pair_qty) AS pair_qty,
              sum(d.paired_cost) AS paired_cost,
              sum(d.pair_pnl) AS pair_pnl,
              sum(d.fee_drag) AS fee_drag,
              sum(d.pair_pnl_after_fee) AS pair_pnl_after_fee,
              sum(d.residual_settle_pnl) AS residual_settle_pnl,
              sum(d.residual_zero_stress_pnl) AS residual_zero_stress_pnl,
              sum(d.fee_after_pnl) AS fee_after_pnl,
              sum(d.fee_after_pnl) / NULLIF(sum(d.gross_buy_cost), 0.0) AS net_roi,
              sum(d.residual_qty) / NULLIF(sum(d.gross_buy_qty), 0.0) AS qty_residual_rate,
              min(d.fee_after_pnl) AS worst_day_fee_after_pnl,
              arg_min(d.day, d.fee_after_pnl) AS worst_day,
              c.max_capital_tied,
              c.avg_capital_tied,
              c.p95_capital_tied,
              (sum(d.fee_after_pnl) / NULLIF(c.max_capital_tied, 0.0)) * {float(args.capacity_notional)}
                / NULLIF(count(DISTINCT d.day), 0) AS daily_capacity_estimate_at_1000
            FROM bucket_day_summary d
            LEFT JOIN bucket_capital_summary c USING (decomposition_bucket)
            GROUP BY d.decomposition_bucket, c.max_capital_tied, c.avg_capital_tied, c.p95_capital_tied
            ORDER BY
              CASE d.decomposition_bucket
                WHEN 'old_baseline_overlap' THEN 1
                WHEN 'v1_normalized_new_only' THEN 2
                WHEN 'v1_normalized_all' THEN 3
                ELSE 4
              END
            """
        )
        mismatch = con.execute(
            f"""
            WITH old_b AS (
              SELECT DISTINCT
                CAST(day AS VARCHAR) AS day,
                condition_id,
                side,
                floor(ts_ms / {int(args.bucket_ms)})::BIGINT AS bucket_id
              FROM old_sm.main.actions
            ),
            new_b AS (
              SELECT DISTINCT
                CAST(day AS VARCHAR) AS day,
                condition_id,
                side,
                floor(ts_ms / {int(args.bucket_ms)})::BIGINT AS bucket_id
              FROM new_sm.main.actions
            ),
            old_market AS (SELECT DISTINCT condition_id FROM old_sm.main.actions),
            new_market AS (SELECT DISTINCT condition_id FROM new_sm.main.actions),
            old_market_side AS (SELECT DISTINCT condition_id, side FROM old_sm.main.actions),
            new_market_side AS (SELECT DISTINCT condition_id, side FROM new_sm.main.actions)
            SELECT
              (SELECT count(*) FROM old_b) AS old_action_bucket_count,
              (SELECT count(*) FROM new_b) AS new_action_bucket_count,
              (SELECT count(*) FROM new_b JOIN old_b USING(day, condition_id, side, bucket_id)) AS overlap_action_bucket_count,
              (SELECT count(*) FROM new_b ANTI JOIN old_b USING(day, condition_id, side, bucket_id)) AS new_only_action_bucket_count,
              (SELECT count(*) FROM old_b ANTI JOIN new_b USING(day, condition_id, side, bucket_id)) AS old_only_action_bucket_count,
              (SELECT count(*) FROM new_market ANTI JOIN old_market USING(condition_id)) AS condition_new_only_count,
              (SELECT count(*) FROM old_market ANTI JOIN new_market USING(condition_id)) AS condition_old_only_count,
              (SELECT count(*) FROM new_market_side ANTI JOIN old_market_side USING(condition_id, side)) AS side_mismatch_new_only_count,
              (SELECT count(*) FROM old_market_side ANTI JOIN new_market_side USING(condition_id, side)) AS side_mismatch_old_only_count
            """
        ).fetchone()
        mismatch_cols = [item[0] for item in con.description]
        mismatch_attribution = dict(zip(mismatch_cols, mismatch))

        bucket_summary_rows = [clean(dict(zip([d[0] for d in con.description], row))) for row in con.execute(
            "SELECT * FROM bucket_summary"
        ).fetchall()]
        # Refresh column metadata after the fetch above.
        bucket_summary_cols = [d[0] for d in con.description]
        bucket_summary_rows = [clean(dict(zip(bucket_summary_cols, row))) for row in con.execute(
            "SELECT * FROM bucket_summary"
        ).fetchall()]
        bucket_day_cols = [d[0] for d in con.execute("SELECT * FROM bucket_day_summary LIMIT 0").description]
        bucket_day_rows = [clean(dict(zip(bucket_day_cols, row))) for row in con.execute(
            "SELECT * FROM bucket_day_summary ORDER BY decomposition_bucket, day"
        ).fetchall()]
        pair_cost_cols = [d[0] for d in con.execute("SELECT * FROM bucket_pair_cost_distribution LIMIT 0").description]
        pair_cost_rows = [clean(dict(zip(pair_cost_cols, row))) for row in con.execute(
            "SELECT * FROM bucket_pair_cost_distribution ORDER BY decomposition_bucket"
        ).fetchall()]
        con.execute(f"COPY bucket_summary TO {quote(output_dir / 'bucket_summary.csv')} (HEADER, DELIMITER ',')")
        con.execute(f"COPY bucket_day_summary TO {quote(output_dir / 'bucket_day_summary.csv')} (HEADER, DELIMITER ',')")
        con.execute(
            f"COPY bucket_pair_cost_distribution TO {quote(output_dir / 'pair_cost_distribution.csv')} "
            "(HEADER, DELIMITER ',')"
        )
        con.execute(f"COPY bucket_capital_curve TO {quote(output_dir / 'capital_curve.csv')} (HEADER, DELIMITER ',')")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    # Explicit CSV write keeps JSON-like rounded rows easy to inspect when DuckDB
    # emits platform-specific float formatting.
    write_csv(output_dir / "bucket_summary_rounded.csv", bucket_summary_rows, bucket_summary_cols)
    write_csv(output_dir / "bucket_day_summary_rounded.csv", bucket_day_rows, bucket_day_cols)
    write_csv(output_dir / "pair_cost_distribution_rounded.csv", pair_cost_rows, pair_cost_cols)

    source_semantics_contract = {
        "source_semantics_contract_id": SOURCE_SEMANTICS_CONTRACT_ID,
        "l2_top_overlay_contract_id": L2_TOP_OVERLAY_CONTRACT_ID,
        "source_semantics_policy": (
            "V1 normalized BUY adapter is accepted as canonical research source for V1 decomposition; "
            "this is not proof of old BTC baseline parity"
        ),
        "known_non_equivalence_to_old_baseline": True,
        "promotion_blocker_if_old_parity_unproven": False,
        "old_baseline": {
            "event_source": "completion_candidate_pipeline_v1 local BTC baseline",
            "runner_taker_side": "SELL",
            "event_mix": "public_trade plus l1_price_change candidate base; selected actions from legacy state machine",
            "side_boolean": "YES/NO outcome side",
            "timestamp_source": "legacy candidate ts_ms",
            "price_source": "legacy public_trade_price/seed_px",
            "l1_pair_ask_source": "legacy L1 pair ask field",
            "offset_window": "0 <= offset_s < 300 in candidate base; selected actions by legacy state machine",
        },
        "v1_normalized": {
            "event_source": "btc_completion_candidate_base_from_l1_flow_taker_normalized_v1",
            "runner_taker_side": "BUY",
            "event_mix": "public_trade only from normalized multiasset L1 flow/search-safe source",
            "side_boolean": "YES/NO outcome side",
            "timestamp_source": "normalized candidate ts_ms from core replay md_trades/source event",
            "price_source": "normalized public_trade_price/seed_px",
            "l1_pair_ask_source": "md_book_l1 canonical top / search-safe L1 pair ask",
            "offset_window": "selected actions generated by the normalized completion state machine",
        },
        "overlap_definition": {
            "bucket_ms": args.bucket_ms,
            "old_baseline_overlap": "V1 selected actions with same day, condition_id, side and floor(ts_ms/bucket_ms) in old selected actions.",
            "v1_normalized_new_only": "V1 selected actions without that old selected-action bucket.",
            "v1_normalized_all": "All V1 normalized selected actions.",
        },
    }

    manifest = {
        "schema_version": "btc_v1_old_baseline_overlap_decomposition_report_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_READY_RESEARCH_ONLY",
        "source_semantics_contract": source_semantics_contract,
        "mismatch_attribution": clean(mismatch_attribution),
        "summary": {
            "bucket_count": len(bucket_summary_rows),
            "old_parity_status": semantic_alignment.get("status") or "MISSING",
            "source_semantics_delta_status": source_semantics.get("status") or "MISSING",
            "bucket_ms": args.bucket_ms,
            "capacity_notional": args.capacity_notional,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_orders_allowed": False,
        },
        "buckets": bucket_summary_rows,
        "outputs": {
            "duckdb": str(out_db),
            "bucket_summary_csv": str(output_dir / "bucket_summary.csv"),
            "bucket_summary_rounded_csv": str(output_dir / "bucket_summary_rounded.csv"),
            "bucket_day_summary_csv": str(output_dir / "bucket_day_summary.csv"),
            "bucket_day_summary_rounded_csv": str(output_dir / "bucket_day_summary_rounded.csv"),
            "pair_cost_distribution_csv": str(output_dir / "pair_cost_distribution.csv"),
            "pair_cost_distribution_rounded_csv": str(output_dir / "pair_cost_distribution_rounded.csv"),
            "capital_curve_csv": str(output_dir / "capital_curve.csv"),
        },
        "policy": {
            "research_ranking_material": True,
            "promotion_gate": {
                "private_truth_ready": False,
                "strategy_promotion_ready": False,
                "deployable": False,
                "live_orders_allowed": False,
            },
            "residual_settlement_pnl_is_strategy_edge": False,
            "historical_public_or_shadow_can_set_private_truth_ready": False,
        },
        "inputs": {
            "old_state_machine_dir": str(old_dir),
            "new_state_machine_dir": str(new_dir),
            "btc_semantic_alignment": str(args.btc_semantic_alignment.expanduser()),
            "btc_source_semantics": str(args.btc_source_semantics.expanduser()),
        },
    }
    manifest_path = output_dir / "BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {"status": manifest["status"], "summary": manifest["summary"], "buckets": bucket_summary_rows},
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

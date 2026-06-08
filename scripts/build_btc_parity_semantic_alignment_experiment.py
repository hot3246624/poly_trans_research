#!/usr/bin/env python3
"""Run event/window-level BTC semantic alignment checks for parity review."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASE = (
    DEFAULT_DATA_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
DEFAULT_NEW_BASE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_candidate_base_from_l1_flow_taker_normalized_v1"
)
DEFAULT_OLD_STATE_MACHINE = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
DEFAULT_NEW_STATE_MACHINE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rounded(value: Any, digits: int = 6) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def safe_div(num: Any, den: Any) -> float | None:
    try:
        n = float(num)
        d = float(den)
    except (TypeError, ValueError):
        return None
    if d == 0.0:
        return None
    return n / d


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def manifest_db(manifest_path: Path, default_db: str, default_table: str) -> tuple[Path, str, dict[str, Any]]:
    manifest = read_json(manifest_path)
    outputs = manifest.get("outputs") or {}
    db_name = outputs.get("duckdb") or default_db
    table = outputs.get("duckdb_table") or outputs.get("table") or default_table
    return manifest_path.parent / str(db_name), str(table), manifest


def side_predicate(mode: str) -> tuple[str, str]:
    if mode == "same_side":
        return "o.side = n.side", "abs(o.public_trade_price - n.public_trade_price)"
    if mode == "opposite_side_complement":
        return "o.side <> n.side", "abs((o.public_trade_price + n.public_trade_price) - 1.0)"
    if mode == "any_side_best_semantic":
        return (
            "1=1",
            "CASE WHEN o.side = n.side "
            "THEN abs(o.public_trade_price - n.public_trade_price) "
            "ELSE abs((o.public_trade_price + n.public_trade_price) - 1.0) END",
        )
    raise ValueError(f"unsupported side match mode: {mode}")


def action_match_row(
    con: Any,
    old_table: str,
    new_table: str,
    mode: str,
    tolerance_ms: int,
) -> dict[str, Any]:
    side_sql, price_sql = side_predicate(mode)
    row = con.execute(
        f"""
        WITH old_total AS (
          SELECT count(*) AS c FROM old_sm.main.{old_table}
        ),
        new_total AS (
          SELECT count(*) AS c FROM new_sm.main.{new_table}
        ),
        nearest AS (
          SELECT
            o.action_id AS old_action_id,
            n.action_id AS new_action_id,
            abs(o.ts_ms - n.ts_ms) AS dt_ms,
            {price_sql} AS semantic_price_delta,
            row_number() OVER (
              PARTITION BY o.action_id
              ORDER BY abs(o.ts_ms - n.ts_ms), {price_sql}, n.action_id
            ) AS rn
          FROM old_sm.main.{old_table} o
          JOIN new_sm.main.{new_table} n
            ON o.condition_id = n.condition_id
           AND {side_sql}
           AND abs(o.ts_ms - n.ts_ms) <= {int(tolerance_ms)}
          WHERE o.side IN ('YES', 'NO')
            AND n.side IN ('YES', 'NO')
        ),
        best AS (
          SELECT * FROM nearest WHERE rn = 1
        )
        SELECT
          (SELECT c FROM old_total) AS old_action_count,
          (SELECT c FROM new_total) AS new_action_count,
          count(*) AS matched_old_action_count,
          count(DISTINCT new_action_id) AS matched_new_action_count,
          avg(dt_ms) AS avg_dt_ms,
          quantile_cont(dt_ms, 0.5) AS p50_dt_ms,
          quantile_cont(dt_ms, 0.9) AS p90_dt_ms,
          max(dt_ms) AS max_dt_ms,
          avg(semantic_price_delta) AS avg_semantic_price_delta,
          quantile_cont(semantic_price_delta, 0.5) AS p50_semantic_price_delta,
          quantile_cont(semantic_price_delta, 0.9) AS p90_semantic_price_delta,
          max(semantic_price_delta) AS max_semantic_price_delta
        FROM best
        """
    ).fetchone()
    fields = [desc[0] for desc in con.description]
    out = dict(zip(fields, row))
    out["match_scope"] = "selected_actions"
    out["match_mode"] = mode
    out["tolerance_ms"] = tolerance_ms
    out["old_action_match_rate"] = rounded(
        safe_div(out.get("matched_old_action_count"), out.get("old_action_count"))
    )
    out["new_action_match_rate"] = rounded(
        safe_div(out.get("matched_new_action_count"), out.get("new_action_count"))
    )
    for key in (
        "avg_dt_ms",
        "p50_dt_ms",
        "p90_dt_ms",
        "max_dt_ms",
        "avg_semantic_price_delta",
        "p50_semantic_price_delta",
        "p90_semantic_price_delta",
        "max_semantic_price_delta",
    ):
        out[key] = rounded(out.get(key))
    return out


def action_match_by_day(
    con: Any,
    old_table: str,
    new_table: str,
    mode: str,
    tolerance_ms: int,
) -> list[dict[str, Any]]:
    side_sql, price_sql = side_predicate(mode)
    rows = con.execute(
        f"""
        WITH old_day AS (
          SELECT day, count(*) AS old_action_count
          FROM old_sm.main.{old_table}
          GROUP BY day
        ),
        new_day AS (
          SELECT day, count(*) AS new_action_count
          FROM new_sm.main.{new_table}
          GROUP BY day
        ),
        nearest AS (
          SELECT
            o.day,
            o.action_id AS old_action_id,
            n.action_id AS new_action_id,
            abs(o.ts_ms - n.ts_ms) AS dt_ms,
            {price_sql} AS semantic_price_delta,
            row_number() OVER (
              PARTITION BY o.action_id
              ORDER BY abs(o.ts_ms - n.ts_ms), {price_sql}, n.action_id
            ) AS rn
          FROM old_sm.main.{old_table} o
          JOIN new_sm.main.{new_table} n
            ON o.condition_id = n.condition_id
           AND {side_sql}
           AND abs(o.ts_ms - n.ts_ms) <= {int(tolerance_ms)}
          WHERE o.side IN ('YES', 'NO')
            AND n.side IN ('YES', 'NO')
        ),
        best_day AS (
          SELECT
            day,
            count(*) AS matched_old_action_count,
            count(DISTINCT new_action_id) AS matched_new_action_count,
            avg(dt_ms) AS avg_dt_ms,
            quantile_cont(dt_ms, 0.9) AS p90_dt_ms,
            quantile_cont(semantic_price_delta, 0.9) AS p90_semantic_price_delta
          FROM nearest
          WHERE rn = 1
          GROUP BY day
        )
        SELECT
          coalesce(o.day, n.day, b.day) AS day,
          o.old_action_count,
          n.new_action_count,
          coalesce(b.matched_old_action_count, 0) AS matched_old_action_count,
          coalesce(b.matched_new_action_count, 0) AS matched_new_action_count,
          coalesce(b.matched_old_action_count, 0)::DOUBLE / NULLIF(o.old_action_count, 0) AS old_action_match_rate,
          coalesce(b.matched_new_action_count, 0)::DOUBLE / NULLIF(n.new_action_count, 0) AS new_action_match_rate,
          b.avg_dt_ms,
          b.p90_dt_ms,
          b.p90_semantic_price_delta
        FROM old_day o
        FULL OUTER JOIN new_day n USING(day)
        FULL OUTER JOIN best_day b USING(day)
        ORDER BY day
        """
    ).fetchall()
    fields = [desc[0] for desc in con.description]
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(zip(fields, raw))
        row["match_scope"] = "selected_actions"
        row["match_mode"] = mode
        row["tolerance_ms"] = tolerance_ms
        for key in (
            "old_action_match_rate",
            "new_action_match_rate",
            "avg_dt_ms",
            "p90_dt_ms",
            "p90_semantic_price_delta",
        ):
            row[key] = rounded(row.get(key))
        out.append(row)
    return out


def bucket_alignment_row(
    con: Any,
    old_table: str,
    new_table: str,
    mode: str,
    bucket_ms: int,
    old_runner_taker_side: str,
    new_runner_taker_side: str,
) -> dict[str, Any]:
    old_filter = "1=1" if old_runner_taker_side == "ANY" else f"public_trade_taker_side = '{old_runner_taker_side}'"
    new_filter = "1=1" if new_runner_taker_side == "ANY" else f"public_trade_taker_side = '{new_runner_taker_side}'"
    group_cols = "day, condition_id, side, floor(ts_ms / {bucket_ms})::BIGINT"
    join_using = "day, condition_id, side, bucket_id"
    price_sql = "abs(o.avg_public_trade_price - n.avg_public_trade_price)"
    join_sql = f"FULL OUTER JOIN new_r n USING({join_using})"
    select_day = "coalesce(o.day, n.day)"
    if mode == "opposite_side_complement":
        join_sql = (
            "JOIN new_r n ON o.day = n.day AND o.condition_id = n.condition_id "
            "AND o.bucket_id = n.bucket_id AND o.side <> n.side"
        )
        price_sql = "abs((o.avg_public_trade_price + n.avg_public_trade_price) - 1.0)"
        select_day = "o.day"
    elif mode == "market_any_side":
        group_cols = "day, condition_id, floor(ts_ms / {bucket_ms})::BIGINT"
        join_using = "day, condition_id, bucket_id"
        join_sql = f"FULL OUTER JOIN new_r n USING({join_using})"
        price_sql = "NULL"
    elif mode != "same_side":
        raise ValueError(f"unsupported bucket alignment mode: {mode}")

    row = con.execute(
        f"""
        WITH old_r AS (
          SELECT
            {group_cols.format(bucket_ms=int(bucket_ms))} AS bucket_id,
            count(*) AS row_count,
            avg(public_trade_price) AS avg_public_trade_price
          FROM old_base.main.{old_table}
          WHERE event_kind = 'public_trade'
            AND {old_filter}
            AND side IN ('YES', 'NO')
            AND offset_s >= 0
            AND offset_s < 300
          GROUP BY ALL
        ),
        new_r AS (
          SELECT
            {group_cols.format(bucket_ms=int(bucket_ms))} AS bucket_id,
            count(*) AS row_count,
            avg(public_trade_price) AS avg_public_trade_price
          FROM new_base.main.{new_table}
          WHERE event_kind = 'public_trade'
            AND {new_filter}
            AND side IN ('YES', 'NO')
            AND offset_s >= 0
            AND offset_s < 300
          GROUP BY ALL
        ),
        joined AS (
          SELECT
            {select_day} AS bucket_day,
            o.row_count AS old_rows,
            n.row_count AS new_rows,
            {price_sql} AS semantic_price_delta
          FROM old_r o
          {join_sql}
        )
        SELECT
          (SELECT count(*) FROM old_r) AS old_bucket_count,
          (SELECT count(*) FROM new_r) AS new_bucket_count,
          count(*) FILTER (WHERE old_rows IS NOT NULL AND new_rows IS NOT NULL) AS common_bucket_count,
          (SELECT sum(row_count) FROM old_r) AS old_runner_rows,
          (SELECT sum(row_count) FROM new_r) AS new_runner_rows,
          sum(old_rows) FILTER (WHERE new_rows IS NOT NULL) AS old_rows_in_common_buckets,
          sum(new_rows) FILTER (WHERE old_rows IS NOT NULL) AS new_rows_in_common_buckets,
          avg(semantic_price_delta) FILTER (WHERE old_rows IS NOT NULL AND new_rows IS NOT NULL) AS avg_semantic_price_delta,
          quantile_cont(semantic_price_delta, 0.5) FILTER (
            WHERE old_rows IS NOT NULL AND new_rows IS NOT NULL
          ) AS p50_semantic_price_delta,
          quantile_cont(semantic_price_delta, 0.9) FILTER (
            WHERE old_rows IS NOT NULL AND new_rows IS NOT NULL
          ) AS p90_semantic_price_delta
        FROM joined
        """
    ).fetchone()
    fields = [desc[0] for desc in con.description]
    out = dict(zip(fields, row))
    out["match_scope"] = "runner_event_buckets"
    out["match_mode"] = mode
    out["bucket_ms"] = bucket_ms
    out["old_bucket_match_rate"] = rounded(
        safe_div(out.get("common_bucket_count"), out.get("old_bucket_count"))
    )
    out["new_bucket_match_rate"] = rounded(
        safe_div(out.get("common_bucket_count"), out.get("new_bucket_count"))
    )
    out["old_row_coverage_in_common_buckets"] = rounded(
        safe_div(out.get("old_rows_in_common_buckets"), out.get("old_runner_rows"))
    )
    out["new_row_coverage_in_common_buckets"] = rounded(
        safe_div(out.get("new_rows_in_common_buckets"), out.get("new_runner_rows"))
    )
    for key in ("avg_semantic_price_delta", "p50_semantic_price_delta", "p90_semantic_price_delta"):
        out[key] = rounded(out.get(key))
    return out


def market_divergence_rows(
    con: Any,
    old_base_table: str,
    new_base_table: str,
    old_actions_table: str,
    new_actions_table: str,
    old_runner_taker_side: str,
    new_runner_taker_side: str,
    limit: int,
) -> list[dict[str, Any]]:
    old_filter = "1=1" if old_runner_taker_side == "ANY" else f"public_trade_taker_side = '{old_runner_taker_side}'"
    new_filter = "1=1" if new_runner_taker_side == "ANY" else f"public_trade_taker_side = '{new_runner_taker_side}'"
    rows = con.execute(
        f"""
        WITH old_runner AS (
          SELECT condition_id, any_value(slug) AS slug, any_value(day) AS day, count(*) AS old_runner_rows
          FROM old_base.main.{old_base_table}
          WHERE event_kind = 'public_trade'
            AND {old_filter}
            AND side IN ('YES', 'NO')
            AND offset_s >= 0
            AND offset_s < 300
          GROUP BY condition_id
        ),
        new_runner AS (
          SELECT condition_id, any_value(slug) AS slug, any_value(day) AS day, count(*) AS new_runner_rows
          FROM new_base.main.{new_base_table}
          WHERE event_kind = 'public_trade'
            AND {new_filter}
            AND side IN ('YES', 'NO')
            AND offset_s >= 0
            AND offset_s < 300
          GROUP BY condition_id
        ),
        old_actions AS (
          SELECT condition_id, count(*) AS old_selected_actions
          FROM old_sm.main.{old_actions_table}
          GROUP BY condition_id
        ),
        new_actions AS (
          SELECT condition_id, count(*) AS new_selected_actions
          FROM new_sm.main.{new_actions_table}
          GROUP BY condition_id
        )
        SELECT
          coalesce(o.day, n.day) AS day,
          coalesce(o.condition_id, n.condition_id) AS condition_id,
          coalesce(o.slug, n.slug) AS slug,
          coalesce(o.old_runner_rows, 0) AS old_runner_rows,
          coalesce(n.new_runner_rows, 0) AS new_runner_rows,
          coalesce(n.new_runner_rows, 0) - coalesce(o.old_runner_rows, 0) AS runner_row_delta,
          coalesce(n.new_runner_rows, 0)::DOUBLE / NULLIF(o.old_runner_rows, 0) AS runner_row_ratio,
          coalesce(oa.old_selected_actions, 0) AS old_selected_actions,
          coalesce(na.new_selected_actions, 0) AS new_selected_actions,
          coalesce(na.new_selected_actions, 0) - coalesce(oa.old_selected_actions, 0) AS selected_action_delta
        FROM old_runner o
        FULL OUTER JOIN new_runner n USING(condition_id)
        LEFT JOIN old_actions oa ON oa.condition_id = coalesce(o.condition_id, n.condition_id)
        LEFT JOIN new_actions na ON na.condition_id = coalesce(o.condition_id, n.condition_id)
        ORDER BY abs(runner_row_delta) DESC, condition_id
        LIMIT {int(limit)}
        """
    ).fetchall()
    fields = [desc[0] for desc in con.description]
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(zip(fields, raw))
        row["runner_row_ratio"] = rounded(row.get("runner_row_ratio"))
        out.append(row)
    return out


def example_rows(
    con: Any,
    old_table: str,
    new_table: str,
    limit_per_type: int,
) -> list[dict[str, Any]]:
    examples: list[dict[str, Any]] = []
    fields = [
        "row_type",
        "day",
        "condition_id",
        "slug",
        "old_action_id",
        "new_action_id",
        "old_ts_ms",
        "new_ts_ms",
        "dt_ms",
        "old_side",
        "new_side",
        "old_public_trade_price",
        "new_public_trade_price",
        "semantic_price_delta",
        "old_seed_px",
        "new_seed_px",
    ]
    queries = [
        (
            "matched_same_side_1s",
            "o.side = n.side",
            "abs(o.public_trade_price - n.public_trade_price)",
            1000,
            True,
        ),
        (
            "matched_opposite_complement_1s",
            "o.side <> n.side",
            "abs((o.public_trade_price + n.public_trade_price) - 1.0)",
            1000,
            True,
        ),
    ]
    for row_type, side_sql, price_sql, tolerance_ms, matched in queries:
        if not matched:
            continue
        rows = con.execute(
            f"""
            WITH nearest AS (
              SELECT
                {quote(row_type)} AS row_type,
                o.day,
                o.condition_id,
                o.slug,
                o.action_id AS old_action_id,
                n.action_id AS new_action_id,
                o.ts_ms AS old_ts_ms,
                n.ts_ms AS new_ts_ms,
                abs(o.ts_ms - n.ts_ms) AS dt_ms,
                o.side AS old_side,
                n.side AS new_side,
                o.public_trade_price AS old_public_trade_price,
                n.public_trade_price AS new_public_trade_price,
                {price_sql} AS semantic_price_delta,
                o.seed_px AS old_seed_px,
                n.seed_px AS new_seed_px,
                row_number() OVER (
                  PARTITION BY o.action_id
                  ORDER BY abs(o.ts_ms - n.ts_ms), {price_sql}, n.action_id
                ) AS rn
              FROM old_sm.main.{old_table} o
              JOIN new_sm.main.{new_table} n
                ON o.condition_id = n.condition_id
               AND {side_sql}
               AND abs(o.ts_ms - n.ts_ms) <= {int(tolerance_ms)}
              WHERE o.side IN ('YES', 'NO')
                AND n.side IN ('YES', 'NO')
            )
            SELECT {", ".join(fields)}
            FROM nearest
            WHERE rn = 1
            ORDER BY dt_ms, semantic_price_delta, old_action_id
            LIMIT {int(limit_per_type)}
            """
        ).fetchall()
        examples.extend(dict(zip(fields, row)) for row in rows)

    rows = con.execute(
        f"""
        SELECT
          'unmatched_old_same_side_5s' AS row_type,
          o.day,
          o.condition_id,
          o.slug,
          o.action_id AS old_action_id,
          NULL AS new_action_id,
          o.ts_ms AS old_ts_ms,
          NULL AS new_ts_ms,
          NULL AS dt_ms,
          o.side AS old_side,
          NULL AS new_side,
          o.public_trade_price AS old_public_trade_price,
          NULL AS new_public_trade_price,
          NULL AS semantic_price_delta,
          o.seed_px AS old_seed_px,
          NULL AS new_seed_px
        FROM old_sm.main.{old_table} o
        WHERE o.side IN ('YES', 'NO')
          AND NOT EXISTS (
            SELECT 1
            FROM new_sm.main.{new_table} n
            WHERE n.condition_id = o.condition_id
              AND n.side = o.side
              AND abs(n.ts_ms - o.ts_ms) <= 5000
          )
        ORDER BY o.day, o.condition_id, o.ts_ms
        LIMIT {int(limit_per_type)}
        """
    ).fetchall()
    examples.extend(dict(zip(fields, row)) for row in rows)
    return examples


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb  # type: ignore

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    old_base_db, old_base_table, old_base_manifest = manifest_db(
        args.old_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json",
        "candidate_base.duckdb",
        "candidate_base",
    )
    new_base_db, new_base_table, new_base_manifest = manifest_db(
        args.new_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json",
        "candidate_base.duckdb",
        "candidate_base",
    )
    old_sm_db, old_actions_table, old_sm_manifest = manifest_db(
        args.old_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json",
        "state_machine_results.duckdb",
        "actions",
    )
    new_sm_db, new_actions_table, new_sm_manifest = manifest_db(
        args.new_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json",
        "state_machine_results.duckdb",
        "actions",
    )

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {quote(old_base_db)} AS old_base (READ_ONLY)")
        con.execute(f"ATTACH {quote(new_base_db)} AS new_base (READ_ONLY)")
        con.execute(f"ATTACH {quote(old_sm_db)} AS old_sm (READ_ONLY)")
        con.execute(f"ATTACH {quote(new_sm_db)} AS new_sm (READ_ONLY)")

        action_summary_rows: list[dict[str, Any]] = []
        for mode in ("same_side", "opposite_side_complement", "any_side_best_semantic"):
            for tolerance_ms in args.action_tolerance_ms:
                action_summary_rows.append(
                    action_match_row(con, old_actions_table, new_actions_table, mode, tolerance_ms)
                )

        by_day_rows = action_match_by_day(
            con,
            old_actions_table,
            new_actions_table,
            "same_side",
            args.primary_action_tolerance_ms,
        )

        bucket_rows: list[dict[str, Any]] = []
        for mode in ("same_side", "opposite_side_complement", "market_any_side"):
            for bucket_ms in args.bucket_ms:
                bucket_rows.append(
                    bucket_alignment_row(
                        con,
                        old_base_table,
                        new_base_table,
                        mode,
                        bucket_ms,
                        args.old_runner_taker_side,
                        args.new_runner_taker_side,
                    )
                )

        divergence_rows = market_divergence_rows(
            con,
            old_base_table,
            new_base_table,
            old_actions_table,
            new_actions_table,
            args.old_runner_taker_side,
            args.new_runner_taker_side,
            args.market_divergence_limit,
        )
        examples = example_rows(con, old_actions_table, new_actions_table, args.example_limit_per_type)
        condition_slug_summary_row = con.execute(
            f"""
            WITH old_m AS (
              SELECT condition_id, any_value(slug) AS slug, count(*) AS old_selected_actions
              FROM old_sm.main.{old_actions_table}
              GROUP BY 1
            ),
            new_m AS (
              SELECT condition_id, any_value(slug) AS slug, count(*) AS new_selected_actions
              FROM new_sm.main.{new_actions_table}
              GROUP BY 1
            )
            SELECT
              count(*) FILTER (WHERE o.condition_id IS NOT NULL) AS old_condition_count,
              count(*) FILTER (WHERE n.condition_id IS NOT NULL) AS new_condition_count,
              count(*) FILTER (WHERE o.condition_id IS NOT NULL AND n.condition_id IS NOT NULL) AS overlap_condition_count,
              count(*) FILTER (WHERE o.condition_id IS NULL AND n.condition_id IS NOT NULL) AS new_only_condition_count,
              count(*) FILTER (WHERE o.condition_id IS NOT NULL AND n.condition_id IS NULL) AS old_only_condition_count,
              count(*) FILTER (
                WHERE o.condition_id IS NOT NULL
                  AND n.condition_id IS NOT NULL
                  AND coalesce(o.slug, '') <> coalesce(n.slug, '')
              ) AS slug_mismatch_count
            FROM old_m o
            FULL OUTER JOIN new_m n USING(condition_id)
            """
        ).fetchone()
        condition_slug_summary = dict(zip([desc[0] for desc in con.description], condition_slug_summary_row))
    finally:
        con.close()

    action_summary_csv = output_dir / "btc_parity_semantic_alignment_action_summary.csv"
    action_summary_fields = [
        "match_scope",
        "match_mode",
        "tolerance_ms",
        "old_action_count",
        "new_action_count",
        "matched_old_action_count",
        "matched_new_action_count",
        "old_action_match_rate",
        "new_action_match_rate",
        "avg_dt_ms",
        "p50_dt_ms",
        "p90_dt_ms",
        "max_dt_ms",
        "avg_semantic_price_delta",
        "p50_semantic_price_delta",
        "p90_semantic_price_delta",
        "max_semantic_price_delta",
    ]
    write_csv(action_summary_csv, action_summary_rows, action_summary_fields)

    by_day_csv = output_dir / "btc_parity_semantic_alignment_by_day.csv"
    by_day_fields = [
        "day",
        "match_scope",
        "match_mode",
        "tolerance_ms",
        "old_action_count",
        "new_action_count",
        "matched_old_action_count",
        "matched_new_action_count",
        "old_action_match_rate",
        "new_action_match_rate",
        "avg_dt_ms",
        "p90_dt_ms",
        "p90_semantic_price_delta",
    ]
    write_csv(by_day_csv, by_day_rows, by_day_fields)

    bucket_csv = output_dir / "btc_parity_semantic_alignment_bucket_summary.csv"
    bucket_fields = [
        "match_scope",
        "match_mode",
        "bucket_ms",
        "old_bucket_count",
        "new_bucket_count",
        "common_bucket_count",
        "old_bucket_match_rate",
        "new_bucket_match_rate",
        "old_runner_rows",
        "new_runner_rows",
        "old_rows_in_common_buckets",
        "new_rows_in_common_buckets",
        "old_row_coverage_in_common_buckets",
        "new_row_coverage_in_common_buckets",
        "avg_semantic_price_delta",
        "p50_semantic_price_delta",
        "p90_semantic_price_delta",
    ]
    write_csv(bucket_csv, bucket_rows, bucket_fields)

    divergence_csv = output_dir / "btc_parity_semantic_alignment_market_divergence.csv"
    divergence_fields = [
        "day",
        "condition_id",
        "slug",
        "old_runner_rows",
        "new_runner_rows",
        "runner_row_delta",
        "runner_row_ratio",
        "old_selected_actions",
        "new_selected_actions",
        "selected_action_delta",
    ]
    write_csv(divergence_csv, divergence_rows, divergence_fields)

    examples_csv = output_dir / "btc_parity_semantic_alignment_examples.csv"
    example_fields = [
        "row_type",
        "day",
        "condition_id",
        "slug",
        "old_action_id",
        "new_action_id",
        "old_ts_ms",
        "new_ts_ms",
        "dt_ms",
        "old_side",
        "new_side",
        "old_public_trade_price",
        "new_public_trade_price",
        "semantic_price_delta",
        "old_seed_px",
        "new_seed_px",
    ]
    write_csv(examples_csv, examples, example_fields)

    primary_action = next(
        row
        for row in action_summary_rows
        if row["match_mode"] == "same_side" and row["tolerance_ms"] == args.primary_action_tolerance_ms
    )
    primary_bucket = next(
        row
        for row in bucket_rows
        if row["match_mode"] == "same_side" and row["bucket_ms"] == args.primary_bucket_ms
    )
    primary_market_any_bucket = next(
        row
        for row in bucket_rows
        if row["match_mode"] == "market_any_side" and row["bucket_ms"] == args.primary_bucket_ms
    )
    primary_opposite_bucket = next(
        row
        for row in bucket_rows
        if row["match_mode"] == "opposite_side_complement" and row["bucket_ms"] == args.primary_bucket_ms
    )
    old_core = old_sm_manifest.get("core_metrics") or {}
    new_core = new_sm_manifest.get("core_metrics") or {}
    old_candidate_count = old_core.get("candidate_count")
    new_candidate_count = new_core.get("candidate_count")
    parity_proven = bool(
        (primary_action.get("old_action_match_rate") or 0.0) >= args.required_action_match_rate
        and (primary_action.get("new_action_match_rate") or 0.0) >= args.required_action_match_rate
        and (primary_bucket.get("old_row_coverage_in_common_buckets") or 0.0) >= args.required_bucket_row_coverage
        and (primary_bucket.get("new_row_coverage_in_common_buckets") or 0.0) >= args.required_bucket_row_coverage
        and (primary_action.get("p90_semantic_price_delta") or 1.0) <= args.required_p90_price_delta
    )
    status = "OK_BTC_SEMANTIC_ALIGNMENT_PROVEN" if parity_proven else "BLOCKED_BTC_SEMANTIC_ALIGNMENT_NOT_PROVEN"
    summary = {
        "old_runner_taker_side": args.old_runner_taker_side,
        "new_runner_taker_side": args.new_runner_taker_side,
        "old_runner_candidate_rows": old_candidate_count,
        "new_runner_candidate_rows": new_candidate_count,
        "runner_candidate_ratio_new_over_old": rounded(safe_div(new_candidate_count, old_candidate_count)),
        "primary_action_match_mode": primary_action["match_mode"],
        "primary_action_tolerance_ms": primary_action["tolerance_ms"],
        "primary_old_action_match_rate": primary_action["old_action_match_rate"],
        "primary_new_action_match_rate": primary_action["new_action_match_rate"],
        "primary_action_p90_dt_ms": primary_action["p90_dt_ms"],
        "primary_action_p90_semantic_price_delta": primary_action["p90_semantic_price_delta"],
        "primary_bucket_ms": primary_bucket["bucket_ms"],
        "primary_old_bucket_match_rate": primary_bucket["old_bucket_match_rate"],
        "primary_new_bucket_match_rate": primary_bucket["new_bucket_match_rate"],
        "primary_old_row_coverage_in_common_buckets": primary_bucket["old_row_coverage_in_common_buckets"],
        "primary_new_row_coverage_in_common_buckets": primary_bucket["new_row_coverage_in_common_buckets"],
        "primary_bucket_p90_semantic_price_delta": primary_bucket["p90_semantic_price_delta"],
        "parity_proven": parity_proven,
    }
    source_semantics_contract = {
        "source_semantics_contract_id": "btc_v1_source_semantics_alignment_contract_v1",
        "source_dataset_fingerprint": {
            "old_candidate_base_manifest": str(args.old_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"),
            "new_candidate_base_manifest": str(args.new_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"),
            "old_state_machine_manifest": str(args.old_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json"),
            "new_state_machine_manifest": str(args.new_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json"),
            "old_candidate_count": old_candidate_count,
            "new_candidate_count": new_candidate_count,
        },
        "l2_top_overlay_contract_id": "md_book_l2_top_aligned_l1_canonical_top_raw_l2_depth_asof_v1",
        "source_semantics_policy": (
            "This experiment tests whether the old BTC baseline and V1 normalized BUY adapter are equivalent. "
            "If parity is not proven, V1 normalized BUY may still be used as a new canonical research source, "
            "but it must not be represented as old-baseline parity or promotion readiness."
        ),
        "known_non_equivalence_to_old_baseline": not parity_proven,
        "promotion_blocker_if_old_parity_unproven": False,
        "old_baseline": {
            "event_source": "completion_candidate_pipeline_v1/local BTC candidate base and legacy state machine",
            "runner_taker_side": args.old_runner_taker_side,
            "event_kind": "public_trade runner rows with offset window",
            "side_boolean": "YES/NO outcome side from legacy candidate/action rows",
            "price_source": "legacy public_trade_price / seed_px",
            "timestamp_source": "legacy candidate/action ts_ms",
            "l1_pair_ask_source": "legacy candidate L1 pair ask fields",
            "offset_window": "offset_s >= 0 and offset_s < 300 for runner-window comparisons",
        },
        "v1_normalized": {
            "event_source": "btc_completion_candidate_base_from_l1_flow_taker_normalized_v1",
            "runner_taker_side": args.new_runner_taker_side,
            "event_kind": "normalized public_trade rows from multiasset L1/search-safe flow",
            "side_boolean": "YES/NO outcome side from normalized candidate/action rows",
            "price_source": "normalized public_trade_price / seed_px",
            "timestamp_source": "normalized candidate/action ts_ms from core replay event time",
            "l1_pair_ask_source": "md_book_l1 canonical top / L1 pair ask in normalized flow",
            "offset_window": "offset_s >= 0 and offset_s < 300 for runner-window comparisons",
        },
    }
    mismatch_attribution = {
        "side_mismatch": {
            "same_side_common_bucket_count": primary_bucket.get("common_bucket_count"),
            "opposite_side_complement_common_bucket_count": primary_opposite_bucket.get("common_bucket_count"),
            "market_any_side_common_bucket_count": primary_market_any_bucket.get("common_bucket_count"),
            "market_any_minus_same_side_bucket_count": max(
                int(primary_market_any_bucket.get("common_bucket_count") or 0)
                - int(primary_bucket.get("common_bucket_count") or 0),
                0,
            ),
        },
        "time_bucket_mismatch": {
            "old_bucket_count": primary_bucket.get("old_bucket_count"),
            "new_bucket_count": primary_bucket.get("new_bucket_count"),
            "common_bucket_count": primary_bucket.get("common_bucket_count"),
            "old_only_bucket_count": max(
                int(primary_bucket.get("old_bucket_count") or 0) - int(primary_bucket.get("common_bucket_count") or 0),
                0,
            ),
            "new_only_bucket_count": max(
                int(primary_bucket.get("new_bucket_count") or 0) - int(primary_bucket.get("common_bucket_count") or 0),
                0,
            ),
        },
        "selected_action_mismatch": {
            "old_action_count": primary_action.get("old_action_count"),
            "new_action_count": primary_action.get("new_action_count"),
            "matched_old_action_count": primary_action.get("matched_old_action_count"),
            "matched_new_action_count": primary_action.get("matched_new_action_count"),
            "old_only_action_count": max(
                int(primary_action.get("old_action_count") or 0)
                - int(primary_action.get("matched_old_action_count") or 0),
                0,
            ),
            "new_only_action_count": max(
                int(primary_action.get("new_action_count") or 0)
                - int(primary_action.get("matched_new_action_count") or 0),
                0,
            ),
        },
        "price_delta": {
            "primary_action_p50_semantic_price_delta": primary_action.get("p50_semantic_price_delta"),
            "primary_action_p90_semantic_price_delta": primary_action.get("p90_semantic_price_delta"),
            "primary_action_max_semantic_price_delta": primary_action.get("max_semantic_price_delta"),
            "primary_bucket_p90_semantic_price_delta": primary_bucket.get("p90_semantic_price_delta"),
        },
        "condition_slug_mismatch": condition_slug_summary,
        "new_only": {
            "candidate_row_delta": (new_candidate_count or 0) - (old_candidate_count or 0)
            if new_candidate_count is not None and old_candidate_count is not None
            else None,
            "new_only_condition_count": condition_slug_summary.get("new_only_condition_count"),
            "new_only_action_count": max(
                int(primary_action.get("new_action_count") or 0)
                - int(primary_action.get("matched_new_action_count") or 0),
                0,
            ),
        },
        "old_only": {
            "old_only_condition_count": condition_slug_summary.get("old_only_condition_count"),
            "old_only_action_count": max(
                int(primary_action.get("old_action_count") or 0)
                - int(primary_action.get("matched_old_action_count") or 0),
                0,
            ),
        },
    }
    manifest = {
        "schema_version": "btc_parity_semantic_alignment_experiment_v1",
        "created_utc": utc_now(),
        "status": status,
        "data_root": str(args.data_root.expanduser()),
        "old_candidate_base_manifest": str(args.old_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"),
        "new_candidate_base_manifest": str(args.new_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"),
        "old_state_machine_manifest": str(args.old_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json"),
        "new_state_machine_manifest": str(args.new_state_machine_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json"),
        "summary": summary,
        "source_semantics_contract": source_semantics_contract,
        "mismatch_attribution": mismatch_attribution,
        "decision": {
            "parity_proven": parity_proven,
            "status": status,
            "criteria": {
                "required_action_match_rate": args.required_action_match_rate,
                "required_bucket_row_coverage": args.required_bucket_row_coverage,
                "required_p90_price_delta": args.required_p90_price_delta,
            },
            "reason": (
                "Selected-action and runner-window coverage do not meet the explicit semantic alignment "
                "thresholds, so BTC parity remains blocked."
                if not parity_proven
                else "Selected actions and runner windows meet the explicit semantic alignment thresholds."
            ),
            "not_private_truth": True,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "deployable": False,
            "live_orders_allowed": False,
        },
        "semantics_under_test": {
            "old_btc_baseline": "legacy public_trade runner with public_trade_taker_side=SELL",
            "new_btc_adapter": "core replay md_trades taker_side normalized runner with public_trade_taker_side=BUY",
            "same_side": "same condition_id, same YES/NO side, nearby timestamp, direct price delta",
            "opposite_side_complement": "same condition_id, opposite YES/NO side, nearby timestamp, price complement delta",
            "market_any_side": "same condition_id and time bucket regardless of side",
        },
        "outputs": {
            "action_summary_csv": str(action_summary_csv),
            "by_day_csv": str(by_day_csv),
            "bucket_summary_csv": str(bucket_csv),
            "market_divergence_csv": str(divergence_csv),
            "examples_csv": str(examples_csv),
        },
    }
    manifest_path = output_dir / "BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "summary": summary, "outputs": manifest["outputs"]}, indent=2, sort_keys=True))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--old-base-dir", type=Path, default=DEFAULT_OLD_BASE)
    parser.add_argument("--new-base-dir", type=Path, default=DEFAULT_NEW_BASE)
    parser.add_argument("--old-state-machine-dir", type=Path, default=DEFAULT_OLD_STATE_MACHINE)
    parser.add_argument("--new-state-machine-dir", type=Path, default=DEFAULT_NEW_STATE_MACHINE)
    parser.add_argument("--old-runner-taker-side", choices=["SELL", "BUY", "ANY"], default="SELL")
    parser.add_argument("--new-runner-taker-side", choices=["SELL", "BUY", "ANY"], default="BUY")
    parser.add_argument("--action-tolerance-ms", type=int, nargs="+", default=[250, 1000, 5000])
    parser.add_argument("--bucket-ms", type=int, nargs="+", default=[1000, 5000])
    parser.add_argument("--primary-action-tolerance-ms", type=int, default=5000)
    parser.add_argument("--primary-bucket-ms", type=int, default=5000)
    parser.add_argument("--required-action-match-rate", type=float, default=0.99)
    parser.add_argument("--required-bucket-row-coverage", type=float, default=0.99)
    parser.add_argument("--required-p90-price-delta", type=float, default=0.01)
    parser.add_argument("--market-divergence-limit", type=int, default=100)
    parser.add_argument("--example-limit-per-type", type=int, default=100)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_parity_semantic_alignment_latest",
    )
    args = parser.parse_args()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

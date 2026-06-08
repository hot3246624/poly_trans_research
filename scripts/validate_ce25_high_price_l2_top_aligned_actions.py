#!/usr/bin/env python3
"""Validate CE25 high-price book-shadow actions against top-aligned L2 depth.

This is local research validation only. It reads existing book-shadow action
CSV files and the local md_book_l2_top_aligned mart. It does not fetch live
data, load private keys, import candidates, place orders, or claim promotion.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_L2_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
)
DEFAULT_CANDIDATE_BASE_DUCKDB = (
    DEFAULT_DATA_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
NON_CLAIMS = {
    "private_truth_ready": False,
    "strategy_promotion_ready": False,
    "live_ready": False,
    "deployable": False,
    "canary_authorized": False,
    "orders_authorized": False,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def qlit(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    if not fields:
        fields = ["status"]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fp:
        for block in iter(lambda: fp.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out


def load_result_manifest(actions_csv: Path, override: Path | None) -> dict[str, Any]:
    manifest_path = override or actions_csv.parent / "BOOK_SHADOW_RESULT_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing result manifest: {manifest_path}")
    manifest = read_json(manifest_path)
    manifest["_path"] = str(manifest_path)
    manifest["_sha256"] = sha256_file(manifest_path)
    return manifest


def l2_mart_db_from_manifest(manifest_path: Path) -> tuple[dict[str, Any], Path]:
    manifest = read_json(manifest_path)
    db = Path(str(manifest.get("output_duckdb") or manifest_path.parent / "l2_top_aligned_mart.duckdb")).expanduser()
    if not db.is_file():
        raise FileNotFoundError(f"missing L2 top-aligned duckdb: {db}")
    return manifest, db


def validate_actions(args: argparse.Namespace) -> dict[str, Any]:
    actions_csv = args.actions_csv.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists() and not args.force:
        raise FileExistsError(f"output exists; pass --force: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    result_manifest = load_result_manifest(actions_csv, args.result_manifest.expanduser().resolve() if args.result_manifest else None)
    variant = result_manifest.get("variant") if isinstance(result_manifest.get("variant"), dict) else {}
    pair_cap = args.pair_cap if args.pair_cap is not None else as_float(variant.get("seed_l1_pair_cap"), 0.98)

    l2_manifest_path = args.l2_top_aligned_mart_manifest.expanduser().resolve()
    l2_manifest, l2_db = l2_mart_db_from_manifest(l2_manifest_path)
    if l2_manifest.get("status") != "OK":
        raise SystemExit(f"L2 top-aligned mart is not OK: {l2_manifest.get('status')}")

    con = duckdb.connect(":memory:")
    try:
        con.execute("SET threads=1")
        con.execute("SET preserve_insertion_order=false")
        con.execute(f"ATTACH {qlit(l2_db)} AS l2 (READ_ONLY)")
        candidate_base_duckdb = args.candidate_base_duckdb.expanduser().resolve()
        if not candidate_base_duckdb.is_file():
            raise FileNotFoundError(f"missing candidate-base duckdb: {candidate_base_duckdb}")
        con.execute(f"ATTACH {qlit(candidate_base_duckdb)} AS cb (READ_ONLY)")
        con.execute(
            f"""
            CREATE TEMP TABLE actions AS
            SELECT *
            FROM read_csv_auto({qlit(actions_csv)}, HEADER=TRUE, ALL_VARCHAR=TRUE)
            """
        )
        action_count = int(con.execute("SELECT count(*) FROM actions").fetchone()[0] or 0)
        if action_count == 0:
            raise SystemExit("actions CSV has zero rows")
        con.execute(
            f"""
            CREATE TEMP TABLE action_source_bridge_candidates AS
            SELECT
              a.candidate_id,
              c.candidate_row_id,
              c.strict_l1_row_id,
              c.strict_l2_row_id,
              c.strict_l1_age_ms,
              c.strict_l2_age_ms,
              row_number() OVER (
                PARTITION BY a.candidate_id
                ORDER BY
                  abs(c.side_ask - CAST(a.first_leg_price AS DOUBLE))
                  + abs(c.opp_ask - CAST(a.completion_leg_price AS DOUBLE)),
                  c.candidate_row_id
              ) AS rn
            FROM actions a
            LEFT JOIN cb.main.candidate_base c
              ON c.condition_id = a.condition_id
             AND c.day = a.day
             AND c.ts_ms = CAST(a.first_leg_ts_ms AS BIGINT)
             AND (
               (
                 c.side = a.first_leg_side
                 AND abs(c.side_ask - CAST(a.first_leg_price AS DOUBLE)) <= {float(args.price_epsilon)}
                 AND abs(c.opp_ask - CAST(a.completion_leg_price AS DOUBLE)) <= {float(args.price_epsilon)}
               )
               OR (
                 c.opposite_side = a.first_leg_side
                 AND abs(c.opp_ask - CAST(a.first_leg_price AS DOUBLE)) <= {float(args.price_epsilon)}
                 AND abs(c.side_ask - CAST(a.completion_leg_price AS DOUBLE)) <= {float(args.price_epsilon)}
               )
             )
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE action_source_bridge AS
            SELECT *
            FROM action_source_bridge_candidates
            WHERE rn = 1
            """
        )
        con.execute(
            """
            CREATE TEMP TABLE action_legs AS
            SELECT
              candidate_id,
              variant_id,
              policy_id,
              branch_id,
              condition_id,
              slug,
              day,
              'first' AS leg_role,
              first_leg_side AS leg_side,
              CAST(first_leg_ts_ms AS BIGINT) AS leg_ts_ms,
              CAST(first_leg_price AS DOUBLE) AS leg_price,
              CAST(paired_qty AS DOUBLE) AS leg_qty,
              CAST(pair_cost AS DOUBLE) AS pair_cost,
              CAST(paired_qty AS DOUBLE) AS paired_qty,
              CAST(buy_actual_est AS DOUBLE) AS buy_actual_est,
              CAST(cash_pnl_est AS DOUBLE) AS cash_pnl_est,
              b.strict_l1_row_id AS preferred_l1_source_row_id,
              b.strict_l2_row_id AS preferred_l2_source_row_id,
              b.strict_l1_age_ms AS candidate_strict_l1_age_ms,
              b.strict_l2_age_ms AS candidate_strict_l2_age_ms
            FROM actions
            LEFT JOIN action_source_bridge b USING (candidate_id)
            UNION ALL
            SELECT
              candidate_id,
              variant_id,
              policy_id,
              branch_id,
              condition_id,
              slug,
              day,
              'completion' AS leg_role,
              completion_leg_side AS leg_side,
              CAST(completion_leg_ts_ms AS BIGINT) AS leg_ts_ms,
              CAST(completion_leg_price AS DOUBLE) AS leg_price,
              CAST(paired_qty AS DOUBLE) AS leg_qty,
              CAST(pair_cost AS DOUBLE) AS pair_cost,
              CAST(paired_qty AS DOUBLE) AS paired_qty,
              CAST(buy_actual_est AS DOUBLE) AS buy_actual_est,
              CAST(cash_pnl_est AS DOUBLE) AS cash_pnl_est,
              b.strict_l1_row_id AS preferred_l1_source_row_id,
              b.strict_l2_row_id AS preferred_l2_source_row_id,
              b.strict_l1_age_ms AS candidate_strict_l1_age_ms,
              b.strict_l2_age_ms AS candidate_strict_l2_age_ms
            FROM actions
            LEFT JOIN action_source_bridge b USING (candidate_id)
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE action_leg_keys AS
            SELECT
              day,
              condition_id,
              leg_side,
              preferred_l1_source_row_id,
              min(leg_ts_ms) AS min_leg_ts_ms,
              max(leg_ts_ms) AS max_leg_ts_ms
            FROM action_legs
            WHERE preferred_l1_source_row_id IS NOT NULL
            GROUP BY day, condition_id, leg_side, preferred_l1_source_row_id
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE l2_subset AS
            SELECT m.*
            FROM l2.main.md_book_l2_top_aligned m
            JOIN action_leg_keys k
              ON m.day = k.day
             AND m.condition_id = k.condition_id
             AND m.market_side = k.leg_side
             AND m.l1_source_row_id = k.preferred_l1_source_row_id
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE leg_joined AS
            SELECT
              l.*,
              m.l1_source_row_id,
              m.recv_ms AS l2_top_recv_ms,
              m.raw_l2_source_row_id,
              m.raw_l2_age_ms,
              m.top_overlay_required,
              m.top_source,
              m.depth_source,
              m.ask1_px,
              m.ask1_sz,
              m.raw_l2_ask2_px,
              m.raw_l2_ask2_sz,
              m.raw_l2_ask3_px,
              m.raw_l2_ask3_sz,
              m.raw_l2_ask4_px,
              m.raw_l2_ask4_sz,
              m.raw_l2_ask5_px,
              m.raw_l2_ask5_sz
            FROM action_legs l LEFT JOIN l2_subset m
              ON l.day = m.day
             AND l.condition_id = m.condition_id
             AND l.leg_side = m.market_side
             AND l.preferred_l1_source_row_id = m.l1_source_row_id
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE leg_evidence AS
            WITH base AS (
              SELECT
                *,
                CASE WHEN ask1_px IS NOT NULL AND ask1_sz > 0 THEN ask1_sz ELSE 0.0 END AS sz1,
                CASE WHEN raw_l2_ask2_px IS NOT NULL AND raw_l2_ask2_sz > 0 THEN raw_l2_ask2_sz ELSE 0.0 END AS sz2,
                CASE WHEN raw_l2_ask3_px IS NOT NULL AND raw_l2_ask3_sz > 0 THEN raw_l2_ask3_sz ELSE 0.0 END AS sz3,
                CASE WHEN raw_l2_ask4_px IS NOT NULL AND raw_l2_ask4_sz > 0 THEN raw_l2_ask4_sz ELSE 0.0 END AS sz4,
                CASE WHEN raw_l2_ask5_px IS NOT NULL AND raw_l2_ask5_sz > 0 THEN raw_l2_ask5_sz ELSE 0.0 END AS sz5
              FROM leg_joined
            ),
            takes AS (
              SELECT
                *,
                sz1 + sz2 + sz3 + sz4 + sz5 AS top5_ask_depth_qty,
                least(leg_qty, sz1) AS take1,
                least(greatest(leg_qty - sz1, 0.0), sz2) AS take2,
                least(greatest(leg_qty - sz1 - sz2, 0.0), sz3) AS take3,
                least(greatest(leg_qty - sz1 - sz2 - sz3, 0.0), sz4) AS take4,
                least(greatest(leg_qty - sz1 - sz2 - sz3 - sz4, 0.0), sz5) AS take5
              FROM base
            )
            SELECT
              *,
              raw_l2_source_row_id IS NOT NULL AS l2_depth_present,
              raw_l2_age_ms <= {int(args.max_raw_l2_age_ms)} AS raw_l2_age_ok,
              abs(coalesce(ask1_px, -999.0) - leg_price) <= {float(args.price_epsilon)} AS top_price_matches_action,
              sz1 >= leg_qty AS top1_depth_ge_qty,
              top5_ask_depth_qty >= leg_qty AS top5_depth_ge_qty,
              CASE WHEN top5_ask_depth_qty >= leg_qty THEN
                (
                  take1 * coalesce(ask1_px, 0.0)
                  + take2 * coalesce(raw_l2_ask2_px, 0.0)
                  + take3 * coalesce(raw_l2_ask3_px, 0.0)
                  + take4 * coalesce(raw_l2_ask4_px, 0.0)
                  + take5 * coalesce(raw_l2_ask5_px, 0.0)
                ) / leg_qty
              END AS top5_vwap_for_qty,
              CASE
                WHEN top5_ask_depth_qty < leg_qty THEN NULL
                WHEN leg_qty <= sz1 THEN ask1_px
                WHEN leg_qty <= sz1 + sz2 THEN raw_l2_ask2_px
                WHEN leg_qty <= sz1 + sz2 + sz3 THEN raw_l2_ask3_px
                WHEN leg_qty <= sz1 + sz2 + sz3 + sz4 THEN raw_l2_ask4_px
                ELSE raw_l2_ask5_px
              END AS top5_worst_px_for_qty
            FROM takes
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE action_evidence AS
            SELECT
              candidate_id,
              any_value(variant_id) AS variant_id,
              any_value(policy_id) AS policy_id,
              any_value(branch_id) AS branch_id,
              any_value(condition_id) AS condition_id,
              any_value(slug) AS slug,
              any_value(day) AS day,
              max(paired_qty) AS paired_qty,
              max(pair_cost) AS l1_pair_cost,
              max(buy_actual_est) AS buy_actual_est,
              max(cash_pnl_est) AS cash_pnl_est,
              sum(CASE WHEN leg_role = 'first' THEN top5_vwap_for_qty ELSE 0 END)
                + sum(CASE WHEN leg_role = 'completion' THEN top5_vwap_for_qty ELSE 0 END) AS top5_pair_vwap_cost,
              sum(CASE WHEN leg_role = 'first' THEN top5_worst_px_for_qty ELSE 0 END)
                + sum(CASE WHEN leg_role = 'completion' THEN top5_worst_px_for_qty ELSE 0 END) AS top5_pair_worst_cost,
              count(*) AS leg_count,
              sum(CASE WHEN l2_depth_present THEN 1 ELSE 0 END) AS l2_depth_present_legs,
              sum(CASE WHEN raw_l2_age_ok THEN 1 ELSE 0 END) AS raw_l2_age_ok_legs,
              sum(CASE WHEN top_price_matches_action THEN 1 ELSE 0 END) AS top_price_match_legs,
              sum(CASE WHEN top1_depth_ge_qty THEN 1 ELSE 0 END) AS top1_depth_ge_qty_legs,
              sum(CASE WHEN top5_depth_ge_qty THEN 1 ELSE 0 END) AS top5_depth_ge_qty_legs,
              max(raw_l2_age_ms) AS max_raw_l2_age_ms,
              max(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS any_top_overlay_required
            FROM leg_evidence
            GROUP BY candidate_id
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE action_evidence_final AS
            SELECT
              *,
              leg_count = 2 AS has_two_legs,
              l2_depth_present_legs = 2 AS l2_depth_present_pair,
              raw_l2_age_ok_legs = 2 AS raw_l2_age_ok_pair,
              top_price_match_legs = 2 AS top_price_match_pair,
              top1_depth_ge_qty_legs = 2 AS top1_depth_pair_fillable,
              top5_depth_ge_qty_legs = 2 AS top5_depth_pair_fillable,
              top5_pair_vwap_cost <= {float(pair_cap)} + 1e-12 AS top5_pair_vwap_le_pair_cap,
              top5_pair_worst_cost <= {float(pair_cap)} + 1e-12 AS top5_pair_worst_le_pair_cap,
              (
                leg_count = 2
                AND top_price_match_legs = 2
                AND top1_depth_ge_qty_legs = 2
                AND l1_pair_cost <= {float(pair_cap)} + 1e-12
              ) AS l1_top_pair_pass,
              (
                leg_count = 2
                AND l2_depth_present_legs = 2
                AND raw_l2_age_ok_legs = 2
                AND top_price_match_legs = 2
                AND top5_depth_ge_qty_legs = 2
                AND top5_pair_vwap_cost <= {float(pair_cap)} + 1e-12
              ) AS depth_assisted_pair_pass,
              (
                leg_count = 2
                AND top_price_match_legs = 2
                AND (
                  (
                    top1_depth_ge_qty_legs = 2
                    AND l1_pair_cost <= {float(pair_cap)} + 1e-12
                  )
                  OR (
                    l2_depth_present_legs = 2
                    AND raw_l2_age_ok_legs = 2
                    AND top5_depth_ge_qty_legs = 2
                    AND top5_pair_vwap_cost <= {float(pair_cap)} + 1e-12
                  )
                )
              ) AS l2_top_aligned_vwap_pass
            FROM action_evidence
            """
        )
        con.execute(
            f"""
            CREATE TEMP TABLE action_evidence_scored AS
            SELECT
              *,
              CASE
                WHEN l2_top_aligned_vwap_pass THEN 'PASS'
                WHEN NOT has_two_legs THEN 'MISSING_LEG'
                WHEN NOT top_price_match_pair THEN 'TOP_PRICE_MISMATCH'
                WHEN NOT top1_depth_pair_fillable AND NOT l2_depth_present_pair THEN 'TOP1_DEPTH_AND_L2_MISSING'
                WHEN NOT top1_depth_pair_fillable AND NOT raw_l2_age_ok_pair THEN 'TOP1_DEPTH_AND_RAW_L2_STALE'
                WHEN NOT top1_depth_pair_fillable AND NOT top5_depth_pair_fillable THEN 'TOP1_AND_TOP5_DEPTH_INSUFFICIENT'
                WHEN NOT top1_depth_pair_fillable AND top5_pair_vwap_cost > {float(pair_cap)} + 1e-12 THEN 'TOP1_DEPTH_TOP5_VWAP_GT_CAP'
                WHEN l1_pair_cost > {float(pair_cap)} + 1e-12
                  AND (top5_pair_vwap_cost IS NULL OR top5_pair_vwap_cost > {float(pair_cap)} + 1e-12)
                  THEN 'PAIR_COST_GT_CAP'
                WHEN top5_pair_vwap_cost > {float(pair_cap)} + 1e-12 THEN 'TOP5_VWAP_GT_CAP'
                ELSE 'OTHER_L2_GAP'
              END AS l2_top_aligned_fail_reason
            FROM action_evidence_final
            """
        )
        leg_rows = [dict(zip([d[0] for d in con.description], row)) for row in con.execute("SELECT * FROM leg_evidence ORDER BY candidate_id, leg_role").fetchall()]
        action_rows = [
            dict(zip([d[0] for d in con.description], row))
            for row in con.execute("SELECT * FROM action_evidence_scored ORDER BY candidate_id").fetchall()
        ]
        summary_row = con.execute(
            """
            SELECT
              count(*) AS action_count,
              count(DISTINCT condition_id) AS market_count,
              sum(CASE WHEN l2_depth_present_pair THEN 1 ELSE 0 END) AS l2_depth_present_pair_count,
              sum(CASE WHEN raw_l2_age_ok_pair THEN 1 ELSE 0 END) AS raw_l2_age_ok_pair_count,
              sum(CASE WHEN top_price_match_pair THEN 1 ELSE 0 END) AS top_price_match_pair_count,
              sum(CASE WHEN top1_depth_pair_fillable THEN 1 ELSE 0 END) AS top1_depth_pair_fillable_count,
              sum(CASE WHEN top5_depth_pair_fillable THEN 1 ELSE 0 END) AS top5_depth_pair_fillable_count,
              sum(CASE WHEN top5_pair_vwap_le_pair_cap THEN 1 ELSE 0 END) AS top5_pair_vwap_le_cap_count,
              sum(CASE WHEN top5_pair_worst_le_pair_cap THEN 1 ELSE 0 END) AS top5_pair_worst_le_cap_count,
              sum(CASE WHEN l1_top_pair_pass THEN 1 ELSE 0 END) AS l1_top_pair_pass_count,
              sum(CASE WHEN depth_assisted_pair_pass THEN 1 ELSE 0 END) AS depth_assisted_pair_pass_count,
              sum(CASE WHEN l2_top_aligned_vwap_pass THEN 1 ELSE 0 END) AS l2_top_aligned_vwap_pass_count,
              count(DISTINCT CASE WHEN l2_top_aligned_vwap_pass THEN condition_id END) AS l2_top_aligned_vwap_pass_market_count,
              sum(buy_actual_est) AS total_buy_actual_est,
              sum(cash_pnl_est) AS total_cash_pnl_est,
              sum(CASE WHEN l1_top_pair_pass THEN buy_actual_est ELSE 0 END) AS l1_top_pair_pass_buy_actual_est,
              sum(CASE WHEN l1_top_pair_pass THEN cash_pnl_est ELSE 0 END) AS l1_top_pair_pass_cash_pnl_est,
              sum(CASE WHEN depth_assisted_pair_pass THEN buy_actual_est ELSE 0 END) AS depth_assisted_pair_pass_buy_actual_est,
              sum(CASE WHEN depth_assisted_pair_pass THEN cash_pnl_est ELSE 0 END) AS depth_assisted_pair_pass_cash_pnl_est,
              sum(CASE WHEN l2_top_aligned_vwap_pass THEN buy_actual_est ELSE 0 END) AS l2_top_aligned_vwap_pass_buy_actual_est,
              sum(CASE WHEN l2_top_aligned_vwap_pass THEN cash_pnl_est ELSE 0 END) AS l2_top_aligned_vwap_pass_cash_pnl_est,
              max(max_raw_l2_age_ms) AS max_raw_l2_age_ms,
              quantile_cont(max_raw_l2_age_ms, 0.5) AS p50_raw_l2_age_ms,
              quantile_cont(max_raw_l2_age_ms, 0.95) AS p95_raw_l2_age_ms,
              quantile_cont(top5_pair_vwap_cost, 0.5) AS p50_top5_pair_vwap_cost,
              quantile_cont(top5_pair_vwap_cost, 0.95) AS p95_top5_pair_vwap_cost,
              max(top5_pair_vwap_cost) AS max_top5_pair_vwap_cost,
              quantile_cont(top5_pair_worst_cost, 0.5) AS p50_top5_pair_worst_cost,
              quantile_cont(top5_pair_worst_cost, 0.95) AS p95_top5_pair_worst_cost,
              max(top5_pair_worst_cost) AS max_top5_pair_worst_cost,
              sum(CASE WHEN any_top_overlay_required THEN 1 ELSE 0 END) AS top_overlay_required_action_count
            FROM action_evidence_scored
            """
        ).fetchone()
        summary_cols = [d[0] for d in con.description]
    finally:
        con.close()

    def ratio(num: Any, den: Any) -> float:
        return round(float(num or 0) / float(den or 1), 6) if float(den or 0) else 0.0

    summary = dict(zip(summary_cols, summary_row))
    action_count = int(summary.get("action_count") or 0)
    summary.update(
        {
            "actions_csv": str(actions_csv),
            "actions_csv_sha256": sha256_file(actions_csv),
            "result_manifest": result_manifest["_path"],
            "result_manifest_sha256": result_manifest["_sha256"],
            "variant_id": variant.get("variant_id"),
            "branch_id": variant.get("branch_id"),
            "fee_rate": result_manifest.get("fee_rate"),
            "pair_cap": pair_cap,
            "max_raw_l2_age_ms_threshold": int(args.max_raw_l2_age_ms),
            "price_epsilon": float(args.price_epsilon),
            "l2_depth_present_pair_rate": ratio(summary.get("l2_depth_present_pair_count"), action_count),
            "raw_l2_age_ok_pair_rate": ratio(summary.get("raw_l2_age_ok_pair_count"), action_count),
            "top_price_match_pair_rate": ratio(summary.get("top_price_match_pair_count"), action_count),
            "top1_depth_pair_fillable_rate": ratio(summary.get("top1_depth_pair_fillable_count"), action_count),
            "top5_depth_pair_fillable_rate": ratio(summary.get("top5_depth_pair_fillable_count"), action_count),
            "top5_pair_vwap_le_cap_rate": ratio(summary.get("top5_pair_vwap_le_cap_count"), action_count),
            "top5_pair_worst_le_cap_rate": ratio(summary.get("top5_pair_worst_le_cap_count"), action_count),
            "l1_top_pair_pass_rate": ratio(summary.get("l1_top_pair_pass_count"), action_count),
            "depth_assisted_pair_pass_rate": ratio(summary.get("depth_assisted_pair_pass_count"), action_count),
            "l2_top_aligned_vwap_pass_rate": ratio(summary.get("l2_top_aligned_vwap_pass_count"), action_count),
            "total_replay_roi_est": ratio(summary.get("total_cash_pnl_est"), summary.get("total_buy_actual_est")),
            "l1_top_pair_pass_roi_est": ratio(
                summary.get("l1_top_pair_pass_cash_pnl_est"), summary.get("l1_top_pair_pass_buy_actual_est")
            ),
            "depth_assisted_pair_pass_roi_est": ratio(
                summary.get("depth_assisted_pair_pass_cash_pnl_est"),
                summary.get("depth_assisted_pair_pass_buy_actual_est"),
            ),
            "l2_top_aligned_vwap_pass_roi_est": ratio(
                summary.get("l2_top_aligned_vwap_pass_cash_pnl_est"),
                summary.get("l2_top_aligned_vwap_pass_buy_actual_est"),
            ),
            "top_overlay_required_review_required": int(summary.get("top_overlay_required_action_count") or 0) > 0,
            "top_overlay_required_hard_limit": int(args.max_top_overlay_required_actions),
            "top_overlay_required_hard_limit_pass": (
                int(args.max_top_overlay_required_actions) < 0
                or int(summary.get("top_overlay_required_action_count") or 0) <= int(args.max_top_overlay_required_actions)
            ),
        }
    )
    clean = (
        action_count > 0
        and int(summary.get("l2_top_aligned_vwap_pass_count") or 0) == action_count
        and bool(summary.get("top_overlay_required_hard_limit_pass"))
    )
    status = "KEEP_L2_TOP_ALIGNED_ACTIONS_VALIDATED_REVIEW_REQUIRED" if clean else "BLOCKED_L2_TOP_ALIGNED_ACTION_VALIDATION_GAPS"
    summary["status"] = status
    summary["non_claims"] = json.dumps(NON_CLAIMS, sort_keys=True)

    leg_csv = output_dir / "ce25_l2_top_aligned_leg_evidence.csv"
    action_csv = output_dir / "ce25_l2_top_aligned_action_evidence.csv"
    summary_csv = output_dir / "ce25_l2_top_aligned_summary.csv"
    write_csv(leg_csv, leg_rows if args.write_leg_evidence else [])
    write_csv(action_csv, action_rows)
    write_csv(summary_csv, [summary])
    manifest = {
        "created_at": utc_now(),
        "dataset_type": "ce25_high_price_l2_top_aligned_action_validation_v0",
        "status": status,
        "inputs": {
            "actions_csv": str(actions_csv),
            "actions_csv_sha256": sha256_file(actions_csv),
            "result_manifest": result_manifest["_path"],
            "result_manifest_sha256": result_manifest["_sha256"],
            "l2_top_aligned_mart_manifest": str(l2_manifest_path),
            "l2_top_aligned_mart_manifest_sha256": sha256_file(l2_manifest_path),
            "l2_top_aligned_duckdb": str(l2_db),
            "candidate_base_duckdb": str(candidate_base_duckdb),
            "candidate_base_duckdb_sha256": sha256_file(candidate_base_duckdb),
        },
        "contract": {
            "l2_top_semantics": "md_book_l2_top_aligned_l1_canonical_top_raw_l2_depth_asof_v1",
            "raw_md_book_l2_direct_read_allowed": False,
            "pair_cap": pair_cap,
            "max_raw_l2_age_ms": int(args.max_raw_l2_age_ms),
            "price_epsilon": float(args.price_epsilon),
        },
        "summary": summary,
        "outputs": {
            "leg_evidence_csv": str(leg_csv),
            "action_evidence_csv": str(action_csv),
            "summary_csv": str(summary_csv),
        },
        "output_hashes": {
            "leg_evidence_csv": sha256_file(leg_csv),
            "action_evidence_csv": sha256_file(action_csv),
            "summary_csv": sha256_file(summary_csv),
        },
        "non_claims": NON_CLAIMS,
    }
    write_json(output_dir / "CE25_L2_TOP_ALIGNED_VALIDATION_MANIFEST.json", manifest)
    print(json.dumps({"output_dir": str(output_dir), "status": status, "summary": summary}, ensure_ascii=False, indent=2, sort_keys=True))
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actions-csv", type=Path, required=True)
    parser.add_argument("--result-manifest", type=Path)
    parser.add_argument("--l2-top-aligned-mart-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument("--candidate-base-duckdb", type=Path, default=DEFAULT_CANDIDATE_BASE_DUCKDB)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--pair-cap", type=float)
    parser.add_argument("--max-raw-l2-age-ms", type=int, default=750)
    parser.add_argument(
        "--max-top-overlay-required-actions",
        type=int,
        default=-1,
        help="Optional hard cap for top-overlay-required actions. Negative means review-note only.",
    )
    parser.add_argument("--price-epsilon", type=float, default=1e-6)
    parser.add_argument("--write-leg-evidence", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest = validate_actions(args)
    return 0 if str(manifest.get("status")).startswith("KEEP") else 1


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run candidate L2 evidence against the top-aligned L2 mart.

This is a data-interface gate, not the final xuan pair/rescue/residual PnL
engine. It deliberately consumes md_book_l2_top_aligned and refuses to fall
back to raw md_book_l2 side snapshots when legacy replay parity requires the
L1-top overlay model.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_EVENT_DB = (
    DEFAULT_DATA_ROOT
    / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/event_store.duckdb"
)
VALID_DAYS = tuple(
    [f"2026-05-{day:02d}" for day in range(2, 14)]
    + ["2026-05-16", "2026-05-17", "2026-05-18"]
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")
FORBIDDEN_RESULT_TOKENS = ("winner", "outcome", "settlement")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(payload: Any, n: int = 24) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> list[str]:
    fields = list(dict.fromkeys(field for row in rows for field in row.keys())) or ["candidate_key"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})
    return fields


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def sql_literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def forbidden_columns(fields: list[str]) -> list[str]:
    return [
        field
        for field in fields
        if any(token in field.lower() for token in FORBIDDEN_RESULT_TOKENS)
    ]


def top_aligned_assets_days(manifest: dict[str, Any]) -> tuple[list[str], list[str]]:
    by_asset_day = manifest.get("by_asset_day") or []
    assets = manifest.get("assets")
    if assets == "all" or not isinstance(assets, list):
        assets = sorted({str(row.get("asset") or "").upper() for row in by_asset_day if row.get("asset")})
    days = manifest.get("days")
    if not isinstance(days, list) or days == ["all"]:
        days = sorted({str(row.get("day") or "") for row in by_asset_day if row.get("day")})
    return sorted(set(assets or [])), sorted(set(days or []))


def resolve_top_aligned_manifest(args: argparse.Namespace, plan: dict[str, Any]) -> Path:
    if args.l2_top_aligned_mart_manifest is not None:
        return args.l2_top_aligned_mart_manifest.expanduser()
    from_plan = ((plan.get("l2_top_aligned_mart") or {}).get("manifest") or "").strip()
    if from_plan:
        return Path(from_plan).expanduser()
    return (
        args.data_root
        / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
    )


def blocked_row(job: dict[str, Any], status: str, reason: str, manifest_path: Path | None = None) -> dict[str, Any]:
    return {
        "job_id": job.get("upstream_validation_job_id") or job.get("job_id") or "",
        "candidate_key": job.get("candidate_key") or "",
        "asset": job.get("asset") or "",
        "shortlist_rank": job.get("shortlist_rank") or "",
        "l2_validation_status": status,
        "coverage_mode": "BLOCKED",
        "blocker": reason,
        "top_aligned_manifest": str(manifest_path or ""),
        "validated_days": "",
        "missing_valid_days_count": "",
        "event_count": "",
        "l2_matched_event_count": "",
        "l2_match_rate": "",
        "condition_count": "",
        "top_overlay_required_rows": "",
        "top_overlay_required_rate": "",
        "missing_depth_event_count": "",
        "max_raw_l2_age_ms": "",
        "p50_raw_l2_age_ms": "",
        "p95_raw_l2_age_ms": "",
        "l2_depth_clip_fillable_count": "",
        "l2_depth_clip_fillable_rate": "",
        "l2_depth_vwap_p50": "",
        "l2_depth_vwap_p90": "",
        "l2_depth_worst_px_p50": "",
        "l2_depth_worst_px_p90": "",
        "candidate_ask_window_rows": "",
        "candidate_any_top_window_rows": "",
        "top_source": "",
        "depth_source": "",
        "raw_md_book_l2_direct_read_allowed": False,
        "private_truth_ready": False,
        "source_event_hash": "",
        "l2_evidence_hash": "",
    }


def query_candidate(
    con: Any,
    args: argparse.Namespace,
    job: dict[str, Any],
    days: list[str],
    coverage_mode: str,
    manifest_path: Path,
    missing_valid_days: list[str],
) -> dict[str, Any]:
    params = job.get("candidate_params") or {}
    asset = str(job.get("asset") or "").upper()
    price_lo = to_float(params.get("price_lo"), 0.0)
    price_hi = to_float(params.get("price_hi"), 1.0)
    size_lo = to_float(params.get("size_lo"), 0.0)
    size_hi = to_float(params.get("size_hi"), 1e18)
    offset_lo = to_float(params.get("offset_lo"), -1e18)
    offset_hi = to_float(params.get("offset_hi"), 1e18)
    max_pair = to_float(params.get("max_l1_pair_ask"), 1e18)
    max_immediate = to_float(params.get("max_l1_immediate_pair"), 1e18)
    side_alignment = str(params.get("side_alignment") or "")
    placeholders = ",".join(["?"] * len(days))
    size_filter = "AND e.first_ask_sz >= ? AND e.opposite_ask_sz >= ?" if args.require_l1_size else ""
    sql = f"""
        WITH selected AS (
            SELECT *
            FROM eventdb.main.{args.event_table} AS e
            WHERE e.market_symbol = ?
              AND e.day IN ({placeholders})
              AND e.public_trade_price >= ? AND e.public_trade_price < ?
              AND e.public_trade_size >= ? AND e.public_trade_size <= ?
              AND e.offset_s >= ? AND e.offset_s <= ?
              AND e.l1_pair_ask <= ?
              AND e.l1_immediate_pair <= ?
              AND e.side_alignment = ?
              {size_filter}
        ),
        joined AS (
            SELECT
                e.day,
                e.condition_id,
                e.source_trade_row_id,
                e.trade_id,
                e.first_side,
                e.l1_source_row_id AS event_l1_source_row_id,
                m.l1_source_row_id AS mart_l1_source_row_id,
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
            FROM selected AS e
            LEFT JOIN martdb.main.md_book_l2_top_aligned AS m
              ON m.day = e.day
             AND m.asset = e.market_symbol
             AND m.condition_id = e.condition_id
             AND m.market_side = e.first_side
             AND m.l1_source_row_id = e.l1_source_row_id
        ),
        depth_calc AS (
            SELECT
                *,
                coalesce(ask1_sz, 0.0) AS sz1,
                coalesce(raw_l2_ask2_sz, 0.0) AS sz2,
                coalesce(raw_l2_ask3_sz, 0.0) AS sz3,
                coalesce(raw_l2_ask4_sz, 0.0) AS sz4,
                coalesce(raw_l2_ask5_sz, 0.0) AS sz5,
                coalesce(ask1_sz, 0.0)
                  + coalesce(raw_l2_ask2_sz, 0.0)
                  + coalesce(raw_l2_ask3_sz, 0.0)
                  + coalesce(raw_l2_ask4_sz, 0.0)
                  + coalesce(raw_l2_ask5_sz, 0.0) AS total_ask_sz
            FROM joined
        ),
        fills AS (
            SELECT
                *,
                least(?::DOUBLE, sz1) AS take1,
                least(greatest(?::DOUBLE - sz1, 0.0), sz2) AS take2,
                least(greatest(?::DOUBLE - sz1 - sz2, 0.0), sz3) AS take3,
                least(greatest(?::DOUBLE - sz1 - sz2 - sz3, 0.0), sz4) AS take4,
                least(greatest(?::DOUBLE - sz1 - sz2 - sz3 - sz4, 0.0), sz5) AS take5
            FROM depth_calc
        ),
        evidence AS (
            SELECT
                *,
                CASE WHEN total_ask_sz >= ?::DOUBLE THEN
                    (
                        take1 * coalesce(ask1_px, 0.0)
                        + take2 * coalesce(raw_l2_ask2_px, 0.0)
                        + take3 * coalesce(raw_l2_ask3_px, 0.0)
                        + take4 * coalesce(raw_l2_ask4_px, 0.0)
                        + take5 * coalesce(raw_l2_ask5_px, 0.0)
                    ) / ?::DOUBLE
                END AS l2_depth_vwap,
                CASE
                  WHEN total_ask_sz < ?::DOUBLE THEN NULL
                  WHEN ?::DOUBLE <= sz1 THEN ask1_px
                  WHEN ?::DOUBLE <= sz1 + sz2 THEN raw_l2_ask2_px
                  WHEN ?::DOUBLE <= sz1 + sz2 + sz3 THEN raw_l2_ask3_px
                  WHEN ?::DOUBLE <= sz1 + sz2 + sz3 + sz4 THEN raw_l2_ask4_px
                  ELSE raw_l2_ask5_px
                END AS l2_depth_worst_px
            FROM fills
        )
        SELECT
            count(*) AS event_count,
            sum(CASE WHEN mart_l1_source_row_id IS NOT NULL THEN 1 ELSE 0 END) AS l2_matched_event_count,
            count(DISTINCT condition_id) AS condition_count,
            sum(CASE WHEN raw_l2_source_row_id IS NULL THEN 1 ELSE 0 END) AS missing_depth_rows,
            sum(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS top_overlay_required_rows,
            max(raw_l2_age_ms) AS max_raw_l2_age_ms,
            sum(CASE WHEN ask1_px BETWEEN ? AND ? THEN 1 ELSE 0 END) AS candidate_ask_window_rows,
            sum(CASE WHEN ask1_px BETWEEN ? AND ? THEN 1 ELSE 0 END) AS candidate_any_top_window_rows,
            sum(CASE WHEN total_ask_sz >= ?::DOUBLE THEN 1 ELSE 0 END) AS l2_depth_clip_fillable_count,
            quantile_cont(raw_l2_age_ms, 0.50) AS p50_raw_l2_age_ms,
            quantile_cont(raw_l2_age_ms, 0.95) AS p95_raw_l2_age_ms,
            quantile_cont(l2_depth_vwap, 0.50) AS l2_depth_vwap_p50,
            quantile_cont(l2_depth_vwap, 0.90) AS l2_depth_vwap_p90,
            quantile_cont(l2_depth_worst_px, 0.50) AS l2_depth_worst_px_p50,
            quantile_cont(l2_depth_worst_px, 0.90) AS l2_depth_worst_px_p90,
            min(top_source) AS top_source,
            min(depth_source) AS depth_source
        FROM evidence
    """
    values: list[Any] = [
        asset,
        *days,
        price_lo,
        price_hi,
        size_lo,
        size_hi,
        offset_lo,
        offset_hi,
        max_pair,
        max_immediate,
        side_alignment,
    ]
    if args.require_l1_size:
        values.extend([args.clip, args.clip])
    values.extend(
        [
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            args.clip,
            price_lo,
            price_hi,
            price_lo,
            price_hi,
            args.clip,
        ]
    )
    row = con.execute(sql, values).fetchone()
    event_count = int(row[0] or 0)
    l2_matched_event_count = int(row[1] or 0)
    missing_depth_rows = int(row[3] or 0)
    top_overlay_required_rows = int(row[4] or 0)
    l2_depth_clip_fillable_count = int(row[8] or 0)
    if event_count == 0:
        status = "BLOCKED_L2_TOP_ALIGNED_NO_MATCHING_EVENTS"
        blocker = "candidate_params_matched_zero_events"
    elif l2_matched_event_count != event_count:
        status = "BLOCKED_L2_TOP_ALIGNED_EVENT_JOIN_GAP"
        blocker = f"matched={l2_matched_event_count}/events={event_count}"
    else:
        status = (
            "L2_TOP_ALIGNED_CANDIDATE_EVIDENCE_READY"
            if coverage_mode == "FULL"
            else "PARTIAL_L2_TOP_ALIGNED_CANDIDATE_EVIDENCE_READY"
        )
        blocker = ""
    result = {
        "job_id": job.get("upstream_validation_job_id") or job.get("job_id") or "",
        "candidate_key": job.get("candidate_key") or "",
        "asset": asset,
        "shortlist_rank": job.get("shortlist_rank") or "",
        "l2_validation_status": status,
        "coverage_mode": coverage_mode,
        "blocker": blocker,
        "top_aligned_manifest": str(manifest_path),
        "validated_days": ",".join(days),
        "missing_valid_days_count": len(missing_valid_days),
        "missing_valid_days": ",".join(missing_valid_days),
        "event_count": event_count,
        "event_rows": event_count,
        "l2_matched_event_count": l2_matched_event_count,
        "l2_match_rate": round(l2_matched_event_count / event_count, 6) if event_count else 0.0,
        "condition_count": int(row[2] or 0),
        "top_overlay_required_rows": top_overlay_required_rows,
        "top_overlay_required_rate": round(top_overlay_required_rows / event_count, 6) if event_count else 0.0,
        "missing_depth_event_count": missing_depth_rows,
        "missing_depth_rows": missing_depth_rows,
        "max_raw_l2_age_ms": int(row[5] or 0),
        "p50_raw_l2_age_ms": round(float(row[9] or 0), 6),
        "p95_raw_l2_age_ms": round(float(row[10] or 0), 6),
        "l2_depth_clip_fillable_count": l2_depth_clip_fillable_count,
        "l2_depth_clip_fillable_rate": round(l2_depth_clip_fillable_count / event_count, 6) if event_count else 0.0,
        "l2_depth_vwap_p50": round(float(row[11] or 0), 6),
        "l2_depth_vwap_p90": round(float(row[12] or 0), 6),
        "l2_depth_worst_px_p50": round(float(row[13] or 0), 6),
        "l2_depth_worst_px_p90": round(float(row[14] or 0), 6),
        "candidate_ask_window_rows": int(row[6] or 0),
        "candidate_any_top_window_rows": int(row[7] or 0),
        "candidate_price_lo": price_lo,
        "candidate_price_hi": price_hi,
        "clip": args.clip,
        "top_source": row[15] or "",
        "depth_source": row[16] or "",
        "l2_top_semantics": job.get("l2_top_semantics") or "",
        "raw_md_book_l2_direct_read_allowed": False,
        "private_truth_ready": False,
    }
    result["source_event_hash"] = stable_hash(
        {
            "candidate_key": result["candidate_key"],
            "asset": asset,
            "days": days,
            "event_count": event_count,
            "condition_count": result["condition_count"],
            "candidate_params": params,
        }
    )
    result["l2_evidence_hash"] = stable_hash(result)
    return result


def add_result_duckdb(db_path: Path, csv_path: Path) -> tuple[list[str], list[str]]:
    import duckdb  # type: ignore

    con = duckdb.connect(str(db_path))
    try:
        con.execute("CREATE OR REPLACE TABLE l2_validation_results AS SELECT * FROM read_csv_auto(?)", [str(csv_path)])
        con.execute(
            """
            CREATE OR REPLACE VIEW l2_top_aligned_ready AS
            SELECT *
            FROM l2_validation_results
            WHERE l2_validation_status LIKE '%READY%'
            """
        )
        relations = con.execute(
            "select table_name, table_type from information_schema.tables where table_schema='main'"
        ).fetchall()
    finally:
        con.close()
    tables = sorted(row[0] for row in relations if row[1] == "BASE TABLE")
    views = sorted(row[0] for row in relations if row[1] == "VIEW")
    return tables, views


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb  # type: ignore

    args.output_dir.mkdir(parents=True, exist_ok=True)
    plan = read_json(args.l2_plan_manifest)
    manifest_path = resolve_top_aligned_manifest(args, plan)
    mart = read_json(manifest_path)
    jobs = read_jsonl(args.queue_jsonl)
    rows: list[dict[str, Any]] = []

    if not jobs:
        rows = []
    elif not args.event_store_db.exists():
        rows = [blocked_row(job, "BLOCKED_EVENT_STORE_MISSING", "event_store_db_missing", manifest_path) for job in jobs]
    elif not mart:
        rows = [
            blocked_row(job, "BLOCKED_L2_TOP_ALIGNED_MART_MISSING", "top_aligned_mart_manifest_missing", manifest_path)
            for job in jobs
        ]
    elif mart.get("status") != "OK":
        rows = [
            blocked_row(job, "BLOCKED_L2_TOP_ALIGNED_MART_NOT_OK", f"top_aligned_mart_status={mart.get('status')}", manifest_path)
            for job in jobs
        ]
    else:
        assets, days = top_aligned_assets_days(mart)
        available_assets = set(assets)
        available_days = set(days)
        db_path = Path(str(mart.get("output_duckdb") or "")).expanduser()
        if not db_path.exists():
            rows = [
                blocked_row(job, "BLOCKED_L2_TOP_ALIGNED_DUCKDB_MISSING", "top_aligned_duckdb_missing", manifest_path)
                for job in jobs
            ]
        else:
            con = duckdb.connect(":memory:")
            try:
                con.execute(f"ATTACH {sql_literal(args.event_store_db)} AS eventdb (READ_ONLY)")
                con.execute(f"ATTACH {sql_literal(db_path)} AS martdb (READ_ONLY)")
                for job in jobs:
                    asset = str(job.get("asset") or "").upper()
                    requested_days = [str(day) for day in (job.get("valid_days") or VALID_DAYS)]
                    requested_valid_days = [day for day in requested_days if day not in set(BLOCKLISTED_DAYS)]
                    if asset not in available_assets:
                        rows.append(blocked_row(job, "BLOCKED_L2_TOP_ALIGNED_ASSET_MISSING", f"asset_missing={asset}", manifest_path))
                        continue
                    usable_days = [day for day in requested_valid_days if day in available_days]
                    missing_days = [day for day in requested_valid_days if day not in available_days]
                    if missing_days and not args.allow_partial_days:
                        rows.append(
                            blocked_row(
                                job,
                                "BLOCKED_L2_TOP_ALIGNED_MART_INCOMPLETE",
                                f"missing_valid_days={len(missing_days)}",
                                manifest_path,
                            )
                        )
                        continue
                    if not usable_days:
                        rows.append(blocked_row(job, "BLOCKED_L2_TOP_ALIGNED_NO_OVERLAP_DAYS", "no_candidate_days_in_mart", manifest_path))
                        continue
                    rows.append(query_candidate(con, args, job, usable_days, "PARTIAL" if missing_days else "FULL", manifest_path, missing_days))
            finally:
                con.close()

    csv_path = args.output_dir / "backtest_l2_validation_results.csv"
    jsonl_path = args.output_dir / "backtest_l2_validation_results.jsonl"
    result_db = args.output_dir / "backtest_l2_validation_results.duckdb"
    fields = write_csv(csv_path, rows)
    write_jsonl(jsonl_path, rows)
    tables, views = add_result_duckdb(result_db, csv_path) if rows else ([], [])
    status_counts = {
        status: sum(1 for row in rows if row.get("l2_validation_status") == status)
        for status in sorted({str(row.get("l2_validation_status") or "") for row in rows})
    }
    blocklisted_proof = {
        "blocklisted_days": list(BLOCKLISTED_DAYS),
        "input_jobs_include_blocklisted_days": sum(
            1
            for job in jobs
            if set(str(day) for day in (job.get("valid_days") or [])) & set(BLOCKLISTED_DAYS)
        ),
        "result_validated_day_mentions": {
            day: sum(1 for row in rows if day in str(row.get("validated_days") or ""))
            for day in BLOCKLISTED_DAYS
        },
    }
    manifest = {
        "schema_version": "backtest_l2_top_aligned_validation_results_v1",
        "created_utc": utc_now(),
        "status": "OK" if rows and all("READY" in str(row.get("l2_validation_status") or "") for row in rows) else "BLOCKED",
        "coverage_modes": sorted({str(row.get("coverage_mode") or "") for row in rows}),
        "job_count": len(jobs),
        "result_count": len(rows),
        "status_counts": status_counts,
        "l2_plan_manifest": str(args.l2_plan_manifest),
        "l2_plan_manifest_sha256": sha256_file(args.l2_plan_manifest),
        "queue_jsonl": str(args.queue_jsonl),
        "queue_jsonl_sha256": sha256_file(args.queue_jsonl),
        "event_store_db": str(args.event_store_db),
        "event_store_db_sha256": sha256_file(args.event_store_db),
        "event_table": args.event_table,
        "top_aligned_mart_manifest": str(manifest_path),
        "top_aligned_mart_manifest_sha256": sha256_file(manifest_path),
        "raw_md_book_l2_direct_read_allowed": False,
        "allow_partial_days": args.allow_partial_days,
        "clip": args.clip,
        "require_l1_size": args.require_l1_size,
        "valid_days": list(VALID_DAYS),
        "blocklisted_day_zero_row_proof": blocklisted_proof,
        "forbidden_result_columns": forbidden_columns(fields),
        "outputs": {
            "csv": str(csv_path),
            "jsonl": str(jsonl_path),
            "duckdb": str(result_db),
        },
        "output_hashes": {
            "csv": sha256_file(csv_path),
            "jsonl": sha256_file(jsonl_path),
            "duckdb": sha256_file(result_db),
        },
        "duckdb_tables": tables,
        "duckdb_views": views,
    }
    manifest_path_out = args.output_dir / "BACKTEST_L2_VALIDATION_RESULTS_MANIFEST.json"
    manifest_path_out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--event-store-db", type=Path, default=DEFAULT_EVENT_DB)
    parser.add_argument("--event-table", default="l1_taker_buy_events_search_safe")
    parser.add_argument(
        "--l2-plan-manifest",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/backtest_l2_validation_plan_latest/BACKTEST_L2_VALIDATION_PLAN_MANIFEST.json",
    )
    parser.add_argument(
        "--queue-jsonl",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/backtest_l2_validation_plan_latest/backtest_l2_validation_queue.jsonl",
    )
    parser.add_argument("--l2-top-aligned-mart-manifest", type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/backtest_l2_validation_results_latest",
    )
    parser.add_argument("--clip", type=float, default=5.0)
    parser.add_argument("--no-require-l1-size", dest="require_l1_size", action="store_false")
    parser.set_defaults(require_l1_size=True)
    parser.add_argument("--allow-partial-days", action="store_true")
    args = parser.parse_args()
    args.data_root = args.data_root.expanduser()
    args.event_store_db = args.event_store_db.expanduser()
    args.l2_plan_manifest = args.l2_plan_manifest.expanduser()
    args.queue_jsonl = args.queue_jsonl.expanduser()
    args.output_dir = args.output_dir.expanduser()

    manifest = build_report(args)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in ("status", "job_count", "result_count", "status_counts", "coverage_modes", "outputs")
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if manifest["status"] == "OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())

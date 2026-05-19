#!/usr/bin/env python3
"""Materialize a small candidate base from local completion unwind V2 stores."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


DATASET_TYPE = "completion_unwind_event_store_v2_candidate_base"
DEFAULT_BLOCKLIST = {"20260514", "20260515", "20260519", "2026-05-14", "2026-05-15", "2026-05-19"}
REQUIRED_SOURCE_TABLE = "completion_unwind_events"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def label_days(label: str) -> list[str]:
    if "_" in label:
        start, end = label.split("_", 1)
        start_dt = dt.datetime.strptime(start, "%Y%m%d").date()
        end_dt = dt.datetime.strptime(end, "%Y%m%d").date()
        days = []
        cur = start_dt
        while cur <= end_dt:
            days.append(cur.isoformat())
            cur += dt.timedelta(days=1)
        return days
    return [dt.datetime.strptime(label, "%Y%m%d").date().isoformat()]


def compact_day(day: str) -> str:
    return day.replace("-", "")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def safe_run_name(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_.=-]+", "_", value.strip())
    return out.strip("_") or "run"


def discover_labels(store_root: Path, requested: list[str], blocklist: set[str]) -> list[str]:
    labels = requested or sorted(
        p.name for p in store_root.iterdir() if p.is_dir() and (p / "EVENT_STORE_MANIFEST.json").is_file()
    )
    out = []
    for label in labels:
        days = label_days(label)
        blocked = label in blocklist or any(day in blocklist or compact_day(day) in blocklist for day in days)
        if blocked:
            continue
        out.append(label)
    return out


def source_db_path(store_root: Path, label: str) -> Path:
    manifest_path = store_root / label / "EVENT_STORE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing completion store manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    db_name = manifest.get("outputs", {}).get("duckdb", "event_store.duckdb")
    db_path = store_root / label / str(db_name)
    if not db_path.is_file():
        raise FileNotFoundError(f"missing completion store duckdb: {db_path}")
    return db_path


def schema_rows(conn: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    rows = conn.execute(f"DESCRIBE {quote_ident(table)}").fetchall()
    return [{"name": row[0], "type": row[1]} for row in rows]


def output_root(args: argparse.Namespace) -> Path:
    if args.output_dir:
        return Path(args.output_dir).expanduser().resolve()
    data_root = Path(args.data_root).expanduser().resolve()
    return data_root / "derived" / "completion_candidate_pipeline_v1"


def build_where_clause(args: argparse.Namespace) -> str:
    prefix_checks = " OR ".join(f"slug LIKE {quote_literal(prefix + '%')}" for prefix in args.market_prefix)
    if not prefix_checks:
        raise ValueError("at least one --market-prefix is required")
    reasons = []
    if args.include_public_sell:
        reasons.append("public_trade_taker_side = 'SELL'")
    if args.include_bid_drop:
        reasons.append(f"COALESCE(side_bid_level_drop_qty, 0) > {float(args.min_bid_drop_qty)}")
    if args.include_ask_lift:
        reasons.append(f"COALESCE(side_ask_level_lift_qty, 0) > {float(args.min_ask_lift_qty)}")
    if not reasons:
        raise ValueError("at least one candidate reason must be enabled")
    reason_sql = " OR ".join(f"({reason})" for reason in reasons)
    return f"""
      ({prefix_checks})
      AND day NOT IN (SELECT UNNEST(?))
      AND offset_s >= {float(args.offset_min_s)}
      AND offset_s <= {float(args.offset_max_s)}
      AND l1_pair_ask IS NOT NULL
      AND l1_pair_ask <= {float(args.max_pair_cost)}
      AND ({reason_sql})
    """


def candidate_select_sql(source_ref: str, args: argparse.Namespace, label: str) -> str:
    where_sql = build_where_clause(args)
    return f"""
    SELECT
      {quote_literal(DATASET_TYPE)} AS dataset_type,
      {quote_literal(label)} AS source_label,
      'BTC' AS asset,
      event_kind,
      event_id,
      day,
      condition_id,
      slug,
      ts_ms,
      ts_iso,
      offset_s,
      side,
      opposite_side,
      winner_side,
      side_is_winner,
      side_alignment,
      high_side,
      strict_l1_row_id,
      strict_l1_recv_ms,
      strict_l1_age_ms,
      strict_l2_row_id,
      strict_l2_recv_ms,
      strict_l2_age_ms,
      side_bid,
      side_ask,
      side_bid_sz,
      side_ask_sz,
      opp_bid,
      opp_ask,
      opp_bid_sz,
      opp_ask_sz,
      l1_pair_ask,
      l1_pair_bid,
      buy_full_10,
      buy_vwap_10,
      buy_filled_10,
      buy_full_25,
      buy_vwap_25,
      buy_filled_25,
      buy_full_60,
      buy_vwap_60,
      buy_filled_60,
      buy_best_px,
      buy_best_sz,
      buy_available_qty,
      sell_best_px,
      sell_best_sz,
      sell_available_qty,
      side_bid_level_drop_qty,
      side_ask_level_lift_qty,
      side_bid_delta_qty,
      side_ask_delta_qty,
      book_update_reason,
      public_trade_row_id,
      public_trade_taker_side,
      public_trade_price,
      public_trade_size,
      public_trade_recv_ms,
      CASE
        WHEN public_trade_taker_side = 'SELL' THEN 'public_sell'
        WHEN COALESCE(side_bid_level_drop_qty, 0) > {float(args.min_bid_drop_qty)} THEN 'bid_level_drop'
        WHEN COALESCE(side_ask_level_lift_qty, 0) > {float(args.min_ask_lift_qty)} THEN 'ask_level_lift'
        ELSE 'other'
      END AS candidate_reason,
      LEAST(COALESCE(side_ask_sz, 0), COALESCE(opp_ask_sz, 0)) AS l1_pair_available_qty
    FROM {source_ref}.{quote_ident(REQUIRED_SOURCE_TABLE)}
    WHERE {where_sql}
    """


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default=os.environ.get("POLY_BT_ROOT", "/Users/hot/web3Scientist/poly_backtest_data"))
    parser.add_argument("--store-root", default=None)
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--exclude-label", action="append", default=[])
    parser.add_argument("--market-prefix", action="append", default=["btc-updown-5m-"])
    parser.add_argument("--offset-min-s", type=float, default=0.0)
    parser.add_argument("--offset-max-s", type=float, default=300.0)
    parser.add_argument("--max-pair-cost", type=float, default=1.01)
    parser.add_argument("--min-bid-drop-qty", type=float, default=0.0)
    parser.add_argument("--min-ask-lift-qty", type=float, default=0.0)
    parser.add_argument("--include-public-sell", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-bid-drop", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--include-ask-lift", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--run-name", default=None)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--duckdb-threads", type=int, default=4)
    args = parser.parse_args()

    started = time.perf_counter()
    data_root = Path(args.data_root).expanduser().resolve()
    store_root = (
        Path(args.store_root).expanduser().resolve()
        if args.store_root
        else data_root / "verification_store" / "completion_unwind_event_store_v2"
    )
    blocklist = set(DEFAULT_BLOCKLIST) | set(args.exclude_label)
    labels = discover_labels(store_root, args.label, blocklist)
    if not labels:
        raise SystemExit(f"no usable completion_unwind_event_store_v2 labels found under {store_root}")
    run_name = safe_run_name(args.run_name or f"candidate_base_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
    out_dir = output_root(args) / run_name
    if out_dir.exists():
        if not args.force:
            raise FileExistsError(f"output exists; pass --force to replace: {out_dir}")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    db_path = out_dir / "candidate_base.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"PRAGMA threads={max(1, int(args.duckdb_threads))}")

    blocked_days = sorted({x for x in blocklist if re.fullmatch(r"\\d{4}-\\d{2}-\\d{2}", x)})
    label_stats: list[dict[str, Any]] = []
    inserted_any = False
    for idx, label in enumerate(labels):
        db = source_db_path(store_root, label)
        alias = f"src_{idx}"
        conn.execute(f"ATTACH {quote_literal(db)} AS {quote_ident(alias)} (READ_ONLY)")
        full_count = int(conn.execute(f"SELECT COUNT(*) FROM {quote_ident(alias)}.{quote_ident(REQUIRED_SOURCE_TABLE)}").fetchone()[0])
        select_sql = candidate_select_sql(quote_ident(alias), args, label)
        if not inserted_any:
            conn.execute(f"CREATE TABLE candidate_base_stage AS {select_sql} LIMIT 0", [blocked_days])
            inserted_any = True
        before = int(conn.execute("SELECT COUNT(*) FROM candidate_base_stage").fetchone()[0])
        conn.execute(f"INSERT INTO candidate_base_stage {select_sql}", [blocked_days])
        after = int(conn.execute("SELECT COUNT(*) FROM candidate_base_stage").fetchone()[0])
        label_stats.append(
            {
                "label": label,
                "days": label_days(label),
                "source_db": str(db),
                "source_row_count": full_count,
                "candidate_row_count": after - before,
            }
        )
        conn.execute(f"DETACH {quote_ident(alias)}")

    conn.execute(
        """
        CREATE TABLE candidate_base AS
        SELECT
          row_number() OVER (
            ORDER BY source_label, day, condition_id, ts_ms, event_kind, COALESCE(event_id, -1), side
          ) AS candidate_row_id,
          *
        FROM candidate_base_stage
        """
    )
    conn.execute("DROP TABLE candidate_base_stage")
    row_count = int(conn.execute("SELECT COUNT(*) FROM candidate_base").fetchone()[0])
    source_row_count = sum(int(item["source_row_count"]) for item in label_stats)
    days = [row[0] for row in conn.execute("SELECT DISTINCT day FROM candidate_base ORDER BY day").fetchall()]
    schema = schema_rows(conn, "candidate_base")
    reason_counts = dict(conn.execute("SELECT candidate_reason, COUNT(*) FROM candidate_base GROUP BY 1 ORDER BY 1").fetchall())
    day_counts = dict(conn.execute("SELECT day, COUNT(*) FROM candidate_base GROUP BY 1 ORDER BY 1").fetchall())
    parquet_path = out_dir / "candidate_base.parquet"
    conn.execute(f"COPY candidate_base TO {quote_literal(parquet_path)} (FORMAT PARQUET, COMPRESSION ZSTD)")
    conn.close()

    elapsed = time.perf_counter() - started
    manifest = {
        "created_at": utc_now(),
        "dataset_type": DATASET_TYPE,
        "data_root": str(data_root),
        "source_dataset_type": "completion_unwind_event_store_v2",
        "raw_scanned": False,
        "replay_scanned": False,
        "collector_scanned": False,
        "public_account_execution_truth_v1_included": False,
        "labels": labels,
        "days": days,
        "excluded_labels_or_days": sorted(blocklist),
        "market_prefix": args.market_prefix,
        "assets": ["BTC"],
        "filters": {
            "offset_min_s": args.offset_min_s,
            "offset_max_s": args.offset_max_s,
            "max_pair_cost": args.max_pair_cost,
            "include_public_sell": args.include_public_sell,
            "include_bid_drop": args.include_bid_drop,
            "include_ask_lift": args.include_ask_lift,
            "min_bid_drop_qty": args.min_bid_drop_qty,
            "min_ask_lift_qty": args.min_ask_lift_qty,
        },
        "row_count": row_count,
        "source_row_count": source_row_count,
        "candidate_to_source_row_ratio": round(row_count / source_row_count, 6) if source_row_count else None,
        "label_stats": label_stats,
        "day_counts": day_counts,
        "candidate_reason_counts": reason_counts,
        "schema": schema,
        "outputs": {
            "duckdb": "candidate_base.duckdb",
            "duckdb_table": "candidate_base",
            "parquet": "candidate_base.parquet",
        },
        "elapsed_s": round(elapsed, 3),
    }
    write_json(out_dir / "CANDIDATE_BASE_MANIFEST.json", manifest)
    print(json.dumps({"candidate_base_dir": str(out_dir), "row_count": row_count, "days": days}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

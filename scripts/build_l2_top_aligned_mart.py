#!/usr/bin/env python3
"""Build a top-aligned L2 mart for legacy replay archives.

Legacy replay archives may contain md_book_l1 rows produced from price-change
events where top-of-book changed but the embedded raw_l2 depth snapshot did not
refresh.  This mart keeps md_book_l1 as canonical top-of-book and joins the
latest available md_book_l2 side snapshot for depth provenance.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_L2_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/smoke_20260517_l2/REPLAY_STORE_V2_MANIFEST.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def parse_csv_arg(value: str) -> list[str]:
    if value.lower() == "all":
        return []
    return [part.strip().upper() for part in value.split(",") if part.strip()]


def build_mart(args: argparse.Namespace) -> dict[str, Any]:
    import duckdb  # type: ignore

    manifest = read_json(args.l2_manifest)
    l2_db = args.l2_manifest.parent / str((manifest.get("outputs") or {}).get("duckdb") or "store.duckdb")
    if not args.l2_manifest.exists():
        raise SystemExit(f"missing l2 manifest: {args.l2_manifest}")
    if not l2_db.exists():
        raise SystemExit(f"missing l2 duckdb: {l2_db}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_db = args.output_dir / "l2_top_aligned_mart.duckdb"
    assets = parse_csv_arg(args.assets)
    days = [] if args.days.lower() == "all" else [part.strip() for part in args.days.split(",") if part.strip()]
    asset_filter = "TRUE" if not assets else "m.symbol IN (" + ",".join(quote_literal(a) for a in assets) + ")"
    day_filter = "TRUE" if not days else "b.day IN (" + ",".join(quote_literal(d) for d in days) + ")"
    limit_clause = ""
    if args.limit_per_asset:
        limit_clause = f"WHERE rn <= {int(args.limit_per_asset)}"

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
        if args.duckdb_temp_dir:
            args.duckdb_temp_dir.mkdir(parents=True, exist_ok=True)
            con.execute(f"PRAGMA temp_directory={quote_literal(args.duckdb_temp_dir)}")
        con.execute(f"ATTACH {quote_literal(l2_db)} AS src (READ_ONLY)")
        con.execute("DROP TABLE IF EXISTS md_book_l2_top_aligned")
        con.execute(
            f"""
            CREATE TABLE md_book_l2_top_aligned AS
            WITH l1_base AS (
              SELECT
                b.day,
                m.symbol AS asset,
                b.condition_id,
                b.source_row_id AS l1_source_row_id,
                b.recv_ms,
                b.recv_monotonic_ns,
                b.capture_seq,
                b.source_ts_ms,
                b.source_kind,
                b.yes_bid_px,
                b.yes_bid_sz,
                b.yes_ask_px,
                b.yes_ask_sz,
                b.no_bid_px,
                b.no_bid_sz,
                b.no_ask_px,
                b.no_ask_sz,
                row_number() OVER (
                  PARTITION BY m.symbol
                  ORDER BY b.day, b.condition_id, b.recv_ms, b.source_row_id
                ) AS rn
              FROM src.main.md_book_l1 b
              JOIN src.main.market_meta m
                ON b.day = m.day
               AND b.condition_id = m.condition_id
              WHERE {asset_filter}
                AND {day_filter}
            ),
            l1_side AS (
              SELECT
                day, asset, condition_id, l1_source_row_id, recv_ms, recv_monotonic_ns,
                capture_seq, source_ts_ms, source_kind, 'YES' AS market_side,
                yes_bid_px AS bid1_px, yes_bid_sz AS bid1_sz,
                yes_ask_px AS ask1_px, yes_ask_sz AS ask1_sz
              FROM l1_base
              {limit_clause}
              UNION ALL
              SELECT
                day, asset, condition_id, l1_source_row_id, recv_ms, recv_monotonic_ns,
                capture_seq, source_ts_ms, source_kind, 'NO' AS market_side,
                no_bid_px AS bid1_px, no_bid_sz AS bid1_sz,
                no_ask_px AS ask1_px, no_ask_sz AS ask1_sz
              FROM l1_base
              {limit_clause}
            )
            SELECT
              s.day,
              s.asset,
              s.condition_id,
              s.market_side,
              s.l1_source_row_id,
              s.recv_ms,
              s.recv_monotonic_ns,
              s.capture_seq,
              s.source_ts_ms,
              s.source_kind,
              s.bid1_px,
              s.bid1_sz,
              l.bid1_px AS raw_l2_bid1_px,
              l.bid1_sz AS raw_l2_bid1_sz,
              l.bid2_px AS raw_l2_bid2_px,
              l.bid2_sz AS raw_l2_bid2_sz,
              l.bid3_px AS raw_l2_bid3_px,
              l.bid3_sz AS raw_l2_bid3_sz,
              l.bid4_px AS raw_l2_bid4_px,
              l.bid4_sz AS raw_l2_bid4_sz,
              l.bid5_px AS raw_l2_bid5_px,
              l.bid5_sz AS raw_l2_bid5_sz,
              s.ask1_px,
              s.ask1_sz,
              l.ask1_px AS raw_l2_ask1_px,
              l.ask1_sz AS raw_l2_ask1_sz,
              l.ask2_px AS raw_l2_ask2_px,
              l.ask2_sz AS raw_l2_ask2_sz,
              l.ask3_px AS raw_l2_ask3_px,
              l.ask3_sz AS raw_l2_ask3_sz,
              l.ask4_px AS raw_l2_ask4_px,
              l.ask4_sz AS raw_l2_ask4_sz,
              l.ask5_px AS raw_l2_ask5_px,
              l.ask5_sz AS raw_l2_ask5_sz,
              l.source_row_id AS raw_l2_source_row_id,
              l.recv_ms AS raw_l2_recv_ms,
              l.capture_seq AS raw_l2_capture_seq,
              s.recv_ms - l.recv_ms AS raw_l2_age_ms,
              (
                l.source_row_id IS NULL OR
                abs(coalesce(s.bid1_px, -999) - coalesce(l.bid1_px, -999)) > 1e-9 OR
                abs(coalesce(s.ask1_px, -999) - coalesce(l.ask1_px, -999)) > 1e-9
              ) AS top_overlay_required,
              'md_book_l1' AS top_source,
              'md_book_l2_latest_asof' AS depth_source,
              'top level is canonical md_book_l1; raw_l2_* columns preserve latest depth-side provenance' AS alignment_note
            FROM l1_side s ASOF LEFT JOIN src.main.md_book_l2 l
              ON s.day = l.day
             AND s.condition_id = l.condition_id
             AND s.market_side = l.market_side
             AND s.capture_seq >= l.capture_seq
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_l2_aligned_asset_day ON md_book_l2_top_aligned(asset, day)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_l2_aligned_cond_side_recv ON md_book_l2_top_aligned(condition_id, market_side, recv_ms)")
        summary_rows = con.execute(
            """
            SELECT
              asset,
              day,
              count(*) AS rows,
              sum(CASE WHEN raw_l2_source_row_id IS NULL THEN 1 ELSE 0 END) AS missing_depth_rows,
              sum(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS top_overlay_required_rows,
              max(raw_l2_age_ms) AS max_raw_l2_age_ms
            FROM md_book_l2_top_aligned
            GROUP BY 1,2
            ORDER BY 1,2
            """
        ).fetchall()
        totals = con.execute(
            """
            SELECT
              count(*) AS rows,
              sum(CASE WHEN raw_l2_source_row_id IS NULL THEN 1 ELSE 0 END) AS missing_depth_rows,
              sum(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS top_overlay_required_rows,
              max(raw_l2_age_ms) AS max_raw_l2_age_ms
            FROM md_book_l2_top_aligned
            """
        ).fetchone()
    finally:
        con.close()

    by_asset_day = [
        {
            "asset": row[0],
            "day": row[1],
            "rows": int(row[2] or 0),
            "missing_depth_rows": int(row[3] or 0),
            "top_overlay_required_rows": int(row[4] or 0),
            "max_raw_l2_age_ms": int(row[5] or 0) if row[5] is not None else None,
        }
        for row in summary_rows
    ]
    manifest_out = {
        "schema_version": "l2_top_aligned_mart_v1",
        "created_utc": utc_now(),
        "status": "OK",
        "l2_manifest": str(args.l2_manifest),
        "l2_duckdb": str(l2_db),
        "output_dir": str(args.output_dir),
        "output_duckdb": str(output_db),
        "output_duckdb_sha256": sha256_file(output_db),
        "duckdb_threads": args.duckdb_threads,
        "duckdb_temp_dir": str(args.duckdb_temp_dir) if args.duckdb_temp_dir else None,
        "assets": assets or "all",
        "days": days or "all",
        "limit_per_asset": args.limit_per_asset,
        "row_count": int(totals[0] or 0),
        "missing_depth_rows": int(totals[1] or 0),
        "top_overlay_required_rows": int(totals[2] or 0),
        "top_overlay_required_rate": round(float(totals[2] or 0) / float(totals[0] or 1), 6),
        "max_raw_l2_age_ms": int(totals[3] or 0) if totals[3] is not None else None,
        "table": "md_book_l2_top_aligned",
        "by_asset_day": by_asset_day,
        "semantics": {
            "top_source": "md_book_l1",
            "depth_source": "latest md_book_l2 side snapshot at or before L1 capture_seq",
            "warning": "Depth beyond top remains best-effort for legacy price-change records where raw_l2 did not refresh with the L1 top change.",
        },
    }
    manifest_path = args.output_dir / "L2_TOP_ALIGNED_MART_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest_out[k] for k in ("status", "row_count", "missing_depth_rows", "top_overlay_required_rate", "output_duckdb")}, indent=2, sort_keys=True))
    return manifest_out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--assets", default="all")
    parser.add_argument("--days", default="all")
    parser.add_argument("--limit-per-asset", type=int)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--duckdb-temp-dir", type=Path)
    args = parser.parse_args()
    args.l2_manifest = args.l2_manifest.expanduser()
    args.output_dir = args.output_dir.expanduser()
    if args.duckdb_temp_dir:
        args.duckdb_temp_dir = args.duckdb_temp_dir.expanduser()
    build_mart(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

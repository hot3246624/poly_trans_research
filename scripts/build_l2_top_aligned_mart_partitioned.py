#!/usr/bin/env python3
"""Build a top-aligned L2 mart incrementally by day/asset partitions.

This is a partitioned alternative to build_l2_top_aligned_mart.py. It keeps the
same public table name and manifest schema, but commits each day/asset partition
independently so partial progress is visible and resumable.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_L2_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/smoke_20260517_l2/REPLAY_STORE_V2_MANIFEST.json"
)
BLOCKLISTED_DAYS = {"2026-05-14", "2026-05-15", "2026-05-19"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def parse_csv_arg(value: str, *, uppercase: bool = True) -> list[str]:
    if value.strip().lower() == "all":
        return []
    out = [part.strip() for part in value.split(",") if part.strip()]
    return [part.upper() for part in out] if uppercase else out


def progress(message: str, **fields: Any) -> None:
    print(json.dumps({"ts": utc_now(), "message": message, **fields}, ensure_ascii=False, sort_keys=True), flush=True)


def table_exists(con: Any, name: str) -> bool:
    return bool(
        con.execute(
            """
            SELECT count(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
              AND table_name = ?
            """,
            [name],
        ).fetchone()[0]
    )


def partition_select_sql(
    asset: str,
    day: str,
    *,
    empty: bool = False,
    condition_shard_count: int | None = None,
    condition_shard_index: int | None = None,
) -> str:
    extra_empty = "AND FALSE" if empty else ""
    l1_shard_filter = ""
    l2_shard_filter = ""
    if condition_shard_count and condition_shard_count > 1:
        if condition_shard_index is None:
            raise ValueError("condition_shard_index is required when condition_shard_count > 1")
        shard_count = int(condition_shard_count)
        shard_index = int(condition_shard_index)
        l1_shard_filter = f"AND hash(b.condition_id) % {shard_count} = {shard_index}"
        l2_shard_filter = f"AND hash(l.condition_id) % {shard_count} = {shard_index}"
    return f"""
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
            b.no_ask_sz
          FROM src.main.md_book_l1 b
          JOIN src.main.market_meta m
            ON b.day = m.day
           AND b.condition_id = m.condition_id
          WHERE m.symbol = {quote_literal(asset)}
            AND b.day = {quote_literal(day)}
            {l1_shard_filter}
            {extra_empty}
        ),
        l1_side AS (
          SELECT
            day, asset, condition_id, l1_source_row_id, recv_ms, recv_monotonic_ns,
            capture_seq, source_ts_ms, source_kind, 'YES' AS market_side,
            yes_bid_px AS bid1_px, yes_bid_sz AS bid1_sz,
            yes_ask_px AS ask1_px, yes_ask_sz AS ask1_sz
          FROM l1_base
          UNION ALL
          SELECT
            day, asset, condition_id, l1_source_row_id, recv_ms, recv_monotonic_ns,
            capture_seq, source_ts_ms, source_kind, 'NO' AS market_side,
            no_bid_px AS bid1_px, no_bid_sz AS bid1_sz,
            no_ask_px AS ask1_px, no_ask_sz AS ask1_sz
          FROM l1_base
        ),
        l2_base AS (
          SELECT l.*
          FROM src.main.md_book_l2 l
          JOIN src.main.market_meta m
            ON l.day = m.day
           AND l.condition_id = m.condition_id
          WHERE m.symbol = {quote_literal(asset)}
            AND l.day = {quote_literal(day)}
            {l2_shard_filter}
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
        FROM l1_side s ASOF LEFT JOIN l2_base l
          ON s.day = l.day
         AND s.condition_id = l.condition_id
         AND s.market_side = l.market_side
         AND s.capture_seq >= l.capture_seq
    """


def ensure_tables(con: Any, first_asset: str, first_day: str) -> None:
    if not table_exists(con, "md_book_l2_top_aligned"):
        con.execute(
            f"""
            CREATE TABLE md_book_l2_top_aligned AS
            SELECT *
            FROM ({partition_select_sql(first_asset, first_day, empty=True)}) q
            """
        )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS _l2_top_aligned_partition_status (
          asset VARCHAR,
          day VARCHAR,
          status VARCHAR,
          rows BIGINT,
          missing_depth_rows BIGINT,
          top_overlay_required_rows BIGINT,
          max_raw_l2_age_ms BIGINT,
          started_utc VARCHAR,
          completed_utc VARCHAR,
          error VARCHAR,
          PRIMARY KEY (asset, day)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS _l2_top_aligned_build_info (
          key VARCHAR PRIMARY KEY,
          value VARCHAR
        )
        """
    )


def source_values(con: Any, table: str, column: str) -> list[str]:
    rows = con.execute(
        f"""
        SELECT DISTINCT {column}
        FROM src.main.{table}
        WHERE {column} IS NOT NULL
        ORDER BY 1
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def resolve_assets_days(con: Any, manifest: dict[str, Any], args: argparse.Namespace) -> tuple[list[str], list[str]]:
    requested_assets = parse_csv_arg(args.assets)
    requested_days = parse_csv_arg(args.days, uppercase=False)
    if requested_assets:
        assets = sorted(set(requested_assets))
    else:
        manifest_assets = manifest.get("assets")
        if isinstance(manifest_assets, list):
            assets = sorted({str(asset).upper() for asset in manifest_assets if str(asset).strip()})
        else:
            assets = sorted({asset.upper() for asset in source_values(con, "market_meta", "symbol")})
    if requested_days:
        days = sorted(set(requested_days))
    else:
        manifest_days = manifest.get("days")
        if isinstance(manifest_days, list) and manifest_days != ["all"]:
            days = sorted({str(day) for day in manifest_days if str(day).strip()})
        else:
            days = source_values(con, "md_book_l1", "day")
    bad_days = sorted(set(days) & BLOCKLISTED_DAYS)
    if bad_days:
        raise SystemExit(f"refusing to build blocklisted days: {bad_days}")
    if not assets:
        raise SystemExit("no assets resolved from --assets or L2 manifest")
    if not days:
        raise SystemExit("no days resolved from --days or L2 manifest")
    return assets, days


def completed_partitions(con: Any) -> set[tuple[str, str]]:
    if not table_exists(con, "_l2_top_aligned_partition_status"):
        return set()
    rows = con.execute(
        """
        SELECT day, asset
        FROM _l2_top_aligned_partition_status
        WHERE status = 'complete'
        """
    ).fetchall()
    return {(str(day), str(asset)) for day, asset in rows}


def write_partition_status(output_dir: Path, row: dict[str, Any]) -> None:
    status_dir = output_dir / "partition_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    name = f"day={row['day']}__asset={row['asset']}.json"
    (status_dir / name).write_text(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_progress_manifest(
    output_dir: Path,
    args: argparse.Namespace,
    l2_db: Path,
    assets: list[str],
    days: list[str],
    completed: int,
    total: int,
) -> None:
    path = output_dir / "L2_TOP_ALIGNED_MART_PROGRESS.json"
    payload = {
        "schema_version": "l2_top_aligned_mart_partitioned_progress_v1",
        "created_utc": utc_now(),
        "status": "RUNNING" if completed < total else "COMPLETE_PENDING_FINAL_MANIFEST",
        "l2_manifest": str(args.l2_manifest),
        "l2_duckdb": str(l2_db),
        "output_dir": str(output_dir),
        "output_duckdb": str(output_dir / "l2_top_aligned_mart.duckdb"),
        "assets": assets if parse_csv_arg(args.assets) else "all",
        "days": days if parse_csv_arg(args.days, uppercase=False) else "all",
        "completed_partitions": completed,
        "total_partitions": total,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def shard_count_for_asset(args: argparse.Namespace, asset: str) -> int:
    assets = {part.strip().upper() for part in args.condition_shards_for_assets.split(",") if part.strip()}
    return int(args.condition_shards) if asset.upper() in assets and int(args.condition_shards) > 1 else 1


def insert_partition_shard(
    con: Any,
    asset: str,
    day: str,
    shard_count: int,
    shard_index: int,
    *,
    delete_existing: bool,
) -> None:
    con.execute("BEGIN TRANSACTION")
    try:
        if delete_existing:
            con.execute("DELETE FROM md_book_l2_top_aligned WHERE day = ? AND asset = ?", [day, asset])
            con.execute("DELETE FROM _l2_top_aligned_partition_status WHERE day = ? AND asset = ?", [day, asset])
        con.execute(
            f"""
            INSERT INTO md_book_l2_top_aligned
            SELECT *
            FROM ({partition_select_sql(asset, day, condition_shard_count=shard_count, condition_shard_index=shard_index)}) q
            """
        )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def build_partition(con: Any, output_dir: Path, args: argparse.Namespace, asset: str, day: str) -> dict[str, Any]:
    started = utc_now()
    progress("partition_start", day=day, asset=asset)
    try:
        shard_count = shard_count_for_asset(args, asset)
        for shard_index in range(shard_count):
            if shard_count > 1:
                progress("partition_shard_start", day=day, asset=asset, shard_index=shard_index, shard_count=shard_count)
            insert_partition_shard(
                con,
                asset,
                day,
                shard_count,
                shard_index,
                delete_existing=(shard_index == 0),
            )
            con.execute("CHECKPOINT")
            if shard_count > 1:
                progress("partition_shard_done", day=day, asset=asset, shard_index=shard_index, shard_count=shard_count)
        summary = con.execute(
            """
            SELECT
              count(*) AS rows,
              sum(CASE WHEN raw_l2_source_row_id IS NULL THEN 1 ELSE 0 END) AS missing_depth_rows,
              sum(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS top_overlay_required_rows,
              max(raw_l2_age_ms) AS max_raw_l2_age_ms
            FROM md_book_l2_top_aligned
            WHERE day = ?
              AND asset = ?
            """,
            [day, asset],
        ).fetchone()
        con.execute("BEGIN TRANSACTION")
        row = {
            "asset": asset,
            "day": day,
            "status": "complete",
            "rows": int(summary[0] or 0),
            "missing_depth_rows": int(summary[1] or 0),
            "top_overlay_required_rows": int(summary[2] or 0),
            "max_raw_l2_age_ms": int(summary[3] or 0) if summary[3] is not None else None,
            "started_utc": started,
            "completed_utc": utc_now(),
            "error": "",
        }
        con.execute(
            """
            INSERT INTO _l2_top_aligned_partition_status
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                row["asset"],
                row["day"],
                row["status"],
                row["rows"],
                row["missing_depth_rows"],
                row["top_overlay_required_rows"],
                row["max_raw_l2_age_ms"],
                row["started_utc"],
                row["completed_utc"],
                row["error"],
            ],
        )
        con.execute("COMMIT")
    except Exception as exc:
        con.execute("ROLLBACK")
        row = {
            "asset": asset,
            "day": day,
            "status": "failed",
            "rows": 0,
            "missing_depth_rows": 0,
            "top_overlay_required_rows": 0,
            "max_raw_l2_age_ms": None,
            "started_utc": started,
            "completed_utc": utc_now(),
            "error": repr(exc),
        }
        write_partition_status(output_dir, row)
        raise
    con.execute("CHECKPOINT")
    write_partition_status(output_dir, row)
    progress("partition_done", **row)
    return row


def final_summary(con: Any) -> tuple[list[dict[str, Any]], tuple[Any, ...]]:
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
    return by_asset_day, totals


def write_final_manifest(
    con: Any,
    args: argparse.Namespace,
    l2_db: Path,
    output_db: Path,
    assets: list[str],
    days: list[str],
    partition_count: int,
) -> dict[str, Any]:
    progress("final_indexes_start")
    con.execute("CREATE INDEX IF NOT EXISTS idx_l2_aligned_asset_day ON md_book_l2_top_aligned(asset, day)")
    con.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_l2_aligned_cond_side_recv
        ON md_book_l2_top_aligned(condition_id, market_side, recv_ms)
        """
    )
    con.execute("CHECKPOINT")
    progress("final_indexes_done")
    by_asset_day, totals = final_summary(con)
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
        "assets": assets if parse_csv_arg(args.assets) else "all",
        "days": days if parse_csv_arg(args.days, uppercase=False) else "all",
        "limit_per_asset": None,
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
        "build_mode": "partitioned_day_asset_incremental",
        "partition_count": partition_count,
    }
    manifest_path = args.output_dir / "L2_TOP_ALIGNED_MART_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest_out, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    progress_path = args.output_dir / "L2_TOP_ALIGNED_MART_PROGRESS.json"
    if progress_path.exists():
        progress_payload = json.loads(progress_path.read_text(encoding="utf-8"))
        progress_payload.update(
            {
                "created_utc": utc_now(),
                "status": "COMPLETE",
                "final_manifest": str(manifest_path),
                "row_count": manifest_out["row_count"],
                "missing_depth_rows": manifest_out["missing_depth_rows"],
            }
        )
        progress_path.write_text(
            json.dumps(progress_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return manifest_out


def validate_or_init_build_info(con: Any, args: argparse.Namespace, l2_db: Path) -> None:
    existing = {
        str(key): str(value)
        for key, value in con.execute("SELECT key, value FROM _l2_top_aligned_build_info").fetchall()
    }
    expected = {
        "builder": "build_l2_top_aligned_mart_partitioned.py",
        "l2_manifest": str(args.l2_manifest),
        "l2_duckdb": str(l2_db),
    }
    for key, value in expected.items():
        old = existing.get(key)
        if old is not None and old != value:
            raise SystemExit(f"existing partitioned build uses different {key}: {old}; use --replace or another --output-dir")
        con.execute("DELETE FROM _l2_top_aligned_build_info WHERE key = ?", [key])
        con.execute(
            """
            INSERT INTO _l2_top_aligned_build_info
            VALUES (?, ?)
            """,
            [key, value],
        )


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
    lock_path = args.output_dir / ".build_l2_top_aligned_mart_partitioned.lock"
    if args.replace:
        if output_db.exists():
            output_db.unlink()
        for path in [
            args.output_dir / "L2_TOP_ALIGNED_MART_MANIFEST.json",
            args.output_dir / "L2_TOP_ALIGNED_MART_PROGRESS.json",
        ]:
            if path.exists():
                path.unlink()
        status_dir = args.output_dir / "partition_status"
        if status_dir.exists():
            shutil.rmtree(status_dir)
    elif output_db.exists():
        probe = duckdb.connect(str(output_db), read_only=True)
        try:
            if not table_exists(probe, "_l2_top_aligned_partition_status"):
                raise SystemExit(
                    f"existing output DB is not a partitioned build: {output_db}; "
                    "use --replace or choose another --output-dir"
                )
        finally:
            probe.close()

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        con = duckdb.connect(str(output_db))
        try:
            con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
            if args.duckdb_temp_dir:
                args.duckdb_temp_dir.mkdir(parents=True, exist_ok=True)
                con.execute(f"PRAGMA temp_directory={quote_literal(args.duckdb_temp_dir)}")
            con.execute(f"ATTACH {quote_literal(l2_db)} AS src (READ_ONLY)")
            assets, days = resolve_assets_days(con, manifest, args)
            partitions = [(day, asset) for day in days for asset in assets]
            first_day, first_asset = partitions[0]
            ensure_tables(con, first_asset, first_day)
            validate_or_init_build_info(con, args, l2_db)
            con.execute("CHECKPOINT")
            completed = completed_partitions(con)
            total = len(partitions)
            write_progress_manifest(args.output_dir, args, l2_db, assets, days, len(completed), total)
            progress("build_start", output_db=str(output_db), partitions=total, completed=len(completed))
            for day, asset in partitions:
                if (day, asset) in completed:
                    progress("partition_skip_complete", day=day, asset=asset)
                    continue
                build_partition(con, args.output_dir, args, asset, day)
                completed.add((day, asset))
                write_progress_manifest(args.output_dir, args, l2_db, assets, days, len(completed), total)
            manifest_out = write_final_manifest(con, args, l2_db, output_db, assets, days, len(partitions))
        finally:
            con.close()
    print(
        json.dumps(
            {
                k: manifest_out[k]
                for k in ("status", "row_count", "missing_depth_rows", "top_overlay_required_rate", "output_duckdb")
            },
            indent=2,
            sort_keys=True,
        )
    )
    return manifest_out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--assets", default="all")
    parser.add_argument("--days", default="all")
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--duckdb-temp-dir", type=Path)
    parser.add_argument("--condition-shards", type=int, default=1)
    parser.add_argument("--condition-shards-for-assets", default="")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()
    args.l2_manifest = args.l2_manifest.expanduser()
    args.output_dir = args.output_dir.expanduser()
    if args.duckdb_temp_dir:
        args.duckdb_temp_dir = args.duckdb_temp_dir.expanduser()
    build_mart(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

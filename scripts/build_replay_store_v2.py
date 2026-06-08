#!/usr/bin/env python3
"""Build replay_store_v2 from local compressed replay SQLite archives.

replay_store_v2 is a queryable fact layer for source-truth validation.  It is
not a strategy candidate view.  Every exported row keeps the source replay row
id plus day/archive metadata in the manifest so validation can trace back to
the downloaded replay artifact.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


VALID_DAYS = [
    "2026-05-02",
    "2026-05-03",
    "2026-05-04",
    "2026-05-05",
    "2026-05-06",
    "2026-05-07",
    "2026-05-08",
    "2026-05-09",
    "2026-05-10",
    "2026-05-11",
    "2026-05-12",
    "2026-05-13",
    "2026-05-16",
    "2026-05-17",
    "2026-05-18",
    "2026-05-28",
    "2026-05-29",
    "2026-05-30",
    "2026-05-31",
    "2026-06-01",
    "2026-06-02",
    "2026-06-03",
    "2026-06-04",
]
BLOCKLISTED_DAYS = ["2026-05-14", "2026-05-15", "2026-05-19"]
DEFAULT_ASSETS = ["BTC"]
CORE_TABLES = [
    "market_meta",
    "settlement_records",
    "md_trades",
    "md_book_l1",
    "xuan_trades",
    "xuan_activity",
    "xuan_poll_log",
]


TABLE_QUERIES = {
    "market_meta": """
        SELECT
            '{day}' AS day,
            row_number() OVER () AS source_row_id,
            condition_id,
            slug,
            symbol,
            interval_sec,
            start_ms,
            end_ms,
            yes_token_id,
            no_token_id,
            tick_size,
            first_seen_ms,
            last_seen_ms
        FROM src.market_meta
        WHERE {market_filter_sql_no_alias}
    """,
    "settlement_records": """
        SELECT
            '{day}' AS day,
            row_number() OVER () AS source_row_id,
            s.condition_id,
            s.official_outcome,
            s.winner_side,
            s.winner_token_id,
            s.settle_ms,
            s.resolution_source,
            s.capture_seq
        FROM src.settlement_records s
        JOIN src.market_meta m ON m.condition_id = s.condition_id
        WHERE {market_filter_sql}
    """,
    "md_trades": """
        SELECT
            '{day}' AS day,
            t.id AS source_row_id,
            t.condition_id,
            t.trade_ts_ms,
            t.recv_ms,
            t.recv_monotonic_ns,
            t.capture_seq,
            t.source_ts_ms,
            t.trade_id,
            t.market_side,
            t.taker_side,
            t.maker_address,
            t.taker_address,
            t.price,
            t.size,
            t.source_quality,
            t.raw_json
        FROM src.md_trades t
        WHERE {condition_filter_t}
          AND t.market_side IN ('YES', 'NO')
    """,
    "md_book_l1": """
        SELECT
            '{day}' AS day,
            b.id AS source_row_id,
            b.condition_id,
            b.recv_ms,
            b.recv_monotonic_ns,
            b.capture_seq,
            b.source_ts_ms,
            b.yes_bid_px,
            b.yes_ask_px,
            b.no_bid_px,
            b.no_ask_px,
            b.yes_bid_sz,
            b.yes_ask_sz,
            b.no_bid_sz,
            b.no_ask_sz,
            b.source_kind
        FROM src.md_book_l1 b
        WHERE {condition_filter_b}
    """,
    "md_book_l2": """
        SELECT
            '{day}' AS day,
            b.id AS source_row_id,
            b.condition_id,
            b.recv_ms,
            b.recv_monotonic_ns,
            b.capture_seq,
            b.source_ts_ms,
            b.market_side,
            b.depth,
            b.bid1_px,
            b.bid1_sz,
            b.bid2_px,
            b.bid2_sz,
            b.bid3_px,
            b.bid3_sz,
            b.bid4_px,
            b.bid4_sz,
            b.bid5_px,
            b.bid5_sz,
            b.ask1_px,
            b.ask1_sz,
            b.ask2_px,
            b.ask2_sz,
            b.ask3_px,
            b.ask3_sz,
            b.ask4_px,
            b.ask4_sz,
            b.ask5_px,
            b.ask5_sz,
            b.source_kind
        FROM src.md_book_l2 b
        WHERE {condition_filter_b}
          AND b.market_side IN ('YES', 'NO')
    """,
    "xuan_trades": """
        SELECT
            '{day}' AS day,
            x.id AS source_row_id,
            x.user,
            x.poll_ts_ms,
            x.trade_ts_ms,
            x.recv_ms,
            x.recv_monotonic_ns,
            x.capture_seq,
            x.condition_id,
            x.slug,
            x.event_slug,
            x.title,
            x.outcome,
            x.outcome_side,
            x.side,
            x.price,
            x.size,
            x.asset,
            x.proxy_wallet,
            x.tx_hash,
            x.trade_id,
            x.source_quality,
            x.raw_json
        FROM src.xuan_trades x
        WHERE {nullable_condition_filter_x}
    """,
    "xuan_activity": """
        SELECT
            '{day}' AS day,
            x.id AS source_row_id,
            x.user,
            x.poll_ts_ms,
            x.activity_ts_ms,
            x.recv_ms,
            x.recv_monotonic_ns,
            x.capture_seq,
            x.condition_id,
            x.slug,
            x.event_slug,
            x.title,
            x.activity_type,
            x.outcome,
            x.outcome_side,
            x.side,
            x.price,
            x.size,
            x.usdc_size,
            x.asset,
            x.proxy_wallet,
            x.tx_hash,
            x.source_quality,
            x.raw_json
        FROM src.xuan_activity x
        WHERE {nullable_condition_filter_x}
    """,
    "xuan_poll_log": """
        SELECT
            '{day}' AS day,
            x.id AS source_row_id,
            x.user,
            x.endpoint,
            x.poll_ts_ms,
            x.recv_ms,
            x.recv_monotonic_ns,
            x.capture_seq,
            x.rows,
            x.max_ts_ms,
            x.ok,
            x.error
        FROM src.xuan_poll_log x
    """,
    "own_order_events": """
        SELECT
            '{day}' AS day,
            o.id AS source_row_id,
            o.condition_id,
            o.recv_ms,
            o.recv_monotonic_ns,
            o.capture_seq,
            o.client_order_id,
            o.order_id,
            o.event_type,
            o.side,
            o.direction,
            o.price,
            o.size,
            o.remaining,
            o.status,
            o.reason,
            o.reject_kind,
            o.tx_hash,
            o.strategy_tag,
            o.round_id
        FROM src.own_order_events o
        WHERE {condition_filter_o}
    """,
    "own_fill_events": """
        SELECT
            '{day}' AS day,
            f.id AS source_row_id,
            f.condition_id,
            f.asset_id,
            f.order_id,
            f.taker_order_id,
            f.trade_id,
            f.market_side,
            f.direction,
            f.trader_side,
            f.price,
            f.size,
            f.fee_rate_bps,
            f.match_ts_ms,
            f.recv_ms,
            f.recv_monotonic_ns,
            f.capture_seq,
            f.maker_address,
            f.tx_hash,
            f.raw_json
        FROM src.own_fill_events f
        WHERE {condition_filter_f}
    """,
    "own_inventory_events": """
        SELECT
            '{day}' AS day,
            i.id AS source_row_id,
            i.condition_id,
            i.asset_id,
            i.outcome,
            i.size,
            i.avg_price,
            i.redeemable,
            i.mergeable,
            i.source_kind,
            i.recv_ms,
            i.recv_monotonic_ns,
            i.capture_seq
        FROM src.own_inventory_events i
        WHERE {condition_filter_i}
    """,
    "user_ws_log": """
        SELECT
            '{day}' AS day,
            u.id AS source_row_id,
            u.recv_ms,
            u.recv_monotonic_ns,
            u.capture_seq,
            u.event_name,
            u.event_value,
            u.detail
        FROM src.user_ws_log u
    """,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def progress(message: str, **fields: Any) -> None:
    payload = {"ts": utc_now(), "message": message, **fields}
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def parse_days(value: str) -> list[str]:
    if value.strip().lower() in {"valid", "all-valid", "all"}:
        return list(VALID_DAYS)
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    bad = sorted(set(days) - set(VALID_DAYS))
    if bad:
        raise ValueError(f"days are not in the valid-day allowlist: {bad}")
    return days


def parse_assets(value: str) -> list[str] | None:
    normalized = value.strip()
    if normalized.lower() in {"all", "all-assets", "*"}:
        return None
    assets = [part.strip().upper() for part in normalized.split(",") if part.strip()]
    if not assets:
        raise ValueError("at least one asset is required, or use --assets all")
    return sorted(set(assets))


def parse_tables(value: str) -> list[str]:
    normalized = value.strip().lower()
    if normalized in {"all", "*"}:
        return list(TABLE_QUERIES)
    if normalized == "core":
        return list(CORE_TABLES)
    if normalized in {"no-l2", "without-l2"}:
        return [table for table in TABLE_QUERIES if table != "md_book_l2"]
    tables = [part.strip() for part in value.split(",") if part.strip()]
    if not tables:
        raise ValueError("at least one table is required")
    bad = sorted(set(tables) - set(TABLE_QUERIES))
    if bad:
        raise ValueError(f"unknown replay tables: {bad}; valid={sorted(TABLE_QUERIES)}")
    return tables


def enriched_view_names(tables: list[str]) -> list[str]:
    if "market_meta" not in tables:
        return []
    return [
        f"{table}_with_meta"
        for table in tables
        if table
        not in {
            "market_meta",
            "xuan_poll_log",
            "user_ws_log",
        }
    ]


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def run(cmd: list[str], *, cwd: Path | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, check=True)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_sha256sums(path: Path) -> dict[str, str]:
    checks: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"malformed SHA256SUMS line in {path}: {raw_line!r}")
        checks[parts[-1].lstrip("*")] = parts[0].lower()
    return checks


def parse_manifest_tsv(path: Path) -> dict[str, str]:
    lines = [line.rstrip("\n") for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2:
        return {}
    header = lines[0].split("\t")
    values = lines[1].split("\t")
    return {key: values[idx] for idx, key in enumerate(header) if idx < len(values)}


def verify_archive(day_dir: Path) -> dict[str, Any]:
    zst_path = day_dir / "crypto_5m.sqlite.zst"
    manifest_path = day_dir / "MANIFEST.tsv"
    sums_path = day_dir / "SHA256SUMS"
    complete_path = day_dir / ".complete"
    for required in [zst_path, manifest_path, sums_path, complete_path]:
        if not required.exists():
            raise FileNotFoundError(f"missing archive artifact: {required}")

    checks = read_sha256sums(sums_path)
    for name, expected in checks.items():
        actual_path = day_dir / name
        if not actual_path.is_file():
            raise FileNotFoundError(f"SHA256SUMS references missing file: {actual_path}")
        actual = sha256_file(actual_path)
        if actual != expected:
            raise RuntimeError(f"sha256 mismatch for {actual_path}: expected {expected}, got {actual}")

    run(["zstd", "-tq", "--long=31", str(zst_path)])
    stat = zst_path.stat()
    return {
        "archive_dir": str(day_dir),
        "archive_file": str(zst_path),
        "archive_size_bytes": stat.st_size,
        "archive_mtime_ns": stat.st_mtime_ns,
        "sha256": checks.get("crypto_5m.sqlite.zst"),
        "manifest_tsv": str(manifest_path),
        "manifest": parse_manifest_tsv(manifest_path),
        "sha256sums": str(sums_path),
        "complete_marker": str(complete_path),
    }


def extract_sqlite(zst_path: Path, db_path: Path, force: bool) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        if not force:
            raise FileExistsError(f"temp SQLite already exists: {db_path}")
        db_path.unlink()
    run(["zstd", "-dq", "--long=31", "-f", str(zst_path), "-o", str(db_path)])


def load_sqlite_sequence(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        try:
            rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
        except sqlite3.DatabaseError:
            return {}
    return {str(name): int(seq) for name, seq in rows}


def table_names(path: Path) -> set[str]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type IN ('table', 'view')
            ORDER BY name
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


def sql_string_list(values: list[str]) -> str:
    if not values:
        return "('__replay_store_v2_no_condition_ids__')"
    return "(" + ", ".join(quote_literal(value) for value in values) + ")"


def market_filter_sql(assets: list[str] | None, *, alias: str | None = "m") -> str:
    prefix = f"{alias}." if alias else ""
    clauses = [f"{prefix}interval_sec = 300"]
    if assets is not None:
        clauses.append(f"{prefix}symbol IN {sql_string_list(assets)}")
    return " AND ".join(clauses)


def load_condition_ids(path: Path, assets: list[str] | None) -> list[str]:
    where_sql = market_filter_sql(assets, alias=None)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            f"""
            SELECT condition_id
            FROM market_meta
            WHERE {where_sql}
            ORDER BY condition_id
            """
        ).fetchall()
    return [str(row[0]) for row in rows]


def load_asset_condition_counts(path: Path, assets: list[str] | None) -> dict[str, int]:
    where_sql = market_filter_sql(assets, alias=None)
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            f"""
            SELECT symbol, COUNT(*) AS n
            FROM market_meta
            WHERE {where_sql}
            GROUP BY symbol
            ORDER BY symbol
            """
        ).fetchall()
    return {str(symbol): int(n) for symbol, n in rows}


def condition_id_filter_sql(alias: str | None, condition_ids_sql: str | None) -> str:
    if condition_ids_sql is None:
        return "1=1"
    prefix = f"{alias}." if alias else ""
    return f"{prefix}condition_id IN {condition_ids_sql}"


def nullable_condition_id_filter_sql(alias: str | None, condition_ids_sql: str | None) -> str:
    if condition_ids_sql is None:
        return "1=1"
    prefix = f"{alias}." if alias else ""
    return f"({prefix}condition_id IS NULL OR {prefix}condition_id IN {condition_ids_sql})"


def attach_sqlite(conn: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    attached = {str(row[1]) for row in conn.execute("PRAGMA database_list").fetchall()}
    if "src" in attached:
        conn.execute("DETACH src")
    conn.execute(f"ATTACH {quote_literal(db_path)} AS src (TYPE SQLITE, READ_ONLY)")


def query_for_day(
    table: str,
    day: str,
    *,
    market_filter_sql_value: str,
    market_filter_sql_no_alias_value: str,
    condition_ids_sql: str | None,
) -> str:
    return TABLE_QUERIES[table].format(
        day=day,
        market_filter_sql=market_filter_sql_value,
        market_filter_sql_no_alias=market_filter_sql_no_alias_value,
        condition_ids_sql=condition_ids_sql,
        condition_filter_t=condition_id_filter_sql("t", condition_ids_sql),
        condition_filter_b=condition_id_filter_sql("b", condition_ids_sql),
        condition_filter_o=condition_id_filter_sql("o", condition_ids_sql),
        condition_filter_f=condition_id_filter_sql("f", condition_ids_sql),
        condition_filter_i=condition_id_filter_sql("i", condition_ids_sql),
        nullable_condition_filter_x=nullable_condition_id_filter_sql("x", condition_ids_sql),
    )


def count_rows(conn: duckdb.DuckDBPyConnection, query: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM ({query}) q").fetchone()[0])


def copy_table_day(conn: duckdb.DuckDBPyConnection, table: str, query: str, out_dir: Path) -> int:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = conn.execute(
        f"""
        COPY ({query})
        TO {quote_literal(out_dir)}
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day), OVERWRITE_OR_IGNORE TRUE)
        """
    )
    return int(result.fetchone()[0])


def ensure_empty_parquet(
    conn: duckdb.DuckDBPyConnection,
    table: str,
    day: str,
    out_dir: Path,
    *,
    market_filter_sql_value: str,
    market_filter_sql_no_alias_value: str,
    condition_ids_sql: str | None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    lock_path = out_dir / ".schema.lock"
    empty_dir = out_dir / "day=__schema__"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        empty_dir.mkdir(parents=True, exist_ok=True)
        empty_file = empty_dir / "data_0.parquet"
        if empty_file.exists():
            return
        query = query_for_day(
            table,
            day,
            market_filter_sql_value=market_filter_sql_value,
            market_filter_sql_no_alias_value=market_filter_sql_no_alias_value,
            condition_ids_sql=condition_ids_sql,
        )
        conn.execute(
            f"""
            COPY (SELECT * EXCLUDE(day) FROM ({query}) q WHERE 1 = 0)
            TO {quote_literal(empty_file)}
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )


def install_sqlite_extension(conn: duckdb.DuckDBPyConnection) -> None:
    try:
        conn.execute("LOAD sqlite")
    except duckdb.Error:
        conn.execute("INSTALL sqlite")
        conn.execute("LOAD sqlite")


def open_duckdb(path: Path, threads: int) -> duckdb.DuckDBPyConnection:
    conn = duckdb.connect(str(path))
    install_sqlite_extension(conn)
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    return conn


def create_views(conn: duckdb.DuckDBPyConnection, dataset_root: Path, tables: list[str]) -> None:
    for table in tables:
        glob = dataset_root / table / "**" / "*.parquet"
        conn.execute(f"DROP VIEW IF EXISTS {table}")
        conn.execute(
            f"""
            CREATE VIEW {table} AS
            SELECT *
            FROM read_parquet({quote_literal(glob)}, hive_partitioning = true)
            """
        )
    if "market_meta" not in tables:
        return
    enrichable_tables = [
        table
        for table in tables
        if table
        not in {
            "market_meta",
            "xuan_poll_log",
            "user_ws_log",
        }
    ]
    for table in enrichable_tables:
        join_kind = "LEFT JOIN" if table.startswith("xuan_") else "JOIN"
        conn.execute(f"DROP VIEW IF EXISTS {table}_with_meta")
        conn.execute(
            f"""
            CREATE VIEW {table}_with_meta AS
            SELECT
                t.*,
                m.symbol AS market_symbol,
                m.interval_sec AS market_interval_sec,
                m.start_ms AS market_start_ms,
                m.end_ms AS market_end_ms,
                m.slug AS market_slug,
                m.yes_token_id AS market_yes_token_id,
                m.no_token_id AS market_no_token_id
            FROM {table} t
            {join_kind} market_meta m
              ON t.day = m.day
             AND t.condition_id = m.condition_id
            """
        )


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"replay_store_v2 already exists: {final_dir}")
        backup = final_dir.with_name(
            f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
        )
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def remove_day_outputs(dataset_root: Path, day: str, tables: list[str]) -> None:
    for table in tables:
        day_dir = dataset_root / table / f"day={day}"
        if day_dir.exists():
            shutil.rmtree(day_dir)


def process_day(
    args: argparse.Namespace,
    day: str,
    tmp_dir: Path,
    dataset_root: Path,
    temp_root: Path,
) -> dict[str, Any]:
    assets = parse_assets(args.assets)
    tables = parse_tables(args.tables)
    if day in BLOCKLISTED_DAYS:
        raise ValueError(f"blocklisted day cannot be processed: {day}")
    progress("day_start", day=day)
    day_dir = args.archive_root / day
    progress("archive_verify_start", day=day)
    archive_meta = (
        verify_archive(day_dir)
        if args.verify_archive
        else {
            "archive_dir": str(day_dir),
            "archive_file": str(day_dir / "crypto_5m.sqlite.zst"),
        }
    )
    progress("archive_verify_done", day=day, archive_size_bytes=archive_meta.get("archive_size_bytes"))
    db_path = temp_root / day / "crypto_5m.sqlite"
    progress("extract_start", day=day, target=str(db_path))
    extract_sqlite(Path(archive_meta["archive_file"]), db_path, force=True)
    db_stat = db_path.stat()
    progress("extract_done", day=day, sqlite_size_bytes=db_stat.st_size)
    sqlite_sequence = load_sqlite_sequence(db_path)
    missing_tables = sorted(set(tables) - table_names(db_path))
    if missing_tables:
        raise RuntimeError(f"{db_path} is missing replay tables required by v2: {missing_tables}")
    condition_ids = load_condition_ids(db_path, assets)
    condition_ids_sql = None if assets is None else sql_string_list(condition_ids)
    asset_condition_counts = load_asset_condition_counts(db_path, assets)
    market_filter = market_filter_sql(assets, alias="m")
    market_filter_no_alias = market_filter_sql(assets, alias=None)
    source_replay = {
        "day": day,
        "temp_sqlite_size_bytes": db_stat.st_size,
        "temp_sqlite_mtime_ns": db_stat.st_mtime_ns,
        "sqlite_sequence": sqlite_sequence,
        "condition_id_count": len(condition_ids),
        "asset_condition_counts": asset_condition_counts,
        "archive": archive_meta,
    }

    conn = duckdb.connect()
    install_sqlite_extension(conn)
    conn.execute(f"PRAGMA threads={max(1, int(args.duckdb_threads))}")
    table_counts: dict[str, int] = {}
    try:
        attach_sqlite(conn, db_path)
        for table in tables:
            query = query_for_day(
                table,
                day,
                market_filter_sql_value=market_filter,
                market_filter_sql_no_alias_value=market_filter_no_alias,
                condition_ids_sql=condition_ids_sql,
            )
            out_dir = dataset_root / table
            ensure_empty_parquet(
                conn,
                table,
                day,
                out_dir,
                market_filter_sql_value=market_filter,
                market_filter_sql_no_alias_value=market_filter_no_alias,
                condition_ids_sql=condition_ids_sql,
            )
            progress("copy_table_start", day=day, table=table)
            rows = copy_table_day(conn, table, query, out_dir)
            table_counts[table] = rows
            progress("copy_table_done", day=day, table=table, rows=rows)
        conn.execute("DETACH src")
    finally:
        conn.close()
    if not args.keep_temp:
        shutil.rmtree(db_path.parent)
        progress("temp_sqlite_removed", day=day)
    progress("day_done", day=day)
    return {"day": day, "source_replay": source_replay, "table_counts": table_counts}


def run_day_worker(
    args: argparse.Namespace,
    day: str,
    label: str,
    tmp_dir: Path,
    dataset_root: Path,
) -> dict[str, Any]:
    tables = parse_tables(args.tables)
    summary_path = tmp_dir / f".worker_{day}.json"
    if args.resume and summary_path.exists():
        return json.loads(summary_path.read_text(encoding="utf-8"))
    if summary_path.exists():
        summary_path.unlink()
    remove_day_outputs(dataset_root, day, tables)
    worker_temp_day = args.temp_root / label / day
    if worker_temp_day.exists():
        shutil.rmtree(worker_temp_day)
    cmd = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--archive-root",
        str(args.archive_root),
        "--store-root",
        str(args.store_root),
        "--store-name",
        args.store_name,
        "--days",
        day,
        "--assets",
        args.assets,
        "--tables",
        args.tables,
        "--label",
        label,
        "--temp-root",
        str(args.temp_root),
        "--min-store-free-gb",
        str(args.min_store_free_gb),
        "--min-temp-free-gb",
        str(args.min_temp_free_gb),
        "--duckdb-threads",
        str(args.duckdb_threads),
        "--worker-day",
        day,
        "--worker-tmp-dir",
        str(tmp_dir),
        "--worker-dataset-root",
        str(dataset_root),
        "--worker-summary-path",
        str(summary_path),
    ]
    if args.keep_temp:
        cmd.append("--keep-temp")
    if args.force:
        cmd.append("--force")
    if not args.verify_archive:
        cmd.append("--no-verify-archive")
    subprocess.run(cmd, check=True)
    return json.loads(summary_path.read_text(encoding="utf-8"))


def build(args: argparse.Namespace) -> dict[str, Any]:
    days = parse_days(args.days)
    requested_assets = parse_assets(args.assets)
    tables = parse_tables(args.tables)
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    publish_root = args.store_root / args.store_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp" if args.resume else publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    temp_root = args.temp_root / label
    publish_root.mkdir(parents=True, exist_ok=True)
    args.temp_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.store_root, args.min_store_free_gb)
        require_free_gb(args.temp_root, args.min_temp_free_gb)
        if tmp_dir.exists() and not args.resume:
            shutil.rmtree(tmp_dir)
        if temp_root.exists() and args.force and not args.resume:
            shutil.rmtree(temp_root)
        tmp_dir.mkdir(parents=True, exist_ok=args.resume)
        temp_root.mkdir(parents=True, exist_ok=True)

        started_at = utc_now()
        dataset_root = tmp_dir / "dataset"
        dataset_root.mkdir(parents=True, exist_ok=args.resume)

        table_counts: dict[str, dict[str, int]] = {table: {} for table in tables}
        source_replay_by_day: dict[str, dict[str, Any]] = {}
        private_table_names = ["own_order_events", "own_fill_events", "own_inventory_events", "user_ws_log"]
        if args.parallel_days <= 1:
            for day in days:
                summary = run_day_worker(args, day, label, tmp_dir, dataset_root)
                source_replay_by_day[day] = summary["source_replay"]
                for table, rows in summary["table_counts"].items():
                    table_counts[table][day] = int(rows)
                require_free_gb(args.store_root, args.min_store_free_gb)
                require_free_gb(args.temp_root, args.min_temp_free_gb)
        else:
            with ThreadPoolExecutor(max_workers=max(1, int(args.parallel_days))) as executor:
                futures = {
                    executor.submit(run_day_worker, args, day, label, tmp_dir, dataset_root): day
                    for day in days
                }
                for future in as_completed(futures):
                    day = futures[future]
                    summary = future.result()
                    source_replay_by_day[day] = summary["source_replay"]
                    for table, rows in summary["table_counts"].items():
                        table_counts[table][day] = int(rows)
                    require_free_gb(args.store_root, args.min_store_free_gb)
                    require_free_gb(args.temp_root, args.min_temp_free_gb)
        source_replay = [source_replay_by_day[day] for day in days]

        table_totals = {table: sum(day_counts.values()) for table, day_counts in table_counts.items()}
        private_table_counts = {table: table_totals.get(table, 0) for table in private_table_names}
        has_private_truth_rows = any(private_table_counts.values())
        actual_assets = sorted(
            {
                asset
                for day_meta in source_replay
                for asset in day_meta.get("asset_condition_counts", {})
            }
        )
        parquet_files = sorted(p.relative_to(tmp_dir).as_posix() for p in dataset_root.rglob("*.parquet"))
        manifest = {
            "schema_version": "replay_store_v2",
            "dataset_type": "replay_store_v2",
            "created_at_utc": utc_now(),
            "started_at_utc": started_at,
            "label": label,
            "days": days,
            "labels_days": days,
            "blocklisted_days_excluded": BLOCKLISTED_DAYS,
            "market_prefix": "crypto",
            "assets_requested": "all" if requested_assets is None else requested_assets,
            "assets": actual_assets,
            "interval_sec": 300,
            "tables": tables,
            "source": "local_replay_archive_zstd_sqlite",
            "source_archive_root": str(args.archive_root),
            "raw_scanned": False,
            "collector_scanned": False,
            "archive_scanned": True,
            "replay_sqlite_extracted_temporarily": not args.keep_temp,
            "replay_sqlite_temp_root": str(temp_root),
            "truth_capability": {
                "source_truth_ready": True,
                "private_truth_ready": has_private_truth_rows,
                "public_or_proxy_truth_only": not has_private_truth_rows,
                "private_truth_note": (
                    "own_order_events/own_fill_events/own_inventory_events/user_ws_log contain rows"
                    if has_private_truth_rows
                    else "No private own_* or user_ws rows found in the downloaded replay archives."
                ),
            },
            "row_count": sum(table_totals.values()),
            "table_counts": table_counts,
            "table_totals": table_totals,
            "private_table_counts": private_table_counts,
            "source_replay": source_replay,
            "outputs": {
                "duckdb": "store.duckdb",
                "dataset_root": "dataset",
                "parquet_files": parquet_files,
                "table_parquet_globs": {table: f"dataset/{table}/**/*.parquet" for table in tables},
                "duckdb_views": sorted(tables + enriched_view_names(tables)),
            },
            "validation_contract": {
                "supports_pre_action_surplus_budget_replay": True,
                "supports_budgeted_residual_fifo_replay_inputs": True,
                "supports_second_leg_rescue_timing_cost_source_linkage": "md_book_l2" in tables,
                "private_execution_truth_required_for_promotion": True,
            },
        }
        (tmp_dir / "REPLAY_STORE_V2_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (tmp_dir / "README.md").write_text(
            "\n".join(
                [
                    "# replay_store_v2",
                    "",
                    "Queryable source-truth layer built from local compressed replay SQLite archives.",
                    "",
                    "Primary files:",
                    "- `REPLAY_STORE_V2_MANIFEST.json`",
                    "- `store.duckdb` with views for every exported table",
                    "- `dataset/<table>/**/*.parquet`",
                    "",
                    "This store does not scan raw capture data or the remote collector. It verifies and",
                    "temporarily extracts local `.sqlite.zst` replay archives one day at a time.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        publish_tmp(tmp_dir, final_dir, args.force)
        view_conn = duckdb.connect(str(final_dir / "store.duckdb"))
        try:
            create_views(view_conn, final_dir / "dataset", tables)
            view_conn.execute("CHECKPOINT")
        finally:
            view_conn.close()
        if temp_root.exists() and not args.keep_temp:
            try:
                temp_root.rmdir()
            except OSError:
                pass
        return {"published": str(final_dir), "manifest": str(final_dir / "REPLAY_STORE_V2_MANIFEST.json"), "tables": table_totals}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default="replay_store_v2")
    parser.add_argument("--days", required=True, help="Comma-separated YYYY-MM-DD list, or 'valid'.")
    parser.add_argument("--assets", default="BTC", help="Comma-separated symbols, or 'all'. Default: BTC.")
    parser.add_argument(
        "--tables",
        default="all",
        help="Comma-separated replay tables, 'all', 'core' (no L2/private tables), or 'no-l2'. Default: all.",
    )
    parser.add_argument("--label", default=None)
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--min-store-free-gb", type=float, default=80.0)
    parser.add_argument("--min-temp-free-gb", type=float, default=80.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--parallel-days", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--keep-temp", action="store_true")
    parser.add_argument("--no-verify-archive", dest="verify_archive", action="store_false")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--worker-day", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-tmp-dir", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-dataset-root", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--worker-summary-path", type=Path, default=None, help=argparse.SUPPRESS)
    parser.set_defaults(verify_archive=True)
    args = parser.parse_args()
    if args.worker_day:
        if args.worker_tmp_dir is None or args.worker_dataset_root is None or args.worker_summary_path is None:
            raise SystemExit("--worker-tmp-dir, --worker-dataset-root, and --worker-summary-path are required")
        label = args.label or args.worker_day.replace("-", "")
        summary = process_day(args, args.worker_day, args.worker_tmp_dir, args.worker_dataset_root, args.temp_root / label)
        args.worker_summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return 0
    result = build(args)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

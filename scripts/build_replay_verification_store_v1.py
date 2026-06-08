#!/usr/bin/env python3
"""Build a Parquet/DuckDB verification store from published replay SQLite.

The store is a read-optimized query layer for finalist verification.  It does
not replace ``replay_published`` as source of truth; every published store keeps
the source SQLite file metadata and sqlite_sequence counts so the store can be
traced back to the exact replay artifacts used to build it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


BTC_MARKET_SQL = "symbol = 'BTC' AND interval_sec = 300"

TABLE_QUERIES = {
    "market_meta": """
        SELECT
            '{day}' AS day,
            condition_id,
            slug,
            symbol,
            interval_sec,
            start_ms,
            end_ms
        FROM src.market_meta
        WHERE {btc_market_sql}
    """,
    "settlement_records": """
        SELECT
            '{day}' AS day,
            s.condition_id,
            s.winner_side,
            s.settle_ms
        FROM src.settlement_records s
        JOIN src.market_meta m ON m.condition_id = s.condition_id
        WHERE {btc_market_sql}
          AND s.winner_side IN ('YES', 'NO')
    """,
    "md_trades": """
        SELECT
            '{day}' AS day,
            t.id,
            t.condition_id,
            t.trade_ts_ms,
            t.recv_ms,
            t.capture_seq,
            t.market_side,
            t.taker_side,
            t.price,
            t.size
        FROM src.md_trades t
        JOIN src.market_meta m ON m.condition_id = t.condition_id
        WHERE {btc_market_sql}
          AND t.trade_ts_ms IS NOT NULL
          AND t.market_side IN ('YES', 'NO')
    """,
    "md_book_l1": """
        SELECT
            '{day}' AS day,
            l.id,
            l.condition_id,
            l.recv_ms,
            l.capture_seq,
            l.yes_bid_px,
            l.yes_ask_px,
            l.no_bid_px,
            l.no_ask_px,
            l.yes_bid_sz,
            l.yes_ask_sz,
            l.no_bid_sz,
            l.no_ask_sz
        FROM src.md_book_l1 l
        JOIN src.market_meta m ON m.condition_id = l.condition_id
        WHERE {btc_market_sql}
    """,
    "md_book_l2": """
        SELECT
            '{day}' AS day,
            l.id,
            l.condition_id,
            l.recv_ms,
            l.capture_seq,
            l.market_side,
            l.ask1_px,
            l.ask1_sz,
            l.ask2_px,
            l.ask2_sz,
            l.ask3_px,
            l.ask3_sz,
            l.ask4_px,
            l.ask4_sz,
            l.ask5_px,
            l.ask5_sz
        FROM src.md_book_l2 l
        JOIN src.market_meta m ON m.condition_id = l.condition_id
        WHERE {btc_market_sql}
          AND l.market_side IN ('YES', 'NO')
    """,
}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def load_sqlite_sequence(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {str(name): int(seq) for name, seq in rows}


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def parse_days(value: str) -> list[str]:
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    return days


def count_rows(conn: duckdb.DuckDBPyConnection, query: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM ({query}) q").fetchone()[0])


def attach_sqlite(conn: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    conn.execute("DETACH src") if "src" in {row[1] for row in conn.execute("PRAGMA database_list").fetchall()} else None
    conn.execute(f"ATTACH {quote_literal(db_path)} AS src (TYPE SQLITE, READ_ONLY)")


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"verification store already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default="taker_buy_replay_verification_store_v1")
    parser.add_argument("--days", required=True, help="Comma-separated YYYY-MM-DD list.")
    parser.add_argument("--label", default=None)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    days = parse_days(args.days)
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    publish_root = args.store_root / args.store_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    publish_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.store_root, args.min_free_gb)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        started_at = utc_now()
        conn = duckdb.connect(str(tmp_dir / "store.duckdb"))
        try:
            conn.execute("LOAD sqlite")
        except duckdb.Error:
            conn.execute("INSTALL sqlite")
            conn.execute("LOAD sqlite")
        conn.execute(f"PRAGMA threads={max(1, int(args.duckdb_threads))}")

        source_replay: list[dict[str, Any]] = []
        table_counts: dict[str, dict[str, int]] = {name: {} for name in TABLE_QUERIES}
        try:
            for day in days:
                db_path = args.replay_root / day / "crypto_5m.sqlite"
                if not db_path.is_file():
                    raise FileNotFoundError(f"missing replay SQLite for {day}: {db_path}")
                stat = db_path.stat()
                source_replay.append(
                    {
                        "day": day,
                        "path": str(db_path),
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sqlite_sequence": load_sqlite_sequence(db_path),
                    }
                )
                attach_sqlite(conn, db_path)
                for table, template in TABLE_QUERIES.items():
                    query = template.format(day=day, btc_market_sql=BTC_MARKET_SQL)
                    out_dir = tmp_dir / "dataset" / table
                    out_dir.mkdir(parents=True, exist_ok=True)
                    rows = count_rows(conn, query)
                    table_counts[table][day] = rows
                    conn.execute(
                        f"""
                        COPY ({query})
                        TO {quote_literal(out_dir)}
                        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day), OVERWRITE_OR_IGNORE TRUE)
                        """
                    )
            conn.execute("CHECKPOINT")
        finally:
            conn.close()

        parquet_files = sorted(p.relative_to(tmp_dir).as_posix() for p in (tmp_dir / "dataset").rglob("*.parquet"))
        manifest = {
            "schema_version": "taker_buy_replay_verification_store_v1",
            "created_at_utc": utc_now(),
            "started_at_utc": started_at,
            "label": label,
            "days": days,
            "source": "replay_published_sqlite",
            "source_replay": source_replay,
            "scope": "BTC 5m markets only",
            "outputs": {
                "duckdb": "store.duckdb",
                "dataset_root": "dataset",
                "parquet_files": parquet_files,
                "table_parquet_globs": {
                    table: f"dataset/{table}/**/*.parquet" for table in TABLE_QUERIES
                },
                "table_counts": table_counts,
            },
        }
        (tmp_dir / "STORE_MANIFEST.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (tmp_dir / "README.md").write_text(
            "\n".join(
                [
                    "# Replay Verification Store V1",
                    "",
                    "This is a read-optimized Parquet/DuckDB query layer built from replay SQLite.",
                    "`replay_published` remains the source of truth for final audit.",
                    "",
                    "Use this store only after checking `STORE_MANIFEST.json` against source replay metadata.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        publish_tmp(tmp_dir, final_dir, args.force)
        print(json.dumps({"published": str(final_dir), "tables": table_counts}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build and publish the V2 taker-BUY cache from a published V1 cache.

V2 is a query-layer upgrade. It does not rescan replay SQLite; it converts the
validated V1 fact cache into partitioned Parquet plus a portable DuckDB table.
`replay_published` remains the source of truth for final strategy validation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc


STRING_COLUMNS = {
    "day",
    "slug",
    "condition_id",
    "winner_side",
    "trigger_iso",
    "first_side",
    "side_alignment",
    "strict_side_alignment",
    "cache_side_alignment_old",
}
BOOL_COLUMNS = {"first_is_winner"}
INT_COLUMNS = {"trigger_ts_ms", "first_l2_age_ms", "strict_l1_recv_ms", "strict_l1_age_ms"}


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def count_csv_rows(path: Path) -> tuple[int, list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return 0, []
        return sum(1 for _ in reader), list(reader.fieldnames)


def column_type(name: str) -> str:
    if name in STRING_COLUMNS:
        return "VARCHAR"
    if name in BOOL_COLUMNS or name.endswith("_hit"):
        return "BOOLEAN"
    if name in INT_COLUMNS:
        return "BIGINT"
    return "DOUBLE"


def typed_select(fieldnames: list[str], table: str) -> str:
    parts = []
    for name in fieldnames:
        ident = quote_ident(name)
        typ = column_type(name)
        if typ == "VARCHAR":
            expr = f"CAST({table}.{ident} AS VARCHAR)"
        else:
            expr = f"TRY_CAST({table}.{ident} AS {typ})"
        parts.append(f"{expr} AS {ident}")
    return ",\n  ".join(parts)


def day_counts(conn: duckdb.DuckDBPyConnection, table: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT CAST(day AS VARCHAR) AS day, COUNT(*) AS rows FROM {table} GROUP BY 1 ORDER BY 1"
    ).fetchall()
    return {str(day): int(count) for day, count in rows}


def validate_source_v1(source_cache_dir: Path, allow_unvalidated: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    manifest_path = source_cache_dir / "CACHE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing V1 manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    validation_path = source_cache_dir / "CACHE_VALIDATION.json"
    validation = load_json(validation_path) if validation_path.is_file() else None
    if not allow_unvalidated:
        if validation is None:
            raise RuntimeError(f"V1 validation is required before V2 publish: {validation_path}")
        if int(validation.get("error_count", -1)) != 0:
            raise RuntimeError(f"V1 validation failed: error_count={validation.get('error_count')}")
    return manifest, validation


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"cache already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-cache-dir", type=Path, required=True, help="Published V1 cache directory.")
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--cache-name", default="taker_buy_signal_core_v2")
    parser.add_argument("--label", default=None)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-unvalidated-source", action="store_true")
    parser.add_argument("--duckdb-threads", type=int, default=2)
    args = parser.parse_args()

    source_cache_dir = args.source_cache_dir.resolve()
    source_manifest, source_validation = validate_source_v1(source_cache_dir, args.allow_unvalidated_source)
    source_csv = source_cache_dir / str(source_manifest["outputs"]["csv"])
    if not source_csv.is_file():
        raise FileNotFoundError(f"missing V1 CSV: {source_csv}")
    row_count, fieldnames = count_csv_rows(source_csv)
    expected_rows = int(source_manifest["outputs"]["row_count"])
    if row_count != expected_rows:
        raise RuntimeError(f"V1 CSV row count mismatch: csv={row_count} manifest={expected_rows}")

    label = args.label or str(source_manifest.get("label") or source_cache_dir.name)
    publish_root = args.cache_root / args.cache_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    publish_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.cache_root, args.min_free_gb)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        dataset_dir = tmp_dir / "dataset"
        db_path = tmp_dir / "cache.duckdb"
        started_at = utc_now()
        try:
            conn = duckdb.connect(str(db_path))
            conn.execute(f"PRAGMA threads={max(1, int(args.duckdb_threads))}")
            conn.execute(
                f"""
                CREATE VIEW raw_v1 AS
                SELECT * FROM read_csv(
                  {quote_literal(source_csv)},
                  header=true,
                  all_varchar=true,
                  nullstr=''
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE taker_buy_signal_candidates AS
                SELECT
                  {typed_select(fieldnames, "raw_v1")}
                FROM raw_v1
                """
            )
            db_rows = int(conn.execute("SELECT COUNT(*) FROM taker_buy_signal_candidates").fetchone()[0])
            if db_rows != row_count:
                raise RuntimeError(f"DuckDB row count mismatch: db={db_rows} csv={row_count}")
            counts = day_counts(conn, "taker_buy_signal_candidates")
            conn.execute(
                f"""
                COPY taker_buy_signal_candidates TO {quote_literal(dataset_dir)}
                (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day))
                """
            )
            conn.execute("CHECKPOINT")
            conn.close()

            parquet_files = sorted(p.relative_to(tmp_dir).as_posix() for p in dataset_dir.rglob("*.parquet"))
            if not parquet_files:
                raise RuntimeError("V2 build produced no parquet files")

            v1_validation_path = source_cache_dir / "CACHE_VALIDATION.json"
            manifest = {
                "schema_version": 2,
                "cache_kind": "taker_buy_signal_candidate_cache",
                "cache_name": args.cache_name,
                "label": label,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "source_cache": {
                    "cache_dir": str(source_cache_dir),
                    "manifest_sha256": sha256_file(source_cache_dir / "CACHE_MANIFEST.json"),
                    "validation_sha256": sha256_file(v1_validation_path) if v1_validation_path.is_file() else None,
                    "validation_error_count": None if source_validation is None else source_validation.get("error_count"),
                    "source_schema_version": source_manifest.get("schema_version"),
                    "source_row_count": row_count,
                },
                "days": source_manifest.get("days", sorted(counts)),
                "outputs": {
                    "duckdb": "cache.duckdb",
                    "duckdb_table": "taker_buy_signal_candidates",
                    "parquet_glob": "dataset/**/*.parquet",
                    "parquet_files": parquet_files,
                    "row_count": row_count,
                    "fieldnames": fieldnames,
                    "day_counts": counts,
                },
                "source_replay": source_manifest.get("source_replay", []),
                "parameters": source_manifest.get("parameters", {}),
                "truth_policy": "V2 is a query cache derived from V1; final strategies must be verified against replay_published",
            }
            (tmp_dir / "QUERY_EXAMPLES.sql").write_text(
                "\n".join(
                    [
                        "-- DuckDB examples",
                        "SELECT COUNT(*) FROM taker_buy_signal_candidates;",
                        "SELECT day, COUNT(*) FROM taker_buy_signal_candidates GROUP BY 1 ORDER BY 1;",
                        "",
                        "-- Direct Parquet example from this directory:",
                        "SELECT * FROM read_parquet('dataset/**/*.parquet', hive_partitioning=true) LIMIT 10;",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            write_json(tmp_dir / "CACHE_MANIFEST.json", manifest)
            publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "rows": row_count, "parquet_files": len(parquet_files)}, indent=2))
            return 0
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate a V2 Parquet/DuckDB cache against its V1 source cache."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

import build_taker_buy_cache_v2 as v2


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def make_norm_table(conn: duckdb.DuckDBPyConnection, source: str, target: str, fieldnames: list[str]) -> None:
    conn.execute(
        f"""
        CREATE TABLE {target} AS
        SELECT
          {v2.typed_select(fieldnames, source)}
        FROM {source}
        """
    )


def except_count(conn: duckdb.DuckDBPyConnection, left: str, right: str, fieldnames: list[str]) -> int:
    cols = ", ".join(v2.quote_ident(name) for name in fieldnames)
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*) FROM (
              SELECT {cols} FROM {left}
              EXCEPT ALL
              SELECT {cols} FROM {right}
            )
            """
        ).fetchone()[0]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--v1-cache-dir", type=Path, required=True)
    parser.add_argument("--v2-cache-dir", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=200)
    args = parser.parse_args()

    v1_manifest = load_json(args.v1_cache_dir / "CACHE_MANIFEST.json")
    v2_manifest = load_json(args.v2_cache_dir / "CACHE_MANIFEST.json")
    v1_csv = args.v1_cache_dir / str(v1_manifest["outputs"]["csv"])
    v2_db = args.v2_cache_dir / str(v2_manifest["outputs"]["duckdb"])
    v2_parquet = args.v2_cache_dir / str(v2_manifest["outputs"]["parquet_glob"])
    fieldnames = list(v1_manifest["outputs"]["fieldnames"])
    errors: list[str] = []

    if fieldnames != list(v2_manifest["outputs"]["fieldnames"]):
        errors.append("fieldnames differ between V1 and V2 manifests")
    if int(v1_manifest["outputs"]["row_count"]) != int(v2_manifest["outputs"]["row_count"]):
        errors.append("manifest row_count differs between V1 and V2")
    if not v2_db.is_file():
        errors.append(f"missing DuckDB file: {v2_db}")

    conn = duckdb.connect()
    conn.execute(
        f"""
        CREATE VIEW v1_raw AS
        SELECT * FROM read_csv(
          {v2.quote_literal(v1_csv)},
          header=true,
          all_varchar=true,
          nullstr=''
        )
        """
    )
    conn.execute(
        f"""
        CREATE VIEW v2_parquet_raw AS
        SELECT * FROM read_parquet({v2.quote_literal(v2_parquet)}, hive_partitioning=true)
        """
    )
    make_norm_table(conn, "v1_raw", "v1_norm", fieldnames)
    make_norm_table(conn, "v2_parquet_raw", "v2_parquet_norm", fieldnames)
    conn.execute(f"ATTACH {v2.quote_literal(v2_db)} AS v2db (READ_ONLY)")
    make_norm_table(conn, "v2db.taker_buy_signal_candidates", "v2_duckdb_norm", fieldnames)

    v1_rows = int(conn.execute("SELECT COUNT(*) FROM v1_norm").fetchone()[0])
    parquet_rows = int(conn.execute("SELECT COUNT(*) FROM v2_parquet_norm").fetchone()[0])
    duckdb_rows = int(conn.execute("SELECT COUNT(*) FROM v2_duckdb_norm").fetchone()[0])
    if v1_rows != parquet_rows:
        errors.append(f"V1 vs Parquet row count mismatch: {v1_rows} != {parquet_rows}")
    if parquet_rows != duckdb_rows:
        errors.append(f"Parquet vs DuckDB row count mismatch: {parquet_rows} != {duckdb_rows}")

    v1_day_counts = v2.day_counts(conn, "v1_norm")
    parquet_day_counts = v2.day_counts(conn, "v2_parquet_norm")
    duckdb_day_counts = v2.day_counts(conn, "v2_duckdb_norm")
    if v1_day_counts != parquet_day_counts:
        errors.append("V1 vs Parquet day_counts mismatch")
    if parquet_day_counts != duckdb_day_counts:
        errors.append("Parquet vs DuckDB day_counts mismatch")

    v1_missing_from_parquet = except_count(conn, "v1_norm", "v2_parquet_norm", fieldnames)
    parquet_extra = except_count(conn, "v2_parquet_norm", "v1_norm", fieldnames)
    parquet_missing_from_duckdb = except_count(conn, "v2_parquet_norm", "v2_duckdb_norm", fieldnames)
    duckdb_extra = except_count(conn, "v2_duckdb_norm", "v2_parquet_norm", fieldnames)
    if v1_missing_from_parquet:
        errors.append(f"rows present in V1 but missing from Parquet: {v1_missing_from_parquet}")
    if parquet_extra:
        errors.append(f"rows present in Parquet but missing from V1: {parquet_extra}")
    if parquet_missing_from_duckdb:
        errors.append(f"rows present in Parquet but missing from DuckDB: {parquet_missing_from_duckdb}")
    if duckdb_extra:
        errors.append(f"rows present in DuckDB but missing from Parquet: {duckdb_extra}")

    sample_cols = ", ".join(v2.quote_ident(name) for name in fieldnames)
    sample_rows = conn.execute(
        f"""
        SELECT {sample_cols}
        FROM v2_parquet_norm
        ORDER BY hash(condition_id, trigger_ts_ms, first_side)
        LIMIT {max(0, int(args.samples))}
        """
    ).fetchall()

    report = {
        "v1_cache_dir": str(args.v1_cache_dir),
        "v2_cache_dir": str(args.v2_cache_dir),
        "rows": {"v1": v1_rows, "parquet": parquet_rows, "duckdb": duckdb_rows},
        "day_counts": {"v1": v1_day_counts, "parquet": parquet_day_counts, "duckdb": duckdb_day_counts},
        "samples_checked": len(sample_rows),
        "diff_counts": {
            "v1_missing_from_parquet": v1_missing_from_parquet,
            "parquet_extra_vs_v1": parquet_extra,
            "parquet_missing_from_duckdb": parquet_missing_from_duckdb,
            "duckdb_extra_vs_parquet": duckdb_extra,
        },
        "error_count": len(errors),
        "errors": errors,
    }
    write_json(args.v2_cache_dir / "CACHE_VALIDATION_V2.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

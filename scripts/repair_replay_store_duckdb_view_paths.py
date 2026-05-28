#!/usr/bin/env python3
"""Repair DuckDB replay-store views copied from an external root.

The replay store's parquet files can be moved to the MacBook local root while
the DuckDB view definitions still point at /Volumes/PolyData. This rewrites
only persistent view SQL; parquet data is not modified.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_OLD_ROOT = "/Volumes/PolyData/poly_backtest_data"
DEFAULT_NEW_ROOT = "/Users/hot/web3Scientist/poly_backtest_data"


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duckdb", type=Path, required=True)
    parser.add_argument("--old-root", default=DEFAULT_OLD_ROOT)
    parser.add_argument("--new-root", default=DEFAULT_NEW_ROOT)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    import duckdb  # type: ignore

    db_path = args.duckdb.expanduser()
    con = duckdb.connect(str(db_path))
    try:
        rows = con.execute(
            """
            SELECT view_name, sql
            FROM duckdb_views()
            WHERE database_name = current_database()
              AND schema_name = 'main'
              AND NOT internal
              AND NOT temporary
            ORDER BY view_name
            """
        ).fetchall()
        rewrites: list[dict[str, Any]] = []
        for name, sql in rows:
            if args.old_root in sql:
                rewrites.append(
                    {
                        "view": name,
                        "old_sql": sql,
                        "new_sql": sql.replace(args.old_root, args.new_root),
                    }
                )
        if not args.dry_run and rewrites:
            for item in sorted(rewrites, key=lambda row: str(row["view"]), reverse=True):
                con.execute(f"DROP VIEW IF EXISTS {quote_ident(str(item['view']))}")
            for item in sorted(rewrites, key=lambda row: str(row["view"])):
                con.execute(str(item["new_sql"]))
            con.execute("CHECKPOINT")
        remaining = con.execute(
            """
            SELECT count(*)
            FROM duckdb_views()
            WHERE database_name = current_database()
              AND schema_name = 'main'
              AND NOT internal
              AND NOT temporary
              AND contains(sql, ?)
            """,
            [args.old_root],
        ).fetchone()[0]
    finally:
        con.close()

    print(
        json.dumps(
            {
                "duckdb": str(db_path),
                "dry_run": args.dry_run,
                "rewritten_view_count": len(rewrites),
                "rewritten_views": [item["view"] for item in rewrites],
                "remaining_old_root_view_count": int(remaining),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

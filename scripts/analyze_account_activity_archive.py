#!/usr/bin/env python3
"""Run the canonical public-account ledger against a local SQLite archive.

The accounting implementation remains in analyze_xuan_public_activity_pnl.py;
this adapter only replaces the lossy network collection step with a durable
raw_json read from activity_events.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import analyze_xuan_public_activity_pnl as canonical


def parse_iso(value: str) -> int:
    return canonical.parse_iso_to_s(value)


def archive_rows(
    db_path: Path,
    user: str,
    start_s: int,
    end_s: int,
) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT raw_json
            FROM activity_events
            WHERE lower(wallet)=lower(?)
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY timestamp, event_id
            """,
            (user, start_s, end_s),
        ).fetchall()
    finally:
        conn.close()
    out: list[dict[str, Any]] = []
    for (raw_json,) in rows:
        try:
            row = json.loads(raw_json)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--slug-prefix", action="append", default=[])
    parser.add_argument("--skip-positions", action="store_true")
    args = parser.parse_args()

    start_s = parse_iso(args.start_iso)
    end_s = parse_iso(args.end_iso)
    archived = archive_rows(args.db, args.user, start_s, end_s)

    def fetch_activity_rows(*_args, **_kwargs):
        return list(archived)

    canonical.fetch_activity_rows = fetch_activity_rows
    if args.skip_positions:
        canonical.fetch_positions = lambda *_args, **_kwargs: []

    original_argv = sys.argv
    sys.argv = [
        "analyze_xuan_public_activity_pnl.py",
        "--user",
        args.user,
        "--start-iso",
        args.start_iso,
        "--end-iso",
        args.end_iso,
        "--output-dir",
        str(args.output_dir),
    ]
    for prefix in args.slug_prefix:
        sys.argv.extend(["--slug-prefix", prefix])
    try:
        result = canonical.main()
    finally:
        sys.argv = original_argv

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "archive_source.json").write_text(
        json.dumps(
            {
                "source": "local_sqlite_activity_events",
                "db": str(args.db),
                "user": args.user,
                "start_iso": args.start_iso,
                "end_iso": args.end_iso,
                "archived_rows_read": len(archived),
                "positions_included": not args.skip_positions,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())

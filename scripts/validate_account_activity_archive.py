#!/usr/bin/env python3
"""Validate completeness and boundary conditions for a public activity archive."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path


def parse_iso(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso_s(timestamp: int | None) -> str | None:
    if timestamp is None:
        return None
    return dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--wallet", required=True)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    start_s = parse_iso(args.start_iso)
    end_s = parse_iso(args.end_iso)
    conn = sqlite3.connect(args.db)
    try:
        scope = (args.wallet, start_s, end_s)
        total, distinct, raw_invalid, min_ts, max_ts = conn.execute(
            """
            SELECT COUNT(*), COUNT(DISTINCT event_id),
                   COALESCE(SUM(CASE WHEN json_valid(raw_json)=0 THEN 1 ELSE 0 END), 0),
                   MIN(timestamp), MAX(timestamp)
            FROM activity_events
            WHERE lower(wallet)=lower(?) AND timestamp BETWEEN ? AND ?
            """,
            scope,
        ).fetchone()

        type_rows = conn.execute(
            """
            SELECT type, COUNT(*), COALESCE(SUM(usdc_size), 0.0)
            FROM activity_events
            WHERE lower(wallet)=lower(?) AND timestamp BETWEEN ? AND ?
            GROUP BY type ORDER BY type
            """,
            scope,
        ).fetchall()

        task_rows = conn.execute(
            "SELECT status, COUNT(*) FROM activity_scan_tasks WHERE lower(wallet)=lower(?) GROUP BY status ORDER BY status",
            (args.wallet,),
        ).fetchall()
        run_rows = conn.execute(
            "SELECT status, COUNT(*) FROM activity_scan_runs WHERE lower(wallet)=lower(?) GROUP BY status ORDER BY status",
            (args.wallet,),
        ).fetchall()

        no_buy_redeem_usdc, redeem_before_first_buy_usdc, redeem_count = conn.execute(
            """
            WITH first_buy AS (
              SELECT condition_id, MIN(timestamp) AS first_buy_ts
              FROM activity_events
              WHERE lower(wallet)=lower(?) AND timestamp BETWEEN ? AND ?
                AND type='TRADE' AND upper(COALESCE(side, ''))='BUY'
              GROUP BY condition_id
            )
            SELECT
              COALESCE(SUM(CASE WHEN r.type='REDEEM' AND b.first_buy_ts IS NULL
                               THEN COALESCE(r.usdc_size, 0.0) ELSE 0.0 END), 0.0),
              COALESCE(SUM(CASE WHEN r.type='REDEEM' AND b.first_buy_ts IS NOT NULL
                                      AND r.timestamp < b.first_buy_ts
                               THEN COALESCE(r.usdc_size, 0.0) ELSE 0.0 END), 0.0),
              COALESCE(SUM(CASE WHEN r.type='REDEEM' THEN 1 ELSE 0 END), 0)
            FROM activity_events r
            LEFT JOIN first_buy b ON b.condition_id = r.condition_id
            WHERE lower(r.wallet)=lower(?) AND r.timestamp BETWEEN ? AND ?
            """,
            (args.wallet, start_s, end_s, args.wallet, start_s, end_s),
        ).fetchone()

        active_runs = conn.execute(
            """
            SELECT COUNT(*) FROM activity_scan_runs
            WHERE lower(wallet)=lower(?) AND status='running'
            """,
            (args.wallet,),
        ).fetchone()[0]
    finally:
        conn.close()

    payload = {
        "status": "pass" if total == distinct and raw_invalid == 0 and active_runs == 0 else "fail",
        "wallet": args.wallet,
        "window": {
            "start_iso": args.start_iso,
            "end_iso": args.end_iso,
            "min_event_iso": iso_s(min_ts),
            "max_event_iso": iso_s(max_ts),
        },
        "events": {
            "rows": total,
            "distinct_event_ids": distinct,
            "raw_json_invalid": raw_invalid,
        },
        "activity_types": [
            {"type": row[0], "count": row[1], "usdc_sum": row[2]} for row in type_rows
        ],
        "scan_tasks_by_status": {row[0]: row[1] for row in task_rows},
        "scan_runs_by_status": {row[0]: row[1] for row in run_rows},
        "active_collection_runs": active_runs,
        "redeem_boundary": {
            "redeem_count": redeem_count,
            "no_buy_redeem_usdc": no_buy_redeem_usdc,
            "redeem_before_first_buy_usdc": redeem_before_first_buy_usdc,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

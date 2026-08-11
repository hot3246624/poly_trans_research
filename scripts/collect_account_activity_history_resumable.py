#!/usr/bin/env python3
"""Resumable fee-inclusive public activity archive for one Polymarket account.

The public activity endpoint has a bounded historical offset. This collector
avoids relying on deep offsets by scanning disjoint time ranges and splitting a
range when its page budget is exhausted. Every successful page is committed to
SQLite before the next request, so rate limits or connection resets do not
discard completed work.

This is public-data research only. It records activity.usdcSize for accounting,
but cannot establish authenticated maker/taker side, queue priority, or order
placement truth.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import http.client
import json
import random
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "https://data-api.polymarket.com/activity"
PAGE_LIMIT = 500
MAX_OFFSET = 3000
UA = {
    "User-Agent": "Mozilla/5.0 (account-activity-history-resumable; read-only)",
    "Accept": "application/json",
}


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def iso_s(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def event_id(wallet: str, row: dict[str, Any]) -> str:
    # Do not deduplicate by transaction hash alone: multiple fills can share it.
    fields = (
        wallet.lower(),
        row.get("transactionHash"),
        row.get("timestamp"),
        row.get("type"),
        row.get("side"),
        row.get("conditionId"),
        row.get("asset"),
        row.get("outcomeIndex"),
        row.get("outcome"),
        row.get("size"),
        row.get("price"),
        row.get("usdcSize"),
        row.get("slug"),
    )
    return hashlib.sha256("|".join("" if x is None else str(x) for x in fields).encode()).hexdigest()


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_events (
          event_id TEXT PRIMARY KEY,
          wallet TEXT NOT NULL,
          timestamp INTEGER,
          type TEXT NOT NULL,
          side TEXT,
          slug TEXT,
          condition_id TEXT,
          outcome_index INTEGER,
          outcome TEXT,
          asset TEXT,
          size REAL,
          price REAL,
          usdc_size REAL,
          transaction_hash TEXT,
          raw_json TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL,
          last_seen_utc TEXT NOT NULL,
          source TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_wallet_ts ON activity_events(wallet, timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_wallet_type_ts ON activity_events(wallet, type, timestamp)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_scan_tasks (
          task_id INTEGER PRIMARY KEY AUTOINCREMENT,
          wallet TEXT NOT NULL,
          activity_type TEXT NOT NULL,
          start_s INTEGER NOT NULL,
          end_s INTEGER NOT NULL,
          next_offset INTEGER NOT NULL DEFAULT 0,
          status TEXT NOT NULL DEFAULT 'pending',
          fetched_rows INTEGER NOT NULL DEFAULT 0,
          inserted_rows INTEGER NOT NULL DEFAULT 0,
          last_error TEXT,
          updated_utc TEXT NOT NULL,
          UNIQUE(wallet, activity_type, start_s, end_s)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_scan_runs (
          run_id INTEGER PRIMARY KEY AUTOINCREMENT,
          wallet TEXT NOT NULL,
          start_s INTEGER NOT NULL,
          end_s INTEGER NOT NULL,
          started_utc TEXT NOT NULL,
          finished_utc TEXT,
          status TEXT NOT NULL,
          pages INTEGER NOT NULL DEFAULT 0,
          fetched_rows INTEGER NOT NULL DEFAULT 0,
          inserted_rows INTEGER NOT NULL DEFAULT 0,
          errors INTEGER NOT NULL DEFAULT 0,
          last_error TEXT
        )
        """
    )
    conn.commit()


def upsert_rows(conn: sqlite3.Connection, wallet: str, rows: list[dict[str, Any]], source: str) -> int:
    seen = now_iso()
    inserted = 0
    for row in rows:
        eid = event_id(wallet, row)
        values = {
            "event_id": eid,
            "wallet": wallet.lower(),
            "timestamp": row.get("timestamp"),
            "type": str(row.get("type") or "").upper(),
            "side": row.get("side"),
            "slug": row.get("slug") or row.get("eventSlug"),
            "condition_id": row.get("conditionId"),
            "outcome_index": row.get("outcomeIndex"),
            "outcome": row.get("outcome"),
            "asset": row.get("asset"),
            "size": row.get("size"),
            "price": row.get("price"),
            "usdc_size": row.get("usdcSize"),
            "transaction_hash": row.get("transactionHash"),
            "raw_json": json.dumps(row, separators=(",", ":"), ensure_ascii=False),
            "first_seen_utc": seen,
            "last_seen_utc": seen,
            "source": source,
        }
        cur = conn.execute(
            """
            INSERT INTO activity_events (
              event_id, wallet, timestamp, type, side, slug, condition_id,
              outcome_index, outcome, asset, size, price, usdc_size,
              transaction_hash, raw_json, first_seen_utc, last_seen_utc, source
            ) VALUES (
              :event_id, :wallet, :timestamp, :type, :side, :slug, :condition_id,
              :outcome_index, :outcome, :asset, :size, :price, :usdc_size,
              :transaction_hash, :raw_json, :first_seen_utc, :last_seen_utc, :source
            )
            ON CONFLICT(event_id) DO NOTHING
            """,
            values,
        )
        if cur.rowcount == 1:
            inserted += 1
    return inserted


def http_json(
    params: dict[str, Any],
    *,
    timeout: int,
    pause_s: float,
    max_retries: int,
) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(f"{API_URL}?{query}", headers=UA)
    last: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            with urllib.request.urlopen(request, timeout=max(timeout, 30)) as response:
                payload = response.read().decode()
            data = json.loads(payload)
            if not isinstance(data, list):
                data = data.get("data", []) if isinstance(data, dict) else []
            if pause_s:
                time.sleep(pause_s)
            return [row for row in data if isinstance(row, dict)]
        except urllib.error.HTTPError as exc:
            last = exc
            retryable = exc.code in {408, 425, 429, 500, 502, 503, 504}
            if not retryable:
                raise
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            try:
                server_delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                server_delay = 0.0
            delay = max(server_delay, min(120.0, 1.5 * (2**attempt)))
        except (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last = exc
            delay = min(120.0, 1.5 * (2**attempt))
        if attempt < max_retries:
            time.sleep(delay + random.uniform(0.0, 0.75))
    raise RuntimeError(f"activity request failed params={params} error={last!r}")


def ensure_root_tasks(
    conn: sqlite3.Connection,
    wallet: str,
    start_s: int,
    end_s: int,
    activity_types: list[str],
    chunk_hours: int,
) -> None:
    chunk_s = max(3600, chunk_hours * 3600)
    for activity_type in activity_types:
        cursor = start_s
        while cursor <= end_s:
            chunk_end = min(end_s, cursor + chunk_s - 1)
            conn.execute(
                """
                INSERT OR IGNORE INTO activity_scan_tasks
                (wallet, activity_type, start_s, end_s, updated_utc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (wallet.lower(), activity_type, cursor, chunk_end, now_iso()),
            )
            cursor = chunk_end + 1
    conn.commit()


def split_task(conn: sqlite3.Connection, task: sqlite3.Row) -> None:
    start_s = int(task[3])
    end_s = int(task[4])
    if start_s >= end_s:
        raise RuntimeError(
            f"offset cap at one-second range type={task[2]} start={start_s} end={end_s}"
        )
    mid = start_s + (end_s - start_s) // 2
    stamp = now_iso()
    conn.execute(
        "UPDATE activity_scan_tasks SET status='split', last_error=?, updated_utc=? WHERE task_id=?",
        (f"offset_cap_split_at_{task[5]}", stamp, task[0]),
    )
    for left, right in ((start_s, mid), (mid + 1, end_s)):
        conn.execute(
            """
            INSERT OR IGNORE INTO activity_scan_tasks
            (wallet, activity_type, start_s, end_s, updated_utc)
            VALUES (?, ?, ?, ?, ?)
            """,
            (task[1], task[2], left, right, stamp),
        )
    conn.commit()


def collect(
    conn: sqlite3.Connection,
    wallet: str,
    run_id: int,
    *,
    timeout: int,
    pause_s: float,
    max_retries: int,
    max_errors_per_task: int,
) -> dict[str, int]:
    pages = fetched = inserted = errors = 0
    while True:
        task = conn.execute(
            """
            SELECT task_id, wallet, activity_type, start_s, end_s, next_offset,
                   status, fetched_rows, inserted_rows, last_error
            FROM activity_scan_tasks
            WHERE wallet=? AND status IN ('pending', 'running', 'error')
            ORDER BY start_s, activity_type, task_id
            LIMIT 1
            """,
            (wallet.lower(),),
        ).fetchone()
        if task is None:
            break
        task_id, _, activity_type, start_s, end_s, offset, status, task_fetched, task_inserted, _ = task
        conn.execute(
            "UPDATE activity_scan_tasks SET status='running', last_error=NULL, updated_utc=? WHERE task_id=?",
            (now_iso(), task_id),
        )
        conn.commit()
        while True:
            params = {
                "user": wallet,
                "type": activity_type,
                "start": int(start_s),
                "end": int(end_s),
                "limit": PAGE_LIMIT,
                "offset": int(offset),
            }
            try:
                rows = http_json(params, timeout=timeout, pause_s=pause_s, max_retries=max_retries)
            except Exception as exc:
                errors += 1
                task_fetched_local = int(task_fetched)
                conn.execute(
                    """
                    UPDATE activity_scan_tasks
                    SET status='error', last_error=?, updated_utc=?
                    WHERE task_id=?
                    """,
                    (repr(exc), now_iso(), task_id),
                )
                conn.execute(
                    "UPDATE activity_scan_runs SET errors=?, last_error=? WHERE run_id=?",
                    (errors, repr(exc), run_id),
                )
                conn.commit()
                print(
                    f"[error] task={task_id} type={activity_type} range={iso_s(start_s)}..{iso_s(end_s)} "
                    f"offset={offset} error={exc!r}",
                    file=sys.stderr,
                    flush=True,
                )
                break

            pages += 1
            page_count = len(rows)
            if rows:
                page_inserted = upsert_rows(conn, wallet, rows, f"activity:{activity_type}:{start_s}:{end_s}")
                conn.commit()
            else:
                page_inserted = 0
            fetched += page_count
            inserted += page_inserted
            task_fetched += page_count
            task_inserted += page_inserted
            conn.execute(
                """
                UPDATE activity_scan_tasks
                SET next_offset=?, fetched_rows=?, inserted_rows=?, updated_utc=?
                WHERE task_id=?
                """,
                (int(offset) + page_count, int(task_fetched), int(task_inserted), now_iso(), task_id),
            )
            conn.execute(
                "UPDATE activity_scan_runs SET pages=?, fetched_rows=?, inserted_rows=? WHERE run_id=?",
                (pages, fetched, inserted, run_id),
            )
            conn.commit()
            print(
                f"[page] task={task_id} type={activity_type} range={iso_s(start_s)}..{iso_s(end_s)} "
                f"offset={offset} rows={page_count} total_events={conn.execute('SELECT COUNT(*) FROM activity_events WHERE wallet=?', (wallet.lower(),)).fetchone()[0]}",
                file=sys.stderr,
                flush=True,
            )
            if page_count < PAGE_LIMIT:
                conn.execute(
                    "UPDATE activity_scan_tasks SET status='done', updated_utc=? WHERE task_id=?",
                    (now_iso(), task_id),
                )
                conn.commit()
                break
            offset += PAGE_LIMIT
            if offset > MAX_OFFSET:
                task_now = conn.execute(
                    """
                    SELECT task_id, wallet, activity_type, start_s, end_s, next_offset,
                           status, fetched_rows, inserted_rows, last_error
                    FROM activity_scan_tasks WHERE task_id=?
                    """,
                    (task_id,),
                ).fetchone()
                split_task(conn, task_now)
                break
        # Error tasks are retried on the next outer loop. Stop after one full
        # pass of errors so the process is resumable rather than a hot loop.
        if conn.execute(
            "SELECT COUNT(*) FROM activity_scan_tasks WHERE wallet=? AND status='error'", (wallet.lower(),)
        ).fetchone()[0]:
            break
    return {"pages": pages, "fetched_rows": fetched, "inserted_rows": inserted, "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--chunk-hours", type=int, default=24)
    parser.add_argument(
        "--types",
        default="TRADE,MERGE,REDEEM,MAKER_REBATE,REWARD,REFERRAL_REWARD",
        help="Comma-separated activity types.",
    )
    parser.add_argument("--pause", type=float, default=0.75)
    parser.add_argument("--timeout", type=int, default=45)
    parser.add_argument("--max-retries", type=int, default=12)
    args = parser.parse_args()

    wallet = args.user.lower()
    start_s = parse_iso(args.start_iso)
    end_s = parse_iso(args.end_iso)
    if end_s < start_s:
        raise SystemExit("end must be >= start")
    activity_types = [x.strip().upper() for x in args.types.split(",") if x.strip()]
    args.db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    ensure_root_tasks(conn, wallet, start_s, end_s, activity_types, args.chunk_hours)
    run_id = conn.execute(
        """
        INSERT INTO activity_scan_runs (wallet, start_s, end_s, started_utc, status)
        VALUES (?, ?, ?, ?, 'running')
        """,
        (wallet, start_s, end_s, now_iso()),
    ).lastrowid
    conn.commit()
    try:
        result = collect(
            conn,
            wallet,
            int(run_id),
            timeout=args.timeout,
            pause_s=args.pause,
            max_retries=args.max_retries,
            max_errors_per_task=1,
        )
        remaining = conn.execute(
            "SELECT COUNT(*) FROM activity_scan_tasks WHERE wallet=? AND status IN ('pending','running','error')",
            (wallet,),
        ).fetchone()[0]
        status = "complete" if remaining == 0 else "paused_with_errors"
        conn.execute(
            "UPDATE activity_scan_runs SET finished_utc=?, status=?, pages=?, fetched_rows=?, inserted_rows=? WHERE run_id=?",
            (now_iso(), status, result["pages"], result["fetched_rows"], result["inserted_rows"], run_id),
        )
        conn.commit()
        total = conn.execute("SELECT COUNT(*) FROM activity_events WHERE wallet=?", (wallet,)).fetchone()[0]
        print(
            json.dumps(
                {
                    "db": str(args.db),
                    "run_id": run_id,
                    "status": status,
                    "remaining_tasks": remaining,
                    "account_events": total,
                    **result,
                },
                indent=2,
            )
        )
        return 0 if status == "complete" else 2
    except Exception as exc:
        conn.execute(
            "UPDATE activity_scan_runs SET finished_utc=?, status='failed', last_error=? WHERE run_id=?",
            (now_iso(), repr(exc), run_id),
        )
        conn.commit()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())

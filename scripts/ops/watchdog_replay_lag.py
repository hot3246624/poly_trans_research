#!/usr/bin/env python3
"""Watchdog: restart sidecar when md_book_l1 is stale."""

from __future__ import annotations

import argparse
import shlex
import sqlite3
import subprocess
import time
from pathlib import Path


def max_recv_ms(db_path: Path) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(recv_ms) FROM md_book_l1").fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    finally:
        conn.close()


def main() -> int:
    p = argparse.ArgumentParser(description="Restart command when replay book stream is stale")
    p.add_argument("--db-path", required=True)
    p.add_argument("--max-stale-sec", type=int, default=300)
    p.add_argument("--restart-cmd", required=True, help="Command executed when stale")
    args = p.parse_args()

    db = Path(args.db_path)
    if not db.exists():
        print(f"[watchdog] db not found: {db}")
        return 2

    latest = max_recv_ms(db)
    now_ms = int(time.time() * 1000)
    stale_sec = (now_ms - latest) / 1000 if latest > 0 else 1e18
    print(f"[watchdog] latest_recv_ms={latest} stale_sec={stale_sec:.1f}")

    if stale_sec <= max(1, int(args.max_stale_sec)):
        return 0

    cmd = args.restart_cmd
    print(f"[watchdog] stale>{args.max_stale_sec}s, restarting: {cmd}")
    proc = subprocess.run(cmd, shell=True)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())

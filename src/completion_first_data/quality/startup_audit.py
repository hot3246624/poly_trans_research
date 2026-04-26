"""Startup acceptance audit for capture/replay readiness."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Dict


@dataclasses.dataclass(slots=True)
class StartupAuditReport:
    replay_db: str
    md_trades_rows: int
    taker_side_null_rows: int
    taker_side_null_ratio: float
    md_book_rows: int
    yes_bid_sz_null_rows: int
    yes_ask_sz_null_rows: int
    no_bid_sz_null_rows: int
    no_ask_sz_null_rows: int
    avg_trade_latency_ms: float
    market_meta_rounds: int
    settlement_rows: int
    xuan_trades_poll_points: int
    xuan_activity_poll_points: int
    xuan_trades_rows: int
    xuan_activity_rows: int
    pass_taker_side: bool
    pass_book_sizes: bool
    pass_trade_latency: bool
    pass_round_count: bool
    pass_settlement: bool
    pass_xuan_poll_points: bool
    all_passed: bool

    def as_dict(self) -> Dict[str, object]:
        return dataclasses.asdict(self)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone()
    return bool(row)


def run_startup_audit(
    db_path: Path,
    *,
    taker_side_null_max_ratio: float = 0.05,
    min_market_meta_rounds: int = 12,
    min_settlement_rows: int = 1,
    min_xuan_poll_points: int = 12,
    max_abs_avg_trade_latency_ms: int = 60_000,
) -> StartupAuditReport:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    md_trades_rows = int(cur.execute("SELECT COUNT(*) FROM md_trades").fetchone()[0]) if _table_exists(conn, "md_trades") else 0
    taker_side_null_rows = (
        int(
            cur.execute(
                "SELECT COUNT(*) FROM md_trades WHERE taker_side IS NULL OR TRIM(COALESCE(taker_side,''))=''"
            ).fetchone()[0]
        )
        if md_trades_rows > 0
        else 0
    )
    taker_side_null_ratio = (taker_side_null_rows / md_trades_rows) if md_trades_rows > 0 else 1.0

    md_book_rows = int(cur.execute("SELECT COUNT(*) FROM md_book_l1").fetchone()[0]) if _table_exists(conn, "md_book_l1") else 0
    yes_bid_sz_null_rows = int(cur.execute("SELECT COUNT(*) FROM md_book_l1 WHERE yes_bid_sz IS NULL").fetchone()[0]) if md_book_rows > 0 else 0
    yes_ask_sz_null_rows = int(cur.execute("SELECT COUNT(*) FROM md_book_l1 WHERE yes_ask_sz IS NULL").fetchone()[0]) if md_book_rows > 0 else 0
    no_bid_sz_null_rows = int(cur.execute("SELECT COUNT(*) FROM md_book_l1 WHERE no_bid_sz IS NULL").fetchone()[0]) if md_book_rows > 0 else 0
    no_ask_sz_null_rows = int(cur.execute("SELECT COUNT(*) FROM md_book_l1 WHERE no_ask_sz IS NULL").fetchone()[0]) if md_book_rows > 0 else 0

    avg_latency_row = (
        cur.execute(
            "SELECT AVG(recv_ms - trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL"
        ).fetchone()
        if md_trades_rows > 0
        else None
    )
    avg_trade_latency_ms = float(avg_latency_row[0]) if avg_latency_row and avg_latency_row[0] is not None else 0.0

    market_meta_rounds = int(cur.execute("SELECT COUNT(DISTINCT condition_id) FROM market_meta").fetchone()[0]) if _table_exists(conn, "market_meta") else 0
    settlement_rows = int(cur.execute("SELECT COUNT(*) FROM settlement_records").fetchone()[0]) if _table_exists(conn, "settlement_records") else 0

    xuan_trades_rows = int(cur.execute("SELECT COUNT(*) FROM xuan_trades").fetchone()[0]) if _table_exists(conn, "xuan_trades") else 0
    xuan_activity_rows = int(cur.execute("SELECT COUNT(*) FROM xuan_activity").fetchone()[0]) if _table_exists(conn, "xuan_activity") else 0

    xuan_trades_poll_points = (
        int(cur.execute("SELECT COUNT(DISTINCT poll_ts_ms) FROM xuan_trades").fetchone()[0]) if xuan_trades_rows > 0 else 0
    )
    xuan_activity_poll_points = (
        int(cur.execute("SELECT COUNT(DISTINCT poll_ts_ms) FROM xuan_activity").fetchone()[0]) if xuan_activity_rows > 0 else 0
    )

    # Use poll-log heartbeat as an upper-bound signal for "worker ran",
    # because some successful polls can legitimately return zero rows.
    if _table_exists(conn, "xuan_poll_log"):
        log_trades_poll_points = int(
            cur.execute(
                "SELECT COUNT(DISTINCT poll_ts_ms) FROM xuan_poll_log WHERE endpoint='trades' AND ok=1"
            ).fetchone()[0]
        )
        log_activity_poll_points = int(
            cur.execute(
                "SELECT COUNT(DISTINCT poll_ts_ms) FROM xuan_poll_log WHERE endpoint='activity' AND ok=1"
            ).fetchone()[0]
        )
        xuan_trades_poll_points = max(xuan_trades_poll_points, log_trades_poll_points)
        xuan_activity_poll_points = max(xuan_activity_poll_points, log_activity_poll_points)

    conn.close()

    pass_taker_side = md_trades_rows > 0 and taker_side_null_ratio <= taker_side_null_max_ratio
    pass_book_sizes = (
        md_book_rows > 0
        and yes_bid_sz_null_rows == 0
        and yes_ask_sz_null_rows == 0
        and no_bid_sz_null_rows == 0
        and no_ask_sz_null_rows == 0
    )
    pass_trade_latency = abs(avg_trade_latency_ms) <= max_abs_avg_trade_latency_ms
    pass_round_count = market_meta_rounds >= min_market_meta_rounds
    pass_settlement = settlement_rows >= min_settlement_rows
    pass_xuan_poll_points = (
        xuan_trades_poll_points >= min_xuan_poll_points
        and xuan_activity_poll_points >= min_xuan_poll_points
    )

    all_passed = all(
        [
            pass_taker_side,
            pass_book_sizes,
            pass_trade_latency,
            pass_round_count,
            pass_settlement,
            pass_xuan_poll_points,
        ]
    )

    return StartupAuditReport(
        replay_db=str(db_path),
        md_trades_rows=md_trades_rows,
        taker_side_null_rows=taker_side_null_rows,
        taker_side_null_ratio=round(taker_side_null_ratio, 6),
        md_book_rows=md_book_rows,
        yes_bid_sz_null_rows=yes_bid_sz_null_rows,
        yes_ask_sz_null_rows=yes_ask_sz_null_rows,
        no_bid_sz_null_rows=no_bid_sz_null_rows,
        no_ask_sz_null_rows=no_ask_sz_null_rows,
        avg_trade_latency_ms=round(avg_trade_latency_ms, 4),
        market_meta_rounds=market_meta_rounds,
        settlement_rows=settlement_rows,
        xuan_trades_poll_points=xuan_trades_poll_points,
        xuan_activity_poll_points=xuan_activity_poll_points,
        xuan_trades_rows=xuan_trades_rows,
        xuan_activity_rows=xuan_activity_rows,
        pass_taker_side=pass_taker_side,
        pass_book_sizes=pass_book_sizes,
        pass_trade_latency=pass_trade_latency,
        pass_round_count=pass_round_count,
        pass_settlement=pass_settlement,
        pass_xuan_poll_points=pass_xuan_poll_points,
        all_passed=all_passed,
    )


def save_startup_audit_report(report: StartupAuditReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

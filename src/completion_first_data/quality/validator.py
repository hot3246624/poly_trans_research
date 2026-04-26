"""Replay quality checks for BTC 5m public-capture acceptance."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
from pathlib import Path
from typing import Dict, Tuple

from ..constants import DEFAULT_GAP_THRESHOLD_MS


@dataclasses.dataclass(slots=True)
class ValidationReport:
    replay_db: str
    conditions_total: int
    market_meta_rows: int
    md_book_rows: int
    md_trades_rows: int
    market_meta_coverage_pct: float
    md_book_round_coverage_pct: float
    md_trades_round_coverage_pct: float
    quiet_round_count: int
    gap_violation_count: int
    max_gap_ms_observed: int
    pass_market_meta: bool
    pass_md_book_round_coverage: bool
    pass_md_trades_round_coverage: bool
    pass_non_empty: bool
    pass_gap: bool
    all_passed: bool

    def as_dict(self) -> Dict[str, object]:
        return dataclasses.asdict(self)


def _fetch_set(conn: sqlite3.Connection, sql: str) -> set[str]:
    rows = conn.execute(sql).fetchall()
    return {str(r[0]) for r in rows if r[0]}


def _safe_pct(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round((num / den) * 100.0, 4)


def _get_gap_stats(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> Tuple[bool, int]:
    rows = conn.execute(
        """
        SELECT recv_ms FROM md_book_l1
        WHERE condition_id=? AND recv_ms BETWEEN ? AND ?
        UNION ALL
        SELECT recv_ms FROM md_trades
        WHERE condition_id=? AND recv_ms BETWEEN ? AND ?
        ORDER BY recv_ms ASC
        """,
        (condition_id, start_ms, end_ms, condition_id, start_ms, end_ms),
    ).fetchall()

    if not rows:
        return True, end_ms - start_ms

    times = [start_ms] + [int(r[0]) for r in rows] + [end_ms]
    max_gap = 0
    for i in range(1, len(times)):
        gap = times[i] - times[i - 1]
        if gap > max_gap:
            max_gap = gap
    return False, max_gap


def validate_replay_db(db_path: Path, gap_threshold_ms: int = DEFAULT_GAP_THRESHOLD_MS) -> ValidationReport:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    meta_conditions = _fetch_set(conn, "SELECT DISTINCT condition_id FROM market_meta")
    book_conditions = _fetch_set(conn, "SELECT DISTINCT condition_id FROM md_book_l1")
    trade_conditions = _fetch_set(conn, "SELECT DISTINCT condition_id FROM md_trades")

    conditions_total = len(meta_conditions)
    market_meta_rows = int(conn.execute("SELECT COUNT(*) FROM market_meta").fetchone()[0])
    md_book_rows = int(conn.execute("SELECT COUNT(*) FROM md_book_l1").fetchone()[0])
    md_trades_rows = int(conn.execute("SELECT COUNT(*) FROM md_trades").fetchone()[0])

    market_meta_coverage_pct = 100.0 if conditions_total > 0 else 0.0
    md_book_round_coverage_pct = _safe_pct(len(book_conditions & meta_conditions), conditions_total)
    md_trades_round_coverage_pct = _safe_pct(len(trade_conditions & meta_conditions), conditions_total)

    quiet_round_count = 0
    gap_violation_count = 0
    max_gap_ms_observed = 0

    if gap_threshold_ms > 0:
        for row in conn.execute("SELECT condition_id, start_ms, end_ms FROM market_meta"):
            condition_id = row["condition_id"]
            window_start = int(row["start_ms"]) - 60_000
            window_end = int(row["end_ms"]) + 120_000
            quiet, max_gap = _get_gap_stats(conn, condition_id, window_start, window_end)
            if quiet:
                quiet_round_count += 1
                continue
            if max_gap > gap_threshold_ms:
                gap_violation_count += 1
            if max_gap > max_gap_ms_observed:
                max_gap_ms_observed = max_gap

    conn.close()

    pass_market_meta = conditions_total > 0 and market_meta_coverage_pct >= 100.0
    pass_md_book_round_coverage = md_book_round_coverage_pct >= 95.0
    pass_md_trades_round_coverage = md_trades_round_coverage_pct >= 95.0
    pass_non_empty = md_book_rows > 0 and md_trades_rows > 0
    pass_gap = True if gap_threshold_ms <= 0 else gap_violation_count == 0

    all_passed = all(
        [
            pass_market_meta,
            pass_md_book_round_coverage,
            pass_md_trades_round_coverage,
            pass_non_empty,
            pass_gap,
        ]
    )

    return ValidationReport(
        replay_db=str(db_path),
        conditions_total=conditions_total,
        market_meta_rows=market_meta_rows,
        md_book_rows=md_book_rows,
        md_trades_rows=md_trades_rows,
        market_meta_coverage_pct=market_meta_coverage_pct,
        md_book_round_coverage_pct=md_book_round_coverage_pct,
        md_trades_round_coverage_pct=md_trades_round_coverage_pct,
        quiet_round_count=quiet_round_count,
        gap_violation_count=gap_violation_count,
        max_gap_ms_observed=max_gap_ms_observed,
        pass_market_meta=pass_market_meta,
        pass_md_book_round_coverage=pass_md_book_round_coverage,
        pass_md_trades_round_coverage=pass_md_trades_round_coverage,
        pass_non_empty=pass_non_empty,
        pass_gap=pass_gap,
        all_passed=all_passed,
    )


def save_report(report: ValidationReport, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

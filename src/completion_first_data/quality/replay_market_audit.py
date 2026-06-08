"""Market-side replay trust audit.

This audit is intentionally read-only for replay/raw data. It proves whether
the market-side replay DB can be used as the primary research/backtest source.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..capture.envelope import RawEnvelope
from ..replay.normalize import dedup_book_key, dedup_trade_key, normalize_book_row, normalize_md_trade
from ..utils.io import iter_jsonl_gz

PLANNED_OUTAGE_WINDOWS_MS = (
    (
        int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000),
        int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000),
    ),
)

BTC_SLUG_RE = re.compile(r"^btc-updown-5m-(\d+)$")


@dataclasses.dataclass(slots=True)
class AuditConfig:
    raw_root: Path
    replay_root: Path
    days: Sequence[str]
    min_db_bytes: int = 100 * 1024 * 1024
    raw_trade_max_records: int = 1_000_000
    raw_book_max_records: int = 250_000
    taker_side_null_max_ratio: float = 0.05
    trusted_start_ms: Optional[int] = None
    outcome_symbols: Sequence[str] = ("BTC", "ETH", "SOL", "XRP")
    min_official_outcome_coverage: float = 0.99


def _pct(num: int, den: int) -> float:
    if den <= 0:
        return 0.0
    return round(num / den, 6)


def _status(has_fail: bool, has_warning: bool = False) -> str:
    if has_fail:
        return "fail"
    if has_warning:
        return "warning"
    return "pass"


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _one(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return row[0]


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    return int(_one(conn, f"SELECT COUNT(*) FROM {table}") or 0)


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(_one(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)))


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.Error:
        return set()
    return {str(r["name"]) for r in rows}


def _slug_epoch(slug: str) -> Optional[int]:
    match = BTC_SLUG_RE.match(slug or "")
    if not match:
        return None
    return int(match.group(1))


def _planned_outage_gap(prev_start_ms: int, next_start_ms: int) -> bool:
    missing_start = prev_start_ms + 300_000
    missing_end = next_start_ms - 300_000
    if missing_start > missing_end:
        return False
    for outage_start, outage_end in PLANNED_OUTAGE_WINDOWS_MS:
        if missing_start >= outage_start and missing_end <= outage_end:
            return True
    return False


def _raw_path(raw_root: Path, day: str, source: str, channel: str) -> Path:
    return raw_root / day / source / f"{channel}.jsonl.gz"


def _iter_raw_envelopes(path: Path, max_records: int) -> Iterable[RawEnvelope]:
    if not path.exists() or max_records <= 0:
        return
    seen = 0
    for item in iter_jsonl_gz(path):
        yield RawEnvelope.from_dict(item)
        seen += 1
        if seen >= max_records:
            return


def _select_btc_samples(btc_rows: List[sqlite3.Row]) -> List[str]:
    if not btc_rows:
        return []
    rows = sorted(btc_rows, key=lambda r: int(r["start_ms"]))
    idxs = {0, len(rows) // 2, len(rows) - 1}
    return [str(rows[i]["condition_id"]) for i in sorted(idxs)]


def _audit_schema_core(conn: sqlite3.Connection, db_path: Path, min_db_bytes: int) -> Dict[str, Any]:
    db_size = db_path.stat().st_size if db_path.exists() else 0
    symbol_rows = conn.execute(
        "SELECT symbol, COUNT(*) AS n FROM market_meta GROUP BY symbol ORDER BY symbol"
    ).fetchall()
    symbol_counts = {str(r["symbol"]): int(r["n"]) for r in symbol_rows}

    book_rows = _table_count(conn, "md_book_l1")
    trade_rows = _table_count(conn, "md_trades")
    size_nulls = {
        col: int(_one(conn, f"SELECT COUNT(*) FROM md_book_l1 WHERE {col} IS NULL") or 0)
        for col in ("yes_bid_sz", "yes_ask_sz", "no_bid_sz", "no_ask_sz")
    }
    price_nulls = {
        col: int(_one(conn, f"SELECT COUNT(*) FROM md_book_l1 WHERE {col} IS NULL") or 0)
        for col in ("yes_bid_px", "yes_ask_px", "no_bid_px", "no_ask_px")
    }
    trade_ts_nulls = int(_one(conn, "SELECT COUNT(*) FROM md_trades WHERE trade_ts_ms IS NULL") or 0)
    taker_side_nulls = int(
        _one(conn, "SELECT COUNT(*) FROM md_trades WHERE taker_side IS NULL OR TRIM(COALESCE(taker_side,''))=''")
        or 0
    )
    latency = conn.execute(
        """
        SELECT AVG(recv_ms - trade_ts_ms) AS avg_latency,
               MAX(ABS(recv_ms - trade_ts_ms)) AS max_abs_latency
        FROM md_trades
        WHERE trade_ts_ms IS NOT NULL
        """
    ).fetchone()
    settlement_rows = _table_count(conn, "settlement_records")
    book_l2_rows = _table_count(conn, "md_book_l2") if _table_exists(conn, "md_book_l2") else 0
    book_l2_side_rows: Dict[str, int] = {}
    if book_l2_rows > 0:
        rows = conn.execute("SELECT market_side, COUNT(*) AS n FROM md_book_l2 GROUP BY market_side").fetchall()
        book_l2_side_rows = {str(r["market_side"]): int(r["n"]) for r in rows}
    bounds = conn.execute(
        """
        SELECT
          (SELECT MIN(recv_ms) FROM md_book_l1) AS min_book_recv_ms,
          (SELECT MAX(recv_ms) FROM md_book_l1) AS max_book_recv_ms,
          (SELECT MIN(trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL) AS min_trade_ts_ms,
          (SELECT MAX(trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL) AS max_trade_ts_ms
        """
    ).fetchone()

    return {
        "db_size_bytes": db_size,
        "db_size_ok": db_size >= min_db_bytes,
        "market_meta_rows": _table_count(conn, "market_meta"),
        "symbol_counts": symbol_counts,
        "md_book_l1_rows": book_rows,
        "md_book_l2_rows": book_l2_rows,
        "md_book_l2_side_rows": book_l2_side_rows,
        "md_trades_rows": trade_rows,
        "book_size_nulls": size_nulls,
        "book_size_null_rates": {k: _pct(v, book_rows) for k, v in size_nulls.items()},
        "book_price_nulls": price_nulls,
        "book_price_null_rates": {k: _pct(v, book_rows) for k, v in price_nulls.items()},
        "trade_ts_ms_null_rows": trade_ts_nulls,
        "trade_ts_ms_null_rate": _pct(trade_ts_nulls, trade_rows),
        "taker_side_null_rows": taker_side_nulls,
        "taker_side_null_rate": _pct(taker_side_nulls, trade_rows),
        "avg_trade_latency_ms": round(float(latency["avg_latency"] or 0.0), 4) if latency else 0.0,
        "max_abs_trade_latency_ms": int(latency["max_abs_latency"] or 0) if latency else 0,
        "settlement_rows": settlement_rows,
        "capture_bounds": {
            "min_book_recv_ms": int(bounds["min_book_recv_ms"] or 0) if bounds else 0,
            "max_book_recv_ms": int(bounds["max_book_recv_ms"] or 0) if bounds else 0,
            "min_trade_ts_ms": int(bounds["min_trade_ts_ms"] or 0) if bounds else 0,
            "max_trade_ts_ms": int(bounds["max_trade_ts_ms"] or 0) if bounds else 0,
        },
    }


def _audit_btc_continuity(conn: sqlite3.Connection, *, trusted_start_ms: Optional[int] = None) -> Dict[str, Any]:
    where = "WHERE symbol='BTC'"
    params: Tuple[Any, ...] = ()
    if trusted_start_ms is not None:
        where += " AND end_ms > ?"
        params = (trusted_start_ms,)
    rows = conn.execute(
        f"""
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        {where}
        ORDER BY start_ms
        """,
        params,
    ).fetchall()
    gaps: List[Dict[str, Any]] = []
    planned_gaps: List[Dict[str, Any]] = []
    for prev, cur in zip(rows, rows[1:]):
        prev_start = int(prev["start_ms"])
        cur_start = int(cur["start_ms"])
        if cur_start - prev_start == 300_000:
            continue
        item = {
            "prev_slug": prev["slug"],
            "next_slug": cur["slug"],
            "gap_ms": cur_start - prev_start,
        }
        if _planned_outage_gap(prev_start, cur_start):
            planned_gaps.append(item)
        else:
            gaps.append(item)

    bad_slug_rows = [
        {"condition_id": str(r["condition_id"]), "slug": str(r["slug"])}
        for r in rows
        if _slug_epoch(str(r["slug"])) is None
    ]
    return {
        "btc_rounds": len(rows),
        "nonplanned_gap_count": len(gaps),
        "planned_gap_count": len(planned_gaps),
        "nonplanned_gaps": gaps[:20],
        "planned_gaps": planned_gaps[:20],
        "bad_slug_count": len(bad_slug_rows),
        "bad_slugs": bad_slug_rows[:20],
    }


def _audit_btc_book_coverage(conn: sqlite3.Connection, *, trusted_start_ms: Optional[int] = None) -> Dict[str, Any]:
    where = "WHERE m.symbol='BTC'"
    params: Tuple[Any, ...] = ()
    if trusted_start_ms is not None:
        where += " AND m.end_ms > ?"
        params = (trusted_start_ms,)
    rows = conn.execute(
        f"""
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms,
               MIN(b.recv_ms) AS first_book_ms,
               MAX(b.recv_ms) AS last_book_ms,
               COUNT(b.id) AS book_rows
        FROM market_meta m
        LEFT JOIN md_book_l1 b ON b.condition_id=m.condition_id
        {where}
        GROUP BY m.condition_id
        ORDER BY m.start_ms
        """,
        params,
    ).fetchall()
    if not rows:
        return {"btc_book_coverage_rows": 0, "missing_book_rounds": 0, "edge_warning_rounds": 0, "warnings": []}

    first_capture = _one(conn, "SELECT MIN(recv_ms) FROM md_book_l1")
    last_capture = _one(conn, "SELECT MAX(recv_ms) FROM md_book_l1")
    warnings: List[Dict[str, Any]] = []
    missing = 0
    edge_warnings = 0
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        # Ignore rounds that are outside the actual capture window for this DB.
        if first_capture and end_ms < int(first_capture) - 60_000:
            continue
        if last_capture and start_ms > int(last_capture) + 60_000:
            continue
        book_rows = int(row["book_rows"] or 0)
        if book_rows <= 0:
            missing += 1
            warnings.append({"slug": row["slug"], "kind": "missing_book"})
            continue
        first_book = int(row["first_book_ms"])
        last_book = int(row["last_book_ms"])
        if first_book > start_ms + 60_000 or last_book < end_ms - 60_000:
            edge_warnings += 1
            warnings.append(
                {
                    "slug": row["slug"],
                    "kind": "edge_coverage",
                    "first_book_delta_ms": first_book - start_ms,
                    "last_book_delta_ms": last_book - end_ms,
                }
            )

    return {
        "btc_book_coverage_rows": len(rows),
        "missing_book_rounds": missing,
        "edge_warning_rounds": edge_warnings,
        "warnings": warnings[:30],
    }


def _audit_settlement(
    conn: sqlite3.Connection,
    *,
    symbols: Sequence[str],
    trusted_start_ms: Optional[int] = None,
) -> Dict[str, Any]:
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
    allowed_symbols = {s.strip().upper() for s in symbols if s.strip()}
    settlement_cols = _table_columns(conn, "settlement_records")
    winner_expr = "s.winner_side" if "winner_side" in settlement_cols else "s.official_outcome"
    clauses = ["m.end_ms < ?"]
    params: List[Any] = [now_ms]
    if trusted_start_ms is not None:
        clauses.append("m.end_ms > ?")
        params.append(trusted_start_ms)
    if allowed_symbols:
        placeholders = ",".join("?" for _ in allowed_symbols)
        clauses.append(f"upper(m.symbol) IN ({placeholders})")
        params.extend(sorted(allowed_symbols))
    where = " AND ".join(clauses)
    official_expr = f"""
        CASE
          WHEN s.condition_id IS NOT NULL
           AND COALESCE({winner_expr}, s.official_outcome) IN ('YES', 'NO')
           AND lower(COALESCE(s.resolution_source, '')) NOT LIKE '%inferred%'
          THEN 1 ELSE 0
        END
    """
    inferred_expr = """
        CASE
          WHEN s.condition_id IS NOT NULL
           AND lower(COALESCE(s.resolution_source, '')) LIKE '%inferred%'
          THEN 1 ELSE 0
        END
    """
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS markets_total,
               SUM({official_expr}) AS settled_markets,
               SUM({inferred_expr}) AS inferred_markets
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
        WHERE {where}
        """,
        tuple(params),
    ).fetchone()
    markets_total = int(row["markets_total"] or 0)
    settled_markets = int(row["settled_markets"] or 0)
    inferred_markets = int(row["inferred_markets"] or 0)

    by_symbol: Dict[str, Dict[str, Any]] = {}
    for r in conn.execute(
        f"""
        SELECT m.symbol AS symbol,
               COUNT(*) AS markets_total,
               SUM({official_expr}) AS settled_markets,
               SUM({inferred_expr}) AS inferred_markets
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
        WHERE {where}
        GROUP BY m.symbol
        ORDER BY m.symbol
        """,
        tuple(params),
    ).fetchall():
        total = int(r["markets_total"] or 0)
        settled = int(r["settled_markets"] or 0)
        by_symbol[str(r["symbol"])] = {
            "markets_total": total,
            "settled_markets": settled,
            "inferred_markets": int(r["inferred_markets"] or 0),
            "settlement_coverage_ratio": _pct(settled, total),
        }

    by_source = {
        str(r["resolution_source"] or "missing"): int(r["n"] or 0)
        for r in conn.execute(
            f"""
            SELECT COALESCE(s.resolution_source, 'missing') AS resolution_source, COUNT(*) AS n
            FROM market_meta m
            LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
            WHERE {where}
            GROUP BY COALESCE(s.resolution_source, 'missing')
            ORDER BY n DESC
            """,
            tuple(params),
        ).fetchall()
    }

    btc = by_symbol.get("BTC", {})
    return {
        "markets_total": markets_total,
        "settled_markets": settled_markets,
        "inferred_markets": inferred_markets,
        "settlement_coverage_ratio": _pct(settled_markets, markets_total),
        "by_symbol": by_symbol,
        "by_resolution_source": by_source,
        "btc_ended_rounds": int(btc.get("markets_total", 0)),
        "btc_settlement_rows": int(btc.get("settled_markets", 0)),
        "btc_settlement_coverage": float(btc.get("settlement_coverage_ratio", 0.0)),
    }


def _day_bounds_ms(day: str) -> Tuple[int, int]:
    start = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def _audit_raw_replay_sample(
    conn: sqlite3.Connection,
    raw_root: Path,
    day: str,
    sample_condition_ids: Sequence[str],
    *,
    max_trade_records: int,
    max_book_records: int,
) -> Dict[str, Any]:
    trade_path = _raw_path(raw_root, day, "market_ws", "last_trade_price")
    book_path = _raw_path(raw_root, day, "market_ws", "book")

    trade_keys: set[Tuple[Any, ...]] = set()
    trade_scanned = 0
    sample_set = set(sample_condition_ids)
    for env in _iter_raw_envelopes(trade_path, max_trade_records):
        trade_scanned += 1
        if env.condition_id not in sample_set:
            continue
        rec = normalize_md_trade(env)
        if rec:
            trade_keys.add(dedup_trade_key(rec))

    replay_trade_rows = 0
    if sample_condition_ids:
        placeholders = ",".join("?" for _ in sample_condition_ids)
        replay_trade_rows = int(
            _one(conn, f"SELECT COUNT(*) FROM md_trades WHERE condition_id IN ({placeholders})", tuple(sample_condition_ids))
            or 0
        )

    book_keys: set[Tuple[Any, ...]] = set()
    book_conditions: set[str] = set()
    book_scanned = 0
    for env in _iter_raw_envelopes(book_path, max_book_records):
        book_scanned += 1
        rec = normalize_book_row(env)
        if not rec:
            continue
        # Keep this bounded sample BTC-focused without scanning the giant file.
        if env.condition_id in sample_set:
            book_keys.add((env.condition_id, dedup_book_key(rec)))
            book_conditions.add(env.condition_id)

    replay_book_rows = 0
    if book_conditions:
        ids = sorted(book_conditions)
        placeholders = ",".join("?" for _ in ids)
        replay_book_rows = int(_one(conn, f"SELECT COUNT(*) FROM md_book_l1 WHERE condition_id IN ({placeholders})", tuple(ids)) or 0)

    return {
        "sample_condition_ids": list(sample_condition_ids),
        "raw_trade_path": str(trade_path),
        "raw_book_path": str(book_path),
        "raw_trade_records_scanned": trade_scanned,
        "raw_trade_unique_sample": len(trade_keys),
        "replay_trade_rows_for_sample_conditions": replay_trade_rows,
        "trade_sample_replay_at_least_raw": replay_trade_rows >= len(trade_keys),
        "raw_book_records_scanned": book_scanned,
        "raw_book_unique_l1_sample": len(book_keys),
        "raw_book_sample_conditions": sorted(book_conditions),
        "replay_book_rows_for_raw_book_sample_conditions": replay_book_rows,
        "book_sample_replay_at_least_raw": replay_book_rows >= len(book_keys),
        "trade_scan_limited": trade_path.exists() and trade_scanned >= max_trade_records,
        "book_scan_limited": book_path.exists() and book_scanned >= max_book_records,
    }


def _market_feature_probe(conn: sqlite3.Connection, *, trusted_start_ms: Optional[int] = None) -> Dict[str, Any]:
    book_filter = ""
    trade_filter = ""
    book_params: Tuple[Any, ...] = ()
    trade_params: Tuple[Any, ...] = ()
    if trusted_start_ms is not None:
        book_filter = "AND b.recv_ms >= ?"
        trade_filter = "AND t.trade_ts_ms >= ?"
        book_params = (trusted_start_ms,)
        trade_params = (trusted_start_ms,)
    spread = conn.execute(
        f"""
        SELECT COUNT(*) AS n,
               AVG(yes_ask_px - yes_bid_px) AS avg_yes_spread,
               AVG(no_ask_px - no_bid_px) AS avg_no_spread,
               AVG(yes_bid_sz) AS avg_yes_bid_sz,
               AVG(yes_ask_sz) AS avg_yes_ask_sz,
               AVG(no_bid_sz) AS avg_no_bid_sz,
               AVG(no_ask_sz) AS avg_no_ask_sz
        FROM md_book_l1 b
        JOIN market_meta m ON m.condition_id=b.condition_id
        WHERE m.symbol='BTC'
        {book_filter}
        """,
        book_params,
    ).fetchone()
    trades = conn.execute(
        f"""
        SELECT t.condition_id, t.trade_ts_ms, t.market_side
        FROM md_trades t
        JOIN market_meta m ON m.condition_id=t.condition_id
        WHERE m.symbol='BTC' AND t.trade_ts_ms IS NOT NULL AND t.market_side IS NOT NULL
        {trade_filter}
        ORDER BY t.condition_id, t.trade_ts_ms, t.id
        """,
        trade_params,
    ).fetchall()
    by_condition: Dict[str, List[sqlite3.Row]] = {}
    for row in trades:
        by_condition.setdefault(str(row["condition_id"]), []).append(row)

    first_side_counts: Dict[str, int] = {}
    opposite_delays: List[int] = []
    completed_30s = 0
    for rows in by_condition.values():
        first = rows[0]
        first_side = str(first["market_side"])
        first_side_counts[first_side] = first_side_counts.get(first_side, 0) + 1
        first_ts = int(first["trade_ts_ms"])
        for row in rows[1:]:
            if str(row["market_side"]) != first_side:
                delay = int(row["trade_ts_ms"]) - first_ts
                opposite_delays.append(delay)
                if delay <= 30_000:
                    completed_30s += 1
                break

    return {
        "btc_l1_rows": int(spread["n"] or 0) if spread else 0,
        "avg_yes_spread": round(float(spread["avg_yes_spread"] or 0.0), 6) if spread else 0.0,
        "avg_no_spread": round(float(spread["avg_no_spread"] or 0.0), 6) if spread else 0.0,
        "avg_l1_sizes": {
            "yes_bid": round(float(spread["avg_yes_bid_sz"] or 0.0), 4) if spread else 0.0,
            "yes_ask": round(float(spread["avg_yes_ask_sz"] or 0.0), 4) if spread else 0.0,
            "no_bid": round(float(spread["avg_no_bid_sz"] or 0.0), 4) if spread else 0.0,
            "no_ask": round(float(spread["avg_no_ask_sz"] or 0.0), 4) if spread else 0.0,
        },
        "btc_trade_rows": len(trades),
        "markets_with_trades": len(by_condition),
        "first_side_counts": first_side_counts,
        "opposite_delay_observations": len(opposite_delays),
        "first_opposite_delay_avg_ms": round(sum(opposite_delays) / len(opposite_delays), 2) if opposite_delays else 0.0,
        "completion_30s_count": completed_30s,
        "completion_30s_rate": _pct(completed_30s, len(opposite_delays)),
    }


def audit_day(config: AuditConfig, day: str) -> Dict[str, Any]:
    db_path = config.replay_root / day / "crypto_5m.sqlite"
    raw_path = config.raw_root / day
    base: Dict[str, Any] = {
        "date": day,
        "db_path": str(db_path),
        "raw_path": str(raw_path),
        "xuan_truth_audit": "N/A",
        "own_truth_audit": "N/A",
        "db_stability": "warning",
        "db_stability_reason": "builder currently rebuilds target sqlite in place; tmp sqlite atomic rename is not implemented",
        "trusted_start_ms": config.trusted_start_ms,
        "planned_outage_windows_ms": [
            {"start_ms": start, "end_ms": end} for start, end in PLANNED_OUTAGE_WINDOWS_MS
        ],
    }

    if not db_path.exists():
        base["market_side_audit"] = "fail"
        base["errors"] = ["replay db missing"]
        return base
    if db_path.stat().st_size < config.min_db_bytes:
        base["market_side_audit"] = "fail"
        base["errors"] = [f"replay db too small: {db_path.stat().st_size} bytes"]
        return base

    try:
        conn = _connect_ro(db_path)
        schema_core = _audit_schema_core(conn, db_path, config.min_db_bytes)
        day_start_ms, day_end_ms = _day_bounds_ms(day)
        btc_continuity = _audit_btc_continuity(conn, trusted_start_ms=config.trusted_start_ms)
        btc_book_coverage = _audit_btc_book_coverage(conn, trusted_start_ms=config.trusted_start_ms)
        settlement = _audit_settlement(
            conn,
            symbols=config.outcome_symbols,
            trusted_start_ms=config.trusted_start_ms,
        )
        btc_rows = conn.execute(
            """
            SELECT condition_id, slug, start_ms, end_ms
            FROM market_meta
            WHERE symbol='BTC' AND (? IS NULL OR end_ms > ?)
            ORDER BY start_ms
            """,
            (config.trusted_start_ms, config.trusted_start_ms),
        ).fetchall()
        sample_condition_ids = _select_btc_samples(btc_rows)
        raw_sample = _audit_raw_replay_sample(
            conn,
            config.raw_root,
            day,
            sample_condition_ids,
            max_trade_records=config.raw_trade_max_records,
            max_book_records=config.raw_book_max_records,
        )
        feature_probe = _market_feature_probe(conn, trusted_start_ms=config.trusted_start_ms)
    except sqlite3.Error as exc:
        base["market_side_audit"] = "fail"
        base["errors"] = [f"sqlite audit failed: {exc}"]
        return base
    finally:
        try:
            conn.close()
        except UnboundLocalError:
            pass

    failures: List[str] = []
    warnings: List[str] = []
    if not schema_core["db_size_ok"]:
        failures.append("db_size_below_threshold")
    if any(v != 0 for v in schema_core["book_size_nulls"].values()):
        failures.append("book_size_nulls_present")
    if schema_core["trade_ts_ms_null_rows"] != 0:
        failures.append("trade_ts_ms_nulls_present")
    if schema_core["taker_side_null_rate"] > config.taker_side_null_max_ratio:
        failures.append("taker_side_null_rate_too_high")
    if btc_continuity["nonplanned_gap_count"] > 0 or btc_continuity["bad_slug_count"] > 0:
        failures.append("btc_slug_continuity_failed")
    if btc_book_coverage["missing_book_rounds"] > 0:
        failures.append("btc_book_missing_rounds")
    if feature_probe["btc_l1_rows"] <= 0:
        failures.append("market_feature_probe_no_l1")
    if feature_probe["btc_trade_rows"] <= 0:
        failures.append("market_feature_probe_no_trades")
    if raw_sample["raw_trade_unique_sample"] > 0 and not raw_sample["trade_sample_replay_at_least_raw"]:
        failures.append("raw_trade_sample_not_covered_by_replay")
    if raw_sample["raw_book_unique_l1_sample"] > 0 and not raw_sample["book_sample_replay_at_least_raw"]:
        failures.append("raw_book_sample_not_covered_by_replay")

    if any(v != 0 for v in schema_core["book_price_nulls"].values()):
        warnings.append("book_price_nulls_present")
    if schema_core["md_book_l2_rows"] <= 0:
        warnings.append("md_book_l2_empty")
    if abs(schema_core["avg_trade_latency_ms"]) > 60_000 or schema_core["max_abs_trade_latency_ms"] > 600_000:
        warnings.append("trade_latency_large")
    for symbol, symbol_cov in settlement.get("by_symbol", {}).items():
        if symbol_cov.get("markets_total", 0) > 0 and symbol_cov.get("settlement_coverage_ratio", 0.0) < config.min_official_outcome_coverage:
            warnings.append(f"{str(symbol).lower()}_settlement_coverage_low")
    if raw_sample["trade_scan_limited"]:
        warnings.append("raw_trade_sample_limited")
    if raw_sample["book_scan_limited"]:
        warnings.append("raw_book_sample_limited")
    if btc_book_coverage["edge_warning_rounds"] > 0:
        warnings.append("btc_book_edge_coverage_warnings")

    base.update(
        {
            "market_side_audit": _status(bool(failures), bool(warnings)),
            "failures": failures,
            "warnings": warnings,
            "market_meta": {
                "rows": schema_core["market_meta_rows"],
                "symbol_counts": schema_core["symbol_counts"],
            },
            "md_book_l1": {
                "rows": schema_core["md_book_l1_rows"],
                "size_nulls": schema_core["book_size_nulls"],
                "size_null_rates": schema_core["book_size_null_rates"],
                "price_nulls": schema_core["book_price_nulls"],
                "price_null_rates": schema_core["book_price_null_rates"],
            },
            "md_book_l2": {
                "rows": schema_core["md_book_l2_rows"],
                "side_rows": schema_core["md_book_l2_side_rows"],
            },
            "md_trades": {
                "rows": schema_core["md_trades_rows"],
                "trade_ts_ms_null_rows": schema_core["trade_ts_ms_null_rows"],
                "trade_ts_ms_null_rate": schema_core["trade_ts_ms_null_rate"],
                "taker_side_null_rows": schema_core["taker_side_null_rows"],
                "taker_side_null_rate": schema_core["taker_side_null_rate"],
                "avg_trade_latency_ms": schema_core["avg_trade_latency_ms"],
                "max_abs_trade_latency_ms": schema_core["max_abs_trade_latency_ms"],
            },
            "settlement_records": settlement,
            "capture_bounds": {
                **schema_core["capture_bounds"],
                "day_start_ms": day_start_ms,
                "day_end_ms": day_end_ms,
                "partial_day": (
                    schema_core["capture_bounds"]["max_book_recv_ms"] > 0
                    and schema_core["capture_bounds"]["max_book_recv_ms"] < day_end_ms - 60_000
                ),
            },
            "btc_continuity": btc_continuity,
            "btc_book_coverage": btc_book_coverage,
            "raw_replay_sample_check": raw_sample,
            "market_feature_probe": feature_probe,
        }
    )
    return base


def run_market_replay_audit(config: AuditConfig) -> Dict[str, Any]:
    days = [audit_day(config, day) for day in config.days]
    has_fail = any(day.get("market_side_audit") == "fail" for day in days)
    has_warning = any(day.get("market_side_audit") == "warning" for day in days)
    market_trusted = not has_fail
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "days": days,
        "trusted_start_ms": config.trusted_start_ms,
        "planned_outage_windows_ms": [
            {"start_ms": start, "end_ms": end} for start, end in PLANNED_OUTAGE_WINDOWS_MS
        ],
        "market_side_audit": _status(has_fail, has_warning),
        "xuan_truth_audit": "N/A",
        "own_truth_audit": "N/A",
        "db_stability": "warning",
        "db_stability_reason": "builder currently rebuilds target sqlite in place; tmp sqlite atomic rename is not implemented",
        "performance_notes": {
            "raw_trade_check": "bounded BTC sample by default; full raw trade scan is manual follow-up only",
            "raw_book_check": "bounded first-N sample only; full book scan intentionally avoided",
            "db_stability": "warning until builder uses tmp sqlite atomic rename",
        },
        "final_verdict": {
            "market_replay_trusted": market_trusted,
            "xuan_episode_ready": False,
            "own_execution_truth_ready": False,
            "can_switch_market_raw_to_short_retention": market_trusted,
        },
    }


def save_audit_report(report: Dict[str, Any], output: Path, markdown_output: Optional[Path] = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if markdown_output:
        markdown_output.parent.mkdir(parents=True, exist_ok=True)
        markdown_output.write_text(render_markdown_report(report), encoding="utf-8")


def render_markdown_report(report: Dict[str, Any]) -> str:
    verdict = report["final_verdict"]
    lines = [
        "# Replay Audit Report",
        "",
        f"- generated_at_utc: `{report.get('generated_at_utc')}`",
        f"- trusted_start_ms: `{report.get('trusted_start_ms')}`",
        f"- market_side_audit: `{report.get('market_side_audit')}`",
        f"- xuan_truth_audit: `{report.get('xuan_truth_audit')}`",
        f"- own_truth_audit: `{report.get('own_truth_audit')}`",
        f"- db_stability: `{report.get('db_stability')}`",
        f"- market_replay_trusted: `{str(verdict['market_replay_trusted']).lower()}`",
        f"- xuan_episode_ready: `{str(verdict['xuan_episode_ready']).lower()}`",
        f"- own_execution_truth_ready: `{str(verdict['own_execution_truth_ready']).lower()}`",
        f"- can_switch_market_raw_to_short_retention: `{str(verdict['can_switch_market_raw_to_short_retention']).lower()}`",
        "",
        "## Daily Results",
    ]
    for day in report.get("days", []):
        lines.extend(
            [
                "",
                f"### {day['date']}",
                "",
                f"- market_side_audit: `{day.get('market_side_audit')}`",
                f"- db_path: `{day.get('db_path')}`",
                f"- raw_path: `{day.get('raw_path')}`",
                f"- db_stability: `{day.get('db_stability')}`",
                f"- failures: `{', '.join(day.get('failures', [])) or 'none'}`",
                f"- warnings: `{', '.join(day.get('warnings', [])) or 'none'}`",
                f"- md_book_l1_rows: `{day.get('md_book_l1', {}).get('rows', 0)}`",
                f"- md_book_l2_rows: `{day.get('md_book_l2', {}).get('rows', 0)}`",
                f"- md_trades_rows: `{day.get('md_trades', {}).get('rows', 0)}`",
                f"- taker_side_null_rate: `{day.get('md_trades', {}).get('taker_side_null_rate', 0)}`",
                f"- capture_max_book_recv_ms: `{day.get('capture_bounds', {}).get('max_book_recv_ms', 0)}`",
                f"- partial_day: `{str(day.get('capture_bounds', {}).get('partial_day', False)).lower()}`",
                f"- btc_rounds: `{day.get('btc_continuity', {}).get('btc_rounds', 0)}`",
                f"- nonplanned_btc_gaps: `{day.get('btc_continuity', {}).get('nonplanned_gap_count', 0)}`",
                f"- settlement_coverage_ratio: `{day.get('settlement_records', {}).get('settlement_coverage_ratio', 0)}`",
                f"- btc_settlement_coverage: `{day.get('settlement_records', {}).get('btc_settlement_coverage', 0)}`",
                f"- market_feature_probe_btc_trades: `{day.get('market_feature_probe', {}).get('btc_trade_rows', 0)}`",
            ]
        )
    return "\n".join(lines) + "\n"


def safety_gate(
    *,
    replay_root: Path,
    days: Sequence[str],
    min_db_bytes: int,
    min_mem_available_kib: int,
    min_disk_free_bytes: int,
    max_load_1m: float,
) -> Tuple[bool, List[str]]:
    failures: List[str] = []
    if not _process_exists("capture-sidecar-env"):
        failures.append("capture-sidecar-env_not_running")
    if _old_unlocked_rebuild_loop_exists():
        failures.append("old_unlocked_build-replay-rolling_loop_running")
    if _active_rebuild_process_exists():
        failures.append("build-replay-rolling_running")

    mem_available = _mem_available_kib()
    if mem_available is not None and mem_available < min_mem_available_kib:
        failures.append(f"mem_available_below_threshold:{mem_available}KiB")
    disk_free = _disk_free_bytes(replay_root)
    if disk_free is not None and disk_free < min_disk_free_bytes:
        failures.append(f"disk_free_below_threshold:{disk_free}B")
    try:
        load_1m = os.getloadavg()[0]
        if load_1m >= max_load_1m:
            failures.append(f"load_1m_too_high:{load_1m:.2f}")
    except OSError:
        pass
    for day in days:
        db = replay_root / day / "crypto_5m.sqlite"
        if not db.exists():
            failures.append(f"missing_db:{db}")
        elif db.stat().st_size < min_db_bytes:
            failures.append(f"small_db:{db}:{db.stat().st_size}")
    return (not failures), failures


def _iter_proc_cmdlines() -> Iterable[Tuple[str, str]]:
    proc = Path("/proc")
    if not proc.exists():
        return
    for item in proc.iterdir():
        if not item.name.isdigit():
            continue
        try:
            cmdline = (item / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except OSError:
            continue
        yield item.name, cmdline


def _process_exists(pattern: str) -> bool:
    for _pid, cmdline in _iter_proc_cmdlines():
        if pattern in cmdline and "audit-replay-market" not in cmdline:
            return True
    return False


def _old_unlocked_rebuild_loop_exists() -> bool:
    for _pid, cmdline in _iter_proc_cmdlines():
        if "build-replay-rolling" not in cmdline or "audit-replay-market" in cmdline:
            continue
        if "bash" in cmdline and "while" in cmdline and "flock" not in cmdline:
            return True
    return False


def _active_rebuild_process_exists() -> bool:
    for _pid, cmdline in _iter_proc_cmdlines():
        if "build-replay-rolling" not in cmdline or "audit-replay-market" in cmdline:
            continue
        # The locked outer bash loop is expected to sleep between rebuilds and
        # still contains the build command text in argv. Only active flock/uv/python
        # rebuild children should block audit startup.
        if "bash" in cmdline and "while" in cmdline and "flock" in cmdline:
            continue
        return True
    return False


def _mem_available_kib() -> Optional[int]:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1])
    except OSError:
        return None
    return None


def _disk_free_bytes(path: Path) -> Optional[int]:
    try:
        stat = os.statvfs(path if path.exists() else path.parent)
    except OSError:
        return None
    return int(stat.f_bavail * stat.f_frsize)

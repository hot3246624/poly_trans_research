"""Replay SQLite schema and initialization."""

from __future__ import annotations

import sqlite3

SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS market_meta (
    condition_id TEXT PRIMARY KEY,
    slug TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval_sec INTEGER NOT NULL,
    start_ms INTEGER NOT NULL,
    end_ms INTEGER NOT NULL,
    yes_token_id TEXT,
    no_token_id TEXT,
    tick_size REAL,
    first_seen_ms INTEGER NOT NULL,
    last_seen_ms INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS md_book_l1 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    source_ts_ms INTEGER,
    yes_bid_px REAL,
    yes_ask_px REAL,
    no_bid_px REAL,
    no_ask_px REAL,
    yes_bid_sz REAL,
    yes_ask_sz REAL,
    no_bid_sz REAL,
    no_ask_sz REAL,
    source_kind TEXT NOT NULL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS md_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    trade_ts_ms INTEGER,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    source_ts_ms INTEGER,
    trade_id TEXT,
    market_side TEXT,
    taker_side TEXT,
    maker_address TEXT,
    taker_address TEXT,
    price REAL NOT NULL,
    size REAL NOT NULL,
    source_quality TEXT NOT NULL,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS xuan_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    poll_ts_ms INTEGER NOT NULL,
    trade_ts_ms INTEGER,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    condition_id TEXT,
    slug TEXT,
    event_slug TEXT,
    title TEXT,
    outcome TEXT,
    side TEXT,
    price REAL,
    size REAL,
    asset TEXT,
    proxy_wallet TEXT,
    tx_hash TEXT,
    trade_id TEXT,
    source_quality TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xuan_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    poll_ts_ms INTEGER NOT NULL,
    activity_ts_ms INTEGER,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    condition_id TEXT,
    slug TEXT,
    event_slug TEXT,
    title TEXT,
    activity_type TEXT,
    outcome TEXT,
    side TEXT,
    price REAL,
    size REAL,
    usdc_size REAL,
    asset TEXT,
    proxy_wallet TEXT,
    tx_hash TEXT,
    source_quality TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS xuan_poll_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    poll_ts_ms INTEGER NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    max_ts_ms INTEGER,
    ok INTEGER NOT NULL,
    error TEXT
);

CREATE TABLE IF NOT EXISTS own_order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    client_order_id TEXT,
    order_id TEXT,
    event_type TEXT NOT NULL,
    side TEXT,
    direction TEXT,
    price REAL,
    size REAL,
    remaining REAL,
    status TEXT,
    reason TEXT,
    reject_kind TEXT,
    tx_hash TEXT,
    strategy_tag TEXT,
    round_id TEXT
);

CREATE TABLE IF NOT EXISTS own_inventory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    yes_pos REAL,
    no_pos REAL,
    yes_avg_cost REAL,
    no_avg_cost REAL,
    paired_qty REAL,
    residual_qty REAL,
    usdc_available REAL,
    tx_hash TEXT
);

CREATE TABLE IF NOT EXISTS settlement_records (
    condition_id TEXT PRIMARY KEY,
    official_outcome TEXT NOT NULL,
    settle_ms INTEGER,
    resolution_source TEXT,
    capture_seq INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_book_cond_seq ON md_book_l1(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_book_cond_recv ON md_book_l1(condition_id, recv_ms);
CREATE INDEX IF NOT EXISTS idx_trades_cond_seq ON md_trades(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_trades_cond_trade_ts ON md_trades(condition_id, trade_ts_ms);
CREATE INDEX IF NOT EXISTS idx_trades_taker_side ON md_trades(taker_side);
CREATE INDEX IF NOT EXISTS idx_order_cond_seq ON own_order_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_order_type ON own_order_events(event_type);
CREATE INDEX IF NOT EXISTS idx_inventory_cond_seq ON own_inventory_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_xuan_trades_poll_ts ON xuan_trades(poll_ts_ms);
CREATE INDEX IF NOT EXISTS idx_xuan_trades_cond_ts ON xuan_trades(condition_id, trade_ts_ms);
CREATE INDEX IF NOT EXISTS idx_xuan_activity_poll_ts ON xuan_activity(poll_ts_ms);
CREATE INDEX IF NOT EXISTS idx_xuan_activity_cond_ts ON xuan_activity(condition_id, activity_ts_ms);
CREATE INDEX IF NOT EXISTS idx_xuan_poll_log_endpoint_ts ON xuan_poll_log(endpoint, poll_ts_ms);
"""


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(r[1]) for r in rows}


def _ensure_column(conn: sqlite3.Connection, table: str, column_def: str) -> None:
    col_name = column_def.split()[0].strip()
    if col_name in _table_columns(conn, table):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column_def}")


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _ensure_column(conn, "md_trades", "maker_address TEXT")
    _ensure_column(conn, "md_trades", "taker_address TEXT")
    _ensure_column(conn, "md_trades", "raw_json TEXT")
    _ensure_column(conn, "md_book_l1", "raw_json TEXT")
    conn.commit()

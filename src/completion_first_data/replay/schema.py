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
    source_kind TEXT NOT NULL
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
    price REAL NOT NULL,
    size REAL NOT NULL,
    source_quality TEXT NOT NULL
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
CREATE INDEX IF NOT EXISTS idx_order_cond_seq ON own_order_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_order_type ON own_order_events(event_type);
CREATE INDEX IF NOT EXISTS idx_inventory_cond_seq ON own_inventory_events(condition_id, capture_seq);
"""


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()

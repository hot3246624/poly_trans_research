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

CREATE TABLE IF NOT EXISTS md_book_l2 (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    source_ts_ms INTEGER,
    market_side TEXT NOT NULL,
    depth INTEGER NOT NULL,
    bid1_px REAL,
    bid1_sz REAL,
    bid2_px REAL,
    bid2_sz REAL,
    bid3_px REAL,
    bid3_sz REAL,
    bid4_px REAL,
    bid4_sz REAL,
    bid5_px REAL,
    bid5_sz REAL,
    ask1_px REAL,
    ask1_sz REAL,
    ask2_px REAL,
    ask2_sz REAL,
    ask3_px REAL,
    ask3_sz REAL,
    ask4_px REAL,
    ask4_sz REAL,
    ask5_px REAL,
    ask5_sz REAL,
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
    outcome_side TEXT,
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
    outcome_side TEXT,
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

CREATE TABLE IF NOT EXISTS own_fill_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    order_id TEXT,
    taker_order_id TEXT,
    trade_id TEXT,
    market_side TEXT,
    direction TEXT,
    trader_side TEXT,
    price REAL,
    size REAL,
    fee_rate_bps REAL,
    match_ts_ms INTEGER,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    maker_address TEXT,
    tx_hash TEXT,
    raw_json TEXT
);

CREATE TABLE IF NOT EXISTS own_inventory_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    condition_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    outcome TEXT,
    size REAL,
    avg_price REAL,
    redeemable INTEGER,
    mergeable INTEGER,
    source_kind TEXT NOT NULL,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS user_ws_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recv_ms INTEGER NOT NULL,
    recv_monotonic_ns INTEGER NOT NULL,
    capture_seq INTEGER NOT NULL,
    event_name TEXT NOT NULL,
    event_value TEXT,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS settlement_records (
    condition_id TEXT PRIMARY KEY,
    official_outcome TEXT NOT NULL,
    winner_side TEXT,
    winner_token_id TEXT,
    settle_ms INTEGER,
    resolution_source TEXT,
    raw_json TEXT,
    capture_seq INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_book_cond_seq ON md_book_l1(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_book_cond_recv ON md_book_l1(condition_id, recv_ms);
CREATE INDEX IF NOT EXISTS idx_book_l2_cond_side_recv ON md_book_l2(condition_id, market_side, recv_ms);
CREATE INDEX IF NOT EXISTS idx_trades_cond_seq ON md_trades(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_trades_cond_trade_ts ON md_trades(condition_id, trade_ts_ms);
CREATE INDEX IF NOT EXISTS idx_trades_taker_side ON md_trades(taker_side);
CREATE INDEX IF NOT EXISTS idx_order_cond_seq ON own_order_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_order_type ON own_order_events(event_type);
CREATE INDEX IF NOT EXISTS idx_fill_cond_seq ON own_fill_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_fill_trade_id ON own_fill_events(trade_id);
CREATE INDEX IF NOT EXISTS idx_inventory_cond_seq ON own_inventory_events(condition_id, capture_seq);
CREATE INDEX IF NOT EXISTS idx_inventory_source_kind ON own_inventory_events(source_kind);
CREATE INDEX IF NOT EXISTS idx_user_ws_log_event_name ON user_ws_log(event_name, recv_ms);
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


def _ensure_views(conn: sqlite3.Connection) -> None:
    conn.execute("DROP VIEW IF EXISTS market_meta_with_outcome")
    conn.execute(
        """
        CREATE VIEW market_meta_with_outcome AS
        SELECT
            m.*,
            s.official_outcome,
            COALESCE(s.winner_side, s.official_outcome) AS winner_side,
            s.winner_token_id,
            s.settle_ms,
            s.resolution_source
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        """
    )


def _ensure_post_migration_indexes(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE INDEX IF NOT EXISTS idx_xuan_trades_outcome_side ON xuan_trades(outcome_side)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_xuan_activity_outcome_side ON xuan_activity(outcome_side)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_settlement_records_winner_side ON settlement_records(winner_side)")


def _backfill_existing_normalized_columns(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE settlement_records
        SET winner_side = official_outcome
        WHERE (winner_side IS NULL OR TRIM(winner_side) = '')
          AND official_outcome IN ('YES', 'NO')
        """
    )


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    _ensure_column(conn, "md_trades", "maker_address TEXT")
    _ensure_column(conn, "md_trades", "taker_address TEXT")
    _ensure_column(conn, "md_trades", "raw_json TEXT")
    _ensure_column(conn, "md_book_l1", "raw_json TEXT")
    _ensure_column(conn, "own_fill_events", "raw_json TEXT")
    _ensure_column(conn, "settlement_records", "winner_side TEXT")
    _ensure_column(conn, "settlement_records", "winner_token_id TEXT")
    _ensure_column(conn, "settlement_records", "raw_json TEXT")
    _ensure_column(conn, "xuan_trades", "outcome_side TEXT")
    _ensure_column(conn, "xuan_activity", "outcome_side TEXT")
    _ensure_post_migration_indexes(conn)
    _backfill_existing_normalized_columns(conn)
    _ensure_views(conn)
    conn.commit()

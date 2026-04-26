"""Project constants for completion-first raw/replay pipeline."""

from __future__ import annotations

RAW_ENVELOPE_FIELDS = (
    "recv_unix_ms",
    "recv_monotonic_ns",
    "capture_seq",
    "source",
    "channel",
    "condition_id",
    "payload_json",
)

REPLAY_TABLES = (
    "market_meta",
    "md_book_l1",
    "md_trades",
    "xuan_trades",
    "xuan_activity",
    "xuan_poll_log",
    "own_order_events",
    "own_inventory_events",
    "settlement_records",
)

ORDER_EVENT_TYPES = {
    "intent_sent",
    "post_sent",
    "live",
    "cancel_sent",
    "canceled",
    "rejected",
    "partial_fill",
    "fill",
    "taker_repair_sent",
    "merge",
    "redeem",
    "placement",
    "update",
}

SOURCE_KIND_MARKET_WS = "market_ws"
SOURCE_KIND_USER_WS = "user_ws"
SOURCE_KIND_META = "meta"
SOURCE_KIND_SETTLEMENT = "settlement"
SOURCE_KIND_BACKFILL = "backfill"
SOURCE_KIND_INVENTORY = "inventory"
SOURCE_KIND_XUAN_POLL = "xuan_poll"

CHANNEL_BOOK = "book"
CHANNEL_BBA = "best_bid_ask"
CHANNEL_LAST_TRADE = "last_trade_price"
CHANNEL_ORDER = "order"
CHANNEL_TRADE = "trade"
CHANNEL_MARKET_RESOLVED = "market_resolved"
CHANNEL_MARKET_META = "market_meta"
CHANNEL_INVENTORY = "inventory_event"
CHANNEL_TRADES_BACKFILL = "trades_backfill"
CHANNEL_XUAN_TRADES = "xuan_trades"
CHANNEL_XUAN_ACTIVITY = "xuan_activity"
CHANNEL_XUAN_POLL_LOG = "xuan_poll_log"

POLYMARKET_GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
POLYMARKET_CLOB_MARKETS_URL = "https://clob.polymarket.com/markets"
POLYMARKET_DATA_TRADES_URL = "https://data-api.polymarket.com/trades"
POLYMARKET_DATA_ACTIVITY_URL = "https://data-api.polymarket.com/activity"

# Roughly around 5m rounds with light tolerance for bad metadata.
ROUND_INTERVAL_TARGET_SEC = 300
ROUND_INTERVAL_MIN_SEC = 240
ROUND_INTERVAL_MAX_SEC = 360

DEFAULT_META_POLL_SEC = 20
DEFAULT_HEARTBEAT_MS = 100
DEFAULT_GAP_THRESHOLD_MS = 2000

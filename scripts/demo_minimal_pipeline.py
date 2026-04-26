#!/usr/bin/env python3
"""Generate a tiny synthetic raw day and build replay for smoke-testing."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from completion_first_data.capture.raw_store import RawCaptureStore
from completion_first_data.replay.builder import build_replay_for_day
from completion_first_data.quality.validator import validate_replay_db


def main() -> int:
    day = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    raw_root = ROOT / "data" / "raw"
    replay_root = ROOT / "data" / "replay"

    store = RawCaptureStore(raw_root)

    condition_id = "0xdemo_condition"
    now_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)

    store.write(
        source="meta",
        channel="market_meta",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "slug": "btc-updown-5m-demo",
            "symbol": "BTC",
            "interval_sec": 300,
            "start_ms": now_ms,
            "end_ms": now_ms + 300_000,
            "yes_token_id": "1",
            "no_token_id": "2",
            "tick_size": 0.01,
        },
        recv_unix_ms=now_ms,
    )

    store.write(
        source="market_ws",
        channel="book",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "yes_bid_px": 0.49,
            "yes_ask_px": 0.51,
            "no_bid_px": 0.49,
            "no_ask_px": 0.51,
            "yes_bid_sz": 100,
            "yes_ask_sz": 120,
            "no_bid_sz": 90,
            "no_ask_sz": 110,
            "timestamp": now_ms,
        },
        recv_unix_ms=now_ms + 500,
    )

    store.write(
        source="market_ws",
        channel="last_trade_price",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "trade_id": "t1",
            "trade_ts_ms": now_ms + 600,
            "outcome": "YES",
            "price": 0.5,
            "size": 10,
        },
        recv_unix_ms=now_ms + 650,
    )

    store.write(
        source="user_ws",
        channel="order",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "client_order_id": "c1",
            "order_id": "o1",
            "event_type": "fill",
            "side": "YES",
            "direction": "BUY",
            "price": 0.5,
            "size": 10,
            "remaining": 0,
            "status": "FILLED",
        },
        recv_unix_ms=now_ms + 700,
    )

    store.write(
        source="inventory",
        channel="inventory_event",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "event_type": "fill",
            "yes_pos": 10,
            "no_pos": 0,
            "yes_avg_cost": 0.5,
            "no_avg_cost": 0,
            "paired_qty": 0,
            "residual_qty": 10,
            "usdc_available": 100,
        },
        recv_unix_ms=now_ms + 900,
    )

    store.write(
        source="settlement",
        channel="market_resolved",
        condition_id=condition_id,
        payload_json={
            "condition_id": condition_id,
            "official_outcome": "YES",
            "settle_ms": now_ms + 301_000,
            "resolution_source": "demo",
        },
        recv_unix_ms=now_ms + 301_000,
    )

    stats = build_replay_for_day(raw_root, replay_root, day)
    print("build_stats:", json.dumps(stats.as_dict(), ensure_ascii=False, indent=2))

    db = replay_root / day / "crypto_5m.sqlite"
    report = validate_replay_db(db)
    print("validation:", json.dumps(report.as_dict(), ensure_ascii=False, indent=2))

    conn = sqlite3.connect(db)
    c = conn.cursor()
    for table in [
        "market_meta",
        "md_book_l1",
        "md_trades",
        "own_order_events",
        "own_inventory_events",
        "settlement_records",
    ]:
        n = c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"{table}: {n}")
    conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

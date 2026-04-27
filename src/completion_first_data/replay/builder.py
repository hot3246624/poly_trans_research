"""Build replay SQLite from raw envelope files."""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path
from typing import Dict, Iterable, List

from ..capture.envelope import RawEnvelope
from ..constants import (
    CHANNEL_BOOK,
    CHANNEL_BBA,
    CHANNEL_INVENTORY_SNAPSHOT,
    CHANNEL_XUAN_ACTIVITY,
    CHANNEL_XUAN_POLL_LOG,
    CHANNEL_XUAN_TRADES,
    CHANNEL_LAST_TRADE,
    CHANNEL_MARKET_META,
    CHANNEL_MARKET_RESOLVED,
    CHANNEL_USER_ORDER,
    CHANNEL_USER_TRADE,
    CHANNEL_USER_WS_LOG,
    CHANNEL_TRADES_BACKFILL,
)
from ..utils.io import glob_jsonl_gz, iter_jsonl_gz
from .normalize import (
    dedup_book_key,
    dedup_fill_key,
    dedup_order_key,
    dedup_trade_key,
    normalize_book_row,
    normalize_fill_events,
    normalize_inventory_event,
    normalize_market_meta_payload,
    normalize_md_trade,
    normalize_order_event,
    normalize_settlement,
    normalize_user_ws_log,
    normalize_xuan_activity,
    normalize_xuan_poll_log,
    normalize_xuan_trade,
)
from .schema import init_schema


@dataclasses.dataclass(slots=True)
class BuildStats:
    raw_files: int = 0
    raw_records: int = 0
    market_meta_rows: int = 0
    md_book_rows: int = 0
    md_trades_rows: int = 0
    xuan_trades_rows: int = 0
    xuan_activity_rows: int = 0
    xuan_poll_log_rows: int = 0
    own_order_rows: int = 0
    own_fill_rows: int = 0
    own_inventory_rows: int = 0
    user_ws_log_rows: int = 0
    settlement_rows: int = 0
    dedup_skips: int = 0

    def as_dict(self) -> Dict[str, int]:
        return dataclasses.asdict(self)


class ReplayBuilder:
    def __init__(self, *, raw_day_root: Path, replay_db_path: Path):
        self.raw_day_root = raw_day_root
        self.replay_db_path = replay_db_path

    def _load_envelopes(self) -> List[RawEnvelope]:
        files = list(glob_jsonl_gz([self.raw_day_root]))
        self._files_count = len(files)
        envelopes: List[RawEnvelope] = []
        for path in files:
            for rec in iter_jsonl_gz(path):
                envelopes.append(RawEnvelope.from_dict(rec))
        envelopes.sort(key=lambda e: (e.capture_seq, e.recv_monotonic_ns, e.recv_unix_ms))
        return envelopes

    def build(self) -> BuildStats:
        stats = BuildStats()
        envelopes = self._load_envelopes()
        stats.raw_files = self._files_count
        stats.raw_records = len(envelopes)

        self.replay_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.replay_db_path)
        conn.row_factory = sqlite3.Row
        init_schema(conn)

        book_last: Dict[str, tuple] = {}
        trade_seen: set = set()
        xuan_trade_seen: set = set()
        xuan_activity_seen: set = set()
        xuan_poll_seen: set = set()
        order_seen: set = set()
        fill_seen: set = set()
        user_ws_log_seen: set = set()
        settlement_seen: set = set()

        cur = conn.cursor()

        for env in envelopes:
            source = env.source or ""
            channel = env.channel or ""

            # market meta
            if channel == CHANNEL_MARKET_META or source == "meta":
                rec = normalize_market_meta_payload(env.payload_json)
                if rec:
                    cur.execute(
                        """
                        INSERT INTO market_meta (
                            condition_id, slug, symbol, interval_sec, start_ms, end_ms,
                            yes_token_id, no_token_id, tick_size, first_seen_ms, last_seen_ms
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(condition_id) DO UPDATE SET
                            slug=excluded.slug,
                            symbol=excluded.symbol,
                            interval_sec=excluded.interval_sec,
                            start_ms=excluded.start_ms,
                            end_ms=excluded.end_ms,
                            yes_token_id=excluded.yes_token_id,
                            no_token_id=excluded.no_token_id,
                            tick_size=excluded.tick_size,
                            last_seen_ms=excluded.last_seen_ms
                        """,
                        (
                            rec["condition_id"],
                            rec["slug"],
                            rec["symbol"],
                            rec["interval_sec"],
                            rec["start_ms"],
                            rec["end_ms"],
                            rec["yes_token_id"],
                            rec["no_token_id"],
                            rec["tick_size"],
                            env.recv_unix_ms,
                            env.recv_unix_ms,
                        ),
                    )
                    stats.market_meta_rows += 1
                continue

            # settlement
            if channel == CHANNEL_MARKET_RESOLVED or source == "settlement":
                rec = normalize_settlement(env)
                if rec and rec["condition_id"] not in settlement_seen:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO settlement_records
                        (condition_id, official_outcome, settle_ms, resolution_source, capture_seq)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["official_outcome"],
                            rec["settle_ms"],
                            rec["resolution_source"],
                            rec["capture_seq"],
                        ),
                    )
                    settlement_seen.add(rec["condition_id"])
                    stats.settlement_rows += 1
                continue

            # inventory
            if channel == CHANNEL_INVENTORY_SNAPSHOT:
                rec = normalize_inventory_event(env)
                if rec:
                    cur.execute(
                        """
                        INSERT INTO own_inventory_events (
                            condition_id, asset_id, outcome, size, avg_price,
                            redeemable, mergeable, source_kind,
                            recv_ms, recv_monotonic_ns, capture_seq
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["asset_id"],
                            rec["outcome"],
                            rec["size"],
                            rec["avg_price"],
                            rec["redeemable"],
                            rec["mergeable"],
                            rec["source_kind"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                        ),
                    )
                    stats.own_inventory_rows += 1
                continue

            # own order stream
            if channel == CHANNEL_USER_ORDER:
                rec = normalize_order_event(env)
                if rec:
                    key = dedup_order_key(rec)
                    if key in order_seen:
                        stats.dedup_skips += 1
                        continue
                    order_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO own_order_events (
                            condition_id, recv_ms, recv_monotonic_ns, capture_seq,
                            client_order_id, order_id, event_type, side, direction,
                            price, size, remaining, status, reason, reject_kind,
                            tx_hash, strategy_tag, round_id
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["client_order_id"],
                            rec["order_id"],
                            rec["event_type"],
                            rec["side"],
                            rec["direction"],
                            rec["price"],
                            rec["size"],
                            rec["remaining"],
                            rec["status"],
                            rec["reason"],
                            rec["reject_kind"],
                            rec["tx_hash"],
                            rec["strategy_tag"],
                            rec["round_id"],
                        ),
                    )
                    stats.own_order_rows += 1
                continue

            if channel == CHANNEL_USER_TRADE:
                rows = normalize_fill_events(env)
                for rec in rows:
                    key = dedup_fill_key(rec)
                    if key in fill_seen:
                        stats.dedup_skips += 1
                        continue
                    fill_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO own_fill_events (
                            condition_id, asset_id, order_id, taker_order_id, trade_id,
                            market_side, direction, trader_side, price, size, fee_rate_bps,
                            match_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            maker_address, tx_hash, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["asset_id"],
                            rec["order_id"],
                            rec["taker_order_id"],
                            rec["trade_id"],
                            rec["market_side"],
                            rec["direction"],
                            rec["trader_side"],
                            rec["price"],
                            rec["size"],
                            rec["fee_rate_bps"],
                            rec["match_ts_ms"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["maker_address"],
                            rec["tx_hash"],
                            rec["raw_json"],
                        ),
                    )
                    stats.own_fill_rows += 1
                continue

            if channel == CHANNEL_USER_WS_LOG:
                rec = normalize_user_ws_log(env)
                if rec:
                    key = (rec["event_name"], rec["event_value"] or "", rec["recv_ms"])
                    if key in user_ws_log_seen:
                        stats.dedup_skips += 1
                        continue
                    user_ws_log_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO user_ws_log (
                            recv_ms, recv_monotonic_ns, capture_seq, event_name, event_value, detail
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["event_name"],
                            rec["event_value"],
                            rec["detail"],
                        ),
                    )
                    stats.user_ws_log_rows += 1
                continue

            # market trades (standardized contract only)
            if channel in {CHANNEL_LAST_TRADE, CHANNEL_TRADES_BACKFILL}:
                rec = normalize_md_trade(env)
                if rec:
                    key = dedup_trade_key(rec)
                    if key in trade_seen:
                        stats.dedup_skips += 1
                        continue
                    trade_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO md_trades (
                            condition_id, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            source_ts_ms, trade_id, market_side, taker_side, maker_address, taker_address,
                            price, size, source_quality, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["trade_ts_ms"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["source_ts_ms"],
                            rec["trade_id"],
                            rec["market_side"],
                            rec["taker_side"],
                            rec["maker_address"],
                            rec["taker_address"],
                            rec["price"],
                            rec["size"],
                            rec["source_quality"],
                            rec["raw_json"],
                        ),
                    )
                    stats.md_trades_rows += 1
                continue

            # market book (standardized contract only)
            if channel in {CHANNEL_BOOK, CHANNEL_BBA}:
                rec = normalize_book_row(env)
                if rec:
                    key = dedup_book_key(rec)
                    cond = rec["condition_id"]
                    if book_last.get(cond) == key:
                        stats.dedup_skips += 1
                        continue
                    book_last[cond] = key
                    cur.execute(
                        """
                        INSERT INTO md_book_l1 (
                            condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                            yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                            yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz,
                            source_kind, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["source_ts_ms"],
                            rec["yes_bid_px"],
                            rec["yes_ask_px"],
                            rec["no_bid_px"],
                            rec["no_ask_px"],
                            rec["yes_bid_sz"],
                            rec["yes_ask_sz"],
                            rec["no_bid_sz"],
                            rec["no_ask_sz"],
                            rec["source_kind"],
                            rec["raw_json"],
                        ),
                    )
                    stats.md_book_rows += 1
                continue

            if channel == CHANNEL_XUAN_TRADES:
                rec = normalize_xuan_trade(env)
                if rec:
                    key = (
                        rec["user"],
                        rec["tx_hash"] or "",
                        rec["trade_id"] or "",
                        rec["trade_ts_ms"],
                        rec["condition_id"] or "",
                        rec["price"],
                        rec["size"],
                    )
                    if key in xuan_trade_seen:
                        stats.dedup_skips += 1
                        continue
                    xuan_trade_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO xuan_trades (
                            user, poll_ts_ms, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            condition_id, slug, event_slug, title, outcome, side,
                            price, size, asset, proxy_wallet, tx_hash, trade_id,
                            source_quality, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["user"],
                            rec["poll_ts_ms"],
                            rec["trade_ts_ms"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["condition_id"],
                            rec["slug"],
                            rec["event_slug"],
                            rec["title"],
                            rec["outcome"],
                            rec["side"],
                            rec["price"],
                            rec["size"],
                            rec["asset"],
                            rec["proxy_wallet"],
                            rec["tx_hash"],
                            rec["trade_id"],
                            rec["source_quality"],
                            rec["raw_json"],
                        ),
                    )
                    stats.xuan_trades_rows += 1
                continue

            if channel == CHANNEL_XUAN_ACTIVITY:
                rec = normalize_xuan_activity(env)
                if rec:
                    key = (
                        rec["user"],
                        rec["tx_hash"] or "",
                        rec["activity_ts_ms"],
                        rec["activity_type"] or "",
                        rec["condition_id"] or "",
                    )
                    if key in xuan_activity_seen:
                        stats.dedup_skips += 1
                        continue
                    xuan_activity_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO xuan_activity (
                            user, poll_ts_ms, activity_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            condition_id, slug, event_slug, title, activity_type, outcome, side,
                            price, size, usdc_size, asset, proxy_wallet, tx_hash,
                            source_quality, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["user"],
                            rec["poll_ts_ms"],
                            rec["activity_ts_ms"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["condition_id"],
                            rec["slug"],
                            rec["event_slug"],
                            rec["title"],
                            rec["activity_type"],
                            rec["outcome"],
                            rec["side"],
                            rec["price"],
                            rec["size"],
                            rec["usdc_size"],
                            rec["asset"],
                            rec["proxy_wallet"],
                            rec["tx_hash"],
                            rec["source_quality"],
                            rec["raw_json"],
                        ),
                    )
                    stats.xuan_activity_rows += 1
                continue

            if channel == CHANNEL_XUAN_POLL_LOG:
                rec = normalize_xuan_poll_log(env)
                if rec:
                    key = (rec["user"], rec["endpoint"], rec["poll_ts_ms"])
                    if key in xuan_poll_seen:
                        stats.dedup_skips += 1
                        continue
                    xuan_poll_seen.add(key)
                    cur.execute(
                        """
                        INSERT INTO xuan_poll_log (
                            user, endpoint, poll_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            rows, max_ts_ms, ok, error
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["user"],
                            rec["endpoint"],
                            rec["poll_ts_ms"],
                            rec["recv_ms"],
                            rec["recv_monotonic_ns"],
                            rec["capture_seq"],
                            rec["rows"],
                            rec["max_ts_ms"],
                            rec["ok"],
                            rec["error"],
                        ),
                    )
                    stats.xuan_poll_log_rows += 1

        conn.commit()
        conn.close()
        return stats


def build_replay_for_day(raw_root: Path, replay_root: Path, day: str) -> BuildStats:
    raw_day_root = Path(raw_root) / day
    replay_db_path = Path(replay_root) / day / "crypto_5m.sqlite"
    # Rebuild day DB from scratch to keep rolling rebuild idempotent.
    for path in (replay_db_path, Path(f"{replay_db_path}-wal"), Path(f"{replay_db_path}-shm")):
        if path.exists():
            path.unlink()
    builder = ReplayBuilder(raw_day_root=raw_day_root, replay_db_path=replay_db_path)
    return builder.build()

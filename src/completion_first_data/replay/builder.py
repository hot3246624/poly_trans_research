"""Build replay SQLite from raw envelope files."""

from __future__ import annotations

import dataclasses
import heapq
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

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
    normalize_direction,
    normalize_side,
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

LOG = logging.getLogger("completion_first_data.replay.builder")
PROGRESS_LOG_EVERY_RECORDS = 250_000
PROGRESS_LOG_MIN_INTERVAL_SEC = 15.0
BOOK_L2_TOP_N = 5
# L2 is a high-cardinality structured replay table. Raw book payloads remain
# available in data/raw; duplicating them per L2 snapshot makes SQLite larger
# than the compressed raw source and is not useful for backtests.
STORE_BOOK_L2_RAW_JSON = False


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _json_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _extract_levels(levels: Any, *, is_bid: bool) -> Dict[float, float]:
    out: Dict[float, float] = {}
    if not isinstance(levels, list):
        return out
    for level in levels:
        px: Optional[float] = None
        sz: Optional[float] = None
        if isinstance(level, dict):
            px = _as_float(level.get("price") or level.get("p") or level.get("value"))
            sz = _as_float(level.get("size") or level.get("s") or level.get("qty") or level.get("amount"))
        elif isinstance(level, (list, tuple)):
            px = _as_float(level[0] if len(level) >= 1 else None)
            sz = _as_float(level[1] if len(level) >= 2 else None)
        if px is None:
            continue
        out[float(px)] = max(0.0, float(sz or 0.0))
    return out


@dataclasses.dataclass(slots=True)
class _DepthSideState:
    bids: Dict[float, float] = dataclasses.field(default_factory=dict)
    asks: Dict[float, float] = dataclasses.field(default_factory=dict)

    def replace_snapshot(self, *, bids: Any, asks: Any) -> None:
        self.bids = {px: sz for px, sz in _extract_levels(bids, is_bid=True).items() if sz > 0.0}
        self.asks = {px: sz for px, sz in _extract_levels(asks, is_bid=False).items() if sz > 0.0}

    def update_level(self, *, order_side: str, price: float, size: float) -> None:
        levels = self.bids if order_side == "BUY" else self.asks
        if size <= 0.0:
            levels.pop(price, None)
        else:
            levels[price] = size

    def top(self, *, order_side: str, depth: int = BOOK_L2_TOP_N) -> List[Tuple[float, float]]:
        levels = self.bids if order_side == "BUY" else self.asks
        reverse = order_side == "BUY"
        return [(px, levels[px]) for px in sorted(levels.keys(), reverse=reverse)[:depth] if levels[px] > 0.0]


def _flatten_levels(levels: List[Tuple[float, float]], depth: int = BOOK_L2_TOP_N) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for idx in range(depth):
        if idx < len(levels):
            out.extend([levels[idx][0], levels[idx][1]])
        else:
            out.extend([None, None])
    return out


def _depth_signature(side_state: _DepthSideState) -> Tuple[Tuple[float, float], ...]:
    return tuple(side_state.top(order_side="BUY") + side_state.top(order_side="SELL"))


@dataclasses.dataclass(slots=True)
class BuildStats:
    raw_files: int = 0
    raw_records: int = 0
    market_meta_rows: int = 0
    md_book_rows: int = 0
    md_book_l2_rows: int = 0
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

    @staticmethod
    def _sort_key(env: RawEnvelope) -> Tuple[int, int, int]:
        return (env.capture_seq, env.recv_monotonic_ns, env.recv_unix_ms)

    def _source_files(self) -> List[Path]:
        return list(glob_jsonl_gz([self.raw_day_root]))

    def _iter_envelopes(self, files: List[Path]) -> Iterator[RawEnvelope]:
        # Each raw file is append-only in capture order. Merge them by capture sequence
        # so replay build stays globally ordered without loading the whole day into memory.
        heap: List[Tuple[Tuple[int, int, int], int, RawEnvelope, Iterator[dict]]] = []
        for file_idx, path in enumerate(files):
            iterator = iter(iter_jsonl_gz(path))
            try:
                env = RawEnvelope.from_dict(next(iterator))
            except StopIteration:
                continue
            heapq.heappush(heap, (self._sort_key(env), file_idx, env, iterator))

        while heap:
            _, file_idx, env, iterator = heapq.heappop(heap)
            yield env
            try:
                next_env = RawEnvelope.from_dict(next(iterator))
            except StopIteration:
                continue
            heapq.heappush(heap, (self._sort_key(next_env), file_idx, next_env, iterator))

    @staticmethod
    def _infer_l2_market_side(
        *,
        condition_id: str,
        payload: Dict[str, Any],
        raw: Dict[str, Any],
        asset_side: Dict[Tuple[str, str], str],
    ) -> Optional[str]:
        explicit = normalize_side(
            payload.get("raw_market_side")
            or payload.get("market_side")
            or raw.get("market_side")
            or raw.get("outcome")
        )
        if explicit:
            return explicit
        asset_id = str(raw.get("asset_id") or raw.get("assetId") or payload.get("asset_id") or "").strip()
        if asset_id:
            return asset_side.get((condition_id, asset_id))
        return None

    @staticmethod
    def _update_l2_state(
        *,
        condition_id: str,
        payload: Dict[str, Any],
        asset_side: Dict[Tuple[str, str], str],
        state: Dict[Tuple[str, str], _DepthSideState],
    ) -> List[Tuple[str, _DepthSideState]]:
        raw = _json_dict(payload.get("raw_json")) or payload
        raw_l2 = payload.get("raw_l2")
        if isinstance(raw_l2, dict):
            updates: List[Tuple[str, _DepthSideState]] = []
            for raw_key, market_side in (("yes", "YES"), ("no", "NO")):
                side_payload = raw_l2.get(raw_key)
                if not isinstance(side_payload, dict):
                    continue
                side_state = state.setdefault((condition_id, market_side), _DepthSideState())
                side_state.replace_snapshot(bids=side_payload.get("bids"), asks=side_payload.get("asks"))
                updates.append((market_side, side_state))
            if updates:
                return updates

        market_side = ReplayBuilder._infer_l2_market_side(
            condition_id=condition_id,
            payload=payload,
            raw=raw,
            asset_side=asset_side,
        )
        if market_side not in {"YES", "NO"}:
            return []

        side_state = state.setdefault((condition_id, market_side), _DepthSideState())
        if isinstance(raw.get("bids"), list) or isinstance(raw.get("asks"), list):
            side_state.replace_snapshot(bids=raw.get("bids"), asks=raw.get("asks"))
            return [(market_side, side_state)]

        order_side = normalize_direction(raw.get("side") or raw.get("book_side") or raw.get("order_side"))
        price = _as_float(raw.get("price"))
        size = _as_float(raw.get("size") or raw.get("amount"))
        if order_side in {"BUY", "SELL"} and price is not None and size is not None:
            side_state.update_level(order_side=order_side, price=float(price), size=max(0.0, float(size)))
            return [(market_side, side_state)]
        return []

    @staticmethod
    def _insert_l2_snapshot(
        cur: sqlite3.Cursor,
        *,
        rec: Dict[str, Any],
        market_side: str,
        side_state: _DepthSideState,
    ) -> None:
        bids = side_state.top(order_side="BUY")
        asks = side_state.top(order_side="SELL")
        cur.execute(
            """
            INSERT INTO md_book_l2 (
                condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                market_side, depth,
                bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz, bid4_px, bid4_sz, bid5_px, bid5_sz,
                ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz, ask4_px, ask4_sz, ask5_px, ask5_sz,
                source_kind, raw_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["condition_id"],
                rec["recv_ms"],
                rec["recv_monotonic_ns"],
                rec["capture_seq"],
                rec["source_ts_ms"],
                market_side,
                BOOK_L2_TOP_N,
                *_flatten_levels(bids),
                *_flatten_levels(asks),
                rec["source_kind"],
                rec["raw_json"] if STORE_BOOK_L2_RAW_JSON else None,
            ),
        )

    def build(self) -> BuildStats:
        stats = BuildStats()
        files = self._source_files()
        stats.raw_files = len(files)
        build_started = time.monotonic()
        last_progress_log = build_started
        next_progress_at = PROGRESS_LOG_EVERY_RECORDS

        LOG.info(
            "replay build started: day=%s raw_files=%d db=%s",
            self.replay_db_path.parent.name,
            stats.raw_files,
            self.replay_db_path,
        )

        self.replay_db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.replay_db_path)
        conn.row_factory = sqlite3.Row
        init_schema(conn)

        book_last: Dict[str, tuple] = {}
        book_l2_state: Dict[Tuple[str, str], _DepthSideState] = {}
        book_l2_last: Dict[Tuple[str, str], Tuple[Tuple[float, float], ...]] = {}
        asset_side: Dict[Tuple[str, str], str] = {}
        trade_seen: set = set()
        xuan_trade_seen: set = set()
        xuan_activity_seen: set = set()
        xuan_poll_seen: set = set()
        order_seen: set = set()
        fill_seen: set = set()
        user_ws_log_seen: set = set()
        settlement_seen: set = set()

        cur = conn.cursor()

        for env in self._iter_envelopes(files):
            stats.raw_records += 1
            if stats.raw_records >= next_progress_at:
                now = time.monotonic()
                if now - last_progress_log >= PROGRESS_LOG_MIN_INTERVAL_SEC:
                    LOG.info(
                        "replay build progress: day=%s raw_records=%d md_book_rows=%d md_book_l2_rows=%d md_trades_rows=%d market_meta_rows=%d settlement_rows=%d dedup_skips=%d current_capture_seq=%d",
                        self.replay_db_path.parent.name,
                        stats.raw_records,
                        stats.md_book_rows,
                        stats.md_book_l2_rows,
                        stats.md_trades_rows,
                        stats.market_meta_rows,
                        stats.settlement_rows,
                        stats.dedup_skips,
                        env.capture_seq,
                    )
                    last_progress_log = now
                next_progress_at += PROGRESS_LOG_EVERY_RECORDS
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
                    if rec.get("yes_token_id"):
                        asset_side[(rec["condition_id"], str(rec["yes_token_id"]))] = "YES"
                    if rec.get("no_token_id"):
                        asset_side[(rec["condition_id"], str(rec["no_token_id"]))] = "NO"
                continue

            # settlement
            if channel == CHANNEL_MARKET_RESOLVED or source == "settlement":
                rec = normalize_settlement(env)
                if rec and rec["condition_id"] not in settlement_seen:
                    cur.execute(
                        """
                        INSERT OR REPLACE INTO settlement_records
                        (
                            condition_id, official_outcome, winner_side, winner_token_id,
                            settle_ms, resolution_source, raw_json, capture_seq
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            rec["condition_id"],
                            rec["official_outcome"],
                            rec["winner_side"],
                            rec["winner_token_id"],
                            rec["settle_ms"],
                            rec["resolution_source"],
                            rec["raw_json"],
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
                    l2_updates = self._update_l2_state(
                        condition_id=rec["condition_id"],
                        payload=env.payload_json,
                        asset_side=asset_side,
                        state=book_l2_state,
                    )
                    for market_side, side_state in l2_updates:
                        l2_key = (rec["condition_id"], market_side)
                        l2_sig = _depth_signature(side_state)
                        if book_l2_last.get(l2_key) != l2_sig:
                            book_l2_last[l2_key] = l2_sig
                            self._insert_l2_snapshot(
                                cur,
                                rec=rec,
                                market_side=market_side,
                                side_state=side_state,
                            )
                            stats.md_book_l2_rows += 1

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
                            condition_id, slug, event_slug, title, outcome, outcome_side, side,
                            price, size, asset, proxy_wallet, tx_hash, trade_id,
                            source_quality, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            rec["outcome_side"],
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
                            condition_id, slug, event_slug, title, activity_type, outcome, outcome_side, side,
                            price, size, usdc_size, asset, proxy_wallet, tx_hash,
                            source_quality, raw_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                            rec["outcome_side"],
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
        LOG.info(
            "replay build finished: day=%s raw_records=%d md_book_rows=%d md_book_l2_rows=%d md_trades_rows=%d market_meta_rows=%d settlement_rows=%d dedup_skips=%d elapsed_sec=%.1f",
            self.replay_db_path.parent.name,
            stats.raw_records,
            stats.md_book_rows,
            stats.md_book_l2_rows,
            stats.md_trades_rows,
            stats.market_meta_rows,
            stats.settlement_rows,
            stats.dedup_skips,
            time.monotonic() - build_started,
        )
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

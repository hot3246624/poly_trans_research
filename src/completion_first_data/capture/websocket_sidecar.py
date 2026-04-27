"""Async sidecar capture for websocket streams and meta polling."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import requests

from .envelope import pick_condition_id
from .settlement import fetch_condition_settlement
from .meta import (
    MarketMetaFetchState,
    MarketMetaRecord,
    fetch_crypto_5m_markets,
    write_market_meta_records,
)
from .raw_store import RawCaptureStore
from .xuan_poller import XuanPollConfig, run_xuan_poll_worker
from ..constants import (
    CHANNEL_BOOK,
    CHANNEL_INVENTORY_SNAPSHOT,
    CHANNEL_LAST_TRADE,
    CHANNEL_MARKET_RESOLVED,
    CHANNEL_USER_ORDER,
    CHANNEL_USER_TRADE,
    CHANNEL_USER_WS_LOG,
    DEFAULT_META_POLL_SEC,
    POLYMARKET_CLOB_BASE_URL,
    SOURCE_KIND_MARKET_WS,
    SOURCE_KIND_SETTLEMENT,
    SOURCE_KIND_USER_WS,
    SOURCE_KIND_INVENTORY,
)
from ..user_truth import (
    InventorySnapshot,
    UserAuthConfig,
    activity_row_to_user_order_payload,
    apply_fill_rows_to_inventory,
    build_user_auth_message,
    build_user_subscribe_message,
    compute_inventory_drift,
    extract_user_trade_rows,
    fetch_open_orders,
    fetch_positions,
    fetch_recent_user_activity,
    fetch_recent_user_trades,
    mask_secret_id,
    normalize_inventory_snapshot,
    now_ms,
    trade_row_to_user_trade_payload,
)

LOG = logging.getLogger(__name__)
_VALID_MARKET_CHANNELS = {"book", "last_trade_price", "best_bid_ask"}
_DEBUG_RAW_CHANNEL = "market_raw_text"
_ALL_MARKET_PREFIXES = {"*", "all", "all-5m", "crypto-5m"}


@dataclass(slots=True)
class WsSourceConfig:
    name: str
    url: str
    enabled: bool = True
    subscribe: List[Dict[str, Any]] = field(default_factory=list)
    default_channel: str = "unknown"
    reconnect_sec: float = 3.0


@dataclass(slots=True)
class UserWsConfig:
    name: str
    url: str
    auth: UserAuthConfig
    enabled: bool = True
    reconnect_sec: float = 3.0
    heartbeat_sec: int = 10
    reconcile_sec: int = 60
    recovery_lookback_sec: int = 300
    rest_url: str = POLYMARKET_CLOB_BASE_URL


@dataclass(slots=True)
class MetaPollConfig:
    enabled: bool = True
    interval_sec: int = DEFAULT_META_POLL_SEC
    active_only: bool = False
    conditional_get: bool = True
    round_switch_delay_sec: int = 8


@dataclass(slots=True)
class SettlementPollConfig:
    enabled: bool = True
    interval_sec: int = 20
    per_condition_cooldown_sec: int = 30
    retention_hours: int = 12


@dataclass(slots=True)
class SidecarConfig:
    raw_root: str
    market_ws: Optional[WsSourceConfig] = None
    user_ws: Optional[UserWsConfig] = None
    meta_poll: Optional[MetaPollConfig] = None
    market_prefixes: List[str] = field(default_factory=list)
    market_channels: List[str] = field(default_factory=lambda: ["book", "last_trade_price"])
    max_markets_per_prefix: int = 1
    debug_raw_market_ws: bool = False
    settlement_poll: Optional[SettlementPollConfig] = None
    xuan_poll: Optional[XuanPollConfig] = None


@dataclass(slots=True)
class MarketSelectionSnapshot:
    revision: int
    selected_condition_ids: List[str]
    asset_ids: List[str]
    asset_to_condition_id: Dict[str, str]
    asset_to_market_side: Dict[str, str]
    subscribe_msg: Optional[Dict[str, Any]]


@dataclass(slots=True)
class _BookSideState:
    bid_px: float = 0.0
    ask_px: float = 0.0
    bid_sz: float = 0.0
    ask_sz: float = 0.0
    seen: bool = False


@dataclass(slots=True)
class BookAssembler:
    """Merge side-tagged partial updates into a full 4-price L1 snapshot."""

    yes: _BookSideState = field(default_factory=_BookSideState)
    no: _BookSideState = field(default_factory=_BookSideState)

    def _side_state(self, side: str) -> _BookSideState:
        return self.yes if side == "YES" else self.no

    def update_snapshot(self, side: str, bids: Any, asks: Any) -> None:
        state = self._side_state(side)
        bid_px, bid_sz = _extract_best_level(bids, is_bid=True)
        ask_px, ask_sz = _extract_best_level(asks, is_bid=False)
        state.bid_px = bid_px
        state.ask_px = ask_px
        state.bid_sz = bid_sz
        state.ask_sz = ask_sz
        state.seen = True

    def update_best_bid_ask(
        self,
        side: str,
        *,
        bid_px: Optional[float],
        ask_px: Optional[float],
        bid_sz: Optional[float] = None,
        ask_sz: Optional[float] = None,
    ) -> None:
        state = self._side_state(side)
        if bid_px is not None:
            state.bid_px = max(0.0, bid_px)
        if ask_px is not None:
            state.ask_px = max(0.0, ask_px)
        if bid_sz is not None:
            state.bid_sz = max(0.0, bid_sz)
        if ask_sz is not None:
            state.ask_sz = max(0.0, ask_sz)
        state.seen = True

    def full_l1(self, *, source_ts_ms: Optional[int]) -> Optional[Dict[str, Any]]:
        if not self.yes.seen or not self.no.seen:
            return None
        # Keep same safety gate as strategy runtime: require both bids available.
        if self.yes.bid_px <= 0.0 or self.no.bid_px <= 0.0:
            return None

        return {
            "yes_bid_px": self.yes.bid_px,
            "yes_ask_px": self.yes.ask_px,
            "no_bid_px": self.no.bid_px,
            "no_ask_px": self.no.ask_px,
            "yes_bid_sz": self.yes.bid_sz,
            "yes_ask_sz": self.yes.ask_sz,
            "no_bid_sz": self.no.bid_sz,
            "no_ask_sz": self.no.ask_sz,
            "source_ts_ms": source_ts_ms,
        }


@dataclass(slots=True)
class InventoryTruthState:
    snapshots: Dict[str, InventorySnapshot] = field(default_factory=dict)
    touched_conditions: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    reconcile_now: asyncio.Event = field(default_factory=asyncio.Event)


class MarketSelectionState:
    """Mutable shared selection state updated by meta poller and read by market WS task."""

    def __init__(self) -> None:
        self._snapshot = MarketSelectionSnapshot(
            revision=0,
            selected_condition_ids=[],
            asset_ids=[],
            asset_to_condition_id={},
            asset_to_market_side={},
            subscribe_msg=None,
        )

    def snapshot(self) -> MarketSelectionSnapshot:
        s = self._snapshot
        return MarketSelectionSnapshot(
            revision=s.revision,
            selected_condition_ids=list(s.selected_condition_ids),
            asset_ids=list(s.asset_ids),
            asset_to_condition_id=dict(s.asset_to_condition_id),
            asset_to_market_side=dict(s.asset_to_market_side),
            subscribe_msg=dict(s.subscribe_msg) if isinstance(s.subscribe_msg, dict) else None,
        )

    def update_from_markets(self, markets: Sequence[MarketMetaRecord]) -> bool:
        subscribe_msg = build_market_subscription_message(markets)
        asset_to_condition_id, asset_to_market_side = build_asset_maps(markets)
        asset_ids = sorted(asset_to_condition_id.keys())
        condition_ids = [m.condition_id for m in markets]

        prev = self._snapshot
        prev_signature = (
            tuple(prev.asset_ids),
            tuple(prev.selected_condition_ids),
        )
        next_signature = (
            tuple(asset_ids),
            tuple(condition_ids),
        )
        if prev_signature == next_signature:
            return False

        self._snapshot = MarketSelectionSnapshot(
            revision=prev.revision + 1,
            selected_condition_ids=condition_ids,
            asset_ids=asset_ids,
            asset_to_condition_id=asset_to_condition_id,
            asset_to_market_side=asset_to_market_side,
            subscribe_msg=subscribe_msg,
        )
        return True


def _load_json(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_sidecar_config(path: str | Path, raw_root_override: Optional[str] = None) -> SidecarConfig:
    cfg = _load_json(path)
    raw_root = raw_root_override or cfg.get("raw_root") or "data/raw"

    def parse_ws(name: str) -> Optional[WsSourceConfig]:
        data = cfg.get(name)
        if not data:
            return None
        return WsSourceConfig(
            name=name,
            url=str(data.get("url") or ""),
            enabled=bool(data.get("enabled", True)),
            subscribe=list(data.get("subscribe") or []),
            default_channel=str(data.get("default_channel") or ("book" if name == "market_ws" else "order")),
            reconnect_sec=float(data.get("reconnect_sec", 3.0)),
        )

    mp = cfg.get("meta_poll") or {}
    meta_poll = MetaPollConfig(
        enabled=bool(mp.get("enabled", True)),
        interval_sec=int(mp.get("interval_sec", DEFAULT_META_POLL_SEC)),
        active_only=bool(mp.get("active_only", False)),
        conditional_get=bool(mp.get("conditional_get", True)),
        round_switch_delay_sec=max(0, int(mp.get("round_switch_delay_sec", 8))),
    )

    sp = cfg.get("settlement_poll") or {}
    settlement_poll = SettlementPollConfig(
        enabled=bool(sp.get("enabled", True)),
        interval_sec=max(1, int(sp.get("interval_sec", 20))),
        per_condition_cooldown_sec=max(1, int(sp.get("per_condition_cooldown_sec", 30))),
        retention_hours=max(1, int(sp.get("retention_hours", 12))),
    )

    xp = cfg.get("xuan_poll") or {}
    xuan_poll = XuanPollConfig(
        enabled=bool(xp.get("enabled", False)),
        user=str(xp.get("user") or ""),
        interval_sec=max(1, int(xp.get("interval_sec", 300))),
        aggressive_interval_sec=max(1, int(xp.get("aggressive_interval_sec", 60))),
        aggressive_trade_threshold=max(1, int(xp.get("aggressive_trade_threshold", 500))),
        page_limit=max(1, int(xp.get("page_limit", 500))),
        max_pages=max(1, int(xp.get("max_pages", 30))),
        cursor_path=str(xp.get("cursor_path") or "") or None,
    )

    return SidecarConfig(
        raw_root=str(raw_root),
        market_ws=parse_ws("market_ws"),
        user_ws=None,
        meta_poll=meta_poll,
        market_prefixes=list(cfg.get("market_prefixes") or []),
        market_channels=list(cfg.get("market_channels") or ["book", "last_trade_price"]),
        max_markets_per_prefix=int(cfg.get("max_markets_per_prefix", 1)),
        debug_raw_market_ws=bool(cfg.get("debug_raw_market_ws", False)),
        settlement_poll=settlement_poll,
        xuan_poll=xuan_poll,
    )


def _normalized_market_channels(channels: Optional[List[str]]) -> List[str]:
    raw = channels or ["book", "last_trade_price"]
    out: List[str] = []
    seen: set[str] = set()
    for item in raw:
        ch = str(item or "").strip()
        if not ch or ch in seen:
            continue
        if ch not in _VALID_MARKET_CHANNELS:
            continue
        seen.add(ch)
        out.append(ch)
    return out or ["book", "last_trade_price"]


def _market_rank(rec: MarketMetaRecord, now_ms: int) -> Tuple[int, int, int]:
    if rec.start_ms <= now_ms < rec.end_ms:
        return (0, now_ms - rec.start_ms, rec.start_ms)
    if rec.start_ms > now_ms:
        return (1, rec.start_ms - now_ms, rec.start_ms)
    return (2, now_ms - rec.end_ms, -rec.end_ms)


def select_markets_by_prefix(
    metas: Sequence[MarketMetaRecord],
    prefixes: Sequence[str],
    *,
    max_markets_per_prefix: int,
    now_ms: Optional[int] = None,
) -> List[MarketMetaRecord]:
    clean = [p.strip().lower() for p in prefixes if p and p.strip()]
    if not clean:
        return []

    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    per_prefix = max(0, int(max_markets_per_prefix))

    out: List[MarketMetaRecord] = []
    seen_condition_ids: set[str] = set()

    for prefix in clean:
        if prefix in _ALL_MARKET_PREFIXES:
            group = list(metas)
        else:
            group = [m for m in metas if m.slug.lower().startswith(prefix)]
        if not group:
            continue

        if per_prefix > 0:
            group = sorted(group, key=lambda m: _market_rank(m, now))[:per_prefix]
        group = sorted(group, key=lambda m: (m.start_ms, m.end_ms, m.slug))

        for rec in group:
            if rec.condition_id in seen_condition_ids:
                continue
            seen_condition_ids.add(rec.condition_id)
            out.append(rec)

    return out


def build_asset_maps(markets: Sequence[MarketMetaRecord]) -> Tuple[Dict[str, str], Dict[str, str]]:
    asset_to_condition: Dict[str, str] = {}
    asset_to_side: Dict[str, str] = {}
    for rec in markets:
        if rec.yes_token_id:
            asset_to_condition[str(rec.yes_token_id)] = rec.condition_id
            asset_to_side[str(rec.yes_token_id)] = "YES"
        if rec.no_token_id:
            asset_to_condition[str(rec.no_token_id)] = rec.condition_id
            asset_to_side[str(rec.no_token_id)] = "NO"
    return asset_to_condition, asset_to_side


def build_market_subscription_message(markets: Sequence[MarketMetaRecord]) -> Optional[Dict[str, Any]]:
    asset_ids: List[str] = []
    seen: set[str] = set()
    for rec in markets:
        for token in (rec.yes_token_id, rec.no_token_id):
            token_id = str(token or "").strip()
            if not token_id or token_id in seen:
                continue
            seen.add(token_id)
            asset_ids.append(token_id)

    if not asset_ids:
        return None

    return {
        "type": "market",
        "operation": "subscribe",
        "markets": [],
        "assets_ids": asset_ids,
        "asset_ids": asset_ids,
        "initial_dump": True,
    }


def build_market_subscriptions_by_prefix(
    prefixes: List[str],
    *,
    channels: Optional[List[str]] = None,
    max_markets_per_prefix: int = 1,
) -> List[Dict[str, Any]]:
    """Compatibility wrapper: return the single official market subscribe payload."""
    del channels  # Channels are local filters only in the official market subscribe schema.
    metas = fetch_crypto_5m_markets(active_only=True)
    selected = select_markets_by_prefix(metas, prefixes, max_markets_per_prefix=max_markets_per_prefix)
    subscribe = build_market_subscription_message(selected)
    return [subscribe] if subscribe else []


def _parse_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _parse_price(value: Any) -> Optional[float]:
    v = _parse_float(value)
    if v is None:
        return None
    if v < 0.0 or v > 1.0:
        return None
    return v


def _parse_size(value: Any) -> Optional[float]:
    v = _parse_float(value)
    if v is None:
        return None
    return max(0.0, v)


def _parse_direction(value: Any) -> Optional[str]:
    txt = str(value or "").strip().upper()
    if txt in {"BUY", "SELL"}:
        return txt
    return None


def _parse_address(value: Any) -> Optional[str]:
    txt = str(value or "").strip()
    if not txt:
        return None
    return txt


def _parse_ts_ms(value: Any) -> Optional[int]:
    v = _parse_float(value)
    if v is None:
        return None
    if v <= 0:
        return None
    if v < 1_000_000_000_000:
        return int(v * 1000)
    return int(v)


def _extract_best_level(levels: Any, *, is_bid: bool) -> Tuple[float, float]:
    if not isinstance(levels, list) or not levels:
        return 0.0, 0.0

    best_px: Optional[float] = None
    best_sz: float = 0.0
    for level in levels:
        px: Optional[float] = None
        sz: Optional[float] = None
        if isinstance(level, dict):
            px = _parse_price(level.get("price") or level.get("p") or level.get("value"))
            sz = _parse_size(level.get("size") or level.get("s") or level.get("qty") or level.get("amount"))
        elif isinstance(level, (list, tuple)):
            px = _parse_price(level[0] if len(level) >= 1 else None)
            sz = _parse_size(level[1] if len(level) >= 2 else None)

        if px is None:
            continue
        if best_px is None:
            best_px = px
            best_sz = sz or 0.0
            continue

        if is_bid and px > best_px:
            best_px = px
            best_sz = sz or 0.0
        elif (not is_bid) and px < best_px:
            best_px = px
            best_sz = sz or 0.0

    if best_px is None:
        return 0.0, 0.0
    return float(best_px), float(best_sz)


def _iter_ws_objects(parsed: Any) -> Iterable[Dict[str, Any]]:
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                yield item
        return

    if not isinstance(parsed, dict):
        return

    payload = parsed.get("data")
    if isinstance(payload, list):
        for item in payload:
            if not isinstance(item, dict):
                continue
            merged = dict(item)
            for key in ("event_type", "channel", "market", "asset_id", "timestamp"):
                if key not in merged and key in parsed:
                    merged[key] = parsed[key]
            yield merged
        return

    if isinstance(payload, dict):
        merged = dict(payload)
        for key in ("event_type", "channel", "market", "asset_id", "timestamp"):
            if key not in merged and key in parsed:
                merged[key] = parsed[key]
        yield merged
        return

    yield parsed


def _event_type(msg: Dict[str, Any]) -> str:
    return str(msg.get("event_type") or msg.get("type") or msg.get("channel") or msg.get("event") or "").strip().lower()


async def _wait_or_stop(stop_event: asyncio.Event, timeout_sec: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(0.0, timeout_sec))
        return True
    except asyncio.TimeoutError:
        return False


def _allowed_market_events(channels: Sequence[str]) -> set[str]:
    clean = set(_normalized_market_channels(list(channels)))
    allowed: set[str] = set()
    if "book" in clean or "best_bid_ask" in clean:
        allowed.update({"book", "price_change", "best_bid_ask"})
    if "last_trade_price" in clean:
        allowed.update({"last_trade_price", "trade", "tick"})
    return allowed


def _handle_book_snapshot(
    msg: Dict[str, Any],
    *,
    asset_to_condition_id: Dict[str, str],
    asset_to_market_side: Dict[str, str],
    assemblers: Dict[str, BookAssembler],
) -> List[Tuple[str, Dict[str, Any], str]]:
    out: List[Tuple[str, Dict[str, Any], str]] = []
    asset_id = str(msg.get("asset_id") or "").strip()
    if not asset_id:
        return out

    condition_id = asset_to_condition_id.get(asset_id) or str(msg.get("market") or "").strip()
    side = asset_to_market_side.get(asset_id)
    if not condition_id or side not in {"YES", "NO"}:
        return out

    asm = assemblers.setdefault(condition_id, BookAssembler())
    asm.update_snapshot(side, msg.get("bids") or msg.get("buys"), msg.get("asks") or msg.get("sells"))

    source_ts_ms = _parse_ts_ms(msg.get("timestamp") or msg.get("source_ts_ms"))
    full = asm.full_l1(source_ts_ms=source_ts_ms)
    if full is None:
        return out
    full["condition_id"] = condition_id
    full["raw_json"] = msg
    out.append((CHANNEL_BOOK, full, condition_id))
    return out


def _handle_price_change(
    msg: Dict[str, Any],
    *,
    asset_to_condition_id: Dict[str, str],
    asset_to_market_side: Dict[str, str],
    assemblers: Dict[str, BookAssembler],
) -> List[Tuple[str, Dict[str, Any], str]]:
    out: List[Tuple[str, Dict[str, Any], str]] = []
    changes = msg.get("price_changes")
    if not isinstance(changes, list):
        return out

    for change in changes:
        if not isinstance(change, dict):
            continue
        asset_id = str(change.get("asset_id") or "").strip()
        condition_id = asset_to_condition_id.get(asset_id)
        side = asset_to_market_side.get(asset_id)
        if not condition_id or side not in {"YES", "NO"}:
            continue

        best_bid = _parse_price(change.get("best_bid"))
        best_ask = _parse_price(change.get("best_ask"))
        if best_bid is None and best_ask is None:
            continue

        asm = assemblers.setdefault(condition_id, BookAssembler())
        asm.update_best_bid_ask(side, bid_px=best_bid, ask_px=best_ask)

        source_ts_ms = _parse_ts_ms(change.get("timestamp") or msg.get("timestamp") or change.get("source_ts_ms"))
        full = asm.full_l1(source_ts_ms=source_ts_ms)
        if full is None:
            continue
        full["condition_id"] = condition_id
        full["raw_json"] = change
        out.append((CHANNEL_BOOK, full, condition_id))

    return out


def _handle_best_bid_ask(
    msg: Dict[str, Any],
    *,
    asset_to_condition_id: Dict[str, str],
    asset_to_market_side: Dict[str, str],
    assemblers: Dict[str, BookAssembler],
) -> List[Tuple[str, Dict[str, Any], str]]:
    out: List[Tuple[str, Dict[str, Any], str]] = []
    asset_id = str(msg.get("asset_id") or "").strip()
    condition_id = asset_to_condition_id.get(asset_id) or str(msg.get("market") or "").strip()
    side = asset_to_market_side.get(asset_id)
    if not condition_id or side not in {"YES", "NO"}:
        return out

    best_bid = _parse_price(msg.get("best_bid") or msg.get("bid"))
    best_ask = _parse_price(msg.get("best_ask") or msg.get("ask"))
    if best_bid is None and best_ask is None:
        return out

    bid_sz = _parse_size(msg.get("best_bid_size") or msg.get("bid_size"))
    ask_sz = _parse_size(msg.get("best_ask_size") or msg.get("ask_size"))

    asm = assemblers.setdefault(condition_id, BookAssembler())
    asm.update_best_bid_ask(side, bid_px=best_bid, ask_px=best_ask, bid_sz=bid_sz, ask_sz=ask_sz)

    source_ts_ms = _parse_ts_ms(msg.get("timestamp") or msg.get("source_ts_ms"))
    full = asm.full_l1(source_ts_ms=source_ts_ms)
    if full is None:
        return out
    full["condition_id"] = condition_id
    full["raw_json"] = msg
    out.append((CHANNEL_BOOK, full, condition_id))
    return out


def _handle_last_trade_price(
    msg: Dict[str, Any],
    *,
    asset_to_condition_id: Dict[str, str],
    asset_to_market_side: Dict[str, str],
) -> List[Tuple[str, Dict[str, Any], str]]:
    out: List[Tuple[str, Dict[str, Any], str]] = []
    asset_id = str(msg.get("asset_id") or "").strip()
    condition_id = asset_to_condition_id.get(asset_id) or str(msg.get("market") or "").strip()
    market_side = asset_to_market_side.get(asset_id)
    if not condition_id or market_side not in {"YES", "NO"}:
        return out

    price = _parse_price(msg.get("price") or msg.get("last_trade_price"))
    size = _parse_size(msg.get("size") or msg.get("amount"))
    if price is None or size is None or size <= 0.0:
        return out

    trade_ts_ms = _parse_ts_ms(msg.get("timestamp") or msg.get("trade_ts_ms"))
    trade_id = str(
        msg.get("trade_id")
        or msg.get("id")
        or msg.get("transaction_hash")
        or msg.get("hash")
        or ""
    ).strip() or None

    payload = {
        "condition_id": condition_id,
        "trade_id": trade_id,
        "market_side": market_side,
        "taker_side": _parse_direction(msg.get("taker_side") or msg.get("side")),
        "maker_address": _parse_address(
            msg.get("maker_address")
            or msg.get("maker")
            or msg.get("maker_proxy_wallet")
            or msg.get("makerProxyWallet")
        ),
        "taker_address": _parse_address(
            msg.get("taker_address")
            or msg.get("taker")
            or msg.get("proxyWallet")
            or msg.get("proxy_wallet")
            or msg.get("taker_proxy_wallet")
            or msg.get("takerProxyWallet")
        ),
        "price": price,
        "size": size,
        "trade_ts_ms": trade_ts_ms,
        "source_ts_ms": trade_ts_ms,
        "source_quality": "ws",
        "raw_json": msg,
    }
    out.append((CHANNEL_LAST_TRADE, payload, condition_id))
    return out


def normalize_market_ws_message(
    msg: Dict[str, Any],
    *,
    allowed_events: set[str],
    asset_to_condition_id: Dict[str, str],
    asset_to_market_side: Dict[str, str],
    assemblers: Dict[str, BookAssembler],
) -> List[Tuple[str, Dict[str, Any], str]]:
    evt = _event_type(msg)
    if evt not in allowed_events:
        return []

    if evt == "book":
        return _handle_book_snapshot(
            msg,
            asset_to_condition_id=asset_to_condition_id,
            asset_to_market_side=asset_to_market_side,
            assemblers=assemblers,
        )

    if evt == "price_change":
        return _handle_price_change(
            msg,
            asset_to_condition_id=asset_to_condition_id,
            asset_to_market_side=asset_to_market_side,
            assemblers=assemblers,
        )

    if evt == "best_bid_ask":
        return _handle_best_bid_ask(
            msg,
            asset_to_condition_id=asset_to_condition_id,
            asset_to_market_side=asset_to_market_side,
            assemblers=assemblers,
        )

    if evt in {"last_trade_price", "trade", "tick"}:
        return _handle_last_trade_price(
            msg,
            asset_to_condition_id=asset_to_condition_id,
            asset_to_market_side=asset_to_market_side,
        )

    return []


def _write_user_log(
    raw_store: RawCaptureStore,
    *,
    event_name: str,
    event_value: Optional[str] = None,
    detail: Optional[str] = None,
    recv_unix_ms: Optional[int] = None,
) -> None:
    payload: Dict[str, Any] = {
        "event_name": event_name,
    }
    if event_value:
        payload["event_value"] = event_value
    if detail:
        payload["detail"] = detail
    raw_store.write(
        source=SOURCE_KIND_USER_WS,
        channel=CHANNEL_USER_WS_LOG,
        payload_json=payload,
        recv_unix_ms=recv_unix_ms,
    )


def _write_inventory_snapshots(
    raw_store: RawCaptureStore,
    snapshots: Sequence[InventorySnapshot],
    *,
    recv_unix_ms: int,
) -> None:
    for snapshot in snapshots:
        raw_store.write(
            source=SOURCE_KIND_INVENTORY,
            channel=CHANNEL_INVENTORY_SNAPSHOT,
            payload_json=snapshot.as_payload(),
            condition_id=snapshot.condition_id,
            recv_unix_ms=recv_unix_ms,
        )


def _normalize_position_snapshots(
    rows: Sequence[Dict[str, Any]],
    *,
    source_kind: str,
    condition_filter: Optional[set[str]] = None,
) -> List[InventorySnapshot]:
    out: List[InventorySnapshot] = []
    for row in rows:
        snap = normalize_inventory_snapshot(row, source_kind=source_kind)
        if snap is None:
            continue
        if condition_filter and snap.condition_id not in condition_filter:
            continue
        out.append(snap)
    return out


def _materialize_reconcile_snapshots(
    current_state: Dict[str, InventorySnapshot],
    fetched: Sequence[InventorySnapshot],
    *,
    condition_ids: Sequence[str],
) -> List[InventorySnapshot]:
    wanted = {str(v or "").strip() for v in condition_ids if str(v or "").strip()}
    if not wanted:
        return list(fetched)

    by_asset: Dict[str, InventorySnapshot] = {
        snap.asset_id: snap for snap in fetched if snap.condition_id in wanted
    }
    for asset_id, current in current_state.items():
        if current.condition_id not in wanted or asset_id in by_asset:
            continue
        by_asset[asset_id] = InventorySnapshot(
            condition_id=current.condition_id,
            asset_id=current.asset_id,
            outcome=current.outcome,
            size=0.0,
            avg_price=None,
            redeemable=0,
            mergeable=current.mergeable,
            source_kind="reconcile",
        )
    return list(by_asset.values())


def _open_order_to_payload(row: Dict[str, Any], *, funder_address: str) -> Dict[str, Any]:
    ts_ms = _parse_ts_ms(row.get("timestamp")) or now_ms()
    return {
        "event_type": "order",
        "type": str(row.get("type") or "UPDATE"),
        "market": row.get("market"),
        "asset_id": row.get("asset_id") or row.get("assetId") or row.get("asset"),
        "side": row.get("side"),
        "original_size": row.get("original_size") or row.get("originalSize") or row.get("size"),
        "size_matched": row.get("size_matched") or row.get("sizeMatched") or row.get("filled_size"),
        "price": row.get("price"),
        "outcome": row.get("outcome"),
        "status": row.get("status") or "LIVE",
        "timestamp": ts_ms,
        "created_at": _parse_ts_ms(row.get("created_at") or row.get("createdAt")) or ts_ms,
        "maker_address": row.get("maker_address") or row.get("makerAddress"),
        "transaction_hash": row.get("transaction_hash") or row.get("tx_hash") or row.get("txHash"),
        "id": row.get("id") or row.get("order_id") or row.get("orderId"),
        "owner": row.get("owner") or funder_address,
        "order_owner": row.get("order_owner") or row.get("owner") or funder_address,
        "source_quality": "recovery_open_orders",
        "capture_funder_address": funder_address,
        "raw_json": row,
    }


async def _run_inventory_bootstrap(
    raw_store: RawCaptureStore,
    inventory_state: InventoryTruthState,
    session: requests.Session,
    *,
    user_address: str,
) -> None:
    if not user_address:
        return
    rows = await asyncio.to_thread(fetch_positions, session, user=user_address)
    snapshots = _normalize_position_snapshots(rows, source_kind="bootstrap")
    recv_ms = now_ms()
    async with inventory_state.lock:
        inventory_state.snapshots = {snap.asset_id: snap for snap in snapshots}
        inventory_state.touched_conditions.update(snap.condition_id for snap in snapshots)
    _write_inventory_snapshots(raw_store, snapshots, recv_unix_ms=recv_ms)


async def _run_inventory_reconcile(
    raw_store: RawCaptureStore,
    inventory_state: InventoryTruthState,
    session: requests.Session,
    *,
    user_address: str,
    condition_ids: Sequence[str],
) -> None:
    if not user_address:
        return
    rows = await asyncio.to_thread(fetch_positions, session, user=user_address)
    fetched = _normalize_position_snapshots(rows, source_kind="reconcile")
    recv_ms = now_ms()

    async with inventory_state.lock:
        reconcile_rows = _materialize_reconcile_snapshots(
            inventory_state.snapshots,
            fetched,
            condition_ids=condition_ids,
        )
        drifts = compute_inventory_drift(
            inventory_state.snapshots,
            reconcile_rows,
            condition_ids=condition_ids,
        )
        for snapshot in reconcile_rows:
            inventory_state.snapshots[snapshot.asset_id] = snapshot
            inventory_state.touched_conditions.add(snapshot.condition_id)

    _write_inventory_snapshots(raw_store, reconcile_rows, recv_unix_ms=recv_ms)
    if drifts:
        _write_user_log(
            raw_store,
            event_name="inventory_truth_degraded",
            detail=json.dumps(drifts[:20], ensure_ascii=False),
            recv_unix_ms=recv_ms,
        )


async def _inventory_reconcile_loop(
    raw_store: RawCaptureStore,
    cfg: UserWsConfig,
    inventory_state: InventoryTruthState,
    session: requests.Session,
    stop_event: asyncio.Event,
    *,
    selection_state: MarketSelectionState,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(inventory_state.reconcile_now.wait(), timeout=max(1, cfg.reconcile_sec))
            inventory_state.reconcile_now.clear()
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break

        async with inventory_state.lock:
            condition_ids = sorted(inventory_state.touched_conditions)
        if not condition_ids:
            snap = selection_state.snapshot()
            condition_ids = list(snap.selected_condition_ids)
        if not condition_ids:
            continue

        try:
            await _run_inventory_reconcile(
                raw_store,
                inventory_state,
                session,
                user_address=cfg.auth.funder_address,
                condition_ids=condition_ids,
            )
        except Exception as exc:
            LOG.warning("inventory reconcile failed: %s", exc)


async def _run_user_recovery(
    raw_store: RawCaptureStore,
    cfg: UserWsConfig,
    session: requests.Session,
    *,
    selection_state: MarketSelectionState,
    inventory_state: InventoryTruthState,
) -> None:
    recv_ms = now_ms()
    since_ms = recv_ms - (max(1, cfg.recovery_lookback_sec) * 1000)
    selection = selection_state.snapshot()
    selected_markets = selection.selected_condition_ids

    try:
        for row in await asyncio.to_thread(fetch_open_orders, cfg.auth, selected_markets):
            payload = _open_order_to_payload(row, funder_address=cfg.auth.funder_address)
            raw_store.write(
                source=SOURCE_KIND_USER_WS,
                channel=CHANNEL_USER_ORDER,
                payload_json=payload,
                condition_id=str(payload.get("market") or ""),
                recv_unix_ms=recv_ms,
            )
    except Exception as exc:
        LOG.warning("user recovery open-orders failed: %s", exc)

    try:
        trades = await asyncio.to_thread(
            fetch_recent_user_trades,
            session,
            user=cfg.auth.funder_address,
            since_ms=since_ms,
        )
        for row in trades:
            payload = trade_row_to_user_trade_payload(row)
            if not payload:
                continue
            payload["capture_funder_address"] = cfg.auth.funder_address
            raw_store.write(
                source=SOURCE_KIND_USER_WS,
                channel=CHANNEL_USER_TRADE,
                payload_json=payload,
                condition_id=str(payload.get("market") or ""),
                recv_unix_ms=recv_ms,
            )
    except Exception as exc:
        LOG.warning("user recovery trades failed: %s", exc)

    try:
        activity = await asyncio.to_thread(
            fetch_recent_user_activity,
            session,
            user=cfg.auth.funder_address,
            since_ms=since_ms,
        )
        for row in activity:
            payload = activity_row_to_user_order_payload(row)
            if not payload:
                continue
            payload["capture_funder_address"] = cfg.auth.funder_address
            raw_store.write(
                source=SOURCE_KIND_USER_WS,
                channel=CHANNEL_USER_ORDER,
                payload_json=payload,
                condition_id=str(payload.get("market") or ""),
                recv_unix_ms=recv_ms,
            )
    except Exception as exc:
        LOG.warning("user recovery activity failed: %s", exc)

    try:
        condition_ids = selected_markets
        if not condition_ids:
            async with inventory_state.lock:
                condition_ids = sorted(inventory_state.touched_conditions)
        await _run_inventory_reconcile(
            raw_store,
            inventory_state,
            session,
            user_address=cfg.auth.funder_address,
            condition_ids=condition_ids,
        )
    except Exception as exc:
        LOG.warning("user recovery reconcile failed: %s", exc)


async def _send_user_heartbeat(ws: Any, stop_event: asyncio.Event, heartbeat_sec: int) -> None:
    while not stop_event.is_set():
        await asyncio.sleep(max(1, heartbeat_sec))
        if stop_event.is_set():
            return
        await ws.send("{}")


async def _consume_user_ws(
    raw_store: RawCaptureStore,
    cfg: UserWsConfig,
    stop_event: asyncio.Event,
    *,
    selection_state: MarketSelectionState,
) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("Missing dependency 'websockets'. Install it in requirements.txt") from exc

    session = requests.Session()
    inventory_state = InventoryTruthState()
    try:
        await _run_inventory_bootstrap(
            raw_store,
            inventory_state,
            session,
            user_address=cfg.auth.funder_address,
        )
    except Exception as exc:
        LOG.warning("inventory bootstrap failed: %s", exc)

    reconcile_task = asyncio.create_task(
        _inventory_reconcile_loop(
            raw_store,
            cfg,
            inventory_state,
            session,
            stop_event,
            selection_state=selection_state,
        ),
        name="inventory_reconcile",
    )

    try:
        while not stop_event.is_set():
            selection = selection_state.snapshot()
            if not selection.selected_condition_ids:
                await asyncio.sleep(1.0)
                continue

            auth_logged = False
            subscribed_revision = -1
            recovery_task: Optional[asyncio.Task] = None
            heartbeat_task: Optional[asyncio.Task] = None

            try:
                async with websockets.connect(cfg.url, ping_interval=None, ping_timeout=None, max_size=None) as ws:
                    LOG.info(
                        "Connected: %s (auth=%s, api_key=%s)",
                        cfg.name,
                        cfg.auth.auth_source,
                        mask_secret_id(cfg.auth.api_key),
                    )
                    await ws.send(json.dumps(build_user_auth_message(cfg.auth), ensure_ascii=False))
                    heartbeat_task = asyncio.create_task(
                        _send_user_heartbeat(ws, stop_event, cfg.heartbeat_sec),
                        name="user_ws_heartbeat",
                    )

                    while not stop_event.is_set():
                        latest = selection_state.snapshot()
                        if latest.revision != subscribed_revision:
                            await ws.send(
                                json.dumps(
                                    build_user_subscribe_message(latest.selected_condition_ids),
                                    ensure_ascii=False,
                                )
                            )
                            subscribed_revision = latest.revision
                            LOG.info(
                                "updated user subscription: revision=%d markets=%d",
                                latest.revision,
                                len(latest.selected_condition_ids),
                            )

                        try:
                            raw_msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        except asyncio.TimeoutError:
                            if not auth_logged and subscribed_revision >= 0:
                                auth_logged = True
                                _write_user_log(
                                    raw_store,
                                    event_name="auth_success",
                                    event_value=cfg.auth.auth_source,
                                )
                                recovery_task = asyncio.create_task(
                                    _run_user_recovery(
                                        raw_store,
                                        cfg,
                                        session,
                                        selection_state=selection_state,
                                        inventory_state=inventory_state,
                                    ),
                                    name="user_ws_recovery",
                                )
                            continue

                        if isinstance(raw_msg, bytes):
                            raw_msg = raw_msg.decode("utf-8", errors="replace")

                        if str(raw_msg).strip() in {"", "{}"}:
                            if not auth_logged:
                                auth_logged = True
                                _write_user_log(
                                    raw_store,
                                    event_name="auth_success",
                                    event_value=cfg.auth.auth_source,
                                )
                                recovery_task = asyncio.create_task(
                                    _run_user_recovery(
                                        raw_store,
                                        cfg,
                                        session,
                                        selection_state=selection_state,
                                        inventory_state=inventory_state,
                                    ),
                                    name="user_ws_recovery",
                                )
                            continue

                        try:
                            parsed = json.loads(raw_msg)
                        except json.JSONDecodeError:
                            continue

                        if isinstance(parsed, dict) and parsed.get("error"):
                            _write_user_log(
                                raw_store,
                                event_name="auth_error",
                                detail=str(parsed.get("error")),
                            )
                            continue

                        for item in _iter_ws_objects(parsed):
                            if not auth_logged:
                                auth_logged = True
                                _write_user_log(
                                    raw_store,
                                    event_name="auth_success",
                                    event_value=cfg.auth.auth_source,
                                )
                                recovery_task = asyncio.create_task(
                                    _run_user_recovery(
                                        raw_store,
                                        cfg,
                                        session,
                                        selection_state=selection_state,
                                        inventory_state=inventory_state,
                                    ),
                                    name="user_ws_recovery",
                                )

                            item["capture_funder_address"] = cfg.auth.funder_address
                            evt = _event_type(item)
                            if evt == "order":
                                raw_store.write(
                                    source=SOURCE_KIND_USER_WS,
                                    channel=CHANNEL_USER_ORDER,
                                    payload_json=item,
                                    condition_id=pick_condition_id(item),
                                )
                                continue

                            if evt != "trade":
                                continue

                            raw_store.write(
                                source=SOURCE_KIND_USER_WS,
                                channel=CHANNEL_USER_TRADE,
                                payload_json=item,
                                condition_id=pick_condition_id(item),
                            )

                            fill_rows = extract_user_trade_rows(
                                item,
                                funder_address=cfg.auth.funder_address,
                            )
                            if not fill_rows:
                                continue

                            async with inventory_state.lock:
                                derived = apply_fill_rows_to_inventory(inventory_state.snapshots, fill_rows)
                                inventory_state.touched_conditions.update(
                                    row["condition_id"] for row in fill_rows if row.get("condition_id")
                                )
                            if derived:
                                _write_inventory_snapshots(raw_store, derived, recv_unix_ms=now_ms())
                                inventory_state.reconcile_now.set()
            except Exception:
                LOG.exception("WS %s disconnected; reconnect in %.1fs", cfg.name, cfg.reconnect_sec)
                if await _wait_or_stop(stop_event, cfg.reconnect_sec):
                    break
            finally:
                if heartbeat_task is not None:
                    heartbeat_task.cancel()
                    await asyncio.gather(heartbeat_task, return_exceptions=True)
                if recovery_task is not None:
                    await asyncio.gather(recovery_task, return_exceptions=True)
    finally:
        reconcile_task.cancel()
        await asyncio.gather(reconcile_task, return_exceptions=True)
        session.close()


async def _consume_market_ws(
    raw_store: RawCaptureStore,
    cfg: WsSourceConfig,
    stop_event: asyncio.Event,
    *,
    selection_state: MarketSelectionState,
    allowed_events: set[str],
    debug_raw_market_ws: bool,
) -> None:
    try:
        import websockets
    except ImportError as exc:
        raise RuntimeError("Missing dependency 'websockets'. Install it in requirements.txt") from exc

    assemblers: Dict[str, BookAssembler] = {}

    while not stop_event.is_set():
        selection = selection_state.snapshot()
        if not selection.subscribe_msg:
            await asyncio.sleep(1.0)
            continue

        try:
            async with websockets.connect(cfg.url, ping_interval=20, ping_timeout=20, max_size=None) as ws:
                LOG.info(
                    "Connected: %s (revision=%d, assets=%d)",
                    cfg.name,
                    selection.revision,
                    len(selection.asset_ids),
                )
                await ws.send(json.dumps(selection.subscribe_msg, ensure_ascii=False))

                while not stop_event.is_set():
                    # Meta poll switched active token set -> reconnect and resubscribe.
                    if selection_state.snapshot().revision != selection.revision:
                        LOG.info("market token set changed; reconnect to apply new subscription")
                        break

                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    if isinstance(raw_msg, bytes):
                        raw_msg = raw_msg.decode("utf-8", errors="replace")

                    if debug_raw_market_ws:
                        raw_store.write(
                            source=SOURCE_KIND_MARKET_WS,
                            channel=_DEBUG_RAW_CHANNEL,
                            payload_json={"raw_text": raw_msg},
                        )

                    try:
                        parsed = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    for item in _iter_ws_objects(parsed):
                        normalized = normalize_market_ws_message(
                            item,
                            allowed_events=allowed_events,
                            asset_to_condition_id=selection.asset_to_condition_id,
                            asset_to_market_side=selection.asset_to_market_side,
                            assemblers=assemblers,
                        )
                        for channel, payload_json, condition_id in normalized:
                            raw_store.write(
                                source=SOURCE_KIND_MARKET_WS,
                                channel=channel,
                                payload_json=payload_json,
                                condition_id=condition_id,
                            )
        except Exception:
            LOG.exception("WS %s disconnected; reconnect in %.1fs", cfg.name, cfg.reconnect_sec)
            if await _wait_or_stop(stop_event, cfg.reconnect_sec):
                break


async def _meta_poller(
    raw_store: RawCaptureStore,
    cfg: MetaPollConfig,
    stop_event: asyncio.Event,
    *,
    selection_state: Optional[MarketSelectionState],
    market_prefixes: Sequence[str],
    max_markets_per_prefix: int,
) -> None:
    fetch_state = MarketMetaFetchState() if cfg.conditional_get else None
    pending_tracked: Optional[List[MarketMetaRecord]] = None
    pending_apply_at_ms: Optional[int] = None

    def _apply_selection(markets: Sequence[MarketMetaRecord], *, reason: str) -> None:
        if selection_state is None or not market_prefixes:
            return
        changed = selection_state.update_from_markets(markets)
        if changed:
            snap = selection_state.snapshot()
            LOG.info(
                "selected active markets updated: conditions=%d assets=%d revision=%d (%s)",
                len(snap.selected_condition_ids),
                len(snap.asset_ids),
                snap.revision,
                reason,
            )

    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        if pending_tracked is not None and pending_apply_at_ms is not None and now_ms >= pending_apply_at_ms:
            _apply_selection(pending_tracked, reason="delayed_switch")
            pending_tracked = None
            pending_apply_at_ms = None

        try:
            markets = await asyncio.to_thread(
                fetch_crypto_5m_markets,
                active_only=cfg.active_only,
                fetch_state=fetch_state,
            )
            if fetch_state is not None and fetch_state.last_poll_not_modified:
                LOG.info("meta poll not modified (HTTP 304)")
            else:
                tracked = (
                    select_markets_by_prefix(
                        markets,
                        market_prefixes,
                        max_markets_per_prefix=max_markets_per_prefix,
                    )
                    if market_prefixes
                    else list(markets)
                )

                # Guard against transient Gamma fetch failures returning empty sets.
                # Keep the last good subscription instead of dropping all assets.
                if selection_state is not None and market_prefixes and not tracked:
                    snap = selection_state.snapshot()
                    if snap.asset_ids:
                        LOG.warning(
                            "meta poll returned 0 tracked markets; keep previous selection (revision=%d, assets=%d)",
                            snap.revision,
                            len(snap.asset_ids),
                        )
                        if await _wait_or_stop(stop_event, max(1, cfg.interval_sec)):
                            return
                        continue

                written = write_market_meta_records(raw_store, tracked)
                LOG.info("meta poll captured %d markets (fetched=%d)", written, len(markets))

                if selection_state is not None and market_prefixes:
                    snap = selection_state.snapshot()
                    current_sig = tuple(snap.selected_condition_ids)
                    next_sig = tuple(m.condition_id for m in tracked)
                    if current_sig != next_sig:
                        delay_sec = max(0, int(cfg.round_switch_delay_sec))
                        if delay_sec > 0 and snap.asset_ids:
                            pending_tracked = list(tracked)
                            pending_apply_at_ms = now_ms + (delay_sec * 1000)
                            LOG.info(
                                "detected round switch: keep old subscription for %ds before switching",
                                delay_sec,
                            )
                        else:
                            _apply_selection(tracked, reason="immediate_switch")
                            pending_tracked = None
                            pending_apply_at_ms = None
        except Exception:
            LOG.exception("meta poll failed")

        if await _wait_or_stop(stop_event, max(1, cfg.interval_sec)):
            return


async def _settlement_poller(
    raw_store: RawCaptureStore,
    cfg: SettlementPollConfig,
    stop_event: asyncio.Event,
    *,
    selection_state: MarketSelectionState,
) -> None:
    if not cfg.enabled:
        return

    session = requests.Session()
    seen_conditions: Dict[str, int] = {}
    next_check_ms: Dict[str, int] = {}
    resolved_conditions: set[str] = set()

    while not stop_event.is_set():
        now_ms = int(time.time() * 1000)
        snap = selection_state.snapshot()
        for cid in snap.selected_condition_ids:
            seen_conditions[cid] = now_ms

        # Prune stale conditions to bound memory.
        retention_ms = max(1, int(cfg.retention_hours)) * 3600 * 1000
        stale_cids = [cid for cid, ts in seen_conditions.items() if now_ms - ts > retention_ms]
        for cid in stale_cids:
            seen_conditions.pop(cid, None)
            next_check_ms.pop(cid, None)
            resolved_conditions.discard(cid)

        for cid in list(seen_conditions.keys()):
            if cid in resolved_conditions:
                continue
            if now_ms < next_check_ms.get(cid, 0):
                continue

            try:
                record = await asyncio.to_thread(fetch_condition_settlement, cid, session=session)
            except Exception as exc:
                LOG.warning("settlement poll failed for %s: %s", cid, exc)
                next_check_ms[cid] = now_ms + (max(1, cfg.per_condition_cooldown_sec) * 1000)
                continue

            if not record:
                next_check_ms[cid] = now_ms + (max(1, cfg.per_condition_cooldown_sec) * 1000)
                continue

            raw_store.write(
                source=SOURCE_KIND_SETTLEMENT,
                channel=CHANNEL_MARKET_RESOLVED,
                payload_json=record,
                condition_id=cid,
                recv_unix_ms=now_ms,
            )
            resolved_conditions.add(cid)
            LOG.info("settlement captured: condition=%s outcome=%s", cid, record.get("official_outcome"))

        if await _wait_or_stop(stop_event, max(1, int(cfg.interval_sec))):
            return


async def run_sidecar(config: SidecarConfig, duration_sec: Optional[int] = None) -> None:
    raw_store = RawCaptureStore(config.raw_root)
    stop_event = asyncio.Event()
    tasks: List[asyncio.Task] = []

    selection_state = MarketSelectionState()
    market_channels = _normalized_market_channels(config.market_channels)
    allowed_market_events = _allowed_market_events(market_channels)

    if config.market_ws and config.market_ws.enabled and config.market_ws.url:
        tasks.append(
            asyncio.create_task(
                _consume_market_ws(
                    raw_store,
                    config.market_ws,
                    stop_event,
                    selection_state=selection_state,
                    allowed_events=allowed_market_events,
                    debug_raw_market_ws=config.debug_raw_market_ws,
                ),
                name="market_ws",
            )
        )

    if config.user_ws and config.user_ws.enabled and config.user_ws.url:
        tasks.append(
            asyncio.create_task(
                _consume_user_ws(
                    raw_store,
                    config.user_ws,
                    stop_event,
                    selection_state=selection_state,
                ),
                name="user_ws",
            )
        )

    if config.meta_poll and config.meta_poll.enabled:
        tasks.append(
            asyncio.create_task(
                _meta_poller(
                    raw_store,
                    config.meta_poll,
                    stop_event,
                    selection_state=selection_state if config.market_ws and config.market_ws.enabled else None,
                    market_prefixes=config.market_prefixes,
                    max_markets_per_prefix=config.max_markets_per_prefix,
                ),
                name="meta_poll",
            )
        )

    if (
        config.settlement_poll
        and config.settlement_poll.enabled
        and config.market_ws
        and config.market_ws.enabled
        and config.market_prefixes
    ):
        tasks.append(
            asyncio.create_task(
                _settlement_poller(
                    raw_store,
                    config.settlement_poll,
                    stop_event,
                    selection_state=selection_state,
                ),
                name="settlement_poll",
            )
        )

    if config.xuan_poll and config.xuan_poll.enabled and config.xuan_poll.user.strip():
        tasks.append(
            asyncio.create_task(
                run_xuan_poll_worker(raw_store, config.xuan_poll, stop_event),
                name="xuan_poll",
            )
        )

    if not tasks:
        raise RuntimeError("No enabled sidecar sources. Check config JSON.")

    try:
        if duration_sec is None or duration_sec <= 0:
            await asyncio.gather(*tasks)
        else:
            await asyncio.sleep(duration_sec)
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

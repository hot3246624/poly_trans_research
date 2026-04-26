"""Metadata capture from Gamma API and Data API backfill."""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import Dict, Iterable, Iterator, List, Optional

import requests

from ..constants import (
    POLYMARKET_GAMMA_EVENTS_URL,
    POLYMARKET_DATA_TRADES_URL,
    ROUND_INTERVAL_TARGET_SEC,
    ROUND_INTERVAL_MIN_SEC,
    ROUND_INTERVAL_MAX_SEC,
    SOURCE_KIND_META,
    CHANNEL_MARKET_META,
    SOURCE_KIND_BACKFILL,
    CHANNEL_TRADES_BACKFILL,
)
from ..utils.time import parse_datetime_to_unix_ms, now_unix_ms
from .raw_store import RawCaptureStore

_CRYPTO_5M_SLUG_RE = re.compile(r"^[a-z0-9]+-updown-5m-\d+$")
_SLUG_TS_RE = re.compile(r"-(\d+)$")
LOG = logging.getLogger(__name__)

_SYMBOL_MAP = {
    "btc": "BTC",
    "eth": "ETH",
    "sol": "SOL",
    "xrp": "XRP",
    "doge": "DOGE",
    "bnb": "BNB",
    "hype": "HYPE",
    "sui": "SUI",
    "bitcoin": "BTC",
    "ethereum": "ETH",
    "solana": "SOL",
    "dogecoin": "DOGE",
}


@dataclasses.dataclass(slots=True)
class MarketMetaRecord:
    condition_id: str
    slug: str
    symbol: str
    interval_sec: int
    start_ms: int
    end_ms: int
    yes_token_id: Optional[str]
    no_token_id: Optional[str]
    tick_size: Optional[float]

    def as_payload(self) -> Dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(slots=True)
class MarketMetaFetchState:
    """Stateful metadata fetch context for HTTP conditional requests."""

    last_modified: Optional[str] = None
    last_poll_not_modified: bool = False
    session: requests.Session = dataclasses.field(default_factory=requests.Session)


def _slug_round_window_ms(slug: str) -> Optional[tuple[int, int]]:
    """Infer round [start_ms, end_ms] from 5m slug timestamp suffix."""
    txt = (slug or "").strip().lower()
    if not _CRYPTO_5M_SLUG_RE.match(txt):
        return None
    m = _SLUG_TS_RE.search(txt)
    if not m:
        return None
    try:
        start_ms = int(m.group(1)) * 1000
    except ValueError:
        return None
    end_ms = start_ms + (ROUND_INTERVAL_TARGET_SEC * 1000)
    return start_ms, end_ms


def _parse_token_ids(raw) -> tuple[Optional[str], Optional[str]]:
    if raw is None:
        return None, None
    if isinstance(raw, str):
        txt = raw.strip()
        if txt.startswith("[") and txt.endswith("]"):
            try:
                import json

                arr = json.loads(txt)
            except Exception:
                arr = []
        else:
            arr = [raw]
    elif isinstance(raw, list):
        arr = raw
    else:
        arr = []

    yes = str(arr[0]) if len(arr) >= 1 and arr[0] is not None else None
    no = str(arr[1]) if len(arr) >= 2 and arr[1] is not None else None
    return yes, no


def _infer_symbol(question: str, slug: str) -> str:
    q = (question or "").strip().lower()
    if q:
        first = q.split()[0]
        if first in _SYMBOL_MAP:
            return _SYMBOL_MAP[first]
    if slug:
        head = slug.split("-")[0].lower()
        if head in _SYMBOL_MAP:
            return _SYMBOL_MAP[head]
        return head.upper()
    return "UNKNOWN"


def _looks_like_crypto_5m_market(market: Dict[str, object]) -> bool:
    slug = str(market.get("slug") or "")
    question = str(market.get("question") or "")

    if _CRYPTO_5M_SLUG_RE.match(slug):
        return True
    if "up or down" not in question.lower():
        return False

    start_ms = parse_datetime_to_unix_ms(market.get("startDate") or market.get("startDateIso"))
    end_ms = parse_datetime_to_unix_ms(market.get("endDate") or market.get("endDateIso"))
    if start_ms is None or end_ms is None:
        return False
    interval_sec = int((end_ms - start_ms) / 1000)
    return ROUND_INTERVAL_MIN_SEC <= interval_sec <= ROUND_INTERVAL_MAX_SEC


def normalize_market_meta(market: Dict[str, object]) -> Optional[MarketMetaRecord]:
    condition_id = str(market.get("conditionId") or "").strip()
    slug = str(market.get("slug") or "").strip()
    if not condition_id or not slug:
        return None

    slug_window = _slug_round_window_ms(slug)
    start_ms = parse_datetime_to_unix_ms(market.get("startDate") or market.get("startDateIso"))
    end_ms = parse_datetime_to_unix_ms(market.get("endDate") or market.get("endDateIso"))

    # Gamma's start/end fields for 5m rounds may be event-level (many hours).
    # For updown-5m markets, trust slug timestamp when interval is invalid.
    if start_ms is None or end_ms is None:
        if slug_window is None:
            return None
        start_ms, end_ms = slug_window

    interval_sec = max(0, int((end_ms - start_ms) / 1000))
    if slug_window is not None and not (ROUND_INTERVAL_MIN_SEC <= interval_sec <= ROUND_INTERVAL_MAX_SEC):
        start_ms, end_ms = slug_window
        interval_sec = ROUND_INTERVAL_TARGET_SEC

    if interval_sec <= 0:
        return None

    yes_token_id, no_token_id = _parse_token_ids(market.get("clobTokenIds"))
    tick_raw = market.get("orderPriceMinTickSize") or market.get("tickSize")
    tick_size = float(tick_raw) if tick_raw is not None else None

    symbol = _infer_symbol(str(market.get("question") or ""), slug)

    return MarketMetaRecord(
        condition_id=condition_id,
        slug=slug,
        symbol=symbol,
        interval_sec=interval_sec,
        start_ms=start_ms,
        end_ms=end_ms,
        yes_token_id=yes_token_id,
        no_token_id=no_token_id,
        tick_size=tick_size,
    )


def fetch_crypto_5m_markets(
    limit_pages: int = 200,
    page_size: int = 200,
    active_only: bool = False,
    *,
    fetch_state: Optional[MarketMetaFetchState] = None,
) -> List[MarketMetaRecord]:
    """Fetch event pages from Gamma API, keep only crypto 5m up/down markets."""
    seen: set[str] = set()
    out: List[MarketMetaRecord] = []

    offset = 0
    state = fetch_state
    session = state.session if state is not None else requests.Session()
    if state is not None:
        state.last_poll_not_modified = False

    for _ in range(limit_pages):
        params = {"limit": page_size, "offset": offset, "tag_slug": "crypto"}
        if active_only:
            params.update({"active": "true", "closed": "false"})

        events = None
        for _attempt in range(3):
            try:
                headers: Optional[Dict[str, str]] = None
                # Conditional request on the first page only; Gamma returns 304 for unchanged snapshots.
                if offset == 0 and state is not None and state.last_modified:
                    headers = {"If-Modified-Since": state.last_modified}

                resp = session.get(POLYMARKET_GAMMA_EVENTS_URL, params=params, headers=headers, timeout=20)

                if resp.status_code == 304 and offset == 0:
                    if state is not None:
                        state.last_poll_not_modified = True
                    return []

                resp.raise_for_status()

                if offset == 0 and state is not None:
                    last_modified = resp.headers.get("Last-Modified") or resp.headers.get("last-modified")
                    if last_modified:
                        state.last_modified = last_modified

                events = resp.json()
                break
            except requests.RequestException:
                continue
        if events is None:
            # Keep partial results instead of failing the whole capture loop.
            LOG.warning("gamma events fetch failed after retries at offset=%d; keeping partial results=%d", offset, len(out))
            break
        if not isinstance(events, list) or not events:
            break

        for event in events:
            markets = event.get("markets") or []
            for market in markets:
                if not isinstance(market, dict):
                    continue
                if not _looks_like_crypto_5m_market(market):
                    continue
                rec = normalize_market_meta(market)
                if rec is None:
                    continue
                if rec.condition_id in seen:
                    continue
                seen.add(rec.condition_id)
                out.append(rec)

        if len(events) < page_size:
            break
        offset += page_size

    return out


def write_market_meta_records(
    raw_store: RawCaptureStore,
    markets: Iterable[MarketMetaRecord],
    *,
    capture_time_ms: Optional[int] = None,
) -> int:
    capture_ms = capture_time_ms if capture_time_ms is not None else now_unix_ms()
    count = 0
    for rec in markets:
        raw_store.write(
            source=SOURCE_KIND_META,
            channel=CHANNEL_MARKET_META,
            payload_json=rec.as_payload(),
            condition_id=rec.condition_id,
            recv_unix_ms=capture_ms,
        )
        count += 1
    return count


def capture_market_meta_once(
    raw_store: RawCaptureStore,
    *,
    active_only: bool = False,
    capture_time_ms: Optional[int] = None,
    fetch_state: Optional[MarketMetaFetchState] = None,
) -> int:
    markets = fetch_crypto_5m_markets(active_only=active_only, fetch_state=fetch_state)
    if fetch_state is not None and fetch_state.last_poll_not_modified:
        LOG.debug("market meta not modified (HTTP 304), skip raw writes")
        return 0

    return write_market_meta_records(raw_store, markets, capture_time_ms=capture_time_ms)


def _iter_data_api_trades(condition_id: str, page_limit: int = 500) -> Iterator[Dict[str, object]]:
    offset = 0
    session = requests.Session()
    while True:
        params = {
            "limit": page_limit,
            "offset": offset,
            "takerOnly": "false",
            "market": condition_id,
        }
        resp = session.get(POLYMARKET_DATA_TRADES_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        batch = data if isinstance(data, list) else data.get("trades", []) if isinstance(data, dict) else []
        if not batch:
            break
        for trade in batch:
            if isinstance(trade, dict):
                yield trade
        if len(batch) < page_limit:
            break
        offset += page_limit


def backfill_trades_once(
    raw_store: RawCaptureStore,
    *,
    condition_ids: Iterable[str],
    min_ts_ms: Optional[int] = None,
    max_ts_ms: Optional[int] = None,
) -> int:
    written = 0
    for condition_id in condition_ids:
        cid = condition_id.strip()
        if not cid:
            continue
        for trade in _iter_data_api_trades(cid):
            ts_raw = trade.get("timestamp")
            try:
                trade_ts_ms = int(float(ts_raw) * 1000)
            except (TypeError, ValueError):
                trade_ts_ms = None

            if min_ts_ms is not None and trade_ts_ms is not None and trade_ts_ms < min_ts_ms:
                continue
            if max_ts_ms is not None and trade_ts_ms is not None and trade_ts_ms > max_ts_ms:
                continue

            payload = {
                "condition_id": cid,
                "trade_id": trade.get("id") or trade.get("trade_id"),
                "trade_ts_ms": trade_ts_ms,
                "outcome": trade.get("outcome"),
                "side": trade.get("side"),
                "price": trade.get("price"),
                "size": trade.get("size"),
                "source_quality": "data_api_backfill",
                "raw": trade,
            }
            raw_store.write(
                source=SOURCE_KIND_BACKFILL,
                channel=CHANNEL_TRADES_BACKFILL,
                payload_json=payload,
                condition_id=cid,
            )
            written += 1
    return written

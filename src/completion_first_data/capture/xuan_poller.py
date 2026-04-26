"""Periodic user data polling (trades/activity) for xuan-style verification."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from ..constants import (
    CHANNEL_XUAN_ACTIVITY,
    CHANNEL_XUAN_POLL_LOG,
    CHANNEL_XUAN_TRADES,
    POLYMARKET_DATA_ACTIVITY_URL,
    POLYMARKET_DATA_TRADES_URL,
    SOURCE_KIND_XUAN_POLL,
)
from ..utils.time import now_unix_ms
from .raw_store import RawCaptureStore

LOG = logging.getLogger(__name__)


@dataclasses.dataclass(slots=True)
class XuanPollConfig:
    enabled: bool = False
    user: str = ""
    interval_sec: int = 300
    aggressive_interval_sec: int = 60
    aggressive_trade_threshold: int = 500
    page_limit: int = 500
    max_pages: int = 30
    cursor_path: Optional[str] = None


@dataclasses.dataclass(slots=True)
class XuanCursorState:
    last_trade_ts_ms: Optional[int] = None
    last_activity_ts_ms: Optional[int] = None


def _parse_ts_ms(value: Any) -> Optional[int]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    if v >= 1_000_000_000_000:
        return int(v)
    return int(v * 1000)


def _load_cursor(path: Path) -> XuanCursorState:
    if not path.exists():
        return XuanCursorState()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return XuanCursorState()
    return XuanCursorState(
        last_trade_ts_ms=int(payload.get("last_trade_ts_ms")) if payload.get("last_trade_ts_ms") else None,
        last_activity_ts_ms=int(payload.get("last_activity_ts_ms")) if payload.get("last_activity_ts_ms") else None,
    )


def _save_cursor(path: Path, state: XuanCursorState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "last_trade_ts_ms": state.last_trade_ts_ms,
        "last_activity_ts_ms": state.last_activity_ts_ms,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _iter_data_api_rows(
    *,
    session: requests.Session,
    url: str,
    user: str,
    last_seen_ts_ms: Optional[int],
    page_limit: int,
    max_pages: int,
    include_taker_only_false: bool = False,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    # `before` is kept as a best-effort pagination hint per API guidance.
    before_cursor_s: Optional[int] = int(last_seen_ts_ms / 1000) if last_seen_ts_ms else None

    for page in range(max_pages):
        params: Dict[str, Any] = {
            "limit": page_limit,
            "offset": page * page_limit,
            "user": user,
        }
        if include_taker_only_false:
            params["takerOnly"] = "false"
        if before_cursor_s is not None:
            params["before"] = before_cursor_s

        try:
            resp = session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            payload = resp.json()
        except requests.RequestException as exc:
            # Data API may hard-cap deep offsets (e.g. 3500). Keep already fetched rows.
            if page > 0:
                LOG.warning("xuan poll paging stopped at page=%d: %s", page, exc)
                break
            raise

        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("trades") or payload.get("history") or []
        else:
            rows = []

        if not isinstance(rows, list) or not rows:
            break

        min_ts_ms: Optional[int] = None
        stop = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts_ms = _parse_ts_ms(row.get("timestamp"))
            if ts_ms is not None:
                if min_ts_ms is None or ts_ms < min_ts_ms:
                    min_ts_ms = ts_ms
                if last_seen_ts_ms is not None and ts_ms <= last_seen_ts_ms:
                    stop = True
                    continue
            out.append(row)

        if min_ts_ms is not None:
            before_cursor_s = max(0, int(min_ts_ms / 1000) - 1)

        if stop or len(rows) < page_limit:
            break

    return out


def _write_xuan_trade_rows(
    raw_store: RawCaptureStore,
    *,
    user: str,
    poll_ts_ms: int,
    rows: List[Dict[str, Any]],
) -> Optional[int]:
    max_ts: Optional[int] = None
    for row in sorted(rows, key=lambda r: _parse_ts_ms(r.get("timestamp")) or 0):
        ts_ms = _parse_ts_ms(row.get("timestamp"))
        if ts_ms is not None and (max_ts is None or ts_ms > max_ts):
            max_ts = ts_ms
        payload = {
            "user": user,
            "poll_ts_ms": poll_ts_ms,
            "condition_id": row.get("conditionId"),
            "trade_ts_ms": ts_ms,
            "trade_id": row.get("id"),
            "tx_hash": row.get("transactionHash"),
            "asset": row.get("asset"),
            "outcome": row.get("outcome"),
            "side": row.get("side"),
            "price": row.get("price"),
            "size": row.get("size"),
            "slug": row.get("slug"),
            "event_slug": row.get("eventSlug"),
            "title": row.get("title"),
            "proxy_wallet": row.get("proxyWallet"),
            "source_quality": "data_api_poll",
            "raw_json": row,
        }
        raw_store.write(
            source=SOURCE_KIND_XUAN_POLL,
            channel=CHANNEL_XUAN_TRADES,
            payload_json=payload,
            condition_id=str(row.get("conditionId") or ""),
            recv_unix_ms=poll_ts_ms,
        )
    return max_ts


def _write_xuan_activity_rows(
    raw_store: RawCaptureStore,
    *,
    user: str,
    poll_ts_ms: int,
    rows: List[Dict[str, Any]],
) -> Optional[int]:
    max_ts: Optional[int] = None
    for row in sorted(rows, key=lambda r: _parse_ts_ms(r.get("timestamp")) or 0):
        ts_ms = _parse_ts_ms(row.get("timestamp"))
        if ts_ms is not None and (max_ts is None or ts_ms > max_ts):
            max_ts = ts_ms
        payload = {
            "user": user,
            "poll_ts_ms": poll_ts_ms,
            "condition_id": row.get("conditionId"),
            "activity_ts_ms": ts_ms,
            "activity_type": row.get("type"),
            "tx_hash": row.get("transactionHash"),
            "asset": row.get("asset"),
            "outcome": row.get("outcome"),
            "side": row.get("side"),
            "price": row.get("price"),
            "size": row.get("size"),
            "usdc_size": row.get("usdcSize"),
            "slug": row.get("slug"),
            "event_slug": row.get("eventSlug"),
            "title": row.get("title"),
            "proxy_wallet": row.get("proxyWallet"),
            "source_quality": "data_api_poll",
            "raw_json": row,
        }
        raw_store.write(
            source=SOURCE_KIND_XUAN_POLL,
            channel=CHANNEL_XUAN_ACTIVITY,
            payload_json=payload,
            condition_id=str(row.get("conditionId") or ""),
            recv_unix_ms=poll_ts_ms,
        )
    return max_ts


def _write_poll_log(
    raw_store: RawCaptureStore,
    *,
    user: str,
    endpoint: str,
    poll_ts_ms: int,
    row_count: int,
    max_ts_ms: Optional[int],
    ok: bool,
    error: Optional[str] = None,
) -> None:
    raw_store.write(
        source=SOURCE_KIND_XUAN_POLL,
        channel=CHANNEL_XUAN_POLL_LOG,
        payload_json={
            "user": user,
            "endpoint": endpoint,
            "poll_ts_ms": poll_ts_ms,
            "rows": row_count,
            "max_ts_ms": max_ts_ms,
            "ok": ok,
            "error": error,
        },
        recv_unix_ms=poll_ts_ms,
    )


async def run_xuan_poll_worker(
    raw_store: RawCaptureStore,
    cfg: XuanPollConfig,
    stop_event: Any,
) -> None:
    if not cfg.enabled or not cfg.user.strip():
        return

    cursor_path = Path(cfg.cursor_path or (Path(raw_store.raw_root) / ".xuan_cursor.json"))
    cursor = _load_cursor(cursor_path)
    session = requests.Session()
    interval_sec = max(1, int(cfg.interval_sec))

    while not stop_event.is_set():
        poll_ts_ms = now_unix_ms()
        trade_count = 0
        activity_count = 0
        trade_max_ts = None
        activity_max_ts = None

        try:
            trade_rows = await asyncio.to_thread(
                _iter_data_api_rows,
                session=session,
                url=POLYMARKET_DATA_TRADES_URL,
                user=cfg.user,
                last_seen_ts_ms=cursor.last_trade_ts_ms,
                page_limit=max(1, int(cfg.page_limit)),
                max_pages=max(1, int(cfg.max_pages)),
                include_taker_only_false=True,
            )
            trade_count = len(trade_rows)
            trade_max_ts = _write_xuan_trade_rows(
                raw_store,
                user=cfg.user,
                poll_ts_ms=poll_ts_ms,
                rows=trade_rows,
            )
            if trade_max_ts is not None:
                cursor.last_trade_ts_ms = max(cursor.last_trade_ts_ms or 0, trade_max_ts)
            _write_poll_log(
                raw_store,
                user=cfg.user,
                endpoint="trades",
                poll_ts_ms=poll_ts_ms,
                row_count=trade_count,
                max_ts_ms=trade_max_ts,
                ok=True,
            )
        except Exception as exc:
            LOG.warning("xuan trades poll failed: %s", exc)
            _write_poll_log(
                raw_store,
                user=cfg.user,
                endpoint="trades",
                poll_ts_ms=poll_ts_ms,
                row_count=0,
                max_ts_ms=None,
                ok=False,
                error=str(exc),
            )

        try:
            activity_rows = await asyncio.to_thread(
                _iter_data_api_rows,
                session=session,
                url=POLYMARKET_DATA_ACTIVITY_URL,
                user=cfg.user,
                last_seen_ts_ms=cursor.last_activity_ts_ms,
                page_limit=max(1, int(cfg.page_limit)),
                max_pages=max(1, int(cfg.max_pages)),
                include_taker_only_false=False,
            )
            activity_count = len(activity_rows)
            activity_max_ts = _write_xuan_activity_rows(
                raw_store,
                user=cfg.user,
                poll_ts_ms=poll_ts_ms,
                rows=activity_rows,
            )
            if activity_max_ts is not None:
                cursor.last_activity_ts_ms = max(cursor.last_activity_ts_ms or 0, activity_max_ts)
            _write_poll_log(
                raw_store,
                user=cfg.user,
                endpoint="activity",
                poll_ts_ms=poll_ts_ms,
                row_count=activity_count,
                max_ts_ms=activity_max_ts,
                ok=True,
            )
        except Exception as exc:
            LOG.warning("xuan activity poll failed: %s", exc)
            _write_poll_log(
                raw_store,
                user=cfg.user,
                endpoint="activity",
                poll_ts_ms=poll_ts_ms,
                row_count=0,
                max_ts_ms=None,
                ok=False,
                error=str(exc),
            )

        _save_cursor(cursor_path, cursor)
        LOG.info(
            "xuan poll captured trades=%d activity=%d user=%s",
            trade_count,
            activity_count,
            cfg.user,
        )

        next_interval = interval_sec
        if trade_count >= max(1, int(cfg.aggressive_trade_threshold)):
            next_interval = max(1, int(cfg.aggressive_interval_sec))
        await asyncio.sleep(next_interval)

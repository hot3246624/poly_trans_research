"""Helpers for authenticated user-truth capture and inventory reconciliation."""

from __future__ import annotations

import dataclasses
import logging
import time
from typing import Any, Dict, List, Optional, Sequence

import requests

from .constants import (
    POLYMARKET_CLOB_BASE_URL,
    POLYMARKET_DATA_ACTIVITY_URL,
    POLYMARKET_DATA_POSITIONS_URL,
    POLYMARKET_DATA_TRADES_URL,
)

LOG = logging.getLogger(__name__)

_API_KEY_ALIASES = ("CF_API_KEY", "POLYMARKET_BUILDER_API_KEY", "POLYMARKET_API_KEY")
_API_SECRET_ALIASES = ("CF_API_SECRET", "POLYMARKET_BUILDER_SECRET", "POLYMARKET_API_SECRET")
_API_PASSPHRASE_ALIASES = (
    "CF_API_PASSPHRASE",
    "POLYMARKET_BUILDER_PASSPHRASE",
    "POLYMARKET_API_PASSPHRASE",
)
_L1_PRIVATE_KEY_ALIASES = ("CF_L1_PRIVATE_KEY", "POLYMARKET_PRIVATE_KEY")
_FUNDER_ADDRESS_ALIASES = ("POLYMARKET_FUNDER_ADDRESS", "ETHEREUM_ADDRESS")
_SIGNATURE_TYPE_ALIASES = ("CF_SIGNATURE_TYPE", "PM_SIGNATURE_TYPE")
_QUOTE_CHARS = "'\"“”‘’`"


@dataclasses.dataclass(slots=True)
class UserAuthConfig:
    api_key: str
    api_secret: str
    api_passphrase: str
    funder_address: str
    auth_source: str
    clob_rest_url: str = POLYMARKET_CLOB_BASE_URL
    l1_private_key: Optional[str] = None
    signature_type: Optional[int] = None


@dataclasses.dataclass(slots=True)
class InventorySnapshot:
    condition_id: str
    asset_id: str
    outcome: str
    size: float
    avg_price: Optional[float]
    redeemable: int
    mergeable: int
    source_kind: str

    def as_payload(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


def _sanitize_env_value(value: Any) -> str:
    txt = str(value or "").strip()
    while len(txt) >= 2 and txt[0] in _QUOTE_CHARS and txt[-1] in _QUOTE_CHARS:
        txt = txt[1:-1].strip()
    if txt and txt[0] in _QUOTE_CHARS:
        txt = txt[1:].strip()
    if txt and txt[-1] in _QUOTE_CHARS:
        txt = txt[:-1].strip()
    return txt


def _first_env(env: Dict[str, str], keys: Sequence[str]) -> str:
    for key in keys:
        value = _sanitize_env_value(env.get(key, ""))
        if value:
            return value
    return ""


def _as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    a = abs(n)
    if a >= 1_000_000_000_000_000_000:
        return int(n / 1_000_000)
    if a >= 1_000_000_000_000_000:
        return int(n / 1_000)
    if a >= 1_000_000_000_000:
        return int(n)
    if a >= 1_000_000_000:
        return int(n * 1000)
    return int(n)


def _as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if value in (None, "", 0, "0", "false", "False", "FALSE", "no", "NO"):
        return 0
    return 1


def _pick(payload: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, ""):
            return payload[key]
    return None


def _normalize_outcome(value: Any) -> Optional[str]:
    if value is None:
        return None
    txt = str(value).strip().lower()
    if txt in {"yes", "up", "y", "1", "true"}:
        return "YES"
    if txt in {"no", "down", "n", "0", "false"}:
        return "NO"
    return None


def _normalize_direction(value: Any) -> Optional[str]:
    if value is None:
        return None
    txt = str(value).strip().upper()
    if txt in {"BUY", "SELL"}:
        return txt
    return None


def _normalize_text(value: Any) -> Optional[str]:
    txt = str(value or "").strip()
    return txt or None


def mask_secret_id(value: str, visible: int = 8) -> str:
    txt = str(value or "")
    if len(txt) <= visible:
        return txt
    return f"{txt[:visible]}..."


def build_user_auth_message(auth: UserAuthConfig) -> Dict[str, Any]:
    return {
        "auth": {
            "apiKey": auth.api_key,
            "secret": auth.api_secret,
            "passphrase": auth.api_passphrase,
        },
        "type": "user",
    }


def build_user_subscribe_message(condition_ids: Sequence[str]) -> Dict[str, Any]:
    markets: List[str] = []
    seen: set[str] = set()
    for condition_id in condition_ids:
        cid = str(condition_id or "").strip()
        if not cid or cid in seen:
            continue
        seen.add(cid)
        markets.append(cid)
    return {
        "operation": "subscribe",
        "markets": markets,
    }


def resolve_user_auth_config(
    env: Dict[str, str],
    *,
    clob_rest_url: str = POLYMARKET_CLOB_BASE_URL,
) -> Optional[UserAuthConfig]:
    funder_address = _first_env(env, _FUNDER_ADDRESS_ALIASES)
    api_key = _first_env(env, _API_KEY_ALIASES)
    api_secret = _first_env(env, _API_SECRET_ALIASES)
    api_passphrase = _first_env(env, _API_PASSPHRASE_ALIASES)
    l1_private_key = _first_env(env, _L1_PRIVATE_KEY_ALIASES) or None
    sig_value = _first_env(env, _SIGNATURE_TYPE_ALIASES)
    signature_type: Optional[int]
    try:
        signature_type = int(sig_value) if sig_value else None
    except ValueError:
        signature_type = None

    if api_key and api_secret and api_passphrase:
        candidate = UserAuthConfig(
            api_key=api_key,
            api_secret=api_secret,
            api_passphrase=api_passphrase,
            funder_address=funder_address,
            auth_source="api_creds",
            clob_rest_url=clob_rest_url,
            l1_private_key=l1_private_key,
            signature_type=signature_type,
        )
        if not l1_private_key or _validate_api_creds(candidate):
            return candidate

    if not l1_private_key:
        return None

    try:
        from py_clob_client.client import ClobClient
    except ImportError as exc:  # pragma: no cover - dependency should exist in runtime
        raise RuntimeError("Missing dependency 'py-clob-client'. Install it before enabling user truth.") from exc

    kwargs: Dict[str, Any] = {
        "host": clob_rest_url,
        "chain_id": 137,
        "key": l1_private_key,
    }
    if signature_type is not None:
        kwargs["signature_type"] = signature_type
    if funder_address:
        kwargs["funder"] = funder_address

    client = ClobClient(**kwargs)
    creds = client.create_or_derive_api_creds()
    if creds is None:
        return None

    return UserAuthConfig(
        api_key=str(creds.api_key),
        api_secret=str(creds.api_secret),
        api_passphrase=str(creds.api_passphrase),
        funder_address=funder_address,
        auth_source="derived_api_creds",
        clob_rest_url=clob_rest_url,
        l1_private_key=l1_private_key,
        signature_type=signature_type,
    )


def create_l2_client(auth: UserAuthConfig):
    if not auth.l1_private_key:
        return None
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds
    except ImportError as exc:  # pragma: no cover - dependency should exist in runtime
        raise RuntimeError("Missing dependency 'py-clob-client'. Install it before enabling user truth.") from exc

    creds = ApiCreds(
        api_key=auth.api_key,
        api_secret=auth.api_secret,
        api_passphrase=auth.api_passphrase,
    )
    kwargs: Dict[str, Any] = {
        "host": auth.clob_rest_url,
        "chain_id": 137,
        "key": auth.l1_private_key,
        "creds": creds,
    }
    if auth.signature_type is not None:
        kwargs["signature_type"] = auth.signature_type
    if auth.funder_address:
        kwargs["funder"] = auth.funder_address
    return ClobClient(**kwargs)


def _validate_api_creds(auth: UserAuthConfig) -> bool:
    client = create_l2_client(auth)
    if client is None:
        return False
    try:
        client.get_orders()
        return True
    except Exception as exc:
        LOG.warning(
            "existing api creds validation failed (%s); trying L1 derive fallback",
            exc,
        )
        return False


def fetch_open_orders(auth: UserAuthConfig, market_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    client = create_l2_client(auth)
    if client is None:
        return []
    rows = client.get_orders()
    if not isinstance(rows, list):
        return []
    if not market_ids:
        return [row for row in rows if isinstance(row, dict)]
    wanted = {str(v or "").strip() for v in market_ids if str(v or "").strip()}
    if not wanted:
        return [row for row in rows if isinstance(row, dict)]
    return [row for row in rows if isinstance(row, dict) and str(row.get("market") or "").strip() in wanted]


def fetch_positions(
    session: requests.Session,
    *,
    user: str,
    url: str = POLYMARKET_DATA_POSITIONS_URL,
    page_limit: int = 500,
    max_pages: int = 50,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    user = str(user or "").strip()
    if not user:
        return out

    for page in range(max(1, max_pages)):
        params = {
            "user": user,
            "limit": max(1, page_limit),
            "offset": page * max(1, page_limit),
        }
        resp = session.get(url, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("positions") or payload.get("data") or []
        else:
            rows = []
        if not isinstance(rows, list) or not rows:
            break
        out.extend([row for row in rows if isinstance(row, dict)])
        if len(rows) < page_limit:
            break
    return out


def normalize_inventory_snapshot(row: Dict[str, Any], *, source_kind: str) -> Optional[InventorySnapshot]:
    condition_id = _normalize_text(_pick(row, "conditionId", "condition_id"))
    asset_id = _normalize_text(_pick(row, "asset", "asset_id", "assetId"))
    outcome = _normalize_outcome(_pick(row, "outcome", "market_side", "side"))
    size = _as_float(_pick(row, "size", "amount"))
    if not condition_id or not asset_id or outcome is None or size is None:
        return None
    avg_price = _as_float(_pick(row, "avgPrice", "avg_price", "averagePrice"))
    return InventorySnapshot(
        condition_id=condition_id,
        asset_id=asset_id,
        outcome=outcome,
        size=size,
        avg_price=avg_price,
        redeemable=_as_bool_int(_pick(row, "redeemable")),
        mergeable=_as_bool_int(_pick(row, "mergeable")),
        source_kind=source_kind,
    )


def _fetch_recent_rows(
    session: requests.Session,
    *,
    url: str,
    user: str,
    since_ms: int,
    page_limit: int = 500,
    max_pages: int = 6,
    include_taker_only_false: bool = False,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for page in range(max(1, max_pages)):
        params: Dict[str, Any] = {
            "user": user,
            "limit": max(1, page_limit),
            "offset": page * max(1, page_limit),
        }
        if include_taker_only_false:
            params["takerOnly"] = "false"
        resp = session.get(url, params=params, timeout=20)
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("trades") or payload.get("history") or payload.get("data") or []
        else:
            rows = []
        if not isinstance(rows, list) or not rows:
            break

        fresh = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            ts_ms = _as_int(_pick(row, "timestamp", "time"))
            if ts_ms is None or ts_ms < since_ms:
                continue
            out.append(row)
            fresh += 1

        if len(rows) < page_limit or fresh == 0:
            break
    return out


def fetch_recent_user_trades(
    session: requests.Session,
    *,
    user: str,
    since_ms: int,
    page_limit: int = 500,
    max_pages: int = 6,
) -> List[Dict[str, Any]]:
    return _fetch_recent_rows(
        session,
        url=POLYMARKET_DATA_TRADES_URL,
        user=user,
        since_ms=since_ms,
        page_limit=page_limit,
        max_pages=max_pages,
        include_taker_only_false=True,
    )


def fetch_recent_user_activity(
    session: requests.Session,
    *,
    user: str,
    since_ms: int,
    page_limit: int = 500,
    max_pages: int = 6,
) -> List[Dict[str, Any]]:
    return _fetch_recent_rows(
        session,
        url=POLYMARKET_DATA_ACTIVITY_URL,
        user=user,
        since_ms=since_ms,
        page_limit=page_limit,
        max_pages=max_pages,
    )


def trade_row_to_user_trade_payload(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    condition_id = _normalize_text(_pick(row, "conditionId", "condition_id"))
    if not condition_id:
        return None
    ts_ms = _as_int(_pick(row, "timestamp", "time"))
    return {
        "event_type": "trade",
        "type": "TRADE",
        "market": condition_id,
        "asset_id": _pick(row, "asset", "asset_id", "assetId"),
        "side": _pick(row, "side"),
        "size": _pick(row, "size", "amount"),
        "price": _pick(row, "price"),
        "fee_rate_bps": _pick(row, "feeRateBps", "fee_rate_bps"),
        "status": _pick(row, "status") or "CONFIRMED",
        "matchtime": ts_ms,
        "last_update": ts_ms,
        "outcome": _pick(row, "outcome"),
        "owner": _pick(row, "proxyWallet", "proxy_wallet"),
        "trade_owner": _pick(row, "proxyWallet", "proxy_wallet"),
        "maker_address": _pick(row, "makerAddress", "maker_address"),
        "transaction_hash": _pick(row, "transactionHash", "tx_hash", "txHash"),
        "taker_order_id": _pick(row, "takerOrderId", "taker_order_id"),
        "trader_side": _pick(row, "traderSide", "trader_side"),
        "timestamp": ts_ms,
        "id": _pick(row, "id", "tradeId", "trade_id"),
        "source_quality": "public_recovery_trade",
        "raw_json": row,
    }


def activity_row_to_user_order_payload(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    activity_type = str(_pick(row, "type", "activity_type") or "").strip().upper()
    if activity_type == "TRADE":
        return None
    condition_id = _normalize_text(_pick(row, "conditionId", "condition_id"))
    if not condition_id:
        return None
    ts_ms = _as_int(_pick(row, "timestamp", "time"))
    return {
        "event_type": "order",
        "type": activity_type or "UPDATE",
        "market": condition_id,
        "asset_id": _pick(row, "asset", "asset_id", "assetId"),
        "side": _pick(row, "side"),
        "original_size": _pick(row, "size", "amount"),
        "size_matched": _pick(row, "sizeMatched", "size_matched"),
        "price": _pick(row, "price"),
        "outcome": _pick(row, "outcome"),
        "status": _pick(row, "status") or activity_type,
        "timestamp": ts_ms,
        "created_at": ts_ms,
        "maker_address": _pick(row, "makerAddress", "maker_address"),
        "transaction_hash": _pick(row, "transactionHash", "tx_hash", "txHash"),
        "id": _pick(row, "id", "orderId", "order_id"),
        "owner": _pick(row, "proxyWallet", "proxy_wallet"),
        "order_owner": _pick(row, "proxyWallet", "proxy_wallet"),
        "source_quality": "public_recovery_activity",
        "raw_json": row,
    }


def extract_user_trade_rows(
    payload: Dict[str, Any],
    *,
    funder_address: Optional[str] = None,
) -> List[Dict[str, Any]]:
    event_type = str(_pick(payload, "event_type", "eventType") or "").strip().lower()
    if event_type != "trade":
        return []

    condition_id = _normalize_text(_pick(payload, "market", "condition_id", "conditionId"))
    trade_id = _normalize_text(_pick(payload, "id", "trade_id", "tradeId"))
    taker_order_id = _normalize_text(_pick(payload, "taker_order_id", "takerOrderId"))
    trader_side = _normalize_text(_pick(payload, "trader_side", "traderSide"))
    trader_side = trader_side.upper() if trader_side else None
    top_market_side = _normalize_outcome(_pick(payload, "outcome", "market_side", "side_label"))
    top_direction = _normalize_direction(_pick(payload, "side", "order_side", "orderSide"))
    top_asset_id = _normalize_text(_pick(payload, "asset_id", "assetId", "asset"))
    top_price = _as_float(_pick(payload, "price"))
    top_size = _as_float(_pick(payload, "size", "amount"))
    top_fee = _as_float(_pick(payload, "fee_rate_bps", "feeRateBps"))
    match_ts_ms = _as_int(_pick(payload, "matchtime", "timestamp", "last_update", "lastUpdate"))
    tx_hash = _normalize_text(_pick(payload, "transaction_hash", "tx_hash", "txHash"))
    top_maker_address = _normalize_text(_pick(payload, "maker_address", "makerAddress"))
    normalized_funder = str(funder_address or "").strip().lower()

    out: List[Dict[str, Any]] = []
    maker_orders = payload.get("maker_orders") if isinstance(payload.get("maker_orders"), list) else []
    if trader_side == "MAKER" and maker_orders:
        for maker_order in maker_orders:
            if not isinstance(maker_order, dict):
                continue
            owner = str(_pick(maker_order, "owner") or "").strip().lower()
            if normalized_funder and owner and owner != normalized_funder:
                continue
            asset_id = _normalize_text(_pick(maker_order, "asset_id", "assetId", "asset")) or top_asset_id
            price = _as_float(_pick(maker_order, "price"))
            size = _as_float(_pick(maker_order, "matched_amount", "matchedAmount", "size", "amount"))
            if not condition_id or not asset_id or price is None or size is None:
                continue
            out.append(
                {
                    "condition_id": condition_id,
                    "asset_id": asset_id,
                    "order_id": _normalize_text(_pick(maker_order, "order_id", "orderId", "id")),
                    "taker_order_id": taker_order_id,
                    "trade_id": trade_id,
                    "market_side": _normalize_outcome(_pick(maker_order, "outcome")) or top_market_side,
                    "direction": _normalize_direction(_pick(maker_order, "side", "order_side", "orderSide"))
                    or top_direction,
                    "trader_side": trader_side,
                    "price": price,
                    "size": size,
                    "fee_rate_bps": _as_float(_pick(maker_order, "fee_rate_bps", "feeRateBps")) or top_fee,
                    "match_ts_ms": match_ts_ms,
                    "maker_address": _normalize_text(_pick(maker_order, "maker_address", "makerAddress"))
                    or top_maker_address,
                    "tx_hash": tx_hash,
                    "status": _normalize_text(_pick(payload, "status")),
                    "event_ts_ms": _as_int(_pick(payload, "timestamp", "last_update", "lastUpdate")),
                    "raw_json": payload,
                }
            )

    if out:
        return out

    if not condition_id or not top_asset_id or top_price is None or top_size is None:
        return []

    out.append(
        {
            "condition_id": condition_id,
            "asset_id": top_asset_id,
            "order_id": _normalize_text(_pick(payload, "order_id", "orderId")) or taker_order_id,
            "taker_order_id": taker_order_id,
            "trade_id": trade_id,
            "market_side": top_market_side,
            "direction": top_direction,
            "trader_side": trader_side,
            "price": top_price,
            "size": top_size,
            "fee_rate_bps": top_fee,
            "match_ts_ms": match_ts_ms,
            "maker_address": top_maker_address,
            "tx_hash": tx_hash,
            "status": _normalize_text(_pick(payload, "status")),
            "event_ts_ms": _as_int(_pick(payload, "timestamp", "last_update", "lastUpdate")),
            "raw_json": payload,
        }
    )
    return out


def apply_fill_rows_to_inventory(
    state: Dict[str, InventorySnapshot],
    fill_rows: Sequence[Dict[str, Any]],
) -> List[InventorySnapshot]:
    out: List[InventorySnapshot] = []
    for row in fill_rows:
        asset_id = _normalize_text(row.get("asset_id"))
        condition_id = _normalize_text(row.get("condition_id"))
        outcome = _normalize_outcome(row.get("market_side"))
        direction = _normalize_direction(row.get("direction"))
        size = _as_float(row.get("size"))
        price = _as_float(row.get("price"))
        if not asset_id or not condition_id or outcome is None or direction is None or size is None or size <= 0:
            continue

        current = state.get(asset_id)
        if current is None:
            current = InventorySnapshot(
                condition_id=condition_id,
                asset_id=asset_id,
                outcome=outcome,
                size=0.0,
                avg_price=None,
                redeemable=0,
                mergeable=0,
                source_kind="derived_fill",
            )

        new_size = current.size
        new_avg = current.avg_price
        if direction == "BUY":
            if price is None:
                continue
            if current.size <= 1e-12 or current.avg_price is None:
                new_size = size
                new_avg = price
            else:
                new_size = current.size + size
                new_avg = ((current.size * current.avg_price) + (size * price)) / max(new_size, 1e-12)
        elif direction == "SELL":
            new_size = max(0.0, current.size - size)
            new_avg = current.avg_price if new_size > 1e-12 else None

        updated = InventorySnapshot(
            condition_id=condition_id,
            asset_id=asset_id,
            outcome=outcome,
            size=new_size,
            avg_price=new_avg,
            redeemable=current.redeemable,
            mergeable=current.mergeable,
            source_kind="derived_fill",
        )
        state[asset_id] = updated
        out.append(updated)
    return out


def compute_inventory_drift(
    derived_state: Dict[str, InventorySnapshot],
    reconcile_rows: Sequence[InventorySnapshot],
    *,
    condition_ids: Sequence[str],
    epsilon: float = 1e-6,
) -> List[Dict[str, Any]]:
    wanted = {str(v or "").strip() for v in condition_ids if str(v or "").strip()}
    expected: Dict[str, float] = {}
    actual: Dict[str, float] = {}

    for asset_id, snapshot in derived_state.items():
        if wanted and snapshot.condition_id not in wanted:
            continue
        expected[asset_id] = float(snapshot.size)

    for snapshot in reconcile_rows:
        if wanted and snapshot.condition_id not in wanted:
            continue
        actual[snapshot.asset_id] = float(snapshot.size)

    drifts: List[Dict[str, Any]] = []
    for asset_id in sorted(set(expected) | set(actual)):
        expected_size = expected.get(asset_id, 0.0)
        actual_size = actual.get(asset_id, 0.0)
        diff = actual_size - expected_size
        if abs(diff) <= epsilon:
            continue
        drifts.append(
            {
                "asset_id": asset_id,
                "expected_size": expected_size,
                "actual_size": actual_size,
                "diff": diff,
            }
        )
    return drifts


def now_ms() -> int:
    return int(time.time() * 1000)

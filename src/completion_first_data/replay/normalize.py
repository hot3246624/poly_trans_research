"""Normalization helpers from raw envelopes to replay records."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from ..capture.envelope import RawEnvelope
from ..constants import ORDER_EVENT_TYPES


def as_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if n > 10_000_000_000_000:  # already ms-like
        return int(n)
    if n > 1_000_000_000:  # seconds timestamp
        return int(n * 1000)
    return int(n)


def as_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_non_none(*values: Optional[float]) -> Optional[float]:
    for value in values:
        if value is not None:
            return value
    return None


def pick(payload: Dict[str, Any], *keys: str) -> Any:
    for k in keys:
        if k in payload and payload[k] not in (None, ""):
            return payload[k]
    return None


def normalize_side(value: Any) -> Optional[str]:
    if value is None:
        return None
    txt = str(value).strip().lower()
    if txt in {"yes", "up", "y", "1", "true"}:
        return "YES"
    if txt in {"no", "down", "n", "0", "false"}:
        return "NO"
    return None


def normalize_direction(value: Any) -> Optional[str]:
    if value is None:
        return None
    txt = str(value).strip().upper()
    if txt in {"BUY", "SELL"}:
        return txt
    return None


def normalize_event_type(payload: Dict[str, Any], channel: str) -> str:
    raw = pick(payload, "event_type", "eventType", "event", "type", "status")
    if raw is None and channel == "trade":
        raw = "fill"

    txt = str(raw or "unknown").strip().lower()
    mapping = {
        "open": "live",
        "opened": "live",
        "live": "live",
        "pending": "post_sent",
        "placed": "placement",
        "placement": "placement",
        "new": "placement",
        "cancel": "cancel_sent",
        "cancel_sent": "cancel_sent",
        "cancelled": "canceled",
        "canceled": "canceled",
        "rejected": "rejected",
        "reject": "rejected",
        "filled": "fill",
        "fill": "fill",
        "partially_filled": "partial_fill",
        "partial_fill": "partial_fill",
        "partial": "partial_fill",
        "update": "update",
        "updated": "update",
        "merge": "merge",
        "redeem": "redeem",
    }
    normalized = mapping.get(txt, txt)
    if normalized not in ORDER_EVENT_TYPES:
        return "update"
    return normalized


def infer_source_ts_ms(payload: Dict[str, Any]) -> Optional[int]:
    return as_int(
        pick(
            payload,
            "source_ts_ms",
            "sourceTsMs",
            "trade_ts_ms",
            "tradeTsMs",
            "timestamp",
            "ts",
            "time",
        )
    )


def parse_book_l1(payload: Dict[str, Any]) -> Dict[str, Optional[float]]:
    yes = payload.get("yes") if isinstance(payload.get("yes"), dict) else {}
    no = payload.get("no") if isinstance(payload.get("no"), dict) else {}

    out = {
        "yes_bid_px": first_non_none(
            as_float(pick(payload, "yes_bid_px", "yes_bid", "bid_yes", "bid_up", "yesBestBid")),
            as_float(pick(yes, "bid_px", "bid", "best_bid", "price_bid")),
        ),
        "yes_ask_px": first_non_none(
            as_float(pick(payload, "yes_ask_px", "yes_ask", "ask_yes", "ask_up", "yesBestAsk")),
            as_float(pick(yes, "ask_px", "ask", "best_ask", "price_ask")),
        ),
        "no_bid_px": first_non_none(
            as_float(pick(payload, "no_bid_px", "no_bid", "bid_no", "bid_down", "noBestBid")),
            as_float(pick(no, "bid_px", "bid", "best_bid", "price_bid")),
        ),
        "no_ask_px": first_non_none(
            as_float(pick(payload, "no_ask_px", "no_ask", "ask_no", "ask_down", "noBestAsk")),
            as_float(pick(no, "ask_px", "ask", "best_ask", "price_ask")),
        ),
        "yes_bid_sz": first_non_none(
            as_float(pick(payload, "yes_bid_sz", "yes_bid_size", "bid_size_yes", "bid_size_up", "yes_bid_sz_l1")),
            as_float(pick(yes, "bid_sz", "bid_size", "size_bid")),
        ),
        "yes_ask_sz": first_non_none(
            as_float(pick(payload, "yes_ask_sz", "yes_ask_size", "ask_size_yes", "ask_size_up", "yes_ask_sz_l1")),
            as_float(pick(yes, "ask_sz", "ask_size", "size_ask")),
        ),
        "no_bid_sz": first_non_none(
            as_float(pick(payload, "no_bid_sz", "no_bid_size", "bid_size_no", "bid_size_down", "no_bid_sz_l1")),
            as_float(pick(no, "bid_sz", "bid_size", "size_bid")),
        ),
        "no_ask_sz": first_non_none(
            as_float(pick(payload, "no_ask_sz", "no_ask_size", "ask_size_no", "ask_size_down", "no_ask_sz_l1")),
            as_float(pick(no, "ask_sz", "ask_size", "size_ask")),
        ),
    }
    return out


def normalize_market_meta_payload(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    condition_id = str(pick(payload, "condition_id", "conditionId") or "").strip()
    slug = str(pick(payload, "slug") or "").strip()
    symbol = str(pick(payload, "symbol") or "").strip()
    interval_sec = as_int(pick(payload, "interval_sec", "intervalSec"))
    start_ms = as_int(pick(payload, "start_ms", "startMs", "market_start_unix_ms"))
    end_ms = as_int(pick(payload, "end_ms", "endMs", "market_end_unix_ms"))

    if not all([condition_id, slug, symbol, interval_sec, start_ms, end_ms]):
        return None

    return {
        "condition_id": condition_id,
        "slug": slug,
        "symbol": symbol,
        "interval_sec": int(interval_sec),
        "start_ms": int(start_ms),
        "end_ms": int(end_ms),
        "yes_token_id": str(pick(payload, "yes_token_id", "yesTokenId") or "") or None,
        "no_token_id": str(pick(payload, "no_token_id", "noTokenId") or "") or None,
        "tick_size": as_float(pick(payload, "tick_size", "tickSize")),
    }


def normalize_md_trade(env: RawEnvelope) -> Optional[Dict[str, Any]]:
    p = env.payload_json
    condition_id = env.condition_id or str(pick(p, "condition_id", "conditionId") or "")
    if not condition_id:
        return None

    market_side = normalize_side(pick(p, "market_side", "marketSide", "outcome_side", "outcome", "side_label"))
    if market_side is None:
        # side might be BUY/SELL in Data API; map by explicit outcome if present.
        market_side = normalize_side(pick(p, "outcome"))

    price = as_float(pick(p, "price", "trade_price"))
    size = as_float(pick(p, "size", "amount", "trade_size"))
    if price is None or size is None:
        return None

    return {
        "condition_id": condition_id,
        "trade_ts_ms": as_int(pick(p, "trade_ts_ms", "tradeTsMs", "timestamp", "time")),
        "recv_ms": env.recv_unix_ms,
        "recv_monotonic_ns": env.recv_monotonic_ns,
        "capture_seq": env.capture_seq,
        "source_ts_ms": infer_source_ts_ms(p),
        "trade_id": str(pick(p, "trade_id", "tradeId", "id") or "") or None,
        "market_side": market_side,
        "taker_side": normalize_direction(pick(p, "taker_side", "takerSide")),
        "price": price,
        "size": size,
        "source_quality": str(pick(p, "source_quality") or ("ws" if env.source.startswith("market") else "unknown")),
    }


def normalize_order_event(env: RawEnvelope) -> Optional[Dict[str, Any]]:
    p = env.payload_json
    condition_id = env.condition_id or str(pick(p, "condition_id", "conditionId", "market") or "")
    if not condition_id:
        return None

    return {
        "condition_id": condition_id,
        "recv_ms": env.recv_unix_ms,
        "recv_monotonic_ns": env.recv_monotonic_ns,
        "capture_seq": env.capture_seq,
        "client_order_id": str(pick(p, "client_order_id", "clientOrderId") or "") or None,
        "order_id": str(pick(p, "order_id", "orderId", "id") or "") or None,
        "event_type": normalize_event_type(p, env.channel),
        "side": normalize_side(pick(p, "side", "market_side", "outcome")),
        "direction": normalize_direction(pick(p, "direction", "order_side", "orderSide", "action", "side")),
        "price": as_float(pick(p, "price")),
        "size": as_float(pick(p, "size", "amount")),
        "remaining": as_float(pick(p, "remaining", "remaining_size", "size_remaining")),
        "status": str(pick(p, "status") or "") or None,
        "reason": str(pick(p, "reason", "message") or "") or None,
        "reject_kind": str(pick(p, "reject_kind", "rejectKind") or "") or None,
        "tx_hash": str(pick(p, "tx_hash", "txHash", "transactionHash", "tx") or "") or None,
        "strategy_tag": str(pick(p, "strategy_tag", "strategyTag") or "") or None,
        "round_id": str(pick(p, "round_id", "roundId") or "") or None,
    }


def normalize_inventory_event(env: RawEnvelope) -> Optional[Dict[str, Any]]:
    p = env.payload_json
    condition_id = env.condition_id or str(pick(p, "condition_id", "conditionId") or "")
    if not condition_id:
        return None

    event_type = str(pick(p, "event_type", "eventType", "type") or "inventory")

    return {
        "condition_id": condition_id,
        "recv_ms": env.recv_unix_ms,
        "recv_monotonic_ns": env.recv_monotonic_ns,
        "capture_seq": env.capture_seq,
        "event_type": event_type,
        "yes_pos": as_float(pick(p, "yes_pos", "working_yes_qty", "yes_qty")),
        "no_pos": as_float(pick(p, "no_pos", "working_no_qty", "no_qty")),
        "yes_avg_cost": as_float(pick(p, "yes_avg_cost")),
        "no_avg_cost": as_float(pick(p, "no_avg_cost")),
        "paired_qty": as_float(pick(p, "paired_qty")),
        "residual_qty": as_float(pick(p, "residual_qty")),
        "usdc_available": as_float(pick(p, "usdc_available", "available_usdc")),
        "tx_hash": str(pick(p, "tx_hash", "txHash", "transactionHash") or "") or None,
    }


def normalize_settlement(env: RawEnvelope) -> Optional[Dict[str, Any]]:
    p = env.payload_json
    condition_id = env.condition_id or str(pick(p, "condition_id", "conditionId", "market") or "")
    if not condition_id:
        return None

    side = normalize_side(pick(p, "official_outcome", "outcome", "winner", "resolution"))
    if side is None:
        return None

    return {
        "condition_id": condition_id,
        "official_outcome": side,
        "settle_ms": as_int(pick(p, "settle_ms", "settled_ms", "timestamp", "resolved_at")),
        "resolution_source": str(pick(p, "resolution_source", "source") or env.source),
        "capture_seq": env.capture_seq,
    }


def normalize_book_row(env: RawEnvelope) -> Optional[Dict[str, Any]]:
    p = env.payload_json
    condition_id = env.condition_id or str(pick(p, "condition_id", "conditionId", "market") or "")
    if not condition_id:
        return None

    l1 = parse_book_l1(p)
    if all(v is None for v in l1.values()):
        return None

    return {
        "condition_id": condition_id,
        "recv_ms": env.recv_unix_ms,
        "recv_monotonic_ns": env.recv_monotonic_ns,
        "capture_seq": env.capture_seq,
        "source_ts_ms": infer_source_ts_ms(p),
        "source_kind": env.source,
        **l1,
    }


def dedup_book_key(row: Dict[str, Any]) -> Tuple:
    return (
        row.get("yes_bid_px"),
        row.get("yes_ask_px"),
        row.get("no_bid_px"),
        row.get("no_ask_px"),
        row.get("yes_bid_sz"),
        row.get("yes_ask_sz"),
        row.get("no_bid_sz"),
        row.get("no_ask_sz"),
    )


def dedup_trade_key(row: Dict[str, Any]) -> Tuple:
    trade_id = row.get("trade_id")
    if trade_id:
        return ("id", row["condition_id"], str(trade_id))
    return (
        "fallback",
        row["condition_id"],
        row.get("trade_ts_ms"),
        row.get("market_side"),
        row.get("price"),
        row.get("size"),
    )


def dedup_order_key(row: Dict[str, Any]) -> Tuple:
    return (
        row.get("client_order_id"),
        row.get("event_type"),
        row.get("status"),
        row.get("recv_ms"),
    )

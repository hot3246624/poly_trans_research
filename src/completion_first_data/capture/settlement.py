"""Settlement polling helpers from Gamma closed-market metadata."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Sequence

import requests

from ..constants import POLYMARKET_GAMMA_MARKETS_URL
from ..utils.time import parse_datetime_to_unix_ms


def _normalize_outcome(value: Any) -> Optional[str]:
    txt = str(value or "").strip().lower()
    if txt in {"yes", "up", "true", "1"}:
        return "YES"
    if txt in {"no", "down", "false", "0"}:
        return "NO"
    return None


def _winner_from_tokens(tokens: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(tokens, list):
        return None, None
    for token in tokens:
        if not isinstance(token, dict):
            continue
        if not bool(token.get("winner")):
            continue
        outcome = _normalize_outcome(token.get("outcome"))
        if outcome is None:
            continue
        token_id = str(token.get("token_id") or "") or None
        return outcome, token_id
    return None, None


def _winner_from_gamma_payload(payload: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
    official_outcome, winner_token_id = _winner_from_tokens(payload.get("tokens"))
    if official_outcome is not None:
        return official_outcome, winner_token_id

    raw_outcomes = payload.get("outcomes")
    raw_prices = payload.get("outcomePrices")
    raw_token_ids = payload.get("clobTokenIds")
    try:
        outcomes = json.loads(raw_outcomes) if isinstance(raw_outcomes, str) else raw_outcomes
    except Exception:
        outcomes = None
    try:
        prices = json.loads(raw_prices) if isinstance(raw_prices, str) else raw_prices
    except Exception:
        prices = None
    try:
        token_ids = json.loads(raw_token_ids) if isinstance(raw_token_ids, str) else raw_token_ids
    except Exception:
        token_ids = None

    if not isinstance(outcomes, Sequence) or not isinstance(prices, Sequence):
        return None, None

    for idx, outcome_label in enumerate(outcomes):
        outcome = _normalize_outcome(outcome_label)
        if outcome is None or idx >= len(prices):
            continue
        try:
            price = float(prices[idx])
        except (TypeError, ValueError):
            continue
        if price < 0.999:
            continue
        token_id = None
        if isinstance(token_ids, Sequence) and idx < len(token_ids):
            token_id = str(token_ids[idx] or "") or None
        return outcome, token_id

    return None, None


def fetch_condition_settlement(
    condition_id: str,
    *,
    market_slug: Optional[str] = None,
    session: Optional[requests.Session] = None,
    timeout_sec: float = 15.0,
) -> Optional[Dict[str, Any]]:
    cid = str(condition_id or "").strip()
    slug = str(market_slug or "").strip()
    if not cid or not slug:
        return None

    sess = session or requests.Session()
    resp = sess.get(
        POLYMARKET_GAMMA_MARKETS_URL,
        params={"slug": slug, "closed": "true"},
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, list) or not payload:
        return None
    market = payload[0]
    if not isinstance(market, dict):
        return None
    if str(market.get("conditionId") or "").strip() != cid:
        return None
    if not bool(market.get("closed")):
        return None

    official_outcome, winner_token_id = _winner_from_gamma_payload(market)
    if official_outcome is None:
        return None

    settle_ms = parse_datetime_to_unix_ms(
        str(market.get("endDate") or market.get("endDateIso") or "").strip()
    )
    return {
        "condition_id": cid,
        "official_outcome": official_outcome,
        "settle_ms": settle_ms,
        "resolution_source": "gamma_market",
        "market_slug": slug,
        "winner_token_id": winner_token_id,
        # Keep a compact serialized copy for future re-parsing and audits.
        "raw_json": json.dumps(market, ensure_ascii=False, separators=(",", ":")),
    }

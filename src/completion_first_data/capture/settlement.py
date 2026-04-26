"""Settlement polling helpers from CLOB market endpoint."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import requests

from ..constants import POLYMARKET_CLOB_MARKETS_URL
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


def fetch_condition_settlement(
    condition_id: str,
    *,
    session: Optional[requests.Session] = None,
    timeout_sec: float = 15.0,
) -> Optional[Dict[str, Any]]:
    cid = str(condition_id or "").strip()
    if not cid:
        return None

    sess = session or requests.Session()
    resp = sess.get(f"{POLYMARKET_CLOB_MARKETS_URL}/{cid}", timeout=timeout_sec)
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict):
        return None
    if not bool(payload.get("closed")):
        return None

    official_outcome, winner_token_id = _winner_from_tokens(payload.get("tokens"))
    if official_outcome is None:
        return None

    settle_ms = parse_datetime_to_unix_ms(str(payload.get("end_date_iso") or "").strip())
    return {
        "condition_id": cid,
        "official_outcome": official_outcome,
        "settle_ms": settle_ms,
        "resolution_source": "clob_market",
        "market_slug": str(payload.get("market_slug") or ""),
        "winner_token_id": winner_token_id,
        # Keep a compact serialized copy for future re-parsing and audits.
        "raw_json": json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    }

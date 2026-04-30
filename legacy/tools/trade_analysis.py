from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import parse_qs, unquote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo


SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
EVENTS_URL = "https://gamma-api.polymarket.com/events"
TRADES_URL = "https://data-api.polymarket.com/trades"
CLOB_URL = "https://clob.polymarket.com"
CACHE_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "trade_analysis" / "_cache"
PRICE_RESOLUTION_THRESHOLD = 0.5
DEFAULT_DISPLAY_TZ = "America/New_York"
EPSILON = 1e-9
YES_OUTCOME_ALIASES = {"up", "yes", "y", "true", "long"}
NO_OUTCOME_ALIASES = {"down", "no", "n", "false", "short"}
CONDITION_ID_RE = re.compile(r"^0x[a-fA-F0-9]{16,}$")
ENV_FILES = (Path(".env"), Path("config/.env"), Path("config/research.env"))
API_KEY_ALIASES = ("CF_API_KEY", "POLYMARKET_BUILDER_API_KEY", "POLYMARKET_API_KEY")
API_SECRET_ALIASES = ("CF_API_SECRET", "POLYMARKET_BUILDER_SECRET", "POLYMARKET_API_SECRET")
API_PASSPHRASE_ALIASES = (
    "CF_API_PASSPHRASE",
    "POLYMARKET_BUILDER_PASSPHRASE",
    "POLYMARKET_API_PASSPHRASE",
)
L1_PRIVATE_KEY_ALIASES = ("CF_L1_PRIVATE_KEY", "POLYMARKET_PRIVATE_KEY")
FUNDER_ADDRESS_ALIASES = ("POLYMARKET_FUNDER_ADDRESS", "ETHEREUM_ADDRESS")
SIGNATURE_TYPE_ALIASES = ("CF_SIGNATURE_TYPE", "PM_SIGNATURE_TYPE")
QUOTE_CHARS = "'\"“”‘’`"


@dataclass
class FetchResult:
    trades: list[dict[str, Any]]
    meta: dict[str, Any]


@dataclass
class OutcomeState:
    shares: float = 0.0
    net_spent: float = 0.0
    cost_basis: float = 0.0
    buy_shares: float = 0.0
    buy_cost: float = 0.0
    sell_shares: float = 0.0
    sell_proceeds: float = 0.0
    realized_pnl: float = 0.0

    @property
    def avg_cost(self) -> float:
        if self.shares > EPSILON:
            return self.cost_basis / self.shares
        return 0.0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int_timestamp(value: Any) -> int:
    try:
        ts = int(float(value or 0))
    except (TypeError, ValueError):
        return 0
    # Some APIs emit milliseconds. The legacy scripts expect seconds.
    if ts > 10_000_000_000:
        ts //= 1000
    return ts


def _sanitize_env_value(value: Any) -> str:
    txt = str(value or "").strip()
    while len(txt) >= 2 and txt[0] in QUOTE_CHARS and txt[-1] in QUOTE_CHARS:
        txt = txt[1:-1].strip()
    if txt and txt[0] in QUOTE_CHARS:
        txt = txt[1:].strip()
    if txt and txt[-1] in QUOTE_CHARS:
        txt = txt[:-1].strip()
    return txt


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for path in ENV_FILES:
        if not path.exists():
            continue
        for line in path.read_text(errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            env[key.strip()] = _sanitize_env_value(value)
    for key, value in os.environ.items():
        if value:
            env[key] = _sanitize_env_value(value)
    return env


def _first_env(env: dict[str, str], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _sanitize_env_value(env.get(key))
        if value:
            return value
    return ""


def _mask_address(value: str) -> str:
    text = str(value or "").strip()
    if len(text) <= 12:
        return text
    return f"{text[:6]}...{text[-4:]}"


def _utc_now_label() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _json_cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_json_cache(namespace: str, key: str) -> Optional[dict[str, Any]]:
    path = CACHE_ROOT / namespace / f"{key}.json"
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_cache(namespace: str, key: str, payload: dict[str, Any]) -> Path:
    path = CACHE_ROOT / namespace / f"{key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    return path


def _cache_path(namespace: str, key: str) -> Path:
    return CACHE_ROOT / namespace / f"{key}.json"


def _normalize_market_text(text: str | None) -> str:
    if not text:
        return ""
    lowered = text.lower().replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def normalize_market_text(text: str | None) -> str:
    return _normalize_market_text(text)


def is_condition_id(value: Any) -> bool:
    return bool(CONDITION_ID_RE.match(str(value or "").strip()))


def extract_market_identifier(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    parsed = urlparse(text)
    if parsed.scheme and parsed.netloc:
        query = parse_qs(parsed.query)
        for key in ("conditionId", "condition_id", "conditionid", "market"):
            for candidate in query.get(key, []):
                if is_condition_id(candidate):
                    return candidate.strip()

        segments = [unquote(part).strip() for part in parsed.path.split("/") if part.strip()]
        for segment in reversed(segments):
            if segment.lower() not in {"event", "events", "market", "markets"}:
                return segment
        return ""

    return text


def normalize_direction(value: Any) -> str:
    txt = str(value or "BUY").strip().upper()
    return "Sell" if txt == "SELL" else "Buy"


def display_outcome(side: str) -> str:
    return "YES" if side == "Up" else "NO"


def trade_label(trade: dict[str, Any], *, include_side: bool = False) -> str:
    side_part = f" {display_outcome(trade['side'])}" if include_side else ""
    return f"{trade['type'].upper()}{side_part} {float(trade['shares']):.2f} @ {float(trade['price']):.2f}c"


def normalize_outcome(value: Any, outcome_index: Any = None) -> str:
    txt = str(value or "").strip().lower()
    if txt in YES_OUTCOME_ALIASES:
        return "Up"
    if txt in NO_OUTCOME_ALIASES:
        return "Down"

    try:
        idx = int(outcome_index)
    except (TypeError, ValueError):
        idx = None
    if idx == 0:
        return "Up"
    if idx == 1:
        return "Down"

    return "Up"


def normalize_resolved_side(value: Any) -> Optional[str]:
    txt = str(value or "").strip().upper()
    if txt in {"YES", "UP"}:
        return "YES"
    if txt in {"NO", "DOWN"}:
        return "NO"
    if txt == "AUTO":
        return "AUTO"
    return None


def outcome_to_resolved_side(outcome: Any, outcome_index: Any = None) -> Optional[str]:
    txt = str(outcome or "").strip().lower()
    if txt in YES_OUTCOME_ALIASES:
        return "YES"
    if txt in NO_OUTCOME_ALIASES:
        return "NO"

    try:
        idx = int(outcome_index)
    except (TypeError, ValueError):
        idx = None
    if idx == 0:
        return "YES"
    if idx == 1:
        return "NO"
    return None


def parse_trades(
    raw_data: Iterable[dict[str, Any]],
    *,
    display_tz: str = DEFAULT_DISPLAY_TZ,
    sort: bool = True,
) -> list[dict[str, Any]]:
    tz = ZoneInfo(display_tz)
    parsed: list[dict[str, Any]] = []
    for item in raw_data:
        price_dollars = _as_float(item.get("price"))
        shares = _as_float(item.get("size"))
        timestamp = _as_int_timestamp(item.get("timestamp"))
        dt_et = dt.datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(tz)
        outcome = normalize_outcome(item.get("outcome"), item.get("outcomeIndex"))
        trade_type = normalize_direction(item.get("side"))
        cost = price_dollars * shares
        signed = 1.0 if trade_type == "Buy" else -1.0
        parsed.append(
            {
                "type": trade_type,
                "market": item.get("title", ""),
                "side": outcome,
                "price": price_dollars * 100.0,
                "price_dollars": price_dollars,
                "shares": shares,
                "cost": cost,
                "signed_cost": signed * cost,
                "signed_shares": signed * shares,
                "timestamp": timestamp,
                "dt": dt_et.replace(tzinfo=None),
                "dt_et": dt_et,
                "time_label": dt_et.strftime("%Y-%m-%d %H:%M:%S %Z"),
                "time_axis_label": dt_et.strftime("%m-%d %H:%M:%S %Z"),
                "time_only_label": dt_et.strftime("%H:%M:%S %Z"),
                "raw": item,
            }
        )

    if sort:
        parsed.sort(key=lambda x: x["timestamp"])
    return parsed


def _apply_trade(state: OutcomeState, trade: dict[str, Any]) -> None:
    qty = float(trade["shares"])
    price = float(trade["price_dollars"])
    gross = float(trade["cost"])

    if trade["type"] == "Buy":
        state.buy_shares += qty
        state.buy_cost += gross
        state.shares += qty
        state.net_spent += gross
        state.cost_basis += gross
        return

    before_shares = state.shares
    before_basis = state.cost_basis
    avg_before = before_basis / before_shares if before_shares > EPSILON else 0.0
    closed_qty = min(qty, before_shares) if before_shares > EPSILON else 0.0
    basis_removed = avg_before * closed_qty

    state.sell_shares += qty
    state.sell_proceeds += gross
    state.shares -= qty
    state.net_spent -= gross
    state.cost_basis = max(0.0, before_basis - basis_removed)
    state.realized_pnl += (price - avg_before) * closed_qty

    if state.shares <= EPSILON:
        state.cost_basis = 0.0


def _snapshot(
    yes: OutcomeState,
    no: OutcomeState,
    trade: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    yes_sh = yes.shares
    no_sh = no.shares
    total_net_spent = yes.net_spent + no.net_spent
    if_yes_wins_pnl = yes_sh - total_net_spent
    if_no_wins_pnl = no_sh - total_net_spent
    locked_profit = min(yes_sh, no_sh) - total_net_spent
    snap: dict[str, Any] = {
        "yes_shares": yes_sh,
        "no_shares": no_sh,
        "total_shares": yes_sh + no_sh,
        "imbalance_shares": yes_sh - no_sh,
        "yes_net_spent": yes.net_spent,
        "no_net_spent": no.net_spent,
        "total_net_spent": total_net_spent,
        "yes_cost_basis": yes.cost_basis,
        "no_cost_basis": no.cost_basis,
        "total_cost_basis": yes.cost_basis + no.cost_basis,
        "yes_avg_cost": yes.avg_cost,
        "no_avg_cost": no.avg_cost,
        "pair_cost": yes.avg_cost + no.avg_cost,
        "yes_buy_shares": yes.buy_shares,
        "yes_buy_cost": yes.buy_cost,
        "yes_sell_shares": yes.sell_shares,
        "yes_sell_proceeds": yes.sell_proceeds,
        "no_buy_shares": no.buy_shares,
        "no_buy_cost": no.buy_cost,
        "no_sell_shares": no.sell_shares,
        "no_sell_proceeds": no.sell_proceeds,
        "total_buy_cost": yes.buy_cost + no.buy_cost,
        "total_sell_proceeds": yes.sell_proceeds + no.sell_proceeds,
        "realized_pnl": yes.realized_pnl + no.realized_pnl,
        "locked_profit": locked_profit,
        "if_yes_wins_pnl": if_yes_wins_pnl,
        "if_no_wins_pnl": if_no_wins_pnl,
    }
    if trade is not None:
        snap["time"] = trade["time_axis_label"]
        snap["time_label"] = trade["time_label"]
        snap["timestamp"] = trade["timestamp"]
    return snap


def calculate_position_series(parsed: list[dict[str, Any]]) -> dict[str, list[Any]]:
    yes = OutcomeState()
    no = OutcomeState()
    rows: list[dict[str, Any]] = []

    for trade in parsed:
        _apply_trade(yes if trade["side"] == "Up" else no, trade)
        rows.append(_snapshot(yes, no, trade))

    keys = [
        "time",
        "timestamp",
        "yes_shares",
        "no_shares",
        "total_shares",
        "imbalance_shares",
        "yes_net_spent",
        "no_net_spent",
        "total_net_spent",
        "yes_cost_basis",
        "no_cost_basis",
        "total_cost_basis",
        "yes_avg_cost",
        "no_avg_cost",
        "pair_cost",
        "yes_buy_shares",
        "yes_buy_cost",
        "yes_sell_shares",
        "yes_sell_proceeds",
        "no_buy_shares",
        "no_buy_cost",
        "no_sell_shares",
        "no_sell_proceeds",
        "total_buy_cost",
        "total_sell_proceeds",
        "realized_pnl",
        "locked_profit",
        "if_yes_wins_pnl",
        "if_no_wins_pnl",
    ]
    return {key: [row[key] for row in rows] for key in keys}


def calculate_trade_summary(parsed: list[dict[str, Any]]) -> dict[str, Any]:
    yes = OutcomeState()
    no = OutcomeState()
    for trade in parsed:
        _apply_trade(yes if trade["side"] == "Up" else no, trade)
    snap = _snapshot(yes, no)
    snap["yes_state"] = yes
    snap["no_state"] = no
    return snap


def calculate_resolution_pnl(parsed: list[dict[str, Any]], resolved_side: str) -> dict[str, float]:
    summary = calculate_trade_summary(parsed)
    normalized = normalize_resolved_side(resolved_side)
    final_value = summary["yes_shares"] if normalized == "YES" else summary["no_shares"]
    total_spent = summary["total_net_spent"]
    return {
        "remaining_yes": summary["yes_shares"],
        "remaining_no": summary["no_shares"],
        "final_value": final_value,
        "total_spent": total_spent,
        "pnl": final_value - total_spent,
        "if_yes_wins_pnl": summary["if_yes_wins_pnl"],
        "if_no_wins_pnl": summary["if_no_wins_pnl"],
        "locked_profit": summary["locked_profit"],
    }


def calculate_table_metrics(parsed: list[dict[str, Any]]) -> list[dict[str, Any]]:
    yes = OutcomeState()
    no = OutcomeState()
    rows: list[dict[str, Any]] = []

    for trade in parsed:
        _apply_trade(yes if trade["side"] == "Up" else no, trade)
        snap = _snapshot(yes, no, trade)
        is_yes = trade["side"] == "Up"
        trade_str = trade_label(trade)
        diff_val = 0.0
        if snap["imbalance_shares"] > 0:
            diff_val = snap["imbalance_shares"] * snap["yes_avg_cost"]
        elif snap["imbalance_shares"] < 0:
            diff_val = abs(snap["imbalance_shares"]) * snap["no_avg_cost"]

        rows.append(
            {
                "time": trade["time_only_label"],
                "yes_trade": trade_str if is_yes else "",
                "no_trade": trade_str if not is_yes else "",
                "cum_yes": snap["yes_shares"],
                "avg_yes": snap["yes_avg_cost"],
                "cum_no": snap["no_shares"],
                "avg_no": snap["no_avg_cost"],
                "pair_cost": snap["pair_cost"],
                "net_diff": snap["imbalance_shares"],
                "diff_val": diff_val,
                "net_spent": snap["total_net_spent"],
                "realized_pnl": snap["realized_pnl"],
                "profit": snap["locked_profit"],
                "if_yes_wins_pnl": snap["if_yes_wins_pnl"],
                "if_no_wins_pnl": snap["if_no_wins_pnl"],
            }
        )

    return rows


def calculate_summary(rows: list[dict[str, Any]]) -> dict[str, float | str]:
    if not rows:
        return {
            "cum_yes": 0.0,
            "cum_no": 0.0,
            "total_spent": 0.0,
            "locked_profit": 0.0,
            "if_yes_wins_pnl": 0.0,
            "if_no_wins_pnl": 0.0,
            "realized_pnl": 0.0,
            "final_verdict": "NEUTRAL",
        }

    last = rows[-1]
    locked_profit = float(last["profit"])
    if locked_profit > 1e-6:
        final_verdict = "POSITIVE"
    elif locked_profit < -1e-6:
        final_verdict = "NEGATIVE"
    else:
        final_verdict = "NEUTRAL"

    return {
        "cum_yes": float(last["cum_yes"]),
        "cum_no": float(last["cum_no"]),
        "total_spent": float(last["net_spent"]),
        "locked_profit": locked_profit,
        "if_yes_wins_pnl": float(last["if_yes_wins_pnl"]),
        "if_no_wins_pnl": float(last["if_no_wins_pnl"]),
        "realized_pnl": float(last["realized_pnl"]),
        "final_verdict": final_verdict,
    }


def infer_resolved_side_from_trades(
    trades: list[dict[str, Any]],
    threshold: float = PRICE_RESOLUTION_THRESHOLD,
) -> tuple[Optional[str], Optional[dict[str, Any]]]:
    if not trades:
        return None, None
    latest = max(trades, key=lambda t: _as_int_timestamp(t.get("timestamp")))
    price = _as_float(latest.get("price"))
    outcome = outcome_to_resolved_side(latest.get("outcome"), latest.get("outcomeIndex"))
    if outcome is None:
        return None, latest

    if price >= threshold:
        inferred = outcome
    else:
        inferred = "NO" if outcome == "YES" else "YES"
    return inferred, latest


def _market_title(event: dict[str, Any], market: dict[str, Any]) -> str:
    return (market.get("question") or market.get("title") or event.get("title") or "").strip()


def _best_market_from_event(
    event: dict[str, Any],
    *,
    identifier: str,
) -> Optional[dict[str, Any]]:
    markets = event.get("markets") or []
    if not markets:
        return None
    if len(markets) == 1:
        return markets[0]

    identifier_norm = _normalize_market_text(identifier)
    for market in markets:
        market_slug = str(market.get("slug") or "").strip()
        if identifier_norm and _normalize_market_text(market_slug) == identifier_norm:
            return market
    return markets[0]


def _fetch_event_by_slug(slug: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    if not slug:
        return None, None
    try:
        resp = requests.get(EVENTS_URL, params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None, None

    events = data if isinstance(data, list) else []
    if not events:
        return None, None

    slug_norm = _normalize_market_text(slug)
    for event in events:
        if _normalize_market_text(event.get("slug")) == slug_norm:
            market = _best_market_from_event(event, identifier=slug)
            return (event, market) if market else (None, None)

    event = events[0]
    market = _best_market_from_event(event, identifier=slug)
    return (event, market) if market else (None, None)


def search_market(query: str) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    try:
        resp = requests.get(SEARCH_URL, params={"q": query}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"Error searching market: {exc}")
        return None, None

    events = data.get("events", []) if isinstance(data, dict) else []
    if not events:
        return None, None

    query_raw = (query or "").strip().lower()
    query_norm = _normalize_market_text(query)
    fallback = None
    best = None
    best_score = -1

    for event in events:
        markets = event.get("markets") or []
        for market in markets:
            title = _market_title(event, market)
            if not title:
                continue
            if fallback is None:
                fallback = (event, market)

            title_raw = title.lower()
            title_norm = _normalize_market_text(title)
            market_slug_norm = _normalize_market_text(market.get("slug"))
            event_slug_norm = _normalize_market_text(event.get("slug"))
            if query_raw and title_raw == query_raw:
                return event, market
            if query_norm and title_norm == query_norm:
                return event, market
            if query_norm and query_norm in {market_slug_norm, event_slug_norm}:
                return event, market

            score = 0
            if query_raw and query_raw in title_raw:
                score = 200
            elif query_norm and query_norm in title_norm:
                score = 180
            elif query_norm and market_slug_norm and query_norm in market_slug_norm:
                score = 170
            elif query_norm and event_slug_norm and query_norm in event_slug_norm:
                score = 160
            else:
                tokens = [t for t in re.split(r"[^a-z0-9]+", query_raw) if t]
                if tokens:
                    search_space = " ".join(
                        str(v or "").lower()
                        for v in (title, market.get("slug"), event.get("slug"))
                    )
                    score = sum(1 for t in set(tokens) if t in search_space) * 10

            if score > best_score:
                best_score = score
                best = (event, market)

    if best and best_score > 0:
        return best
    if fallback:
        return fallback
    return None, None


def resolve_market_identifier(value: Any) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], str]:
    identifier = extract_market_identifier(value)
    if not identifier:
        return None, None, ""

    if is_condition_id(identifier):
        return None, {"conditionId": identifier, "question": identifier, "slug": identifier}, identifier

    event, market = _fetch_event_by_slug(identifier)
    if market:
        return event, market, identifier

    event, market = search_market(identifier)
    return event, market, identifier


def _requests_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": "poly_trans_research/1.0 (+https://github.com/)",
            "Accept": "application/json, text/plain, */*",
        }
    )
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        status=5,
        backoff_factor=0.8,
        status_forcelist=(408, 429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _addr_matches_trade(trade: dict[str, Any], address: str) -> bool:
    addr = (address or "").lower()
    if not addr:
        return False
    for key in ("proxyWallet", "user", "wallet", "maker", "taker", "address"):
        value = trade.get(key)
        if isinstance(value, str) and value.lower() == addr:
            return True
    return False


def with_trade_source(trades: Iterable[dict[str, Any]], source: str) -> list[dict[str, Any]]:
    tagged: list[dict[str, Any]] = []
    for trade in trades:
        item = dict(trade)
        item.setdefault("source", source)
        tagged.append(item)
    return tagged


def describe_trade_source(raw_data: Iterable[dict[str, Any]]) -> str:
    sources = {str(item.get("source") or "").strip() for item in raw_data if isinstance(item, dict)}
    if "clob_user_trades" in sources:
        return "Authenticated CLOB user trade history"
    if "data_api_public_trades" in sources:
        return "Public Data API trades"
    return "Unknown/local JSON"


def trade_source_warning(raw_data: Iterable[dict[str, Any]]) -> str:
    source = describe_trade_source(raw_data)
    if source == "Public Data API trades":
        return (
            "Public Data API can canonicalize binary-market order direction; use matching CLOB credentials "
            "to preserve UI actions such as SELL NO / SELL YES."
        )
    if source == "Unknown/local JSON":
        return "Loaded local JSON; completeness depends on how that JSON was originally fetched."
    return ""


def _clob_auth_context(user_address: str) -> tuple[dict[str, Any], dict[str, Any]]:
    env = _load_env()
    configured_funder = _first_env(env, FUNDER_ADDRESS_ALIASES)
    api_key = _first_env(env, API_KEY_ALIASES)
    api_secret = _first_env(env, API_SECRET_ALIASES)
    api_passphrase = _first_env(env, API_PASSPHRASE_ALIASES)
    l1_private_key = _first_env(env, L1_PRIVATE_KEY_ALIASES)
    signature_type_value = _first_env(env, SIGNATURE_TYPE_ALIASES)
    try:
        signature_type = int(signature_type_value) if signature_type_value else None
    except ValueError:
        signature_type = None

    requested = str(user_address or "").strip()
    context: dict[str, Any] = {
        "attempted": False,
        "can_attempt": False,
        "requested_user": _mask_address(requested),
        "configured_funder": _mask_address(configured_funder),
        "reason": "",
    }
    secrets = {
        "configured_funder": configured_funder,
        "api_key": api_key,
        "api_secret": api_secret,
        "api_passphrase": api_passphrase,
        "l1_private_key": l1_private_key,
        "signature_type": signature_type,
    }

    if not configured_funder:
        context["reason"] = "CLOB auth not configured"
        return context, secrets
    if configured_funder.lower() != requested.lower():
        context["reason"] = "configured CLOB funder does not match requested user"
        return context, secrets
    if not l1_private_key:
        context["reason"] = "CLOB auth requires CF_L1_PRIVATE_KEY/POLYMARKET_PRIVATE_KEY"
        return context, secrets

    context["can_attempt"] = True
    context["reason"] = "configured CLOB funder matches requested user"
    return context, secrets


def _normalize_clob_direction(value: Any) -> str:
    return "SELL" if str(value or "").strip().upper() == "SELL" else "BUY"


def _normalize_clob_outcome(value: Any) -> str:
    normalized = normalize_outcome(value)
    return "YES" if normalized == "Up" else "NO"


def _extract_clob_user_fills(row: dict[str, Any], user_address: str) -> list[dict[str, Any]]:
    market = str(row.get("market") or row.get("conditionId") or row.get("condition_id") or "").strip()
    if not market:
        return []

    trader_side = str(row.get("trader_side") or row.get("traderSide") or "").strip().upper()
    normalized_user = str(user_address or "").strip().lower()
    match_time = row.get("match_time") or row.get("matchTime") or row.get("timestamp") or row.get("last_update")
    tx_hash = row.get("transaction_hash") or row.get("transactionHash") or row.get("tx_hash") or row.get("txHash")
    trade_id = row.get("id") or row.get("trade_id") or row.get("tradeId")
    taker_order_id = row.get("taker_order_id") or row.get("takerOrderId")

    fills: list[dict[str, Any]] = []
    maker_orders = row.get("maker_orders") if isinstance(row.get("maker_orders"), list) else []
    if trader_side == "MAKER" and maker_orders:
        for maker_order in maker_orders:
            if not isinstance(maker_order, dict):
                continue
            owner = str(maker_order.get("maker_address") or maker_order.get("makerAddress") or "").strip().lower()
            if normalized_user and owner and owner != normalized_user:
                continue
            fills.append(
                {
                    "conditionId": market,
                    "asset": maker_order.get("asset_id") or maker_order.get("assetId") or row.get("asset_id") or row.get("assetId"),
                    "side": _normalize_clob_direction(maker_order.get("side") or row.get("side")),
                    "outcome": _normalize_clob_outcome(maker_order.get("outcome") or row.get("outcome")),
                    "size": maker_order.get("matched_amount") or maker_order.get("matchedAmount") or maker_order.get("size") or row.get("size"),
                    "price": maker_order.get("price") or row.get("price"),
                    "timestamp": match_time,
                    "transactionHash": tx_hash,
                    "id": trade_id,
                    "orderId": maker_order.get("order_id") or maker_order.get("orderId"),
                    "takerOrderId": taker_order_id,
                    "traderSide": trader_side,
                    "proxyWallet": user_address,
                    "source": "clob_user_trades",
                    "rawClobTrade": row,
                }
            )

    if fills:
        return fills

    fills.append(
        {
            "conditionId": market,
            "asset": row.get("asset_id") or row.get("assetId") or row.get("asset"),
            "side": _normalize_clob_direction(row.get("side")),
            "outcome": _normalize_clob_outcome(row.get("outcome")),
            "size": row.get("size"),
            "price": row.get("price"),
            "timestamp": match_time,
            "transactionHash": tx_hash,
            "id": trade_id,
            "orderId": row.get("order_id") or row.get("orderId") or taker_order_id,
            "takerOrderId": taker_order_id,
            "traderSide": trader_side or "TAKER",
            "proxyWallet": user_address,
            "source": "clob_user_trades",
            "rawClobTrade": row,
        }
    )
    return fills


def normalize_clob_user_trades(
    rows: Iterable[dict[str, Any]],
    *,
    user_address: str,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for row in rows:
        for fill in _extract_clob_user_fills(row, user_address):
            key = (
                fill.get("id"),
                fill.get("orderId"),
                fill.get("side"),
                fill.get("outcome"),
                str(fill.get("size")),
                str(fill.get("price")),
                str(fill.get("timestamp")),
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(fill)

    normalized.sort(key=lambda item: _as_int_timestamp(item.get("timestamp")))
    return normalized


def _fetch_authenticated_clob_trades(
    condition_id: str,
    user_address: str,
    *,
    verbose: bool = False,
) -> Optional[list[dict[str, Any]]]:
    auth_meta, auth_secrets = _clob_auth_context(user_address)
    if not auth_meta["can_attempt"]:
        if verbose:
            print(f"{auth_meta['reason']}; using public Data API fallback.")
        return None

    try:
        from py_clob_client_v2 import ApiCreds, ClobClient, TradeParams
    except ImportError:
        if verbose:
            print("py_clob_client_v2 is not installed; using public Data API fallback.")
        return None

    try:
        creds = None
        if auth_secrets["api_key"] and auth_secrets["api_secret"] and auth_secrets["api_passphrase"]:
            creds = ApiCreds(
                api_key=auth_secrets["api_key"],
                api_secret=auth_secrets["api_secret"],
                api_passphrase=auth_secrets["api_passphrase"],
            )

        client = ClobClient(
            CLOB_URL,
            chain_id=137,
            key=auth_secrets["l1_private_key"],
            creds=creds,
            signature_type=auth_secrets["signature_type"],
            funder=auth_secrets["configured_funder"],
        )
        if creds is None:
            if hasattr(client, "create_or_derive_api_key"):
                creds = client.create_or_derive_api_key()
            elif hasattr(client, "create_or_derive_api_creds"):
                creds = client.create_or_derive_api_creds()
            if creds is None:
                return None
            client.set_api_creds(creds)

        clob_rows = client.get_trades(params=TradeParams(market=condition_id))
    except Exception as exc:
        if verbose:
            print(f"CLOB authenticated trade fetch failed ({exc.__class__.__name__}); using public Data API fallback.")
        return None

    normalized = normalize_clob_user_trades(clob_rows, user_address=user_address)
    if verbose:
        print(f"Fetched {len(normalized)} user fills from authenticated CLOB trade history.")
    return with_trade_source(normalized, "clob_user_trades")


def fetch_public_trades(
    condition_id: str,
    user_address: str,
    *,
    page_limit: int = 1000,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    if verbose:
        print(f"Fetching public trades for market={condition_id}, user={user_address}...")
    session = _requests_session()

    def fetch_pages(base_params: dict[str, Any], label: str) -> tuple[Optional[list[dict[str, Any]]], Optional[int]]:
        all_trades: list[dict[str, Any]] = []
        offset = 0
        while True:
            params = dict(base_params)
            params["limit"] = page_limit
            params["offset"] = offset
            try:
                resp = session.get(TRADES_URL, params=params, timeout=(10, 120))
            except Exception as exc:
                print(f"Error fetching trades [{label}] (offset={offset}, limit={page_limit}): {exc!r}")
                return None, None

            if resp.status_code >= 400:
                body_preview = (resp.text or "").strip()
                if len(body_preview) > 500:
                    body_preview = body_preview[:500] + "...(truncated)"
                if (
                    resp.status_code == 400
                    and "max historical activity offset" in body_preview.lower()
                    and all_trades
                ):
                    print(
                        f"Reached server-side historical pagination limit [{label}] at offset={offset}. "
                        f"Continuing with {len(all_trades)} trades already fetched."
                    )
                    return all_trades, 206
                print(
                    f"Error fetching trades [{label}] (offset={offset}, limit={page_limit}): "
                    f"HTTP {resp.status_code} {resp.reason}. Body: {body_preview}"
                )
                return None, resp.status_code

            try:
                data = resp.json()
            except Exception as exc:
                print(f"Error parsing trades JSON [{label}] (offset={offset}): {exc!r}")
                return None, None

            if isinstance(data, dict):
                batch = data.get("trades", [])
            elif isinstance(data, list):
                batch = data
            else:
                batch = []

            if not batch:
                break
            all_trades.extend(batch)
            if len(batch) < page_limit:
                break
            offset += page_limit
            if verbose:
                print(f"  [{label}] Fetched {len(all_trades)} trades so far...")

        return all_trades, 200

    trades, status = fetch_pages(
        {"takerOnly": False, "market": condition_id, "user": user_address},
        "market+user",
    )
    if trades is not None:
        return with_trade_source(trades, "data_api_public_trades")

    if status == 408:
        print("Server timed out on market+user query. Falling back to market-only fetch and local address filtering...")
        market_trades, _ = fetch_pages({"takerOnly": False, "market": condition_id}, "market-only")
        if market_trades:
            filtered_market = [t for t in market_trades if _addr_matches_trade(t, user_address)]
            print(f"  [market-only] Filtered {len(filtered_market)} trades for target user.")
            if filtered_market:
                return with_trade_source(filtered_market, "data_api_public_trades")

        print("Market-only path did not yield results. Falling back to user-only fetch and local market filtering...")
        user_trades, _ = fetch_pages({"takerOnly": False, "user": user_address}, "user-only")
        if not user_trades:
            return []
        filtered_user = [
            t
            for t in user_trades
            if str(t.get("conditionId") or t.get("condition_id") or "").lower() == str(condition_id).lower()
        ]
        print(f"  [user-only] Filtered {len(filtered_user)} trades for target market.")
        return with_trade_source(filtered_user, "data_api_public_trades")

    return []


def _fetch_cache_key(condition_id: str, user_address: str, source: str) -> str:
    auth_meta, auth_secrets = _clob_auth_context(user_address)
    return _json_cache_key(
        {
            "kind": "legacy_trade_fetch_v2",
            "condition_id": str(condition_id or "").lower(),
            "user_address": str(user_address or "").lower(),
            "requested_source": source,
            "configured_funder": str(auth_secrets.get("configured_funder") or "").lower(),
            "auth_can_attempt": bool(auth_meta.get("can_attempt")),
        }
    )


def fetch_trades_detailed(
    condition_id: str,
    user_address: str,
    *,
    source: str = "auto",
    page_limit: int = 1000,
    verbose: bool = False,
    use_cache: bool = False,
    refresh_cache: bool = False,
) -> FetchResult:
    requested_source = str(source or "auto").strip().lower()
    if requested_source not in {"auto", "public", "authenticated"}:
        raise ValueError("source must be one of: auto, public, authenticated")

    auth_meta, _ = _clob_auth_context(user_address)
    cache_key = _fetch_cache_key(condition_id, user_address, requested_source)
    cache_info = {
        "enabled": bool(use_cache),
        "hit": False,
        "key": cache_key,
        "path": str(_cache_path("fetch", cache_key)),
    }

    if use_cache and not refresh_cache:
        cached = _read_json_cache("fetch", cache_key)
        if cached and isinstance(cached.get("trades"), list):
            meta = cached.get("fetch_meta") if isinstance(cached.get("fetch_meta"), dict) else {}
            meta["cache"] = {**cache_info, "hit": True}
            meta["fetched_at"] = _utc_now_label()
            meta["trade_count"] = len(cached["trades"])
            return FetchResult(trades=cached["trades"], meta=meta)

    meta: dict[str, Any] = {
        "fetched_at": _utc_now_label(),
        "condition_id": condition_id,
        "user_address": user_address,
        "requested_source": requested_source,
        "data_source": None,
        "view_mode": None,
        "trade_count": 0,
        "authenticated_clob": auth_meta,
        "fallback_reason": None,
        "warnings": [],
        "endpoints": [],
        "cache": cache_info,
    }

    trades: list[dict[str, Any]] = []
    if requested_source in {"auto", "authenticated"}:
        meta["authenticated_clob"]["attempted"] = bool(auth_meta["can_attempt"])
        if auth_meta["can_attempt"]:
            trades = _fetch_authenticated_clob_trades(condition_id, user_address, verbose=verbose) or []
            meta["endpoints"].append("clob:/data/trades")
            if trades:
                meta["data_source"] = "authenticated_clob"
                meta["view_mode"] = "authenticated_execution_view"
            elif requested_source == "authenticated":
                meta["data_source"] = "authenticated_clob"
                meta["view_mode"] = "authenticated_execution_view"
        elif requested_source == "authenticated":
            meta["fallback_reason"] = auth_meta["reason"]
            meta["warnings"].append("Authenticated source requested, but matching CLOB credentials are not available.")

    if not trades and requested_source in {"auto", "public"}:
        if requested_source == "auto" and auth_meta["reason"]:
            meta["fallback_reason"] = auth_meta["reason"]
        trades = fetch_public_trades(condition_id, user_address, page_limit=page_limit, verbose=verbose)
        meta["endpoints"].append("data-api:/trades")
        meta["data_source"] = "public_data_api"
        meta["view_mode"] = "public_canonical_view"

    if meta["data_source"] == "public_data_api":
        warning = trade_source_warning(trades) or (
            "Public Data API can canonicalize binary-market order direction; use matching CLOB credentials "
            "to preserve UI actions such as SELL NO / SELL YES."
        )
        meta["warnings"].append(warning)

    meta["trade_count"] = len(trades)
    if use_cache and trades:
        _write_json_cache("fetch", cache_key, {"trades": trades, "fetch_meta": meta})

    return FetchResult(trades=trades, meta=meta)


def fetch_trades(
    condition_id: str,
    user_address: str,
    *,
    page_limit: int = 1000,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    return fetch_trades_detailed(
        condition_id,
        user_address,
        page_limit=page_limit,
        verbose=verbose,
    ).trades


def format_ts(timestamp: int, *, tz_name: str = DEFAULT_DISPLAY_TZ) -> str:
    tz = ZoneInfo(tz_name)
    return dt.datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

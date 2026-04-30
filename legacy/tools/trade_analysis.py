from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from datetime import timezone
from typing import Any, Iterable, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zoneinfo import ZoneInfo


SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
TRADES_URL = "https://data-api.polymarket.com/trades"
PRICE_RESOLUTION_THRESHOLD = 0.5
DEFAULT_DISPLAY_TZ = "America/New_York"
EPSILON = 1e-9
YES_OUTCOME_ALIASES = {"up", "yes", "y", "true", "long"}
NO_OUTCOME_ALIASES = {"down", "no", "n", "false", "short"}


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


def _normalize_market_text(text: str | None) -> str:
    if not text:
        return ""
    lowered = text.lower().replace("–", "-").replace("—", "-")
    return re.sub(r"[^a-z0-9]+", "", lowered)


def normalize_market_text(text: str | None) -> str:
    return _normalize_market_text(text)


def normalize_direction(value: Any) -> str:
    txt = str(value or "BUY").strip().upper()
    return "Sell" if txt == "SELL" else "Buy"


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
        trade_str = f"{trade['type'].upper()} {trade['shares']:.2f} @ {trade['price']:.2f}c"
        is_yes = trade["side"] == "Up"
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
            title = (market.get("question") or event.get("title") or "").strip()
            if not title:
                continue
            if fallback is None:
                fallback = (event, market)

            title_raw = title.lower()
            title_norm = _normalize_market_text(title)
            if query_raw and title_raw == query_raw:
                return event, market
            if query_norm and title_norm == query_norm:
                return event, market

            score = 0
            if query_raw and query_raw in title_raw:
                score = 200
            elif query_norm and query_norm in title_norm:
                score = 180
            else:
                tokens = [t for t in re.split(r"[^a-z0-9]+", query_raw) if t]
                if tokens:
                    score = sum(1 for t in set(tokens) if t in title_raw) * 10

            if score > best_score:
                best_score = score
                best = (event, market)

    if best and best_score > 0:
        return best
    if fallback:
        return fallback
    return None, None


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


def fetch_trades(
    condition_id: str,
    user_address: str,
    *,
    page_limit: int = 1000,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    if verbose:
        print(f"Fetching trades for market={condition_id}, user={user_address}...")
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
        return trades

    if status == 408:
        print("Server timed out on market+user query. Falling back to market-only fetch and local address filtering...")
        market_trades, _ = fetch_pages({"takerOnly": False, "market": condition_id}, "market-only")
        if market_trades:
            filtered_market = [t for t in market_trades if _addr_matches_trade(t, user_address)]
            print(f"  [market-only] Filtered {len(filtered_market)} trades for target user.")
            if filtered_market:
                return filtered_market

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
        return filtered_user

    return []


def format_ts(timestamp: int, *, tz_name: str = DEFAULT_DISPLAY_TZ) -> str:
    tz = ZoneInfo(tz_name)
    return dt.datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(tz).strftime("%Y-%m-%d %H:%M:%S %Z")

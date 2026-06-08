#!/usr/bin/env python3
"""Public WS Fast-Cancel shadow observer.

This is an online, no-order observer. It subscribes to Polymarket public market
WS, evaluates the Fast-Cancel gate, and writes compact shadow events/reports.
It does not use private keys, does not send REST orders, and does not write raw
or replay data.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from completion_first_data.capture.websocket_sidecar import (  # noqa: WPS436
    BookAssembler,
    _event_type,
    _iter_ws_objects,
    build_market_subscription_message,
    normalize_market_ws_message,
)


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_URL = "https://gamma-api.polymarket.com/markets"
TICK = 0.01


@dataclass(slots=True)
class Market:
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class OrderShadow:
    market: Market
    window_name: str
    first_side: str
    candidate_ts_ms: int
    order_price: float
    clip: float
    required_size: float
    queue_same: float
    top_bid_sz: float | None
    first_timeout_ms: int
    first_reach_ts_ms: int | None = None
    sell_vol_le_order: float = 0.0
    fill_event_count: int = 0
    min_sell_price_before_fill: float | None = None


@dataclass(slots=True)
class EpisodeShadow:
    market: Market
    window_name: str
    first_side: str
    first_fill_ts_ms: int
    first_price: float
    clip: float
    completion_deadline_ms: int
    slow_deadline_ms: int
    repair_deadline_ms: int
    min_pair_cost_seen_30s: float | None = None
    slow_continue_eligible: bool = False


@dataclass(slots=True)
class MarketRuntime:
    market: Market
    assembler: BookAssembler = field(default_factory=BookAssembler)
    l1_by_sec: dict[int, dict[str, Any]] = field(default_factory=dict)
    pending_order: OrderShadow | None = None
    active_episode: EpisodeShadow | None = None
    blocked_until_ms: int = 0
    gate_stats: Counter[str] = field(default_factory=Counter)
    gate_metrics: dict[str, Any] = field(default_factory=dict)
    seen_keys: set[str] = field(default_factory=set)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("configs/xuan/fastcancel_shadow_core_v1.json"))
    parser.add_argument("--prefix", default="btc-updown-5m")
    parser.add_argument("--round-offsets", default="0,1,2", help="Comma-separated 5m round offsets from current UTC slot.")
    parser.add_argument("--duration-sec", type=float, default=900.0)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/fastcancel_public_ws_shadow"))
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--ws-url", default=WS_URL)
    parser.add_argument("--gamma-timeout-sec", type=float, default=4.0)
    parser.add_argument("--no-reset", action="store_true", help="Append to existing event file instead of starting fresh.")
    return parser.parse_args()


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def slug_for_offset(prefix: str, offset: int) -> str:
    now = int(time.time())
    base = (now // 300) * 300
    return f"{prefix}-{base + 300 * offset}"


def parse_offsets(raw: str) -> list[int]:
    out: list[int] = []
    for item in raw.split(","):
        item = item.strip()
        if item:
            out.append(int(item))
    return out or [0, 1]


def parse_token_ids(payload: dict[str, Any]) -> tuple[str, str]:
    raw = payload.get("clobTokenIds") or payload.get("clob_token_ids")
    ids = json.loads(raw) if isinstance(raw, str) else raw
    if isinstance(ids, list) and len(ids) >= 2:
        return str(ids[0]), str(ids[1])
    tokens = payload.get("tokens")
    yes = no = None
    if isinstance(tokens, list):
        for token in tokens:
            if not isinstance(token, dict):
                continue
            outcome = str(token.get("outcome") or token.get("name") or "").lower()
            token_id = str(token.get("token_id") or token.get("tokenId") or "")
            if outcome in {"yes", "up"}:
                yes = token_id
            elif outcome in {"no", "down"}:
                no = token_id
    if yes and no:
        return yes, no
    raise ValueError("missing clob token ids")


def parse_market(payload: dict[str, Any], slug: str) -> Market:
    yes, no = parse_token_ids(payload)
    start_ts = int(slug.rsplit("-", 1)[-1])
    return Market(
        slug=slug,
        condition_id=str(payload.get("conditionId") or payload.get("condition_id")),
        yes_token_id=yes,
        no_token_id=no,
        start_ms=start_ts * 1000,
        end_ms=(start_ts + 300) * 1000,
    )


def resolve_markets(prefix: str, offsets: list[int], timeout_sec: float) -> list[Market]:
    session = requests.Session()
    markets: list[Market] = []
    seen: set[str] = set()
    for offset in offsets:
        slug = slug_for_offset(prefix, offset)
        if slug in seen:
            continue
        seen.add(slug)
        url = f"{GAMMA_URL}?{urlencode({'slug': slug, 'limit': 1})}"
        try:
            resp = session.get(url, timeout=timeout_sec)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list) or not data:
                continue
            markets.append(parse_market(data[0], slug))
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"event": "resolve_market_failed", "slug": slug, "error": str(exc)}))
    session.close()
    return markets


def market_records_for_subscription(markets: list[Market]) -> list[Any]:
    class Rec:
        def __init__(self, market: Market) -> None:
            self.condition_id = market.condition_id
            self.yes_token_id = market.yes_token_id
            self.no_token_id = market.no_token_id

    return [Rec(market) for market in markets]


def asset_maps(markets: list[Market]) -> tuple[dict[str, str], dict[str, str]]:
    asset_to_condition: dict[str, str] = {}
    asset_to_side: dict[str, str] = {}
    for market in markets:
        asset_to_condition[market.yes_token_id] = market.condition_id
        asset_to_condition[market.no_token_id] = market.condition_id
        asset_to_side[market.yes_token_id] = "YES"
        asset_to_side[market.no_token_id] = "NO"
    return asset_to_condition, asset_to_side


def side_quote(book: dict[str, Any], side: str) -> dict[str, Any]:
    prefix = "yes" if side == "YES" else "no"
    return {
        "bid": book.get(f"{prefix}_bid_px"),
        "ask": book.get(f"{prefix}_ask_px"),
        "bid_sz": book.get(f"{prefix}_bid_sz"),
        "ask_sz": book.get(f"{prefix}_ask_sz"),
    }


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def select_momentum_side(current: dict[str, Any], prev: dict[str, Any]) -> tuple[str | None, float | None]:
    best_side = None
    best_delta = None
    for side in ("YES", "NO"):
        cur = side_quote(current, side)["bid"]
        old = side_quote(prev, side)["bid"]
        if cur is None or old is None:
            continue
        delta = float(cur) - float(old)
        if best_delta is None or delta > best_delta:
            best_side = side
            best_delta = delta
    return best_side, best_delta


def l2_queue_same(book: dict[str, Any], side: str, order_price: float) -> tuple[float, float | None, float | None]:
    raw_l2 = book.get("raw_l2") or {}
    side_key = "yes" if side == "YES" else "no"
    bids = ((raw_l2.get(side_key) or {}).get("bids") or [])
    queue_same = 0.0
    top_bid = None
    top_bid_sz = None
    for idx, level in enumerate(bids):
        px = float(level.get("price"))
        sz = float(level.get("size"))
        if idx == 0:
            top_bid = px
            top_bid_sz = sz
        if abs(px - order_price) < TICK / 2:
            queue_same += sz
    return queue_same, top_bid, top_bid_sz


def dynamic_upclip_enabled(strategy: dict[str, Any]) -> tuple[bool, str | None, float | None]:
    cfg = strategy.get("sizing", {}).get("dynamic_upclip", {})
    if not cfg.get("enabled"):
        return False, None, None
    return True, str(cfg.get("condition") or ""), float(cfg.get("effective_clip"))


def dynamic_condition_applies(row: dict[str, Any], condition: str | None) -> bool:
    if not condition:
        return False
    parts = condition.split()
    if len(parts) != 3:
        return False
    field, op, raw_value = parts
    value = float(raw_value)
    left = row.get(field)
    if left is None:
        return False
    left = float(left)
    if op == ">=":
        return left >= value
    if op == ">":
        return left > value
    if op == "<=":
        return left <= value
    if op == "<":
        return left < value
    return False


def event_base(event_type: str, market: Market, ts_ms: int, seq: int) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "event_seq": seq,
        "generated_at": iso_ms(now_ms()),
        "source": "public_ws_fastcancel_shadow",
        "day": iso_ms(ts_ms)[:10],
        "market_slug": market.slug,
        "condition_id": market.condition_id,
        "candidate_ts_ms": ts_ms,
        "candidate_iso": iso_ms(ts_ms),
        "candidate_offset_s": (ts_ms - market.start_ms) / 1000,
    }


def gate_count(rt: MarketRuntime, reason: str, window_name: str | None = None) -> None:
    key = reason if window_name is None else f"{reason}:{window_name}"
    rt.gate_stats[key] += 1


def metric_key(name: str, window_name: str | None = None) -> str:
    return name if window_name is None else f"{name}:{window_name}"


def gate_metric_max(rt: MarketRuntime, name: str, value: float | None, window_name: str | None = None) -> None:
    if value is None or not math.isfinite(float(value)):
        return
    key = metric_key(name, window_name)
    current = rt.gate_metrics.get(key)
    value = float(value)
    if current is None or value > float(current):
        rt.gate_metrics[key] = value


def gate_metric_min(rt: MarketRuntime, name: str, value: float | None, window_name: str | None = None) -> None:
    if value is None or not math.isfinite(float(value)):
        return
    key = metric_key(name, window_name)
    current = rt.gate_metrics.get(key)
    value = float(value)
    if current is None or value < float(current):
        rt.gate_metrics[key] = value


def rounded_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in metrics.items():
        out[key] = round(float(value), 6) if isinstance(value, (int, float)) else value
    return out


def gate_stats_snapshot(runtime: dict[str, MarketRuntime]) -> dict[str, Any]:
    total: Counter[str] = Counter()
    by_market: dict[str, dict[str, int]] = {}
    metrics_by_market: dict[str, dict[str, Any]] = {}
    for rt in runtime.values():
        if not rt.gate_stats:
            if rt.gate_metrics:
                metrics_by_market[rt.market.slug] = rounded_metrics(rt.gate_metrics)
            continue
        total.update(rt.gate_stats)
        by_market[rt.market.slug] = dict(rt.gate_stats)
        if rt.gate_metrics:
            metrics_by_market[rt.market.slug] = rounded_metrics(rt.gate_metrics)
    return {"total": dict(total), "by_market": by_market, "metrics_by_market": metrics_by_market}


class ShadowWriter:
    def __init__(self, output_dir: Path, reset: bool) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = output_dir / "fastcancel_public_ws_shadow_events.jsonl"
        if reset and self.events_path.exists():
            self.events_path.unlink()
        self.seq = 1
        if self.events_path.exists():
            self.seq = sum(1 for _ in self.events_path.open()) + 1

    def emit(self, payload: dict[str, Any]) -> None:
        payload["event_seq"] = self.seq
        self.seq += 1
        with self.events_path.open("a") as f:
            f.write(json.dumps(payload, sort_keys=True) + "\n")

    def write_report(self) -> dict[str, Any]:
        episodes = []
        event_counts: Counter[str] = Counter()
        daily: dict[str, Counter[str]] = defaultdict(Counter)
        latest_gate_stats: dict[str, Any] = {}
        if self.events_path.exists():
            for line in self.events_path.open():
                event = json.loads(line)
                event_type = event.get("event_type", "unknown")
                event_counts[event_type] += 1
                if event_type == "fastcancel_public_ws_shadow_heartbeat" and event.get("gate_stats"):
                    latest_gate_stats = event["gate_stats"]
                if event_type == "fastcancel_episode_summary":
                    episodes.append(event)
                    day = event.get("day") or "unknown"
                    daily[day]["episodes"] += 1
                    if event.get("proxy_first_fill"):
                        daily[day]["proxy_fills"] += 1
                    daily[day][f"path_{event.get('path') or 'unknown'}"] += 1
        fills = sum(1 for event in episodes if event.get("proxy_first_fill"))
        closed = [event for event in episodes if event.get("path") in {"completion", "slow_completion", "repair"}]
        report = {
            "generated_at": iso_ms(now_ms()),
            "events": sum(event_counts.values()),
            "event_type_counts": dict(event_counts),
            "episodes": len(episodes),
            "proxy_filled": fills,
            "proxy_fill_rate": fills / len(episodes) if episodes else None,
            "closed": len(closed),
            "closed_rate_among_filled": len(closed) / fills if fills else None,
            "path_counts": dict(Counter(event.get("path") or "unknown" for event in episodes)),
            "daily": {day: dict(counts) for day, counts in sorted(daily.items())},
            "gate_stats": latest_gate_stats,
            "verdict": {
                "market_side_online_shadow_reportable": len(episodes) > 0,
                "own_execution_truth_ready": False,
                "enforce_evaluable": False,
            },
        }
        (self.output_dir / "fastcancel_public_ws_shadow_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        )
        lines = [
            "# Fast-Cancel Public WS Shadow",
            "",
            f"- generated_at: `{report['generated_at']}`",
            f"- episodes: `{report['episodes']}`",
            f"- proxy_filled: `{report['proxy_filled']}`",
            f"- proxy_fill_rate: `{report['proxy_fill_rate']}`",
            f"- closed_rate_among_filled: `{report['closed_rate_among_filled']}`",
            f"- path_counts: `{report['path_counts']}`",
            "",
            "This report uses public market WS only. It is not own execution truth.",
        ]
        (self.output_dir / "fastcancel_public_ws_shadow_report.md").write_text("\n".join(lines) + "\n")
        return report


def maybe_open_order(rt: MarketRuntime, strategy: dict[str, Any], ts_ms: int, writer: ShadowWriter) -> None:
    sec = ts_ms // 1000
    gate_count(rt, "eval_event")
    if rt.pending_order or rt.active_episode or ts_ms < rt.blocked_until_ms:
        gate_count(rt, "busy_or_blocked")
        return
    if ts_ms < rt.market.start_ms or ts_ms >= rt.market.end_ms:
        gate_count(rt, "outside_market_time")
        return
    offset_s = (ts_ms - rt.market.start_ms) / 1000
    if offset_s >= 300 - float(strategy["state_machine"]["tail_freeze_s"]):
        gate_count(rt, "tail_freeze")
        return
    current = rt.l1_by_sec.get(sec)
    prev = rt.l1_by_sec.get(sec - 1)
    if not current or not prev:
        gate_count(rt, "missing_current_or_prev_book")
        return
    side, prev_delta = select_momentum_side(current, prev)
    if side is None or prev_delta is None:
        gate_count(rt, "no_momentum_side")
        return
    gate_metric_max(rt, "max_prev_bid_delta_1s", prev_delta)
    matched_offset_window = False
    for window in strategy["open_windows"]:
        window_name = str(window["name"])
        if not (float(window["min_offset_s"]) <= offset_s < float(window["max_offset_s"])):
            continue
        matched_offset_window = True
        gate_count(rt, "window_eval", window_name)
        gate_metric_max(rt, "max_prev_bid_delta_1s", prev_delta, window_name)
        if prev_delta < float(window["min_prev_bid_delta_1s"]):
            gate_count(rt, "prev_delta_too_low", window_name)
            continue
        quote = side_quote(current, side)
        opp_quote = side_quote(current, other(side))
        bid = quote["bid"]
        ask = quote["ask"]
        opp_bid = opp_quote["bid"]
        opp_ask = opp_quote["ask"]
        if None in {bid, ask, opp_bid, opp_ask}:
            gate_count(rt, "missing_quote", window_name)
            continue
        bid = float(bid)
        ask = float(ask)
        opp_bid = float(opp_bid)
        opp_ask = float(opp_ask)
        spread_ticks = (ask - bid) * 100
        opp_spread_ticks = (opp_ask - opp_bid) * 100
        gate_metric_min(rt, "min_side_bid", bid, window_name)
        gate_metric_max(rt, "max_side_bid", bid, window_name)
        gate_metric_min(rt, "min_spread_ticks", spread_ticks, window_name)
        gate_metric_min(rt, "min_opp_spread_ticks", opp_spread_ticks, window_name)
        gate_metric_min(rt, "min_immediate_pair_cost", bid + opp_ask, window_name)
        if bid < float(window["min_side_bid"]) or bid >= float(window["max_side_bid"]):
            gate_count(rt, "side_bid_out_of_range", window_name)
            continue
        if spread_ticks > float(window["max_spread_ticks"]):
            gate_count(rt, "spread_too_wide", window_name)
            continue
        if "max_opp_spread_ticks" in window and opp_spread_ticks > float(window["max_opp_spread_ticks"]):
            gate_count(rt, "opp_spread_too_wide", window_name)
            continue
        order_price = bid
        if "max_immediate_pair_cost" in window and order_price + opp_ask > float(window["max_immediate_pair_cost"]):
            gate_count(rt, "immediate_pair_cost_too_high", window_name)
            continue
        queue_same, top_bid, top_bid_sz = l2_queue_same(current, side, order_price)
        gate_metric_min(rt, "min_top_bid_sz", top_bid_sz, window_name)
        gate_metric_max(rt, "max_top_bid_sz", top_bid_sz, window_name)
        gate_metric_min(rt, "min_queue_same", queue_same, window_name)
        if "max_top_bid_sz" in window and top_bid_sz is not None and top_bid_sz > float(window["max_top_bid_sz"]):
            gate_count(rt, "top_bid_sz_too_large", window_name)
            continue
        base_clip = float(strategy["sizing"]["base_clip"])
        candidate_row = {
            "prev_bid_delta_1s": prev_delta,
            "side_bid": bid,
            "top_bid_sz": top_bid_sz,
            "queue_same": queue_same,
            "immediate_pair_cost": order_price + opp_ask,
        }
        up_enabled, up_condition, up_clip = dynamic_upclip_enabled(strategy)
        clip = up_clip if up_enabled and dynamic_condition_applies(candidate_row, up_condition) and up_clip else base_clip
        required_size = clip + queue_same
        first_timeout_ms = ts_ms + int(strategy["first_leg"]["fill_timeout_s"]) * 1000
        rt.pending_order = OrderShadow(
            market=rt.market,
            window_name=str(window["name"]),
            first_side=side,
            candidate_ts_ms=ts_ms,
            order_price=order_price,
            clip=clip,
            required_size=required_size,
            queue_same=queue_same,
            top_bid_sz=top_bid_sz,
            first_timeout_ms=first_timeout_ms,
        )
        gate_count(rt, "candidate_allowed", window_name)
        common = event_base("fastcancel_open_candidate", rt.market, ts_ms, writer.seq)
        common.update(
            {
                "window_name": window["name"],
                "first_side": side,
                "side_bid": round(bid, 6),
                "side_ask": round(ask, 6),
                "opp_bid": round(opp_bid, 6),
                "opp_ask": round(opp_ask, 6),
                "spread_ticks": round(spread_ticks, 6),
                "opp_spread_ticks": round(opp_spread_ticks, 6),
                "prev_bid_delta_1s": round(prev_delta, 6),
                "order_price": round(order_price, 6),
                "base_clip": base_clip,
                "effective_clip": clip,
                "queue_same": round(queue_same, 6),
                "top_bid": top_bid,
                "top_bid_sz": top_bid_sz,
                "required_size_proxy": round(required_size, 6),
                "candidate_allowed": True,
            }
        )
        writer.emit(common)
        place = dict(common)
        place["event_type"] = "fastcancel_would_place_first_maker"
        place["fill_timeout_s"] = strategy["first_leg"]["fill_timeout_s"]
        place["cancel_if_unfilled"] = strategy["first_leg"]["cancel_if_unfilled"]
        writer.emit(place)
        return
    if matched_offset_window:
        gate_count(rt, "no_window_passed")
    else:
        gate_count(rt, "no_matching_offset_window")


def handle_trade(rt: MarketRuntime, trade: dict[str, Any], strategy: dict[str, Any], writer: ShadowWriter) -> None:
    ts_ms = int(trade.get("trade_ts_ms") or now_ms())
    order = rt.pending_order
    if order is None:
        return
    if ts_ms < order.candidate_ts_ms or ts_ms > order.first_timeout_ms:
        return
    if trade.get("market_side") != order.first_side:
        return
    if trade.get("taker_side") not in {"SELL", "Sell", "sell"}:
        return
    price = float(trade["price"])
    size = float(trade["size"])
    if price > order.order_price + 1e-9:
        return
    if order.first_reach_ts_ms is None:
        order.first_reach_ts_ms = ts_ms
    order.sell_vol_le_order += size
    order.fill_event_count += 1
    order.min_sell_price_before_fill = (
        price if order.min_sell_price_before_fill is None else min(order.min_sell_price_before_fill, price)
    )
    if order.sell_vol_le_order + 1e-9 < order.required_size:
        return

    completion = strategy["completion_controller"]
    rt.active_episode = EpisodeShadow(
        market=rt.market,
        window_name=order.window_name,
        first_side=order.first_side,
        first_fill_ts_ms=ts_ms,
        first_price=order.order_price,
        clip=order.clip,
        completion_deadline_ms=ts_ms + int(completion["primary"]["deadline_s"]) * 1000,
        slow_deadline_ms=ts_ms + int(completion["slow_path"]["deadline_s"]) * 1000,
        repair_deadline_ms=ts_ms + int(completion["repair"]["deadline_s"]) * 1000,
    )
    event = event_base("fastcancel_first_fill_truth_or_proxy", rt.market, order.candidate_ts_ms, writer.seq)
    event.update(
        {
            "window_name": order.window_name,
            "first_side": order.first_side,
            "proxy_first_fill": True,
            "proxy_first_fill_ts_ms": ts_ms,
            "proxy_fill_delay_s": round((ts_ms - order.candidate_ts_ms) / 1000, 3),
            "proxy_first_fill_price": round(order.order_price, 6),
            "proxy_first_fill_qty": order.clip,
            "sell_vol_le_order_until_fill": round(order.sell_vol_le_order, 6),
            "fill_event_count": order.fill_event_count,
            "min_sell_price_before_fill": order.min_sell_price_before_fill,
        }
    )
    writer.emit(event)
    rt.pending_order = None


def synthetic_trades_from_price_change(
    item: dict[str, Any],
    *,
    asset_to_condition: dict[str, str],
    asset_to_side: dict[str, str],
    recv_ms: int,
) -> list[tuple[str, dict[str, Any]]]:
    changes = item.get("price_changes")
    if not isinstance(changes, list):
        return []
    out: list[tuple[str, dict[str, Any]]] = []
    for change in changes:
        if not isinstance(change, dict):
            continue
        asset_id = str(change.get("asset_id") or "")
        condition_id = asset_to_condition.get(asset_id)
        market_side = asset_to_side.get(asset_id)
        if not condition_id or market_side not in {"YES", "NO"}:
            continue
        try:
            price = float(change["price"])
            size = float(change["size"])
        except (KeyError, TypeError, ValueError):
            continue
        if size <= 0:
            continue
        out.append(
            (
                condition_id,
                {
                    "condition_id": condition_id,
                    "market_side": market_side,
                    "taker_side": str(change.get("side") or "").upper(),
                    "price": price,
                    "size": size,
                    "trade_ts_ms": recv_ms,
                    "source": "price_change",
                    "raw_json": change,
                },
            )
        )
    return out


def maybe_advance_episode(rt: MarketRuntime, strategy: dict[str, Any], ts_ms: int, writer: ShadowWriter) -> None:
    ep = rt.active_episode
    if ep is None:
        return
    sec = ts_ms // 1000
    book = rt.l1_by_sec.get(sec)
    if not book:
        return
    opp_ask = side_quote(book, other(ep.first_side))["ask"]
    opp_ask_sz = side_quote(book, other(ep.first_side))["ask_sz"]
    if opp_ask is None:
        return
    opp_ask = float(opp_ask)
    pair_cost = ep.first_price + opp_ask
    if ts_ms <= ep.completion_deadline_ms:
        ep.min_pair_cost_seen_30s = pair_cost if ep.min_pair_cost_seen_30s is None else min(ep.min_pair_cost_seen_30s, pair_cost)
    if opp_ask_sz is None or float(opp_ask_sz) < ep.clip:
        return
    completion = strategy["completion_controller"]
    path = None
    if ts_ms <= ep.completion_deadline_ms and pair_cost <= float(completion["primary"]["pair_cost_ceiling"]) + 1e-9:
        path = "completion"
        event_type = "fastcancel_completion_window"
    else:
        if ts_ms > ep.completion_deadline_ms and ep.min_pair_cost_seen_30s is not None:
            ep.slow_continue_eligible = (
                ep.min_pair_cost_seen_30s <= float(completion["slow_path"]["allow_if_min_pair_cost_seen_30s_lte"]) + 1e-9
            )
        if (
            ep.slow_continue_eligible
            and ts_ms <= ep.slow_deadline_ms
            and pair_cost <= float(completion["slow_path"]["pair_cost_ceiling"]) + 1e-9
        ):
            path = "slow_completion"
            event_type = "fastcancel_slow_path_decision"
        elif ts_ms <= ep.repair_deadline_ms and pair_cost <= float(completion["repair"]["pair_cost_ceiling"]) + 1e-9:
            path = "repair"
            event_type = "fastcancel_repair_decision"
        else:
            return
    pnl = (1.0 - pair_cost) * ep.clip
    event = event_base(event_type, rt.market, ep.first_fill_ts_ms, writer.seq)
    event.update(
        {
            "window_name": ep.window_name,
            "first_side": ep.first_side,
            "proxy_first_fill": True,
            "path": path,
            "first_price": round(ep.first_price, 6),
            "second_price": round(opp_ask, 6),
            "pair_cost": round(pair_cost, 6),
            "completion_ts_ms": ts_ms,
            "completion_delay_s": round((ts_ms - ep.first_fill_ts_ms) / 1000, 3),
            "min_pair_cost_seen_30s": None if ep.min_pair_cost_seen_30s is None else round(ep.min_pair_cost_seen_30s, 6),
            "raw_replay_pnl": round(pnl, 6),
        }
    )
    writer.emit(event)
    summary = dict(event)
    summary["event_type"] = "fastcancel_episode_summary"
    writer.emit(summary)
    rt.active_episode = None
    rt.blocked_until_ms = ts_ms + int(strategy["state_machine"]["cooldown_after_close_s"]) * 1000


def expire_pending(rt: MarketRuntime, ts_ms: int, writer: ShadowWriter) -> None:
    order = rt.pending_order
    if order is None or ts_ms <= order.first_timeout_ms:
        return
    event = event_base("fastcancel_first_fill_truth_or_proxy", rt.market, order.candidate_ts_ms, writer.seq)
    event.update(
        {
            "window_name": order.window_name,
            "first_side": order.first_side,
            "proxy_first_fill": False,
            "proxy_fill_delay_s": None,
            "sell_vol_le_order_until_fill": round(order.sell_vol_le_order, 6),
            "fill_event_count": order.fill_event_count,
            "path": "no_first_fill",
        }
    )
    writer.emit(event)
    summary = dict(event)
    summary["event_type"] = "fastcancel_episode_summary"
    summary["raw_replay_pnl"] = 0.0
    writer.emit(summary)
    rt.pending_order = None
    rt.blocked_until_ms = ts_ms


def expire_episode(rt: MarketRuntime, ts_ms: int, writer: ShadowWriter) -> None:
    ep = rt.active_episode
    if ep is None:
        return
    if ts_ms <= max(ep.slow_deadline_ms, ep.repair_deadline_ms):
        return
    event = event_base("fastcancel_residual_exit_plan", rt.market, ep.first_fill_ts_ms, writer.seq)
    event.update(
        {
            "window_name": ep.window_name,
            "first_side": ep.first_side,
            "proxy_first_fill": True,
            "path": "residual_open",
            "first_price": round(ep.first_price, 6),
            "min_pair_cost_seen_30s": None if ep.min_pair_cost_seen_30s is None else round(ep.min_pair_cost_seen_30s, 6),
        }
    )
    writer.emit(event)
    summary = dict(event)
    summary["event_type"] = "fastcancel_episode_summary"
    writer.emit(summary)
    rt.active_episode = None
    rt.blocked_until_ms = ts_ms + 10_000


async def run_observer(args: argparse.Namespace) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Missing dependency: websockets") from exc

    config = load_config(args.config)
    strategy = config["strategy"]
    markets = resolve_markets(args.prefix, parse_offsets(args.round_offsets), args.gamma_timeout_sec)
    if not markets:
        raise SystemExit("no markets resolved")
    subscribe = build_market_subscription_message(market_records_for_subscription(markets))
    if not subscribe:
        raise SystemExit("empty subscription")
    asset_to_condition, asset_to_side = asset_maps(markets)
    market_by_condition = {market.condition_id: market for market in markets}
    runtime = {market.condition_id: MarketRuntime(market=market) for market in markets}
    writer = ShadowWriter(args.output_dir, reset=not args.no_reset)
    allowed = {"book", "price_change", "best_bid_ask", "last_trade_price", "trade", "tick"}
    started = time.monotonic()
    last_report = 0.0
    stats: Counter[str] = Counter()

    writer.emit(
        {
            "event_type": "fastcancel_public_ws_shadow_start",
            "event_seq": writer.seq,
            "generated_at": iso_ms(now_ms()),
            "source": "public_ws_fastcancel_shadow",
            "config": str(args.config),
            "markets": [market.slug for market in markets],
            "asset_count": len(asset_to_condition),
            "place_real_orders": False,
            "send_rest": False,
        }
    )
    while time.monotonic() - started < args.duration_sec:
        try:
            stats["ws_connect_attempt"] += 1
            async with websockets.connect(args.ws_url, ping_interval=None, max_size=None) as ws:
                await ws.send(json.dumps(subscribe, ensure_ascii=False))
                stats["ws_connected"] += 1
                while time.monotonic() - started < args.duration_sec:
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=max(0.2, args.poll_sec))
                    except asyncio.TimeoutError:
                        ts = now_ms()
                        for rt in runtime.values():
                            expire_pending(rt, ts, writer)
                            maybe_advance_episode(rt, strategy, ts, writer)
                            expire_episode(rt, ts, writer)
                        continue
                    if isinstance(raw_msg, bytes):
                        raw_msg = raw_msg.decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue
                    ts = now_ms()
                    for item in _iter_ws_objects(parsed):
                        evt = _event_type(item)
                        stats[f"ws_{evt or 'unknown'}"] += 1
                        condition = asset_to_condition.get(str(item.get("asset_id") or "")) or str(item.get("market") or "")
                        rt = runtime.get(condition)
                        if evt == "price_change":
                            for trade_condition, trade in synthetic_trades_from_price_change(
                                item,
                                asset_to_condition=asset_to_condition,
                                asset_to_side=asset_to_side,
                                recv_ms=ts,
                            ):
                                trade_rt = runtime.get(trade_condition)
                                if trade_rt is not None:
                                    stats["synthetic_price_change_trades"] += 1
                                    handle_trade(trade_rt, trade, strategy, writer)
                        if rt is None and evt != "price_change":
                            continue
                        normalized = normalize_market_ws_message(
                            item,
                            allowed_events=allowed,
                            asset_to_condition_id=asset_to_condition,
                            asset_to_market_side=asset_to_side,
                            assemblers={condition: rt.assembler} if rt is not None else {},
                        )
                        for channel, payload, payload_condition in normalized:
                            market = market_by_condition.get(payload_condition)
                            if market is None:
                                continue
                            rt = runtime[payload_condition]
                            if channel == "book":
                                sec = ts // 1000
                                payload["recv_ms"] = ts
                                rt.l1_by_sec[sec] = payload
                                # Keep memory bounded.
                                for old_sec in list(rt.l1_by_sec):
                                    if old_sec < sec - 240:
                                        rt.l1_by_sec.pop(old_sec, None)
                                maybe_open_order(rt, strategy, ts, writer)
                                maybe_advance_episode(rt, strategy, ts, writer)
                            elif channel == "last_trade_price":
                                payload["recv_ms"] = ts
                                handle_trade(rt, payload, strategy, writer)
                        if rt is not None:
                            expire_pending(rt, ts, writer)
                            expire_episode(rt, ts, writer)
                    if time.monotonic() - last_report >= 30:
                        writer.emit(
                            {
                                "event_type": "fastcancel_public_ws_shadow_heartbeat",
                                "event_seq": writer.seq,
                                "generated_at": iso_ms(now_ms()),
                                "source": "public_ws_fastcancel_shadow",
                                "stats": dict(stats),
                                "gate_stats": gate_stats_snapshot(runtime),
                            }
                        )
                        writer.write_report()
                        last_report = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            stats["ws_disconnect"] += 1
            writer.emit(
                {
                    "event_type": "fastcancel_public_ws_shadow_ws_disconnect",
                    "event_seq": writer.seq,
                    "generated_at": iso_ms(now_ms()),
                    "source": "public_ws_fastcancel_shadow",
                    "error": str(exc),
                    "stats": dict(stats),
                    "gate_stats": gate_stats_snapshot(runtime),
                }
            )
            writer.write_report()
            if time.monotonic() - started >= args.duration_sec:
                break
            await asyncio.sleep(min(5.0, max(0.2, args.poll_sec)))
    writer.write_report()
    print(json.dumps({"output_dir": str(args.output_dir), "markets": [m.slug for m in markets]}, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run_observer(args))


if __name__ == "__main__":
    raise SystemExit(main())

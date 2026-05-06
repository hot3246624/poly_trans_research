#!/usr/bin/env python3
"""Public WS shadow observer for the xuan-inspired taker BUY signal.

This is a no-order observer:

- subscribes to public Polymarket market WS only;
- evaluates configs/xuan/taker_buy_signal_core_v1.json;
- writes compact JSONL events and a report;
- never uses private keys, never sends REST orders, never writes raw capture.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from collections import Counter, defaultdict
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
DEFAULT_CONFIG = Path("configs/xuan/taker_buy_signal_core_v1.json")


@dataclass(slots=True)
class Market:
    slug: str
    condition_id: str
    yes_token_id: str
    no_token_id: str
    start_ms: int
    end_ms: int


@dataclass(slots=True)
class Episode:
    market: Market
    first_side: str
    first_ts_ms: int
    first_price: float
    public_trade_price: float
    public_trade_size: float
    clip: float
    deadline_ms: int
    min_pair_cost_seen: float | None = None
    completed: bool = False


@dataclass(slots=True)
class Runtime:
    market: Market
    assembler: BookAssembler = field(default_factory=BookAssembler)
    latest_book: dict[str, Any] | None = None
    active_episode: Episode | None = None
    block_market: bool = False
    blocked_until_ms: int = 0
    probe_episodes: dict[str, Episode] = field(default_factory=dict)
    probe_block_market: set[str] = field(default_factory=set)
    probe_blocked_until_ms: dict[str, int] = field(default_factory=dict)
    seen_trade_keys: set[str] = field(default_factory=set)
    gate_stats: Counter[str] = field(default_factory=Counter)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--prefix", default="btc-updown-5m")
    parser.add_argument("--round-offsets", default="0,1,2", help="Comma-separated 5m round offsets from current UTC slot.")
    parser.add_argument("--duration-sec", type=float, default=900.0)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_signal_public_ws_shadow"))
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--ws-url", default=WS_URL)
    parser.add_argument("--gamma-timeout-sec", type=float, default=4.0)
    parser.add_argument(
        "--trigger-source",
        choices=("last_trade_price", "price_change", "hybrid"),
        default="last_trade_price",
        help="Default matches replay md_trades. price_change is debug-only.",
    )
    parser.add_argument(
        "--probe-immediate-pairs",
        default="0.99,1.00,1.01",
        help="Comma-separated immediate-pair thresholds for no-order sensitivity probes.",
    )
    parser.add_argument("--no-reset", action="store_true", help="Append to existing event file instead of starting fresh.")
    return parser.parse_args()


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def parse_offsets(raw: str) -> list[int]:
    out = [int(part.strip()) for part in raw.split(",") if part.strip()]
    return out or [0, 1]


def parse_float_list(raw: str) -> list[float]:
    values: list[float] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            values.append(float(part))
    return sorted(set(values))


def threshold_key(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def slug_for_offset(prefix: str, offset: int) -> str:
    base = (int(time.time()) // 300) * 300
    return f"{prefix}-{base + 300 * offset}"


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
            if isinstance(data, list) and data:
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


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_quote(book: dict[str, Any], side: str) -> dict[str, Any]:
    prefix = "yes" if side == "YES" else "no"
    return {
        "bid": book.get(f"{prefix}_bid_px"),
        "ask": book.get(f"{prefix}_ask_px"),
        "bid_sz": book.get(f"{prefix}_bid_sz"),
        "ask_sz": book.get(f"{prefix}_ask_sz"),
    }


def mid(book: dict[str, Any], side: str) -> float | None:
    quote = side_quote(book, side)
    if quote["bid"] is None or quote["ask"] is None:
        return None
    return (float(quote["bid"]) + float(quote["ask"])) / 2.0


def high_side(book: dict[str, Any]) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def ask_levels(book: dict[str, Any], side: str) -> list[tuple[float, float]]:
    raw_l2 = book.get("raw_l2") or {}
    side_key = "yes" if side == "YES" else "no"
    asks = ((raw_l2.get(side_key) or {}).get("asks") or [])
    levels: list[tuple[float, float]] = []
    for level in asks:
        try:
            px = float(level.get("price"))
            sz = float(level.get("size"))
        except (TypeError, ValueError, AttributeError):
            continue
        if px > 0 and sz > 0:
            levels.append((px, sz))
    levels.sort(key=lambda item: item[0])
    return levels


def sweep_vwap(levels: list[tuple[float, float]], target_size: float) -> tuple[float | None, float, float | None]:
    filled = 0.0
    notional = 0.0
    worst_px = None
    for px, sz in levels:
        use = min(sz, target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * px
        worst_px = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst_px
    return None, filled, worst_px


def normalized_taker_side(value: Any) -> str | None:
    raw = str(value or "").upper()
    if raw in {"BUY", "SELL"}:
        return raw
    return None


def fnum(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize_numeric(events: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [v for event in events if (v := fnum(event.get(field))) is not None]
    return {
        "count": len(values),
        "avg": round(sum(values) / len(values), 6) if values else None,
        "p50": percentile(values, 50),
        "p90": percentile(values, 90),
        "min": round(min(values), 6) if values else None,
        "max": round(max(values), 6) if values else None,
    }


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
                    "taker_side": normalized_taker_side(change.get("side")),
                    "price": price,
                    "size": size,
                    "trade_ts_ms": recv_ms,
                    "source": "price_change",
                    "raw_json": change,
                },
            )
        )
    return out


def event_base(event_type: str, market: Market, ts_ms: int) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "generated_at": iso_ms(now_ms()),
        "source": "taker_buy_signal_public_ws_shadow",
        "day": iso_ms(ts_ms)[:10],
        "market_slug": market.slug,
        "condition_id": market.condition_id,
        "ts_ms": ts_ms,
        "ts_iso": iso_ms(ts_ms),
        "offset_s": round((ts_ms - market.start_ms) / 1000.0, 3),
    }


class ShadowWriter:
    def __init__(self, output_dir: Path, reset: bool) -> None:
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = output_dir / "taker_buy_signal_public_ws_shadow_events.jsonl"
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

    def write_report(self, runtime: dict[str, Runtime]) -> dict[str, Any]:
        events = []
        if self.events_path.exists():
            with self.events_path.open() as f:
                events = [json.loads(line) for line in f if line.strip()]
        counts = Counter(event.get("event_type", "unknown") for event in events)
        candidates = [event for event in events if event.get("event_type") == "taker_buy_signal_candidate"]
        allowed = [event for event in candidates if event.get("allowed")]
        summaries = [event for event in events if event.get("event_type") == "taker_buy_signal_episode_summary"]
        completed = [event for event in summaries if event.get("status") == "completed"]
        probe_starts = [event for event in events if event.get("event_type") == "taker_buy_signal_probe_would_take_first"]
        probe_summaries = [
            event for event in events if event.get("event_type") == "taker_buy_signal_probe_episode_summary"
        ]
        probe_completed = [event for event in probe_summaries if event.get("status") == "completed"]
        pnl = sum(float(event.get("shadow_pnl") or 0.0) for event in summaries)
        probe_pnl = sum(float(event.get("shadow_pnl") or 0.0) for event in probe_summaries)
        candidate_reason_counts = Counter(str(event.get("reason") or "unknown") for event in candidates)
        probe_by_threshold: dict[str, dict[str, Any]] = {}
        for threshold in sorted({str(event.get("probe_threshold")) for event in probe_starts + probe_summaries}):
            starts = [event for event in probe_starts if str(event.get("probe_threshold")) == threshold]
            items = [event for event in probe_summaries if str(event.get("probe_threshold")) == threshold]
            wins = [event for event in items if event.get("status") == "completed"]
            pnl_threshold = sum(float(event.get("shadow_pnl") or 0.0) for event in items)
            probe_by_threshold[threshold] = {
                "starts": len(starts),
                "episodes": len(items),
                "completed": len(wins),
                "completion_rate": len(wins) / len(items) if items else None,
                "shadow_pnl": round(pnl_threshold, 6),
                "pair_cost": summarize_numeric(wins, "pair_cost"),
            }
        gate_total: Counter[str] = Counter()
        by_market: dict[str, dict[str, int]] = {}
        for rt in runtime.values():
            gate_total.update(rt.gate_stats)
            by_market[rt.market.slug] = dict(rt.gate_stats)
        report = {
            "generated_at": iso_ms(now_ms()),
            "events": len(events),
            "event_type_counts": dict(counts),
            "candidate_count": len(candidates),
            "allowed_count": len(allowed),
            "allowed_rate": len(allowed) / len(candidates) if candidates else None,
            "candidate_reason_counts": dict(candidate_reason_counts),
            "candidate_metrics": {
                "offset_s": summarize_numeric(candidates, "offset_s"),
                "public_trade_price": summarize_numeric(candidates, "public_trade_price"),
                "public_trade_size": summarize_numeric(candidates, "public_trade_size"),
                "first_l2_vwap": summarize_numeric(candidates, "first_l2_vwap"),
                "l1_immediate_pair": summarize_numeric(candidates, "l1_immediate_pair"),
            },
            "allowed_metrics": {
                "offset_s": summarize_numeric(allowed, "offset_s"),
                "public_trade_price": summarize_numeric(allowed, "public_trade_price"),
                "public_trade_size": summarize_numeric(allowed, "public_trade_size"),
                "first_l2_vwap": summarize_numeric(allowed, "first_l2_vwap"),
                "l1_immediate_pair": summarize_numeric(allowed, "l1_immediate_pair"),
            },
            "episode_count": len(summaries),
            "completed_count": len(completed),
            "completion_rate": len(completed) / len(summaries) if summaries else None,
            "shadow_pnl": round(pnl, 6),
            "probe": {
                "start_count": len(probe_starts),
                "episode_count": len(probe_summaries),
                "completed_count": len(probe_completed),
                "completion_rate": len(probe_completed) / len(probe_summaries) if probe_summaries else None,
                "shadow_pnl": round(probe_pnl, 6),
                "by_threshold": probe_by_threshold,
            },
            "gate_stats": {"total": dict(gate_total), "by_market": by_market},
            "verdict": {
                "public_ws_shadow_running": True,
                "own_execution_truth_ready": False,
                "place_real_orders": False,
            },
        }
        (self.output_dir / "taker_buy_signal_public_ws_shadow_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        lines = [
            "# Taker BUY Signal Public WS Shadow",
            "",
            f"- generated_at: `{report['generated_at']}`",
            f"- candidates: `{report['candidate_count']}`",
            f"- allowed: `{report['allowed_count']}`",
            f"- candidate_reason_counts: `{report['candidate_reason_counts']}`",
            f"- episodes: `{report['episode_count']}`",
            f"- completed: `{report['completed_count']}`",
            f"- shadow_pnl: `{report['shadow_pnl']}`",
            f"- probe: `{report['probe']}`",
            "",
            "This report uses public market WS only. It is not own execution truth and does not place orders.",
        ]
        (self.output_dir / "taker_buy_signal_public_ws_shadow_report.md").write_text("\n".join(lines) + "\n")
        return report


def trade_key(trade: dict[str, Any]) -> str:
    return ":".join(
        [
            str(trade.get("trade_id") or ""),
            str(trade.get("trade_ts_ms") or ""),
            str(trade.get("market_side") or ""),
            str(trade.get("taker_side") or ""),
            str(trade.get("price") or ""),
            str(trade.get("size") or ""),
            str(trade.get("source") or ""),
        ]
    )


def rule(config: dict[str, Any]) -> dict[str, Any]:
    return config["default_rule"]


def runtime_probe_thresholds(config: dict[str, Any]) -> list[float]:
    return [float(v) for v in config.get("_runtime_probe_immediate_pairs", [])]


def emit_candidate(
    writer: ShadowWriter,
    rt: Runtime,
    trade: dict[str, Any],
    *,
    allowed: bool,
    reason: str,
    fields: dict[str, Any] | None = None,
) -> None:
    ts_ms = int(trade.get("trade_ts_ms") or now_ms())
    event = event_base("taker_buy_signal_candidate", rt.market, ts_ms)
    event.update(
        {
            "allowed": allowed,
            "reason": reason,
            "market_side": trade.get("market_side"),
            "taker_side": trade.get("taker_side"),
            "public_trade_price": trade.get("price"),
            "public_trade_size": trade.get("size"),
            "trade_source": trade.get("source") or "last_trade_price",
        }
    )
    if fields:
        event.update(fields)
    writer.emit(event)


def start_probe_episodes(
    rt: Runtime,
    trade: dict[str, Any],
    config: dict[str, Any],
    writer: ShadowWriter,
    *,
    first_side: str,
    first_ts_ms: int,
    first_price: float,
    public_price: float,
    public_size: float,
    immediate_pair: float,
    fields: dict[str, Any],
) -> list[str]:
    cfg = rule(config)
    started: list[str] = []
    for threshold in runtime_probe_thresholds(config):
        key = threshold_key(threshold)
        if immediate_pair > threshold + 1e-9:
            continue
        if key in rt.probe_block_market:
            continue
        if first_ts_ms < rt.probe_blocked_until_ms.get(key, 0):
            continue
        if key in rt.probe_episodes:
            continue
        ep = Episode(
            market=rt.market,
            first_side=first_side,
            first_ts_ms=first_ts_ms,
            first_price=first_price,
            public_trade_price=public_price,
            public_trade_size=public_size,
            clip=float(cfg["clip_size"]),
            deadline_ms=first_ts_ms + int(cfg["completion_window_s"]) * 1000,
        )
        rt.probe_episodes[key] = ep
        started.append(key)
        event = event_base("taker_buy_signal_probe_would_take_first", rt.market, first_ts_ms)
        event.update(fields)
        event.update(
            {
                "probe_threshold": key,
                "first_side": first_side,
                "public_trade_price": public_price,
                "public_trade_size": public_size,
                "send_rest": False,
                "place_real_orders": False,
            }
        )
        writer.emit(event)
    return started


def maybe_open_from_trade(rt: Runtime, trade: dict[str, Any], config: dict[str, Any], writer: ShadowWriter) -> None:
    cfg = rule(config)
    ts_ms = int(trade.get("trade_ts_ms") or now_ms())
    if ts_ms < rt.market.start_ms or ts_ms >= rt.market.end_ms:
        rt.gate_stats["outside_market_time"] += 1
        return
    key = trade_key(trade)
    if key in rt.seen_trade_keys:
        rt.gate_stats["duplicate_trade"] += 1
        return
    rt.seen_trade_keys.add(key)
    if normalized_taker_side(trade.get("taker_side")) != "BUY":
        rt.gate_stats["not_taker_buy"] += 1
        return
    rt.gate_stats["buy_seen"] += 1
    try:
        public_price = float(trade["price"])
        public_size = float(trade["size"])
    except (KeyError, TypeError, ValueError):
        rt.gate_stats["bad_trade_price_or_size"] += 1
        return
    if public_price < float(cfg["trade_price_min"]) or public_price >= float(cfg["trade_price_max"]):
        rt.gate_stats["price_out_of_range"] += 1
        return
    if public_size < float(cfg["trade_size_min"]) or public_size >= float(cfg["trade_size_max"]):
        rt.gate_stats["size_out_of_range"] += 1
        return
    if rt.block_market:
        rt.gate_stats["blocked_after_residual"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="blocked_after_residual")
        return
    if ts_ms < rt.blocked_until_ms or rt.active_episode is not None:
        rt.gate_stats["active_or_cooldown"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="active_or_cooldown")
        return
    if ts_ms >= rt.market.end_ms - int(cfg.get("tail_freeze_s", 60)) * 1000:
        rt.gate_stats["tail_freeze"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="tail_freeze")
        return
    book = rt.latest_book
    if book is None:
        rt.gate_stats["missing_book"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="missing_book")
        return
    book_age_ms = ts_ms - int(book.get("recv_ms") or ts_ms)
    if book_age_ms < -1000 or book_age_ms > 1000:
        rt.gate_stats["stale_book"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="stale_book", fields={"book_age_ms": book_age_ms})
        return
    side = str(trade.get("market_side") or "")
    if side not in {"YES", "NO"}:
        rt.gate_stats["bad_market_side"] += 1
        return
    high = high_side(book)
    if high != side:
        rt.gate_stats["not_high_side"] += 1
        emit_candidate(writer, rt, trade, allowed=False, reason="not_high_side", fields={"high_side": high})
        return
    clip = float(cfg["clip_size"])
    first_vwap, first_filled, first_worst = sweep_vwap(ask_levels(book, side), clip)
    if first_vwap is None:
        rt.gate_stats["insufficient_first_l2"] += 1
        emit_candidate(
            writer,
            rt,
            trade,
            allowed=False,
            reason="insufficient_first_l2",
            fields={"first_l2_filled": round(first_filled, 6)},
        )
        return
    if first_vwap < float(cfg["min_first_l2_vwap"]) or first_vwap >= float(cfg["max_first_l2_vwap"]):
        rt.gate_stats["first_l2_vwap_out_of_range"] += 1
        emit_candidate(
            writer,
            rt,
            trade,
            allowed=False,
            reason="first_l2_vwap_out_of_range",
            fields={"first_l2_vwap": round(first_vwap, 6)},
        )
        return
    opp = other(side)
    opp_quote = side_quote(book, opp)
    if opp_quote["ask"] is None:
        rt.gate_stats["missing_opp_ask"] += 1
        return
    immediate_pair = first_vwap + float(opp_quote["ask"])
    fields = {
        "high_side": high,
        "first_l2_vwap": round(first_vwap, 6),
        "first_l2_filled": round(first_filled, 6),
        "first_l2_worst_px": None if first_worst is None else round(first_worst, 6),
        "opp_l1_ask": round(float(opp_quote["ask"]), 6),
        "l1_immediate_pair": round(immediate_pair, 6),
        "clip": clip,
        "book_age_ms": book_age_ms,
    }
    probe_allowed_thresholds = start_probe_episodes(
        rt,
        trade,
        config,
        writer,
        first_side=side,
        first_ts_ms=ts_ms,
        first_price=first_vwap,
        public_price=public_price,
        public_size=public_size,
        immediate_pair=immediate_pair,
        fields=fields,
    )
    if immediate_pair > float(cfg["max_l1_immediate_pair"]) + 1e-9:
        rt.gate_stats["immediate_pair_too_high"] += 1
        emit_candidate(
            writer,
            rt,
            trade,
            allowed=False,
            reason="immediate_pair_too_high",
            fields={
                "first_l2_vwap": round(first_vwap, 6),
                "opp_l1_ask": round(float(opp_quote["ask"]), 6),
                "l1_immediate_pair": round(immediate_pair, 6),
                "probe_allowed_thresholds": probe_allowed_thresholds,
            },
        )
        return

    rt.gate_stats["allowed"] += 1
    fields["probe_allowed_thresholds"] = probe_allowed_thresholds
    emit_candidate(writer, rt, trade, allowed=True, reason="allowed", fields=fields)
    event = event_base("taker_buy_signal_would_take_first", rt.market, ts_ms)
    event.update(fields)
    event.update(
        {
            "first_side": side,
            "public_trade_price": public_price,
            "public_trade_size": public_size,
            "send_rest": False,
            "place_real_orders": False,
        }
    )
    writer.emit(event)
    rt.active_episode = Episode(
        market=rt.market,
        first_side=side,
        first_ts_ms=ts_ms,
        first_price=first_vwap,
        public_trade_price=public_price,
        public_trade_size=public_size,
        clip=clip,
        deadline_ms=ts_ms + int(cfg["completion_window_s"]) * 1000,
    )


def maybe_complete_episode(rt: Runtime, config: dict[str, Any], writer: ShadowWriter, ts_ms: int) -> None:
    ep = rt.active_episode
    if ep is None or ep.completed:
        return
    book = rt.latest_book
    if book is None:
        return
    opp = other(ep.first_side)
    completion_vwap, filled, worst = sweep_vwap(ask_levels(book, opp), ep.clip)
    if completion_vwap is not None:
        pair_cost = ep.first_price + completion_vwap
        if ts_ms <= ep.deadline_ms:
            ep.min_pair_cost_seen = pair_cost if ep.min_pair_cost_seen is None else min(ep.min_pair_cost_seen, pair_cost)
        if ts_ms <= ep.deadline_ms and pair_cost <= float(rule(config)["completion_pair_ceiling"]) + 1e-9:
            pnl = (1.0 - pair_cost) * ep.clip
            event = event_base("taker_buy_signal_completion", rt.market, ts_ms)
            event.update(
                {
                    "status": "completed",
                    "first_side": ep.first_side,
                    "first_ts_ms": ep.first_ts_ms,
                    "first_price": round(ep.first_price, 6),
                    "completion_vwap": round(completion_vwap, 6),
                    "completion_worst_px": None if worst is None else round(worst, 6),
                    "completion_filled": round(filled, 6),
                    "pair_cost": round(pair_cost, 6),
                    "completion_delay_s": round((ts_ms - ep.first_ts_ms) / 1000.0, 3),
                    "shadow_pnl": round(pnl, 6),
                }
            )
            writer.emit(event)
            summary = dict(event)
            summary["event_type"] = "taker_buy_signal_episode_summary"
            writer.emit(summary)
            ep.completed = True
            rt.active_episode = None
            rt.blocked_until_ms = ts_ms + int(config["execution_model"].get("cooldown_s", 10)) * 1000
            return
    if ts_ms > ep.deadline_ms:
        event = event_base("taker_buy_signal_residual_open", rt.market, ep.deadline_ms)
        event.update(
            {
                "status": "residual_open",
                "first_side": ep.first_side,
                "first_ts_ms": ep.first_ts_ms,
                "first_price": round(ep.first_price, 6),
                "public_trade_price": round(ep.public_trade_price, 6),
                "public_trade_size": round(ep.public_trade_size, 6),
                "min_pair_cost_seen_30s": None if ep.min_pair_cost_seen is None else round(ep.min_pair_cost_seen, 6),
                "completion_l2_filled_last": round(filled, 6),
                "shadow_pnl": None,
            }
        )
        writer.emit(event)
        summary = dict(event)
        summary["event_type"] = "taker_buy_signal_episode_summary"
        writer.emit(summary)
        rt.active_episode = None
        if config["execution_model"].get("block_after_residual", True):
            rt.block_market = True
        else:
            rt.blocked_until_ms = ts_ms + int(config["execution_model"].get("cooldown_s", 10)) * 1000


def maybe_complete_probe_episodes(rt: Runtime, config: dict[str, Any], writer: ShadowWriter, ts_ms: int) -> None:
    if not rt.probe_episodes:
        return
    book = rt.latest_book
    if book is None:
        return
    for probe_key, ep in list(rt.probe_episodes.items()):
        if ep.completed:
            continue
        opp = other(ep.first_side)
        completion_vwap, filled, worst = sweep_vwap(ask_levels(book, opp), ep.clip)
        if completion_vwap is not None:
            pair_cost = ep.first_price + completion_vwap
            if ts_ms <= ep.deadline_ms:
                ep.min_pair_cost_seen = (
                    pair_cost if ep.min_pair_cost_seen is None else min(ep.min_pair_cost_seen, pair_cost)
                )
            if ts_ms <= ep.deadline_ms and pair_cost <= float(rule(config)["completion_pair_ceiling"]) + 1e-9:
                pnl = (1.0 - pair_cost) * ep.clip
                event = event_base("taker_buy_signal_probe_completion", rt.market, ts_ms)
                event.update(
                    {
                        "probe_threshold": probe_key,
                        "status": "completed",
                        "first_side": ep.first_side,
                        "first_ts_ms": ep.first_ts_ms,
                        "first_price": round(ep.first_price, 6),
                        "completion_vwap": round(completion_vwap, 6),
                        "completion_worst_px": None if worst is None else round(worst, 6),
                        "completion_filled": round(filled, 6),
                        "pair_cost": round(pair_cost, 6),
                        "completion_delay_s": round((ts_ms - ep.first_ts_ms) / 1000.0, 3),
                        "shadow_pnl": round(pnl, 6),
                    }
                )
                writer.emit(event)
                summary = dict(event)
                summary["event_type"] = "taker_buy_signal_probe_episode_summary"
                writer.emit(summary)
                ep.completed = True
                rt.probe_episodes.pop(probe_key, None)
                rt.probe_blocked_until_ms[probe_key] = (
                    ts_ms + int(config["execution_model"].get("cooldown_s", 10)) * 1000
                )
                continue
        if ts_ms > ep.deadline_ms:
            event = event_base("taker_buy_signal_probe_residual_open", rt.market, ep.deadline_ms)
            event.update(
                {
                    "probe_threshold": probe_key,
                    "status": "residual_open",
                    "first_side": ep.first_side,
                    "first_ts_ms": ep.first_ts_ms,
                    "first_price": round(ep.first_price, 6),
                    "public_trade_price": round(ep.public_trade_price, 6),
                    "public_trade_size": round(ep.public_trade_size, 6),
                    "min_pair_cost_seen_30s": (
                        None if ep.min_pair_cost_seen is None else round(ep.min_pair_cost_seen, 6)
                    ),
                    "completion_l2_filled_last": round(filled, 6),
                    "shadow_pnl": None,
                }
            )
            writer.emit(event)
            summary = dict(event)
            summary["event_type"] = "taker_buy_signal_probe_episode_summary"
            writer.emit(summary)
            rt.probe_episodes.pop(probe_key, None)
            if config["execution_model"].get("block_after_residual", True):
                rt.probe_block_market.add(probe_key)
            else:
                rt.probe_blocked_until_ms[probe_key] = (
                    ts_ms + int(config["execution_model"].get("cooldown_s", 10)) * 1000
                )


async def run_observer(args: argparse.Namespace) -> int:
    try:
        import websockets
    except ImportError as exc:
        raise SystemExit("Missing dependency: websockets") from exc

    config = load_json(args.config)
    config["_runtime_probe_immediate_pairs"] = parse_float_list(args.probe_immediate_pairs)
    markets = resolve_markets(args.prefix, parse_offsets(args.round_offsets), args.gamma_timeout_sec)
    if not markets:
        raise SystemExit("no markets resolved")
    subscribe = build_market_subscription_message(market_records_for_subscription(markets))
    if not subscribe:
        raise SystemExit("empty subscription")
    asset_to_condition, asset_to_side = asset_maps(markets)
    runtime = {market.condition_id: Runtime(market=market) for market in markets}
    writer = ShadowWriter(args.output_dir, reset=not args.no_reset)
    allowed_events = {"book", "price_change", "best_bid_ask", "last_trade_price", "trade", "tick"}
    stats: Counter[str] = Counter()
    started = time.monotonic()
    last_report = 0.0

    writer.emit(
        {
            "event_type": "taker_buy_signal_public_ws_shadow_start",
            "generated_at": iso_ms(now_ms()),
            "source": "taker_buy_signal_public_ws_shadow",
            "config": str(args.config),
            "markets": [market.slug for market in markets],
            "asset_count": len(asset_to_condition),
            "trigger_source": args.trigger_source,
            "probe_immediate_pairs": config["_runtime_probe_immediate_pairs"],
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
                    ts = now_ms()
                    try:
                        raw_msg = await asyncio.wait_for(ws.recv(), timeout=max(0.2, args.poll_sec))
                    except asyncio.TimeoutError:
                        for rt in runtime.values():
                            maybe_complete_episode(rt, config, writer, now_ms())
                            maybe_complete_probe_episodes(rt, config, writer, now_ms())
                        continue
                    if isinstance(raw_msg, bytes):
                        raw_msg = raw_msg.decode("utf-8", errors="replace")
                    try:
                        parsed = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue
                    for item in _iter_ws_objects(parsed):
                        evt = _event_type(item)
                        stats[f"ws_{evt or 'unknown'}"] += 1
                        if evt == "price_change" and args.trigger_source in {"price_change", "hybrid"}:
                            for condition_id, trade in synthetic_trades_from_price_change(
                                item,
                                asset_to_condition=asset_to_condition,
                                asset_to_side=asset_to_side,
                                recv_ms=ts,
                            ):
                                rt = runtime.get(condition_id)
                                if rt is not None:
                                    stats["synthetic_price_change_trades"] += 1
                                    maybe_open_from_trade(rt, trade, config, writer)
                            continue
                        condition = asset_to_condition.get(str(item.get("asset_id") or "")) or str(item.get("market") or "")
                        rt = runtime.get(condition)
                        if rt is None:
                            continue
                        normalized = normalize_market_ws_message(
                            item,
                            allowed_events=allowed_events,
                            asset_to_condition_id=asset_to_condition,
                            asset_to_market_side=asset_to_side,
                            assemblers={condition: rt.assembler},
                        )
                        for channel, payload, payload_condition in normalized:
                            payload_rt = runtime.get(payload_condition)
                            if payload_rt is None:
                                continue
                            if channel == "book":
                                payload["recv_ms"] = ts
                                payload_rt.latest_book = payload
                                maybe_complete_episode(payload_rt, config, writer, ts)
                                maybe_complete_probe_episodes(payload_rt, config, writer, ts)
                            elif channel == "last_trade_price" and args.trigger_source in {"last_trade_price", "hybrid"}:
                                payload["source"] = "last_trade_price"
                                if payload.get("trade_ts_ms") is None:
                                    payload["trade_ts_ms"] = ts
                                maybe_open_from_trade(payload_rt, payload, config, writer)
                    if time.monotonic() - last_report >= 30:
                        writer.emit(
                            {
                                "event_type": "taker_buy_signal_public_ws_shadow_heartbeat",
                                "generated_at": iso_ms(now_ms()),
                                "source": "taker_buy_signal_public_ws_shadow",
                                "stats": dict(stats),
                            }
                        )
                        writer.write_report(runtime)
                        last_report = time.monotonic()
        except Exception as exc:  # noqa: BLE001
            stats["ws_disconnect"] += 1
            writer.emit(
                {
                    "event_type": "taker_buy_signal_public_ws_shadow_ws_disconnect",
                    "generated_at": iso_ms(now_ms()),
                    "source": "taker_buy_signal_public_ws_shadow",
                    "error": str(exc),
                    "stats": dict(stats),
                }
            )
            writer.write_report(runtime)
            if time.monotonic() - started >= args.duration_sec:
                break
            await asyncio.sleep(min(5.0, max(0.2, args.poll_sec)))
    writer.write_report(runtime)
    print(json.dumps({"output_dir": str(args.output_dir), "markets": [market.slug for market in markets]}, indent=2))
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run_observer(args))


if __name__ == "__main__":
    raise SystemExit(main())

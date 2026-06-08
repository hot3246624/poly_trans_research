#!/usr/bin/env python3
"""Direct public CLOB WS no-order observer for BTC_CORE scoped OOS targets.

This runner is intentionally public-only:
- reads a reviewed projected target CSV;
- subscribes to direct public CLOB market WebSocket streams;
- writes report/audit/gate/eval artifacts;
- never imports candidates, loads private keys, sends orders, or uses REST book
  data as evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
STATUS_KEEP = "KEEP_BTC_CORE_SCOPED_PUBLIC_OOS_EVIDENCE_REVIEW_REQUIRED_PROMOTION_BLOCKED_OWNER_TRUTH"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_SCOPED_PUBLIC_OOS_FAIL_CLOSED"
OUTPUT_FILES = {
    "report": "BTC_CORE_PUBLIC_OOS_REPORT.csv",
    "audit": "BTC_CORE_PUBLIC_OOS_AUDIT_MANIFEST.json",
    "gate": "BTC_CORE_PUBLIC_OOS_GATE_SUMMARY.json",
    "eval": "BTC_CORE_PUBLIC_OOS_EVAL.json",
    "events": "BTC_CORE_PUBLIC_OOS_EVENTS.jsonl",
}


@dataclass(frozen=True, slots=True)
class TargetMarket:
    projection_round_index: int
    slug: str
    market_id: str
    condition_id: str
    token_id_yes: str
    token_id_no: str
    subscribed_asset_ids: tuple[str, ...]
    window_start_ts_ms: int
    window_end_ts_ms: int
    target_role: str = "evidence"


@dataclass(slots=True)
class MarketStats:
    target: TargetMarket
    ws_chunk_id: int
    book_snapshot_count: int = 0
    top_depth_complete_count: int = 0
    stale_snapshot_count: int = 0
    warmup_stale_snapshot_count: int = 0
    post_warmup_stale_snapshot_count: int = 0
    fresh_after_warmup_count: int = 0
    fresh_top_depth_after_warmup_count: int = 0
    message_count: int = 0
    first_recv_ts_ms: int | None = None
    last_recv_ts_ms: int | None = None
    first_source_ts_ms: int | None = None
    last_source_ts_ms: int | None = None
    max_book_age_ms: int | None = None
    first_fresh_after_warmup_recv_ts_ms: int | None = None
    last_fresh_after_warmup_recv_ts_ms: int | None = None
    min_yes_bid_depth: int | None = None
    min_yes_ask_depth: int | None = None
    min_no_bid_depth: int | None = None
    min_no_ask_depth: int | None = None
    book_ages_ms: list[int] = field(default_factory=list)
    latency_ms: list[int] = field(default_factory=list)
    pair_ask_costs: list[float] = field(default_factory=list)
    pair_bid_values: list[float] = field(default_factory=list)
    pair_spreads: list[float] = field(default_factory=list)
    yes_spreads: list[float] = field(default_factory=list)
    no_spreads: list[float] = field(default_factory=list)
    top_yes_ask_sizes: list[float] = field(default_factory=list)
    top_no_ask_sizes: list[float] = field(default_factory=list)
    top_pair_ask_sizes: list[float] = field(default_factory=list)


@dataclass(slots=True)
class WsChunkResult:
    chunk_id: int
    target_count: int
    ws_open_count: int = 0
    ws_disconnect_count: int = 0
    ws_reconnect_count: int = 0
    raw_message_count: int = 0
    normalized_book_count: int = 0
    stop_reason: str = "not_started"
    returncode: int = 0
    error: str | None = None
    events_path: str | None = None
    event_counts: Counter[str] = field(default_factory=Counter)
    stats: dict[str, MarketStats] = field(default_factory=dict)


@dataclass(slots=True)
class _BookSideState:
    bids: list[dict[str, float]] = field(default_factory=list)
    asks: list[dict[str, float]] = field(default_factory=list)
    bid_px: float = 0.0
    ask_px: float = 0.0
    bid_sz: float = 0.0
    ask_sz: float = 0.0
    seen: bool = False


@dataclass(slots=True)
class _FallbackBookAssembler:
    yes: _BookSideState = field(default_factory=_BookSideState)
    no: _BookSideState = field(default_factory=_BookSideState)

    def _state(self, side: str) -> _BookSideState:
        return self.yes if side == "YES" else self.no

    def update_snapshot(self, side: str, bids: Any, asks: Any) -> None:
        state = self._state(side)
        state.bids = _fallback_top_levels(bids, is_bid=True)
        state.asks = _fallback_top_levels(asks, is_bid=False)
        state.bid_px = state.bids[0]["price"] if state.bids else 0.0
        state.bid_sz = state.bids[0]["size"] if state.bids else 0.0
        state.ask_px = state.asks[0]["price"] if state.asks else 0.0
        state.ask_sz = state.asks[0]["size"] if state.asks else 0.0
        state.seen = True

    def update_best_bid_ask(
        self,
        side: str,
        *,
        bid_px: float | None,
        ask_px: float | None,
        bid_sz: float | None = None,
        ask_sz: float | None = None,
    ) -> None:
        state = self._state(side)
        if bid_px is not None:
            state.bid_px = max(0.0, bid_px)
        if ask_px is not None:
            state.ask_px = max(0.0, ask_px)
        if bid_sz is not None:
            state.bid_sz = max(0.0, bid_sz)
        if ask_sz is not None:
            state.ask_sz = max(0.0, ask_sz)
        if bid_px is not None and bid_sz is not None:
            state.bids = _fallback_replace_level(state.bids, price=bid_px, size=bid_sz, is_bid=True)
        if ask_px is not None and ask_sz is not None:
            state.asks = _fallback_replace_level(state.asks, price=ask_px, size=ask_sz, is_bid=False)
        state.seen = True

    def full_l1(self, *, source_ts_ms: int | None) -> dict[str, Any] | None:
        if not self.yes.seen or not self.no.seen:
            return None
        if self.yes.bid_px <= 0.0 or self.no.bid_px <= 0.0:
            return None
        return {
            "yes_bid_px": self.yes.bid_px,
            "yes_ask_px": self.yes.ask_px,
            "no_bid_px": self.no.bid_px,
            "no_ask_px": self.no.ask_px,
            "yes_bid_sz": self.yes.bid_sz,
            "yes_ask_sz": self.yes.ask_sz,
            "no_bid_sz": self.no.bid_sz,
            "no_ask_sz": self.no.ask_sz,
            "source_ts_ms": source_ts_ms,
            "raw_l2": {
                "yes": {"bids": self.yes.bids, "asks": self.yes.asks},
                "no": {"bids": self.no.bids, "asks": self.no.asks},
            },
        }


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def iso_ms(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonish_list(raw: str) -> list[str]:
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("subscribed_asset_ids is not a JSON list")
    return [str(item) for item in value]


def parse_int(raw: str, field_name: str) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} is not an integer: {raw!r}") from exc


def load_targets(path: Path) -> list[TargetMarket]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        required = {
            "projection_round_index",
            "slug",
            "market_id",
            "condition_id",
            "token_id_yes",
            "token_id_no",
            "subscribed_asset_ids",
            "window_start_ts_ms",
            "window_end_ts_ms",
            "binding_status",
        }
        missing = sorted(required - set(reader.fieldnames or []))
        if missing:
            raise ValueError(f"target CSV missing required columns: {missing}")
        targets: list[TargetMarket] = []
        for row in reader:
            if row.get("binding_status") != "BOUND":
                continue
            token_id_yes = str(row["token_id_yes"]).strip()
            token_id_no = str(row["token_id_no"]).strip()
            subscribed = tuple(read_jsonish_list(row["subscribed_asset_ids"]))
            if token_id_yes == token_id_no:
                raise ValueError(f"token_id_yes equals token_id_no for slug={row.get('slug')}")
            if set(subscribed) != {token_id_yes, token_id_no}:
                raise ValueError(f"subscribed_asset_ids mismatch for slug={row.get('slug')}")
            targets.append(
                TargetMarket(
                    projection_round_index=parse_int(row["projection_round_index"], "projection_round_index"),
                    slug=str(row["slug"]).strip(),
                    market_id=str(row["market_id"]).strip(),
                    condition_id=str(row["condition_id"]).strip(),
                    token_id_yes=token_id_yes,
                    token_id_no=token_id_no,
                    subscribed_asset_ids=subscribed,
                    window_start_ts_ms=parse_int(row["window_start_ts_ms"], "window_start_ts_ms"),
                    window_end_ts_ms=parse_int(row["window_end_ts_ms"], "window_end_ts_ms"),
                    target_role=str(row.get("target_role") or "evidence").strip() or "evidence",
                )
            )
    if not targets:
        raise ValueError("target CSV has zero BOUND rows")
    duplicate_conditions = [k for k, v in Counter(t.condition_id for t in targets).items() if v > 1]
    if duplicate_conditions:
        raise ValueError(f"duplicate condition_id values: {duplicate_conditions[:5]}")
    return targets


def stale_targets(targets: Iterable[TargetMarket], *, at_ms: int) -> list[TargetMarket]:
    return [target for target in targets if target.window_start_ts_ms <= at_ms]


def ended_targets(targets: Iterable[TargetMarket], *, at_ms: int) -> list[TargetMarket]:
    return [target for target in targets if target.window_end_ts_ms <= at_ms]


def started_targets(targets: Iterable[TargetMarket], *, at_ms: int) -> list[TargetMarket]:
    return [target for target in targets if target.window_start_ts_ms <= at_ms]


def split_targets(targets: list[TargetMarket], chunks: int) -> list[list[TargetMarket]]:
    chunks = max(1, min(chunks, len(targets)))
    out = [[] for _ in range(chunks)]
    for index, target in enumerate(targets):
        out[index % chunks].append(target)
    return [chunk for chunk in out if chunk]


def pct(values: list[int], q: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return int(ordered[lo])
    return int(round(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)))


def pct_float(values: list[float], q: float, *, digits: int = 6) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(float(ordered[lo]), digits)
    return round(float(ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)), digits)


def _fallback_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def _fallback_ts_ms(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        value = int(raw)
    else:
        text = str(raw).strip()
        if not text:
            return None
        try:
            value = int(float(text))
        except ValueError:
            try:
                return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)
            except ValueError:
                return None
    if value and value < 10_000_000_000:
        value *= 1000
    return value if value > 0 else None


def _fallback_top_levels(levels: Any, *, is_bid: bool, depth: int = 5) -> list[dict[str, float]]:
    if not isinstance(levels, list):
        return []
    parsed: dict[float, float] = {}
    for level in levels:
        if isinstance(level, dict):
            px = _fallback_float(level.get("price") or level.get("p") or level.get("value"))
            sz = _fallback_float(level.get("size") or level.get("s") or level.get("qty") or level.get("amount"))
        elif isinstance(level, (list, tuple)):
            px = _fallback_float(level[0] if len(level) >= 1 else None)
            sz = _fallback_float(level[1] if len(level) >= 2 else None)
        else:
            continue
        if px is None or sz is None or sz <= 0.0:
            continue
        parsed[float(px)] = float(sz)
    return [{"price": px, "size": parsed[px]} for px in sorted(parsed, reverse=is_bid)[:depth]]


def _fallback_replace_level(
    levels: list[dict[str, float]],
    *,
    price: float,
    size: float,
    is_bid: bool,
    depth: int = 5,
) -> list[dict[str, float]]:
    merged = {float(level["price"]): float(level["size"]) for level in levels if "price" in level and "size" in level}
    if size <= 0.0:
        merged.pop(float(price), None)
    else:
        merged[float(price)] = float(size)
    return [{"price": px, "size": merged[px]} for px in sorted(merged, reverse=is_bid)[:depth] if merged[px] > 0.0]


def fallback_iter_ws_objects(parsed: Any) -> Iterable[dict[str, Any]]:
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                yield item
        return
    if not isinstance(parsed, dict):
        return
    payload = parsed.get("data")
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                merged = dict(item)
                for key in ("event_type", "channel", "market", "asset_id", "timestamp"):
                    if key not in merged and key in parsed:
                        merged[key] = parsed[key]
                yield merged
        return
    if isinstance(payload, dict):
        merged = dict(payload)
        for key in ("event_type", "channel", "market", "asset_id", "timestamp"):
            if key not in merged and key in parsed:
                merged[key] = parsed[key]
        yield merged
        return
    yield parsed


def fallback_normalize_market_ws_message(
    msg: dict[str, Any],
    *,
    allowed_events: set[str],
    asset_to_condition_id: dict[str, str],
    asset_to_market_side: dict[str, str],
    assemblers: dict[str, _FallbackBookAssembler],
) -> list[tuple[str, dict[str, Any], str]]:
    evt = str(msg.get("event_type") or msg.get("type") or msg.get("channel") or msg.get("event") or "").strip().lower()
    if evt not in allowed_events:
        return []
    out: list[tuple[str, dict[str, Any], str]] = []
    if evt == "book":
        asset_id = str(msg.get("asset_id") or "").strip()
        condition_id = asset_to_condition_id.get(asset_id) or str(msg.get("market") or "").strip()
        side = asset_to_market_side.get(asset_id)
        if not condition_id or side not in {"YES", "NO"}:
            return out
        asm = assemblers.setdefault(condition_id, _FallbackBookAssembler())
        asm.update_snapshot(side, msg.get("bids") or msg.get("buys"), msg.get("asks") or msg.get("sells"))
        full = asm.full_l1(source_ts_ms=_fallback_ts_ms(msg.get("timestamp") or msg.get("source_ts_ms")))
        if full is not None:
            full.update({"condition_id": condition_id, "raw_asset_id": asset_id, "raw_market_side": side, "raw_event_type": "book", "raw_json": msg})
            out.append(("book", full, condition_id))
        return out
    if evt == "price_change":
        changes = msg.get("price_changes")
        if not isinstance(changes, list):
            return out
        for change in changes:
            if not isinstance(change, dict):
                continue
            asset_id = str(change.get("asset_id") or "").strip()
            condition_id = asset_to_condition_id.get(asset_id)
            side = asset_to_market_side.get(asset_id)
            if not condition_id or side not in {"YES", "NO"}:
                continue
            best_bid = _fallback_float(change.get("best_bid"))
            best_ask = _fallback_float(change.get("best_ask"))
            if best_bid is None and best_ask is None:
                continue
            asm = assemblers.setdefault(condition_id, _FallbackBookAssembler())
            asm.update_best_bid_ask(side, bid_px=best_bid, ask_px=best_ask)
            full = asm.full_l1(source_ts_ms=_fallback_ts_ms(change.get("timestamp") or msg.get("timestamp") or change.get("source_ts_ms")))
            if full is not None:
                full.update({"condition_id": condition_id, "raw_asset_id": asset_id, "raw_market_side": side, "raw_event_type": "price_change", "raw_json": change})
                out.append(("book", full, condition_id))
        return out
    if evt == "best_bid_ask":
        asset_id = str(msg.get("asset_id") or "").strip()
        condition_id = asset_to_condition_id.get(asset_id) or str(msg.get("market") or "").strip()
        side = asset_to_market_side.get(asset_id)
        if not condition_id or side not in {"YES", "NO"}:
            return out
        best_bid = _fallback_float(msg.get("best_bid") or msg.get("bid"))
        best_ask = _fallback_float(msg.get("best_ask") or msg.get("ask"))
        bid_sz = _fallback_float(msg.get("best_bid_size") or msg.get("bid_size"))
        ask_sz = _fallback_float(msg.get("best_ask_size") or msg.get("ask_size"))
        if best_bid is None and best_ask is None:
            return out
        asm = assemblers.setdefault(condition_id, _FallbackBookAssembler())
        asm.update_best_bid_ask(side, bid_px=best_bid, ask_px=best_ask, bid_sz=bid_sz, ask_sz=ask_sz)
        full = asm.full_l1(source_ts_ms=_fallback_ts_ms(msg.get("timestamp") or msg.get("source_ts_ms")))
        if full is not None:
            full.update({"condition_id": condition_id, "raw_asset_id": asset_id, "raw_market_side": side, "raw_event_type": "best_bid_ask", "raw_json": msg})
            out.append(("book", full, condition_id))
    return out


def min_seen(current: int | None, value: int) -> int:
    return value if current is None else min(current, value)


def top_depth_counts(book: dict[str, Any]) -> dict[str, int]:
    raw_l2 = book.get("raw_l2") or {}
    yes = raw_l2.get("yes") or {}
    no = raw_l2.get("no") or {}
    return {
        "yes_bid_depth": len(yes.get("bids") or []),
        "yes_ask_depth": len(yes.get("asks") or []),
        "no_bid_depth": len(no.get("bids") or []),
        "no_ask_depth": len(no.get("asks") or []),
    }


def top_depth_complete(book: dict[str, Any], min_levels: int) -> bool:
    counts = top_depth_counts(book)
    return all(value >= min_levels for value in counts.values())


def collect_price_proxy(stats: MarketStats, book: dict[str, Any]) -> None:
    yes_bid = _fallback_float(book.get("yes_bid_px"))
    yes_ask = _fallback_float(book.get("yes_ask_px"))
    no_bid = _fallback_float(book.get("no_bid_px"))
    no_ask = _fallback_float(book.get("no_ask_px"))
    yes_ask_sz = _fallback_float(book.get("yes_ask_sz"))
    no_ask_sz = _fallback_float(book.get("no_ask_sz"))
    if yes_bid is None or yes_ask is None or no_bid is None or no_ask is None:
        return
    if min(yes_bid, yes_ask, no_bid, no_ask) <= 0.0:
        return
    pair_ask_cost = yes_ask + no_ask
    pair_bid_value = yes_bid + no_bid
    stats.pair_ask_costs.append(pair_ask_cost)
    stats.pair_bid_values.append(pair_bid_value)
    stats.pair_spreads.append(pair_ask_cost - pair_bid_value)
    stats.yes_spreads.append(yes_ask - yes_bid)
    stats.no_spreads.append(no_ask - no_bid)
    if yes_ask_sz is not None and yes_ask_sz > 0:
        stats.top_yes_ask_sizes.append(yes_ask_sz)
    if no_ask_sz is not None and no_ask_sz > 0:
        stats.top_no_ask_sizes.append(no_ask_sz)
    if yes_ask_sz is not None and no_ask_sz is not None and min(yes_ask_sz, no_ask_sz) > 0:
        stats.top_pair_ask_sizes.append(min(yes_ask_sz, no_ask_sz))


def market_row(stats: MarketStats) -> dict[str, Any]:
    ages = stats.book_ages_ms
    latencies = stats.latency_ms
    return {
        "strategy_owner_line": "xuan_research_local",
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "projection_round_index": stats.target.projection_round_index,
        "slug": stats.target.slug,
        "market_id": stats.target.market_id,
        "condition_id": stats.target.condition_id,
        "token_id_yes": stats.target.token_id_yes,
        "token_id_no": stats.target.token_id_no,
        "window_start_ts_ms": stats.target.window_start_ts_ms,
        "window_end_ts_ms": stats.target.window_end_ts_ms,
        "target_role": stats.target.target_role,
        "ws_chunk_id": stats.ws_chunk_id,
        "book_snapshot_count": stats.book_snapshot_count,
        "top_depth_complete_count": stats.top_depth_complete_count,
        "stale_snapshot_count": stats.stale_snapshot_count,
        "warmup_stale_snapshot_count": stats.warmup_stale_snapshot_count,
        "post_warmup_stale_snapshot_count": stats.post_warmup_stale_snapshot_count,
        "fresh_after_warmup_count": stats.fresh_after_warmup_count,
        "fresh_top_depth_after_warmup_count": stats.fresh_top_depth_after_warmup_count,
        "first_recv_ts_ms": stats.first_recv_ts_ms,
        "last_recv_ts_ms": stats.last_recv_ts_ms,
        "first_source_ts_ms": stats.first_source_ts_ms,
        "last_source_ts_ms": stats.last_source_ts_ms,
        "first_fresh_after_warmup_recv_ts_ms": stats.first_fresh_after_warmup_recv_ts_ms,
        "last_fresh_after_warmup_recv_ts_ms": stats.last_fresh_after_warmup_recv_ts_ms,
        "book_age_p50_ms": pct(ages, 0.50),
        "book_age_p95_ms": pct(ages, 0.95),
        "book_age_max_ms": max(ages) if ages else None,
        "latency_p50_ms": pct(latencies, 0.50),
        "latency_p95_ms": pct(latencies, 0.95),
        "latency_max_ms": max(latencies) if latencies else None,
        "pair_ask_cost_p50": pct_float(stats.pair_ask_costs, 0.50),
        "pair_ask_cost_p95": pct_float(stats.pair_ask_costs, 0.95),
        "pair_ask_cost_min": round(min(stats.pair_ask_costs), 6) if stats.pair_ask_costs else None,
        "pair_ask_cost_max": round(max(stats.pair_ask_costs), 6) if stats.pair_ask_costs else None,
        "pair_bid_value_p50": pct_float(stats.pair_bid_values, 0.50),
        "pair_bid_value_p05": pct_float(stats.pair_bid_values, 0.05),
        "pair_spread_p50": pct_float(stats.pair_spreads, 0.50),
        "yes_spread_p50": pct_float(stats.yes_spreads, 0.50),
        "no_spread_p50": pct_float(stats.no_spreads, 0.50),
        "top_yes_ask_size_p50": pct_float(stats.top_yes_ask_sizes, 0.50),
        "top_no_ask_size_p50": pct_float(stats.top_no_ask_sizes, 0.50),
        "top_pair_ask_size_p50": pct_float(stats.top_pair_ask_sizes, 0.50),
        "min_yes_bid_depth": stats.min_yes_bid_depth,
        "min_yes_ask_depth": stats.min_yes_ask_depth,
        "min_no_bid_depth": stats.min_no_bid_depth,
        "min_no_ask_depth": stats.min_no_ask_depth,
        "token_side_top_depth_complete": stats.top_depth_complete_count > 0,
        "observed": stats.book_snapshot_count > 0,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "strategy_owner_line",
        "strategy_id",
        "projection_round_index",
        "slug",
        "market_id",
        "condition_id",
        "token_id_yes",
        "token_id_no",
        "window_start_ts_ms",
        "window_end_ts_ms",
        "target_role",
        "ws_chunk_id",
        "book_snapshot_count",
        "top_depth_complete_count",
        "stale_snapshot_count",
        "warmup_stale_snapshot_count",
        "post_warmup_stale_snapshot_count",
        "fresh_after_warmup_count",
        "fresh_top_depth_after_warmup_count",
        "first_recv_ts_ms",
        "last_recv_ts_ms",
        "first_source_ts_ms",
        "last_source_ts_ms",
        "first_fresh_after_warmup_recv_ts_ms",
        "last_fresh_after_warmup_recv_ts_ms",
        "book_age_p50_ms",
        "book_age_p95_ms",
        "book_age_max_ms",
        "latency_p50_ms",
        "latency_p95_ms",
        "latency_max_ms",
        "pair_ask_cost_p50",
        "pair_ask_cost_p95",
        "pair_ask_cost_min",
        "pair_ask_cost_max",
        "pair_bid_value_p50",
        "pair_bid_value_p05",
        "pair_spread_p50",
        "yes_spread_p50",
        "no_spread_p50",
        "top_yes_ask_size_p50",
        "top_no_ask_size_p50",
        "top_pair_ask_size_p50",
        "min_yes_bid_depth",
        "min_yes_ask_depth",
        "min_no_bid_depth",
        "min_no_ask_depth",
        "token_side_top_depth_complete",
        "observed",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {key: output_dir / name for key, name in OUTPUT_FILES.items()}


def write_fail_closed(
    *,
    output_dir: Path,
    target_csv: Path,
    status_reason: str,
    errors: list[str],
    expected_target_count: int | None = None,
    targets: list[TargetMarket] | None = None,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(output_dir)
    report_rows = [market_row(MarketStats(target=t, ws_chunk_id=-1)) for t in (targets or [])]
    write_report(paths["report"], report_rows)
    common = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS_BLOCKED,
        "status_reason": status_reason,
        "errors": errors,
        "target_csv": str(target_csv),
        "expected_target_count": expected_target_count,
        "non_claims": non_claims(),
    }
    write_json(paths["audit"], {**common, "book_ws_used": False, "transport": "direct_public_clob_ws_not_started"})
    write_json(paths["gate"], {**common, "threshold_failure_count": len(errors), "threshold_failures": errors})
    write_json(paths["eval"], {**common, "ok": False})
    paths["events"].write_text("", encoding="utf-8")


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "orders_authorized": False,
        "cancels_authorized": False,
        "redeems_authorized": False,
        "candidate_import_authorized": False,
        "private_key_loaded": False,
        "latest_pointer_update_authorized": False,
    }


async def observe_chunk(
    *,
    chunk_id: int,
    targets: list[TargetMarket],
    ws_url: str,
    duration_sec: float,
    book_max_age_ms: int,
    min_top_levels: int,
    warmup_sec: float,
    require_live_fresh_after_warmup: bool,
    per_session_warmup: bool,
    allow_sequential_reconnects: bool,
    max_reconnects: int,
    reconnect_backoff_sec: float,
    events_path: Path,
) -> WsChunkResult:
    import websockets

    result = WsChunkResult(chunk_id=chunk_id, target_count=len(targets))
    result.events_path = str(events_path)
    target_by_condition = {target.condition_id: target for target in targets}
    stats = {
        target.condition_id: MarketStats(target=target, ws_chunk_id=chunk_id)
        for target in targets
    }
    result.stats = stats
    asset_to_condition: dict[str, str] = {}
    asset_to_side: dict[str, str] = {}
    for target in targets:
        asset_to_condition[target.token_id_yes] = target.condition_id
        asset_to_condition[target.token_id_no] = target.condition_id
        asset_to_side[target.token_id_yes] = "YES"
        asset_to_side[target.token_id_no] = "NO"
    asset_ids = sorted({asset_id for target in targets for asset_id in target.subscribed_asset_ids})
    subscribe_msg = {
        "type": "market",
        "operation": "subscribe",
        "markets": [],
        "assets_ids": asset_ids,
        "asset_ids": asset_ids,
        "initial_dump": True,
    }
    assemblers: dict[str, Any] = {}
    allowed_events = {"book", "price_change", "best_bid_ask"}
    run_start_ts_ms = now_ms()
    warmup_end_ts_ms = run_start_ts_ms + int(max(0.0, warmup_sec) * 1000)
    stop_at = time.monotonic() + duration_sec
    try:
        from completion_first_data.capture.websocket_sidecar import _iter_ws_objects, normalize_market_ws_message
    except Exception:  # noqa: BLE001
        _iter_ws_objects = fallback_iter_ws_objects
        normalize_market_ws_message = fallback_normalize_market_ws_message

    with events_path.open("a", encoding="utf-8") as event_file:
        while time.monotonic() < stop_at:
            if result.ws_open_count > 0:
                if not allow_sequential_reconnects or result.ws_reconnect_count >= max_reconnects:
                    result.returncode = 2
                    result.stop_reason = "reconnect_limit_reached"
                    break
                result.ws_reconnect_count += 1
                await asyncio.sleep(max(0.0, reconnect_backoff_sec))
            try:
                async with websockets.connect(ws_url, ping_interval=None, max_size=None) as ws:
                    result.ws_open_count += 1
                    session_warmup_end_ts_ms = now_ms() + int(max(0.0, warmup_sec) * 1000)
                    await ws.send(json.dumps(subscribe_msg))
                    result.stop_reason = "connected"
                    while True:
                        remaining = stop_at - time.monotonic()
                        if remaining <= 0:
                            result.stop_reason = "duration_elapsed"
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, remaining))
                        except asyncio.TimeoutError:
                            continue
                        recv_ts_ms = now_ms()
                        result.raw_message_count += 1
                        try:
                            parsed = json.loads(raw)
                        except json.JSONDecodeError:
                            result.event_counts["json_decode_error"] += 1
                            continue
                        for msg in _iter_ws_objects(parsed):
                            event_type = str(msg.get("event_type") or msg.get("type") or msg.get("channel") or "").lower()
                            result.event_counts[event_type or "unknown"] += 1
                            normalized = normalize_market_ws_message(
                                msg,
                                allowed_events=allowed_events,
                                asset_to_condition_id=asset_to_condition,
                                asset_to_market_side=asset_to_side,
                                assemblers=assemblers,
                            )
                            for channel, payload, condition_id in normalized:
                                if channel != "book" or condition_id not in target_by_condition:
                                    continue
                                result.normalized_book_count += 1
                                s = stats[condition_id]
                                s.book_snapshot_count += 1
                                s.message_count += 1
                                s.first_recv_ts_ms = s.first_recv_ts_ms or recv_ts_ms
                                s.last_recv_ts_ms = recv_ts_ms
                                source_ts_ms = payload.get("source_ts_ms")
                                age: int | None = None
                                active_warmup_end_ts_ms = session_warmup_end_ts_ms if per_session_warmup else warmup_end_ts_ms
                                is_after_warmup = recv_ts_ms >= active_warmup_end_ts_ms
                                if isinstance(source_ts_ms, int) and source_ts_ms > 0:
                                    s.first_source_ts_ms = s.first_source_ts_ms or source_ts_ms
                                    s.last_source_ts_ms = source_ts_ms
                                    age = max(0, recv_ts_ms - source_ts_ms)
                                    s.book_ages_ms.append(age)
                                    s.latency_ms.append(age)
                                    s.max_book_age_ms = age if s.max_book_age_ms is None else max(s.max_book_age_ms, age)
                                    if age > book_max_age_ms:
                                        s.stale_snapshot_count += 1
                                        if is_after_warmup:
                                            s.post_warmup_stale_snapshot_count += 1
                                        else:
                                            s.warmup_stale_snapshot_count += 1
                                    elif is_after_warmup:
                                        s.fresh_after_warmup_count += 1
                                        s.first_fresh_after_warmup_recv_ts_ms = (
                                            s.first_fresh_after_warmup_recv_ts_ms or recv_ts_ms
                                        )
                                        s.last_fresh_after_warmup_recv_ts_ms = recv_ts_ms
                                counts = top_depth_counts(payload)
                                s.min_yes_bid_depth = min_seen(s.min_yes_bid_depth, counts["yes_bid_depth"])
                                s.min_yes_ask_depth = min_seen(s.min_yes_ask_depth, counts["yes_ask_depth"])
                                s.min_no_bid_depth = min_seen(s.min_no_bid_depth, counts["no_bid_depth"])
                                s.min_no_ask_depth = min_seen(s.min_no_ask_depth, counts["no_ask_depth"])
                                complete = top_depth_complete(payload, min_top_levels)
                                if complete:
                                    s.top_depth_complete_count += 1
                                    if (
                                        require_live_fresh_after_warmup
                                        and is_after_warmup
                                        and age is not None
                                        and age <= book_max_age_ms
                                    ):
                                        s.fresh_top_depth_after_warmup_count += 1
                                        collect_price_proxy(s, payload)
                                event_file.write(
                                    json.dumps(
                                        {
                                            "chunk_id": chunk_id,
                                            "ws_session_index": result.ws_open_count - 1,
                                            "recv_ts_ms": recv_ts_ms,
                                            "condition_id": condition_id,
                                            "slug": target_by_condition[condition_id].slug,
                                            "source_ts_ms": source_ts_ms,
                                            "book_age_ms": age,
                                            "is_after_warmup": is_after_warmup,
                                            "warmup_end_ts_ms": active_warmup_end_ts_ms,
                                            "per_session_warmup": per_session_warmup,
                                            "fresh_after_warmup": bool(
                                                is_after_warmup
                                                and age is not None
                                                and age <= book_max_age_ms
                                            ),
                                            "top_depth_complete": complete,
                                            **counts,
                                        },
                                        sort_keys=True,
                                    )
                                    + "\n"
                                )
                    if result.stop_reason == "duration_elapsed":
                        break
            except Exception as exc:  # noqa: BLE001
                result.ws_disconnect_count += 1
                result.error = repr(exc)
                if time.monotonic() >= stop_at:
                    result.stop_reason = "duration_elapsed_after_disconnect"
                    break
                if not allow_sequential_reconnects:
                    result.returncode = 2
                    result.stop_reason = "exception"
                    break
                result.stop_reason = "reconnect_pending"
                continue
    if result.stop_reason == "connected":
        result.stop_reason = "duration_elapsed"
    return result


async def run_observer(args: argparse.Namespace) -> int:
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        print(f"BLOCKED_OUTPUT_DIR_EXISTS path={args.output_dir}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(args.output_dir)
    paths["events"].write_text("", encoding="utf-8")
    errors: list[str] = []
    targets: list[TargetMarket] = []
    try:
        if args.expected_target_csv_sha256 and sha256_file(args.target_csv) != args.expected_target_csv_sha256:
            errors.append("target_csv_hash_mismatch")
        targets = load_targets(args.target_csv)
        if args.expected_target_count is not None and len(targets) != args.expected_target_count:
            errors.append(f"target_count_mismatch:{len(targets)}!={args.expected_target_count}")
        preflight_ts_ms = now_ms()
        if args.target_timing_policy == "future_not_started":
            prestart_stale = stale_targets(targets, at_ms=preflight_ts_ms)
            if prestart_stale:
                errors.append(f"stale_target_count_before_start:{len(prestart_stale)}")
        elif args.target_timing_policy == "live_or_future_not_ended":
            prestart_ended = ended_targets(targets, at_ms=preflight_ts_ms)
            if prestart_ended:
                errors.append(f"ended_target_count_before_start:{len(prestart_ended)}")
        else:
            errors.append(f"unknown_target_timing_policy:{args.target_timing_policy}")
        if args.max_ws_connections != 1:
            errors.append("max_ws_connections_out_of_bounds")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"target_load_error:{exc}")
    if errors:
        write_fail_closed(
            output_dir=args.output_dir,
            target_csv=args.target_csv,
            status_reason="preflight_failed",
            errors=errors,
            expected_target_count=args.expected_target_count,
            targets=targets,
        )
        return 2

    chunks = split_targets(targets, args.max_ws_connections)
    chunk_event_paths = [
        args.output_dir / f"BTC_CORE_PUBLIC_OOS_EVENTS_CHUNK_{index}.jsonl"
        for index in range(len(chunks))
    ]
    for chunk_event_path in chunk_event_paths:
        chunk_event_path.write_text("", encoding="utf-8")
    inside_ts_ms = now_ms()
    inside_stale: list[TargetMarket] = []
    if args.target_timing_policy == "future_not_started":
        inside_stale = stale_targets(targets, at_ms=inside_ts_ms)
        inside_error = f"stale_target_count_inside_runner_before_first_observation:{len(inside_stale)}"
    else:
        inside_stale = ended_targets(targets, at_ms=inside_ts_ms)
        inside_error = f"ended_target_count_inside_runner_before_first_observation:{len(inside_stale)}"
    if inside_stale:
        write_fail_closed(
            output_dir=args.output_dir,
            target_csv=args.target_csv,
            status_reason="inside_runner_stale_pre_observation",
            errors=[inside_error],
            expected_target_count=args.expected_target_count,
            targets=targets,
        )
        return 2

    results = await asyncio.gather(
        *[
            observe_chunk(
                chunk_id=index,
                targets=chunk,
                ws_url=args.ws_url,
                duration_sec=args.duration_sec,
                book_max_age_ms=args.book_max_age_ms,
                min_top_levels=args.min_top_levels,
                warmup_sec=args.warmup_sec,
                require_live_fresh_after_warmup=args.require_live_fresh_after_warmup,
                per_session_warmup=args.per_session_warmup,
                allow_sequential_reconnects=args.allow_sequential_reconnects,
                max_reconnects=args.max_reconnects,
                reconnect_backoff_sec=args.reconnect_backoff_sec,
                events_path=chunk_event_paths[index],
            )
            for index, chunk in enumerate(chunks)
        ]
    )
    with paths["events"].open("w", encoding="utf-8") as merged_events:
        for chunk_event_path in chunk_event_paths:
            if chunk_event_path.exists():
                merged_events.write(chunk_event_path.read_text(encoding="utf-8"))
    all_stats = [stats for result in results for stats in result.stats.values()]
    evidence_stats = [
        stats
        for stats in all_stats
        if stats.target.target_role in {"evidence", "evidence_current", "current"}
        or stats.target.target_role.startswith("evidence")
    ]
    evidence_stat_ids = {id(stats) for stats in evidence_stats}
    handoff_stats = [stats for stats in all_stats if id(stats) not in evidence_stat_ids]
    report_rows = [market_row(stats) for stats in sorted(all_stats, key=lambda item: item.target.projection_round_index)]
    write_report(paths["report"], report_rows)

    observed_target_market_count = sum(1 for stats in all_stats if stats.book_snapshot_count > 0)
    top_depth_complete_market_count = sum(1 for stats in all_stats if stats.top_depth_complete_count > 0)
    live_fresh_target_market_count = sum(1 for stats in all_stats if stats.fresh_after_warmup_count > 0)
    live_fresh_top_depth_market_count = sum(
        1 for stats in all_stats if stats.fresh_top_depth_after_warmup_count > 0
    )
    observed_evidence_target_market_count = sum(1 for stats in evidence_stats if stats.book_snapshot_count > 0)
    top_depth_complete_evidence_market_count = sum(1 for stats in evidence_stats if stats.top_depth_complete_count > 0)
    live_fresh_evidence_target_market_count = sum(1 for stats in evidence_stats if stats.fresh_after_warmup_count > 0)
    live_fresh_top_depth_evidence_market_count = sum(
        1 for stats in evidence_stats if stats.fresh_top_depth_after_warmup_count > 0
    )
    warmup_stale_snapshot_count = sum(stats.warmup_stale_snapshot_count for stats in all_stats)
    post_warmup_stale_snapshot_count = sum(stats.post_warmup_stale_snapshot_count for stats in all_stats)
    stale_only_round_count = sum(
        1 for stats in evidence_stats if stats.book_snapshot_count > 0 and stats.top_depth_complete_count == 0
    )
    zero_valid_snapshot_rounds = sum(1 for stats in evidence_stats if stats.book_snapshot_count == 0)
    ws_disconnect_count = sum(result.ws_disconnect_count for result in results)
    ws_reconnect_count = sum(result.ws_reconnect_count for result in results)
    observer_nonzero_rounds = sum(1 for result in results if result.returncode != 0)
    all_ages = [age for stats in all_stats for age in stats.book_ages_ms]
    all_latencies = [latency for stats in all_stats for latency in stats.latency_ms]
    threshold_failures: list[str] = []
    if not evidence_stats:
        threshold_failures.append("evidence_target_market_count_zero")
    if observed_evidence_target_market_count != len(evidence_stats):
        threshold_failures.append(
            f"observed_evidence_target_market_count:{observed_evidence_target_market_count}!={len(evidence_stats)}"
        )
    if top_depth_complete_evidence_market_count != len(evidence_stats):
        threshold_failures.append(
            f"top_depth_complete_evidence_market_count:{top_depth_complete_evidence_market_count}!={len(evidence_stats)}"
        )
    if args.require_live_fresh_after_warmup:
        if live_fresh_evidence_target_market_count != len(evidence_stats):
            threshold_failures.append(
                f"live_fresh_evidence_target_market_count:{live_fresh_evidence_target_market_count}!={len(evidence_stats)}"
            )
        if live_fresh_top_depth_evidence_market_count != len(evidence_stats):
            threshold_failures.append(
                "live_fresh_top_depth_evidence_market_count:"
                f"{live_fresh_top_depth_evidence_market_count}!={len(evidence_stats)}"
            )
    if ws_disconnect_count != 0:
        threshold_failures.append(f"ws_disconnect_count:{ws_disconnect_count}")
    if ws_reconnect_count != 0:
        threshold_failures.append(f"ws_reconnect_count:{ws_reconnect_count}")
    if stale_only_round_count != 0:
        threshold_failures.append(f"stale_only_round_count:{stale_only_round_count}")
    if zero_valid_snapshot_rounds != 0:
        threshold_failures.append(f"zero_valid_snapshot_rounds:{zero_valid_snapshot_rounds}")
    if observer_nonzero_rounds != 0:
        threshold_failures.append(f"observer_nonzero_rounds:{observer_nonzero_rounds}")
    if (not args.require_live_fresh_after_warmup) and any(stats.stale_snapshot_count for stats in all_stats):
        threshold_failures.append("book_age_out_of_bounds")
    status = STATUS_KEEP if not threshold_failures else STATUS_BLOCKED
    summary = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "scoped_public_oos_215_bound_targets_only",
        "target_market_count": len(targets),
        "evidence_target_market_count": len(evidence_stats),
        "handoff_target_market_count": len(handoff_stats),
        "observed_target_market_count": observed_target_market_count,
        "top_depth_complete_market_count": top_depth_complete_market_count,
        "live_fresh_target_market_count": live_fresh_target_market_count,
        "live_fresh_top_depth_market_count": live_fresh_top_depth_market_count,
        "observed_evidence_target_market_count": observed_evidence_target_market_count,
        "top_depth_complete_evidence_market_count": top_depth_complete_evidence_market_count,
        "live_fresh_evidence_target_market_count": live_fresh_evidence_target_market_count,
        "live_fresh_top_depth_evidence_market_count": live_fresh_top_depth_evidence_market_count,
        "warmup_sec": args.warmup_sec,
        "target_timing_policy": args.target_timing_policy,
        "started_target_count_at_summary": len(started_targets((stats.target for stats in all_stats), at_ms=now_ms())),
        "ended_target_count_at_summary": len(ended_targets((stats.target for stats in all_stats), at_ms=now_ms())),
        "require_live_fresh_after_warmup": args.require_live_fresh_after_warmup,
        "per_session_warmup": args.per_session_warmup,
        "allow_sequential_reconnects": args.allow_sequential_reconnects,
        "max_reconnects": args.max_reconnects,
        "reconnect_backoff_sec": args.reconnect_backoff_sec,
        "warmup_stale_snapshot_count": warmup_stale_snapshot_count,
        "post_warmup_stale_snapshot_count": post_warmup_stale_snapshot_count,
        "book_ws_used": True,
        "transport": "direct_public_clob_ws",
        "rest_book_used": False,
        "shared_ingress_used": False,
        "ws_connection_count": len(results),
        "ws_disconnect_count": ws_disconnect_count,
        "ws_reconnect_count": ws_reconnect_count,
        "recovered_round_count": 0,
        "stale_only_round_count": stale_only_round_count,
        "zero_valid_snapshot_rounds": zero_valid_snapshot_rounds,
        "observer_nonzero_rounds": observer_nonzero_rounds,
        "safety_counters": {
            "private_key_loaded": 0,
            "candidate_import_calls": 0,
            "orders_sent": 0,
            "cancels_sent": 0,
            "redeems_sent": 0,
            "live_orders_allowed": 0,
            "latest_pointer_updates": 0,
        },
        "latency_p50_ms": pct(all_latencies, 0.50),
        "latency_p95_ms": pct(all_latencies, 0.95),
        "latency_max_ms": max(all_latencies) if all_latencies else None,
        "book_age_p50_ms": pct(all_ages, 0.50),
        "book_age_p95_ms": pct(all_ages, 0.95),
        "book_age_max_ms": max(all_ages) if all_ages else None,
        "threshold_failure_count": len(threshold_failures),
        "threshold_failures": threshold_failures,
        "readiness": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "non_claims": non_claims(),
    }
    chunk_audit = [
        {
            "chunk_id": result.chunk_id,
            "target_count": result.target_count,
            "ws_open_count": result.ws_open_count,
            "ws_disconnect_count": result.ws_disconnect_count,
            "ws_reconnect_count": result.ws_reconnect_count,
            "raw_message_count": result.raw_message_count,
            "normalized_book_count": result.normalized_book_count,
            "stop_reason": result.stop_reason,
            "returncode": result.returncode,
            "error": result.error,
            "events_path": result.events_path,
            "event_counts": dict(result.event_counts),
        }
        for result in results
    ]
    audit = {
        **summary,
        "target_csv": str(args.target_csv),
        "target_csv_sha256": sha256_file(args.target_csv),
        "duration_sec": args.duration_sec,
        "book_max_age_ms": args.book_max_age_ms,
        "min_top_levels": args.min_top_levels,
        "chunk_audit": chunk_audit,
    }
    eval_payload = {
        **summary,
        "ok": not threshold_failures,
        "highest_allowed_status": STATUS_KEEP,
        "full_288_market_oos_pass": False,
        "scope_interpretation": "Only the reviewed 215 BOUND targets are evaluated; this cannot claim full 288-market OOS pass.",
    }
    write_json(paths["audit"], audit)
    write_json(paths["gate"], summary)
    write_json(paths["eval"], eval_payload)
    return 0 if not threshold_failures else 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-csv", type=Path, required=True)
    parser.add_argument("--expected-target-csv-sha256", default="")
    parser.add_argument("--expected-target-count", type=int, default=215)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=float, default=900.0)
    parser.add_argument("--warmup-sec", type=float, default=0.0)
    parser.add_argument("--require-live-fresh-after-warmup", action="store_true")
    parser.add_argument("--per-session-warmup", action="store_true")
    parser.add_argument("--allow-sequential-reconnects", action="store_true")
    parser.add_argument("--max-reconnects", type=int, default=0)
    parser.add_argument("--reconnect-backoff-sec", type=float, default=5.0)
    parser.add_argument("--book-max-age-ms", type=int, default=60_000)
    parser.add_argument("--min-top-levels", type=int, default=1)
    parser.add_argument("--max-ws-connections", type=int, default=1)
    parser.add_argument(
        "--target-timing-policy",
        choices=("future_not_started", "live_or_future_not_ended"),
        default="future_not_started",
        help=(
            "future_not_started preserves projection/OOS stale gating; "
            "live_or_future_not_ended is for rolling near-window research where the current live market is valid."
        ),
    )
    parser.add_argument("--ws-url", default=WS_URL)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(run_observer(args))


if __name__ == "__main__":
    raise SystemExit(main())

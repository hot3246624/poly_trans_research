#!/usr/bin/env python3
"""Bilateral maker-pair proxy backtest for BTC 5m.

This tests a different hypothesis from the one-sided completion-first scripts:
post maker bids on both outcomes, let public SELL flow decide which side fills
first, then complete only the opposite side. This is closer to a high
participation xuan-like market making structure.

Data rules:
- replay SQLite is opened read-only;
- raw capture is never read;
- winner_side is used only for ex-post residual settlement diagnostics.

Execution proxy:
- first and opposite maker fills are inferred from public taker SELL flow;
- queue position is approximated from L2 same-price bid size;
- fallback completion uses L1 opposite ask, so it is intentionally conservative
  versus a real L2 taker sweep.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def load_base_module() -> Any:
    path = Path(__file__).with_name("backtest_btc5m_maker_fill_triggered.py")
    spec = importlib.util.spec_from_file_location("backtest_btc5m_maker_fill_triggered_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base helper module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base_module()


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
    w = pos - lo
    return round(xs[lo] * (1.0 - w) + xs[hi] * w, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p10": percentile(xs, 10),
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
        "min": round(min(xs), 6) if xs else None,
        "max": round(max(xs), 6) if xs else None,
    }


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_csv_floats(value: str) -> list[float]:
    return [float(part.strip()) for part in value.split(",") if part.strip()]


def queue_required(
    *,
    l2_lookup: Any,
    sec: int,
    side: str,
    order_price: float,
    clip: float,
    queue_fraction: float,
    extra_required_size: float,
) -> tuple[float, float | None, float | None]:
    l2 = l2_lookup(sec, side)
    queue_same, _top_bid, top_bid_sz = base.l2_queue_same(l2, order_price)
    queue_same_v = float(queue_same or 0.0)
    return clip + queue_fraction * queue_same_v + extra_required_size, queue_same, top_bid_sz


def find_first_bilateral_fill(
    *,
    sells: dict[str, list[dict[str, Any]]],
    candidate_ms: int,
    end_ms: int,
    order_prices: dict[str, float],
    required_sizes: dict[str, float],
) -> tuple[str | None, dict[str, Any] | None, dict[str, dict[str, Any] | None]]:
    fills = {
        side: base.find_maker_fill(sells[side], candidate_ms, end_ms, order_prices[side], required_sizes[side])
        for side in ("YES", "NO")
    }
    available = [(side, fill) for side, fill in fills.items() if fill is not None]
    if not available:
        return None, None, fills
    available.sort(key=lambda item: (int(item[1]["fill_ts_ms"]), item[0]))
    return available[0][0], available[0][1], fills


def recent_sell_stats(trades: list[dict[str, Any]], start_ms: int, end_ms: int) -> tuple[int, float]:
    count = 0
    size = 0.0
    for trade in trades:
        ts = int(trade["ts_ms"])
        if ts < start_ms:
            continue
        if ts > end_ms:
            break
        count += 1
        size += float(trade["size"])
    return count, size


def find_l1_sell_exit(
    l1_by_sec: dict[int, dict[str, Any]],
    *,
    first_ms: int,
    start_s: int,
    deadline_s: int,
    market_end_ms: int,
    first_side: str,
    clip: float,
    first_price: float,
    max_loss: float,
    force_deadline: bool,
) -> dict[str, Any] | None:
    start_sec = math.floor(first_ms / 1000) + start_s
    end_sec = min(math.floor(market_end_ms / 1000), math.floor(first_ms / 1000) + deadline_s)
    min_exit_price = first_price - max_loss
    last_full: dict[str, Any] | None = None
    for sec in range(start_sec, end_sec + 1):
        book = l1_by_sec.get(sec)
        if not book:
            continue
        quote = base.side_quote(book, first_side)
        bid = quote["bid"]
        bid_sz = quote["bid_sz"]
        if bid is None or bid_sz is None:
            continue
        bid_f = float(bid)
        if float(bid_sz) + 1e-9 < clip:
            continue
        item = {
            "exit_ts_ms": sec * 1000,
            "exit_price": bid_f,
            "exit_delay_s": (sec * 1000 - first_ms) / 1000.0,
            "exit_pnl": (bid_f - first_price) * clip,
        }
        last_full = item
        if bid_f >= min_exit_price - 1e-9:
            item["exit_rule"] = "threshold"
            return item
    if force_deadline and last_full is not None:
        last_full["exit_rule"] = "deadline"
        return last_full
    return None


def simulate_market(
    conn: Any,
    market: Any,
    args: argparse.Namespace,
    *,
    clip: float,
    price_offset: float,
    queue_fraction: float,
) -> list[dict[str, Any]]:
    market_start_ms = int(market["start_ms"])
    market_end_ms = int(market["end_ms"])
    base_ms = max(market_start_ms, base.TRUSTED_START_MS)
    start_sec = base_ms // 1000
    stop_sec = market_end_ms // 1000 - args.tail_freeze_s
    max_follow_s = max(args.maker_completion_deadline_s, args.taker_deadline_s, args.repair_deadline_s)
    fetch_start_ms = max(market_start_ms, base_ms + (args.min_offset_s - 2) * 1000)
    fetch_end_ms = min(market_end_ms, base_ms + (args.max_offset_s + args.first_fill_timeout_s + max_follow_s + 2) * 1000)
    needs_l2 = queue_fraction > 0.0 or args.max_queue_same is not None or args.max_top_bid_sz is not None
    cache: dict[tuple[str, int, int, bool], tuple[dict[int, dict[str, Any]], dict[str, list[dict[str, Any]]], dict[tuple[int, str], dict[str, Any]]]] = getattr(
        args, "_bilateral_market_cache", {}
    )
    cache_key = (str(market["condition_id"]), fetch_start_ms, fetch_end_ms, needs_l2)
    if cache_key in cache:
        l1_by_sec, sells, l2_by_sec = cache[cache_key]
    else:
        l1_by_sec = base.latest_l1_by_second(conn, market["condition_id"], fetch_start_ms, fetch_end_ms)
        sells = base.sell_trades_by_side(conn, market["condition_id"], fetch_start_ms, fetch_end_ms)
        l2_by_sec = (
            base.latest_l2_by_second(conn, market["condition_id"], fetch_start_ms, fetch_end_ms) if needs_l2 else {}
        )
        cache[cache_key] = (l1_by_sec, sells, l2_by_sec)
        setattr(args, "_bilateral_market_cache", cache)

    def l2_lookup(sec: int, side: str) -> dict[str, Any] | None:
        return l2_by_sec.get((sec, side)) or l2_by_sec.get((sec - 1, side))

    rows: list[dict[str, Any]] = []
    cursor_sec = start_sec + args.min_offset_s
    for sec in range(start_sec + args.min_offset_s, min(start_sec + args.max_offset_s, stop_sec), args.sample_interval_s):
        if sec < cursor_sec:
            continue
        current = l1_by_sec.get(sec)
        if not current:
            continue
        quotes = {side: base.side_quote(current, side) for side in ("YES", "NO")}
        if any(quotes[side]["bid"] is None or quotes[side]["ask"] is None for side in ("YES", "NO")):
            continue
        order_prices = {side: max(0.0, float(quotes[side]["bid"]) - price_offset) for side in ("YES", "NO")}
        if any(order_prices[side] < args.min_order_price or order_prices[side] >= args.max_order_price for side in ("YES", "NO")):
            continue
        spreads = {
            side: round((float(quotes[side]["ask"]) - float(quotes[side]["bid"])) * 100.0, 6)
            for side in ("YES", "NO")
        }
        if max(spreads.values()) > args.max_spread_ticks:
            continue
        pair_bid_cost = order_prices["YES"] + order_prices["NO"]
        if pair_bid_cost > args.max_pair_bid_cost + 1e-9:
            continue
        if args.min_pair_bid_cost is not None and pair_bid_cost < args.min_pair_bid_cost - 1e-9:
            continue
        recent_sell_counts = {"YES": 0, "NO": 0}
        recent_sell_sizes = {"YES": 0.0, "NO": 0.0}
        if args.recent_sell_lookback_s > 0:
            recent_start_ms = sec * 1000 - args.recent_sell_lookback_s * 1000
            recent_end_ms = sec * 1000
            for side in ("YES", "NO"):
                recent_sell_counts[side], recent_sell_sizes[side] = recent_sell_stats(
                    sells[side], recent_start_ms, recent_end_ms
                )
            if min(recent_sell_counts.values()) < args.min_recent_sell_events_both:
                continue
            if min(recent_sell_sizes.values()) < args.min_recent_sell_size_both:
                continue

        required_sizes: dict[str, float] = {}
        queue_same: dict[str, float | None] = {}
        top_bid_sz: dict[str, float | None] = {}
        skip = False
        for side in ("YES", "NO"):
            required, same, top_sz = queue_required(
                l2_lookup=l2_lookup,
                sec=sec,
                side=side,
                order_price=order_prices[side],
                clip=clip,
                queue_fraction=queue_fraction,
                extra_required_size=args.extra_required_size,
            )
            if args.max_queue_same is not None and same is not None and float(same) > args.max_queue_same:
                skip = True
                break
            if args.max_top_bid_sz is not None and top_sz is not None and float(top_sz) > args.max_top_bid_sz:
                skip = True
                break
            required_sizes[side] = required
            queue_same[side] = same
            top_bid_sz[side] = top_sz
        if skip:
            continue

        candidate_ms = sec * 1000
        first_side, first_fill, initial_fills = find_first_bilateral_fill(
            sells=sells,
            candidate_ms=candidate_ms,
            end_ms=min(candidate_ms + args.first_fill_timeout_s * 1000, market_end_ms),
            order_prices=order_prices,
            required_sizes=required_sizes,
        )
        base_row = {
            "day": base.iso_ms(candidate_ms)[:10],
            "slug": market["slug"],
            "condition_id": market["condition_id"],
            "candidate_ts_ms": candidate_ms,
            "candidate_iso": base.iso_ms(candidate_ms),
            "offset_s": sec - (market_start_ms // 1000),
            "clip": clip,
            "price_offset": price_offset,
            "queue_fraction": queue_fraction,
            "yes_order_price": round(order_prices["YES"], 6),
            "no_order_price": round(order_prices["NO"], 6),
            "pair_bid_cost": round(pair_bid_cost, 6),
            "yes_bid": round(float(quotes["YES"]["bid"]), 6),
            "no_bid": round(float(quotes["NO"]["bid"]), 6),
            "yes_ask": round(float(quotes["YES"]["ask"]), 6),
            "no_ask": round(float(quotes["NO"]["ask"]), 6),
            "yes_spread_ticks": spreads["YES"],
            "no_spread_ticks": spreads["NO"],
            "yes_queue_same": None if queue_same["YES"] is None else round(float(queue_same["YES"]), 6),
            "no_queue_same": None if queue_same["NO"] is None else round(float(queue_same["NO"]), 6),
            "yes_top_bid_sz": top_bid_sz["YES"],
            "no_top_bid_sz": top_bid_sz["NO"],
            "yes_recent_sell_count": recent_sell_counts["YES"],
            "no_recent_sell_count": recent_sell_counts["NO"],
            "yes_recent_sell_size": round(recent_sell_sizes["YES"], 6),
            "no_recent_sell_size": round(recent_sell_sizes["NO"], 6),
            "yes_required_size": round(required_sizes["YES"], 6),
            "no_required_size": round(required_sizes["NO"], 6),
            "winner_side": market["winner_side"],
            "first_fill": first_fill is not None,
            "path": "no_first_fill",
            "pnl": 0.0,
        }
        if first_side is None or first_fill is None:
            rows.append(base_row)
            cursor_sec = sec + args.first_fill_timeout_s
            continue

        opp = base.other(first_side)
        first_ms = int(first_fill["fill_ts_ms"])
        first_price = order_prices[first_side]
        second_price = None
        completion_ts = None
        path = "residual_settle"
        pair_cost = None

        # The opposite order was resting from candidate time; if its public flow
        # proxy fills soon after the first leg, treat it as maker completion.
        maker_fill = initial_fills.get(opp)
        if maker_fill is None or int(maker_fill["fill_ts_ms"]) < first_ms:
            maker_fill = base.find_maker_fill(
                sells[opp],
                first_ms,
                min(first_ms + args.maker_completion_deadline_s * 1000, market_end_ms),
                order_prices[opp],
                required_sizes[opp],
            )
        if maker_fill is not None and int(maker_fill["fill_ts_ms"]) <= first_ms + args.maker_completion_deadline_s * 1000:
            completion_ts = int(maker_fill["fill_ts_ms"])
            second_price = order_prices[opp]
            pair_cost = first_price + second_price
            path = "maker_pair"
        else:
            taker_ts, taker_price = base.find_completion(
                l1_by_sec,
                first_ms,
                min(first_ms + args.taker_deadline_s * 1000, market_end_ms),
                first_side,
                clip,
                args.taker_pair_ceiling - first_price,
            )
            if taker_price is not None:
                completion_ts = taker_ts
                second_price = taker_price
                pair_cost = first_price + second_price
                path = "taker_pair"
            else:
                repair_ts, repair_price = base.find_completion(
                    l1_by_sec,
                    first_ms + args.taker_deadline_s * 1000,
                    min(first_ms + args.repair_deadline_s * 1000, market_end_ms),
                    first_side,
                    clip,
                    args.repair_pair_ceiling - first_price,
                )
                if repair_price is not None:
                    completion_ts = repair_ts
                    second_price = repair_price
                    pair_cost = first_price + second_price
                    path = "repair_pair"

        if pair_cost is not None:
            pnl = (1.0 - pair_cost) * clip
        elif args.residual_exit_mode != "none":
            exit_result = find_l1_sell_exit(
                l1_by_sec,
                first_ms=first_ms,
                start_s=args.residual_exit_start_s,
                deadline_s=args.residual_exit_deadline_s,
                market_end_ms=market_end_ms,
                first_side=first_side,
                clip=clip,
                first_price=first_price,
                max_loss=args.residual_exit_max_loss,
                force_deadline=args.residual_exit_mode == "deadline",
            )
            if exit_result is not None:
                completion_ts = int(exit_result["exit_ts_ms"])
                second_price = float(exit_result["exit_price"])
                path = "exit_first_leg"
                pnl = float(exit_result["exit_pnl"])
            elif first_side == market["winner_side"]:
                pnl = (1.0 - first_price) * clip
            else:
                pnl = -first_price * clip
        elif first_side == market["winner_side"]:
            pnl = (1.0 - first_price) * clip
        else:
            pnl = -first_price * clip

        rows.append(
            {
                **base_row,
                **first_fill,
                "first_fill": True,
                "first_side": first_side,
                "opposite_side": opp,
                "first_is_winner": first_side == market["winner_side"],
                "first_price": round(first_price, 6),
                "path": path,
                "completion_ts_ms": completion_ts,
                "completion_delay_s": None if completion_ts is None else round((completion_ts - first_ms) / 1000.0, 3),
                "second_price": None if second_price is None else round(float(second_price), 6),
                "pair_cost": None if pair_cost is None else round(float(pair_cost), 6),
                "exit_rule": None if path != "exit_first_leg" else exit_result.get("exit_rule") if "exit_result" in locals() and exit_result is not None else None,
                "pnl": round(float(pnl), 6),
            }
        )
        if path == "residual_settle":
            break
        cursor_sec = math.floor(int(completion_ts or first_ms) / 1000) + args.cooldown_s
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[tuple[float, float, float], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(float(row["clip"]), float(row["price_offset"]), float(row["queue_fraction"]))].append(row)
    out: dict[str, Any] = {}
    for (clip, offset, queue_fraction), xs in sorted(groups.items()):
        fills = [row for row in xs if row.get("first_fill") is True]
        paired = [row for row in fills if row.get("path") in {"maker_pair", "taker_pair", "repair_pair"}]
        exited = [row for row in fills if row.get("path") == "exit_first_leg"]
        maker = [row for row in fills if row.get("path") == "maker_pair"]
        taker = [row for row in fills if row.get("path") == "taker_pair"]
        repair = [row for row in fills if row.get("path") == "repair_pair"]
        residual = [row for row in fills if row.get("path") == "residual_settle"]
        spend = sum(float(row.get("first_price") or 0.0) * float(row["clip"]) for row in fills)
        pnl = sum(float(row.get("pnl") or 0.0) for row in fills)
        key = f"clip_{clip:g}__offset_{offset:g}__queue_{queue_fraction:g}"
        daily: dict[str, Any] = {}
        for day in sorted({str(row["day"]) for row in xs}):
            ds = [row for row in xs if str(row["day"]) == day]
            fs = [row for row in ds if row.get("first_fill") is True]
            day_spend = sum(float(row.get("first_price") or 0.0) * float(row["clip"]) for row in fs)
            day_pnl = sum(float(row.get("pnl") or 0.0) for row in fs)
            daily[day] = {
                "attempts": len(ds),
                "fills": len(fs),
                "paired": sum(1 for row in fs if row.get("path") in {"maker_pair", "taker_pair", "repair_pair"}),
                "exited": sum(1 for row in fs if row.get("path") == "exit_first_leg"),
                "residual": sum(1 for row in fs if row.get("path") == "residual_settle"),
                "pnl": round(day_pnl, 6),
                "roi_on_first_spend": rate(day_pnl, day_spend),
            }
        out[key] = {
            "attempts": len(xs),
            "fills": len(fills),
            "fill_rate": rate(len(fills), len(xs)),
            "paired": len(paired),
            "pair_rate_among_fills": rate(len(paired), len(fills)),
            "maker_pair": len(maker),
            "taker_pair": len(taker),
            "repair_pair": len(repair),
            "exit_first_leg": len(exited),
            "residual": len(residual),
            "first_winner_rate": rate(sum(1 for row in fills if row.get("first_is_winner") is True), len(fills)),
            "residual_winner_rate": rate(sum(1 for row in residual if row.get("first_is_winner") is True), len(residual)),
            "pnl": round(pnl, 6),
            "spend": round(spend, 6),
            "roi_on_first_spend": rate(pnl, spend),
            "pnl_per_attempt": rate(pnl, len(xs)),
            "pair_cost": summarize([row.get("pair_cost") for row in paired]),
            "pair_bid_cost": summarize([row.get("pair_bid_cost") for row in xs]),
            "fill_delay_s": summarize([row.get("fill_delay_s") for row in fills]),
            "completion_delay_s": summarize([row.get("completion_delay_s") for row in paired]),
            "daily": daily,
            "path_counts": {
                name: sum(1 for row in fills if row.get("path") == name)
                for name in ("maker_pair", "taker_pair", "repair_pair", "exit_first_leg", "residual_settle")
            },
        }
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Bilateral Maker-Pair Backtest",
        "",
        "## Scope",
        "",
        f"- days: `{','.join(report['days'])}`",
        "- Read-only replay SQLite; no raw.",
        "- Both YES and NO maker bids are assumed active at candidate time.",
        "- Public SELL flow is used as a fill proxy; own queue truth is unavailable.",
        "",
        "## Aggregate",
        "",
        "| mode | attempts | fills | fill rate | paired/fill | maker | taker | repair | exit | residual | first winner | pnl | ROI | pair p50 | completion p50 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["aggregate"].items():
        lines.append(
            f"| {key} | {item['attempts']} | {item['fills']} | {item['fill_rate']} | "
            f"{item['pair_rate_among_fills']} | {item['maker_pair']} | {item['taker_pair']} | "
            f"{item['repair_pair']} | {item['exit_first_leg']} | {item['residual']} | {item['first_winner_rate']} | "
            f"{item['pnl']} | {item['roi_on_first_spend']} | {item['pair_cost']['p50']} | "
            f"{item['completion_delay_s']['p50']} |"
        )
    lines.extend(["", "## Daily PnL", "", "| mode | day | attempts | fills | paired | exited | residual | pnl | ROI |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"])
    for key, item in report["aggregate"].items():
        for day, day_item in item["daily"].items():
            lines.append(
                f"| {key} | {day} | {day_item['attempts']} | {day_item['fills']} | "
                f"{day_item['paired']} | {day_item['exited']} | {day_item['residual']} | {day_item['pnl']} | "
                f"{day_item['roi_on_first_spend']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/backtest_btc5m_bilateral_maker_pair")
    parser.add_argument("--sample-interval-s", type=int, default=5)
    parser.add_argument("--clips", default="20,40,60")
    parser.add_argument("--price-offsets", default="0,0.01")
    parser.add_argument("--queue-fractions", default="0,0.5,1")
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--tail-freeze-s", type=int, default=30)
    parser.add_argument("--min-order-price", type=float, default=0.02)
    parser.add_argument("--max-order-price", type=float, default=0.98)
    parser.add_argument("--min-pair-bid-cost", type=float, default=None)
    parser.add_argument("--max-pair-bid-cost", type=float, default=0.99)
    parser.add_argument("--max-spread-ticks", type=float, default=3.0)
    parser.add_argument("--max-queue-same", type=float, default=None)
    parser.add_argument("--max-top-bid-sz", type=float, default=None)
    parser.add_argument("--extra-required-size", type=float, default=0.0)
    parser.add_argument("--recent-sell-lookback-s", type=int, default=0)
    parser.add_argument("--min-recent-sell-events-both", type=int, default=0)
    parser.add_argument("--min-recent-sell-size-both", type=float, default=0.0)
    parser.add_argument("--first-fill-timeout-s", type=int, default=20)
    parser.add_argument("--maker-completion-deadline-s", type=int, default=30)
    parser.add_argument("--taker-pair-ceiling", type=float, default=1.005)
    parser.add_argument("--taker-deadline-s", type=int, default=30)
    parser.add_argument("--repair-pair-ceiling", type=float, default=1.04)
    parser.add_argument("--repair-deadline-s", type=int, default=60)
    parser.add_argument("--residual-exit-mode", choices=("none", "threshold", "deadline"), default="none")
    parser.add_argument("--residual-exit-start-s", type=int, default=60)
    parser.add_argument("--residual-exit-deadline-s", type=int, default=75)
    parser.add_argument("--residual-exit-max-loss", type=float, default=0.04)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [day.strip() for day in args.days.split(",") if day.strip()]
    clips = parse_csv_floats(args.clips)
    offsets = parse_csv_floats(args.price_offsets)
    queue_fractions = parse_csv_floats(args.queue_fractions)
    rows: list[dict[str, Any]] = []
    coverage: list[dict[str, Any]] = []
    markets_seen = 0
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            coverage.append({"day": day, "status": "missing"})
            continue
        with base.ro_connect(db_path) as conn:
            markets = base.fetch_markets(conn, base.day_max_ms(conn))
            day_rows: list[dict[str, Any]] = []
            for market in markets:
                if args.max_markets and markets_seen >= args.max_markets:
                    break
                markets_seen += 1
                setattr(args, "_bilateral_market_cache", {})
                for clip in clips:
                    for offset in offsets:
                        for queue_fraction in queue_fractions:
                            day_rows.extend(
                                simulate_market(
                                    conn,
                                    market,
                                    args,
                                    clip=clip,
                                    price_offset=offset,
                                    queue_fraction=queue_fraction,
                                )
                            )
                setattr(args, "_bilateral_market_cache", {})
            rows.extend(day_rows)
            coverage.append({"day": day, "status": "ok", "markets": len(markets), "rows": len(day_rows)})
        if args.max_markets and markets_seen >= args.max_markets:
            break

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "parameters": {key: value for key, value in vars(args).items() if not key.startswith("_")},
        "coverage": coverage,
        "rows": len(rows),
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_bilateral_maker_pair_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_bilateral_maker_pair_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_bilateral_maker_pair_report.md").resolve()),
        },
    }
    write_csv(output_dir / "btc5m_bilateral_maker_pair_rows.csv", rows)
    (output_dir / "btc5m_bilateral_maker_pair_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_bilateral_maker_pair_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "markets_seen": markets_seen}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

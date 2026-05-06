#!/usr/bin/env python3
"""Fill-triggered maker proxy backtest for BTC 5m short-momentum candidates.

This upgrades the execution-discount sensitivity test:
- it does not assume bid/bid-1c/bid-2c fills;
- a first leg exists only after public SELL flow reaches the maker order price;
- optional queue-aware models require SELL volume to cover visible same-price
  queue before our clip;
- completion/repair then use future L1 opposite ask, and residuals settle.

This is still a proxy, not live truth. Public trades cannot fully reconstruct
our queue position, cancels, hidden liquidity, or partial fills.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


TRUSTED_START_MS = 1777274700000
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
TICK = 0.01


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


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
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
    }


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


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_quote(book: dict[str, Any], side: str) -> dict[str, float | None]:
    return book[side]


def fetch_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC' AND m.interval_sec = 300 AND m.end_ms > ?
        ORDER BY m.start_ms
        """,
        (TRUSTED_START_MS,),
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        if row["winner_side"] not in ("YES", "NO"):
            continue
        out.append(row)
    return out


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(recv_ms) FROM md_book_l1").fetchone()
    return None if row is None or row[0] is None else int(row[0])


def latest_l1_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms >= ? AND recv_ms <= ?
        ORDER BY recv_ms, capture_seq
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[int(row["recv_ms"]) // 1000] = {
            "recv_ms": int(row["recv_ms"]),
            "YES": {"bid": row["yes_bid_px"], "ask": row["yes_ask_px"], "bid_sz": row["yes_bid_sz"], "ask_sz": row["yes_ask_sz"]},
            "NO": {"bid": row["no_bid_px"], "ask": row["no_ask_px"], "bid_sz": row["no_bid_sz"], "ask_sz": row["no_ask_sz"]},
        }
    return out


def latest_l2_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[tuple[int, str], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz
        FROM md_book_l2
        WHERE condition_id = ? AND recv_ms >= ? AND recv_ms <= ?
          AND market_side IN ('YES', 'NO')
        ORDER BY recv_ms, id
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        levels = []
        for idx in range(1, 6):
            px = row[f"bid{idx}_px"]
            sz = row[f"bid{idx}_sz"]
            if px is None or sz is None or float(sz) <= 0:
                continue
            levels.append((float(px), float(sz)))
        if levels:
            out[(int(row["recv_ms"]) // 1000, str(row["market_side"]))] = {"recv_ms": int(row["recv_ms"]), "levels": levels}
    return out


def latest_l2_bid_for_side_at_second(
    conn: sqlite3.Connection,
    condition_id: str,
    sec: int,
    side: str,
) -> dict[str, Any] | None:
    start_ms = sec * 1000
    end_ms = start_ms + 999
    row = conn.execute(
        """
        SELECT recv_ms,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND market_side = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, id DESC
        LIMIT 1
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchone()
    if row is None:
        return None
    levels = []
    for idx in range(1, 6):
        px = row[f"bid{idx}_px"]
        sz = row[f"bid{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    if not levels:
        return None
    return {"recv_ms": int(row["recv_ms"]), "levels": levels}


def sell_trades_by_side(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND taker_side = 'SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    )
    out = {"YES": [], "NO": []}
    for row in rows:
        out[str(row["market_side"])].append({"ts_ms": int(row["trade_ts_ms"]), "price": float(row["price"]), "size": float(row["size"])})
    return out


def select_momentum_side(current: dict[str, Any], prev: dict[str, Any]) -> tuple[str | None, float | None]:
    best_side = None
    best_delta = None
    for side in ("YES", "NO"):
        cur_bid = side_quote(current, side)["bid"]
        prev_bid = side_quote(prev, side)["bid"]
        if cur_bid is None or prev_bid is None:
            continue
        delta = float(cur_bid) - float(prev_bid)
        if best_delta is None or delta > best_delta:
            best_side = side
            best_delta = delta
    return best_side, best_delta


def select_high_side(current: dict[str, Any], prev: dict[str, Any] | None) -> tuple[str | None, float | None]:
    mids: list[tuple[float, str]] = []
    for side in ("YES", "NO"):
        quote = side_quote(current, side)
        bid = quote["bid"]
        ask = quote["ask"]
        if bid is None or ask is None:
            continue
        mids.append(((float(bid) + float(ask)) / 2.0, side))
    if not mids:
        return None, None
    mids.sort(reverse=True)
    side = mids[0][1]
    prev_delta = None
    if prev is not None:
        cur_bid = side_quote(current, side)["bid"]
        prev_bid = side_quote(prev, side)["bid"]
        if cur_bid is not None and prev_bid is not None:
            prev_delta = float(cur_bid) - float(prev_bid)
    return side, prev_delta


def l2_queue_same(l2: dict[str, Any] | None, order_price: float) -> tuple[float | None, float | None, float | None]:
    if not l2:
        return None, None, None
    levels = l2["levels"]
    queue_same = sum(sz for px, sz in levels if abs(px - order_price) < TICK / 2)
    top_bid, top_sz = levels[0] if levels else (None, None)
    return queue_same, top_bid, top_sz


def l2_sweep_vwap(l2: dict[str, Any] | None, size: float) -> tuple[bool, float | None, float | None]:
    if not l2:
        return False, None, None
    remaining = size
    notional = 0.0
    filled = 0.0
    worst_px = None
    for px, sz in l2["levels"]:
        take = min(remaining, float(sz))
        if take <= 0:
            continue
        notional += float(px) * take
        filled += take
        remaining -= take
        worst_px = float(px)
        if remaining <= 1e-9:
            return True, notional / filled, worst_px
    return False, None, worst_px


def find_maker_fill(
    trades: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
    order_price: float,
    required_size: float,
) -> dict[str, Any] | None:
    lo = 0
    hi = len(trades)
    while lo < hi:
        mid = (lo + hi) // 2
        if trades[mid]["ts_ms"] < start_ms:
            lo = mid + 1
        else:
            hi = mid
    cum = 0.0
    first_reach_ms = None
    event_count = 0
    min_price = None
    for trade in trades[lo:]:
        if trade["ts_ms"] > end_ms:
            break
        if min_price is None or trade["price"] < min_price:
            min_price = trade["price"]
        if trade["price"] <= order_price + 1e-9:
            first_reach_ms = trade["ts_ms"] if first_reach_ms is None else first_reach_ms
            cum += trade["size"]
            event_count += 1
            if cum + 1e-9 >= required_size:
                return {
                    "fill_ts_ms": trade["ts_ms"],
                    "fill_delay_s": (trade["ts_ms"] - start_ms) / 1000.0,
                    "first_reach_delay_s": (first_reach_ms - start_ms) / 1000.0 if first_reach_ms is not None else None,
                    "sell_vol_le_order_until_fill": cum,
                    "fill_event_count": event_count,
                    "min_sell_price_before_fill": min_price,
                }
    return None


def find_completion(
    l1_by_sec: dict[int, dict[str, Any]],
    first_ms: int,
    end_ms: int,
    first_side: str,
    clip: float,
    max_opp_price: float,
) -> tuple[int | None, float | None]:
    opp = other(first_side)
    start_sec = math.floor(first_ms / 1000) + 1
    end_sec = math.floor(end_ms / 1000)
    for sec in range(start_sec, end_sec + 1):
        book = l1_by_sec.get(sec)
        if not book:
            continue
        quote = side_quote(book, opp)
        ask = quote["ask"]
        ask_sz = quote["ask_sz"]
        if ask is None or ask_sz is None:
            continue
        if float(ask) <= max_opp_price + 1e-9 and float(ask_sz) >= clip:
            return sec * 1000, float(ask)
    return None, None


def min_opposite_ask(
    l1_by_sec: dict[int, dict[str, Any]],
    first_ms: int,
    end_ms: int,
    first_side: str,
) -> float | None:
    opp = other(first_side)
    start_sec = math.floor(first_ms / 1000) + 1
    end_sec = math.floor(end_ms / 1000)
    best = None
    for sec in range(start_sec, end_sec + 1):
        book = l1_by_sec.get(sec)
        if not book:
            continue
        ask = side_quote(book, opp)["ask"]
        if ask is None:
            continue
        ask_f = float(ask)
        best = ask_f if best is None else min(best, ask_f)
    return best


def find_first_side_bid_exit(
    l1_by_sec: dict[int, dict[str, Any]],
    first_ms: int,
    market_end_ms: int,
    first_side: str,
    clip: float,
    first_price: float,
    start_s: int,
    deadline_s: int,
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
        quote = side_quote(book, first_side)
        bid = quote["bid"]
        bid_sz = quote["bid_sz"]
        if bid is None or bid_sz is None or float(bid_sz) + 1e-9 < clip:
            continue
        bid_f = float(bid)
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


def simulate_market_mode(
    market: sqlite3.Row,
    l1_by_sec: dict[int, dict[str, Any]],
    l2_lookup: Callable[[int, str], dict[str, Any] | None],
    sells: dict[str, list[dict[str, Any]]],
    price_offset: float,
    fill_model: str,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    start_sec = max(int(market["start_ms"]), TRUSTED_START_MS) // 1000
    end_sec = int(market["end_ms"]) // 1000
    stop_sec = end_sec - args.tail_freeze_s
    rows: list[dict[str, Any]] = []
    cursor_sec = start_sec + args.min_offset_s
    for sec in range(start_sec + args.min_offset_s, min(start_sec + args.max_offset_s, stop_sec), args.sample_interval_s):
        if not args.emit_all_candidates and sec < cursor_sec:
            continue
        current = l1_by_sec.get(sec)
        prev = l1_by_sec.get(sec - 1)
        if not current or not prev:
            continue
        if args.side_mode == "high_side":
            side, prev_delta = select_high_side(current, prev)
        elif args.side_mode == "momentum":
            side, prev_delta = select_momentum_side(current, prev)
        else:
            raise ValueError(f"unknown side mode: {args.side_mode}")
        if side is None:
            continue
        if args.min_prev_bid_delta_1s is not None:
            if prev_delta is None or prev_delta < args.min_prev_bid_delta_1s:
                continue
        quote = side_quote(current, side)
        bid = quote["bid"]
        ask = quote["ask"]
        if bid is None or ask is None:
            continue
        bid = float(bid)
        ask = float(ask)
        spread_ticks = round((ask - bid) * 100.0, 6)
        if bid < args.min_side_bid or bid >= args.max_side_bid or spread_ticks > args.max_spread_ticks:
            continue
        opp_quote = side_quote(current, other(side))
        opp_bid = opp_quote["bid"]
        opp_ask = opp_quote["ask"]
        opp_ask_sz = opp_quote["ask_sz"]
        if opp_bid is None or opp_ask is None:
            continue
        opp_bid = float(opp_bid)
        opp_ask = float(opp_ask)
        opp_spread_ticks = round((opp_ask - opp_bid) * 100.0, 6)
        if args.max_opp_spread_ticks is not None and opp_spread_ticks > args.max_opp_spread_ticks:
            continue
        if args.max_immediate_pair_cost is not None and bid + opp_ask > args.max_immediate_pair_cost:
            continue
        if args.min_opp_ask_sz is not None and (opp_ask_sz is None or float(opp_ask_sz) < args.min_opp_ask_sz):
            continue
        order_price = max(0.0, bid - price_offset)
        l2 = l2_lookup(sec, side)
        queue_same, top_bid, top_bid_sz = l2_queue_same(l2, order_price)
        first_l2_full_size, first_l2_vwap, first_l2_worst_px = l2_sweep_vwap(l2, args.clip)
        first_l2_edge = None if first_l2_vwap is None else first_l2_vwap - order_price
        if args.min_first_l2_edge is not None and (
            first_l2_edge is None or first_l2_edge < args.min_first_l2_edge
        ):
            continue
        if args.max_first_l2_edge is not None and (
            first_l2_edge is None or first_l2_edge > args.max_first_l2_edge
        ):
            continue
        if args.max_queue_same is not None and queue_same is not None and float(queue_same) > args.max_queue_same:
            continue
        if args.max_top_bid_sz is not None and top_bid_sz is not None and float(top_bid_sz) > args.max_top_bid_sz:
            continue
        queue_same_v = float(queue_same or 0.0)
        if fill_model == "optimistic":
            required_size = args.clip
        elif fill_model == "queue_half":
            required_size = args.clip + 0.5 * queue_same_v
        elif fill_model == "queue_full":
            required_size = args.clip + queue_same_v
        else:
            raise ValueError(f"unknown fill_model: {fill_model}")
        required_size += args.extra_required_size
        fill = find_maker_fill(
            sells[side],
            sec * 1000,
            min(sec * 1000 + args.first_fill_timeout_s * 1000, int(market["end_ms"])),
            order_price,
            required_size,
        )
        base = {
            "day": iso_ms(sec * 1000)[:10],
            "slug": market["slug"],
            "condition_id": market["condition_id"],
            "candidate_ts_ms": sec * 1000,
            "candidate_iso": iso_ms(sec * 1000),
            "offset_s": sec - (int(market["start_ms"]) // 1000),
            "price_offset": round(price_offset, 6),
            "fill_model": fill_model,
            "first_side": side,
            "side_mode": args.side_mode,
            "winner_side": market["winner_side"],
            "first_is_winner": side == market["winner_side"],
            "side_bid": round(bid, 6),
            "side_ask": round(ask, 6),
            "spread_ticks": spread_ticks,
            "opp_bid": round(opp_bid, 6),
            "opp_ask": round(opp_ask, 6),
            "opp_spread_ticks": opp_spread_ticks,
            "opp_ask_sz": None if opp_ask_sz is None else round(float(opp_ask_sz), 6),
            "immediate_pair_cost": round(order_price + opp_ask, 6),
            "prev_bid_delta_1s": round(prev_delta, 6),
            "order_price": round(order_price, 6),
            "clip": args.clip,
            "queue_same": None if queue_same is None else round(queue_same, 6),
            "top_bid": top_bid,
            "top_bid_sz": top_bid_sz,
            "first_l2_full_size": first_l2_full_size,
            "first_l2_vwap": None if first_l2_vwap is None else round(first_l2_vwap, 6),
            "first_l2_worst_px": None if first_l2_worst_px is None else round(first_l2_worst_px, 6),
            "first_l2_edge": None if first_l2_edge is None else round(first_l2_edge, 6),
            "required_size": round(required_size, 6),
            "first_fill": False,
            "path": "no_first_fill",
            "pnl": 0.0,
        }
        if fill is None:
            rows.append(base)
            if not args.emit_all_candidates:
                cursor_sec = sec + args.first_fill_timeout_s
            continue
        first_price = order_price
        completion_ts, completion_price = find_completion(
            l1_by_sec,
            int(fill["fill_ts_ms"]),
            min(int(fill["fill_ts_ms"]) + args.completion_deadline_s * 1000, int(market["end_ms"])),
            side,
            args.clip,
            args.completion_pair_ceiling - first_price,
        )
        path = "completion"
        exit_ts = completion_ts
        second_price = completion_price
        pair_cost = None
        min_pair_cost_seen_30s = None
        slow_continue_eligible = False
        if completion_price is not None:
            pair_cost = first_price + completion_price
            pnl = (1.0 - pair_cost) * args.clip
        else:
            if args.slow_continue_evidence_ceiling is not None:
                min_opp = min_opposite_ask(
                    l1_by_sec,
                    int(fill["fill_ts_ms"]),
                    min(int(fill["fill_ts_ms"]) + args.completion_deadline_s * 1000, int(market["end_ms"])),
                    side,
                )
                if min_opp is not None:
                    min_pair_cost_seen_30s = first_price + min_opp
                    slow_continue_eligible = min_pair_cost_seen_30s <= args.slow_continue_evidence_ceiling + 1e-9
            repair_ts = None
            repair_price = None
            if slow_continue_eligible:
                repair_ts, repair_price = find_completion(
                    l1_by_sec,
                    int(fill["fill_ts_ms"]) + args.completion_deadline_s * 1000,
                    min(int(fill["fill_ts_ms"]) + args.slow_completion_deadline_s * 1000, int(market["end_ms"])),
                    side,
                    args.clip,
                    args.slow_completion_pair_ceiling - first_price,
                )
                if repair_price is not None:
                    path = "slow_completion"
            if repair_price is None and not args.disable_repair:
                repair_ts, repair_price = find_completion(
                    l1_by_sec,
                    int(fill["fill_ts_ms"]) + args.completion_deadline_s * 1000,
                    min(int(fill["fill_ts_ms"]) + args.repair_deadline_s * 1000, int(market["end_ms"])),
                    side,
                    args.clip,
                    args.repair_pair_ceiling - first_price,
                )
                if repair_price is not None:
                    path = "repair"
            if repair_price is not None:
                exit_ts = repair_ts
                second_price = repair_price
                pair_cost = first_price + repair_price
                pnl = (1.0 - pair_cost) * args.clip
            else:
                exit_first = None
                if args.residual_exit_mode != "none":
                    exit_first = find_first_side_bid_exit(
                        l1_by_sec,
                        int(fill["fill_ts_ms"]),
                        int(market["end_ms"]),
                        side,
                        args.clip,
                        first_price,
                        args.residual_exit_start_s,
                        args.residual_exit_deadline_s,
                        args.residual_exit_max_loss,
                        args.residual_exit_mode == "deadline",
                    )
                if exit_first is not None:
                    path = "exit_first_leg"
                    exit_ts = int(exit_first["exit_ts_ms"])
                    second_price = float(exit_first["exit_price"])
                    pair_cost = None
                    pnl = float(exit_first["exit_pnl"])
                else:
                    path = "residual_settle"
                    exit_ts = None
                    second_price = None
                    pair_cost = None
                    pnl = (1.0 - first_price) * args.clip if side == market["winner_side"] else -first_price * args.clip
        rows.append(
            {
                **base,
                **fill,
                "first_fill": True,
                "first_price": round(first_price, 6),
                "path": path,
                "completion_ts_ms": exit_ts,
                "completion_delay_s": None if exit_ts is None else round((exit_ts - int(fill["fill_ts_ms"])) / 1000.0, 3),
                "second_price": None if second_price is None else round(second_price, 6),
                "pair_cost": None if pair_cost is None else round(pair_cost, 6),
                "min_pair_cost_seen_30s": None if min_pair_cost_seen_30s is None else round(float(min_pair_cost_seen_30s), 6),
                "slow_continue_eligible": slow_continue_eligible,
                "pnl": round(pnl, 6),
            }
        )
        if path == "residual_settle" and not args.emit_all_candidates:
            break
        if not args.emit_all_candidates:
            cursor_sec = math.floor(int(exit_ts or fill["fill_ts_ms"]) / 1000) + args.cooldown_s
    return rows


def build_rows_for_market(conn: sqlite3.Connection, market: sqlite3.Row, args: argparse.Namespace) -> list[dict[str, Any]]:
    market_start_ms = int(market["start_ms"])
    market_end_ms = int(market["end_ms"])
    base_ms = max(market_start_ms, TRUSTED_START_MS)
    max_follow_s = max(
        args.completion_deadline_s,
        args.slow_completion_deadline_s if args.slow_continue_evidence_ceiling is not None else 0,
        0 if args.disable_repair else args.repair_deadline_s,
    )
    candidate_end_ms = base_ms + (args.max_offset_s + args.first_fill_timeout_s + 2) * 1000
    l1_end_ms = min(market_end_ms, base_ms + (args.max_offset_s + args.first_fill_timeout_s + max_follow_s + 2) * 1000)
    fetch_start_ms = max(market_start_ms, base_ms + (args.min_offset_s - 2) * 1000)
    l1_by_sec = latest_l1_by_second(conn, market["condition_id"], fetch_start_ms, l1_end_ms)
    sells = sell_trades_by_side(conn, market["condition_id"], fetch_start_ms, min(market_end_ms, candidate_end_ms))
    l2_by_sec = latest_l2_by_second(conn, market["condition_id"], fetch_start_ms, l1_end_ms)
    l2_cache: dict[tuple[int, str], dict[str, Any] | None] = {}

    def l2_lookup(sec: int, side: str) -> dict[str, Any] | None:
        key = (sec, side)
        if key not in l2_cache:
            l2_cache[key] = l2_by_sec.get((sec, side)) or l2_by_sec.get((sec - 1, side))
        return l2_cache[key]

    rows: list[dict[str, Any]] = []
    price_offsets = [float(x) for x in args.price_offsets.split(",") if x.strip()]
    fill_models = [x.strip() for x in args.fill_models.split(",") if x.strip()]
    for price_offset in price_offsets:
        for fill_model in fill_models:
            rows.extend(simulate_market_mode(market, l1_by_sec, l2_lookup, sells, price_offset, fill_model, args))
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    groups: dict[tuple[float, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(float(row["price_offset"]), str(row["fill_model"]))].append(row)
    for (price_offset, fill_model), xs in sorted(groups.items()):
        fills = [r for r in xs if r.get("first_fill") is True]
        completed = [r for r in fills if r["path"] in ("completion", "slow_completion", "repair")]
        residual = [r for r in fills if r["path"] == "residual_settle"]
        spend = sum(float(r.get("first_price") or 0.0) * float(r["clip"]) for r in fills)
        pnl = sum(float(r.get("pnl") or 0.0) for r in fills)
        key = f"offset_{price_offset:g}__{fill_model}"
        item: dict[str, Any] = {
            "attempts": len(xs),
            "fills": len(fills),
            "completed": len(completed),
            "residual": len(residual),
            "fill_rate": rate(len(fills), len(xs)),
            "completion_rate_among_fills": rate(len(completed), len(fills)),
            "first_winner_rate_among_fills": rate(sum(1 for r in fills if r["first_is_winner"]), len(fills)),
            "residual_winner_rate": rate(sum(1 for r in residual if r["first_is_winner"]), len(residual)),
            "pnl": round(pnl, 6),
            "spend": round(spend, 6),
            "roi_on_filled_spend": rate(pnl, spend),
            "pnl_per_attempt": rate(pnl, len(xs)),
            "pair_cost": summarize([r.get("pair_cost") for r in completed]),
            "fill_delay_s": summarize([r.get("fill_delay_s") for r in fills]),
            "completion_delay_s": summarize([r.get("completion_delay_s") for r in completed]),
            "required_size": summarize([r.get("required_size") for r in xs]),
            "queue_same": summarize([r.get("queue_same") for r in xs]),
        }
        daily = {}
        for day in sorted({r["day"] for r in xs}):
            ds = [r for r in xs if r["day"] == day]
            fs = [r for r in ds if r.get("first_fill") is True]
            day_spend = sum(float(r.get("first_price") or 0.0) * float(r["clip"]) for r in fs)
            day_pnl = sum(float(r.get("pnl") or 0.0) for r in fs)
            daily[day] = {
                "attempts": len(ds),
                "fills": len(fs),
                "fill_rate": rate(len(fs), len(ds)),
                "pnl": round(day_pnl, 6),
                "roi_on_filled_spend": rate(day_pnl, day_spend),
            }
        item["daily"] = daily
        out[key] = item
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Fill-Triggered Maker Proxy Backtest",
        "",
        "## Scope",
        "",
        f"- days: `{','.join(report['days'])}`",
        "- First leg exists only after public SELL flow triggers maker fill proxy.",
        "- Completion/repair uses future L1 opposite ask. Residual settles by official outcome.",
        "- This remains a proxy; own queue truth is still required before enforce.",
        "",
        "## Aggregate",
        "",
        "| mode | attempts | fills | fill rate | completed | residual | first winner | residual winner | pnl | ROI filled | pnl/attempt | pair p50 | fill p50 s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["aggregate"].items():
        lines.append(
            f"| {key} | {item['attempts']} | {item['fills']} | {item['fill_rate']} | "
            f"{item['completed']} | {item['residual']} | {item['first_winner_rate_among_fills']} | "
            f"{item['residual_winner_rate']} | {item['pnl']} | {item['roi_on_filled_spend']} | "
            f"{item['pnl_per_attempt']} | {item['pair_cost']['p50']} | {item['fill_delay_s']['p50']} |"
        )
    lines.extend(["", "## Daily PnL", "", "| mode | day | attempts | fills | fill rate | pnl | ROI filled |", "|---|---|---:|---:|---:|---:|---:|"])
    for key, item in report["aggregate"].items():
        for day, day_item in item["daily"].items():
            lines.append(
                f"| {key} | {day} | {day_item['attempts']} | {day_item['fills']} | "
                f"{day_item['fill_rate']} | {day_item['pnl']} | {day_item['roi_on_filled_spend']} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/backtest_btc5m_maker_fill_triggered")
    parser.add_argument("--sample-interval-s", type=int, default=1)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--min-offset-s", type=int, default=30)
    parser.add_argument("--max-offset-s", type=int, default=60)
    parser.add_argument("--tail-freeze-s", type=int, default=60)
    parser.add_argument("--min-side-bid", type=float, default=0.40)
    parser.add_argument("--max-side-bid", type=float, default=0.55)
    parser.add_argument("--side-mode", choices=("momentum", "high_side"), default="momentum")
    parser.add_argument("--max-spread-ticks", type=float, default=1.0)
    parser.add_argument("--max-opp-spread-ticks", type=float, default=None)
    parser.add_argument("--max-immediate-pair-cost", type=float, default=None)
    parser.add_argument("--min-opp-ask-sz", type=float, default=None)
    parser.add_argument("--min-prev-bid-delta-1s", type=float, default=0.04)
    parser.add_argument("--max-queue-same", type=float, default=None)
    parser.add_argument("--max-top-bid-sz", type=float, default=None)
    parser.add_argument("--min-first-l2-edge", type=float, default=None)
    parser.add_argument("--max-first-l2-edge", type=float, default=None)
    parser.add_argument("--price-offsets", default="0,0.01,0.02")
    parser.add_argument("--fill-models", default="optimistic,queue_full")
    parser.add_argument("--extra-required-size", type=float, default=0.0)
    parser.add_argument("--first-fill-timeout-s", type=int, default=30)
    parser.add_argument("--completion-pair-ceiling", type=float, default=0.95)
    parser.add_argument("--completion-deadline-s", type=int, default=30)
    parser.add_argument("--slow-continue-evidence-ceiling", type=float, default=None)
    parser.add_argument("--slow-completion-pair-ceiling", type=float, default=0.95)
    parser.add_argument("--slow-completion-deadline-s", type=int, default=120)
    parser.add_argument("--repair-pair-ceiling", type=float, default=1.04)
    parser.add_argument("--repair-deadline-s", type=int, default=60)
    parser.add_argument("--disable-repair", action="store_true")
    parser.add_argument("--residual-exit-mode", choices=("none", "threshold", "deadline"), default="none")
    parser.add_argument("--residual-exit-start-s", type=int, default=30)
    parser.add_argument("--residual-exit-deadline-s", type=int, default=120)
    parser.add_argument("--residual-exit-max-loss", type=float, default=0.08)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--emit-all-candidates", action="store_true")
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    rows: list[dict[str, Any]] = []
    coverage = []
    markets_seen = 0
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            coverage.append({"day": day, "status": "missing"})
            continue
        with ro_connect(db_path) as conn:
            markets = fetch_markets(conn, day_max_ms(conn))
            day_rows = []
            for market in markets:
                if args.max_markets and markets_seen >= args.max_markets:
                    break
                markets_seen += 1
                day_rows.extend(build_rows_for_market(conn, market, args))
            rows.extend(day_rows)
            coverage.append({"day": day, "status": "ok", "markets": len(markets), "rows": len(day_rows)})
        if args.max_markets and markets_seen >= args.max_markets:
            break

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "parameters": vars(args),
        "coverage": coverage,
        "rows": len(rows),
        "aggregate": aggregate(rows),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_maker_fill_triggered_rows.csv", rows)
    (output_dir / "btc5m_maker_fill_triggered_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_maker_fill_triggered_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "markets_seen": markets_seen}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""BTC 5m two-sided maker-grid market-level backtest.

Research purpose: test the hypothesis that xuan's core edge is not a single
completion-first tranche, but repeatedly buying both YES and NO at maker bids
when the visible bid pair sum is below 1, while bounding inventory imbalance.

Execution proxy:
- At sampled L1 times, post maker BUY bids on eligible YES/NO sides at current
  best bid.
- A bid is considered filled if public taker SELL flow on that outcome reaches
  clip size before order_timeout_s.
- PnL is market-level: accumulated position valued by settlement winner.

This reads replay SQLite read-only and never reads raw data.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class L1Book:
    recv_ms: int
    yes_bid_px: float | None
    yes_ask_px: float | None
    no_bid_px: float | None
    no_ask_px: float | None
    yes_bid_sz: float | None
    no_bid_sz: float | None


@dataclass(frozen=True)
class Trade:
    ts_ms: int
    side: str
    price: float
    size: float


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(values)
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


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_bid(book: L1Book, side: str) -> float | None:
    return book.yes_bid_px if side == "YES" else book.no_bid_px


def side_bid_size(book: L1Book, side: str) -> float | None:
    return book.yes_bid_sz if side == "YES" else book.no_bid_sz


def bid_pair_sum(book: L1Book) -> float | None:
    if book.yes_bid_px is None or book.no_bid_px is None:
        return None
    return float(book.yes_bid_px) + float(book.no_bid_px)


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT MAX(x) FROM (
          SELECT MAX(recv_ms) AS x FROM md_book_l1
          UNION ALL
          SELECT MAX(trade_ts_ms) AS x FROM md_trades WHERE trade_ts_ms IS NOT NULL
        )
        """
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def load_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol='BTC' AND m.interval_sec=300
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if PLANNED_OUTAGE_START_MS <= start_ms < PLANNED_OUTAGE_END_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        out.append(row)
    return out


def load_l1_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[L1Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px, yes_bid_sz, no_bid_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms, capture_seq
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    return [
        L1Book(
            recv_ms=int(row["recv_ms"]),
            yes_bid_px=row["yes_bid_px"],
            yes_ask_px=row["yes_ask_px"],
            no_bid_px=row["no_bid_px"],
            no_ask_px=row["no_ask_px"],
            yes_bid_sz=row["yes_bid_sz"],
            no_bid_sz=row["no_bid_sz"],
        )
        for row in rows
    ]


def load_sell_trades(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[Trade]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms < ?
          AND taker_side='SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        side = str(row["market_side"])
        out[side].append(Trade(ts_ms=int(row["trade_ts_ms"]), side=side, price=float(row["price"]), size=float(row["size"])))
    return out


def sample_indices(books: list[L1Book], start_ms: int, interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + interval_ms
    return out


def sell_flow_fill(
    trades: list[Trade],
    times: list[int],
    start_ms: int,
    end_ms: int,
    max_price: float,
    target_size: float,
) -> tuple[int, int, float] | None:
    idx = bisect.bisect_left(times, start_ms)
    filled = 0.0
    events = 0
    while idx < len(trades):
        trade = trades[idx]
        if trade.ts_ms > end_ms:
            return None
        if trade.price <= max_price:
            use = min(trade.size, target_size - filled)
            filled += use
            events += 1
            if filled + 1e-9 >= target_size:
                return trade.ts_ms, events, filled
        idx += 1
    return None


def first_sell_flow_fill(
    trades: list[Trade],
    times: list[int],
    start_ms: int,
    end_ms: int,
    max_price: float,
    target_size: float,
) -> tuple[int, int, float, float] | None:
    idx = bisect.bisect_left(times, start_ms)
    filled = 0.0
    notional = 0.0
    events = 0
    while idx < len(trades):
        trade = trades[idx]
        if trade.ts_ms > end_ms:
            return None
        if trade.price <= max_price:
            use = min(trade.size, target_size - filled)
            filled += use
            notional += use * trade.price
            events += 1
            if filled + 1e-9 >= target_size:
                return trade.ts_ms, events, filled, notional / filled
        idx += 1
    return None


def simulate_pair_gated_market(
    market: sqlite3.Row,
    books: list[L1Book],
    sells: dict[str, list[Trade]],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    stop_ms = end_ms - args.tail_freeze_s * 1000
    samples = sample_indices(books, start_ms + args.min_offset_s * 1000, int(args.sample_interval_s * 1000))
    sell_times = {side: [trade.ts_ms for trade in xs] for side, xs in sells.items()}
    qty = {"YES": 0.0, "NO": 0.0}
    cost = {"YES": 0.0, "NO": 0.0}
    fills: list[dict[str, Any]] = []
    cursor_ms = start_ms + args.min_offset_s * 1000
    for idx in samples:
        book = books[idx]
        if book.recv_ms < cursor_ms:
            continue
        if book.recv_ms >= stop_ms or book.recv_ms >= start_ms + args.max_offset_s * 1000:
            break
        pair_sum = bid_pair_sum(book)
        if pair_sum is None or pair_sum > args.max_bid_pair_sum:
            cursor_ms = book.recv_ms + int(args.sample_interval_s * 1000)
            continue

        first_candidates = []
        for side in ("YES", "NO"):
            bid = side_bid(book, side)
            if bid is None or bid < args.min_bid_price or bid > args.max_bid_price:
                continue
            queue_ahead = max(float(side_bid_size(book, side) or 0.0) * args.queue_ahead_fraction, 0.0)
            flow_required = args.clip_size + queue_ahead
            fill = first_sell_flow_fill(
                sells[side],
                sell_times[side],
                book.recv_ms,
                min(book.recv_ms + args.order_timeout_s * 1000, stop_ms),
                float(bid),
                flow_required,
            )
            if fill is None:
                continue
            fill_ts, event_count, _filled, proxy_vwap = fill
            first_candidates.append((fill_ts, side, float(bid), event_count, queue_ahead, flow_required, proxy_vwap))
        if not first_candidates:
            cursor_ms = book.recv_ms + args.order_timeout_s * 1000
            continue

        first_ts, first_side, first_price, first_events, first_queue, first_flow_required, first_proxy_vwap = min(
            first_candidates, key=lambda item: (item[0], item[1])
        )
        opposite = other(first_side)
        qty[first_side] += args.clip_size
        cost[first_side] += args.clip_size * first_price
        fills.append(
            {
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "round_start_ms": start_ms,
                "round_start_iso": iso_ms(start_ms),
                "candidate_ts_ms": book.recv_ms,
                "candidate_offset_s": round((book.recv_ms - start_ms) / 1000.0, 3),
                "fill_ts_ms": first_ts,
                "fill_delay_s": round((first_ts - book.recv_ms) / 1000.0, 3),
                "side": first_side,
                "price": round(first_price, 6),
                "clip_size": args.clip_size,
                "role": "first",
                "bid_pair_sum": round(pair_sum, 6),
                "queue_ahead": round(first_queue, 6),
                "flow_required": round(first_flow_required, 6),
                "event_count": first_events,
                "proxy_vwap": round(first_proxy_vwap, 6),
            }
        )

        completion_ceiling = args.pair_target - first_price
        if completion_ceiling < args.min_bid_price:
            break
        completion = first_sell_flow_fill(
            sells[opposite],
            sell_times[opposite],
            first_ts,
            min(first_ts + args.completion_timeout_s * 1000, stop_ms),
            completion_ceiling,
            args.clip_size,
        )
        if completion is None:
            break
        completion_ts, completion_events, _filled, completion_vwap = completion
        completion_price = completion_vwap if args.use_trade_vwap_for_completion else completion_ceiling
        qty[opposite] += args.clip_size
        cost[opposite] += args.clip_size * completion_price
        fills.append(
            {
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "round_start_ms": start_ms,
                "round_start_iso": iso_ms(start_ms),
                "candidate_ts_ms": first_ts,
                "candidate_offset_s": round((first_ts - start_ms) / 1000.0, 3),
                "fill_ts_ms": completion_ts,
                "fill_delay_s": round((completion_ts - first_ts) / 1000.0, 3),
                "side": opposite,
                "price": round(completion_price, 6),
                "clip_size": args.clip_size,
                "role": "completion",
                "pair_target": args.pair_target,
                "first_price": round(first_price, 6),
                "event_count": completion_events,
                "proxy_vwap": round(completion_vwap, 6),
            }
        )
        cursor_ms = completion_ts + args.cooldown_s * 1000

    return finalize_market_row(market, qty, cost, fills)


def finalize_market_row(
    market: sqlite3.Row,
    qty: dict[str, float],
    cost: dict[str, float],
    fills: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start_ms = int(market["start_ms"])
    yes_qty = qty["YES"]
    no_qty = qty["NO"]
    yes_avg = cost["YES"] / yes_qty if yes_qty > 0 else None
    no_avg = cost["NO"] / no_qty if no_qty > 0 else None
    paired_qty = min(yes_qty, no_qty)
    weighted_pair_cost = yes_avg + no_avg if paired_qty > 0 and yes_avg is not None and no_avg is not None else None
    paired_profit = paired_qty * (1.0 - weighted_pair_cost) if weighted_pair_cost is not None else 0.0
    total_cost = cost["YES"] + cost["NO"]
    winner = market["winner_side"]
    if winner == "YES":
        total_value = yes_qty
    elif winner == "NO":
        total_value = no_qty
    else:
        total_value = 0.0
    residual_side = None
    residual_qty = abs(yes_qty - no_qty)
    residual_pnl = 0.0
    if residual_qty > 1e-9:
        residual_side = "YES" if yes_qty > no_qty else "NO"
        residual_avg = yes_avg if residual_side == "YES" else no_avg
        residual_value = 1.0 if residual_side == winner else 0.0
        residual_pnl = residual_qty * (residual_value - float(residual_avg or 0.0))
    row = {
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_ms": start_ms,
        "round_start_iso": iso_ms(start_ms),
        "winner_side": winner,
        "fill_count": len(fills),
        "yes_qty": round(yes_qty, 6),
        "no_qty": round(no_qty, 6),
        "yes_cost": round(cost["YES"], 6),
        "no_cost": round(cost["NO"], 6),
        "yes_avg": round(yes_avg, 6) if yes_avg is not None else None,
        "no_avg": round(no_avg, 6) if no_avg is not None else None,
        "paired_qty": round(paired_qty, 6),
        "weighted_pair_cost": round(weighted_pair_cost, 6) if weighted_pair_cost is not None else None,
        "paired_profit": round(paired_profit, 6),
        "residual_side": residual_side,
        "residual_qty": round(residual_qty, 6),
        "residual_is_winner": residual_side == winner if residual_side and winner in {"YES", "NO"} else None,
        "residual_pnl": round(residual_pnl, 6),
        "total_cost": round(total_cost, 6),
        "total_value": round(total_value, 6),
        "trade_pnl": round(total_value - total_cost, 6),
        "roi_on_cost": round((total_value - total_cost) / total_cost, 6) if total_cost > 0 else None,
    }
    return row, fills


def simulate_market(market: sqlite3.Row, books: list[L1Book], sells: dict[str, list[Trade]], args: argparse.Namespace) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    stop_ms = end_ms - args.tail_freeze_s * 1000
    samples = sample_indices(books, start_ms + args.min_offset_s * 1000, int(args.sample_interval_s * 1000))
    sell_times = {side: [trade.ts_ms for trade in xs] for side, xs in sells.items()}
    qty = {"YES": 0.0, "NO": 0.0}
    cost = {"YES": 0.0, "NO": 0.0}
    fills: list[dict[str, Any]] = []
    cursor_ms = start_ms + args.min_offset_s * 1000
    for idx in samples:
        book = books[idx]
        if book.recv_ms < cursor_ms:
            continue
        if book.recv_ms >= stop_ms or book.recv_ms >= start_ms + args.max_offset_s * 1000:
            break
        pair_sum = bid_pair_sum(book)
        if pair_sum is None or pair_sum > args.max_bid_pair_sum:
            cursor_ms = book.recv_ms + int(args.sample_interval_s * 1000)
            continue
        filled_any = False
        # Quote both sides, but suppress the side that would deepen an existing imbalance.
        for side in ("YES", "NO"):
            bid = side_bid(book, side)
            if bid is None or bid < args.min_bid_price or bid > args.max_bid_price:
                continue
            side_imbalance = qty[side] - qty[other(side)]
            if side_imbalance >= args.max_side_imbalance:
                continue
            queue_ahead = max(float(side_bid_size(book, side) or 0.0) * args.queue_ahead_fraction, 0.0)
            flow_required = args.clip_size + queue_ahead
            fill = sell_flow_fill(
                sells[side],
                sell_times[side],
                book.recv_ms,
                min(book.recv_ms + args.order_timeout_s * 1000, stop_ms),
                float(bid),
                flow_required,
            )
            if fill is None:
                continue
            fill_ts, event_count, _filled = fill
            qty[side] += args.clip_size
            cost[side] += args.clip_size * float(bid)
            filled_any = True
            fills.append(
                {
                    "slug": market["slug"],
                    "condition_id": market["condition_id"],
                    "round_start_ms": start_ms,
                    "round_start_iso": iso_ms(start_ms),
                    "candidate_ts_ms": book.recv_ms,
                    "candidate_offset_s": round((book.recv_ms - start_ms) / 1000.0, 3),
                    "fill_ts_ms": fill_ts,
                    "fill_delay_s": round((fill_ts - book.recv_ms) / 1000.0, 3),
                    "side": side,
                    "price": round(float(bid), 6),
                    "clip_size": args.clip_size,
                    "bid_pair_sum": round(pair_sum, 6),
                    "queue_ahead": round(queue_ahead, 6),
                    "flow_required": round(flow_required, 6),
                    "event_count": event_count,
                }
            )
        if filled_any:
            cursor_ms = book.recv_ms + args.cooldown_s * 1000
        else:
            cursor_ms = book.recv_ms + args.order_timeout_s * 1000

    return finalize_market_row(market, qty, cost, fills)


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    active = [row for row in rows if float(row.get("total_cost") or 0.0) > 0]
    total_cost = sum(float(row.get("total_cost") or 0.0) for row in active)
    total_value = sum(float(row.get("total_value") or 0.0) for row in active)
    paired_qty = sum(float(row.get("paired_qty") or 0.0) for row in active)
    pair_notional = sum(
        float(row.get("paired_qty") or 0.0) * float(row.get("weighted_pair_cost") or 0.0)
        for row in active
        if row.get("weighted_pair_cost") not in (None, "")
    )
    profitable = [row for row in active if float(row.get("trade_pnl") or 0.0) > 0]
    residual = [row for row in active if float(row.get("residual_qty") or 0.0) > 1e-9]
    residual_winner = [row for row in residual if row.get("residual_is_winner") is True]
    return {
        "market_count": len(rows),
        "active_market_count": len(active),
        "active_market_rate": rate(len(active), len(rows)),
        "profitable_market_count": len(profitable),
        "profitable_market_rate": rate(len(profitable), len(active)),
        "fill_count": sum(int(row.get("fill_count") or 0) for row in active),
        "total_cost": round(total_cost, 6),
        "total_value": round(total_value, 6),
        "trade_pnl": round(total_value - total_cost, 6),
        "roi_on_cost": round((total_value - total_cost) / total_cost, 6) if total_cost > 0 else None,
        "paired_qty": round(paired_qty, 6),
        "weighted_pair_cost": round(pair_notional / paired_qty, 6) if paired_qty > 0 else None,
        "paired_profit": round(sum(float(row.get("paired_profit") or 0.0) for row in active), 6),
        "residual_pnl": round(sum(float(row.get("residual_pnl") or 0.0) for row in active), 6),
        "residual_market_count": len(residual),
        "residual_is_winner_rate": rate(len(residual_winner), len(residual)),
        "per_market_pnl": summarize([float(row.get("trade_pnl") or 0.0) for row in active]),
        "per_market_pair_cost": summarize([row.get("weighted_pair_cost") for row in active if row.get("weighted_pair_cost") not in (None, "")]),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    s = report["summary"]
    lines = [
        "# BTC 5m Two-Sided Maker Grid Backtest",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        "- Read-only replay SQLite. Fill proxy uses public taker SELL flow.",
        "- This is not own execution truth; queue priority must be validated by dry-run/live shadow.",
        "",
        "## Summary",
        "",
        f"- markets / active: `{s['market_count']}` / `{s['active_market_count']}`",
        f"- fills: `{s['fill_count']}`",
        f"- cost / value: `${s['total_cost']}` / `${s['total_value']}`",
        f"- trade PnL: `${s['trade_pnl']}`",
        f"- ROI: `{s['roi_on_cost']}`",
        f"- weighted pair cost: `{s['weighted_pair_cost']}`",
        f"- paired profit / residual PnL: `${s['paired_profit']}` / `${s['residual_pnl']}`",
        f"- residual-is-winner rate: `{s['residual_is_winner_rate']}`",
        "",
        "## By Day",
        "",
        "| day | active | fills | cost | pnl | roi | pair cost | paired profit | residual pnl |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for day, item in report["by_day"].items():
        lines.append(
            f"| {day} | {item['active_market_count']} | {item['fill_count']} | {item['total_cost']} | "
            f"{item['trade_pnl']} | {item['roi_on_cost']} | {item['weighted_pair_cost']} | "
            f"{item['paired_profit']} | {item['residual_pnl']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", default="data/exports/btc5m_two_sided_maker_grid")
    parser.add_argument("--clip-size", type=float, default=60.0)
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--order-timeout-s", type=int, default=5)
    parser.add_argument("--cooldown-s", type=int, default=0)
    parser.add_argument("--queue-ahead-fraction", type=float, default=0.0)
    parser.add_argument("--max-bid-pair-sum", type=float, default=0.99)
    parser.add_argument("--pair-gated", action="store_true")
    parser.add_argument("--pair-target", type=float, default=0.98)
    parser.add_argument("--completion-timeout-s", type=int, default=30)
    parser.add_argument("--use-trade-vwap-for-completion", action="store_true")
    parser.add_argument("--max-side-imbalance", type=float, default=180.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--tail-freeze-s", type=int, default=60)
    parser.add_argument("--min-bid-price", type=float, default=0.05)
    parser.add_argument("--max-bid-price", type=float, default=0.95)
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [day.strip() for day in args.days.split(",") if day.strip()]
    market_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    db_summaries = []
    markets_seen = 0
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            db_summaries.append({"day": day, "exists": False, "markets": 0})
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            db_summaries.append({"day": day, "exists": True, "markets": len(markets)})
            for market in markets:
                if args.max_markets and markets_seen >= args.max_markets:
                    break
                markets_seen += 1
                start_ms = int(market["start_ms"]) + args.min_offset_s * 1000
                end_ms = min(int(market["start_ms"]) + args.max_offset_s * 1000 + args.order_timeout_s * 1000, int(market["end_ms"]))
                books = load_l1_books(conn, market["condition_id"], start_ms, end_ms)
                if not books:
                    continue
                sells = load_sell_trades(conn, market["condition_id"], start_ms, int(market["end_ms"]))
                if args.pair_gated:
                    row, fills = simulate_pair_gated_market(market, books, sells, args)
                else:
                    row, fills = simulate_market(market, books, sells, args)
                row["day"] = day
                for fill in fills:
                    fill["day"] = day
                market_rows.append(row)
                fill_rows.extend(fills)
            if args.max_markets and markets_seen >= args.max_markets:
                break
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_two_sided_maker_grid_markets.csv", market_rows)
    write_csv(output_dir / "btc5m_two_sided_maker_grid_fills.csv", fill_rows)
    by_day = {day: compact([row for row in market_rows if row.get("day") == day]) for day in days}
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "parameters": vars(args),
        "db_summaries": db_summaries,
        "summary": compact(market_rows),
        "by_day": by_day,
        "outputs": {
            "markets_csv": str((output_dir / "btc5m_two_sided_maker_grid_markets.csv").resolve()),
            "fills_csv": str((output_dir / "btc5m_two_sided_maker_grid_fills.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_two_sided_maker_grid_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_two_sided_maker_grid_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_two_sided_maker_grid_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "btc5m_two_sided_maker_grid_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "markets": len(market_rows), "fills": len(fill_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

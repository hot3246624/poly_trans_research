#!/usr/bin/env python3
"""BTC 5m market-side PGT replay backtest over poly_trans_research SQLite DBs.

This is a public-market replay only. It never writes to SQLite and it does not
claim exact queue position, xuan episode truth, or own execution truth.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence


REPLAY_ROOT = Path("/Users/hot/web3Scientist/poly_trans_research/data/replay")
DEFAULT_DATES = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_274_700_000
OUTAGE_START_MS = 1_777_374_000_000
OUTAGE_END_MS = 1_777_377_600_000
POSITIVE_EDGE = 0.005
DEFAULT_CLIP_QTY = 57.6
SEED_CUTOFF_SEC = 150
COMPLETION_WINDOW_MS = 30_000
BREAKEVEN_UNLOCK_AGE_MS = 60_000
BREAKEVEN_UNLOCK_REMAINING_MS = 90_000


@dataclass
class BookRow:
    ts_ms: int
    yes_bid: float
    yes_ask: float
    no_bid: float
    no_ask: float
    yes_bid_sz: float
    yes_ask_sz: float
    no_bid_sz: float
    no_ask_sz: float


@dataclass
class TradeRow:
    ts_ms: int
    side: str
    taker_side: str
    price: float
    size: float


@dataclass
class SeedCandidate:
    side: str
    place_ts_ms: int
    price: float
    bid_size: float
    opp_ask_at_place: float


@dataclass
class FillProxy:
    side: str
    ts_ms: int
    price: float
    qty: float
    model: str


@dataclass
class RoundResult:
    db_date: str
    slug: str
    condition_id: str
    start_ms: int
    end_ms: int
    official_outcome: str | None
    excluded_reason: str | None
    book_rows: int
    trade_rows: int
    first_book_ms: int | None
    last_book_ms: int | None
    first_trade_ms: int | None
    last_trade_ms: int | None
    seed_yes_eligible: int
    seed_no_eligible: int
    seed_yes_price: float | None
    seed_no_price: float | None
    first_leg_side: str | None
    first_leg_ts_ms: int | None
    first_leg_offset_ms: int | None
    first_leg_price: float | None
    first_leg_qty: float
    first_leg_touch_side: str | None
    first_leg_touch_ts_ms: int | None
    first_leg_touch_qty: float
    positive_visible_delay_ms: int | None
    breakeven_visible_delay_ms: int | None
    completion30_positive_visible: int
    completion30_breakeven_visible: int
    completion30_maker_strict: int
    completion_strict_ts_ms: int | None
    completion_strict_delay_ms: int | None
    completion_strict_price_vwap: float | None
    completion_strict_qty: float
    pair_cost_strict: float | None
    pair_cost_30s_positive_visible: float | None
    pair_cost_30s_breakeven_visible: float | None
    residual_qty_strict: float
    net_diff_strict: float
    status: str


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--replay-root", default=str(REPLAY_ROOT))
    p.add_argument("--date", action="append", default=None, help="UTC date, repeatable")
    p.add_argument("--clip-qty", type=float, default=DEFAULT_CLIP_QTY)
    p.add_argument("--output-dir", default="data/exports/pgt_market_side_backtest")
    p.add_argument("--include-outage", action="store_true")
    p.add_argument("--include-pre-trusted", action="store_true")
    p.add_argument("--max-rounds", type=int, default=0)
    return p.parse_args()


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).isoformat()


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def overlaps(a0: int, a1: int, b0: int, b1: int) -> bool:
    return a0 < b1 and b0 < a1


def side_bid(book: BookRow, side: str) -> float:
    return book.yes_bid if side == "YES" else book.no_bid


def side_ask(book: BookRow, side: str) -> float:
    return book.yes_ask if side == "YES" else book.no_ask


def side_bid_size(book: BookRow, side: str) -> float:
    return book.yes_bid_sz if side == "YES" else book.no_bid_sz


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def valid_book(book: BookRow) -> bool:
    vals = (book.yes_bid, book.yes_ask, book.no_bid, book.no_ask)
    return all(v > 0.0 for v in vals)


def read_btc_rounds(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.official_outcome
        FROM market_meta m
        LEFT JOIN settlement_records s USING(condition_id)
        WHERE lower(m.slug) LIKE 'btc-updown-5m-%'
        ORDER BY m.end_ms ASC
        """
    ).fetchall()


def read_books(conn: sqlite3.Connection, condition_id: str, lo_ms: int, hi_ms: int) -> list[BookRow]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms BETWEEN ? AND ?
        ORDER BY recv_ms ASC, capture_seq ASC
        """,
        (condition_id, lo_ms, hi_ms),
    ).fetchall()
    return [
        BookRow(
            ts_ms=int(r["recv_ms"]),
            yes_bid=float(r["yes_bid_px"] or 0.0),
            yes_ask=float(r["yes_ask_px"] or 0.0),
            no_bid=float(r["no_bid_px"] or 0.0),
            no_ask=float(r["no_ask_px"] or 0.0),
            yes_bid_sz=float(r["yes_bid_sz"] or 0.0),
            yes_ask_sz=float(r["yes_ask_sz"] or 0.0),
            no_bid_sz=float(r["no_bid_sz"] or 0.0),
            no_ask_sz=float(r["no_ask_sz"] or 0.0),
        )
        for r in rows
    ]


def read_trades(conn: sqlite3.Connection, condition_id: str, lo_ms: int, hi_ms: int) -> list[TradeRow]:
    rows = conn.execute(
        """
        SELECT COALESCE(trade_ts_ms, recv_ms) AS ts_ms, market_side, taker_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND COALESCE(trade_ts_ms, recv_ms) BETWEEN ? AND ?
        ORDER BY COALESCE(trade_ts_ms, recv_ms) ASC, capture_seq ASC
        """,
        (condition_id, lo_ms, hi_ms),
    ).fetchall()
    out: list[TradeRow] = []
    for r in rows:
        side = str(r["market_side"] or "").upper()
        taker_side = str(r["taker_side"] or "").upper()
        if side not in {"YES", "NO"}:
            continue
        out.append(
            TradeRow(
                ts_ms=int(r["ts_ms"]),
                side=side,
                taker_side=taker_side,
                price=float(r["price"] or 0.0),
                size=float(r["size"] or 0.0),
            )
        )
    return out


def seed_candidate(side: str, book: BookRow) -> SeedCandidate | None:
    opp = opposite(side)
    price = side_bid(book, side)
    opp_ask = side_ask(book, opp)
    if price <= 0.0 or opp_ask <= 0.0:
        return None
    if price + opp_ask > 1.0 + 1e-9:
        return None
    return SeedCandidate(
        side=side,
        place_ts_ms=book.ts_ms,
        price=price,
        bid_size=side_bid_size(book, side),
        opp_ask_at_place=opp_ask,
    )


def first_seed_candidates(books: Sequence[BookRow], start_ms: int, end_ms: int) -> dict[str, SeedCandidate]:
    cutoff_ms = end_ms - SEED_CUTOFF_SEC * 1000
    out: dict[str, SeedCandidate] = {}
    for book in books:
        if book.ts_ms < start_ms or book.ts_ms > cutoff_ms or not valid_book(book):
            continue
        for side in ("YES", "NO"):
            if side in out:
                continue
            cand = seed_candidate(side, book)
            if cand is not None:
                out[side] = cand
        if len(out) == 2:
            break
    return out


def first_touch_fill(
    candidate: SeedCandidate,
    trades: Sequence[TradeRow],
    cutoff_ms: int,
    clip_qty: float,
) -> FillProxy | None:
    qty = 0.0
    first_ts: int | None = None
    for tr in trades:
        if tr.ts_ms < candidate.place_ts_ms or tr.ts_ms > cutoff_ms:
            continue
        if tr.side != candidate.side or tr.taker_side != "SELL":
            continue
        if tr.price > candidate.price + 1e-9:
            continue
        if first_ts is None:
            first_ts = tr.ts_ms
        qty += max(0.0, tr.size)
        return FillProxy(candidate.side, tr.ts_ms, candidate.price, min(clip_qty, qty), "touch")
    return None


def strict_full_fill(
    candidate: SeedCandidate,
    trades: Sequence[TradeRow],
    cutoff_ms: int,
    clip_qty: float,
) -> FillProxy | None:
    qty = 0.0
    for tr in trades:
        if tr.ts_ms < candidate.place_ts_ms or tr.ts_ms > cutoff_ms:
            continue
        if tr.side != candidate.side or tr.taker_side != "SELL":
            continue
        if tr.price > candidate.price + 1e-9:
            continue
        qty += max(0.0, tr.size)
        if qty + 1e-9 >= clip_qty:
            return FillProxy(candidate.side, tr.ts_ms, candidate.price, clip_qty, "strict_full")
    return None


def last_book_at_or_before(books: Sequence[BookRow], ts_ms: int, start_idx: int = 0) -> tuple[BookRow | None, int]:
    if not books:
        return None, start_idx
    i = max(0, min(start_idx, len(books) - 1))
    while i + 1 < len(books) and books[i + 1].ts_ms <= ts_ms:
        i += 1
    if books[i].ts_ms <= ts_ms:
        return books[i], i
    return None, i


def first_visible_ask_delay(
    books: Sequence[BookRow],
    *,
    side: str,
    start_ts_ms: int,
    ceiling: float,
    max_ts_ms: int,
) -> tuple[int | None, float | None]:
    best_price: float | None = None
    first_delay: int | None = None
    for book in books:
        if book.ts_ms < start_ts_ms:
            continue
        if book.ts_ms > max_ts_ms:
            break
        ask = side_ask(book, side)
        if ask <= 0.0:
            continue
        if ask <= ceiling + 1e-9:
            if first_delay is None:
                first_delay = book.ts_ms - start_ts_ms
            if best_price is None or ask < best_price:
                best_price = ask
    return first_delay, best_price


def simulate_completion_maker(
    *,
    first_fill: FillProxy,
    books: Sequence[BookRow],
    trades: Sequence[TradeRow],
    end_ms: int,
    clip_qty: float,
) -> tuple[FillProxy | None, float]:
    hedge_side = opposite(first_fill.side)
    pos_ceiling = 1.0 - first_fill.price - POSITIVE_EDGE
    be_ceiling = 1.0 - first_fill.price
    qty = 0.0
    notional = 0.0
    last_fill_ts: int | None = None
    book_idx = 0
    for tr in trades:
        if tr.ts_ms < first_fill.ts_ms:
            continue
        if tr.side != hedge_side or tr.taker_side != "SELL":
            continue
        book, book_idx = last_book_at_or_before(books, tr.ts_ms, book_idx)
        if book is None:
            continue
        age_ms = tr.ts_ms - first_fill.ts_ms
        remaining_ms = end_ms - tr.ts_ms
        ceiling = (
            be_ceiling
            if age_ms >= BREAKEVEN_UNLOCK_AGE_MS or remaining_ms <= BREAKEVEN_UNLOCK_REMAINING_MS
            else pos_ceiling
        )
        quote_price = min(side_bid(book, hedge_side), ceiling)
        if quote_price <= 0.0:
            continue
        if tr.price > quote_price + 1e-9:
            continue
        fill_qty = min(max(0.0, tr.size), clip_qty - qty)
        if fill_qty <= 0.0:
            continue
        qty += fill_qty
        notional += fill_qty * quote_price
        last_fill_ts = tr.ts_ms
        if qty + 1e-9 >= clip_qty:
            vwap = notional / qty if qty > 0.0 else quote_price
            return FillProxy(hedge_side, tr.ts_ms, vwap, qty, "strict_full"), qty
    if qty > 0.0:
        vwap = notional / qty
        return FillProxy(hedge_side, last_fill_ts or first_fill.ts_ms, vwap, qty, "partial"), qty
    return None, 0.0


def status_for(
    *,
    excluded_reason: str | None,
    candidates: dict[str, SeedCandidate],
    first_fill: FillProxy | None,
    completion_fill: FillProxy | None,
    completion_qty: float,
    clip_qty: float,
) -> str:
    if excluded_reason:
        return "excluded"
    if not candidates:
        return "no_seed_candidate"
    if first_fill is None:
        return "no_first_fill_strict"
    if completion_fill is not None and completion_qty + 1e-9 >= clip_qty:
        return "completed_strict"
    if completion_qty > 0.0:
        return "partial_completion_strict"
    return "first_leg_only_strict"


def backtest_round(
    *,
    db_date: str,
    row: sqlite3.Row,
    books: Sequence[BookRow],
    trades: Sequence[TradeRow],
    clip_qty: float,
    excluded_reason: str | None,
) -> RoundResult:
    condition_id = str(row["condition_id"])
    slug = str(row["slug"])
    start_ms = int(row["start_ms"])
    end_ms = int(row["end_ms"])
    outcome = str(row["official_outcome"] or "").upper() or None
    candidates: dict[str, SeedCandidate] = {} if excluded_reason else first_seed_candidates(books, start_ms, end_ms)
    cutoff_ms = end_ms - SEED_CUTOFF_SEC * 1000

    strict_fills = [
        fill for cand in candidates.values() if (fill := strict_full_fill(cand, trades, cutoff_ms, clip_qty))
    ]
    strict_fills.sort(key=lambda f: (f.ts_ms, f.side))
    first_fill = strict_fills[0] if strict_fills else None

    touch_fills = [
        fill for cand in candidates.values() if (fill := first_touch_fill(cand, trades, cutoff_ms, clip_qty))
    ]
    touch_fills.sort(key=lambda f: (f.ts_ms, f.side))
    first_touch = touch_fills[0] if touch_fills else None

    pos_delay = be_delay = None
    pos_price = be_price = None
    completion_fill: FillProxy | None = None
    completion_qty = 0.0
    if first_fill:
        hedge_side = opposite(first_fill.side)
        pos_delay, pos_price = first_visible_ask_delay(
            books,
            side=hedge_side,
            start_ts_ms=first_fill.ts_ms,
            ceiling=1.0 - first_fill.price - POSITIVE_EDGE,
            max_ts_ms=first_fill.ts_ms + COMPLETION_WINDOW_MS,
        )
        be_delay, be_price = first_visible_ask_delay(
            books,
            side=hedge_side,
            start_ts_ms=first_fill.ts_ms,
            ceiling=1.0 - first_fill.price,
            max_ts_ms=first_fill.ts_ms + COMPLETION_WINDOW_MS,
        )
        completion_fill, completion_qty = simulate_completion_maker(
            first_fill=first_fill,
            books=books,
            trades=trades,
            end_ms=end_ms,
            clip_qty=clip_qty,
        )

    residual = max(0.0, (first_fill.qty if first_fill else 0.0) - completion_qty)
    net_sign = 0.0
    if first_fill:
        net_sign = 1.0 if first_fill.side == "YES" else -1.0
    seed_yes = candidates.get("YES")
    seed_no = candidates.get("NO")
    pair_cost = None
    if first_fill and completion_fill and completion_fill.qty > 0.0:
        pair_cost = first_fill.price + completion_fill.price
    return RoundResult(
        db_date=db_date,
        slug=slug,
        condition_id=condition_id,
        start_ms=start_ms,
        end_ms=end_ms,
        official_outcome=outcome,
        excluded_reason=excluded_reason,
        book_rows=len(books),
        trade_rows=len(trades),
        first_book_ms=books[0].ts_ms if books else None,
        last_book_ms=books[-1].ts_ms if books else None,
        first_trade_ms=trades[0].ts_ms if trades else None,
        last_trade_ms=trades[-1].ts_ms if trades else None,
        seed_yes_eligible=1 if seed_yes else 0,
        seed_no_eligible=1 if seed_no else 0,
        seed_yes_price=seed_yes.price if seed_yes else None,
        seed_no_price=seed_no.price if seed_no else None,
        first_leg_side=first_fill.side if first_fill else None,
        first_leg_ts_ms=first_fill.ts_ms if first_fill else None,
        first_leg_offset_ms=(first_fill.ts_ms - start_ms) if first_fill else None,
        first_leg_price=first_fill.price if first_fill else None,
        first_leg_qty=first_fill.qty if first_fill else 0.0,
        first_leg_touch_side=first_touch.side if first_touch else None,
        first_leg_touch_ts_ms=first_touch.ts_ms if first_touch else None,
        first_leg_touch_qty=first_touch.qty if first_touch else 0.0,
        positive_visible_delay_ms=pos_delay,
        breakeven_visible_delay_ms=be_delay,
        completion30_positive_visible=1 if pos_delay is not None else 0,
        completion30_breakeven_visible=1 if be_delay is not None else 0,
        completion30_maker_strict=(
            1
            if first_fill
            and completion_fill
            and completion_fill.qty + 1e-9 >= clip_qty
            and completion_fill.ts_ms <= first_fill.ts_ms + COMPLETION_WINDOW_MS
            else 0
        ),
        completion_strict_ts_ms=completion_fill.ts_ms if completion_fill else None,
        completion_strict_delay_ms=(completion_fill.ts_ms - first_fill.ts_ms) if first_fill and completion_fill else None,
        completion_strict_price_vwap=completion_fill.price if completion_fill else None,
        completion_strict_qty=completion_qty,
        pair_cost_strict=pair_cost,
        pair_cost_30s_positive_visible=(first_fill.price + pos_price) if first_fill and pos_price is not None else None,
        pair_cost_30s_breakeven_visible=(first_fill.price + be_price) if first_fill and be_price is not None else None,
        residual_qty_strict=residual,
        net_diff_strict=residual * net_sign,
        status=status_for(
            excluded_reason=excluded_reason,
            candidates=candidates,
            first_fill=first_fill,
            completion_fill=completion_fill,
            completion_qty=completion_qty,
            clip_qty=clip_qty,
        ),
    )


def percentile(values: Sequence[float], q: float) -> float | None:
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    rank = (len(vals) - 1) * q
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return vals[int(lo)]
    frac = rank - lo
    return vals[lo] * (1 - frac) + vals[hi] * frac


def ratio(n: int, d: int) -> float:
    return float(n) / float(d) if d else 0.0


def aggregate(results: Sequence[RoundResult], args: argparse.Namespace) -> dict[str, object]:
    included = [r for r in results if not r.excluded_reason]
    candidates = [r for r in included if r.seed_yes_eligible or r.seed_no_eligible]
    strict_first = [r for r in included if r.first_leg_ts_ms is not None]
    strict_complete = [r for r in strict_first if r.status == "completed_strict"]
    strict_complete30 = [r for r in strict_first if r.completion30_maker_strict]
    active_pair_costs = [r.pair_cost_strict for r in strict_complete if r.pair_cost_strict is not None]
    be_visible = [r for r in strict_first if r.completion30_breakeven_visible]
    pos_visible = [r for r in strict_first if r.completion30_positive_visible]
    residual_rounds = [r for r in strict_first if r.residual_qty_strict > 1e-9]
    first_offsets = [float(r.first_leg_offset_ms) for r in strict_first if r.first_leg_offset_ms is not None]
    strict_completion_delays = [
        float(r.completion_strict_delay_ms) for r in strict_complete if r.completion_strict_delay_ms is not None
    ]
    be_delays = [float(r.breakeven_visible_delay_ms) for r in be_visible if r.breakeven_visible_delay_ms is not None]
    pos_delays = [float(r.positive_visible_delay_ms) for r in pos_visible if r.positive_visible_delay_ms is not None]
    full_spend = sum((r.first_leg_price or 0.0) * args.clip_qty + (r.completion_strict_price_vwap or 0.0) * args.clip_qty for r in strict_complete)
    full_payout = float(len(strict_complete)) * args.clip_qty
    full_profit = full_payout - full_spend
    paired_qty = sum(r.completion_strict_qty for r in strict_first)
    paired_cost = sum(
        ((r.first_leg_price or 0.0) + (r.completion_strict_price_vwap or 0.0)) * r.completion_strict_qty
        for r in strict_first
        if r.completion_strict_qty > 0.0 and r.completion_strict_price_vwap is not None
    )
    paired_profit = paired_qty - paired_cost
    first_leg_spend = sum((r.first_leg_price or 0.0) * args.clip_qty for r in strict_first)
    completion_spend = sum((r.completion_strict_price_vwap or 0.0) * r.completion_strict_qty for r in strict_first)
    all_spend = first_leg_spend + completion_spend
    residual_qty = sum(r.residual_qty_strict for r in strict_first)
    residual_cost = sum((r.first_leg_price or 0.0) * r.residual_qty_strict for r in strict_first)
    worst_profit = paired_profit - residual_cost
    best_profit = paired_profit + residual_qty - residual_cost
    expected_50_profit = paired_profit + 0.5 * residual_qty - residual_cost
    breakeven_residual_win_qty = max(0.0, residual_cost - paired_profit)
    known_residual_profit = 0.0
    known_residual_cost = 0.0
    known_residual_qty = 0.0
    known_residual_count = 0
    for r in strict_first:
        if r.residual_qty_strict <= 1e-9 or r.official_outcome not in ("YES", "NO"):
            continue
        cost = (r.first_leg_price or 0.0) * r.residual_qty_strict
        payout = r.residual_qty_strict if r.first_leg_side == r.official_outcome else 0.0
        known_residual_profit += payout - cost
        known_residual_cost += cost
        known_residual_qty += r.residual_qty_strict
        known_residual_count += 1
    positive_visible_costs = [
        r.pair_cost_30s_positive_visible for r in pos_visible if r.pair_cost_30s_positive_visible is not None
    ]
    breakeven_visible_costs = [
        r.pair_cost_30s_breakeven_visible for r in be_visible if r.pair_cost_30s_breakeven_visible is not None
    ]
    return {
        "data_boundaries": {
            "db_dates": args.date or list(DEFAULT_DATES),
            "trusted_start_ms": TRUSTED_START_MS,
            "trusted_start_utc": iso_ms(TRUSTED_START_MS),
            "planned_outage_start_ms": OUTAGE_START_MS,
            "planned_outage_end_ms": OUTAGE_END_MS,
            "planned_outage_start_utc": iso_ms(OUTAGE_START_MS),
            "planned_outage_end_utc": iso_ms(OUTAGE_END_MS),
            "partial_day_note": "2026-04-30 is used as available-window data; per-round rows expose first/last book/trade timestamps.",
        },
        "model": {
            "scope": "BTC 5m public market-side replay only",
            "clip_qty": args.clip_qty,
            "seed_cutoff_sec": SEED_CUTOFF_SEC,
            "strict_completion_horizon": "opposite maker completion is simulated from first-leg fill through round end + 30s",
            "positive_edge": POSITIVE_EDGE,
            "strict_full_fill": "same-side public trade with taker_side=SELL at or below our maker bid, cumulative size >= clip",
            "touch_fill": "same-side public trade with taker_side=SELL at or below our maker bid, any size",
            "completion30_visible": "opposite L1 ask reaches positive/breakeven ceiling within 30s after strict first-leg proxy fill",
            "limitations": [
                "No queue-position truth",
                "No xuan exact episode truth",
                "No own execution truth",
                "L1 only; no full depth queue reconstruction",
            ],
        },
        "counts": {
            "round_rows": len(results),
            "included_rounds": len(included),
            "excluded_rounds": len(results) - len(included),
            "seed_candidate_rounds": len(candidates),
            "strict_first_leg_rounds": len(strict_first),
            "strict_completed_rounds": len(strict_complete),
            "completion30_maker_strict_rounds": len(strict_complete30),
            "strict_residual_rounds": len(residual_rounds),
            "completion30_positive_visible_rounds": len(pos_visible),
            "completion30_breakeven_visible_rounds": len(be_visible),
        },
        "ratios": {
            "seed_candidate_per_included": ratio(len(candidates), len(included)),
            "strict_first_leg_per_candidate": ratio(len(strict_first), len(candidates)),
            "strict_completed_per_first_leg": ratio(len(strict_complete), len(strict_first)),
            "completion30_maker_strict_per_first_leg": ratio(len(strict_complete30), len(strict_first)),
            "strict_residual_per_first_leg": ratio(len(residual_rounds), len(strict_first)),
            "completion30_positive_visible_per_first_leg": ratio(len(pos_visible), len(strict_first)),
            "completion30_breakeven_visible_per_first_leg": ratio(len(be_visible), len(strict_first)),
        },
        "pair_cost_strict": {
            "count": len(active_pair_costs),
            "median": median(active_pair_costs) if active_pair_costs else None,
            "p90": percentile(active_pair_costs, 0.90),
            "max": max(active_pair_costs) if active_pair_costs else None,
            "lt_0_995_ratio": ratio(sum(1 for v in active_pair_costs if v < 0.995 - 1e-9), len(active_pair_costs)),
            "lte_1_000_ratio": ratio(sum(1 for v in active_pair_costs if v <= 1.0 + 1e-9), len(active_pair_costs)),
            "gt_1_000_ratio": ratio(sum(1 for v in active_pair_costs if v > 1.0 + 1e-9), len(active_pair_costs)),
        },
        "pnl_scenarios": {
            "full_completed_only": {
                "spend": full_spend,
                "payout": full_payout,
                "profit": full_profit,
                "roi_on_spend": ratio(full_profit, full_spend),
            },
            "paired_total_ignore_residual": {
                "paired_qty": paired_qty,
                "spend": paired_cost,
                "profit": paired_profit,
                "roi_on_spend": ratio(paired_profit, paired_cost),
            },
            "all_first_leg_system": {
                "total_spend": all_spend,
                "paired_profit_before_residual_settlement": paired_profit,
                "residual_qty": residual_qty,
                "residual_cost": residual_cost,
                "worst_profit_if_all_residual_loses": worst_profit,
                "worst_roi_on_total_spend": ratio(worst_profit, all_spend),
                "expected_profit_if_residual_win_rate_50pct_by_qty": expected_50_profit,
                "expected_roi_if_residual_win_rate_50pct_by_qty": ratio(expected_50_profit, all_spend),
                "best_profit_if_all_residual_wins": best_profit,
                "best_roi_on_total_spend": ratio(best_profit, all_spend),
                "breakeven_residual_win_qty": breakeven_residual_win_qty,
                "breakeven_residual_win_rate_by_qty": ratio(breakeven_residual_win_qty, residual_qty),
            },
            "known_residual_outcome_subset": {
                "rounds": known_residual_count,
                "qty": known_residual_qty,
                "cost": known_residual_cost,
                "profit": known_residual_profit,
                "roi_on_cost": ratio(known_residual_profit, known_residual_cost),
                "coverage_note": "Sparse BTC settlement coverage in this replay; do not extrapolate this subset as full residual truth.",
            },
            "visible_30s_opportunity_costs": {
                "positive_count": len(positive_visible_costs),
                "positive_median_cost": median(positive_visible_costs) if positive_visible_costs else None,
                "positive_p90_cost": percentile(positive_visible_costs, 0.90),
                "breakeven_count": len(breakeven_visible_costs),
                "breakeven_median_cost": median(breakeven_visible_costs) if breakeven_visible_costs else None,
                "breakeven_p90_cost": percentile(breakeven_visible_costs, 0.90),
            },
        },
        "timing_ms": {
            "first_leg_offset_p50": percentile(first_offsets, 0.50),
            "first_leg_offset_p90": percentile(first_offsets, 0.90),
            "strict_completion_delay_p50": percentile(strict_completion_delays, 0.50),
            "strict_completion_delay_p90": percentile(strict_completion_delays, 0.90),
            "positive_visible_delay_p50": percentile(pos_delays, 0.50),
            "positive_visible_delay_p90": percentile(pos_delays, 0.90),
            "breakeven_visible_delay_p50": percentile(be_delays, 0.50),
            "breakeven_visible_delay_p90": percentile(be_delays, 0.90),
        },
        "status_counts": dict(sorted({r.status: sum(1 for x in results if x.status == r.status) for r in results}.items())),
        "excluded_counts": dict(
            sorted(
                {
                    str(r.excluded_reason): sum(1 for x in results if x.excluded_reason == r.excluded_reason)
                    for r in results
                    if r.excluded_reason
                }.items()
            )
        ),
    }


def iter_db_paths(replay_root: Path, dates: Iterable[str]) -> Iterable[tuple[str, Path]]:
    for day in dates:
        path = replay_root / day / "crypto_5m.sqlite"
        if not path.exists():
            raise SystemExit(f"missing replay DB: {path}")
        yield day, path


def main() -> None:
    args = parse_args()
    dates = args.date or list(DEFAULT_DATES)
    replay_root = Path(args.replay_root).expanduser()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    results: list[RoundResult] = []
    for db_date, db_path in iter_db_paths(replay_root, dates):
        with connect_ro(db_path) as conn:
            for meta in read_btc_rounds(conn):
                start_ms = int(meta["start_ms"])
                end_ms = int(meta["end_ms"])
                excluded = None
                if not args.include_pre_trusted and start_ms < TRUSTED_START_MS:
                    excluded = "pre_trusted_start"
                if not args.include_outage and overlaps(start_ms, end_ms, OUTAGE_START_MS, OUTAGE_END_MS):
                    excluded = "planned_outage"

                lo_ms = max(start_ms, TRUSTED_START_MS)
                hi_ms = end_ms + COMPLETION_WINDOW_MS
                books = read_books(conn, str(meta["condition_id"]), lo_ms, hi_ms)
                trades = read_trades(conn, str(meta["condition_id"]), lo_ms, hi_ms)
                if excluded is None and not books:
                    excluded = "no_book_data"
                results.append(
                    backtest_round(
                        db_date=db_date,
                        row=meta,
                        books=books,
                        trades=trades,
                        clip_qty=args.clip_qty,
                        excluded_reason=excluded,
                    )
                )
                if args.max_rounds and len([r for r in results if not r.excluded_reason]) >= args.max_rounds:
                    break
        if args.max_rounds and len([r for r in results if not r.excluded_reason]) >= args.max_rounds:
            break

    csv_path = output_dir / "btc5m_pgt_market_side_rounds.csv"
    json_path = output_dir / "btc5m_pgt_market_side_summary.json"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(results[0]).keys()) if results else list(RoundResult.__dataclass_fields__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))

    summary = aggregate(results, args)
    summary["outputs"] = {"round_csv": str(csv_path), "summary_json": str(json_path)}
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

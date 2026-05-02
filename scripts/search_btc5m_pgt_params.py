#!/usr/bin/env python3
"""Coarse BTC 5m PGT parameter search over public replay SQLite.

This is still market-side only. It reads the same replay DBs in read-only mode
and does not claim queue-position truth, xuan episode truth, or own fills.
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence

from backtest_btc5m_pgt_market_side import (
    COMPLETION_WINDOW_MS,
    DEFAULT_DATES,
    OUTAGE_END_MS,
    OUTAGE_START_MS,
    REPLAY_ROOT,
    TRUSTED_START_MS,
    BookRow,
    FillProxy,
    TradeRow,
    connect_ro,
    iter_db_paths,
    last_book_at_or_before,
    opposite,
    overlaps,
    read_books,
    read_btc_rounds,
    read_trades,
    side_ask,
    side_bid,
    side_bid_size,
    valid_book,
)


@dataclass(frozen=True)
class ParamSet:
    name: str
    clip_qty: float
    entry_start_sec: int
    seed_end_guard_sec: int
    seed_pair_cap: float
    early_pair_cap: float
    late_pair_cap: float
    unlock_age_ms: int
    unlock_remaining_ms: int


@dataclass
class Agg:
    name: str
    clip_qty: float
    entry_start_sec: int
    seed_end_guard_sec: int
    seed_pair_cap: float
    early_pair_cap: float
    late_pair_cap: float
    unlock_age_ms: int
    unlock_remaining_ms: int
    included_rounds: int = 0
    seed_rounds: int = 0
    first_leg_rounds: int = 0
    full_complete_rounds: int = 0
    partial_rounds: int = 0
    residual_rounds: int = 0
    paired_qty: float = 0.0
    paired_first_cost: float = 0.0
    completion_spend: float = 0.0
    paired_spend: float = 0.0
    paired_profit: float = 0.0
    first_leg_spend: float = 0.0
    total_spend: float = 0.0
    residual_qty: float = 0.0
    residual_cost: float = 0.0

    def finish(self) -> dict[str, float | int | str]:
        worst_profit = self.paired_profit - self.residual_cost
        profit_25 = self.paired_profit + 0.25 * self.residual_qty - self.residual_cost
        profit_33 = self.paired_profit + 0.33 * self.residual_qty - self.residual_cost
        profit_50 = self.paired_profit + 0.50 * self.residual_qty - self.residual_cost
        best_profit = self.paired_profit + self.residual_qty - self.residual_cost
        breakeven_win_qty = max(0.0, self.residual_cost - self.paired_profit)
        out = asdict(self)
        for haircut in (0.70, 0.50, 0.30):
            paired_profit_h = self.paired_profit * haircut
            residual_qty_h = self.residual_qty + (1.0 - haircut) * self.paired_qty
            residual_cost_h = self.residual_cost + (1.0 - haircut) * self.paired_first_cost
            total_spend_h = self.first_leg_spend + haircut * self.completion_spend
            worst_profit_h = paired_profit_h - residual_cost_h
            profit33_h = paired_profit_h + 0.33 * residual_qty_h - residual_cost_h
            profit50_h = paired_profit_h + 0.50 * residual_qty_h - residual_cost_h
            breakeven_h = max(0.0, residual_cost_h - paired_profit_h)
            prefix = f"completion_haircut_{int(haircut * 100)}"
            out.update(
                {
                    f"{prefix}_total_spend": total_spend_h,
                    f"{prefix}_worst_roi": safe_ratio(worst_profit_h, total_spend_h),
                    f"{prefix}_roi_if_residual_win_33pct": safe_ratio(profit33_h, total_spend_h),
                    f"{prefix}_roi_if_residual_win_50pct": safe_ratio(profit50_h, total_spend_h),
                    f"{prefix}_breakeven_residual_win_rate_by_qty": safe_ratio(breakeven_h, residual_qty_h),
                }
            )
        out.update(
            {
                "seed_ratio": safe_ratio(self.seed_rounds, self.included_rounds),
                "first_leg_ratio": safe_ratio(self.first_leg_rounds, self.seed_rounds),
                "full_complete_ratio": safe_ratio(self.full_complete_rounds, self.first_leg_rounds),
                "residual_ratio": safe_ratio(self.residual_rounds, self.first_leg_rounds),
                "paired_roi": safe_ratio(self.paired_profit, self.paired_spend),
                "residual_cost_ratio_total_spend": safe_ratio(self.residual_cost, self.total_spend),
                "worst_profit": worst_profit,
                "worst_roi": safe_ratio(worst_profit, self.total_spend),
                "profit_if_residual_win_25pct": profit_25,
                "roi_if_residual_win_25pct": safe_ratio(profit_25, self.total_spend),
                "profit_if_residual_win_33pct": profit_33,
                "roi_if_residual_win_33pct": safe_ratio(profit_33, self.total_spend),
                "profit_if_residual_win_50pct": profit_50,
                "roi_if_residual_win_50pct": safe_ratio(profit_50, self.total_spend),
                "best_profit": best_profit,
                "best_roi": safe_ratio(best_profit, self.total_spend),
                "breakeven_residual_win_rate_by_qty": safe_ratio(breakeven_win_qty, self.residual_qty),
            }
        )
        return out


def safe_ratio(n: float, d: float) -> float:
    return float(n) / float(d) if d else 0.0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--replay-root", default=str(REPLAY_ROOT))
    p.add_argument("--date", action="append", default=None)
    p.add_argument("--output-dir", default="data/exports/pgt_param_search_20260501")
    p.add_argument("--max-rounds", type=int, default=0)
    p.add_argument("--include-outage", action="store_true")
    p.add_argument("--include-pre-trusted", action="store_true")
    p.add_argument("--quick", action="store_true", help="smaller grid for fast iteration")
    p.add_argument("--focused", action="store_true", help="focused grid around the current best region")
    return p.parse_args()


def build_grid(quick: bool, focused: bool) -> list[ParamSet]:
    if focused:
        clips = [30.0, 45.0, 57.6]
        entry_starts = [45, 60, 75]
        end_guards = [150]
        seed_caps = [0.970, 0.975, 0.980, 0.985]
        early_caps = [0.975, 0.980, 0.985]
        late_caps = [0.995, 1.000]
        unlock_ages = [30_000, 60_000]
    else:
        clips = [30.0, 45.0, 57.6] if quick else [20.0, 30.0, 45.0, 57.6]
        entry_starts = [0, 60] if quick else [0, 60, 120]
        end_guards = [150] if quick else [90, 150]
        seed_caps = [0.98, 1.0]
        early_caps = [0.98, 0.995]
        late_caps = [0.995, 1.0]
        unlock_ages = [30_000, 60_000] if quick else [30_000, 60_000, 90_000]
    out: list[ParamSet] = []
    for clip in clips:
        for entry in entry_starts:
            for guard in end_guards:
                for seed_cap in seed_caps:
                    for early_cap in early_caps:
                        for late_cap in late_caps:
                            if late_cap + 1e-9 < early_cap:
                                continue
                            for unlock_age in unlock_ages:
                                name = (
                                    f"clip{clip:g}_entry{entry}_guard{guard}_seed{seed_cap:.3f}_"
                                    f"early{early_cap:.3f}_late{late_cap:.3f}_unlock{unlock_age//1000}"
                                )
                                out.append(
                                    ParamSet(
                                        name=name,
                                        clip_qty=clip,
                                        entry_start_sec=entry,
                                        seed_end_guard_sec=guard,
                                        seed_pair_cap=seed_cap,
                                        early_pair_cap=early_cap,
                                        late_pair_cap=late_cap,
                                        unlock_age_ms=unlock_age,
                                        unlock_remaining_ms=90_000,
                                    )
                                )
    baseline = ParamSet(
        name="baseline_clip57.6_entry0_guard150_seed1.000_early0.995_late1.000_unlock60",
        clip_qty=57.6,
        entry_start_sec=0,
        seed_end_guard_sec=150,
        seed_pair_cap=1.0,
        early_pair_cap=0.995,
        late_pair_cap=1.0,
        unlock_age_ms=60_000,
        unlock_remaining_ms=90_000,
    )
    if baseline not in out:
        out.append(baseline)
    return out


def seed_candidate_for_params(
    books: Sequence[BookRow],
    *,
    side: str,
    start_ms: int,
    end_ms: int,
    params: ParamSet,
) -> tuple[str, int, float] | None:
    lo_ms = start_ms + params.entry_start_sec * 1000
    hi_ms = end_ms - params.seed_end_guard_sec * 1000
    if hi_ms < lo_ms:
        return None
    opp = opposite(side)
    for book in books:
        if book.ts_ms < lo_ms:
            continue
        if book.ts_ms > hi_ms:
            break
        if not valid_book(book):
            continue
        price = side_bid(book, side)
        opp_ask = side_ask(book, opp)
        if price <= 0.0 or opp_ask <= 0.0:
            continue
        if price + opp_ask > params.seed_pair_cap + 1e-9:
            continue
        if side_bid_size(book, side) <= 0.0:
            continue
        return (side, book.ts_ms, price)
    return None


def strict_full_fill(
    candidate: tuple[str, int, float],
    trades: Sequence[TradeRow],
    *,
    cutoff_ms: int,
    clip_qty: float,
) -> FillProxy | None:
    side, place_ts_ms, price = candidate
    qty = 0.0
    for tr in trades:
        if tr.ts_ms < place_ts_ms:
            continue
        if tr.ts_ms > cutoff_ms:
            break
        if tr.side != side or tr.taker_side != "SELL":
            continue
        if tr.price > price + 1e-9:
            continue
        qty += max(0.0, tr.size)
        if qty + 1e-9 >= clip_qty:
            return FillProxy(side, tr.ts_ms, price, clip_qty, "strict_full")
    return None


def simulate_completion(
    *,
    first_fill: FillProxy,
    books: Sequence[BookRow],
    trades: Sequence[TradeRow],
    end_ms: int,
    params: ParamSet,
) -> FillProxy | None:
    hedge_side = opposite(first_fill.side)
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
        pair_cap = (
            params.late_pair_cap
            if age_ms >= params.unlock_age_ms or remaining_ms <= params.unlock_remaining_ms
            else params.early_pair_cap
        )
        quote_price = min(side_bid(book, hedge_side), pair_cap - first_fill.price)
        if quote_price <= 0.0:
            continue
        if tr.price > quote_price + 1e-9:
            continue
        fill_qty = min(max(0.0, tr.size), params.clip_qty - qty)
        if fill_qty <= 0.0:
            continue
        qty += fill_qty
        notional += fill_qty * quote_price
        last_fill_ts = tr.ts_ms
        if qty + 1e-9 >= params.clip_qty:
            return FillProxy(hedge_side, tr.ts_ms, notional / qty, qty, "strict_full")
    if qty > 0.0:
        return FillProxy(hedge_side, last_fill_ts or first_fill.ts_ms, notional / qty, qty, "partial")
    return None


def simulate_round(
    *,
    books: Sequence[BookRow],
    trades: Sequence[TradeRow],
    start_ms: int,
    end_ms: int,
    params: ParamSet,
    agg: Agg,
) -> None:
    agg.included_rounds += 1
    cutoff_ms = end_ms - params.seed_end_guard_sec * 1000
    candidates = [
        c
        for side in ("YES", "NO")
        if (c := seed_candidate_for_params(books, side=side, start_ms=start_ms, end_ms=end_ms, params=params))
        is not None
    ]
    if not candidates:
        return
    agg.seed_rounds += 1
    fills = [f for c in candidates if (f := strict_full_fill(c, trades, cutoff_ms=cutoff_ms, clip_qty=params.clip_qty))]
    fills.sort(key=lambda x: (x.ts_ms, x.side))
    if not fills:
        return
    first = fills[0]
    agg.first_leg_rounds += 1
    agg.first_leg_spend += first.price * params.clip_qty
    agg.total_spend += first.price * params.clip_qty
    completion = simulate_completion(first_fill=first, books=books, trades=trades, end_ms=end_ms, params=params)
    comp_qty = completion.qty if completion else 0.0
    if completion:
        agg.paired_qty += comp_qty
        agg.paired_first_cost += first.price * comp_qty
        agg.completion_spend += completion.price * comp_qty
        agg.paired_spend += (first.price + completion.price) * comp_qty
        agg.paired_profit += (1.0 - first.price - completion.price) * comp_qty
        agg.total_spend += completion.price * comp_qty
        if comp_qty + 1e-9 >= params.clip_qty:
            agg.full_complete_rounds += 1
        else:
            agg.partial_rounds += 1
    residual = max(0.0, params.clip_qty - comp_qty)
    if residual > 1e-9:
        agg.residual_rounds += 1
        agg.residual_qty += residual
        agg.residual_cost += first.price * residual


def main() -> None:
    args = parse_args()
    dates = args.date or list(DEFAULT_DATES)
    params = build_grid(args.quick, args.focused)
    aggs = {p.name: Agg(**asdict(p)) for p in params}
    included_seen = 0
    for db_date, db_path in iter_db_paths(Path(args.replay_root), dates):
        with connect_ro(db_path) as conn:
            for meta in read_btc_rounds(conn):
                start_ms = int(meta["start_ms"])
                end_ms = int(meta["end_ms"])
                if not args.include_pre_trusted and start_ms < TRUSTED_START_MS:
                    continue
                if not args.include_outage and overlaps(start_ms, end_ms, OUTAGE_START_MS, OUTAGE_END_MS):
                    continue
                books = read_books(conn, str(meta["condition_id"]), max(start_ms, TRUSTED_START_MS), end_ms + COMPLETION_WINDOW_MS)
                if not books:
                    continue
                trades = read_trades(conn, str(meta["condition_id"]), max(start_ms, TRUSTED_START_MS), end_ms + COMPLETION_WINDOW_MS)
                for p in params:
                    simulate_round(books=books, trades=trades, start_ms=start_ms, end_ms=end_ms, params=p, agg=aggs[p.name])
                included_seen += 1
                if args.max_rounds and included_seen >= args.max_rounds:
                    break
        if args.max_rounds and included_seen >= args.max_rounds:
            break

    rows = [agg.finish() for agg in aggs.values()]
    rows.sort(key=lambda r: (r["roi_if_residual_win_33pct"], r["paired_profit"]), reverse=True)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "btc5m_pgt_param_search.csv"
    json_path = out_dir / "btc5m_pgt_param_search_top.json"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    top = {
        "scope": "BTC 5m public market-side proxy; no queue/xuan/own execution truth",
        "db_dates": dates,
        "configs": len(rows),
        "included_rounds_seen": included_seen,
        "sort_key": "roi_if_residual_win_33pct desc, paired_profit desc",
        "top_by_expected33": rows[:20],
        "top_by_haircut70_expected33": sorted(
            rows,
            key=lambda r: (r["completion_haircut_70_roi_if_residual_win_33pct"], r["paired_profit"]),
            reverse=True,
        )[:20],
        "top_by_haircut50_expected33": sorted(
            rows,
            key=lambda r: (r["completion_haircut_50_roi_if_residual_win_33pct"], r["paired_profit"]),
            reverse=True,
        )[:20],
        "top_by_breakeven_residual": sorted(
            rows,
            key=lambda r: (r["breakeven_residual_win_rate_by_qty"], -r["paired_profit"]),
        )[:20],
        "top_by_paired_profit_min_500_first_legs": [
            r
            for r in sorted(rows, key=lambda r: r["paired_profit"], reverse=True)
            if r["first_leg_rounds"] >= 500
        ][:20],
    }
    json_path.write_text(json.dumps(top, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(top, ensure_ascii=False, indent=2))
    print(f"csv={csv_path}")
    print(f"json={json_path}")


if __name__ == "__main__":
    main()

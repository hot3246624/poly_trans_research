#!/usr/bin/env python3
"""Shared replay finalist verifier for taker-BUY strict-L1 candidates.

This is the optimized replay verification path for top-N finalist gates.  It
keeps replay SQLite as the source of truth, but it does not verify each rank in
isolation.  Instead it:

1. Reads selected search ranks.
2. Uses the strict V1 cache only as a trigger index.
3. Unions all required trigger events across ranks.
4. Loads trades/L1/L2 once per day/condition from replay SQLite.
5. Simulates each rank in memory and writes per-rank verified metrics.

For occasional pre-deploy audits, keep using the independent strict verifier.
"""

from __future__ import annotations

import argparse
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import verify_taker_buy_search_finalists_batched_sqlite as batched
import verify_taker_buy_search_finalists_strict as strict


EventKey = tuple[int, str, float, float]


@dataclass
class ConditionBundle:
    day: str
    condition_id: str
    market: Any
    trades_by_key: dict[EventKey, dict[str, Any]]
    l1_keys: list[tuple[int, int, int]]
    l1_rows: list[Any]
    l2_by_side: dict[str, tuple[list[tuple[int, int]], list[Any]]]
    load_elapsed_s: float


def utc_now() -> str:
    return batched.utc_now()


def event_key(event: dict[str, Any]) -> EventKey:
    return (
        int(event["trigger_ts_ms"]),
        str(event["first_side"]),
        batched.key_float(event["public_trade_price"]),
        batched.key_float(event["public_trade_size"]),
    )


def trade_key(trade: dict[str, Any]) -> EventKey:
    return (
        int(trade["trade_ts_ms"]),
        str(trade["market_side"]),
        batched.key_float(trade["price"]),
        batched.key_float(trade["size"]),
    )


def unique_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[EventKey] = set()
    out = []
    for event in events:
        key = event_key(event)
        if key in seen:
            continue
        seen.add(key)
        out.append(event)
    out.sort(key=lambda row: (int(row["trigger_ts_ms"]), str(row["first_side"]), float(row["public_trade_price"]), float(row["public_trade_size"])))
    return out


def load_condition_bundle(
    conn: Any,
    day: str,
    condition_id: str,
    events: list[dict[str, Any]],
    args: argparse.Namespace,
) -> ConditionBundle | None:
    started = time.perf_counter()
    market = batched.fetch_market(conn, condition_id)
    if market is None:
        return None
    trades = batched.resolve_trades(conn, condition_id, events, market)
    if not trades:
        return None
    trades_by_key = {trade_key(trade): trade for trade in trades}
    min_ts = min(int(trade["trade_ts_ms"]) for trade in trades)
    max_ts = max(int(trade["trade_ts_ms"]) for trade in trades)
    l1_keys, l1_rows = batched.load_l1_rows(conn, condition_id, min_ts, max_ts, args.max_l1_age_ms)
    l2_by_side = batched.load_l2_rows(conn, condition_id, min_ts, max_ts, args.max_l2_age_ms, args.completion_s)
    return ConditionBundle(
        day=day,
        condition_id=condition_id,
        market=market,
        trades_by_key=trades_by_key,
        l1_keys=l1_keys,
        l1_rows=l1_rows,
        l2_by_side=l2_by_side,
        load_elapsed_s=round(time.perf_counter() - started, 6),
    )


def simulate_loaded_condition(
    day: str,
    condition_id: str,
    events: list[dict[str, Any]],
    bundle: ConditionBundle,
    params: strict.Params,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    trades = []
    for event in events:
        trade = bundle.trades_by_key.get(event_key(event))
        if trade is not None:
            trades.append(trade)
    if not trades:
        return []
    trades.sort(key=lambda row: (int(row["trade_ts_ms"]), int(row["trade_row_id"])))

    out: list[dict[str, Any]] = []
    market_start = int(bundle.market["start_ms"])
    market_end = int(bundle.market["end_ms"])
    cursor_ms = max(market_start, strict.TRUSTED_START_MS) + params.offset_lo * 1000
    for trade in trades:
        ts_ms = int(trade["trade_ts_ms"])
        if ts_ms < cursor_ms:
            continue
        side = str(trade["market_side"])
        book = batched.l1_at(bundle.l1_keys, bundle.l1_rows, ts_ms, args.max_l1_age_ms)
        if book is None:
            continue
        alignment = strict.side_alignment(book, side)
        if alignment is None:
            continue
        if params.side_alignment != "any" and alignment != params.side_alignment:
            continue
        first_price, first_age_ms, first_worst_px, first_filled = batched.latest_l2_sweep_cached(
            bundle.l2_by_side, side, ts_ms, args.clip, args.max_l2_age_ms
        )
        if first_price is None:
            continue
        if first_price < params.first_lo or first_price >= params.first_hi:
            continue
        opp = strict.other(side)
        opp_ask = book[opp]["ask"]
        if opp_ask is None:
            continue
        l1_immediate_pair = first_price + float(opp_ask)
        if l1_immediate_pair > params.max_l1_pair + 1e-9:
            continue
        completion = batched.completion_cached(
            bundle.l2_by_side,
            opp,
            ts_ms,
            min(market_end, ts_ms + args.completion_s * 1000),
            first_price,
            args.clip,
            params.pair_ceiling,
        )
        row: dict[str, Any] = {
            "day": day,
            "slug": trade["slug"],
            "condition_id": condition_id,
            "winner_side": trade["winner_side"],
            "trigger_ts_ms": ts_ms,
            "trigger_iso": strict.iso_ms(ts_ms),
            "offset_s": round((ts_ms - market_start) / 1000.0, 3),
            "first_side": side,
            "side_alignment": alignment,
            "first_is_winner": side == trade["winner_side"],
            "trigger_price": round(first_price, 6),
            "public_trade_price": round(float(trade["price"]), 6),
            "trigger_size": round(float(trade["size"]), 6),
            "first_price_source": "l2",
            "first_price_age_ms": first_age_ms,
            "first_worst_px": None if first_worst_px is None else round(first_worst_px, 6),
            "first_filled": None if first_filled is None else round(first_filled, 6),
            "clip": args.clip,
            "strict_l1_recv_ms": book["recv_ms"],
            "strict_l1_age_ms": book["age_ms"],
            "l1_immediate_pair": round(l1_immediate_pair, 6),
            "completion_fill": False,
            "status": "residual_settle",
        }
        if completion is not None:
            pnl = (1.0 - float(completion["pair_cost"])) * args.clip
            row.update(
                {
                    "completion_fill": True,
                    "completion_ts_ms": completion["completion_ts_ms"],
                    "completion_iso": strict.iso_ms(int(completion["completion_ts_ms"])),
                    "completion_delay_s": round(float(completion["completion_delay_s"]), 3),
                    "completion_vwap": round(float(completion["completion_vwap"]), 6),
                    "completion_worst_px": round(float(completion["completion_worst_px"]), 6)
                    if completion["completion_worst_px"] is not None
                    else None,
                    "completion_filled": round(float(completion["completion_filled"]), 6),
                    "pair_cost": round(float(completion["pair_cost"]), 6),
                    "pnl": round(pnl, 6),
                    "status": "closed",
                }
            )
        else:
            pnl = (1.0 - first_price) * args.clip if side == trade["winner_side"] else -first_price * args.clip
            row["pnl"] = round(pnl, 6)
        out.append(row)
        if completion is None and params.block_after_residual:
            break
        if completion is not None:
            cursor_ms = ts_ms + int(float(completion["completion_delay_s"]) * 1000) + params.cooldown_s * 1000
        else:
            cursor_ms = ts_ms + params.cooldown_s * 1000
    return out


def build_rank_event_index(
    selected: list[tuple[int, strict.Params]],
    cache_by_day: dict[str, list[dict[str, str]]],
    days: list[str],
) -> tuple[
    dict[int, dict[str, dict[str, list[dict[str, Any]]]]],
    dict[str, dict[str, list[dict[str, Any]]]],
]:
    rank_events: dict[int, dict[str, dict[str, list[dict[str, Any]]]]] = {}
    union_events: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    union_seen: dict[tuple[str, str], set[EventKey]] = defaultdict(set)
    for rank, params in selected:
        per_day: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for day in days:
            grouped = batched.read_cache_events(cache_by_day.get(day, []), params)
            per_day[day] = grouped
            for condition_id, events in grouped.items():
                seen = union_seen[(day, condition_id)]
                for event in events:
                    key = event_key(event)
                    if key in seen:
                        continue
                    seen.add(key)
                    union_events[day][condition_id].append(event)
        rank_events[rank] = per_day
    for condition_map in union_events.values():
        for condition_id, events in list(condition_map.items()):
            condition_map[condition_id] = unique_events(events)
    return rank_events, union_events


def write_rank_report(
    rank: int,
    params: strict.Params,
    rows: list[dict[str, Any]],
    day_stats: list[dict[str, Any]],
    args: argparse.Namespace,
) -> dict[str, Any]:
    rows.sort(key=lambda row: (str(row["condition_id"]), int(row["trigger_ts_ms"])))
    report = {
        "generated_at_utc": utc_now(),
        "rank": rank,
        "parameters": {
            "price_lo": params.price_lo,
            "price_hi": params.price_hi,
            "size_lo": params.size_lo,
            "size_hi": params.size_hi,
            "first_lo": params.first_lo,
            "first_hi": params.first_hi,
            "offset_lo": params.offset_lo,
            "offset_hi": params.offset_hi,
            "max_l1_pair": params.max_l1_pair,
            "pair_ceiling": params.pair_ceiling,
            "side_alignment": params.side_alignment,
            "block_after_residual": params.block_after_residual,
            "cooldown_s": params.cooldown_s,
            "clip": args.clip,
            "max_l1_age_ms": args.max_l1_age_ms,
            "max_l2_age_ms": args.max_l2_age_ms,
            "completion_s": args.completion_s,
        },
        "cache_reference": {
            "rows": params.cache_rows,
            "pnl": params.cache_pnl,
            "min_day_pnl": params.cache_min_day_pnl,
        },
        "replay_root": str(args.replay_root),
        "days": args.days,
        "l1_policy": "strict_l1_at_or_before_trigger_ts_ms",
        "event_index_policy": "strict_cache_trigger_index_shared_replay_load",
        "truth_policy": "raw replay SQLite verification with shared day/condition range loads across selected ranks",
        "day_stats": day_stats,
        "aggregate": strict.aggregate(rows),
    }
    finalist_dir = args.output_dir / f"finalist_{rank:02d}"
    finalist_dir.mkdir(parents=True, exist_ok=True)
    strict.write_csv(finalist_dir / "taker_buy_shared_replay_verification_rows.csv", rows)
    (finalist_dir / "taker_buy_shared_replay_verification_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (finalist_dir / "taker_buy_shared_replay_verification_report.md").write_text(strict.render_report(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--candidate-cache-csv", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--days", default=strict.DEFAULT_DAYS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rank", type=int, action="append")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--max-l1-age-ms", type=int, default=3000)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--progress-every-conditions", type=int, default=25)
    args = parser.parse_args()

    started = time.perf_counter()
    days = [part.strip() for part in args.days.split(",") if part.strip()]
    search_rows = strict.read_search_rows(args.search_results_csv)
    selected_ranks = set(args.rank or range(1, min(args.top_n, len(search_rows)) + 1))
    selected: list[tuple[int, strict.Params]] = []
    for rank, row in enumerate(search_rows, start=1):
        if rank in selected_ranks:
            selected.append((rank, strict.row_to_params(rank, row)))
    cache_by_day = strict.read_candidate_cache_rows(args.candidate_cache_csv)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rank_events, union_events = build_rank_event_index(selected, cache_by_day, days)
    rank_rows: dict[int, list[dict[str, Any]]] = {rank: [] for rank, _ in selected}
    rank_day_stats: dict[int, list[dict[str, Any]]] = {rank: [] for rank, _ in selected}
    load_stats = []

    for day in days:
        day_started = time.perf_counter()
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            for rank, _ in selected:
                rank_day_stats[rank].append({"day": day, "status": "missing_db", "rows": 0})
            continue
        day_bundles: dict[str, ConditionBundle] = {}
        condition_items = sorted(union_events.get(day, {}).items())
        with batched.ro_connect(db_path) as conn:
            total_conditions = len(condition_items)
            for idx, (condition_id, events) in enumerate(condition_items, start=1):
                bundle = load_condition_bundle(conn, day, condition_id, events, args)
                if bundle is not None:
                    day_bundles[condition_id] = bundle
                    load_stats.append(
                        {
                            "day": day,
                            "condition_id": condition_id,
                            "events": len(events),
                            "trades": len(bundle.trades_by_key),
                            "l1_rows": len(bundle.l1_rows),
                            "l2_rows": sum(len(rows) for _keys, rows in bundle.l2_by_side.values()),
                            "elapsed_s": bundle.load_elapsed_s,
                        }
                    )
                if args.progress and (idx == total_conditions or idx % args.progress_every_conditions == 0):
                    print(
                        json.dumps(
                            {
                                "stage": "load_replay",
                                "day": day,
                                "processed_conditions": idx,
                                "total_conditions": total_conditions,
                                "loaded_conditions": len(day_bundles),
                                "elapsed_s": round(time.perf_counter() - day_started, 3),
                            }
                        ),
                        flush=True,
                    )
        for rank, params in selected:
            sim_started = time.perf_counter()
            rows_before = len(rank_rows[rank])
            day_conditions = rank_events.get(rank, {}).get(day, {})
            for condition_id, events in sorted(day_conditions.items()):
                bundle = day_bundles.get(condition_id)
                if bundle is None:
                    continue
                rank_rows[rank].extend(simulate_loaded_condition(day, condition_id, events, bundle, params, args))
            day_rows = len(rank_rows[rank]) - rows_before
            rank_day_stats[rank].append(
                {
                    "day": day,
                    "status": "ok",
                    "candidate_conditions": len(day_conditions),
                    "candidate_events": sum(len(v) for v in day_conditions.values()),
                    "rows": day_rows,
                    "elapsed_s": round(time.perf_counter() - sim_started, 3),
                }
            )
        if args.progress:
            print(
                json.dumps(
                    {
                        "stage": "simulate_day",
                        "day": day,
                        "ranks": len(selected),
                        "elapsed_s": round(time.perf_counter() - day_started, 3),
                    }
                ),
                flush=True,
            )

    results = []
    for rank, params in selected:
        report = write_rank_report(rank, params, rank_rows[rank], rank_day_stats[rank], args)
        aggregate = report["aggregate"]["all"]
        results.append(
            {
                "rank": rank,
                "finalist_dir": str(args.output_dir / f"finalist_{rank:02d}"),
                "cache_rows": params.cache_rows,
                "cache_pnl": params.cache_pnl,
                "cache_min_day_pnl": params.cache_min_day_pnl,
                "verified_rows": aggregate.get("rows"),
                "verified_pnl": aggregate.get("pnl"),
                "verified_roi_on_first_cost": aggregate.get("roi_on_first_cost"),
                "verified_l1_age_p90_ms": aggregate.get("strict_l1_age_ms", {}).get("p90"),
            }
        )

    summary = {
        "generated_at_utc": utc_now(),
        "search_results_csv": str(args.search_results_csv),
        "candidate_cache_csv": str(args.candidate_cache_csv),
        "replay_root": str(args.replay_root),
        "days": args.days,
        "selected_ranks": [rank for rank, _ in selected],
        "truth_policy": "raw replay SQLite shared day/condition range verification",
        "elapsed_s": round(time.perf_counter() - started, 3),
        "load_stats": load_stats,
        "results": results,
    }
    (args.output_dir / "taker_buy_shared_replay_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(results), "elapsed_s": summary["elapsed_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Batched SQLite finalist verifier for taker-BUY strict-L1 candidates.

This keeps replay SQLite as the source of truth but changes the access pattern:
candidate events are grouped by day/condition, and L1/L2 rows are loaded in
time ranges once per condition instead of with per-event SQL lookups.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import verify_taker_buy_search_finalists_strict as strict


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def key_float(value: Any) -> float:
    return round(float(value), 9)


def read_cache_events(day_rows: list[dict[str, str]], params: strict.Params) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in day_rows:
        if not strict.cache_row_matches(row, params):
            continue
        condition_id = str(row["condition_id"])
        grouped[condition_id].append(
            {
                "condition_id": condition_id,
                "trigger_ts_ms": int(float(row["trigger_ts_ms"])),
                "first_side": str(row["first_side"]),
                "public_trade_price": strict.parse_float(row.get("public_trade_price"), "public_trade_price"),
                "public_trade_size": strict.parse_float(row.get("public_trade_size"), "public_trade_size"),
            }
        )
    for rows in grouped.values():
        rows.sort(key=lambda row: (int(row["trigger_ts_ms"]), str(row["first_side"]), float(row["public_trade_price"]), float(row["public_trade_size"])))
    return grouped


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = strict.ro_connect(path)
    conn.execute("PRAGMA mmap_size = 1073741824")
    return conn


def fetch_market(conn: sqlite3.Connection, condition_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.condition_id = ?
          AND m.symbol = 'BTC'
          AND m.interval_sec = 300
          AND s.winner_side IN ('YES', 'NO')
        """,
        (condition_id,),
    ).fetchone()


def resolve_trades(conn: sqlite3.Connection, condition_id: str, events: list[dict[str, Any]], market: sqlite3.Row) -> list[dict[str, Any]]:
    if not events:
        return []
    min_ts = min(int(row["trigger_ts_ms"]) for row in events)
    max_ts = max(int(row["trigger_ts_ms"]) for row in events)
    rows = conn.execute(
        """
        SELECT id AS trade_row_id, condition_id, trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND taker_side = 'BUY'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, min_ts, max_ts),
    ).fetchall()
    by_key: dict[tuple[int, str, float, float], list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_key[(int(row["trade_ts_ms"]), str(row["market_side"]), key_float(row["price"]), key_float(row["size"]))].append(row)

    out = []
    for event in events:
        key = (
            int(event["trigger_ts_ms"]),
            str(event["first_side"]),
            key_float(event["public_trade_price"]),
            key_float(event["public_trade_size"]),
        )
        matches = by_key.get(key) or []
        if not matches:
            raise RuntimeError(f"cache trigger missing in replay: condition_id={condition_id} key={key}")
        row = matches[0]
        out.append(
            {
                "trade_row_id": int(row["trade_row_id"]),
                "condition_id": condition_id,
                "trade_ts_ms": int(row["trade_ts_ms"]),
                "market_side": str(row["market_side"]),
                "price": float(row["price"]),
                "size": float(row["size"]),
                "slug": market["slug"],
                "start_ms": int(market["start_ms"]),
                "end_ms": int(market["end_ms"]),
                "winner_side": market["winner_side"],
            }
        )
    out.sort(key=lambda row: (int(row["trade_ts_ms"]), int(row["trade_row_id"])))
    return out


def load_l1_rows(conn: sqlite3.Connection, condition_id: str, min_ts: int, max_ts: int, max_age_ms: int) -> tuple[list[tuple[int, int, int]], list[sqlite3.Row]]:
    rows = conn.execute(
        """
        SELECT id, recv_ms, capture_seq, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, capture_seq, id
        """,
        (condition_id, min_ts - max_age_ms, max_ts),
    ).fetchall()
    keys = [(int(row["recv_ms"]), int(row["capture_seq"]), int(row["id"])) for row in rows]
    return keys, rows


def l1_at(keys: list[tuple[int, int, int]], rows: list[sqlite3.Row], ts_ms: int, max_age_ms: int) -> dict[str, Any] | None:
    idx = bisect.bisect_right(keys, (ts_ms, 10**18, 10**18)) - 1
    if idx < 0:
        return None
    row = rows[idx]
    age_ms = ts_ms - int(row["recv_ms"])
    if age_ms < 0 or age_ms > max_age_ms:
        return None
    return {
        "recv_ms": int(row["recv_ms"]),
        "age_ms": age_ms,
        "YES": {"bid": row["yes_bid_px"], "ask": row["yes_ask_px"], "bid_sz": row["yes_bid_sz"], "ask_sz": row["yes_ask_sz"]},
        "NO": {"bid": row["no_bid_px"], "ask": row["no_ask_px"], "bid_sz": row["no_bid_sz"], "ask_sz": row["no_ask_sz"]},
    }


def load_l2_rows(conn: sqlite3.Connection, condition_id: str, min_ts: int, max_ts: int, max_l2_age_ms: int, completion_s: int) -> dict[str, tuple[list[tuple[int, int]], list[sqlite3.Row]]]:
    rows = conn.execute(
        """
        SELECT id, recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
          AND market_side IN ('YES', 'NO')
        ORDER BY market_side, recv_ms, id
        """,
        (condition_id, min_ts - max_l2_age_ms, max_ts + completion_s * 1000),
    ).fetchall()
    by_side: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        by_side[str(row["market_side"])].append(row)
    out = {}
    for side, side_rows in by_side.items():
        out[side] = ([(int(row["recv_ms"]), int(row["id"])) for row in side_rows], side_rows)
    return out


def latest_l2_sweep_cached(
    l2_by_side: dict[str, tuple[list[tuple[int, int]], list[sqlite3.Row]]],
    side: str,
    ts_ms: int,
    clip: float,
    max_age_ms: int,
) -> tuple[float | None, int | None, float | None, float | None]:
    keys, rows = l2_by_side.get(side, ([], []))
    idx = bisect.bisect_right(keys, (ts_ms, 10**18)) - 1
    if idx < 0:
        return None, None, None, None
    row = rows[idx]
    age_ms = ts_ms - int(row["recv_ms"])
    if age_ms < 0 or age_ms > max_age_ms:
        return None, age_ms, None, None
    vwap, filled, worst = strict.sweep_vwap(strict.ask_levels(row), clip)
    if vwap is None:
        return None, age_ms, worst, filled
    return vwap, age_ms, worst, filled


def completion_cached(
    l2_by_side: dict[str, tuple[list[tuple[int, int]], list[sqlite3.Row]]],
    side: str,
    start_ms: int,
    end_ms: int,
    first_price: float,
    clip: float,
    pair_ceiling: float,
) -> dict[str, Any] | None:
    keys, rows = l2_by_side.get(side, ([], []))
    idx = bisect.bisect_left(keys, (start_ms, -1))
    while idx < len(rows):
        row = rows[idx]
        recv_ms = int(row["recv_ms"])
        if recv_ms > end_ms:
            break
        vwap, filled, worst = strict.sweep_vwap(strict.ask_levels(row), clip)
        if vwap is not None:
            pair_cost = first_price + vwap
            if pair_cost <= pair_ceiling + 1e-9:
                return {
                    "completion_ts_ms": recv_ms,
                    "completion_vwap": vwap,
                    "completion_worst_px": worst,
                    "completion_filled": filled,
                    "completion_delay_s": (recv_ms - start_ms) / 1000.0,
                    "pair_cost": pair_cost,
                }
        idx += 1
    return None


def simulate_condition(
    conn: sqlite3.Connection,
    day: str,
    condition_id: str,
    events: list[dict[str, Any]],
    params: strict.Params,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    market = fetch_market(conn, condition_id)
    if market is None:
        return []
    trades = resolve_trades(conn, condition_id, events, market)
    if not trades:
        return []
    min_ts = min(int(row["trade_ts_ms"]) for row in trades)
    max_ts = max(int(row["trade_ts_ms"]) for row in trades)
    l1_keys, l1_rows = load_l1_rows(conn, condition_id, min_ts, max_ts, args.max_l1_age_ms)
    l2_by_side = load_l2_rows(conn, condition_id, min_ts, max_ts, args.max_l2_age_ms, args.completion_s)

    out: list[dict[str, Any]] = []
    market_start = int(market["start_ms"])
    market_end = int(market["end_ms"])
    cursor_ms = max(market_start, strict.TRUSTED_START_MS) + params.offset_lo * 1000
    for trade in trades:
        ts_ms = int(trade["trade_ts_ms"])
        if ts_ms < cursor_ms:
            continue
        side = str(trade["market_side"])
        book = l1_at(l1_keys, l1_rows, ts_ms, args.max_l1_age_ms)
        if book is None:
            continue
        alignment = strict.side_alignment(book, side)
        if alignment is None:
            continue
        if params.side_alignment != "any" and alignment != params.side_alignment:
            continue
        first_price, first_age_ms, first_worst_px, first_filled = latest_l2_sweep_cached(
            l2_by_side, side, ts_ms, args.clip, args.max_l2_age_ms
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
        completion = completion_cached(
            l2_by_side,
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


def verify_one(params: strict.Params, args: argparse.Namespace, finalist_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    day_stats = []
    cache_by_day = args._candidate_cache_by_day
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        started = time.perf_counter()
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_stats.append({"day": day, "status": "missing_db", "rows": 0})
            continue
        grouped = read_cache_events(cache_by_day.get(day, []), params)
        day_rows: list[dict[str, Any]] = []
        with ro_connect(db_path) as conn:
            total = len(grouped)
            for idx, (condition_id, events) in enumerate(sorted(grouped.items()), start=1):
                day_rows.extend(simulate_condition(conn, day, condition_id, events, params, args))
                if args.progress and (idx == total or idx % args.progress_every_conditions == 0):
                    print(
                        json.dumps(
                            {
                                "rank": params.rank,
                                "day": day,
                                "processed_conditions": idx,
                                "total_conditions": total,
                                "rows": len(day_rows),
                                "elapsed_s": round(time.perf_counter() - started, 3),
                            }
                        ),
                        flush=True,
                    )
        rows.extend(day_rows)
        day_stats.append(
            {
                "day": day,
                "status": "ok",
                "candidate_conditions": len(grouped),
                "candidate_events": sum(len(v) for v in grouped.values()),
                "rows": len(day_rows),
                "elapsed_s": round(time.perf_counter() - started, 3),
            }
        )
    rows.sort(key=lambda row: (str(row["condition_id"]), int(row["trigger_ts_ms"])))
    report = {
        "generated_at_utc": utc_now(),
        "rank": params.rank,
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
        "event_index_policy": "strict_cache_trigger_index_batched_by_condition",
        "truth_policy": "raw replay SQLite batched range verification; cache is used only as a trigger index",
        "day_stats": day_stats,
        "aggregate": strict.aggregate(rows),
    }
    finalist_dir.mkdir(parents=True, exist_ok=True)
    strict.write_csv(finalist_dir / "taker_buy_batched_sqlite_verification_rows.csv", rows)
    (finalist_dir / "taker_buy_batched_sqlite_verification_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (finalist_dir / "taker_buy_batched_sqlite_verification_report.md").write_text(strict.render_report(report), encoding="utf-8")
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

    search_rows = strict.read_search_rows(args.search_results_csv)
    args._candidate_cache_by_day = strict.read_candidate_cache_rows(args.candidate_cache_csv)
    selected_ranks = set(args.rank or range(1, min(args.top_n, len(search_rows)) + 1))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for rank, row in enumerate(search_rows, start=1):
        if rank not in selected_ranks:
            continue
        params = strict.row_to_params(rank, row)
        finalist_dir = args.output_dir / f"finalist_{rank:02d}"
        report = verify_one(params, args, finalist_dir)
        aggregate = report["aggregate"]["all"]
        results.append(
            {
                "rank": rank,
                "finalist_dir": str(finalist_dir),
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
        "selected_ranks": sorted(selected_ranks),
        "truth_policy": "raw replay SQLite batched range verification",
        "results": results,
    }
    (args.output_dir / "taker_buy_batched_sqlite_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

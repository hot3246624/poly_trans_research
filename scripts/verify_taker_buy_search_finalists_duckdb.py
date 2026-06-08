#!/usr/bin/env python3
"""Verify taker-BUY finalists against a Parquet/DuckDB replay verification store.

This is the fast verification-store path.  It verifies the same selected events
as ``verify_taker_buy_search_finalists_strict.py --candidate-cache-csv`` but
reads strict L1/L2/trade truth from a Parquet store built from replay SQLite.
Use the SQLite verifier for final pre-deploy audit; use this script to benchmark
and to remove SQLite/NFS random-lookups from routine finalist gating.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

import verify_taker_buy_search_finalists_strict as strict


REQUIRED_TABLES = ("market_meta", "settlement_records", "md_trades", "md_book_l1", "md_book_l2")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_dicts(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def connect_store(store_dir: Path, threads: int) -> duckdb.DuckDBPyConnection:
    manifest_path = store_dir / "STORE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing store manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    conn = duckdb.connect()
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    globs = manifest.get("outputs", {}).get("table_parquet_globs", {})
    for table in REQUIRED_TABLES:
        rel_glob = globs.get(table) or f"dataset/{table}/**/*.parquet"
        full_glob = store_dir / str(rel_glob)
        conn.execute(
            f"""
            CREATE VIEW {table} AS
            SELECT * FROM read_parquet({quote_literal(full_glob)}, hive_partitioning=true)
            """
        )
    return conn


def parse_candidate_rows(cache_by_day: dict[str, list[dict[str, str]]], day: str, params: strict.Params) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in cache_by_day.get(day, []):
        if not strict.cache_row_matches(row, params):
            continue
        out.append(
            {
                "event_idx": len(out) + 1,
                "day": day,
                "condition_id": str(row["condition_id"]),
                "trigger_ts_ms": int(float(row["trigger_ts_ms"])),
                "first_side": str(row["first_side"]),
                "public_trade_price": strict.parse_float(row.get("public_trade_price"), "public_trade_price"),
                "public_trade_size": strict.parse_float(row.get("public_trade_size"), "public_trade_size"),
            }
        )
    return out


def load_candidates(conn: duckdb.DuckDBPyConnection, candidates: list[dict[str, Any]]) -> None:
    conn.execute("DROP TABLE IF EXISTS candidate_events")
    conn.execute(
        """
        CREATE TEMP TABLE candidate_events (
            event_idx BIGINT,
            day VARCHAR,
            condition_id VARCHAR,
            trigger_ts_ms BIGINT,
            first_side VARCHAR,
            public_trade_price DOUBLE,
            public_trade_size DOUBLE
        )
        """
    )
    if not candidates:
        return
    conn.executemany(
        """
        INSERT INTO candidate_events
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["event_idx"],
                row["day"],
                row["condition_id"],
                row["trigger_ts_ms"],
                row["first_side"],
                row["public_trade_price"],
                row["public_trade_size"],
            )
            for row in candidates
        ],
    )


def resolve_trades(conn: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    conn.execute("DROP TABLE IF EXISTS candidate_resolved")
    conn.execute(
        """
        CREATE TEMP TABLE candidate_resolved AS
        SELECT * EXCLUDE (rn) FROM (
            SELECT
                c.event_idx,
                c.day,
                t.id AS trade_row_id,
                t.condition_id,
                t.trade_ts_ms,
                t.market_side,
                t.price,
                t.size,
                m.slug,
                m.start_ms,
                m.end_ms,
                s.winner_side,
                row_number() OVER (PARTITION BY c.event_idx ORDER BY t.id) AS rn
            FROM candidate_events c
            JOIN md_trades t
              ON t.day = c.day
             AND t.condition_id = c.condition_id
             AND t.trade_ts_ms = c.trigger_ts_ms
             AND t.market_side = c.first_side
             AND t.taker_side = 'BUY'
             AND abs(t.price - c.public_trade_price) <= 1e-9
             AND abs(t.size - c.public_trade_size) <= 1e-9
            JOIN market_meta m
              ON m.day = c.day
             AND m.condition_id = t.condition_id
            JOIN settlement_records s
              ON s.day = c.day
             AND s.condition_id = t.condition_id
             AND s.winner_side IN ('YES', 'NO')
        ) q
        WHERE rn = 1
        """
    )
    return fetch_dicts(conn.execute("SELECT * FROM candidate_resolved ORDER BY event_idx"))


def load_strict_l1(conn: duckdb.DuckDBPyConnection, max_age_ms: int) -> dict[int, dict[str, Any]]:
    rows = fetch_dicts(
        conn.execute(
            """
            SELECT * EXCLUDE (rn) FROM (
                SELECT
                    c.event_idx,
                    l.recv_ms,
                    l.yes_bid_px,
                    l.yes_ask_px,
                    l.no_bid_px,
                    l.no_ask_px,
                    l.yes_bid_sz,
                    l.yes_ask_sz,
                    l.no_bid_sz,
                    l.no_ask_sz,
                    row_number() OVER (
                        PARTITION BY c.event_idx
                        ORDER BY l.recv_ms DESC, l.capture_seq DESC, l.id DESC
                    ) AS rn
                FROM candidate_resolved c
                JOIN md_book_l1 l
                  ON l.day = c.day
                 AND l.condition_id = c.condition_id
                 AND l.recv_ms <= c.trade_ts_ms
                 AND l.recv_ms >= c.trade_ts_ms - ?
            ) q
            WHERE rn = 1
            ORDER BY event_idx
            """,
            [max_age_ms],
        )
    )
    out = {}
    for row in rows:
        event_idx = int(row["event_idx"])
        out[event_idx] = {
            "recv_ms": int(row["recv_ms"]),
            "YES": {
                "bid": row["yes_bid_px"],
                "ask": row["yes_ask_px"],
                "bid_sz": row["yes_bid_sz"],
                "ask_sz": row["yes_ask_sz"],
            },
            "NO": {
                "bid": row["no_bid_px"],
                "ask": row["no_ask_px"],
                "bid_sz": row["no_bid_sz"],
                "ask_sz": row["no_ask_sz"],
            },
        }
    return out


def load_first_l2(conn: duckdb.DuckDBPyConnection, max_age_ms: int, clip: float) -> dict[int, dict[str, Any]]:
    rows = fetch_dicts(
        conn.execute(
            """
            SELECT * EXCLUDE (rn) FROM (
                SELECT
                    c.event_idx,
                    l.recv_ms,
                    l.ask1_px, l.ask1_sz,
                    l.ask2_px, l.ask2_sz,
                    l.ask3_px, l.ask3_sz,
                    l.ask4_px, l.ask4_sz,
                    l.ask5_px, l.ask5_sz,
                    row_number() OVER (
                        PARTITION BY c.event_idx
                        ORDER BY l.recv_ms DESC, l.id DESC
                    ) AS rn
                FROM candidate_resolved c
                JOIN md_book_l2 l
                  ON l.day = c.day
                 AND l.condition_id = c.condition_id
                 AND l.market_side = c.market_side
                 AND l.recv_ms <= c.trade_ts_ms
                 AND l.recv_ms >= c.trade_ts_ms - ?
            ) q
            WHERE rn = 1
            ORDER BY event_idx
            """,
            [max_age_ms],
        )
    )
    out = {}
    for row in rows:
        vwap, filled, worst = strict.sweep_vwap(strict.ask_levels(row), clip)
        out[int(row["event_idx"])] = {
            "recv_ms": int(row["recv_ms"]),
            "vwap": vwap,
            "filled": filled,
            "worst": worst,
        }
    return out


def load_completion_l2(conn: duckdb.DuckDBPyConnection, completion_s: int) -> dict[int, list[dict[str, Any]]]:
    rows = fetch_dicts(
        conn.execute(
            """
            SELECT
                c.event_idx,
                l.recv_ms,
                l.ask1_px, l.ask1_sz,
                l.ask2_px, l.ask2_sz,
                l.ask3_px, l.ask3_sz,
                l.ask4_px, l.ask4_sz,
                l.ask5_px, l.ask5_sz
            FROM candidate_resolved c
            JOIN md_book_l2 l
              ON l.day = c.day
             AND l.condition_id = c.condition_id
             AND l.market_side = CASE WHEN c.market_side = 'YES' THEN 'NO' ELSE 'YES' END
             AND l.recv_ms >= c.trade_ts_ms
             AND l.recv_ms <= least(c.end_ms, c.trade_ts_ms + ?)
            ORDER BY c.event_idx, l.recv_ms, l.id
            """,
            [completion_s * 1000],
        )
    )
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        out[int(row["event_idx"])].append(row)
    return out


def first_completion_from_rows(
    rows: list[dict[str, Any]],
    start_ms: int,
    first_price: float,
    clip: float,
    pair_ceiling: float,
) -> dict[str, Any] | None:
    for row in rows:
        vwap, filled, worst = strict.sweep_vwap(strict.ask_levels(row), clip)
        if vwap is None:
            continue
        pair_cost = first_price + vwap
        if pair_cost <= pair_ceiling + 1e-9:
            recv_ms = int(row["recv_ms"])
            return {
                "completion_ts_ms": recv_ms,
                "completion_vwap": vwap,
                "completion_worst_px": worst,
                "completion_filled": filled,
                "completion_delay_s": (recv_ms - start_ms) / 1000.0,
                "pair_cost": pair_cost,
            }
    return None


def simulate_day_duckdb(
    conn: duckdb.DuckDBPyConnection,
    day: str,
    params: strict.Params,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    candidates = parse_candidate_rows(args._candidate_cache_by_day, day, params)
    load_candidates(conn, candidates)
    if not candidates:
        return []
    resolved = resolve_trades(conn)
    if len(resolved) != len(candidates) and not args.allow_missing_cache_triggers:
        raise RuntimeError(f"{day}: cache trigger resolution mismatch candidates={len(candidates)} resolved={len(resolved)}")
    strict_l1 = load_strict_l1(conn, args.max_l1_age_ms)
    first_l2 = load_first_l2(conn, args.max_l2_age_ms, args.clip)
    completion_l2 = load_completion_l2(conn, args.completion_s)

    rows: list[dict[str, Any]] = []
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in resolved:
        by_condition[str(trade["condition_id"])].append(trade)

    for condition_id, condition_trades in by_condition.items():
        condition_trades.sort(key=lambda r: (int(r["trade_ts_ms"]), int(r["trade_row_id"])))
        first_trade = condition_trades[0]
        market_start = int(first_trade["start_ms"])
        market_end = int(first_trade["end_ms"])
        cursor_ms = max(market_start, strict.TRUSTED_START_MS) + params.offset_lo * 1000
        for trade in condition_trades:
            event_idx = int(trade["event_idx"])
            ts_ms = int(trade["trade_ts_ms"])
            if ts_ms < cursor_ms:
                continue
            side = str(trade["market_side"])
            book = strict_l1.get(event_idx)
            if book is None:
                continue
            age_ms = ts_ms - int(book["recv_ms"])
            if age_ms < 0 or age_ms > args.max_l1_age_ms:
                continue
            alignment = strict.side_alignment(book, side)
            if alignment is None:
                continue
            if params.side_alignment != "any" and alignment != params.side_alignment:
                continue

            first = first_l2.get(event_idx)
            if first is None or first["vwap"] is None:
                continue
            first_price = float(first["vwap"])
            first_age_ms = ts_ms - int(first["recv_ms"])
            if first_age_ms < 0 or first_age_ms > args.max_l2_age_ms:
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

            completion = first_completion_from_rows(
                completion_l2.get(event_idx, []),
                ts_ms,
                first_price,
                args.clip,
                params.pair_ceiling,
            )
            row: dict[str, Any] = {
                "day": day,
                "slug": first_trade["slug"],
                "condition_id": condition_id,
                "winner_side": first_trade["winner_side"],
                "trigger_ts_ms": ts_ms,
                "trigger_iso": strict.iso_ms(ts_ms),
                "offset_s": round((ts_ms - market_start) / 1000.0, 3),
                "first_side": side,
                "side_alignment": alignment,
                "first_is_winner": side == first_trade["winner_side"],
                "trigger_price": round(first_price, 6),
                "public_trade_price": round(float(trade["price"]), 6),
                "trigger_size": round(float(trade["size"]), 6),
                "first_price_source": "l2",
                "first_price_age_ms": first_age_ms,
                "first_worst_px": None if first["worst"] is None else round(float(first["worst"]), 6),
                "first_filled": None if first["filled"] is None else round(float(first["filled"]), 6),
                "clip": args.clip,
                "strict_l1_recv_ms": book["recv_ms"],
                "strict_l1_age_ms": age_ms,
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
                pnl = (1.0 - first_price) * args.clip if side == first_trade["winner_side"] else -first_price * args.clip
                row["pnl"] = round(pnl, 6)
            rows.append(row)
            if completion is None and params.block_after_residual:
                break
            if completion is not None:
                cursor_ms = ts_ms + int(float(completion["completion_delay_s"]) * 1000) + params.cooldown_s * 1000
            else:
                cursor_ms = ts_ms + params.cooldown_s * 1000
    rows.sort(key=lambda r: (str(r["condition_id"]), int(r["trigger_ts_ms"])))
    return rows


def verify_one(
    conn: duckdb.DuckDBPyConnection,
    params: strict.Params,
    args: argparse.Namespace,
    finalist_dir: Path,
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    day_stats = []
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        started = time.perf_counter()
        day_rows = simulate_day_duckdb(conn, day, params, args)
        elapsed = round(time.perf_counter() - started, 3)
        rows.extend(day_rows)
        day_stats.append({"day": day, "status": "ok", "rows": len(day_rows), "elapsed_s": elapsed})
        if args.progress:
            print(json.dumps({"rank": params.rank, "day": day, "rows": len(day_rows), "elapsed_s": elapsed}), flush=True)

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
        "verification_store_dir": str(args.verification_store_dir),
        "days": args.days,
        "l1_policy": "strict_l1_at_or_before_trigger_ts_ms",
        "event_index_policy": "strict_cache_trigger_index",
        "truth_policy": "Parquet/DuckDB verification store built from replay SQLite; final audit still traces to replay_published",
        "day_stats": day_stats,
        "aggregate": strict.aggregate(rows),
    }
    finalist_dir.mkdir(parents=True, exist_ok=True)
    strict.write_csv(finalist_dir / "taker_buy_duckdb_verification_rows.csv", rows)
    (finalist_dir / "taker_buy_duckdb_verification_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (finalist_dir / "taker_buy_duckdb_verification_report.md").write_text(strict.render_report(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--candidate-cache-csv", type=Path, required=True)
    parser.add_argument("--verification-store-dir", type=Path, required=True)
    parser.add_argument("--days", default=strict.DEFAULT_DAYS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rank", type=int, action="append", help="1-based search-result rank to verify; may be repeated.")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--max-l1-age-ms", type=int, default=3000)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--duckdb-threads", type=int, default=4)
    parser.add_argument("--allow-missing-cache-triggers", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    search_rows = strict.read_search_rows(args.search_results_csv)
    args._candidate_cache_by_day = strict.read_candidate_cache_rows(args.candidate_cache_csv)
    selected_ranks = set(args.rank or range(1, min(args.top_n, len(search_rows)) + 1))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    conn = connect_store(args.verification_store_dir, args.duckdb_threads)
    try:
        results = []
        for rank, row in enumerate(search_rows, start=1):
            if rank not in selected_ranks:
                continue
            params = strict.row_to_params(rank, row)
            finalist_dir = args.output_dir / f"finalist_{rank:02d}"
            report = verify_one(conn, params, args, finalist_dir)
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
    finally:
        conn.close()

    summary = {
        "generated_at_utc": utc_now(),
        "search_results_csv": str(args.search_results_csv),
        "candidate_cache_csv": str(args.candidate_cache_csv),
        "verification_store_dir": str(args.verification_store_dir),
        "days": args.days,
        "selected_ranks": sorted(selected_ranks),
        "truth_policy": "Parquet/DuckDB verification store built from replay SQLite; final audit still traces to replay_published",
        "results": results,
    }
    (args.output_dir / "taker_buy_duckdb_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

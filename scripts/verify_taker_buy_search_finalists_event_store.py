#!/usr/bin/env python3
"""Fast finalist verifier over taker-BUY event verification store.

This is the routine final-gate backend.  The event store is built from a
strict-L1 V1 cache that has already been validated against replay SQLite.  For
independent audits, compare selected jobs with
``verify_taker_buy_search_finalists_shared_replay.py``.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import verify_taker_buy_search_finalists_strict as strict  # noqa: E402


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def utc_now() -> str:
    return strict.utc_now()


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def connect_store(store_dir: Path, threads: int) -> duckdb.DuckDBPyConnection:
    manifest_path = store_dir / "EVENT_STORE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing event store manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    if manifest.get("source_cache", {}).get("validation_error_count") not in (0, "0"):
        raise RuntimeError(f"event store source validation is not clean: {manifest_path}")
    db_path = store_dir / str(manifest.get("outputs", {}).get("duckdb", "event_store.duckdb"))
    if db_path.is_file():
        conn = duckdb.connect(str(db_path), read_only=True)
        conn.execute(f"PRAGMA threads={max(1, int(threads))}")
        return conn
    parquet_glob = store_dir / str(manifest.get("outputs", {}).get("parquet_glob", "dataset/**/*.parquet"))
    conn = duckdb.connect()
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    conn.execute(
        f"""
        CREATE VIEW taker_buy_verification_events AS
        SELECT * FROM read_parquet({quote_literal(parquet_glob)}, hive_partitioning=true)
        """
    )
    return conn


def fetch_dicts(cur: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    cols = [desc[0] for desc in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def load_matching_events(conn: duckdb.DuckDBPyConnection, days: list[str], params: strict.Params) -> list[dict[str, Any]]:
    rows = fetch_dicts(
        conn.execute(
            """
            SELECT *
            FROM taker_buy_verification_events
            WHERE day IN (SELECT UNNEST(?))
              AND public_trade_price >= ?
              AND public_trade_price < ?
              AND public_trade_size >= ?
              AND public_trade_size < ?
              AND first_l2_vwap >= ?
              AND first_l2_vwap < ?
              AND offset_s >= ?
              AND offset_s < ?
              AND l1_immediate_pair <= ?
              AND (? = 'any' OR side_alignment = ?)
            ORDER BY condition_id, trigger_ts_ms, event_store_row_id
            """,
            [
                days,
                params.price_lo,
                params.price_hi,
                params.size_lo,
                params.size_hi,
                params.first_lo,
                params.first_hi,
                params.offset_lo,
                params.offset_hi,
                params.max_l1_pair,
                params.side_alignment,
                params.side_alignment,
            ],
        )
    )
    return rows


def ceiling_key(ceiling: float) -> str:
    return f"ceil_{str(ceiling).replace('.', '_')}"


def event_completion(row: dict[str, Any], pair_ceiling: float) -> dict[str, Any] | None:
    key = ceiling_key(pair_ceiling)
    verify_key = f"verify_{key}"
    if f"{verify_key}_hit" in row:
        if not parse_bool(row.get(f"{verify_key}_hit")):
            return None
        pair_cost = parse_float(row.get(f"{verify_key}_pair_cost"))
        delay_s = parse_float(row.get(f"{verify_key}_delay_s"))
        vwap = parse_float(row.get(f"{verify_key}_vwap"))
        ts_ms = row.get(f"{verify_key}_ts_ms")
        worst_px = parse_float(row.get(f"{verify_key}_worst_px"))
        filled = parse_float(row.get(f"{verify_key}_filled"))
        if pair_cost is None or delay_s is None:
            return None
        trigger_ts_ms = int(row["trigger_ts_ms"])
        return {
            "completion_ts_ms": int(ts_ms) if ts_ms is not None else trigger_ts_ms + int(round(delay_s * 1000)),
            "completion_delay_s": delay_s,
            "completion_vwap": vwap,
            "completion_worst_px": worst_px,
            "completion_filled": filled,
            "pair_cost": pair_cost,
        }
    if not parse_bool(row.get(f"{key}_hit")):
        return None
    pair_cost = parse_float(row.get(f"{key}_pair_cost"))
    delay_s = parse_float(row.get(f"{key}_delay_s"))
    vwap = parse_float(row.get(f"{key}_vwap"))
    if pair_cost is None or delay_s is None:
        return None
    ts_ms = int(row["trigger_ts_ms"])
    return {
        "completion_ts_ms": ts_ms + int(round(delay_s * 1000)),
        "completion_delay_s": delay_s,
        "completion_vwap": vwap,
        "pair_cost": pair_cost,
    }


def event_to_output_row(row: dict[str, Any], params: strict.Params, completion: dict[str, Any] | None, clip: float) -> dict[str, Any]:
    ts_ms = int(row["trigger_ts_ms"])
    first_price = float(row.get("verify_first_l2_vwap") if row.get("verify_first_l2_vwap") is not None else row["first_l2_vwap"])
    side = str(row["first_side"])
    winner_side = str(row["winner_side"])
    out: dict[str, Any] = {
        "day": str(row["day"])[:10],
        "slug": row["slug"],
        "condition_id": row["condition_id"],
        "winner_side": winner_side,
        "trigger_ts_ms": ts_ms,
        "trigger_iso": strict.iso_ms(ts_ms),
        "offset_s": round(float(row["offset_s"]), 3),
        "first_side": side,
        "side_alignment": row.get("verify_side_alignment") or row["side_alignment"],
        "first_is_winner": side == winner_side,
        "trigger_price": round(first_price, 6),
        "public_trade_price": round(float(row["public_trade_price"]), 6),
        "trigger_size": round(float(row["public_trade_size"]), 6),
        "first_price_source": "event_store_l2",
        "first_price_age_ms": None
        if (row.get("verify_first_l2_age_ms") if row.get("verify_first_l2_age_ms") is not None else row.get("first_l2_age_ms")) is None
        else int(row.get("verify_first_l2_age_ms") if row.get("verify_first_l2_age_ms") is not None else row["first_l2_age_ms"]),
        "first_worst_px": None
        if (row.get("verify_first_l2_worst_px") if row.get("verify_first_l2_worst_px") is not None else row.get("first_l2_worst_px")) is None
        else round(float(row.get("verify_first_l2_worst_px") if row.get("verify_first_l2_worst_px") is not None else row["first_l2_worst_px"]), 6),
        "first_filled": None
        if (row.get("verify_first_l2_filled") if row.get("verify_first_l2_filled") is not None else row.get("first_l2_filled")) is None
        else round(float(row.get("verify_first_l2_filled") if row.get("verify_first_l2_filled") is not None else row["first_l2_filled"]), 6),
        "clip": clip,
        "strict_l1_recv_ms": None
        if (row.get("verify_strict_l1_recv_ms") if row.get("verify_strict_l1_recv_ms") is not None else row.get("strict_l1_recv_ms")) is None
        else int(row.get("verify_strict_l1_recv_ms") if row.get("verify_strict_l1_recv_ms") is not None else row["strict_l1_recv_ms"]),
        "strict_l1_age_ms": None
        if (row.get("verify_strict_l1_age_ms") if row.get("verify_strict_l1_age_ms") is not None else row.get("strict_l1_age_ms")) is None
        else int(row.get("verify_strict_l1_age_ms") if row.get("verify_strict_l1_age_ms") is not None else row["strict_l1_age_ms"]),
        "l1_immediate_pair": round(
            float(row.get("verify_l1_immediate_pair") if row.get("verify_l1_immediate_pair") is not None else row["l1_immediate_pair"]),
            6,
        ),
        "completion_fill": False,
        "status": "residual_settle",
    }
    if completion is not None:
        pnl = (1.0 - float(completion["pair_cost"])) * clip
        out.update(
            {
                "completion_fill": True,
                "completion_ts_ms": completion["completion_ts_ms"],
                "completion_iso": strict.iso_ms(int(completion["completion_ts_ms"])),
                "completion_delay_s": round(float(completion["completion_delay_s"]), 3),
                "completion_vwap": None
                if completion.get("completion_vwap") is None
                else round(float(completion["completion_vwap"]), 6),
                "completion_worst_px": None
                if completion.get("completion_worst_px") is None
                else round(float(completion["completion_worst_px"]), 6),
                "completion_filled": None
                if completion.get("completion_filled") is None
                else round(float(completion["completion_filled"]), 6),
                "pair_cost": round(float(completion["pair_cost"]), 6),
                "pnl": round(pnl, 6),
                "status": "closed",
            }
        )
    else:
        pnl = (1.0 - first_price) * clip if side == winner_side else -first_price * clip
        out["pnl"] = round(pnl, 6)
    return out


def simulate_events(events: list[dict[str, Any]], params: strict.Params, args: argparse.Namespace) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_condition[str(event["condition_id"])].append(event)
    for condition_id, condition_events in sorted(by_condition.items()):
        condition_events.sort(key=lambda row: (int(row["trigger_ts_ms"]), int(row.get("event_store_row_id") or 0)))
        cursor_ms = -1
        for event in condition_events:
            ts_ms = int(event["trigger_ts_ms"])
            if ts_ms < cursor_ms:
                continue
            completion = event_completion(event, params.pair_ceiling)
            rows.append(event_to_output_row(event, params, completion, args.clip))
            if completion is None and params.block_after_residual:
                break
            if completion is not None:
                cursor_ms = ts_ms + int(float(completion["completion_delay_s"]) * 1000) + params.cooldown_s * 1000
            else:
                cursor_ms = ts_ms + params.cooldown_s * 1000
    rows.sort(key=lambda row: (str(row["condition_id"]), int(row["trigger_ts_ms"])))
    return rows


def write_rank_report(rank: int, params: strict.Params, rows: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any]:
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
        },
        "cache_reference": {
            "rows": params.cache_rows,
            "pnl": params.cache_pnl,
            "min_day_pnl": params.cache_min_day_pnl,
        },
        "verification_event_store_dir": str(args.event_store_dir),
        "days": args.days,
        "truth_policy": "event store derived from strict-L1 replay-validated V1 cache; replay_published remains audit source",
        "aggregate": strict.aggregate(rows),
    }
    finalist_dir = args.output_dir / f"finalist_{rank:02d}"
    finalist_dir.mkdir(parents=True, exist_ok=True)
    strict.write_csv(finalist_dir / "taker_buy_event_store_verification_rows.csv", rows)
    (finalist_dir / "taker_buy_event_store_verification_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (finalist_dir / "taker_buy_event_store_verification_report.md").write_text(strict.render_report(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--event-store-dir", type=Path, required=True)
    parser.add_argument("--days", default=strict.DEFAULT_DAYS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rank", type=int, action="append")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--duckdb-threads", type=int, default=4)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    started = time.perf_counter()
    days = [part.strip() for part in args.days.split(",") if part.strip()]
    search_rows = strict.read_search_rows(args.search_results_csv)
    selected_ranks = set(args.rank or range(1, min(args.top_n, len(search_rows)) + 1))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    conn = connect_store(args.event_store_dir, args.duckdb_threads)
    results = []
    try:
        for rank, row in enumerate(search_rows, start=1):
            if rank not in selected_ranks:
                continue
            rank_started = time.perf_counter()
            params = strict.row_to_params(rank, row)
            events = load_matching_events(conn, days, params)
            rows = simulate_events(events, params, args)
            report = write_rank_report(rank, params, rows, args)
            aggregate = report["aggregate"]["all"]
            results.append(
                {
                    "rank": rank,
                    "finalist_dir": str(args.output_dir / f"finalist_{rank:02d}"),
                    "cache_rows": params.cache_rows,
                    "cache_pnl": params.cache_pnl,
                    "cache_min_day_pnl": params.cache_min_day_pnl,
                    "matched_events": len(events),
                    "verified_rows": aggregate.get("rows"),
                    "verified_pnl": aggregate.get("pnl"),
                    "verified_roi_on_first_cost": aggregate.get("roi_on_first_cost"),
                    "verified_l1_age_p90_ms": aggregate.get("strict_l1_age_ms", {}).get("p90"),
                    "elapsed_s": round(time.perf_counter() - rank_started, 3),
                }
            )
            if args.progress:
                print(json.dumps({"rank": rank, "matched_events": len(events), "verified_rows": aggregate.get("rows"), "elapsed_s": results[-1]["elapsed_s"]}), flush=True)
    finally:
        conn.close()

    summary = {
        "generated_at_utc": utc_now(),
        "search_results_csv": str(args.search_results_csv),
        "event_store_dir": str(args.event_store_dir),
        "days": args.days,
        "selected_ranks": sorted(selected_ranks),
        "truth_policy": "event store derived from strict-L1 replay-validated V1 cache; replay_published remains audit source",
        "elapsed_s": round(time.perf_counter() - started, 3),
        "results": results,
    }
    (args.output_dir / "taker_buy_event_store_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(results), "elapsed_s": summary["elapsed_s"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

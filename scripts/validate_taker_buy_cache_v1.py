#!/usr/bin/env python3
"""Validate a V1 taker-BUY cache against source replay SQLite.

This script checks both provenance and sampled feature correctness. It is meant
to run after publishing a cache and before agents treat it as a shared search
index.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import random
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
MODULE_DIRS = [
    SCRIPT_DIR,
    Path(os.environ["TAKER_BUY_MODULE_DIR"]) if os.environ.get("TAKER_BUY_MODULE_DIR") else None,
    Path("/home/ubuntu/poly_trans_research_ops/scripts"),
]
for module_dir in reversed([path for path in MODULE_DIRS if path is not None and path.exists()]):
    sys.path.insert(0, str(module_dir))

import build_taker_buy_signal_candidate_cache as base  # noqa: E402

try:
    import build_taker_buy_signal_candidate_cache_stream as stream_builder  # noqa: E402
except ImportError:  # pragma: no cover - fallback for local minimal installs
    stream_builder = None


STRING_FIELDS = {
    "day",
    "slug",
    "condition_id",
    "winner_side",
    "trigger_iso",
    "first_side",
    "side_alignment",
    "strict_side_alignment",
    "cache_side_alignment_old",
}
BOOL_FIELDS = {"first_is_winner"}
STRICT_L1_FIELDS = {
    "strict_l1_recv_ms",
    "strict_l1_age_ms",
    "strict_side_alignment",
    "strict_l1_immediate_pair",
}


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


def sqlite_sequence(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {str(name): int(seq) for name, seq in rows}


def load_manifest(cache_dir: Path) -> dict[str, Any]:
    path = cache_dir / "CACHE_MANIFEST.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_rows(cache_csv: Path) -> list[dict[str, str]]:
    with cache_csv.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def source_matches(manifest: dict[str, Any], replay_root: Path) -> list[str]:
    errors: list[str] = []
    for item in manifest.get("source_replay", []):
        day = str(item["day"])
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.is_file():
            errors.append(f"{day}: missing source db {db_path}")
            continue
        stat = db_path.stat()
        if int(item.get("size_bytes", -1)) != stat.st_size:
            errors.append(f"{day}: size changed manifest={item.get('size_bytes')} current={stat.st_size}")
        if int(item.get("mtime_ns", -1)) != stat.st_mtime_ns:
            errors.append(f"{day}: mtime changed manifest={item.get('mtime_ns')} current={stat.st_mtime_ns}")
        current_seq = sqlite_sequence(db_path)
        if item.get("sqlite_sequence") != current_seq:
            errors.append(f"{day}: sqlite_sequence changed")
    return errors


def fetch_market(conn: sqlite3.Connection, condition_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.condition_id = ?
        """,
        (condition_id,),
    ).fetchone()


def strict_l1_sql(conn: sqlite3.Connection, condition_id: str, ts_ms: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms <= ?
        ORDER BY recv_ms DESC, capture_seq DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()


def book_from_l1_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
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


def validate_strict_l1_row(
    conn: sqlite3.Connection,
    row: dict[str, str],
    tol: float,
    require_strict_l1: bool,
) -> list[str]:
    missing_fields = sorted(field for field in STRICT_L1_FIELDS if field not in row)
    if missing_fields:
        if require_strict_l1:
            return [f"{row.get('condition_id')} {row.get('trigger_ts_ms')}: missing strict L1 fields {missing_fields}"]
        return []

    errors: list[str] = []
    condition_id = str(row["condition_id"])
    ts_ms = int(float(row["trigger_ts_ms"]))
    side = str(row["first_side"])
    strict_row = strict_l1_sql(conn, condition_id, ts_ms)
    if strict_row is None:
        return [f"{condition_id} {ts_ms}: strict L1 book not found"]
    strict_book = book_from_l1_row(strict_row)
    strict_recv_ms = int(strict_book["recv_ms"])
    strict_age_ms = ts_ms - strict_recv_ms
    if int(float(row.get("strict_l1_recv_ms") or -1)) != strict_recv_ms:
        errors.append(f"{condition_id} {ts_ms}: strict_l1_recv_ms cache={row.get('strict_l1_recv_ms')} replay={strict_recv_ms}")
    if int(float(row.get("strict_l1_age_ms") or -1)) != strict_age_ms:
        errors.append(f"{condition_id} {ts_ms}: strict_l1_age_ms cache={row.get('strict_l1_age_ms')} replay={strict_age_ms}")

    strict_high = base.high_side(strict_book)
    if strict_high is None:
        errors.append(f"{condition_id} {ts_ms}: strict L1 high side unavailable")
        return errors
    strict_alignment = "high" if side == strict_high else "low"
    if row.get("strict_side_alignment") != strict_alignment:
        errors.append(f"{condition_id} {ts_ms}: strict_side_alignment cache={row.get('strict_side_alignment')} replay={strict_alignment}")
    if row.get("side_alignment") != strict_alignment:
        errors.append(f"{condition_id} {ts_ms}: side_alignment cache={row.get('side_alignment')} strict={strict_alignment}")

    first_vwap = parse_float(row.get("first_l2_vwap"))
    opp = base.other(side)
    opp_ask = strict_book[opp]["ask"]
    if first_vwap is None or opp_ask is None:
        errors.append(f"{condition_id} {ts_ms}: strict pair inputs unavailable")
        return errors
    strict_pair = first_vwap + float(opp_ask)
    if not close_enough(parse_float(row.get("strict_l1_immediate_pair")), strict_pair, tol):
        errors.append(f"{condition_id} {ts_ms}: strict_l1_immediate_pair cache={row.get('strict_l1_immediate_pair')} replay={strict_pair}")
    if not close_enough(parse_float(row.get("l1_immediate_pair")), strict_pair, tol):
        errors.append(f"{condition_id} {ts_ms}: l1_immediate_pair cache={row.get('l1_immediate_pair')} strict={strict_pair}")
    if not close_enough(parse_float(row.get("opp_l1_ask")), float(opp_ask), tol):
        errors.append(f"{condition_id} {ts_ms}: opp_l1_ask cache={row.get('opp_l1_ask')} strict={opp_ask}")
    return errors


def close_enough(a: float | None, b: float | None, tol: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(float(a) - float(b)) <= tol


def manifest_args(params: dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        min_trade_price=float(params.get("min_trade_price", 0.50)),
        max_trade_price=float(params.get("max_trade_price", 0.75)),
        min_trade_size=float(params.get("min_trade_size", 50.0)),
        max_trade_size=float(params.get("max_trade_size", 250.0)),
        min_offset_s=int(params.get("min_offset_s", 0)),
        max_offset_s=int(params.get("max_offset_s", 240)),
        completion_s=int(params.get("completion_s", 30)),
        max_l2_age_ms=int(params.get("max_l2_age_ms", 750)),
        clip=float(params.get("clip", 60.0)),
    )


def row_key(row: dict[str, Any]) -> tuple[str, int, str, float | None, float | None]:
    return (
        str(row["condition_id"]),
        int(float(row["trigger_ts_ms"])),
        str(row["first_side"]),
        None if parse_float(row.get("public_trade_price")) is None else round(float(parse_float(row.get("public_trade_price"))), 6),
        None if parse_float(row.get("public_trade_size")) is None else round(float(parse_float(row.get("public_trade_size"))), 6),
    )


def is_bool_field(name: str) -> bool:
    return name in BOOL_FIELDS or name.endswith("_hit")


def compare_field(name: str, cache_value: Any, expected_value: Any, tol: float) -> str | None:
    if name in STRING_FIELDS:
        cache_text = "" if cache_value is None else str(cache_value)
        expected_text = "" if expected_value is None else str(expected_value)
        if cache_text != expected_text:
            return f"{name} cache={cache_value} replay={expected_value}"
        return None
    if is_bool_field(name):
        if parse_bool(cache_value) != bool(expected_value):
            return f"{name} cache={cache_value} replay={expected_value}"
        return None
    cache_float = parse_float(cache_value)
    expected_float = parse_float(expected_value)
    if not close_enough(cache_float, expected_float, tol):
        return f"{name} cache={cache_value} replay={expected_value}"
    return None


def condition_l2(conn: sqlite3.Connection, args: argparse.Namespace, market: sqlite3.Row) -> dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]]:
    if stream_builder is not None:
        return stream_builder.load_condition_l2(conn, args, market)
    condition_id = str(market["condition_id"])
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    out = {}
    for side in ("YES", "NO"):
        out[(condition_id, side)] = base.load_l2_asks(
            conn,
            condition_id,
            side,
            max(start_ms, base.TRUSTED_START_MS),
            min(end_ms, start_ms + (args.max_offset_s + args.completion_s) * 1000),
        )
    return out


def validate_condition_rows(
    conn: sqlite3.Connection,
    condition_id: str,
    sample_rows: list[dict[str, str]],
    params: dict[str, Any],
    fieldnames: list[str],
    tol: float,
    require_strict_l1: bool,
) -> list[str]:
    market = fetch_market(conn, condition_id)
    if market is None:
        return [f"{condition_id}: missing market"]
    args = manifest_args(params)
    trades = base.load_trigger_trades(conn, condition_id, market, args)
    l2 = condition_l2(conn, args, market)
    expected_rows = base.market_candidates(conn, market, trades, l2, args)
    expected_by_key: dict[tuple[str, int, str, float | None, float | None], list[dict[str, Any]]] = defaultdict(list)
    for expected in expected_rows:
        expected_by_key[row_key(expected)].append(expected)

    errors: list[str] = []
    for cache_row in sample_rows:
        key = row_key(cache_row)
        matches = expected_by_key.get(key, [])
        if not matches:
            errors.append(f"{condition_id} {key[1]} {key[2]}: sampled cache row not regenerated from replay")
            continue
        expected = matches[0]
        for field in fieldnames:
            issue = compare_field(field, cache_row.get(field), expected.get(field), tol)
            if issue is not None:
                errors.append(f"{condition_id} {key[1]}: {issue}")
        errors.extend(validate_strict_l1_row(conn, cache_row, tol, require_strict_l1))
    return errors


def validate_row(
    conn: sqlite3.Connection,
    row: dict[str, str],
    params: dict[str, Any],
    tol: float,
    require_strict_l1: bool = False,
) -> list[str]:
    errors: list[str] = []
    condition_id = str(row["condition_id"])
    market = fetch_market(conn, condition_id)
    if market is None:
        return [f"{condition_id}: missing market"]
    ts_ms = int(float(row["trigger_ts_ms"]))
    side = str(row["first_side"])
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    clip = float(parse_float(row.get("clip")) or params.get("clip") or 60.0)
    max_l2_age_ms = int(params.get("max_l2_age_ms", 750))
    completion_s = int(params.get("completion_s", 30))
    max_offset_s = int(params.get("max_offset_s", 240))

    trade = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms = ?
          AND market_side = ?
          AND taker_side = 'BUY'
          AND ABS(price - ?) < 0.000001
          AND ABS(size - ?) < 0.000001
        LIMIT 1
        """,
        (
            condition_id,
            ts_ms,
            side,
            float(parse_float(row.get("public_trade_price")) or -1),
            float(parse_float(row.get("public_trade_size")) or -1),
        ),
    ).fetchone()
    if trade is None:
        errors.append(f"{condition_id} {ts_ms}: trigger trade not found")
    errors.extend(validate_strict_l1_row(conn, row, tol, require_strict_l1))

    strict_row = strict_l1_sql(conn, condition_id, ts_ms)
    if strict_row is None:
        errors.append(f"{condition_id} {ts_ms}: strict l1 book not found")
        return errors
    book = book_from_l1_row(strict_row)
    high = base.high_side(book)
    expected_alignment = "high" if side == high else "low"
    if row.get("side_alignment") != expected_alignment:
        errors.append(f"{condition_id} {ts_ms}: side_alignment cache={row.get('side_alignment')} replay={expected_alignment}")

    first_times, first_books = base.load_l2_asks(
        conn,
        condition_id,
        side,
        max(start_ms, base.TRUSTED_START_MS),
        min(end_ms, start_ms + (max_offset_s + completion_s) * 1000),
    )
    first_vwap, first_age_ms, first_worst, first_filled = base.latest_sweep(
        first_times,
        first_books,
        ts_ms,
        clip,
        max_l2_age_ms,
    )
    if not close_enough(first_vwap, parse_float(row.get("first_l2_vwap")), tol):
        errors.append(f"{condition_id} {ts_ms}: first_l2_vwap cache={row.get('first_l2_vwap')} replay={first_vwap}")
    if first_age_ms != int(float(row.get("first_l2_age_ms") or -1)):
        errors.append(f"{condition_id} {ts_ms}: first_l2_age_ms cache={row.get('first_l2_age_ms')} replay={first_age_ms}")
    if not close_enough(first_worst, parse_float(row.get("first_l2_worst_px")), tol):
        errors.append(f"{condition_id} {ts_ms}: first_l2_worst_px cache={row.get('first_l2_worst_px')} replay={first_worst}")
    if not close_enough(first_filled, parse_float(row.get("first_l2_filled")), tol):
        errors.append(f"{condition_id} {ts_ms}: first_l2_filled cache={row.get('first_l2_filled')} replay={first_filled}")
    if first_vwap is None:
        return errors

    opp = base.other(side)
    opp_ask = book[opp]["ask"]
    l1_pair = first_vwap + float(opp_ask) if opp_ask is not None else None
    if not close_enough(l1_pair, parse_float(row.get("l1_immediate_pair")), tol):
        errors.append(f"{condition_id} {ts_ms}: l1_immediate_pair cache={row.get('l1_immediate_pair')} replay={l1_pair}")

    opp_times, opp_books = base.load_l2_asks(
        conn,
        condition_id,
        opp,
        max(start_ms, base.TRUSTED_START_MS),
        min(end_ms, start_ms + (max_offset_s + completion_s) * 1000),
    )
    completion = base.completion_scan(opp_times, opp_books, ts_ms, min(end_ms, ts_ms + completion_s * 1000), first_vwap, clip)
    for key, expected in completion.items():
        if key.endswith("_hit"):
            if parse_bool(row.get(key)) != bool(expected):
                errors.append(f"{condition_id} {ts_ms}: {key} cache={row.get(key)} replay={expected}")
        elif not close_enough(parse_float(row.get(key)), parse_float(expected), tol):
            errors.append(f"{condition_id} {ts_ms}: {key} cache={row.get(key)} replay={expected}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--float-tol", type=float, default=0.00001)
    parser.add_argument("--allow-source-drift", action="store_true")
    parser.add_argument("--legacy-per-row", action="store_true")
    parser.add_argument("--require-strict-l1", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    manifest = load_manifest(args.cache_dir)
    cache_csv = args.cache_dir / str(manifest["outputs"]["csv"])
    rows = load_rows(cache_csv)
    fieldnames = list(manifest.get("outputs", {}).get("fieldnames") or (rows[0].keys() if rows else []))
    expected_rows = int(manifest["outputs"]["row_count"])
    errors: list[str] = []
    if len(rows) != expected_rows:
        errors.append(f"row_count mismatch csv={len(rows)} manifest={expected_rows}")
    source_errors = source_matches(manifest, args.replay_root)
    if source_errors and not args.allow_source_drift:
        errors.extend(source_errors)

    sample_size = min(args.samples, len(rows))
    rng = random.Random(args.seed)
    sample_rows = rng.sample(rows, sample_size) if sample_size else []
    by_day: dict[str, list[dict[str, str]]] = {}
    for row in sample_rows:
        by_day.setdefault(str(row["day"]), []).append(row)

    params = manifest.get("parameters", {})
    checked = 0
    total_conditions = sum(len({str(row["condition_id"]) for row in day_rows}) for day_rows in by_day.values())
    print(
        f"validation_start samples={sample_size} days={len(by_day)} conditions={total_conditions} legacy_per_row={args.legacy_per_row}",
        file=sys.stderr,
        flush=True,
    )
    for day, day_rows in sorted(by_day.items()):
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        with base.ro_connect(db_path) as conn:
            if args.legacy_per_row:
                for row in day_rows:
                    checked += 1
                    errors.extend(validate_row(conn, row, params, args.float_tol, args.require_strict_l1))
                    if args.progress_every > 0 and checked % args.progress_every == 0:
                        print(f"validated_rows={checked}/{sample_size}", file=sys.stderr, flush=True)
            else:
                by_condition: dict[str, list[dict[str, str]]] = {}
                for row in day_rows:
                    by_condition.setdefault(str(row["condition_id"]), []).append(row)
                print(
                    f"{day}: sample_rows={len(day_rows)} sample_conditions={len(by_condition)}",
                    file=sys.stderr,
                    flush=True,
                )
                for condition_id, condition_rows in sorted(by_condition.items()):
                    errors.extend(
                        validate_condition_rows(
                            conn,
                            condition_id,
                            condition_rows,
                            params,
                            fieldnames,
                            args.float_tol,
                            args.require_strict_l1,
                        )
                    )
                    checked += len(condition_rows)
                    if args.progress_every > 0 and checked % args.progress_every == 0:
                        print(f"validated_rows={checked}/{sample_size}", file=sys.stderr, flush=True)

    report = {
        "cache_dir": str(args.cache_dir),
        "replay_root": str(args.replay_root),
        "rows": len(rows),
        "samples_requested": args.samples,
        "samples_checked": checked,
        "validation_mode": "legacy_per_row" if args.legacy_per_row else "grouped_condition_regeneration",
        "require_strict_l1": args.require_strict_l1,
        "source_errors": source_errors,
        "error_count": len(errors),
        "errors": errors[:200],
    }
    output_path = args.cache_dir / "CACHE_VALIDATION.json"
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

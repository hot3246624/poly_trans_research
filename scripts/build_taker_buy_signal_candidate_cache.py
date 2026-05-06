#!/usr/bin/env python3
"""Build an offline candidate cache for the BTC 5m taker-BUY signal.

The cache is intentionally market-side only: read-only replay SQLite in,
candidate CSV out. It records enough L1/L2 features to run many gate searches
without repeatedly scanning SQLite.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any


TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PAIR_CEILINGS = (0.94, 0.95, 0.96, 0.98)


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def safe_round(value: float | None, ndigits: int = 6) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, ndigits)


def sweep_vwap(levels: list[tuple[float, float]], target_size: float) -> tuple[float | None, float, float | None]:
    filled = 0.0
    notional = 0.0
    worst_px = None
    for px, sz in levels:
        use = min(float(sz), target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * float(px)
        worst_px = float(px)
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst_px
    return None, filled, worst_px


def ask_levels(row: sqlite3.Row) -> list[tuple[float, float]]:
    out = []
    for idx in range(1, 6):
        px = row[f"ask{idx}_px"]
        sz = row[f"ask{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        out.append((float(px), float(sz)))
    return out


def load_markets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC' AND m.interval_sec = 300
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        if row["winner_side"] not in ("YES", "NO"):
            continue
        out.append(row)
    return out


def load_l1_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, Any]]:
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


def book_at(l1_by_sec: dict[int, dict[str, Any]], ts_ms: int) -> dict[str, Any] | None:
    sec = ts_ms // 1000
    for candidate in (sec, sec - 1, sec - 2):
        book = l1_by_sec.get(candidate)
        if book is not None:
            return book
    return None


def mid(book: dict[str, Any], side: str) -> float | None:
    bid = book[side]["bid"]
    ask = book[side]["ask"]
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def high_side(book: dict[str, Any]) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def load_trigger_trades(conn: sqlite3.Connection, condition_id: str, market: sqlite3.Row, args: argparse.Namespace) -> list[sqlite3.Row]:
    start_ms = max(int(market["start_ms"]), TRUSTED_START_MS) + args.min_offset_s * 1000
    end_ms = min(int(market["end_ms"]), int(market["start_ms"]) + args.max_offset_s * 1000)
    return conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND taker_side = 'BUY'
          AND market_side IN ('YES', 'NO')
          AND price >= ?
          AND price < ?
          AND size >= ?
          AND size < ?
        ORDER BY trade_ts_ms, id
        """,
        (
            condition_id,
            start_ms,
            end_ms,
            args.min_trade_price,
            args.max_trade_price,
            args.min_trade_size,
            args.max_trade_size,
        ),
    ).fetchall()


def load_day_trigger_trades(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[str, list[sqlite3.Row]]:
    """Load broad trigger candidates once per day instead of querying per market."""
    rows = conn.execute(
        """
        SELECT m.condition_id, t.trade_ts_ms, t.market_side, t.price, t.size
        FROM md_trades t
        JOIN market_meta m ON m.condition_id = t.condition_id
        WHERE m.symbol = 'BTC'
          AND m.interval_sec = 300
          AND t.trade_ts_ms IS NOT NULL
          AND t.taker_side = 'BUY'
          AND t.market_side IN ('YES', 'NO')
          AND t.price >= ?
          AND t.price < ?
          AND t.size >= ?
          AND t.size < ?
          AND t.trade_ts_ms >= (CASE WHEN m.start_ms > ? THEN m.start_ms ELSE ? END) + ?
          AND t.trade_ts_ms <= m.start_ms + ?
        ORDER BY m.condition_id, t.trade_ts_ms, t.id
        """,
        (
            args.min_trade_price,
            args.max_trade_price,
            args.min_trade_size,
            args.max_trade_size,
            TRUSTED_START_MS,
            TRUSTED_START_MS,
            args.min_offset_s * 1000,
            args.max_offset_s * 1000,
        ),
    ).fetchall()
    out: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        out.setdefault(str(row["condition_id"]), []).append(row)
    return out


def load_l2_asks(conn: sqlite3.Connection, condition_id: str, side: str, start_ms: int, end_ms: int) -> tuple[list[int], list[list[tuple[float, float]]]]:
    rows = conn.execute(
        """
        SELECT recv_ms, ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND market_side = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, id
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    times = []
    books = []
    for row in rows:
        levels = ask_levels(row)
        if not levels:
            continue
        times.append(int(row["recv_ms"]))
        books.append(levels)
    return times, books


def load_day_l2_asks(conn: sqlite3.Connection, args: argparse.Namespace) -> dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]]:
    """Preload the relevant BTC 5m L2 ask window once per day.

    Per-market L2 queries dominate runtime on large windows. A single ordered
    day scan is much faster and keeps the downstream market loop deterministic.
    """
    window_ms = (args.max_offset_s + args.completion_s) * 1000
    rows = conn.execute(
        """
        SELECT l.condition_id, l.market_side, l.recv_ms,
               l.ask1_px, l.ask1_sz, l.ask2_px, l.ask2_sz, l.ask3_px, l.ask3_sz,
               l.ask4_px, l.ask4_sz, l.ask5_px, l.ask5_sz
        FROM md_book_l2 l
        JOIN market_meta m ON m.condition_id = l.condition_id
        WHERE m.symbol = 'BTC'
          AND m.interval_sec = 300
          AND l.market_side IN ('YES', 'NO')
          AND l.recv_ms >= CASE WHEN m.start_ms > ? THEN m.start_ms ELSE ? END
          AND l.recv_ms <= CASE WHEN m.end_ms < m.start_ms + ? THEN m.end_ms ELSE m.start_ms + ? END
        ORDER BY l.condition_id, l.market_side, l.recv_ms, l.id
        """,
        (TRUSTED_START_MS, TRUSTED_START_MS, window_ms, window_ms),
    )
    out: dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]] = {}
    for row in rows:
        levels = ask_levels(row)
        if not levels:
            continue
        key = (str(row["condition_id"]), str(row["market_side"]))
        if key not in out:
            out[key] = ([], [])
        out[key][0].append(int(row["recv_ms"]))
        out[key][1].append(levels)
    return out


def latest_sweep(
    times: list[int],
    books: list[list[tuple[float, float]]],
    ts_ms: int,
    clip: float,
    max_age_ms: int,
) -> tuple[float | None, int | None, float | None, float | None]:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None, None, None, None
    age = ts_ms - times[idx]
    if age < 0 or age > max_age_ms:
        return None, None, None, None
    vwap, filled, worst = sweep_vwap(books[idx], clip)
    return vwap, age, worst, filled


def completion_scan(
    times: list[int],
    books: list[list[tuple[float, float]]],
    start_ms: int,
    end_ms: int,
    first_price: float,
    clip: float,
) -> dict[str, Any]:
    lo = bisect.bisect_left(times, start_ms)
    hi = bisect.bisect_right(times, end_ms)
    min_pair_cost = None
    min_pair_delay_s = None
    min_pair_vwap = None
    first_by_ceiling: dict[str, dict[str, Any] | None] = {str(c): None for c in PAIR_CEILINGS}
    for idx in range(lo, hi):
        vwap, _filled, worst = sweep_vwap(books[idx], clip)
        if vwap is None:
            continue
        pair_cost = first_price + vwap
        delay_s = (times[idx] - start_ms) / 1000.0
        if min_pair_cost is None or pair_cost < min_pair_cost:
            min_pair_cost = pair_cost
            min_pair_delay_s = delay_s
            min_pair_vwap = vwap
        for ceiling in PAIR_CEILINGS:
            key = str(ceiling)
            if first_by_ceiling[key] is None and pair_cost <= ceiling + 1e-9:
                first_by_ceiling[key] = {
                    "pair_cost": pair_cost,
                    "vwap": vwap,
                    "delay_s": delay_s,
                    "ts_ms": times[idx],
                    "worst_px": worst,
                }
    out: dict[str, Any] = {
        "min_pair_cost_30s": safe_round(min_pair_cost),
        "min_pair_delay_s": safe_round(min_pair_delay_s, 3),
        "min_pair_completion_vwap": safe_round(min_pair_vwap),
    }
    for ceiling in PAIR_CEILINGS:
        key = str(ceiling)
        hit = first_by_ceiling[key]
        prefix = f"ceil_{key.replace('.', '_')}"
        out[f"{prefix}_hit"] = hit is not None
        out[f"{prefix}_pair_cost"] = safe_round(None if hit is None else hit["pair_cost"])
        out[f"{prefix}_delay_s"] = safe_round(None if hit is None else hit["delay_s"], 3)
        out[f"{prefix}_vwap"] = safe_round(None if hit is None else hit["vwap"])
    return out


def market_candidates(
    conn: sqlite3.Connection,
    market: sqlite3.Row,
    trades: list[sqlite3.Row],
    day_l2: dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]] | None,
    args: argparse.Namespace,
) -> list[dict[str, Any]]:
    condition_id = str(market["condition_id"])
    if not trades:
        return []
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    l1_by_sec = load_l1_by_second(conn, condition_id, max(start_ms, TRUSTED_START_MS) - 2_000, end_ms)
    l2_cache: dict[str, tuple[list[int], list[list[tuple[float, float]]]]] = {}
    rows = []
    for trade in trades:
        ts_ms = int(trade["trade_ts_ms"])
        side = str(trade["market_side"])
        book = book_at(l1_by_sec, ts_ms)
        if book is None:
            continue
        high = high_side(book)
        if high is None:
            continue
        if side not in l2_cache:
            if day_l2 is not None:
                l2_cache[side] = day_l2.get((condition_id, side), ([], []))
            else:
                l2_cache[side] = load_l2_asks(
                    conn,
                    condition_id,
                    side,
                    max(start_ms, TRUSTED_START_MS),
                    min(end_ms, start_ms + (args.max_offset_s + args.completion_s) * 1000),
                )
        first_times, first_books = l2_cache[side]
        first_vwap, first_age_ms, first_worst, first_filled = latest_sweep(first_times, first_books, ts_ms, args.clip, args.max_l2_age_ms)
        if first_vwap is None:
            continue
        opp = other(side)
        if opp not in l2_cache:
            if day_l2 is not None:
                l2_cache[opp] = day_l2.get((condition_id, opp), ([], []))
            else:
                l2_cache[opp] = load_l2_asks(
                    conn,
                    condition_id,
                    opp,
                    max(start_ms, TRUSTED_START_MS),
                    min(end_ms, start_ms + (args.max_offset_s + args.completion_s) * 1000),
                )
        opp_times, opp_books = l2_cache[opp]
        opp_ask = book[opp]["ask"]
        if opp_ask is None:
            continue
        completion = completion_scan(opp_times, opp_books, ts_ms, min(end_ms, ts_ms + args.completion_s * 1000), first_vwap, args.clip)
        rows.append(
            {
                "day": iso_ms(ts_ms)[:10],
                "slug": market["slug"],
                "condition_id": condition_id,
                "winner_side": market["winner_side"],
                "trigger_ts_ms": ts_ms,
                "trigger_iso": iso_ms(ts_ms),
                "offset_s": round((ts_ms - start_ms) / 1000.0, 3),
                "first_side": side,
                "side_alignment": "high" if side == high else "low",
                "first_is_winner": side == market["winner_side"],
                "public_trade_price": safe_round(float(trade["price"])),
                "public_trade_size": safe_round(float(trade["size"])),
                "clip": args.clip,
                "first_l2_vwap": safe_round(first_vwap),
                "first_l2_age_ms": first_age_ms,
                "first_l2_worst_px": safe_round(first_worst),
                "first_l2_filled": safe_round(first_filled),
                "opp_l1_ask": safe_round(float(opp_ask)),
                "l1_immediate_pair": safe_round(first_vwap + float(opp_ask)),
                **completion,
            }
        )
    return rows


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
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_signal_candidate_cache"))
    parser.add_argument("--min-trade-price", type=float, default=0.50)
    parser.add_argument("--max-trade-price", type=float, default=0.75)
    parser.add_argument("--min-trade-size", type=float, default=50.0)
    parser.add_argument("--max-trade-size", type=float, default=250.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--preload-day-l2", action="store_true", help="Experimental: preload day L2 in one scan. Not the default; it can be slower on large DBs.")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    market_count = 0
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        with ro_connect(db_path) as conn:
            markets = load_markets(conn)
            trades_by_condition = load_day_trigger_trades(conn, args)
            day_l2 = load_day_l2_asks(conn, args) if args.preload_day_l2 else None
            print(
                f"{day}: markets={len(markets)} trigger_markets={len(trades_by_condition)} l2_keys={0 if day_l2 is None else len(day_l2)}",
                file=sys.stderr,
                flush=True,
            )
            for idx, market in enumerate(markets, start=1):
                market_count += 1
                rows.extend(market_candidates(conn, market, trades_by_condition.get(str(market["condition_id"]), []), day_l2, args))
                if idx % 100 == 0:
                    print(f"{day}: processed_markets={idx} rows={len(rows)}", file=sys.stderr, flush=True)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "taker_buy_signal_candidate_cache.csv"
    write_csv(csv_path, rows)
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "market_count": market_count,
        "candidate_rows": len(rows),
        "csv_path": str(csv_path),
    }
    (args.output_dir / "taker_buy_signal_candidate_cache_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

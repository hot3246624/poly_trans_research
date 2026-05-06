#!/usr/bin/env python3
"""Fast event-triggered BTC 5m taker BUY signal backtest.

This is a focused validator for the current xuan hypothesis:

  public BUY event + price/size bucket + L1 immediate pair gate
  -> strict 30s cheap completion, otherwise settle residual for diagnostics.

It uses replay SQLite read-only and does not read raw capture.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


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
    levels = []
    for idx in range(1, 6):
        px = row[f"ask{idx}_px"]
        sz = row[f"ask{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    return levels


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


def side_alignment(book: dict[str, Any], side: str) -> str | None:
    high = high_side(book)
    if high is None:
        return None
    return "high" if side == high else "low"


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


def load_l2_asks(
    conn: sqlite3.Connection, condition_id: str, side: str, start_ms: int, end_ms: int
) -> tuple[list[int], list[list[tuple[float, float]]]]:
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


def first_completion(
    times: list[int],
    books: list[list[tuple[float, float]]],
    start_ms: int,
    end_ms: int,
    first_price: float,
    clip: float,
    pair_ceiling: float,
) -> dict[str, Any] | None:
    lo = bisect.bisect_left(times, start_ms)
    hi = bisect.bisect_right(times, end_ms)
    for idx in range(lo, hi):
        vwap, filled, worst = sweep_vwap(books[idx], clip)
        if vwap is None:
            continue
        pair_cost = first_price + vwap
        if pair_cost <= pair_ceiling + 1e-9:
            return {
                "completion_ts_ms": times[idx],
                "completion_vwap": vwap,
                "completion_worst_px": worst,
                "completion_filled": filled,
                "completion_delay_s": (times[idx] - start_ms) / 1000.0,
                "pair_cost": pair_cost,
            }
    return None


def latest_sweep(
    times: list[int],
    books: list[list[tuple[float, float]]],
    ts_ms: int,
    clip: float,
    max_age_ms: int,
) -> tuple[float | None, int | None, float | None]:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None, None, None
    age = ts_ms - times[idx]
    if age < 0 or age > max_age_ms:
        return None, None, None
    vwap, _filled, worst = sweep_vwap(books[idx], clip)
    return vwap, age, worst


def simulate_market(conn: sqlite3.Connection, market: sqlite3.Row, args: argparse.Namespace) -> list[dict[str, Any]]:
    condition_id = str(market["condition_id"])
    trades = load_trigger_trades(conn, condition_id, market, args)
    if not trades:
        return []
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    l1_by_sec = load_l1_by_second(conn, condition_id, max(start_ms, TRUSTED_START_MS) - 2_000, end_ms)
    rows: list[dict[str, Any]] = []
    cursor_ms = max(start_ms, TRUSTED_START_MS) + args.min_offset_s * 1000
    l2_cache: dict[str, tuple[list[int], list[list[tuple[float, float]]]]] = {}
    for trade in trades:
        ts_ms = int(trade["trade_ts_ms"])
        if ts_ms < cursor_ms:
            continue
        side = str(trade["market_side"])
        first_price = float(trade["price"])
        size = float(trade["size"])
        book = book_at(l1_by_sec, ts_ms)
        if book is None:
            continue
        alignment = side_alignment(book, side)
        if alignment is None:
            continue
        side_filter = "high" if args.require_high_side else args.side_filter
        if side_filter != "any" and alignment != side_filter:
            continue
        if args.first_price_source == "trade":
            first_price = float(trade["price"])
            first_price_age_ms = None
            first_worst_px = first_price
        else:
            if side not in l2_cache:
                l2_cache[side] = load_l2_asks(
                    conn,
                    condition_id,
                    side,
                    max(start_ms, TRUSTED_START_MS),
                    min(end_ms, ts_ms + args.max_completion_s * 1000),
                )
            side_times, side_books = l2_cache[side]
            first_price_l2, first_price_age_ms, first_worst_px = latest_sweep(
                side_times,
                side_books,
                ts_ms,
                args.clip,
                args.max_l2_age_ms,
            )
            if first_price_l2 is None:
                continue
            first_price = first_price_l2
        if first_price < args.min_first_price or first_price >= args.max_first_price:
            continue
        opp = other(side)
        opp_ask = book[opp]["ask"]
        if opp_ask is None:
            continue
        l1_immediate_pair = first_price + float(opp_ask)
        if args.max_l1_immediate_pair is not None and l1_immediate_pair > args.max_l1_immediate_pair:
            continue
        if opp not in l2_cache:
            l2_cache[opp] = load_l2_asks(conn, condition_id, opp, ts_ms, min(end_ms, ts_ms + args.max_completion_s * 1000))
        times, books = l2_cache[opp]
        completion = first_completion(
            times,
            books,
            ts_ms,
            min(end_ms, ts_ms + args.completion_s * 1000),
            first_price,
            args.clip,
            args.pair_ceiling,
        )
        row: dict[str, Any] = {
            "day": iso_ms(ts_ms)[:10],
            "slug": market["slug"],
            "condition_id": condition_id,
            "winner_side": market["winner_side"],
            "trigger_ts_ms": ts_ms,
            "trigger_iso": iso_ms(ts_ms),
            "offset_s": round((ts_ms - start_ms) / 1000.0, 3),
            "first_side": side,
            "side_alignment": alignment,
            "first_is_winner": side == market["winner_side"],
            "trigger_price": round(first_price, 6),
            "public_trade_price": round(float(trade["price"]), 6),
            "trigger_size": round(size, 6),
            "first_price_source": args.first_price_source,
            "first_price_age_ms": first_price_age_ms,
            "first_worst_px": None if first_worst_px is None else round(first_worst_px, 6),
            "clip": args.clip,
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
                    "completion_iso": iso_ms(int(completion["completion_ts_ms"])),
                    "completion_delay_s": round(float(completion["completion_delay_s"]), 3),
                    "completion_vwap": round(float(completion["completion_vwap"]), 6),
                    "pair_cost": round(float(completion["pair_cost"]), 6),
                    "pnl": round(pnl, 6),
                    "status": "closed",
                }
            )
        else:
            pnl = (1.0 - first_price) * args.clip if side == market["winner_side"] else -first_price * args.clip
            row["pnl"] = round(pnl, 6)
        rows.append(row)
        if completion is None and args.block_after_residual:
            break
        cursor_ms = ts_ms + args.cooldown_s * 1000
    return rows


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row.get("completion_fill") is True]
    residual = [row for row in rows if row.get("completion_fill") is not True]
    cost = sum(float(row["trigger_price"]) * float(row["clip"]) for row in rows)
    pnl = sum(float(row["pnl"]) for row in rows)
    return {
        "rows": len(rows),
        "closed": len(closed),
        "closed_rate": rate(len(closed), len(rows)),
        "first_winner_rate": rate(sum(1 for row in rows if row.get("first_is_winner") is True), len(rows)),
        "residual": len(residual),
        "residual_winner_rate": rate(sum(1 for row in residual if row.get("first_is_winner") is True), len(residual)),
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / cost, 6) if cost else None,
        "pair_cost": summarize([row.get("pair_cost") for row in closed]),
        "completion_delay_s": summarize([row.get("completion_delay_s") for row in closed]),
        "l1_immediate_pair": summarize([row.get("l1_immediate_pair") for row in rows]),
        "status_counts": {status: sum(1 for row in rows if row.get("status") == status) for status in sorted({row.get("status") for row in rows})},
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "all": compact(rows),
        "by_day": {day: compact([row for row in rows if row["day"] == day]) for day in sorted({row["day"] for row in rows})},
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
    lines = [
        "# BTC 5m Fast Taker BUY Signal Backtest",
        "",
        "## Aggregate",
        "",
        "| scope | rows | closed | first winner | residual | residual winner | pair p50 | delay p50 | pnl | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    all_item = report["aggregate"]["all"]
    lines.append(
        f"| all | {all_item['rows']} | {all_item['closed_rate']} | {all_item['first_winner_rate']} | "
        f"{all_item['residual']} | {all_item['residual_winner_rate']} | {all_item['pair_cost']['p50']} | "
        f"{all_item['completion_delay_s']['p50']} | {all_item['pnl']} | {all_item['roi_on_first_cost']} |"
    )
    lines.extend(["", "## By Day", "", "| day | rows | closed | first winner | pnl | ROI |", "|---|---:|---:|---:|---:|---:|"])
    for day, item in report["aggregate"]["by_day"].items():
        lines.append(
            f"| {day} | {item['rows']} | {item['closed_rate']} | {item['first_winner_rate']} | "
            f"{item['pnl']} | {item['roi_on_first_cost']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/btc5m_taker_buy_signal_fast"))
    parser.add_argument("--min-trade-price", type=float, default=0.55)
    parser.add_argument("--max-trade-price", type=float, default=0.70)
    parser.add_argument("--min-trade-size", type=float, default=100.0)
    parser.add_argument("--max-trade-size", type=float, default=200.0)
    parser.add_argument("--first-price-source", choices=("trade", "l2"), default="trade")
    parser.add_argument("--min-first-price", type=float, default=0.0)
    parser.add_argument("--max-first-price", type=float, default=1.0)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--max-l1-immediate-pair", type=float, default=1.00)
    parser.add_argument("--side-filter", choices=("any", "high", "low"), default="any")
    parser.add_argument("--require-high-side", action="store_true")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--max-completion-s", type=int, default=30)
    parser.add_argument("--pair-ceiling", type=float, default=0.95)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--block-after-residual", action="store_true")
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        with ro_connect(db_path) as conn:
            for market in load_markets(conn):
                rows.extend(simulate_market(conn, market, args))

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "btc5m_taker_buy_signal_fast_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "aggregate": aggregate(rows),
    }
    (args.output_dir / "btc5m_taker_buy_signal_fast_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "btc5m_taker_buy_signal_fast_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

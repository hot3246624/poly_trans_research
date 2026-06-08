#!/usr/bin/env python3
"""Lightweight BTC 5m upcross taker-first proxy backtest.

This is a fast research proxy, not a live execution model:
- first leg is assumed filled at current L1 ask;
- completion is assumed filled at future L1 opposite ask if size is enough;
- no L2 sweep, no queue modeling, no fees/rebates.

The purpose is to quickly reject or keep the all-market short-momentum upcross
direction before spending compute on full L2 maker/taker simulations.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from typing import Any


TRUSTED_START_MS = 1777274700000
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


def summarize(values: list[float | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_quote(book: dict[str, Any], side: str) -> dict[str, float | None]:
    return book[side]


def fetch_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC' AND m.interval_sec = 300 AND m.end_ms > ?
        ORDER BY m.start_ms
        """,
        (TRUSTED_START_MS,),
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        if row["winner_side"] not in ("YES", "NO"):
            continue
        out.append(row)
    return out


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(recv_ms) FROM md_book_l1").fetchone()
    return None if row is None or row[0] is None else int(row[0])


def latest_l1_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, Any]]:
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
        sec = int(row["recv_ms"]) // 1000
        out[sec] = {
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


def select_momentum_side(current: dict[str, Any], prev: dict[str, Any]) -> tuple[str | None, float | None]:
    best_side = None
    best_delta = None
    for side in ("YES", "NO"):
        cur_bid = side_quote(current, side)["bid"]
        prev_bid = side_quote(prev, side)["bid"]
        if cur_bid is None or prev_bid is None:
            continue
        delta = float(cur_bid) - float(prev_bid)
        if best_delta is None or delta > best_delta:
            best_side = side
            best_delta = delta
    return best_side, best_delta


def find_completion(
    l1_by_sec: dict[int, dict[str, Any]],
    first_sec: int,
    end_sec: int,
    side: str,
    clip: float,
    max_opp_price: float,
) -> tuple[int | None, float | None]:
    opp = other(side)
    for sec in range(first_sec + 1, end_sec + 1):
        book = l1_by_sec.get(sec)
        if not book:
            continue
        quote = side_quote(book, opp)
        ask = quote["ask"]
        ask_sz = quote["ask_sz"]
        if ask is None or ask_sz is None:
            continue
        if float(ask) <= max_opp_price + 1e-9 and float(ask_sz) >= clip:
            return sec, float(ask)
    return None, None


def latest_book_at_or_before(l1_by_sec: dict[int, dict[str, Any]], target_sec: int, min_sec: int) -> tuple[int | None, dict[str, Any] | None]:
    for sec in range(target_sec, min_sec - 1, -1):
        book = l1_by_sec.get(sec)
        if book:
            return sec, book
    return None, None


def simulate_market(market: sqlite3.Row, l1_by_sec: dict[int, dict[str, Any]], args: argparse.Namespace) -> list[dict[str, Any]]:
    start_sec = max(int(market["start_ms"]), TRUSTED_START_MS) // 1000
    end_sec = int(market["end_ms"]) // 1000
    stop_sec = end_sec - args.tail_freeze_s
    rows: list[dict[str, Any]] = []
    cursor_sec = start_sec + args.min_offset_s
    for sec in range(start_sec + args.min_offset_s, min(start_sec + args.max_offset_s, stop_sec), args.sample_interval_s):
        if sec < cursor_sec:
            continue
        current = l1_by_sec.get(sec)
        prev = l1_by_sec.get(sec - 1)
        if not current or not prev:
            continue
        side, prev_delta = select_momentum_side(current, prev)
        if side is None or prev_delta is None or prev_delta < args.min_prev_bid_delta_1s:
            continue
        quote = side_quote(current, side)
        bid = quote["bid"]
        ask = quote["ask"]
        ask_sz = quote["ask_sz"]
        if bid is None or ask is None or ask_sz is None:
            continue
        bid = float(bid)
        ask = float(ask)
        ask_sz = float(ask_sz)
        spread_ticks = round((ask - bid) * 100.0, 6)
        if bid < args.min_side_bid or bid >= args.max_side_bid:
            continue
        if spread_ticks > args.max_spread_ticks:
            continue
        if ask_sz < args.clip:
            continue
        if args.first_price_source == "ask":
            first_price = ask
        elif args.first_price_source == "bid":
            first_price = bid
        elif args.first_price_source == "bid_minus":
            first_price = max(0.0, bid - args.first_bid_improvement)
        else:
            raise ValueError(f"unknown first_price_source: {args.first_price_source}")
        completion_sec, completion_price = find_completion(
            l1_by_sec,
            sec,
            min(sec + args.completion_deadline_s, end_sec),
            side,
            args.clip,
            args.completion_pair_ceiling - first_price,
        )
        path = "completion"
        exit_sec = completion_sec
        second_price = completion_price
        pair_cost = None
        pnl = None
        residual_mark_sec = None
        residual_first_bid = None
        residual_first_ask = None
        residual_opp_bid = None
        residual_opp_ask = None
        residual_sell_pnl = None
        residual_mark_pair_cost = None
        if completion_price is not None:
            pair_cost = first_price + completion_price
            pnl = (1.0 - pair_cost) * args.clip
        else:
            repair_sec, repair_price = find_completion(
                l1_by_sec,
                sec + args.completion_deadline_s,
                min(sec + args.repair_deadline_s, end_sec),
                side,
                args.clip,
                args.repair_pair_ceiling - first_price,
            )
            if repair_price is not None:
                path = "repair"
                exit_sec = repair_sec
                second_price = repair_price
                pair_cost = first_price + repair_price
                pnl = (1.0 - pair_cost) * args.clip
            else:
                path = "residual_settle"
                residual_mark_sec, residual_book = latest_book_at_or_before(
                    l1_by_sec,
                    min(sec + args.repair_deadline_s, end_sec),
                    sec,
                )
                if residual_book is not None:
                    residual_first = side_quote(residual_book, side)
                    residual_opp = side_quote(residual_book, other(side))
                    residual_first_bid = residual_first["bid"]
                    residual_first_ask = residual_first["ask"]
                    residual_opp_bid = residual_opp["bid"]
                    residual_opp_ask = residual_opp["ask"]
                    if residual_first_bid is not None:
                        residual_sell_pnl = (float(residual_first_bid) - first_price) * args.clip
                    if residual_opp_ask is not None:
                        residual_mark_pair_cost = first_price + float(residual_opp_ask)
                if side == market["winner_side"]:
                    pnl = (1.0 - first_price) * args.clip
                else:
                    pnl = -first_price * args.clip
        rows.append(
            {
                "day": iso_ms(sec * 1000)[:10],
                "slug": market["slug"],
                "condition_id": market["condition_id"],
                "candidate_ts_ms": sec * 1000,
                "candidate_iso": iso_ms(sec * 1000),
                "offset_s": sec - (int(market["start_ms"]) // 1000),
                "first_side": side,
                "winner_side": market["winner_side"],
                "first_is_winner": side == market["winner_side"],
                "first_bid": round(bid, 6),
                "first_ask": round(ask, 6),
                "first_price": round(first_price, 6),
                "first_price_source": args.first_price_source,
                "first_bid_improvement": args.first_bid_improvement,
                "prev_bid_delta_1s": round(prev_delta, 6),
                "spread_ticks": spread_ticks,
                "path": path,
                "completion_ts_ms": None if exit_sec is None else exit_sec * 1000,
                "completion_delay_s": None if exit_sec is None else exit_sec - sec,
                "second_price": None if second_price is None else round(second_price, 6),
                "pair_cost": None if pair_cost is None else round(pair_cost, 6),
                "residual_mark_ts_ms": None if residual_mark_sec is None else residual_mark_sec * 1000,
                "residual_mark_delay_s": None if residual_mark_sec is None else residual_mark_sec - sec,
                "residual_first_bid": None if residual_first_bid is None else round(float(residual_first_bid), 6),
                "residual_first_ask": None if residual_first_ask is None else round(float(residual_first_ask), 6),
                "residual_opp_bid": None if residual_opp_bid is None else round(float(residual_opp_bid), 6),
                "residual_opp_ask": None if residual_opp_ask is None else round(float(residual_opp_ask), 6),
                "residual_sell_pnl": None if residual_sell_pnl is None else round(residual_sell_pnl, 6),
                "residual_mark_pair_cost": None if residual_mark_pair_cost is None else round(residual_mark_pair_cost, 6),
                "residual_first_bid_minus_entry": (
                    None if residual_first_bid is None else round(float(residual_first_bid) - first_price, 6)
                ),
                "pnl": round(float(pnl or 0.0), 6),
                "clip": args.clip,
            }
        )
        if path == "residual_settle":
            break
        cursor_sec = int(exit_sec or sec) + args.cooldown_s
    return rows


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    completed = [r for r in rows if r["path"] in ("completion", "repair")]
    residual = [r for r in rows if r["path"] == "residual_settle"]
    total_spend = sum(float(r["first_price"]) * float(r["clip"]) for r in rows)
    pnl = sum(float(r["pnl"]) for r in rows)
    out = {
        "trades": len(rows),
        "completed": len(completed),
        "residual": len(residual),
        "completion_rate": rate(len(completed), len(rows)),
        "first_winner_rate": rate(sum(1 for r in rows if r["first_is_winner"]), len(rows)),
        "pnl": round(pnl, 6),
        "total_spend": round(total_spend, 6),
        "roi": rate(pnl, total_spend),
        "pair_cost": summarize([r.get("pair_cost") for r in completed]),
        "completion_delay_s": summarize([r.get("completion_delay_s") for r in completed]),
    }
    daily = {}
    for day in sorted({r["day"] for r in rows}):
        xs = [r for r in rows if r["day"] == day]
        day_pnl = sum(float(r["pnl"]) for r in xs)
        day_spend = sum(float(r["first_price"]) * float(r["clip"]) for r in xs)
        daily[day] = {
            "trades": len(xs),
            "completed": sum(1 for r in xs if r["path"] in ("completion", "repair")),
            "residual": sum(1 for r in xs if r["path"] == "residual_settle"),
            "pnl": round(day_pnl, 6),
            "roi": rate(day_pnl, day_spend),
        }
    out["daily"] = daily
    return out


def render_report(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    lines = [
        "# BTC 5m Upcross L1 Taker Proxy Backtest",
        "",
        "## Scope",
        "",
        f"- days: `{','.join(report['days'])}`",
        "- First leg: taker at current L1 ask, requiring ask size >= clip.",
        "- Completion: future L1 opposite ask, no L2 sweep.",
        "- This is optimistic and research-only.",
        "",
        "## Aggregate",
        "",
    ]
    for key in ("trades", "completed", "residual", "completion_rate", "first_winner_rate", "pnl", "total_spend", "roi"):
        lines.append(f"- {key}: `{agg[key]}`")
    lines.append(f"- pair_cost_p50: `{agg['pair_cost']['p50']}`")
    lines.extend(["", "## Daily", "", "| day | trades | completed | residual | pnl | roi |", "|---|---:|---:|---:|---:|---:|"])
    for day, item in agg["daily"].items():
        lines.append(f"| {day} | {item['trades']} | {item['completed']} | {item['residual']} | {item['pnl']} | {item['roi']} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/backtest_btc5m_upcross_l1_taker")
    parser.add_argument("--sample-interval-s", type=int, default=1)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--first-price-source", choices=("ask", "bid", "bid_minus"), default="ask")
    parser.add_argument("--first-bid-improvement", type=float, default=0.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--tail-freeze-s", type=int, default=60)
    parser.add_argument("--min-side-bid", type=float, default=0.40)
    parser.add_argument("--max-side-bid", type=float, default=0.55)
    parser.add_argument("--max-spread-ticks", type=float, default=1.0)
    parser.add_argument("--min-prev-bid-delta-1s", type=float, default=0.02)
    parser.add_argument("--completion-pair-ceiling", type=float, default=0.95)
    parser.add_argument("--completion-deadline-s", type=int, default=30)
    parser.add_argument("--repair-pair-ceiling", type=float, default=1.04)
    parser.add_argument("--repair-deadline-s", type=int, default=60)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    rows: list[dict[str, Any]] = []
    coverage = []
    markets_seen = 0
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            coverage.append({"day": day, "status": "missing"})
            continue
        with ro_connect(db_path) as conn:
            markets = fetch_markets(conn, day_max_ms(conn))
            day_rows = []
            for market in markets:
                if args.max_markets and markets_seen >= args.max_markets:
                    break
                markets_seen += 1
                l1 = latest_l1_by_second(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                market_rows = simulate_market(market, l1, args)
                day_rows.extend(market_rows)
            rows.extend(day_rows)
            coverage.append({"day": day, "status": "ok", "markets": len(markets), "rows": len(day_rows)})
        if args.max_markets and markets_seen >= args.max_markets:
            break

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "parameters": vars(args),
        "coverage": coverage,
        "aggregate": aggregate(rows),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_upcross_l1_taker_rows.csv", rows)
    (output_dir / "btc5m_upcross_l1_taker_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_upcross_l1_taker_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "markets_seen": markets_seen}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

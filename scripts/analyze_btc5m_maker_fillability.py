#!/usr/bin/env python3
"""Maker fillability audit for BTC 5m short-momentum candidates.

This script answers a narrow question:

    If we place a maker BUY at bid / bid-1c / bid-2c during the selected
    short-momentum window, how often does public SELL flow reach that price?

It is not a PnL backtest. It does not assume fills are guaranteed. It outputs
optimistic and queue-aware proxies so execution assumptions stay explicit.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any


TRUSTED_START_MS = 1777274700000
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
TICK = 0.01


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


def side_quote(book: dict[str, Any], side: str) -> dict[str, float | None]:
    return book[side]


def fetch_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        WHERE symbol = 'BTC' AND interval_sec = 300 AND end_ms > ?
        ORDER BY start_ms
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
        out[int(row["recv_ms"]) // 1000] = {
            "recv_ms": int(row["recv_ms"]),
            "YES": {"bid": row["yes_bid_px"], "ask": row["yes_ask_px"], "bid_sz": row["yes_bid_sz"], "ask_sz": row["yes_ask_sz"]},
            "NO": {"bid": row["no_bid_px"], "ask": row["no_ask_px"], "bid_sz": row["no_bid_sz"], "ask_sz": row["no_ask_sz"]},
        }
    return out


def latest_l2_by_second(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int,
    end_ms: int,
) -> dict[tuple[int, str], dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz
        FROM md_book_l2
        WHERE condition_id = ? AND recv_ms >= ? AND recv_ms <= ?
          AND market_side IN ('YES', 'NO')
        ORDER BY recv_ms, id
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[tuple[int, str], dict[str, Any]] = {}
    for row in rows:
        levels = []
        for idx in range(1, 6):
            px = row[f"bid{idx}_px"]
            sz = row[f"bid{idx}_sz"]
            if px is None or sz is None:
                continue
            if float(sz) <= 0:
                continue
            levels.append((float(px), float(sz)))
        if levels:
            out[(int(row["recv_ms"]) // 1000, str(row["market_side"]))] = {
                "recv_ms": int(row["recv_ms"]),
                "levels": levels,
            }
    return out


def sell_trades_by_side(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND taker_side = 'SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    )
    out = {"YES": [], "NO": []}
    for row in rows:
        out[str(row["market_side"])].append(
            {
                "ts_ms": int(row["trade_ts_ms"]),
                "price": float(row["price"]),
                "size": float(row["size"]),
            }
        )
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


def l2_queue_stats(l2: dict[str, Any] | None, order_price: float) -> dict[str, Any]:
    if not l2:
        return {"l2_age_ms": None, "queue_above": None, "queue_same": None, "top_bid": None, "top_bid_sz": None}
    levels = l2["levels"]
    queue_above = sum(sz for px, sz in levels if px > order_price + 1e-9)
    queue_same = sum(sz for px, sz in levels if abs(px - order_price) < TICK / 2)
    return {
        "l2_age_ms": None,
        "queue_above": round(queue_above, 6),
        "queue_same": round(queue_same, 6),
        "top_bid": levels[0][0] if levels else None,
        "top_bid_sz": levels[0][1] if levels else None,
    }


def sell_flow_metrics(
    trades: list[dict[str, Any]],
    start_ms: int,
    order_price: float,
    clip: float,
    queue_same: float,
    horizons_s: list[int],
) -> dict[str, Any]:
    metrics: dict[str, Any] = {}
    for horizon_s in horizons_s:
        end_ms = start_ms + horizon_s * 1000
        xs = [t for t in trades if start_ms <= t["ts_ms"] <= end_ms]
        reached = [t for t in xs if t["price"] <= order_price + 1e-9]
        vol_le = sum(t["size"] for t in reached)
        min_price = min([t["price"] for t in xs], default=None)
        first_reach_ts = min([t["ts_ms"] for t in reached], default=None)
        metrics[f"sell_count_{horizon_s}s"] = len(xs)
        metrics[f"sell_vol_{horizon_s}s"] = round(sum(t["size"] for t in xs), 6)
        metrics[f"sell_vol_le_order_{horizon_s}s"] = round(vol_le, 6)
        metrics[f"min_sell_price_{horizon_s}s"] = min_price
        metrics[f"first_reach_delay_{horizon_s}s"] = None if first_reach_ts is None else round((first_reach_ts - start_ms) / 1000.0, 3)
        metrics[f"optimistic_fill_{horizon_s}s"] = vol_le >= clip
        metrics[f"queue_half_fill_{horizon_s}s"] = vol_le >= clip + 0.5 * queue_same
        metrics[f"queue_full_fill_{horizon_s}s"] = vol_le >= clip + queue_same
    return metrics


def build_rows_for_market(conn: sqlite3.Connection, market: sqlite3.Row, args: argparse.Namespace) -> list[dict[str, Any]]:
    start_sec = max(int(market["start_ms"]), TRUSTED_START_MS) // 1000
    end_sec = int(market["end_ms"]) // 1000
    start_ms = start_sec * 1000
    end_ms = end_sec * 1000
    l1_by_sec = latest_l1_by_second(conn, market["condition_id"], start_ms, end_ms)
    l2_by_sec_side = latest_l2_by_second(conn, market["condition_id"], start_ms - 1000, end_ms)
    trades = sell_trades_by_side(conn, market["condition_id"], start_ms, end_ms + max(args.horizons_s) * 1000)
    rows: list[dict[str, Any]] = []
    price_offsets = [float(x) for x in args.price_offsets.split(",") if x.strip()]
    for sec in range(start_sec + args.min_offset_s, min(start_sec + args.max_offset_s, end_sec - args.tail_freeze_s), args.sample_interval_s):
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
        if bid is None or ask is None:
            continue
        bid = float(bid)
        ask = float(ask)
        spread_ticks = round((ask - bid) * 100.0, 6)
        if bid < args.min_side_bid or bid >= args.max_side_bid:
            continue
        if spread_ticks > args.max_spread_ticks:
            continue
        for price_offset in price_offsets:
            order_price = max(0.0, bid - price_offset)
            l2 = l2_by_sec_side.get((sec, side)) or l2_by_sec_side.get((sec - 1, side))
            qstats = l2_queue_stats(l2, order_price)
            if l2 is not None:
                qstats["l2_age_ms"] = sec * 1000 - int(l2["recv_ms"])
            queue_same = float(qstats["queue_same"] or 0.0)
            flow = sell_flow_metrics(trades[side], sec * 1000, order_price, args.clip, queue_same, args.horizons_s)
            rows.append(
                {
                    "day": iso_ms(sec * 1000)[:10],
                    "slug": market["slug"],
                    "condition_id": market["condition_id"],
                    "candidate_ts_ms": sec * 1000,
                    "candidate_iso": iso_ms(sec * 1000),
                    "offset_s": sec - (int(market["start_ms"]) // 1000),
                    "side": side,
                    "side_bid": round(bid, 6),
                    "side_ask": round(ask, 6),
                    "spread_ticks": spread_ticks,
                    "prev_bid_delta_1s": round(prev_delta, 6),
                    "order_price_offset": round(price_offset, 6),
                    "order_price": round(order_price, 6),
                    "clip": args.clip,
                    **qstats,
                    **flow,
                }
            )
    return rows


def aggregate(rows: list[dict[str, Any]], horizons_s: list[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    groups: dict[float, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[float(row["order_price_offset"])].append(row)
    for offset, xs in sorted(groups.items()):
        key = f"offset_{offset:g}"
        item: dict[str, Any] = {
            "candidates": len(xs),
            "queue_above": summarize([r.get("queue_above") for r in xs]),
            "queue_same": summarize([r.get("queue_same") for r in xs]),
            "order_price": summarize([r.get("order_price") for r in xs]),
        }
        for h in horizons_s:
            item[f"optimistic_fill_{h}s_rate"] = rate(sum(1 for r in xs if r.get(f"optimistic_fill_{h}s") is True), len(xs))
            item[f"queue_half_fill_{h}s_rate"] = rate(sum(1 for r in xs if r.get(f"queue_half_fill_{h}s") is True), len(xs))
            item[f"queue_full_fill_{h}s_rate"] = rate(sum(1 for r in xs if r.get(f"queue_full_fill_{h}s") is True), len(xs))
            item[f"sell_vol_le_order_{h}s"] = summarize([r.get(f"sell_vol_le_order_{h}s") for r in xs])
            item[f"first_reach_delay_{h}s"] = summarize([r.get(f"first_reach_delay_{h}s") for r in xs])
        daily = {}
        for day in sorted({r["day"] for r in xs}):
            ds = [r for r in xs if r["day"] == day]
            daily[day] = {"candidates": len(ds)}
            for h in horizons_s:
                daily[day][f"optimistic_fill_{h}s_rate"] = rate(
                    sum(1 for r in ds if r.get(f"optimistic_fill_{h}s") is True), len(ds)
                )
                daily[day][f"queue_full_fill_{h}s_rate"] = rate(
                    sum(1 for r in ds if r.get(f"queue_full_fill_{h}s") is True), len(ds)
                )
        item["daily"] = daily
        out[key] = item
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Maker Fillability Audit",
        "",
        "## Scope",
        "",
        f"- days: `{','.join(report['days'])}`",
        f"- candidates: `{report['candidate_count']}` per price offset",
        "- Gate: short momentum, mid-price bid range, tight spread.",
        "- Fill proxies: optimistic ignores visible same-level queue; queue_full requires SELL volume <= order price to cover visible same-level queue plus clip.",
        "",
        "## Aggregate By Price Offset",
        "",
        "| offset | candidates | opt 2s | full 2s | opt 5s | full 5s | opt 30s | full 30s | queue_same p50 |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["aggregate"].items():
        offset = key.removeprefix("offset_")
        lines.append(
            f"| {offset} | {item['candidates']} | "
            f"{item.get('optimistic_fill_2s_rate')} | {item.get('queue_full_fill_2s_rate')} | "
            f"{item.get('optimistic_fill_5s_rate')} | {item.get('queue_full_fill_5s_rate')} | "
            f"{item.get('optimistic_fill_30s_rate')} | {item.get('queue_full_fill_30s_rate')} | "
            f"{item['queue_same']['p50']} |"
        )
    lines.extend(["", "## Daily Queue-Full 30s", "", "| offset | day | candidates | opt 30s | full 30s |", "|---:|---|---:|---:|---:|"])
    for key, item in report["aggregate"].items():
        offset = key.removeprefix("offset_")
        for day, day_item in item["daily"].items():
            lines.append(
                f"| {offset} | {day} | {day_item['candidates']} | "
                f"{day_item.get('optimistic_fill_30s_rate')} | {day_item.get('queue_full_fill_30s_rate')} |"
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/btc5m_maker_fillability_0427_0501")
    parser.add_argument("--sample-interval-s", type=int, default=1)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--min-offset-s", type=int, default=30)
    parser.add_argument("--max-offset-s", type=int, default=60)
    parser.add_argument("--tail-freeze-s", type=int, default=60)
    parser.add_argument("--min-side-bid", type=float, default=0.40)
    parser.add_argument("--max-side-bid", type=float, default=0.55)
    parser.add_argument("--max-spread-ticks", type=float, default=1.0)
    parser.add_argument("--min-prev-bid-delta-1s", type=float, default=0.04)
    parser.add_argument("--price-offsets", default="0,0.01,0.02")
    parser.add_argument("--horizons-s", nargs="+", type=int, default=[2, 5, 30])
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
                day_rows.extend(build_rows_for_market(conn, market, args))
            rows.extend(day_rows)
            coverage.append({"day": day, "status": "ok", "markets": len(markets), "rows": len(day_rows)})
        if args.max_markets and markets_seen >= args.max_markets:
            break

    price_offset_count = len([x for x in args.price_offsets.split(",") if x.strip()])
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "parameters": vars(args),
        "coverage": coverage,
        "rows": len(rows),
        "candidate_count": len(rows) // price_offset_count if price_offset_count else 0,
        "aggregate": aggregate(rows, args.horizons_s),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_maker_fillability_rows.csv", rows)
    (output_dir / "btc5m_maker_fillability_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_maker_fillability_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows), "candidate_count": report["candidate_count"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

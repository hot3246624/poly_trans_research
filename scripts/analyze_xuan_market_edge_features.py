#!/usr/bin/env python3
"""Analyze ex-ante market features behind xuan's market-level PnL.

This is a profit-first research layer:

- input PnL truth comes from `analyze_xuan_market_pnl_truth.py`;
- replay SQLite is opened read-only;
- raw capture is not read;
- winner_side is used only for ex-post diagnostics, not as a feature bucket.

The output is market-level, not tranche-level. The goal is to find states where
xuan's own pair-cost edge is strong or weak, so we can decide what to copy,
upclip, or avoid.
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


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def day_from_ms(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date().isoformat()


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
    return round(xs[lo] * (1.0 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


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


def side_prefix(side: str) -> str:
    return "yes" if side == "YES" else "no"


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def l1_side(row: sqlite3.Row | None, side: str, field: str) -> float | None:
    if row is None:
        return None
    value = row[f"{side_prefix(side)}_{field}"]
    return None if value is None else float(value)


def midpoint(row: sqlite3.Row | None, side: str) -> float | None:
    bid = l1_side(row, side, "bid_px")
    ask = l1_side(row, side, "ask_px")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def spread_ticks(row: sqlite3.Row | None, side: str) -> float | None:
    bid = l1_side(row, side, "bid_px")
    ask = l1_side(row, side, "ask_px")
    if bid is None or ask is None:
        return None
    return round((ask - bid) * 100.0, 6)


def load_first_trades(trades_csv: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in read_csv(trades_csv):
        condition_id = row["condition_id"]
        ts = as_int(row.get("trade_ts_ms"))
        if ts is None:
            continue
        current = out.get(condition_id)
        if current is None or ts < int(current["trade_ts_ms"]):
            out[condition_id] = {
                "trade_ts_ms": ts,
                "trade_iso": row.get("trade_iso"),
                "side": row.get("side"),
                "outcome_side": row.get("outcome_side"),
                "price": as_float(row.get("price")),
                "size": as_float(row.get("size")),
            }
    return out


def latest_l1(conn: sqlite3.Connection, condition_id: str, ts_ms: int, max_age_ms: int) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, id DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    if row is None:
        return None
    if ts_ms - int(row["recv_ms"]) > max_age_ms:
        return None
    return row


def min_pair_ask_sum(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int,
    end_ms: int,
) -> tuple[float | None, int | None]:
    row = conn.execute(
        """
        SELECT recv_ms, yes_ask_px + no_ask_px AS pair_ask_sum
        FROM md_book_l1
        WHERE condition_id = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
          AND yes_ask_px IS NOT NULL
          AND no_ask_px IS NOT NULL
        ORDER BY pair_ask_sum ASC, recv_ms ASC
        LIMIT 1
        """,
        (condition_id, start_ms, end_ms),
    ).fetchone()
    if row is None or row["pair_ask_sum"] is None:
        return None, None
    return float(row["pair_ask_sum"]), int(row["recv_ms"])


def trade_context(
    conn: sqlite3.Connection,
    condition_id: str,
    first_side: str,
    start_ms: int,
    end_ms: int,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT market_side, taker_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms < ?
          AND market_side IN ('YES', 'NO')
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    total = len(rows)
    same_buy = 0.0
    opp_buy = 0.0
    same_sell = 0.0
    opp_sell = 0.0
    for row in rows:
        side = str(row["market_side"])
        taker = str(row["taker_side"] or "")
        size = float(row["size"] or 0.0)
        if side == first_side and taker == "BUY":
            same_buy += size
        elif side != first_side and taker == "BUY":
            opp_buy += size
        elif side == first_side and taker == "SELL":
            same_sell += size
        elif side != first_side and taker == "SELL":
            opp_sell += size
    return {
        "recent_total_trade_count_15s": total,
        "recent_same_buy_size_15s": round(same_buy, 6),
        "recent_opp_buy_size_15s": round(opp_buy, 6),
        "recent_same_sell_size_15s": round(same_sell, 6),
        "recent_opp_sell_size_15s": round(opp_sell, 6),
        "recent_same_minus_opp_buy_size_15s": round(same_buy - opp_buy, 6),
        "recent_same_minus_opp_sell_size_15s": round(same_sell - opp_sell, 6),
    }


def bucket_num(value: float | int | None, cuts: list[tuple[float, str]], last: str) -> str:
    if value is None:
        return "missing"
    x = float(value)
    for threshold, label in cuts:
        if x < threshold:
            return label
    return last


def bucket_bool(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return "missing"


def add_buckets(row: dict[str, Any]) -> None:
    row["bucket_first_offset"] = bucket_num(
        as_float(row.get("first_trade_offset_s")),
        [(30, "000-030s"), (60, "030-060s"), (120, "060-120s"), (180, "120-180s"), (240, "180-240s")],
        "240s+",
    )
    row["bucket_first_price"] = bucket_num(
        as_float(row.get("first_trade_price")),
        [(0.40, "<0.40"), (0.55, "0.40-0.55"), (0.70, "0.55-0.70"), (0.80, "0.70-0.80"), (0.90, "0.80-0.90")],
        ">=0.90",
    )
    row["bucket_first_is_l1_high_side"] = bucket_bool(row.get("first_is_l1_high_side"))
    row["bucket_first_l1_spread"] = bucket_num(
        as_float(row.get("first_l1_spread_ticks")),
        [(1.01, "<=1"), (2.01, "1-2"), (3.01, "2-3"), (5.01, "3-5")],
        ">5",
    )
    row["bucket_opp_l1_spread"] = bucket_num(
        as_float(row.get("opp_l1_spread_ticks")),
        [(1.01, "<=1"), (2.01, "1-2"), (3.01, "2-3"), (5.01, "3-5")],
        ">5",
    )
    row["bucket_pair_bid_sum"] = bucket_num(
        as_float(row.get("pair_bid_sum")),
        [(0.94, "<0.94"), (0.97, "0.94-0.97"), (0.99, "0.97-0.99"), (1.01, "0.99-1.01")],
        ">=1.01",
    )
    row["bucket_pair_ask_sum"] = bucket_num(
        as_float(row.get("pair_ask_sum")),
        [(0.99, "<0.99"), (1.01, "0.99-1.01"), (1.03, "1.01-1.03"), (1.06, "1.03-1.06")],
        ">=1.06",
    )
    row["bucket_min_pair_ask_30s"] = bucket_num(
        as_float(row.get("min_pair_ask_sum_30s")),
        [(0.90, "<0.90"), (0.95, "0.90-0.95"), (0.99, "0.95-0.99"), (1.01, "0.99-1.01")],
        ">=1.01",
    )
    row["bucket_recent_total_trades_15s"] = bucket_num(
        as_float(row.get("recent_total_trade_count_15s")),
        [(1, "0"), (4, "1-3"), (9, "4-8")],
        ">=9",
    )
    row["bucket_recent_same_minus_opp_buy_15s"] = bucket_num(
        as_float(row.get("recent_same_minus_opp_buy_size_15s")),
        [(-100, "<-100"), (-20, "-100..-20"), (20, "-20..20"), (100, "20..100")],
        ">=100",
    )
    row["bucket_first_exec_edge_to_bid"] = bucket_num(
        as_float(row.get("first_exec_edge_to_bid")),
        [(-0.02, "<-2c"), (-0.005, "-2c..-0.5c"), (0.005, "-0.5c..0.5c"), (0.02, "0.5c..2c")],
        ">=2c",
    )
    row["bucket_weighted_pair_cost"] = bucket_num(
        as_float(row.get("weighted_pair_cost")),
        [(0.95, "<0.95"), (0.98, "0.95-0.98"), (1.00, "0.98-1.00"), (1.02, "1.00-1.02")],
        ">=1.02",
    )


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cost = sum(as_float(row.get("total_cost")) or 0.0 for row in rows)
    pnl = sum(as_float(row.get("trade_pnl")) or 0.0 for row in rows)
    paired_qty = sum(as_float(row.get("paired_qty")) or 0.0 for row in rows)
    pair_cost_notional = sum(
        (as_float(row.get("paired_qty")) or 0.0) * (as_float(row.get("weighted_pair_cost")) or 0.0)
        for row in rows
        if as_float(row.get("weighted_pair_cost")) is not None
    )
    good = [row for row in rows if (as_float(row.get("trade_pnl")) or 0.0) > 0]
    good_pair = [row for row in rows if (as_float(row.get("weighted_pair_cost")) or 9.0) <= 0.98]
    bad_pair = [row for row in rows if (as_float(row.get("weighted_pair_cost")) or 0.0) >= 1.02]
    residual = [row for row in rows if (as_float(row.get("residual_qty")) or 0.0) > 1e-9]
    residual_winner = [row for row in residual if row.get("residual_is_winner") == "True" or row.get("residual_is_winner") is True]
    return {
        "n": len(rows),
        "cost": round(cost, 6),
        "pnl": round(pnl, 6),
        "roi": round(pnl / cost, 6) if cost > 0 else None,
        "weighted_pair_cost": round(pair_cost_notional / paired_qty, 6) if paired_qty > 0 else None,
        "profitable_market_rate": rate(len(good), len(rows)),
        "pair_cost_le_098_rate": rate(len(good_pair), len(rows)),
        "pair_cost_ge_102_rate": rate(len(bad_pair), len(rows)),
        "residual_is_winner_rate": rate(len(residual_winner), len(residual)),
        "trade_pnl": summarize([as_float(row.get("trade_pnl")) for row in rows]),
        "first_trade_offset_s": summarize([as_float(row.get("first_trade_offset_s")) for row in rows]),
    }


def bucket_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    features = [
        "bucket_first_offset",
        "bucket_first_price",
        "bucket_first_is_l1_high_side",
        "bucket_first_l1_spread",
        "bucket_opp_l1_spread",
        "bucket_pair_bid_sum",
        "bucket_pair_ask_sum",
        "bucket_min_pair_ask_30s",
        "bucket_recent_total_trades_15s",
        "bucket_recent_same_minus_opp_buy_15s",
        "bucket_first_exec_edge_to_bid",
    ]
    out = []
    baseline = compact(rows)
    baseline_roi = float(baseline["roi"] or 0.0)
    for feature in features:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(row.get(feature))].append(row)
        for bucket, xs in grouped.items():
            item = compact(xs)
            item.update(
                {
                    "feature": feature,
                    "bucket": bucket,
                    "selected_rate": rate(len(xs), len(rows)),
                    "roi_lift": round(float(item["roi"] or 0.0) - baseline_roi, 6),
                }
            )
            out.append(item)
    out.sort(key=lambda row: (row["pnl"], row["roi"], row["n"]), reverse=True)
    return out


def policy_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def f(name: str, pred: Any) -> dict[str, Any]:
        selected = [row for row in rows if pred(row)]
        item = compact(selected)
        item["policy"] = name
        item["selected_rate"] = rate(len(selected), len(rows))
        return item

    return [
        f("all", lambda _row: True),
        f("first_trade_offset_lt30", lambda row: (as_float(row.get("first_trade_offset_s")) or 999) < 30),
        f("first_trade_offset_30_240", lambda row: 30 <= (as_float(row.get("first_trade_offset_s")) or -1) < 240),
        f("first_is_l1_high_side", lambda row: row.get("first_is_l1_high_side") is True),
        f("exec_edge_to_bid_ge_2c", lambda row: (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.02),
        f("exec_edge_to_bid_ge_0_5c", lambda row: (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.005),
        f("exec_edge_to_bid_ge_minus0_5c", lambda row: (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= -0.005),
        f(
            "price_040_055",
            lambda row: 0.40 <= (as_float(row.get("first_trade_price")) or -1.0) < 0.55,
        ),
        f(
            "price_040_055_or_exec_edge_ge_2c",
            lambda row: (
                0.40 <= (as_float(row.get("first_trade_price")) or -1.0) < 0.55
                or (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.02
            ),
        ),
        f(
            "price_040_055_and_exec_edge_ge_0_5c",
            lambda row: (
                0.40 <= (as_float(row.get("first_trade_price")) or -1.0) < 0.55
                and (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.005
            ),
        ),
        f(
            "offset_lt30_and_exec_edge_ge_0_5c",
            lambda row: (
                (as_float(row.get("first_trade_offset_s")) or 999.0) < 30
                and (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.005
            ),
        ),
        f(
            "high_side_and_exec_edge_ge_0_5c",
            lambda row: (
                row.get("first_is_l1_high_side") is True
                and (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.005
            ),
        ),
        f(
            "first_price_lt055_and_exec_edge_ge_0_5c",
            lambda row: (
                (as_float(row.get("first_trade_price")) or 9.0) < 0.55
                and (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= 0.005
            ),
        ),
        f(
            "no_negative_exec_edge",
            lambda row: (
                as_float(row.get("first_exec_edge_to_bid")) is None
                or (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= -0.005
            ),
        ),
        f(
            "no_negative_exec_edge_and_not_60_120s",
            lambda row: (
                not (60 <= (as_float(row.get("first_trade_offset_s")) or -1.0) < 120)
                and (
                    as_float(row.get("first_exec_edge_to_bid")) is None
                    or (as_float(row.get("first_exec_edge_to_bid")) or -9.0) >= -0.005
                )
            ),
        ),
        f("first_price_080_090", lambda row: 0.80 <= (as_float(row.get("first_trade_price")) or -1) < 0.90),
        f("min_pair_ask_30s_lt099", lambda row: (as_float(row.get("min_pair_ask_sum_30s")) or 9) < 0.99),
        f("min_pair_ask_30s_lt095", lambda row: (as_float(row.get("min_pair_ask_sum_30s")) or 9) < 0.95),
        f("first_high_and_min_pair_30s_lt099", lambda row: row.get("first_is_l1_high_side") is True and (as_float(row.get("min_pair_ask_sum_30s")) or 9) < 0.99),
        f("first_price_080_090_or_min_pair_30s_lt095", lambda row: (0.80 <= (as_float(row.get("first_trade_price")) or -1) < 0.90) or (as_float(row.get("min_pair_ask_sum_30s")) or 9) < 0.95),
    ]


def build_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    market_rows = read_csv(Path(args.markets_csv))
    first_trades = load_first_trades(Path(args.trades_csv))
    db_cache: dict[str, sqlite3.Connection] = {}
    out: list[dict[str, Any]] = []
    try:
        for market in market_rows:
            condition_id = market["condition_id"]
            first = first_trades.get(condition_id)
            first_ts = as_int(market.get("first_trade_ms"))
            if first is None or first_ts is None:
                continue
            day = day_from_ms(first_ts)
            if day not in args.days:
                continue
            db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
            if not db_path.exists():
                continue
            conn = db_cache.get(day)
            if conn is None:
                conn = connect_ro(db_path)
                db_cache[day] = conn

            first_side = str(first.get("outcome_side"))
            opp_side = opposite(first_side)
            l1 = latest_l1(conn, condition_id, first_ts, args.max_l1_age_ms)
            yes_mid = midpoint(l1, "YES")
            no_mid = midpoint(l1, "NO")
            high_side = None
            if yes_mid is not None and no_mid is not None:
                high_side = "YES" if yes_mid >= no_mid else "NO"
            pair_bid_sum = None
            pair_ask_sum = None
            if l1 is not None and l1["yes_bid_px"] is not None and l1["no_bid_px"] is not None:
                pair_bid_sum = float(l1["yes_bid_px"]) + float(l1["no_bid_px"])
            if l1 is not None and l1["yes_ask_px"] is not None and l1["no_ask_px"] is not None:
                pair_ask_sum = float(l1["yes_ask_px"]) + float(l1["no_ask_px"])

            min_pair_30, min_pair_30_ts = min_pair_ask_sum(conn, condition_id, first_ts, first_ts + 30_000)
            min_pair_60, min_pair_60_ts = min_pair_ask_sum(conn, condition_id, first_ts, first_ts + 60_000)
            ctx = trade_context(conn, condition_id, first_side, first_ts - 15_000, first_ts)
            first_bid = l1_side(l1, first_side, "bid_px")
            first_ask = l1_side(l1, first_side, "ask_px")
            first_price = as_float(first.get("price"))
            row = dict(market)
            row.update(
                {
                    "first_trade_side": first_side,
                    "first_trade_price": first_price,
                    "first_trade_size": first.get("size"),
                    "l1_recv_ms": None if l1 is None else int(l1["recv_ms"]),
                    "l1_age_ms": None if l1 is None else first_ts - int(l1["recv_ms"]),
                    "l1_high_side": high_side,
                    "first_is_l1_high_side": None if high_side is None else first_side == high_side,
                    "first_l1_bid_px": first_bid,
                    "first_l1_ask_px": first_ask,
                    "opp_l1_bid_px": l1_side(l1, opp_side, "bid_px"),
                    "opp_l1_ask_px": l1_side(l1, opp_side, "ask_px"),
                    "first_l1_bid_sz": l1_side(l1, first_side, "bid_sz"),
                    "first_l1_ask_sz": l1_side(l1, first_side, "ask_sz"),
                    "opp_l1_bid_sz": l1_side(l1, opp_side, "bid_sz"),
                    "opp_l1_ask_sz": l1_side(l1, opp_side, "ask_sz"),
                    "first_l1_spread_ticks": spread_ticks(l1, first_side),
                    "opp_l1_spread_ticks": spread_ticks(l1, opp_side),
                    "pair_bid_sum": None if pair_bid_sum is None else round(pair_bid_sum, 6),
                    "pair_ask_sum": None if pair_ask_sum is None else round(pair_ask_sum, 6),
                    "first_mid_minus_opp_mid": None if yes_mid is None or no_mid is None else round((midpoint(l1, first_side) or 0.0) - (midpoint(l1, opp_side) or 0.0), 6),
                    "first_exec_edge_to_bid": None if first_bid is None or first_price is None else round(first_bid - first_price, 6),
                    "first_exec_edge_to_ask": None if first_ask is None or first_price is None else round(first_ask - first_price, 6),
                    "min_pair_ask_sum_30s": None if min_pair_30 is None else round(min_pair_30, 6),
                    "min_pair_ask_sum_30s_delay_s": None if min_pair_30_ts is None else round((min_pair_30_ts - first_ts) / 1000.0, 3),
                    "min_pair_ask_sum_60s": None if min_pair_60 is None else round(min_pair_60, 6),
                    "min_pair_ask_sum_60s_delay_s": None if min_pair_60_ts is None else round((min_pair_60_ts - first_ts) / 1000.0, 3),
                    **ctx,
                }
            )
            add_buckets(row)
            out.append(row)
    finally:
        for conn in db_cache.values():
            conn.close()
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan Market Edge Feature Report",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- market rows: `{report['summary']['n']}`",
        "- Read-only replay SQLite. No raw data.",
        "- Features are market-state proxies around xuan's first public trade in each BTC 5m market.",
        "",
        "## Topline",
        "",
    ]
    s = report["summary"]
    for key in ("cost", "pnl", "roi", "weighted_pair_cost", "profitable_market_rate", "pair_cost_le_098_rate", "pair_cost_ge_102_rate"):
        lines.append(f"- {key}: `{s.get(key)}`")
    lines.extend(
        [
            "",
            "## Policy Probes",
            "",
            "| policy | n | selected | pnl | roi | pair cost | good <=0.98 | bad >=1.02 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in report["policy_stats"]:
        lines.append(
            f"| {item['policy']} | {item['n']} | {item['selected_rate']} | {item['pnl']} | "
            f"{item['roi']} | {item['weighted_pair_cost']} | {item['pair_cost_le_098_rate']} | "
            f"{item['pair_cost_ge_102_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Best Positive Buckets By ROI",
            "",
            "| feature | bucket | n | selected | pnl | roi | pair cost | bad >=1.02 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    positives = [
        row for row in report["bucket_stats"] if row["n"] >= report["min_bucket_n"] and row["roi"] is not None
    ]
    positives.sort(key=lambda row: (row["roi"], row["pnl"], row["n"]), reverse=True)
    for row in positives[:20]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['selected_rate']} | "
            f"{row['pnl']} | {row['roi']} | {row['weighted_pair_cost']} | {row['pair_cost_ge_102_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Weak Buckets By ROI",
            "",
            "| feature | bucket | n | selected | pnl | roi | pair cost | bad >=1.02 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    negatives = [
        row for row in report["bucket_stats"] if row["n"] >= report["min_bucket_n"] and row["pnl"] is not None
    ]
    negatives.sort(key=lambda row: (row["roi"], row["pnl"], -row["n"]))
    for row in negatives[:20]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['selected_rate']} | "
            f"{row['pnl']} | {row['roi']} | {row['weighted_pair_cost']} | {row['pair_cost_ge_102_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation Rules",
            "",
            "- Buckets using `min_pair_ask_sum_30s` are post-open continuation evidence, not first-open evidence.",
            "- First-trade features are anchored to xuan's first public trade; they must be translated to our own detector before live use.",
            "- `winner_side` is excluded from feature buckets and only used indirectly through ex-post PnL labels.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--markets-csv", default="data/exports/xuan_market_pnl_truth_0427_0501/xuan_market_pnl_markets.csv")
    parser.add_argument("--trades-csv", default="data/exports/xuan_market_pnl_truth_0427_0501/xuan_market_pnl_trades.csv")
    parser.add_argument("--output-dir", default="data/exports/xuan_market_edge_features_0427_0501")
    parser.add_argument("--max-l1-age-ms", type=int, default=1000)
    parser.add_argument("--min-bucket-n", type=int, default=30)
    args = parser.parse_args()
    args.days = [day.strip() for day in args.days.split(",") if day.strip()]

    rows = build_rows(args)
    buckets = bucket_stats(rows)
    policies = policy_stats(rows)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_csv(out_dir / "xuan_market_edge_feature_rows.csv", rows)
    write_csv(out_dir / "xuan_market_edge_feature_buckets.csv", buckets)
    write_csv(out_dir / "xuan_market_edge_policy_stats.csv", policies)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "markets_csv": str(Path(args.markets_csv).resolve()),
        "trades_csv": str(Path(args.trades_csv).resolve()),
        "days": args.days,
        "max_l1_age_ms": args.max_l1_age_ms,
        "min_bucket_n": args.min_bucket_n,
        "summary": compact(rows),
        "policy_stats": policies,
        "bucket_stats": buckets,
        "outputs": {
            "rows_csv": str((out_dir / "xuan_market_edge_feature_rows.csv").resolve()),
            "buckets_csv": str((out_dir / "xuan_market_edge_feature_buckets.csv").resolve()),
            "policies_csv": str((out_dir / "xuan_market_edge_policy_stats.csv").resolve()),
            "summary_json": str((out_dir / "xuan_market_edge_feature_summary.json").resolve()),
            "report_md": str((out_dir / "xuan_market_edge_feature_report.md").resolve()),
        },
    }
    (out_dir / "xuan_market_edge_feature_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out_dir / "xuan_market_edge_feature_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(out_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

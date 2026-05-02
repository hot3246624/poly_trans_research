#!/usr/bin/env python3
"""Analyze xuanxuan008 equal-size tranche ladders.

This script uses public Polymarket data-api trades plus optional local replay
SQLite L1 snapshots. It does not use private order truth and does not claim
queue priority or exact maker/taker status.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


XUAN = "0xcfb103c37c0234f524c632d964ed31f117b5f694"
DEFAULT_REPLAY_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_s(ts: int | float | None) -> str | None:
    if ts is None:
        return None
    return dt.datetime.fromtimestamp(float(ts), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_to_s(value: str | None) -> int | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    return int(dt.datetime.fromisoformat(text).timestamp())


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "p95": percentile(vals, 95),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def fetch_trades(user: str, page_size: int, max_rows: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while len(rows) < max_rows:
        limit = min(page_size, max_rows - len(rows))
        qs = urllib.parse.urlencode({"user": user, "limit": limit, "offset": offset})
        url = f"https://data-api.polymarket.com/trades?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            page = json.loads(resp.read().decode())
        if not page:
            break
        rows.extend(page)
        if len(page) < limit:
            break
        offset += limit
    return rows


def load_trades(args: argparse.Namespace) -> list[dict[str, Any]]:
    if args.input_json:
        return json.loads(Path(args.input_json).read_text(encoding="utf-8"))
    return fetch_trades(args.user, args.page_size, args.max_rows)


def is_btc_5m_buy(row: dict[str, Any], start_s: int | None, end_s: int | None) -> bool:
    ts = int(row.get("timestamp") or 0)
    return (
        str(row.get("slug", "")).startswith("btc-updown-5m-")
        and row.get("side") == "BUY"
        and (start_s is None or ts >= start_s)
        and (end_s is None or ts < end_s)
    )


def outcome_side(row: dict[str, Any]) -> str:
    return "YES" if int(row["outcomeIndex"]) == 0 else "NO"


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def size_match(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, max(a, b) * rel_tol)


def normalized_trade(row: dict[str, Any]) -> dict[str, Any]:
    size = float(row["size"])
    price = float(row["price"])
    return {
        "timestamp": int(row["timestamp"]),
        "iso": iso_s(int(row["timestamp"])),
        "condition_id": row["conditionId"],
        "slug": row["slug"],
        "title": row.get("title"),
        "outcome": row.get("outcome"),
        "market_side": outcome_side(row),
        "outcome_index": int(row["outcomeIndex"]),
        "price": price,
        "size": size,
        "usdc": float(row.get("usdcSize", price * size)),
        "transaction_hash": row.get("transactionHash"),
        "asset": row.get("asset"),
    }


def build_market_summaries(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_slug[trade["slug"]].append(trade)
    rows: list[dict[str, Any]] = []
    for slug, xs in by_slug.items():
        xs = sorted(xs, key=lambda item: (item["timestamp"], item.get("transaction_hash") or ""))
        qty = defaultdict(float)
        cost = defaultdict(float)
        for trade in xs:
            qty[trade["market_side"]] += trade["size"]
            cost[trade["market_side"]] += trade["usdc"]
        if not qty:
            continue
        yes_qty = qty.get("YES", 0.0)
        no_qty = qty.get("NO", 0.0)
        yes_avg = cost["YES"] / yes_qty if yes_qty > 0 else None
        no_avg = cost["NO"] / no_qty if no_qty > 0 else None
        paired_qty = min(yes_qty, no_qty)
        pair_cost = yes_avg + no_avg if yes_avg is not None and no_avg is not None else None
        start_s = int(slug.split("-")[-1])
        rows.append(
            {
                "slug": slug,
                "condition_id": xs[0]["condition_id"],
                "round_start_s": start_s,
                "round_start_iso": iso_s(start_s),
                "round_end_s": start_s + 300,
                "round_end_iso": iso_s(start_s + 300),
                "first_trade_s": xs[0]["timestamp"],
                "last_trade_s": xs[-1]["timestamp"],
                "first_trade_iso": xs[0]["iso"],
                "last_trade_iso": xs[-1]["iso"],
                "market_span_s": xs[-1]["timestamp"] - xs[0]["timestamp"],
                "trade_count": len(xs),
                "yes_qty": round(yes_qty, 6),
                "no_qty": round(no_qty, 6),
                "paired_qty": round(paired_qty, 6),
                "residual_qty": round(abs(yes_qty - no_qty), 6),
                "yes_avg_price": round(yes_avg, 6) if yes_avg is not None else None,
                "no_avg_price": round(no_avg, 6) if no_avg is not None else None,
                "market_pair_cost_proxy": round(pair_cost, 6) if pair_cost is not None else None,
            }
        )
    return sorted(rows, key=lambda row: row["round_start_s"])


def build_greedy_tranches(
    trades: list[dict[str, Any]],
    abs_tol: float,
    rel_tol: float,
) -> list[dict[str, Any]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_slug[trade["slug"]].append(trade)
    tranches: list[dict[str, Any]] = []
    for slug, xs in by_slug.items():
        xs = sorted(xs, key=lambda item: (item["timestamp"], item.get("transaction_hash") or ""))
        used = [False] * len(xs)
        start_s = int(slug.split("-")[-1])
        tranche_id = 0
        for i, first in enumerate(xs):
            if used[i]:
                continue
            for j in range(i + 1, len(xs)):
                second = xs[j]
                if used[j]:
                    continue
                if second["market_side"] == first["market_side"]:
                    continue
                if not size_match(first["size"], second["size"], abs_tol, rel_tol):
                    continue
                used[i] = True
                used[j] = True
                tranche_id += 1
                pair_cost = first["price"] + second["price"]
                tranches.append(
                    {
                        "slug": slug,
                        "condition_id": first["condition_id"],
                        "tranche_id": tranche_id,
                        "round_start_s": start_s,
                        "round_start_iso": iso_s(start_s),
                        "first_ts_s": first["timestamp"],
                        "first_iso": first["iso"],
                        "second_ts_s": second["timestamp"],
                        "second_iso": second["iso"],
                        "first_offset_s": first["timestamp"] - start_s,
                        "second_offset_s": second["timestamp"] - start_s,
                        "pair_delay_s": second["timestamp"] - first["timestamp"],
                        "size": round(first["size"], 6),
                        "size_diff": round(abs(first["size"] - second["size"]), 6),
                        "first_side": first["market_side"],
                        "second_side": second["market_side"],
                        "first_outcome": first["outcome"],
                        "second_outcome": second["outcome"],
                        "first_price": round(first["price"], 6),
                        "second_price": round(second["price"], 6),
                        "pair_cost": round(pair_cost, 6),
                        "pair_surplus": round(1.0 - pair_cost, 6),
                        "first_tx": first.get("transaction_hash"),
                        "second_tx": second.get("transaction_hash"),
                    }
                )
                break
    return sorted(tranches, key=lambda row: (row["first_ts_s"], row["slug"], row["tranche_id"]))


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def build_replay_db_map(replay_root: Path, days: list[str]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for day in days:
        path = replay_root / day / "crypto_5m.sqlite"
        if path.exists():
            out[day] = path
    return out


def find_l1_near(conn: sqlite3.Connection, condition_id: str, ts_ms: int, max_age_ms: int) -> sqlite3.Row | None:
    before = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=? AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    after = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=? AND recv_ms > ?
        ORDER BY recv_ms ASC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    candidates = [row for row in (before, after) if row is not None and abs(int(row["recv_ms"]) - ts_ms) <= max_age_ms]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(int(row["recv_ms"]) - ts_ms))


def find_l1_before(conn: sqlite3.Connection, condition_id: str, ts_ms: int, max_age_ms: int) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=? AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    if row is None or ts_ms - int(row["recv_ms"]) > max_age_ms:
        return None
    return row


def px(row: sqlite3.Row, side: str, kind: str) -> float | None:
    key = f"{'yes' if side == 'YES' else 'no'}_{kind}_px"
    return row[key]


def sz(row: sqlite3.Row, side: str, kind: str) -> float | None:
    key = f"{'yes' if side == 'YES' else 'no'}_{kind}_sz"
    return row[key]


def classify_buy_fill_against_l1(price: float, bid: float | None, ask: float | None) -> dict[str, Any]:
    """Classify public BUY price as maker/taker-like using nearest L1.

    For BUY trades, price near bid is consistent with a passive bid being hit
    by a seller. Price near ask is consistent with taking the ask. This is only
    a B-grade proxy because public data does not expose the hidden queue state.
    """
    out: dict[str, Any] = {
        "price_minus_bid_ticks": None,
        "ask_minus_price_ticks": None,
        "maker_taker_proxy": "unknown",
    }
    if bid is not None:
        out["price_minus_bid_ticks"] = round((float(price) - float(bid)) / 0.01, 6)
    if ask is not None:
        out["ask_minus_price_ticks"] = round((float(ask) - float(price)) / 0.01, 6)
    bid_dist = abs(float(price) - float(bid)) if bid is not None else None
    ask_dist = abs(float(ask) - float(price)) if ask is not None else None
    tol = 0.005001
    if bid_dist is not None and bid_dist <= tol and (ask_dist is None or bid_dist <= ask_dist):
        out["maker_taker_proxy"] = "maker_like_bid"
    elif ask_dist is not None and ask_dist <= tol and (bid_dist is None or ask_dist < bid_dist):
        out["maker_taker_proxy"] = "taker_like_ask"
    elif bid_dist is not None and ask_dist is not None:
        out["maker_taker_proxy"] = "inside_or_stale"
    return out


def add_l1_fill_context(
    row: dict[str, Any],
    prefix: str,
    l1: sqlite3.Row,
    ts_ms: int,
    side: str,
    price: float,
) -> None:
    side_bid = px(l1, side, "bid")
    side_ask = px(l1, side, "ask")
    row[f"{prefix}_l1_book_recv_ms"] = int(l1["recv_ms"])
    row[f"{prefix}_l1_book_age_ms"] = int(l1["recv_ms"]) - ts_ms
    row[f"{prefix}_l1_side_bid_px"] = side_bid
    row[f"{prefix}_l1_side_ask_px"] = side_ask
    row[f"{prefix}_l1_side_bid_sz"] = sz(l1, side, "bid")
    row[f"{prefix}_l1_side_ask_sz"] = sz(l1, side, "ask")
    proxy = classify_buy_fill_against_l1(price, side_bid, side_ask)
    row[f"{prefix}_price_minus_bid_ticks"] = proxy["price_minus_bid_ticks"]
    row[f"{prefix}_ask_minus_price_ticks"] = proxy["ask_minus_price_ticks"]
    row[f"{prefix}_maker_taker_proxy"] = proxy["maker_taker_proxy"]


def enrich_tranches_with_l1(
    tranches: list[dict[str, Any]],
    replay_root: Path,
    replay_days: list[str],
    max_age_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db_map = build_replay_db_map(replay_root, replay_days)
    conns: dict[str, sqlite3.Connection] = {}
    stats = {"replay_days_available": sorted(db_map), "l1_checked": 0, "l1_matched": 0, "l1_missing": 0}
    try:
        for row in tranches:
            day = dt.datetime.fromtimestamp(row["first_ts_s"], tz=dt.timezone.utc).strftime("%Y-%m-%d")
            db_path = db_map.get(day)
            row["l1_day_available"] = bool(db_path)
            row["l1_matched"] = False
            row["l1_book_recv_ms"] = None
            row["l1_book_age_ms"] = None
            row["l1_pair_ask_sum"] = None
            row["l1_pair_bid_sum"] = None
            row["l1_first_spread_ticks"] = None
            row["l1_opposite_spread_ticks"] = None
            row["l1_first_bid_px"] = None
            row["l1_first_ask_px"] = None
            row["l1_opposite_bid_px"] = None
            row["l1_opposite_ask_px"] = None
            row["l1_first_bid_sz"] = None
            row["l1_opposite_bid_sz"] = None
            row["first_maker_taker_proxy"] = "unknown"
            row["second_maker_taker_proxy"] = "unknown"
            row["second_l1_matched"] = False
            if not db_path:
                stats["l1_missing"] += 1
                continue
            stats["l1_checked"] += 1
            if day not in conns:
                conns[day] = connect_ro(db_path)
            ts_ms = int(row["first_ts_s"]) * 1000
            l1 = find_l1_before(conns[day], row["condition_id"], ts_ms, max_age_ms)
            if l1 is None:
                stats["l1_missing"] += 1
                continue
            first_side = row["first_side"]
            opp_side = opposite(first_side)
            yes_ask = l1["yes_ask_px"]
            no_ask = l1["no_ask_px"]
            yes_bid = l1["yes_bid_px"]
            no_bid = l1["no_bid_px"]
            first_bid = px(l1, first_side, "bid")
            first_ask = px(l1, first_side, "ask")
            opp_bid = px(l1, opp_side, "bid")
            opp_ask = px(l1, opp_side, "ask")
            row.update(
                {
                    "l1_matched": True,
                    "l1_book_recv_ms": int(l1["recv_ms"]),
                    "l1_book_age_ms": int(l1["recv_ms"]) - ts_ms,
                    "l1_pair_ask_sum": round(float(yes_ask) + float(no_ask), 6)
                    if yes_ask is not None and no_ask is not None
                    else None,
                    "l1_pair_bid_sum": round(float(yes_bid) + float(no_bid), 6)
                    if yes_bid is not None and no_bid is not None
                    else None,
                    "l1_first_bid_px": first_bid,
                    "l1_first_ask_px": first_ask,
                    "l1_opposite_bid_px": opp_bid,
                    "l1_opposite_ask_px": opp_ask,
                    "l1_first_bid_sz": sz(l1, first_side, "bid"),
                    "l1_opposite_bid_sz": sz(l1, opp_side, "bid"),
                    "l1_first_spread_ticks": round((float(first_ask) - float(first_bid)) / 0.01, 6)
                    if first_ask is not None and first_bid is not None
                    else None,
                    "l1_opposite_spread_ticks": round((float(opp_ask) - float(opp_bid)) / 0.01, 6)
                    if opp_ask is not None and opp_bid is not None
                    else None,
                }
            )
            add_l1_fill_context(
                row,
                "first",
                l1,
                ts_ms,
                first_side,
                float(row["first_price"]),
            )
            second_ts_ms = int(row["second_ts_s"]) * 1000
            second_l1 = find_l1_before(conns[day], row["condition_id"], second_ts_ms, max_age_ms)
            if second_l1 is not None:
                row["second_l1_matched"] = True
                add_l1_fill_context(
                    row,
                    "second",
                    second_l1,
                    second_ts_ms,
                    row["second_side"],
                    float(row["second_price"]),
                )
            stats["l1_matched"] += 1
    finally:
        for conn in conns.values():
            conn.close()
    return tranches, stats


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


def summarize_tranches(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {
            "tranche_count": 0,
            "pair_cost": summarize([]),
            "delay_s": summarize([]),
            "offset_s": summarize([]),
            "size": summarize([]),
        }
    total_size = sum(float(row["size"]) for row in rows)
    wavg_pair_cost = (
        sum(float(row["pair_cost"]) * float(row["size"]) for row in rows) / total_size if total_size > 0 else None
    )
    return {
        "tranche_count": len(rows),
        "total_size": round(total_size, 6),
        "pair_cost": summarize([row["pair_cost"] for row in rows]),
        "size_weighted_pair_cost": round(wavg_pair_cost, 6) if wavg_pair_cost is not None else None,
        "pair_cost_lt_0_90_count": sum(1 for row in rows if float(row["pair_cost"]) < 0.90),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in rows if float(row["pair_cost"]) < 0.90), len(rows)),
        "pair_cost_lt_0_95_count": sum(1 for row in rows if float(row["pair_cost"]) < 0.95),
        "pair_cost_lt_0_95_rate": rate(sum(1 for row in rows if float(row["pair_cost"]) < 0.95), len(rows)),
        "delay_s": summarize([row["pair_delay_s"] for row in rows]),
        "offset_s": summarize([row["first_offset_s"] for row in rows]),
        "size": summarize([row["size"] for row in rows]),
    }


def offset_bucket(offset_s: int | float) -> str:
    if offset_s < 15:
        return "000_015s"
    if offset_s < 30:
        return "015_030s"
    if offset_s < 60:
        return "030_060s"
    if offset_s < 120:
        return "060_120s"
    if offset_s < 240:
        return "120_240s"
    return "240_300s"


def delay_bucket(delay_s: int | float) -> str:
    if delay_s <= 2:
        return "000_002s"
    if delay_s <= 5:
        return "003_005s"
    if delay_s <= 15:
        return "006_015s"
    if delay_s <= 30:
        return "016_030s"
    if delay_s <= 60:
        return "031_060s"
    return "gt_060s"


def market_tranche_summary(markets: list[dict[str, Any]], tranches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tranches:
        by_slug[row["slug"]].append(row)
    out: list[dict[str, Any]] = []
    for market in markets:
        xs = by_slug.get(market["slug"], [])
        stats = summarize_tranches(xs)
        row = dict(market)
        row.update(
            {
                "tranche_count": stats["tranche_count"],
                "tranche_total_size": stats.get("total_size"),
                "tranche_pair_cost_avg": stats["pair_cost"]["avg"],
                "tranche_pair_cost_p50": stats["pair_cost"]["p50"],
                "tranche_pair_cost_wavg": stats.get("size_weighted_pair_cost"),
                "tranche_pair_cost_lt_0_90_count": stats.get("pair_cost_lt_0_90_count"),
                "tranche_delay_p50_s": stats["delay_s"]["p50"],
                "tranche_size_p50": stats["size"]["p50"],
            }
        )
        out.append(row)
    return out


def build_inventory_ledger(trades: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_slug[trade["slug"]].append(trade)
    event_rows: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    eps = 0.02
    for slug, xs in by_slug.items():
        xs = sorted(xs, key=lambda item: (item["timestamp"], item.get("transaction_hash") or ""))
        start_s = int(slug.split("-")[-1])
        end_s = start_s + 300
        qty = {"YES": 0.0, "NO": 0.0}
        cost = {"YES": 0.0, "NO": 0.0}
        max_residual_qty = 0.0
        max_residual_ratio = 0.0
        max_gross_qty = 0.0
        max_net_usdc = 0.0
        residual_qty_seconds = 0.0
        max_same_side_run_count = 0
        max_same_side_run_qty = 0.0
        current_run_side = None
        current_run_count = 0
        current_run_qty = 0.0
        same_side_add_before_clean_count = 0
        same_side_add_before_clean_qty = 0.0
        last_ts = xs[0]["timestamp"] if xs else start_s
        balance_crossings = 0
        was_clean = True
        first_clean_after_open_s = None
        for idx, trade in enumerate(xs, start=1):
            residual_before = abs(qty["YES"] - qty["NO"])
            residual_side_before = (
                "YES" if qty["YES"] > qty["NO"] + eps else "NO" if qty["NO"] > qty["YES"] + eps else "FLAT"
            )
            if current_run_side == trade["market_side"]:
                current_run_count += 1
                current_run_qty += trade["size"]
            else:
                current_run_side = trade["market_side"]
                current_run_count = 1
                current_run_qty = trade["size"]
            max_same_side_run_count = max(max_same_side_run_count, current_run_count)
            max_same_side_run_qty = max(max_same_side_run_qty, current_run_qty)
            if residual_side_before == trade["market_side"] and residual_before > eps:
                same_side_add_before_clean_count += 1
                same_side_add_before_clean_qty += trade["size"]
            residual_qty_seconds += residual_before * max(0, trade["timestamp"] - last_ts)
            qty[trade["market_side"]] += trade["size"]
            cost[trade["market_side"]] += trade["usdc"]
            paired_qty = min(qty["YES"], qty["NO"])
            residual_side = "YES" if qty["YES"] > qty["NO"] + eps else "NO" if qty["NO"] > qty["YES"] + eps else "FLAT"
            residual_qty = abs(qty["YES"] - qty["NO"])
            gross_qty = qty["YES"] + qty["NO"]
            residual_ratio = residual_qty / gross_qty if gross_qty > 0 else 0.0
            net_usdc_spent = cost["YES"] + cost["NO"]
            clean_now = residual_qty <= eps
            if clean_now and not was_clean:
                balance_crossings += 1
                if first_clean_after_open_s is None:
                    first_clean_after_open_s = trade["timestamp"]
            was_clean = clean_now
            max_residual_qty = max(max_residual_qty, residual_qty)
            max_residual_ratio = max(max_residual_ratio, residual_ratio)
            max_gross_qty = max(max_gross_qty, gross_qty)
            max_net_usdc = max(max_net_usdc, net_usdc_spent)
            event_rows.append(
                {
                    "slug": slug,
                    "condition_id": trade["condition_id"],
                    "event_idx": idx,
                    "ts_s": trade["timestamp"],
                    "iso": trade["iso"],
                    "round_offset_s": trade["timestamp"] - start_s,
                    "market_side": trade["market_side"],
                    "price": round(trade["price"], 6),
                    "size": round(trade["size"], 6),
                    "yes_qty": round(qty["YES"], 6),
                    "no_qty": round(qty["NO"], 6),
                    "paired_qty": round(paired_qty, 6),
                    "residual_side": residual_side,
                    "residual_qty": round(residual_qty, 6),
                    "residual_ratio": round(residual_ratio, 6),
                    "gross_qty": round(gross_qty, 6),
                    "net_usdc_spent": round(net_usdc_spent, 6),
                    "clean_now": clean_now,
                }
            )
            last_ts = trade["timestamp"]
        if xs:
            residual_qty_seconds += abs(qty["YES"] - qty["NO"]) * max(0, min(end_s, xs[-1]["timestamp"]) - last_ts)
        final_residual_qty = abs(qty["YES"] - qty["NO"])
        final_residual_side = "YES" if qty["YES"] > qty["NO"] + eps else "NO" if qty["NO"] > qty["YES"] + eps else "FLAT"
        total_pairable_qty = min(qty["YES"], qty["NO"])
        market_rows.append(
            {
                "slug": slug,
                "condition_id": xs[0]["condition_id"] if xs else None,
                "round_start_s": start_s,
                "round_start_iso": iso_s(start_s),
                "first_trade_s": xs[0]["timestamp"] if xs else None,
                "last_trade_s": xs[-1]["timestamp"] if xs else None,
                "first_offset_s": xs[0]["timestamp"] - start_s if xs else None,
                "last_offset_s": xs[-1]["timestamp"] - start_s if xs else None,
                "trade_count": len(xs),
                "yes_qty": round(qty["YES"], 6),
                "no_qty": round(qty["NO"], 6),
                "pairable_qty": round(total_pairable_qty, 6),
                "final_residual_side": final_residual_side,
                "final_residual_qty": round(final_residual_qty, 6),
                "final_clean": final_residual_qty <= eps,
                "max_residual_qty": round(max_residual_qty, 6),
                "max_residual_ratio": round(max_residual_ratio, 6),
                "max_gross_qty": round(max_gross_qty, 6),
                "max_net_usdc_spent": round(max_net_usdc, 6),
                "balance_crossings": balance_crossings,
                "max_same_side_run_count": max_same_side_run_count,
                "max_same_side_run_qty": round(max_same_side_run_qty, 6),
                "same_side_add_before_clean_count": same_side_add_before_clean_count,
                "same_side_add_before_clean_qty": round(same_side_add_before_clean_qty, 6),
                "first_clean_after_open_delay_s": first_clean_after_open_s - xs[0]["timestamp"]
                if first_clean_after_open_s is not None and xs
                else None,
                "residual_qty_seconds": round(residual_qty_seconds, 6),
            }
        )
    return (
        sorted(event_rows, key=lambda row: (row["ts_s"], row["slug"], row["event_idx"])),
        sorted(market_rows, key=lambda row: row["round_start_s"]),
    )


def summarize_inventory(markets: list[dict[str, Any]]) -> dict[str, Any]:
    if not markets:
        return {}
    return {
        "market_count": len(markets),
        "final_clean_count": sum(1 for row in markets if row["final_clean"]),
        "final_clean_rate": rate(sum(1 for row in markets if row["final_clean"]), len(markets)),
        "final_residual_qty": summarize([row["final_residual_qty"] for row in markets]),
        "max_residual_qty": summarize([row["max_residual_qty"] for row in markets]),
        "max_residual_ratio": summarize([row["max_residual_ratio"] for row in markets]),
        "max_gross_qty": summarize([row["max_gross_qty"] for row in markets]),
        "balance_crossings": summarize([row["balance_crossings"] for row in markets]),
        "max_same_side_run_count": summarize([row["max_same_side_run_count"] for row in markets]),
        "max_same_side_run_qty": summarize([row["max_same_side_run_qty"] for row in markets]),
        "same_side_add_before_clean_count": summarize([row["same_side_add_before_clean_count"] for row in markets]),
        "same_side_add_before_clean_qty": summarize([row["same_side_add_before_clean_qty"] for row in markets]),
        "first_clean_after_open_delay_s": summarize([row["first_clean_after_open_delay_s"] for row in markets]),
    }


def summarize_proxy(values: list[str]) -> dict[str, Any]:
    total = len(values)
    counts = {key: values.count(key) for key in sorted(set(values))}
    counts["total"] = total
    for key in ("maker_like_bid", "taker_like_ask", "inside_or_stale", "unknown"):
        counts[f"{key}_rate"] = rate(values.count(key), total)
    return counts


def cycle_class(row: dict[str, Any]) -> str:
    if not row["closed"]:
        return "failed_residual"
    if row["duration_s"] is not None and row["duration_s"] <= 30:
        return "clean_fast_extended" if row["same_side_add_count"] > 0 else "clean_fast"
    if row["pair_cost"] is not None and row["pair_cost"] < 0.95:
        return "clean_slow_improved"
    return "clean_slow"


def build_inventory_cycles(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_slug: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_slug[trade["slug"]].append(trade)
    cycles: list[dict[str, Any]] = []
    eps = 0.02
    for slug, xs in by_slug.items():
        xs = sorted(xs, key=lambda item: (item["timestamp"], item.get("transaction_hash") or ""))
        start_s = int(slug.split("-")[-1])
        qty = {"YES": 0.0, "NO": 0.0}
        cycle = None
        cycle_id = 0
        for trade in xs:
            before_residual_side = (
                "YES" if qty["YES"] > qty["NO"] + eps else "NO" if qty["NO"] > qty["YES"] + eps else "FLAT"
            )
            if cycle is None:
                cycle_id += 1
                cycle = {
                    "slug": slug,
                    "condition_id": trade["condition_id"],
                    "cycle_id": cycle_id,
                    "start_ts_s": trade["timestamp"],
                    "start_iso": trade["iso"],
                    "start_offset_s": trade["timestamp"] - start_s,
                    "first_side": trade["market_side"],
                    "first_price": trade["price"],
                    "first_size": trade["size"],
                    "trade_count": 0,
                    "yes_qty": 0.0,
                    "no_qty": 0.0,
                    "yes_cost": 0.0,
                    "no_cost": 0.0,
                    "max_residual_qty": 0.0,
                    "same_side_add_count": 0,
                    "same_side_add_qty": 0.0,
                    "closed": False,
                    "end_ts_s": None,
                    "end_iso": None,
                    "end_offset_s": None,
                    "end_side": None,
                    "end_price": None,
                    "end_size": None,
                    "duration_s": None,
                    "pair_cost": None,
                    "residual_side": None,
                    "residual_qty": None,
                }
            if before_residual_side == trade["market_side"]:
                cycle["same_side_add_count"] += 1
                cycle["same_side_add_qty"] += trade["size"]
            cycle["trade_count"] += 1
            cycle[f"{trade['market_side'].lower()}_qty"] += trade["size"]
            cycle[f"{trade['market_side'].lower()}_cost"] += trade["usdc"]
            qty[trade["market_side"]] += trade["size"]
            residual_qty = abs(qty["YES"] - qty["NO"])
            residual_side = "YES" if qty["YES"] > qty["NO"] + eps else "NO" if qty["NO"] > qty["YES"] + eps else "FLAT"
            cycle["max_residual_qty"] = max(cycle["max_residual_qty"], residual_qty)
            cycle["residual_side"] = residual_side
            cycle["residual_qty"] = residual_qty
            if residual_qty <= eps:
                pairable_qty = min(cycle["yes_qty"], cycle["no_qty"])
                pair_cost = (
                    cycle["yes_cost"] / cycle["yes_qty"] + cycle["no_cost"] / cycle["no_qty"]
                    if cycle["yes_qty"] > 0 and cycle["no_qty"] > 0 and pairable_qty > 0
                    else None
                )
                cycle.update(
                    {
                        "closed": True,
                        "end_ts_s": trade["timestamp"],
                        "end_iso": trade["iso"],
                        "end_offset_s": trade["timestamp"] - start_s,
                        "end_side": trade["market_side"],
                        "end_price": trade["price"],
                        "end_size": trade["size"],
                        "duration_s": trade["timestamp"] - cycle["start_ts_s"],
                        "pair_cost": pair_cost,
                    }
                )
                row = finalize_cycle(cycle)
                cycles.append(row)
                cycle = None
        if cycle is not None:
            row = finalize_cycle(cycle)
            cycles.append(row)
    return sorted(cycles, key=lambda row: (row["start_ts_s"], row["slug"], row["cycle_id"]))


def finalize_cycle(cycle: dict[str, Any]) -> dict[str, Any]:
    row = dict(cycle)
    for key in (
        "first_price",
        "first_size",
        "end_price",
        "end_size",
        "yes_qty",
        "no_qty",
        "yes_cost",
        "no_cost",
        "max_residual_qty",
        "same_side_add_qty",
        "residual_qty",
    ):
        if row.get(key) is not None:
            row[key] = round(float(row[key]), 6)
    if row.get("pair_cost") is not None:
        row["pair_cost"] = round(float(row["pair_cost"]), 6)
        row["pair_surplus"] = round(1.0 - float(row["pair_cost"]), 6)
    else:
        row["pair_surplus"] = None
    row["class"] = cycle_class(row)
    return row


def summarize_cycles(cycles: list[dict[str, Any]]) -> dict[str, Any]:
    if not cycles:
        return {}
    class_counts = collections_counter([row["class"] for row in cycles])
    closed = [row for row in cycles if row["closed"]]
    return {
        "cycle_count": len(cycles),
        "closed_count": len(closed),
        "closed_rate": rate(len(closed), len(cycles)),
        "class_counts": class_counts,
        "duration_s": summarize([row["duration_s"] for row in closed]),
        "pair_cost": summarize([row["pair_cost"] for row in closed]),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in closed if row["pair_cost"] is not None and row["pair_cost"] < 0.90), len(closed)),
        "same_side_extension_count": sum(1 for row in cycles if int(row["same_side_add_count"]) > 0),
        "same_side_extension_rate": rate(sum(1 for row in cycles if int(row["same_side_add_count"]) > 0), len(cycles)),
        "max_residual_qty": summarize([row["max_residual_qty"] for row in cycles]),
    }


def collections_counter(values: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items()))


def rolling_best(markets: list[dict[str, Any]], windows: list[int]) -> dict[str, Any]:
    paired = [row for row in markets if row.get("market_pair_cost_proxy") is not None]
    paired = sorted(paired, key=lambda row: row["round_start_s"])
    out: dict[str, Any] = {}
    for window in windows:
        best = None
        for idx in range(0, len(paired) - window + 1):
            xs = paired[idx : idx + window]
            avg = sum(float(row["market_pair_cost_proxy"]) for row in xs) / window
            total_qty = sum(float(row["paired_qty"]) for row in xs)
            wavg = (
                sum(float(row["market_pair_cost_proxy"]) * float(row["paired_qty"]) for row in xs) / total_qty
                if total_qty > 0
                else None
            )
            item = {
                "window": window,
                "start_iso": xs[0]["round_start_iso"],
                "end_iso": xs[-1]["round_end_iso"],
                "avg_pair_cost": round(avg, 6),
                "size_weighted_pair_cost": round(wavg, 6) if wavg is not None else None,
                "lt_0_90_count": sum(1 for row in xs if float(row["market_pair_cost_proxy"]) < 0.90),
                "markets": [
                    {
                        "slug": row["slug"],
                        "pair_cost": row["market_pair_cost_proxy"],
                        "paired_qty": row["paired_qty"],
                    }
                    for row in xs
                ],
            }
            if best is None or item["avg_pair_cost"] < best["avg_pair_cost"]:
                best = item
        out[str(window)] = best
    return out


def render_report(report: dict[str, Any]) -> str:
    full = report["tranche_summary"]
    latest = report["latest_window_summary"]
    lines = [
        "# Xuan Tranche Ladder Analysis",
        "",
        "## Scope",
        "",
        f"- user: `{report['user']}`",
        f"- fetched_trades: `{report['fetched_trade_count']}`",
        f"- btc_5m_buy_trades: `{report['btc_5m_buy_trade_count']}`",
        f"- trade_time_range: `{report['trade_time_range']['min_iso']}` to `{report['trade_time_range']['max_iso']}`",
        f"- filter_start: `{report['filter_start_iso']}`",
        f"- filter_end: `{report['filter_end_iso']}`",
        f"- size_match_abs_tol: `{report['size_match_abs_tol']}`",
        f"- size_match_rel_tol: `{report['size_match_rel_tol']}`",
        "- Source: public Polymarket data-api trades plus optional local replay L1.",
        "- This is not maker/taker truth and not queue-priority truth.",
        "",
        "## Tranche Summary",
        "",
        f"- tranches: `{full['tranche_count']}`",
        f"- pair_cost avg/p50/wavg: `{full['pair_cost']['avg']}` / `{full['pair_cost']['p50']}` / `{full['size_weighted_pair_cost']}`",
        f"- pair_cost < 0.90: `{full['pair_cost_lt_0_90_count']}` / `{full['tranche_count']}` = `{full['pair_cost_lt_0_90_rate']}`",
        f"- pair_cost < 0.95: `{full['pair_cost_lt_0_95_count']}` / `{full['tranche_count']}` = `{full['pair_cost_lt_0_95_rate']}`",
        f"- delay p50/p90 seconds: `{full['delay_s']['p50']}` / `{full['delay_s']['p90']}`",
        f"- first_offset p50/p90 seconds: `{full['offset_s']['p50']}` / `{full['offset_s']['p90']}`",
        f"- size p50/p90: `{full['size']['p50']}` / `{full['size']['p90']}`",
        "",
        "## Latest Window",
        "",
        f"- latest_window_start: `{report['latest_window_start_iso']}`",
        f"- tranches: `{latest['tranche_count']}`",
        f"- pair_cost avg/p50/wavg: `{latest['pair_cost']['avg']}` / `{latest['pair_cost']['p50']}` / `{latest['size_weighted_pair_cost']}`",
        f"- pair_cost < 0.90: `{latest['pair_cost_lt_0_90_count']}` / `{latest['tranche_count']}` = `{latest['pair_cost_lt_0_90_rate']}`",
        f"- delay p50 seconds: `{latest['delay_s']['p50']}`",
        "",
        "## Offset Buckets",
        "",
        "| bucket | n | avg pair | wavg pair | <0.90 | delay p50 | size p50 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for bucket, stats in report["offset_bucket_summary"].items():
        lines.append(
            f"| {bucket} | {stats['tranche_count']} | {stats['pair_cost']['avg']} | "
            f"{stats['size_weighted_pair_cost']} | {stats['pair_cost_lt_0_90_count']} | "
            f"{stats['delay_s']['p50']} | {stats['size']['p50']} |"
        )
    lines.extend(["", "## Rolling Best Market Windows", ""])
    for window, item in report["rolling_best_market_windows"].items():
        if not item:
            continue
        lines.append(
            f"- `{window}` markets: avg `{item['avg_pair_cost']}`, wavg `{item['size_weighted_pair_cost']}`, "
            f"lt0.90 `{item['lt_0_90_count']}`, `{item['start_iso']}` to `{item['end_iso']}`"
        )
    lines.extend(
        [
            "",
            "## Delay Buckets",
            "",
            "| bucket | n | avg pair | wavg pair | <0.90 | offset p50 | size p50 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for bucket, stats in report["delay_bucket_summary"].items():
        lines.append(
            f"| {bucket} | {stats['tranche_count']} | {stats['pair_cost']['avg']} | "
            f"{stats['size_weighted_pair_cost']} | {stats['pair_cost_lt_0_90_count']} | "
            f"{stats['offset_s']['p50']} | {stats['size']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## L1 Alignment",
            "",
            f"- replay_days_available: `{report['l1_stats']['replay_days_available']}`",
            f"- l1_checked: `{report['l1_stats']['l1_checked']}`",
            f"- l1_matched: `{report['l1_stats']['l1_matched']}`",
            f"- l1_missing: `{report['l1_stats']['l1_missing']}`",
            "",
            "## Maker/Taker Proxy From L1",
            "",
            f"- first leg proxy: `{report['l1_maker_taker_proxy']['first_leg']}`",
            f"- second leg proxy: `{report['l1_maker_taker_proxy']['second_leg']}`",
            "- `maker_like_bid` means public BUY price was near L1 bid; `taker_like_ask` means near L1 ask.",
            "- This is a proxy only; replay address fields are empty in the current market-side DB.",
            "",
            "## Inventory Control",
            "",
            f"- final clean markets: `{report['inventory_summary']['final_clean_count']}` / `{report['inventory_summary']['market_count']}` = `{report['inventory_summary']['final_clean_rate']}`",
            f"- max residual qty p50/p90: `{report['inventory_summary']['max_residual_qty']['p50']}` / `{report['inventory_summary']['max_residual_qty']['p90']}`",
            f"- max residual ratio p50/p90: `{report['inventory_summary']['max_residual_ratio']['p50']}` / `{report['inventory_summary']['max_residual_ratio']['p90']}`",
            f"- max same-side run count p50/p90: `{report['inventory_summary']['max_same_side_run_count']['p50']}` / `{report['inventory_summary']['max_same_side_run_count']['p90']}`",
            f"- same-side add before clean count p50/p90: `{report['inventory_summary']['same_side_add_before_clean_count']['p50']}` / `{report['inventory_summary']['same_side_add_before_clean_count']['p90']}`",
            f"- first clean after open delay p50/p90: `{report['inventory_summary']['first_clean_after_open_delay_s']['p50']}` / `{report['inventory_summary']['first_clean_after_open_delay_s']['p90']}`",
            "",
            "## Inventory Cycles",
            "",
            f"- cycles: `{report['cycle_summary']['cycle_count']}`",
            f"- closed cycles: `{report['cycle_summary']['closed_count']}` / `{report['cycle_summary']['cycle_count']}` = `{report['cycle_summary']['closed_rate']}`",
            f"- class_counts: `{report['cycle_summary']['class_counts']}`",
            f"- duration p50/p90: `{report['cycle_summary']['duration_s']['p50']}` / `{report['cycle_summary']['duration_s']['p90']}`",
            f"- closed cycle pair_cost avg/p50: `{report['cycle_summary']['pair_cost']['avg']}` / `{report['cycle_summary']['pair_cost']['p50']}`",
            f"- same-side extension cycles: `{report['cycle_summary']['same_side_extension_count']}` / `{report['cycle_summary']['cycle_count']}` = `{report['cycle_summary']['same_side_extension_rate']}`",
            "",
            "## Interpretation",
            "",
            "- The latest behavior is better described as equal-size tranche laddering than one market-level pair.",
            "- Recent low-cost rounds exist, but the broad average is not below 0.90 in this public sample.",
            "- Median tranche delay is short enough to match the 30s completion question, but low pair cost mostly comes from slower completion buckets.",
            "- Inventory metrics separate controlled temporary residuals from true one-way exposure.",
            "- The missing piece is exact queue priority and whether each public BUY is maker or taker; current replay has no address truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=XUAN)
    parser.add_argument("--input-json")
    parser.add_argument("--output-dir", default="data/exports/xuan_tranche_ladder_20260501")
    parser.add_argument("--max-rows", type=int, default=3000)
    parser.add_argument("--page-size", type=int, default=500)
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--latest-window-start-iso", default="2026-05-01T00:00:00Z")
    parser.add_argument("--size-match-abs-tol", type=float, default=0.02)
    parser.add_argument("--size-match-rel-tol", type=float, default=0.0005)
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--replay-days", default=",".join(DEFAULT_REPLAY_DAYS))
    parser.add_argument("--max-l1-age-ms", type=int, default=1500)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_rows = load_trades(args)
    (output_dir / "xuan_trades_raw.json").write_text(json.dumps(raw_rows, ensure_ascii=False, indent=2), encoding="utf-8")

    start_s = parse_iso_to_s(args.start_iso)
    end_s = parse_iso_to_s(args.end_iso)
    latest_start_s = parse_iso_to_s(args.latest_window_start_iso)
    trades = [normalized_trade(row) for row in raw_rows if is_btc_5m_buy(row, start_s, end_s)]
    markets = build_market_summaries(trades)
    tranches = build_greedy_tranches(trades, args.size_match_abs_tol, args.size_match_rel_tol)
    replay_days = [day.strip() for day in args.replay_days.split(",") if day.strip()]
    tranches, l1_stats = enrich_tranches_with_l1(tranches, Path(args.replay_root), replay_days, args.max_l1_age_ms)
    market_rows = market_tranche_summary(markets, tranches)
    inventory_events, inventory_markets = build_inventory_ledger(trades)
    inventory_cycles = build_inventory_cycles(trades)

    write_csv(output_dir / "xuan_tranche_ladder_tranches.csv", tranches)
    write_csv(output_dir / "xuan_tranche_ladder_markets.csv", market_rows)
    write_csv(output_dir / "xuan_inventory_events.csv", inventory_events)
    write_csv(output_dir / "xuan_inventory_markets.csv", inventory_markets)
    write_csv(output_dir / "xuan_inventory_cycles.csv", inventory_cycles)

    latest_tranches = [row for row in tranches if latest_start_s is not None and row["first_ts_s"] >= latest_start_s]
    offset_buckets = {
        bucket: summarize_tranches([row for row in tranches if offset_bucket(row["first_offset_s"]) == bucket])
        for bucket in ("000_015s", "015_030s", "030_060s", "060_120s", "120_240s", "240_300s")
    }
    delay_buckets = {
        bucket: summarize_tranches([row for row in tranches if delay_bucket(row["pair_delay_s"]) == bucket])
        for bucket in ("000_002s", "003_005s", "006_015s", "016_030s", "031_060s", "gt_060s")
    }
    trade_ts = [int(row.get("timestamp") or 0) for row in raw_rows if row.get("timestamp")]
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "user": args.user,
        "fetched_trade_count": len(raw_rows),
        "btc_5m_buy_trade_count": len(trades),
        "filter_start_iso": args.start_iso,
        "filter_end_iso": args.end_iso,
        "latest_window_start_iso": args.latest_window_start_iso,
        "trade_time_range": {
            "min_s": min(trade_ts) if trade_ts else None,
            "max_s": max(trade_ts) if trade_ts else None,
            "min_iso": iso_s(min(trade_ts)) if trade_ts else None,
            "max_iso": iso_s(max(trade_ts)) if trade_ts else None,
        },
        "planned_outage": {
            "start_iso": iso_ms(PLANNED_OUTAGE_START_MS),
            "end_iso": iso_ms(PLANNED_OUTAGE_END_MS),
        },
        "size_match_abs_tol": args.size_match_abs_tol,
        "size_match_rel_tol": args.size_match_rel_tol,
        "market_count": len(markets),
        "tranche_summary": summarize_tranches(tranches),
        "latest_window_summary": summarize_tranches(latest_tranches),
        "offset_bucket_summary": offset_buckets,
        "delay_bucket_summary": delay_buckets,
        "rolling_best_market_windows": rolling_best(market_rows, [6, 12, 24, 36]),
        "l1_stats": l1_stats,
        "l1_maker_taker_proxy": {
            "first_leg": summarize_proxy([row.get("first_maker_taker_proxy", "unknown") for row in tranches]),
            "second_leg": summarize_proxy([row.get("second_maker_taker_proxy", "unknown") for row in tranches]),
        },
        "inventory_summary": summarize_inventory(inventory_markets),
        "cycle_summary": summarize_cycles(inventory_cycles),
        "outputs": {
            "raw_json": str((output_dir / "xuan_trades_raw.json").resolve()),
            "tranches_csv": str((output_dir / "xuan_tranche_ladder_tranches.csv").resolve()),
            "markets_csv": str((output_dir / "xuan_tranche_ladder_markets.csv").resolve()),
            "inventory_events_csv": str((output_dir / "xuan_inventory_events.csv").resolve()),
            "inventory_markets_csv": str((output_dir / "xuan_inventory_markets.csv").resolve()),
            "inventory_cycles_csv": str((output_dir / "xuan_inventory_cycles.csv").resolve()),
            "summary_json": str((output_dir / "xuan_tranche_ladder_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_tranche_ladder_report.md").resolve()),
        },
    }
    (output_dir / "xuan_tranche_ladder_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "xuan_tranche_ladder_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "tranches": len(tranches), "markets": len(markets)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

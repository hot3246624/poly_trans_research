#!/usr/bin/env python3
"""Verify taker-BUY cache-search finalists against replay SQLite.

This verifier is intentionally independent of the candidate cache.  It uses the
search result rows only as parameter sets, then reruns the policy directly on
`replay_published` with strict L1 snapshots:

    md_book_l1.recv_ms <= trigger_ts_ms ORDER BY recv_ms DESC LIMIT 1

The implementation avoids the old full market L1/L2 scans.  It queries indexed
raw replay rows around each trigger, so single-profile verification can run on
the collector without blocking cache-search agents on the backtest server.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DAYS = "2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07"
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Params:
    rank: int
    price_lo: float
    price_hi: float
    size_lo: float
    size_hi: float
    first_lo: float
    first_hi: float
    offset_lo: int
    offset_hi: int
    max_l1_pair: float
    pair_ceiling: float
    side_alignment: str
    block_after_residual: bool
    cooldown_s: int
    cache_rows: str | None
    cache_pnl: str | None
    cache_min_day_pnl: str | None


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def parse_float(value: Any, name: str) -> float:
    try:
        out = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid float for {name}: {value!r}") from exc
    if not math.isfinite(out):
        raise ValueError(f"non-finite float for {name}: {value!r}")
    return out


def parse_intish(value: Any, name: str) -> int:
    return int(float(str(value).strip()))


def read_search_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_candidate_cache_rows(path: Path) -> dict[str, list[dict[str, str]]]:
    by_day: dict[str, list[dict[str, str]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            day = str(row.get("day") or "")[:10]
            if day:
                by_day[day].append(row)
    return by_day


def row_to_params(rank: int, row: dict[str, str]) -> Params:
    return Params(
        rank=rank,
        price_lo=parse_float(row["price_lo"], "price_lo"),
        price_hi=parse_float(row["price_hi"], "price_hi"),
        size_lo=parse_float(row["size_lo"], "size_lo"),
        size_hi=parse_float(row["size_hi"], "size_hi"),
        first_lo=parse_float(row["first_lo"], "first_lo"),
        first_hi=parse_float(row["first_hi"], "first_hi"),
        offset_lo=parse_intish(row["offset_lo"], "offset_lo"),
        offset_hi=parse_intish(row["offset_hi"], "offset_hi"),
        max_l1_pair=parse_float(row["max_l1_pair"], "max_l1_pair"),
        pair_ceiling=parse_float(row["pair_ceiling"], "pair_ceiling"),
        side_alignment=(row.get("side_alignment") or "any").strip() or "any",
        block_after_residual=parse_bool(row.get("block_after_residual")),
        cooldown_s=parse_intish(row.get("cooldown_s") or 10, "cooldown_s"),
        cache_rows=row.get("rows"),
        cache_pnl=row.get("pnl"),
        cache_min_day_pnl=row.get("min_day_pnl"),
    )


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    conn.execute("PRAGMA cache_size = -200000")
    conn.execute("PRAGMA temp_store = MEMORY")
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


def summarize(values: Iterable[float | int | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
    }


def ask_levels(row: sqlite3.Row) -> list[tuple[float, float]]:
    levels = []
    for idx in range(1, 6):
        px = row[f"ask{idx}_px"]
        sz = row[f"ask{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    return levels


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


def l1_book_at_or_before(
    conn: sqlite3.Connection,
    condition_id: str,
    ts_ms: int,
    max_age_ms: int,
) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, capture_seq DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    if row is None:
        return None
    age_ms = ts_ms - int(row["recv_ms"])
    if age_ms < 0 or age_ms > max_age_ms:
        return None
    return {
        "recv_ms": int(row["recv_ms"]),
        "age_ms": age_ms,
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


def latest_l2_sweep(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    ts_ms: int,
    clip: float,
    max_age_ms: int,
) -> tuple[float | None, int | None, float | None, float | None]:
    row = conn.execute(
        """
        SELECT recv_ms, ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND market_side = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC, id DESC
        LIMIT 1
        """,
        (condition_id, side, ts_ms - max_age_ms, ts_ms),
    ).fetchone()
    if row is None:
        return None, None, None, None
    age_ms = ts_ms - int(row["recv_ms"])
    vwap, filled, worst = sweep_vwap(ask_levels(row), clip)
    if vwap is None:
        return None, age_ms, worst, filled
    return vwap, age_ms, worst, filled


def first_completion(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    start_ms: int,
    end_ms: int,
    first_price: float,
    clip: float,
    pair_ceiling: float,
) -> dict[str, Any] | None:
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
    )
    for row in rows:
        vwap, filled, worst = sweep_vwap(ask_levels(row), clip)
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


def day_bounds_ms(day: str) -> tuple[int, int]:
    start = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc)
    start_ms = int(start.timestamp() * 1000)
    return start_ms, start_ms + 86_400_000 - 1


def cache_row_matches(row: dict[str, str], params: Params) -> bool:
    try:
        price = parse_float(row.get("public_trade_price"), "public_trade_price")
        size = parse_float(row.get("public_trade_size"), "public_trade_size")
        first = parse_float(row.get("first_l2_vwap"), "first_l2_vwap")
        offset = parse_float(row.get("offset_s"), "offset_s")
        l1_pair = parse_float(row.get("l1_immediate_pair"), "l1_immediate_pair")
    except ValueError:
        return False
    if params.side_alignment != "any" and row.get("side_alignment") != params.side_alignment:
        return False
    if price < params.price_lo or price >= params.price_hi:
        return False
    if size < params.size_lo or size >= params.size_hi:
        return False
    if first < params.first_lo or first >= params.first_hi:
        return False
    if offset < params.offset_lo or offset >= params.offset_hi:
        return False
    if l1_pair > params.max_l1_pair:
        return False
    return True


def load_markets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC'
          AND m.interval_sec = 300
          AND s.winner_side IN ('YES', 'NO')
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        market_start = int(row["start_ms"])
        market_end = int(row["end_ms"])
        if market_end <= TRUSTED_START_MS:
            continue
        if market_start < OUTAGE_END_MS and market_end > OUTAGE_START_MS:
            continue
        out.append(row)
    return out


def load_candidate_trades_for_market(
    conn: sqlite3.Connection,
    market: sqlite3.Row,
    params: Params,
) -> list[dict[str, Any]]:
    market_start = int(market["start_ms"])
    market_end = int(market["end_ms"])
    trigger_min = max(market_start, TRUSTED_START_MS) + params.offset_lo * 1000
    trigger_max = min(market_end, market_start + params.offset_hi * 1000)
    if trigger_max < trigger_min:
        return []
    rows = conn.execute(
        """
        SELECT
            t.id AS trade_row_id,
            t.condition_id,
            t.trade_ts_ms,
            t.market_side,
            t.price,
            t.size,
            ? AS slug,
            ? AS start_ms,
            ? AS end_ms,
            ? AS winner_side
        FROM md_trades t
        WHERE t.condition_id = ?
          AND t.trade_ts_ms IS NOT NULL
          AND t.trade_ts_ms >= ?
          AND t.trade_ts_ms <= ?
          AND t.taker_side = 'BUY'
          AND t.market_side IN ('YES', 'NO')
          AND t.price >= ?
          AND t.price < ?
          AND t.size >= ?
          AND t.size < ?
        ORDER BY t.trade_ts_ms, t.id
        """,
        (
            market["slug"],
            market_start,
            market_end,
            market["winner_side"],
            market["condition_id"],
            trigger_min,
            trigger_max,
            params.price_lo,
            params.price_hi,
            params.size_lo,
            params.size_hi,
        ),
    ).fetchall()
    return [dict(row) for row in rows]


def load_candidate_trades_full_scan(conn: sqlite3.Connection, params: Params) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for market in load_markets(conn):
        out.extend(load_candidate_trades_for_market(conn, market, params))
    return out


def resolve_cache_trade(conn: sqlite3.Connection, cache_row: dict[str, str]) -> dict[str, Any]:
    condition_id = str(cache_row["condition_id"])
    ts_ms = int(float(cache_row["trigger_ts_ms"]))
    side = str(cache_row["first_side"])
    rows = conn.execute(
        """
        SELECT
            t.id AS trade_row_id,
            t.condition_id,
            t.trade_ts_ms,
            t.market_side,
            t.price,
            t.size,
            m.slug,
            m.start_ms,
            m.end_ms,
            s.winner_side
        FROM md_trades t
        JOIN market_meta m ON m.condition_id = t.condition_id
        LEFT JOIN settlement_records s ON s.condition_id = t.condition_id
        WHERE t.condition_id = ?
          AND t.trade_ts_ms = ?
          AND t.market_side = ?
          AND t.taker_side = 'BUY'
          AND s.winner_side IN ('YES', 'NO')
        ORDER BY t.id
        """,
        (condition_id, ts_ms, side),
    ).fetchall()
    if not rows:
        raise RuntimeError(f"cache trigger missing in replay: condition_id={condition_id} ts_ms={ts_ms} side={side}")
    cache_price = parse_float(cache_row.get("public_trade_price"), "public_trade_price")
    cache_size = parse_float(cache_row.get("public_trade_size"), "public_trade_size")
    for row in rows:
        if abs(float(row["price"]) - cache_price) <= 1e-9 and abs(float(row["size"]) - cache_size) <= 1e-9:
            return dict(row)
    raise RuntimeError(
        "cache trigger trade price/size mismatch in replay: "
        f"condition_id={condition_id} ts_ms={ts_ms} side={side} cache_price={cache_price} cache_size={cache_size}"
    )


def load_candidate_trades_from_cache_index(
    conn: sqlite3.Connection,
    day: str,
    params: Params,
    cache_by_day: dict[str, list[dict[str, str]]],
) -> list[dict[str, Any]]:
    out = []
    for row in cache_by_day.get(day, []):
        if cache_row_matches(row, params):
            out.append(resolve_cache_trade(conn, row))
    out.sort(key=lambda r: (str(r["condition_id"]), int(r["trade_ts_ms"]), int(r["trade_row_id"])))
    return out


def simulate_day(conn: sqlite3.Connection, day: str, params: Params, args: argparse.Namespace) -> list[dict[str, Any]]:
    cache_by_day = getattr(args, "_candidate_cache_by_day", None)
    if cache_by_day is not None:
        trades = load_candidate_trades_from_cache_index(conn, day, params, cache_by_day)
    else:
        trades = load_candidate_trades_full_scan(conn, params)
    rows: list[dict[str, Any]] = []
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_condition[str(trade["condition_id"])].append(trade)

    for condition_id, condition_trades in by_condition.items():
        condition_trades.sort(key=lambda r: (int(r["trade_ts_ms"]), int(r["trade_row_id"])))
        first_trade = condition_trades[0]
        market_start = int(first_trade["start_ms"])
        market_end = int(first_trade["end_ms"])
        cursor_ms = max(market_start, TRUSTED_START_MS) + params.offset_lo * 1000
        for trade in condition_trades:
            ts_ms = int(trade["trade_ts_ms"])
            if ts_ms < cursor_ms:
                continue
            side = str(trade["market_side"])
            book = l1_book_at_or_before(conn, condition_id, ts_ms, args.max_l1_age_ms)
            if book is None:
                continue
            alignment = side_alignment(book, side)
            if alignment is None:
                continue
            if params.side_alignment != "any" and alignment != params.side_alignment:
                continue

            first_price, first_age_ms, first_worst_px, first_filled = latest_l2_sweep(
                conn, condition_id, side, ts_ms, args.clip, args.max_l2_age_ms
            )
            if first_price is None:
                continue
            if first_price < params.first_lo or first_price >= params.first_hi:
                continue

            opp = other(side)
            opp_ask = book[opp]["ask"]
            if opp_ask is None:
                continue
            l1_immediate_pair = first_price + float(opp_ask)
            if l1_immediate_pair > params.max_l1_pair + 1e-9:
                continue

            completion = first_completion(
                conn,
                condition_id,
                opp,
                ts_ms,
                min(market_end, ts_ms + args.completion_s * 1000),
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
                "trigger_iso": iso_ms(ts_ms),
                "offset_s": round((ts_ms - market_start) / 1000.0, 3),
                "first_side": side,
                "side_alignment": alignment,
                "first_is_winner": side == first_trade["winner_side"],
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
                        "completion_iso": iso_ms(int(completion["completion_ts_ms"])),
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
        "strict_l1_age_ms": summarize([row.get("strict_l1_age_ms") for row in rows]),
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
        "# Strict Raw Replay Verification",
        "",
        "## Aggregate",
        "",
        "| scope | rows | closed | first winner | residual | residual winner | pair p50 | L1 age p90 | pnl | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    all_item = report["aggregate"]["all"]
    lines.append(
        f"| all | {all_item['rows']} | {all_item['closed_rate']} | {all_item['first_winner_rate']} | "
        f"{all_item['residual']} | {all_item['residual_winner_rate']} | {all_item['pair_cost']['p50']} | "
        f"{all_item['strict_l1_age_ms']['p90']} | {all_item['pnl']} | {all_item['roi_on_first_cost']} |"
    )
    lines.extend(["", "## By Day", "", "| day | rows | closed | first winner | pnl | ROI |", "|---|---:|---:|---:|---:|---:|"])
    for day, item in report["aggregate"]["by_day"].items():
        lines.append(
            f"| {day} | {item['rows']} | {item['closed_rate']} | {item['first_winner_rate']} | "
            f"{item['pnl']} | {item['roi_on_first_cost']} |"
        )
    return "\n".join(lines) + "\n"


def verify_one(params: Params, args: argparse.Namespace, finalist_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    day_stats = []
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_stats.append({"day": day, "status": "missing_db", "rows": 0})
            continue
        with ro_connect(db_path) as conn:
            day_rows = simulate_day(conn, day, params, args)
        rows.extend(day_rows)
        day_stats.append({"day": day, "status": "ok", "rows": len(day_rows)})
        if args.progress:
            print(json.dumps({"rank": params.rank, "day": day, "rows": len(day_rows)}, ensure_ascii=False), flush=True)

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
        "event_index_policy": "strict_cache_trigger_index" if getattr(args, "_candidate_cache_by_day", None) is not None else "independent_replay_trade_scan",
        "truth_policy": (
            "raw replay SQLite re-prices selected events; cache is used only as a trigger index"
            if getattr(args, "_candidate_cache_by_day", None) is not None
            else "raw replay SQLite verification; cache is not used for event discovery or outcomes"
        ),
        "day_stats": day_stats,
        "aggregate": aggregate(rows),
    }
    finalist_dir.mkdir(parents=True, exist_ok=True)
    write_csv(finalist_dir / "taker_buy_strict_raw_replay_rows.csv", rows)
    (finalist_dir / "taker_buy_strict_raw_replay_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (finalist_dir / "taker_buy_strict_raw_replay_report.md").write_text(render_report(report), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--days", default=DEFAULT_DAYS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--candidate-cache-csv",
        type=Path,
        default=None,
        help="Optional strict V1 cache CSV used only as a trigger index; raw replay is still queried for verification.",
    )
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--rank", type=int, action="append", help="1-based search-result rank to verify; may be repeated.")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--max-l1-age-ms", type=int, default=3000)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    search_rows = read_search_rows(args.search_results_csv)
    if args.candidate_cache_csv is not None:
        args._candidate_cache_by_day = read_candidate_cache_rows(args.candidate_cache_csv)
    selected_ranks = set(args.rank or range(1, min(args.top_n, len(search_rows)) + 1))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for rank, row in enumerate(search_rows, start=1):
        if rank not in selected_ranks:
            continue
        params = row_to_params(rank, row)
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
                "raw_rows": aggregate.get("rows"),
                "raw_pnl": aggregate.get("pnl"),
                "raw_roi_on_first_cost": aggregate.get("roi_on_first_cost"),
                "raw_l1_age_p90_ms": aggregate.get("strict_l1_age_ms", {}).get("p90"),
            }
        )

    summary = {
        "generated_at_utc": utc_now(),
        "search_results_csv": str(args.search_results_csv),
        "replay_root": str(args.replay_root),
        "days": args.days,
        "candidate_cache_csv": str(args.candidate_cache_csv) if args.candidate_cache_csv else None,
        "event_index_policy": "strict_cache_trigger_index" if args.candidate_cache_csv else "independent_replay_trade_scan",
        "selected_ranks": sorted(selected_ranks),
        "truth_policy": "raw replay SQLite verification; cache search is not sufficient for deployment",
        "results": results,
    }
    (args.output_dir / "taker_buy_strict_raw_replay_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

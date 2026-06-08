#!/usr/bin/env python3
"""BTC 5m market-side replay backtest.

Reads replay SQLite in read-only mode and writes market-side episode summaries.
This is a public-market proxy: it does not use xuan private/public activity
tables and does not use own execution truth.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_274_700_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def day_start_ms(day: str) -> int:
    return int(dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def pct(num: int | float, den: int | float) -> float | None:
    if not den:
        return None
    return round(float(num) / float(den), 6)


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


def summarize_values(values: Iterable[float]) -> dict[str, Any]:
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


def one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def get_day_data_max_ms(conn: sqlite3.Connection) -> int | None:
    book_max = one(conn, "SELECT MAX(recv_ms) FROM md_book_l1")
    trade_max = one(conn, "SELECT MAX(trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL")
    vals = [int(v) for v in (book_max, trade_max) if v is not None]
    return max(vals) if vals else None


def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and a_end > b_start


def get_book_near_first_leg(conn: sqlite3.Connection, condition_id: str, ts_ms: int) -> sqlite3.Row | None:
    before = conn.execute(
        """
        SELECT recv_ms, source_ts_ms,
               yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
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
        SELECT recv_ms, source_ts_ms,
               yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=? AND recv_ms > ?
        ORDER BY recv_ms ASC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    candidates = [row for row in (before, after) if row is not None and abs(int(row["recv_ms"]) - ts_ms) <= 1000]
    if not candidates:
        return None
    return min(candidates, key=lambda row: abs(int(row["recv_ms"]) - ts_ms))


def fetch_buy_qty_counts(conn: sqlite3.Connection, metas: list[sqlite3.Row]) -> dict[str, dict[str, Any]]:
    if not metas:
        return {}
    condition_ids = [str(meta["condition_id"]) for meta in metas]
    placeholders = ",".join("?" for _ in condition_ids)
    rows = conn.execute(
        f"""
        SELECT t.condition_id,
               COUNT(*) AS trade_rows,
               SUM(CASE WHEN t.taker_side='BUY' THEN 1 ELSE 0 END) AS buy_trade_rows,
               SUM(CASE WHEN t.taker_side='BUY' AND t.market_side='YES' THEN t.size ELSE 0 END) AS yes_buy_qty,
               SUM(CASE WHEN t.taker_side='BUY' AND t.market_side='NO' THEN t.size ELSE 0 END) AS no_buy_qty
        FROM md_trades t
        JOIN market_meta m ON m.condition_id=t.condition_id
        WHERE t.condition_id IN ({placeholders})
          AND t.trade_ts_ms IS NOT NULL
          AND t.market_side IN ('YES', 'NO')
          AND t.trade_ts_ms >= m.start_ms
          AND t.trade_ts_ms < m.end_ms
        GROUP BY t.condition_id
        """,
        tuple(condition_ids),
    ).fetchall()
    return {
        str(row["condition_id"]): {
            "trade_rows": int(row["trade_rows"] or 0),
            "buy_trade_rows": int(row["buy_trade_rows"] or 0),
            "yes_buy_qty": float(row["yes_buy_qty"] or 0.0),
            "no_buy_qty": float(row["no_buy_qty"] or 0.0),
        }
        for row in rows
    }


def fetch_episode_trade_proxies(
    conn: sqlite3.Connection,
    metas: list[sqlite3.Row],
    qty_counts: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for meta in metas:
        condition_id = str(meta["condition_id"])
        start_ms = int(meta["start_ms"])
        end_ms = int(meta["end_ms"])
        qty = qty_counts.get(condition_id, {})
        first = conn.execute(
            """
            SELECT id AS first_id, trade_ts_ms AS first_ts_ms,
                   market_side AS first_side, price AS first_price, size AS first_size
            FROM md_trades
            WHERE condition_id=?
              AND trade_ts_ms IS NOT NULL
              AND market_side IN ('YES', 'NO')
              AND taker_side='BUY'
              AND trade_ts_ms >= ?
              AND trade_ts_ms < ?
            ORDER BY trade_ts_ms, id
            LIMIT 1
            """,
            (condition_id, start_ms, end_ms),
        ).fetchone()
        if first is None:
            continue
        opposite = conn.execute(
            """
            SELECT id AS opposite_id, trade_ts_ms AS opposite_ts_ms,
                   market_side AS opposite_side, price AS opposite_price, size AS opposite_size
            FROM md_trades
            WHERE condition_id=?
              AND trade_ts_ms IS NOT NULL
              AND market_side IN ('YES', 'NO')
              AND taker_side='BUY'
              AND market_side != ?
              AND trade_ts_ms >= ?
              AND trade_ts_ms < ?
              AND (trade_ts_ms > ? OR (trade_ts_ms=? AND id > ?))
            ORDER BY trade_ts_ms, id
            LIMIT 1
            """,
            (
                condition_id,
                first["first_side"],
                start_ms,
                end_ms,
                first["first_ts_ms"],
                first["first_ts_ms"],
                first["first_id"],
            ),
        ).fetchone()
        if opposite is None:
            same_side_qty = None
        else:
            # Same-side accumulation before the first opposite fill is not part
            # of the first-pass target and is expensive without a covering
            # replay index. Keep the field explicit but uncomputed.
            same_side_qty = None
        row: dict[str, Any] = {
            "condition_id": condition_id,
            "trade_rows": int(qty.get("trade_rows", 0) or 0),
            "buy_trade_rows": int(qty.get("buy_trade_rows", 0) or 0),
            "yes_buy_qty": float(qty.get("yes_buy_qty", 0.0) or 0.0),
            "no_buy_qty": float(qty.get("no_buy_qty", 0.0) or 0.0),
            "first_id": first["first_id"],
            "first_ts_ms": first["first_ts_ms"],
            "first_side": first["first_side"],
            "first_price": first["first_price"],
            "first_size": first["first_size"],
            "same_side_qty_before_opposite": float(same_side_qty) if same_side_qty is not None else None,
        }
        if opposite is not None:
            row.update(
                {
                    "opposite_id": opposite["opposite_id"],
                    "opposite_ts_ms": opposite["opposite_ts_ms"],
                    "opposite_side": opposite["opposite_side"],
                    "opposite_price": opposite["opposite_price"],
                    "opposite_size": opposite["opposite_size"],
                }
            )
        else:
            row.update(
                {
                    "opposite_id": None,
                    "opposite_ts_ms": None,
                    "opposite_side": None,
                    "opposite_price": None,
                    "opposite_size": None,
                }
            )
        out[condition_id] = row
    return out


def fetch_trade_counts(conn: sqlite3.Connection, condition_ids: list[str]) -> dict[str, int]:
    if not condition_ids:
        return {}
    placeholders = ",".join("?" for _ in condition_ids)
    rows = conn.execute(
        f"""
        SELECT condition_id, COUNT(*) AS trade_rows
        FROM md_trades
        WHERE condition_id IN ({placeholders})
          AND trade_ts_ms IS NOT NULL
          AND market_side IN ('YES', 'NO')
        GROUP BY condition_id
        """,
        tuple(condition_ids),
    ).fetchall()
    return {str(row["condition_id"]): int(row["trade_rows"] or 0) for row in rows}


def compute_market_episode(
    conn: sqlite3.Connection,
    meta: sqlite3.Row,
    ep: dict[str, Any] | None,
    trade_rows: int,
    day: str,
    day_status: str,
) -> dict[str, Any]:
    condition_id = str(meta["condition_id"])
    has_buy = ep is not None
    yes_buy_qty = float(ep["yes_buy_qty"] or 0.0) if ep else 0.0
    no_buy_qty = float(ep["no_buy_qty"] or 0.0) if ep else 0.0

    base: dict[str, Any] = {
        "date": day,
        "day_status": day_status,
        "condition_id": condition_id,
        "slug": str(meta["slug"]),
        "start_ms": int(meta["start_ms"]),
        "end_ms": int(meta["end_ms"]),
        "start_iso": iso_ms(int(meta["start_ms"])),
        "end_iso": iso_ms(int(meta["end_ms"])),
        "official_outcome": meta["official_outcome"],
        "settle_ms": meta["settle_ms"],
        "settle_iso": iso_ms(int(meta["settle_ms"])) if meta["settle_ms"] is not None else None,
        "trade_rows": int(ep["trade_rows"] or trade_rows) if ep else int(trade_rows),
        "buy_trade_rows": int(ep["buy_trade_rows"] or 0) if ep else 0,
        "yes_buy_qty": round(yes_buy_qty, 6),
        "no_buy_qty": round(no_buy_qty, 6),
        "round_window_buy_net_diff_yes_minus_no": round(yes_buy_qty - no_buy_qty, 6),
        "round_window_buy_abs_net_diff": round(abs(yes_buy_qty - no_buy_qty), 6),
        "has_buy_episode": has_buy,
    }
    if ep is None:
        return base

    first_side = str(ep["first_side"])
    opposite_side = "NO" if first_side == "YES" else "YES"
    first_ts = int(ep["first_ts_ms"])
    first_price = float(ep["first_price"])
    first_size = float(ep["first_size"])
    has_opposite = ep["opposite_ts_ms"] is not None
    same_side_qty_before_opposite = (
        float(ep["same_side_qty_before_opposite"]) if ep["same_side_qty_before_opposite"] is not None else None
    )
    opposite_qty_before_first_opposite = float(ep["opposite_size"] or 0.0) if has_opposite else 0.0

    book = get_book_near_first_leg(conn, condition_id, first_ts)
    book_fields: dict[str, Any] = {
        "first_l1_book_recv_ms": None,
        "first_l1_book_age_ms": None,
        "first_l1_yes_bid_px": None,
        "first_l1_yes_ask_px": None,
        "first_l1_no_bid_px": None,
        "first_l1_no_ask_px": None,
        "first_l1_yes_bid_sz": None,
        "first_l1_yes_ask_sz": None,
        "first_l1_no_bid_sz": None,
        "first_l1_no_ask_sz": None,
        "first_l1_pair_ask_sum": None,
        "first_l1_selected_ask_px": None,
        "first_l1_other_ask_px": None,
        "first_l1_selected_is_high_ask": None,
        "first_l1_selected_minus_other_ask_px": None,
        "first_l1_selected_minus_other_ask_sz": None,
    }
    if book is not None:
        yes_ask = book["yes_ask_px"]
        no_ask = book["no_ask_px"]
        selected_ask = yes_ask if first_side == "YES" else no_ask
        other_ask = no_ask if first_side == "YES" else yes_ask
        selected_ask_sz = book["yes_ask_sz"] if first_side == "YES" else book["no_ask_sz"]
        other_ask_sz = book["no_ask_sz"] if first_side == "YES" else book["yes_ask_sz"]
        book_fields.update(
            {
                "first_l1_book_recv_ms": int(book["recv_ms"]),
                "first_l1_book_age_ms": int(book["recv_ms"]) - first_ts,
                "first_l1_yes_bid_px": book["yes_bid_px"],
                "first_l1_yes_ask_px": yes_ask,
                "first_l1_no_bid_px": book["no_bid_px"],
                "first_l1_no_ask_px": no_ask,
                "first_l1_yes_bid_sz": book["yes_bid_sz"],
                "first_l1_yes_ask_sz": book["yes_ask_sz"],
                "first_l1_no_bid_sz": book["no_bid_sz"],
                "first_l1_no_ask_sz": book["no_ask_sz"],
                "first_l1_pair_ask_sum": round(float(yes_ask) + float(no_ask), 6)
                if yes_ask is not None and no_ask is not None
                else None,
                "first_l1_selected_ask_px": selected_ask,
                "first_l1_other_ask_px": other_ask,
                "first_l1_selected_is_high_ask": bool(selected_ask is not None and other_ask is not None and selected_ask >= other_ask),
                "first_l1_selected_minus_other_ask_px": round(float(selected_ask) - float(other_ask), 6)
                if selected_ask is not None and other_ask is not None
                else None,
                "first_l1_selected_minus_other_ask_sz": round(float(selected_ask_sz) - float(other_ask_sz), 6)
                if selected_ask_sz is not None and other_ask_sz is not None
                else None,
            }
        )

    base.update(
        {
            "first_leg_side": first_side,
            "first_leg_price": round(first_price, 6),
            "first_leg_size": round(first_size, 6),
            "first_leg_ts_ms": first_ts,
            "first_leg_iso": iso_ms(first_ts),
            "first_leg_offset_s": round((first_ts - int(meta["start_ms"])) / 1000, 3),
            "has_opposite": bool(has_opposite),
            "same_side_qty_before_first_opposite": round(same_side_qty_before_opposite, 6)
            if same_side_qty_before_opposite is not None
            else None,
            "same_side_add_qty_before_first_opposite": round(max(0.0, same_side_qty_before_opposite - first_size), 6)
            if same_side_qty_before_opposite is not None
            else None,
            "opposite_qty_at_first_opposite": round(opposite_qty_before_first_opposite, 6),
        }
    )
    base.update(book_fields)
    if not has_opposite:
        return base

    opposite_ts = int(ep["opposite_ts_ms"])
    opposite_price = float(ep["opposite_price"])
    delay_ms = opposite_ts - first_ts
    pair_cost = first_price + opposite_price
    base.update(
        {
            "opposite_side": opposite_side,
            "first_opposite_ts_ms": opposite_ts,
            "first_opposite_iso": iso_ms(opposite_ts),
            "first_opposite_delay_s": round(delay_ms / 1000, 3),
            "first_opposite_price": round(opposite_price, 6),
            "first_opposite_size": round(float(ep["opposite_size"]), 6),
            "completion_30s": delay_ms <= 30_000,
            "pair_cost_first_trade_proxy": round(pair_cost, 6),
            "pair_surplus_first_trade_proxy": round(1.0 - pair_cost, 6),
        }
    )
    return base


def status_for_day(day: str, max_data_ms: int | None) -> str:
    if day == "2026-04-30":
        return "partial_available_window"
    if day == "2026-04-27":
        return "trusted_start_partial_day"
    if day == "2026-04-28":
        return "planned_outage_excluded"
    return "full_day"


def load_day(replay_root: Path, day: str, trusted_start_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    db_path = replay_root / day / "crypto_5m.sqlite"
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    with connect_ro(db_path) as conn:
        day_start = day_start_ms(day)
        day_end = day_start + 86_400_000
        max_data_ms = get_day_data_max_ms(conn)
        if max_data_ms is None:
            data_end = day_end
        elif 0 <= day_end - max_data_ms <= 1000:
            # Full-day captures often end a few milliseconds before midnight.
            # Do not drop the last complete 5m round because of that boundary.
            data_end = day_end
        else:
            data_end = min(day_end, max_data_ms)
        day_status = status_for_day(day, max_data_ms)
        metas = conn.execute(
            """
            SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.official_outcome, s.settle_ms
            FROM market_meta m
            LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
            WHERE m.symbol='BTC'
              AND m.interval_sec=300
              AND m.start_ms >= ?
              AND m.start_ms < ?
              AND m.end_ms <= ?
              AND m.end_ms > ?
            ORDER BY m.start_ms
            """,
            (day_start, day_end, data_end, trusted_start_ms),
        ).fetchall()
        eligible_metas: list[sqlite3.Row] = []
        excluded_outage = 0
        for meta in metas:
            start_ms = int(meta["start_ms"])
            end_ms = int(meta["end_ms"])
            if overlaps(start_ms, end_ms, PLANNED_OUTAGE_START_MS, PLANNED_OUTAGE_END_MS):
                excluded_outage += 1
                continue
            eligible_metas.append(meta)

        qty_counts = fetch_buy_qty_counts(conn, eligible_metas)
        episode_proxies = fetch_episode_trade_proxies(conn, eligible_metas, qty_counts)
        trade_counts = {condition_id: int(row.get("trade_rows", 0) or 0) for condition_id, row in qty_counts.items()}

        rows: list[dict[str, Any]] = []
        for meta in eligible_metas:
            condition_id = str(meta["condition_id"])
            rows.append(
                compute_market_episode(
                    conn,
                    meta,
                    episode_proxies.get(condition_id),
                    trade_counts.get(condition_id, 0),
                    day,
                    day_status,
                )
            )
        db_summary = {
            "date": day,
            "db_path": str(db_path),
            "day_status": day_status,
            "day_start_iso": iso_ms(day_start),
            "day_end_iso": iso_ms(day_end),
            "max_data_ms": max_data_ms,
            "max_data_iso": iso_ms(max_data_ms),
            "data_end_ms": data_end,
            "data_end_iso": iso_ms(data_end),
            "eligible_btc_markets": len(rows),
            "excluded_planned_outage_markets": excluded_outage,
        }
        return rows, db_summary


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    episode_rows = [r for r in rows if r.get("has_buy_episode")]
    opposite_rows = [r for r in episode_rows if r.get("has_opposite")]
    pair_rows = [r for r in opposite_rows if r.get("pair_cost_first_trade_proxy") is not None]
    completion_30s = [r for r in opposite_rows if r.get("completion_30s")]
    high_ask_rows = [r for r in episode_rows if r.get("first_l1_selected_is_high_ask") is not None]
    l1_rows = [r for r in episode_rows if r.get("first_l1_pair_ask_sum") is not None]

    by_first_side: dict[str, int] = {}
    for r in episode_rows:
        side = str(r.get("first_leg_side"))
        by_first_side[side] = by_first_side.get(side, 0) + 1

    day_stats: dict[str, Any] = {}
    for day in sorted({str(r["date"]) for r in rows}):
        day_rows = [r for r in rows if r["date"] == day]
        day_stats[day] = aggregate_compact(day_rows)

    return {
        "market_count": len(rows),
        "buy_episode_count": len(episode_rows),
        "opposite_observed_count": len(opposite_rows),
        "completion_30s_count": len(completion_30s),
        "opposite_observed_rate": pct(len(opposite_rows), len(episode_rows)),
        "completion_30s_rate_among_buy_episodes": pct(len(completion_30s), len(episode_rows)),
        "completion_30s_rate_among_opposite_observed": pct(len(completion_30s), len(opposite_rows)),
        "first_side_counts": by_first_side,
        "pair_cost_first_trade_proxy": summarize_values([r["pair_cost_first_trade_proxy"] for r in pair_rows]),
        "pair_cost_lte_0_98_rate": pct(sum(1 for r in pair_rows if r["pair_cost_first_trade_proxy"] <= 0.98), len(pair_rows)),
        "pair_cost_lte_0_99_rate": pct(sum(1 for r in pair_rows if r["pair_cost_first_trade_proxy"] <= 0.99), len(pair_rows)),
        "pair_cost_lte_1_00_rate": pct(sum(1 for r in pair_rows if r["pair_cost_first_trade_proxy"] <= 1.00), len(pair_rows)),
        "pair_cost_gt_1_02_rate": pct(sum(1 for r in pair_rows if r["pair_cost_first_trade_proxy"] > 1.02), len(pair_rows)),
        "first_opposite_delay_s": summarize_values([r["first_opposite_delay_s"] for r in opposite_rows]),
        "first_leg_offset_s": summarize_values([r["first_leg_offset_s"] for r in episode_rows]),
        "same_side_add_qty_before_first_opposite": summarize_values(
            [r["same_side_add_qty_before_first_opposite"] for r in episode_rows]
        ),
        "round_window_buy_abs_net_diff": summarize_values([r["round_window_buy_abs_net_diff"] for r in rows]),
        "first_l1_pair_ask_sum": summarize_values([r["first_l1_pair_ask_sum"] for r in l1_rows]),
        "first_l1_selected_minus_other_ask_px": summarize_values(
            [r["first_l1_selected_minus_other_ask_px"] for r in l1_rows]
        ),
        "selected_high_ask_rate": pct(sum(1 for r in high_ask_rows if r["first_l1_selected_is_high_ask"]), len(high_ask_rows)),
        "daily": day_stats,
    }


def aggregate_compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    episode_rows = [r for r in rows if r.get("has_buy_episode")]
    opposite_rows = [r for r in episode_rows if r.get("has_opposite")]
    pair_rows = [r for r in opposite_rows if r.get("pair_cost_first_trade_proxy") is not None]
    completed = [r for r in opposite_rows if r.get("completion_30s")]
    return {
        "market_count": len(rows),
        "buy_episode_count": len(episode_rows),
        "opposite_observed_count": len(opposite_rows),
        "completion_30s_count": len(completed),
        "completion_30s_rate_among_buy_episodes": pct(len(completed), len(episode_rows)),
        "pair_cost_p50": percentile([r["pair_cost_first_trade_proxy"] for r in pair_rows], 50),
        "pair_cost_p90": percentile([r["pair_cost_first_trade_proxy"] for r in pair_rows], 90),
        "first_opposite_delay_p50_s": percentile([r["first_opposite_delay_s"] for r in opposite_rows], 50),
        "first_opposite_delay_p90_s": percentile([r["first_opposite_delay_s"] for r in opposite_rows], 90),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any]) -> str:
    agg = report["aggregate"]
    lines = [
        "# BTC 5m Market-Side Replay Backtest",
        "",
        "## Data Boundary",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- trusted_start: `{report['trusted_start_iso']}`",
        "- included DBs: `2026-04-27`, `2026-04-28`, `2026-04-29`, `2026-04-30`",
        "- excluded DBs: `2026-04-24`, `2026-04-26`",
        "- planned_outage_excluded: `2026-04-28T11:00:00Z` to `2026-04-28T12:00:00Z`",
        "- `2026-04-30` is treated as an available-window partial day.",
        "- This report uses public market replay only. `xuan_episode_ready=false`; `own_execution_truth_ready=false`.",
        "",
        "## Aggregate",
        "",
        f"- markets: `{agg['market_count']}`",
        f"- buy episodes: `{agg['buy_episode_count']}`",
        f"- opposite observed: `{agg['opposite_observed_count']}`",
        f"- completion_30s / buy episodes: `{agg['completion_30s_rate_among_buy_episodes']}`",
        f"- completion_30s / opposite observed: `{agg['completion_30s_rate_among_opposite_observed']}`",
        f"- pair_cost p50/p90: `{agg['pair_cost_first_trade_proxy']['p50']}` / `{agg['pair_cost_first_trade_proxy']['p90']}`",
        f"- pair_cost <= 0.99: `{agg['pair_cost_lte_0_99_rate']}`",
        f"- pair_cost > 1.02: `{agg['pair_cost_gt_1_02_rate']}`",
        f"- first_opposite_delay p50/p90 seconds: `{agg['first_opposite_delay_s']['p50']}` / `{agg['first_opposite_delay_s']['p90']}`",
        f"- first_l1_pair_ask_sum p50/p90: `{agg['first_l1_pair_ask_sum']['p50']}` / `{agg['first_l1_pair_ask_sum']['p90']}`",
        f"- selected_high_ask_rate: `{agg['selected_high_ask_rate']}`",
        "",
        "## Daily",
        "",
        "| day | markets | buy episodes | hit30 rate | pair cost p50 | pair cost p90 | delay p50s | delay p90s |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for day, row in agg["daily"].items():
        lines.append(
            f"| {day} | {row['market_count']} | {row['buy_episode_count']} | "
            f"{row['completion_30s_rate_among_buy_episodes']} | {row['pair_cost_p50']} | {row['pair_cost_p90']} | "
            f"{row['first_opposite_delay_p50_s']} | {row['first_opposite_delay_p90_s']} |"
        )
    lines.extend(
        [
            "",
            "## Semantics",
            "",
        "- `first_leg` is the first public BTC 5m BUY trade inside the round window `[start_ms, end_ms)`.",
        "- `opposite` is the first subsequent public BUY trade on the other side inside the same round window.",
            "- `pair_cost_first_trade_proxy = first_leg_price + first_opposite_price`.",
        "- `net_diff` fields are round-window public market BUY size imbalance proxies, not our inventory truth.",
            "- These are market-side proxies and should not be described as xuan exact episodes.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--output-dir", default="data/exports/btc5m_market_side_backtest")
    parser.add_argument("--trusted-start-ms", type=int, default=TRUSTED_START_MS)
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    output_dir = Path(args.output_dir)
    days = [d.strip() for d in args.days.split(",") if d.strip()]

    all_rows: list[dict[str, Any]] = []
    db_summaries: list[dict[str, Any]] = []
    for day in days:
        rows, summary = load_day(replay_root, day, args.trusted_start_ms)
        all_rows.extend(rows)
        db_summaries.append(summary)

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "trusted_start_ms": args.trusted_start_ms,
        "trusted_start_iso": iso_ms(args.trusted_start_ms),
        "planned_outage": {
            "start_ms": PLANNED_OUTAGE_START_MS,
            "end_ms": PLANNED_OUTAGE_END_MS,
            "start_iso": iso_ms(PLANNED_OUTAGE_START_MS),
            "end_iso": iso_ms(PLANNED_OUTAGE_END_MS),
        },
        "db_summaries": db_summaries,
        "xuan_episode_ready": False,
        "own_execution_truth_ready": False,
        "semantics": {
            "first_leg": "first public BTC 5m BUY trade in a market",
            "opposite": "first subsequent public BUY trade on the other side",
            "pair_cost_first_trade_proxy": "first_leg_price + first_opposite_price",
            "net_diff": "round-window public market BUY size imbalance proxy, not own inventory truth",
        },
        "aggregate": aggregate(all_rows),
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_market_episode_summary.csv", all_rows)
    (output_dir / "btc5m_market_backtest_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_market_backtest_report.md").write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "aggregate": report["aggregate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

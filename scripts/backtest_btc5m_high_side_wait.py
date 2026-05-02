#!/usr/bin/env python3
"""BTC 5m high-side-first wait opportunity scanner.

This is a market-side replay test. It reads only replay SQLite and does not
claim maker/taker fill truth. The goal is to test whether high-side-first opens
have cheaper opposite asks after a controlled wait budget.
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


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_275_000_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


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
    w = pos - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
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


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_value(row: sqlite3.Row, side: str, field: str) -> float | None:
    prefix = "yes" if side == "YES" else "no"
    value = row[f"{prefix}_{field}"]
    return None if value is None else float(value)


def mid(row: sqlite3.Row, side: str) -> float | None:
    bid = side_value(row, side, "bid_px")
    ask = side_value(row, side, "ask_px")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def first_price(row: sqlite3.Row, side: str, kind: str) -> float | None:
    if kind == "bid":
        return side_value(row, side, "bid_px")
    if kind == "ask":
        return side_value(row, side, "ask_px")
    if kind == "mid":
        return mid(row, side)
    raise ValueError(f"unsupported first price kind: {kind}")


def load_btc_markets(conn: sqlite3.Connection, day_max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        WHERE symbol='BTC' AND interval_sec=300
        ORDER BY start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if PLANNED_OUTAGE_START_MS <= start_ms < PLANNED_OUTAGE_END_MS:
            continue
        if day_max_ms is not None and start_ms >= day_max_ms:
            continue
        out.append(row)
    return out


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT MAX(x) FROM (
          SELECT MAX(recv_ms) AS x FROM md_book_l1
          UNION ALL
          SELECT MAX(trade_ts_ms) AS x FROM md_trades WHERE trade_ts_ms IS NOT NULL
        )
        """
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def load_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                   yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
            FROM md_book_l1
            WHERE condition_id=? AND recv_ms >= ? AND recv_ms < ?
            ORDER BY recv_ms
            """,
            (condition_id, start_ms, end_ms),
        )
    )


def candidate_from_book(
    market: sqlite3.Row,
    book: sqlite3.Row,
    first_price_kind: str,
    clip_size: float,
    require_first_depth: bool,
    min_offset_s: int,
    max_offset_s: int,
    min_first_price: float,
    max_first_price: float,
) -> dict[str, Any] | None:
    offset_s = (int(book["recv_ms"]) - int(market["start_ms"])) / 1000.0
    if offset_s < min_offset_s or offset_s >= max_offset_s:
        return None
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    side = "YES" if yes_mid >= no_mid else "NO"
    opp = other(side)
    px = first_price(book, side, first_price_kind)
    opp_ask = side_value(book, opp, "ask_px")
    if px is None or opp_ask is None:
        return None
    if px < min_first_price or px >= max_first_price:
        return None
    first_depth_field = "ask_sz" if first_price_kind == "ask" else "bid_sz"
    first_depth = side_value(book, side, first_depth_field)
    if require_first_depth and (first_depth is None or first_depth < clip_size):
        return None
    opp_ask_sz = side_value(book, opp, "ask_sz")
    return {
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_ms": int(market["start_ms"]),
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "recv_ms": int(book["recv_ms"]),
        "recv_iso": iso_ms(int(book["recv_ms"])),
        "offset_s": round(offset_s, 3),
        "first_side": side,
        "first_price_kind": first_price_kind,
        "first_price": round(px, 6),
        "first_depth": first_depth,
        "opp_side": opp,
        "opp_ask_now": round(opp_ask, 6),
        "opp_ask_sz_now": opp_ask_sz,
        "immediate_pair_cost": round(px + opp_ask, 6),
        "yes_mid": round(yes_mid, 6),
        "no_mid": round(no_mid, 6),
        "mid_skew": round(abs(yes_mid - no_mid), 6),
    }


def enrich_waits(candidate: dict[str, Any], books: list[sqlite3.Row], wait_budgets_s: list[int], clip_size: float) -> None:
    start_ms = int(candidate["recv_ms"])
    opp = candidate["opp_side"]
    first_px = float(candidate["first_price"])
    for budget_s in wait_budgets_s:
        end_ms = start_ms + budget_s * 1000
        best_px = None
        best_sz = None
        best_ms = None
        for book in books:
            recv_ms = int(book["recv_ms"])
            if recv_ms < start_ms or recv_ms > end_ms:
                continue
            px = side_value(book, opp, "ask_px")
            sz = side_value(book, opp, "ask_sz")
            if px is None:
                continue
            if best_px is None or px < best_px:
                best_px = px
                best_sz = sz
                best_ms = recv_ms
        prefix = f"wait_{budget_s}s"
        if best_px is None:
            candidate[f"{prefix}_best_opp_ask"] = None
            candidate[f"{prefix}_best_pair_cost"] = None
            candidate[f"{prefix}_improvement"] = None
            candidate[f"{prefix}_best_delay_s"] = None
            candidate[f"{prefix}_opp_ask_sz"] = None
            candidate[f"{prefix}_depth_ge_clip"] = None
            continue
        pair_cost = first_px + best_px
        candidate[f"{prefix}_best_opp_ask"] = round(best_px, 6)
        candidate[f"{prefix}_best_pair_cost"] = round(pair_cost, 6)
        candidate[f"{prefix}_improvement"] = round(float(candidate["immediate_pair_cost"]) - pair_cost, 6)
        candidate[f"{prefix}_best_delay_s"] = round((best_ms - start_ms) / 1000.0, 3) if best_ms is not None else None
        candidate[f"{prefix}_opp_ask_sz"] = best_sz
        candidate[f"{prefix}_depth_ge_clip"] = bool(best_sz is not None and best_sz >= clip_size)


def scan_market(
    market: sqlite3.Row,
    books: list[sqlite3.Row],
    args: argparse.Namespace,
    wait_budgets_s: list[int],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    next_sample_ms = int(market["start_ms"])
    for book in books:
        recv_ms = int(book["recv_ms"])
        if recv_ms < next_sample_ms:
            continue
        next_sample_ms = recv_ms + args.sample_interval_sec * 1000
        candidate = candidate_from_book(
            market,
            book,
            args.first_price_kind,
            args.clip_size,
            args.require_first_depth,
            args.min_offset_s,
            args.max_offset_s,
            args.min_first_price,
            args.max_first_price,
        )
        if candidate is None:
            continue
        enrich_waits(candidate, books, wait_budgets_s, args.clip_size)
        rows.append(candidate)
        if args.one_per_market:
            break
    return rows


def summarize_candidates(rows: list[dict[str, Any]], wait_budgets_s: list[int]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "candidate_count": len(rows),
        "market_count": len(set(row["slug"] for row in rows)),
        "immediate_pair_cost": summarize([row.get("immediate_pair_cost") for row in rows]),
        "first_price": summarize([row.get("first_price") for row in rows]),
        "offset_s": summarize([row.get("offset_s") for row in rows]),
    }
    for budget_s in wait_budgets_s:
        prefix = f"wait_{budget_s}s"
        costs = [row.get(f"{prefix}_best_pair_cost") for row in rows]
        improvements = [row.get(f"{prefix}_improvement") for row in rows]
        summary[prefix] = {
            "pair_cost": summarize(costs),
            "improvement": summarize(improvements),
            "pair_cost_lt_0_90_rate": rate(
                sum(1 for row in rows if row.get(f"{prefix}_best_pair_cost") is not None and float(row[f"{prefix}_best_pair_cost"]) < 0.90),
                len(rows),
            ),
            "pair_cost_lt_0_95_rate": rate(
                sum(1 for row in rows if row.get(f"{prefix}_best_pair_cost") is not None and float(row[f"{prefix}_best_pair_cost"]) < 0.95),
                len(rows),
            ),
            "improvement_gt_0_03_rate": rate(
                sum(1 for row in rows if row.get(f"{prefix}_improvement") is not None and float(row[f"{prefix}_improvement"]) >= 0.03),
                len(rows),
            ),
            "depth_ge_clip_rate": rate(
                sum(1 for row in rows if row.get(f"{prefix}_depth_ge_clip") is True),
                len(rows),
            ),
        }
    return summary


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


def render_report(summary: dict[str, Any], wait_budgets_s: list[int]) -> str:
    lines = [
        "# BTC 5m High-Side Wait Opportunity",
        "",
        "## Config",
        "",
        f"- days: `{summary['config']['days']}`",
        f"- first_price_kind: `{summary['config']['first_price_kind']}`",
        f"- clip_size: `{summary['config']['clip_size']}`",
        f"- require_first_depth: `{summary['config']['require_first_depth']}`",
        f"- offset: `{summary['config']['min_offset_s']}` to `{summary['config']['max_offset_s']}` seconds",
        f"- first_price: `{summary['config']['min_first_price']}` to `{summary['config']['max_first_price']}`",
        f"- one_per_market: `{summary['config']['one_per_market']}`",
        "",
        "## Summary",
        "",
        f"- candidates: `{summary['stats']['candidate_count']}`",
        f"- markets: `{summary['stats']['market_count']}`",
        f"- immediate pair p50/p90: `{summary['stats']['immediate_pair_cost']['p50']}` / `{summary['stats']['immediate_pair_cost']['p90']}`",
        "",
        "| wait | pair p50 | pair p90 | <0.90 | <0.95 | improvement p50 | improvement >=0.03 | depth>=clip |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for budget_s in wait_budgets_s:
        item = summary["stats"][f"wait_{budget_s}s"]
        lines.append(
            f"| {budget_s}s | {item['pair_cost']['p50']} | {item['pair_cost']['p90']} | "
            f"{item['pair_cost_lt_0_90_rate']} | {item['pair_cost_lt_0_95_rate']} | "
            f"{item['improvement']['p50']} | {item['improvement_gt_0_03_rate']} | {item['depth_ge_clip_rate']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This measures market-side price opportunity only; it does not prove our first leg or second leg would fill.",
            "- If wait improves pair cost only when depth is insufficient, it is not directly tradable without queue truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", default="data/exports/btc5m_high_side_wait_20260501")
    parser.add_argument("--first-price-kind", choices=("bid", "ask", "mid"), default="ask")
    parser.add_argument("--clip-size", type=float, default=180.0)
    parser.add_argument("--require-first-depth", action="store_true")
    parser.add_argument("--sample-interval-sec", type=int, default=5)
    parser.add_argument("--min-offset-s", type=int, default=180)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--min-first-price", type=float, default=0.50)
    parser.add_argument("--max-first-price", type=float, default=0.85)
    parser.add_argument("--wait-budgets-s", default="50,70,98")
    parser.add_argument("--one-per-market", action="store_true", default=True)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    wait_budgets_s = [int(x.strip()) for x in args.wait_budgets_s.split(",") if x.strip()]
    rows: list[dict[str, Any]] = []
    market_count = 0
    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conn = connect_ro(db_path)
        try:
            max_ms = day_max_ms(conn)
            markets = load_btc_markets(conn, max_ms)
            market_count += len(markets)
            for market in markets:
                books = load_books(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                if not books:
                    continue
                rows.extend(scan_market(market, books, args, wait_budgets_s))
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_high_side_wait_candidates.csv", rows)
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "config": {
            "days": days,
            "first_price_kind": args.first_price_kind,
            "clip_size": args.clip_size,
            "require_first_depth": args.require_first_depth,
            "sample_interval_sec": args.sample_interval_sec,
            "min_offset_s": args.min_offset_s,
            "max_offset_s": args.max_offset_s,
            "min_first_price": args.min_first_price,
            "max_first_price": args.max_first_price,
            "wait_budgets_s": wait_budgets_s,
            "one_per_market": args.one_per_market,
            "source_market_count": market_count,
        },
        "stats": summarize_candidates(rows, wait_budgets_s),
        "outputs": {
            "candidates_csv": str((output_dir / "btc5m_high_side_wait_candidates.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_high_side_wait_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_high_side_wait_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_high_side_wait_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_high_side_wait_report.md").write_text(render_report(summary, wait_budgets_s), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "candidates": len(rows), "markets": market_count}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

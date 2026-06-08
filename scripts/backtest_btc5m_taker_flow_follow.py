#!/usr/bin/env python3
"""Event-triggered taker-flow follow backtest for BTC 5m.

This tests the execution path implied by xuan public exact matches: first-leg
triggers are public aggressive BUY prints, not resting maker bids.

It reads replay SQLite read-only and uses settlement only for residual
diagnostics.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def load_base() -> Any:
    path = Path(__file__).with_name("backtest_btc5m_bounded_taker_l2_schedule.py")
    spec = importlib.util.spec_from_file_location("bounded_taker_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


base = load_base()


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


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def load_buy_trades(conn: Any, condition_id: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND taker_side = 'BUY'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    return [
        {
            "trade_ts_ms": int(row["trade_ts_ms"]),
            "market_side": str(row["market_side"]),
            "price": float(row["price"]),
            "size": float(row["size"]),
        }
        for row in rows
    ]


def latest_l1_at_or_before(l1_books: list[Any], times: list[int], ts_ms: int) -> Any | None:
    idx = bisect.bisect_right(times, ts_ms) - 1
    if idx < 0:
        return None
    return l1_books[idx]


def simulate_market(conn: Any, market: Any, args: argparse.Namespace, schedules: list[list[tuple[int, float]]]) -> list[dict[str, Any]]:
    start_ms = int(market["start_ms"])
    end_ms = int(market["end_ms"])
    condition_id = str(market["condition_id"])
    l1_books = base.load_l1_books(conn, condition_id, start_ms, end_ms)
    if not l1_books:
        return []
    l1_times = [book.recv_ms for book in l1_books]
    l2_by_side = base.load_l2_books(conn, condition_id, start_ms, end_ms)
    l2_times_by_side = {side: [book.recv_ms for book in books] for side, books in l2_by_side.items()}
    trades = load_buy_trades(
        conn,
        condition_id,
        max(start_ms, base.TRUSTED_START_MS) + args.min_offset_s * 1000,
        min(end_ms, start_ms + args.max_offset_s * 1000),
    )
    rows: list[dict[str, Any]] = []
    cursor_ms = start_ms + args.min_offset_s * 1000
    for trade in trades:
        ts_ms = int(trade["trade_ts_ms"])
        if ts_ms < cursor_ms:
            continue
        side = str(trade["market_side"])
        if trade["price"] < args.min_trade_price or trade["price"] >= args.max_trade_price:
            continue
        if trade["size"] < args.min_trigger_trade_size:
            continue
        if args.max_trigger_trade_size is not None and trade["size"] >= args.max_trigger_trade_size:
            continue
        l1 = latest_l1_at_or_before(l1_books, l1_times, ts_ms)
        if l1 is None:
            continue
        high_side = base.high_side(l1)
        if args.require_high_side and side != high_side:
            continue
        l1_opp_ask = None
        if l1 is not None:
            l1_opp_ask = base.side_px(l1, base.other(side), "ask")
        if args.first_price_source == "trade":
            first_vwap = float(trade["price"])
            first_filled = args.clip
            first_worst = first_vwap
            age = None
        else:
            first_l2, age = base.latest_l2(l2_by_side[side], l2_times_by_side[side], ts_ms, args.max_l2_age_ms)
            if first_l2 is None or age is None:
                continue
            first_vwap, first_filled, first_worst = base.sweep_vwap(first_l2, args.clip)
            if first_vwap is None:
                continue
        if first_vwap < args.min_first_vwap or first_vwap >= args.max_first_vwap:
            continue
        l1_immediate_pair_cost = first_vwap + float(l1_opp_ask) if l1_opp_ask is not None else None
        if args.max_l1_immediate_pair_cost is not None:
            if l1_immediate_pair_cost is None or l1_immediate_pair_cost > args.max_l1_immediate_pair_cost:
                continue
        opposite = base.other(side)
        opposite_now, opposite_now_age = base.latest_l2(
            l2_by_side[opposite], l2_times_by_side[opposite], ts_ms, args.max_l2_age_ms
        )
        immediate_pair_cost = None
        if opposite_now is not None:
            opposite_now_vwap, _opposite_now_filled, _opposite_now_worst = base.sweep_vwap(opposite_now, args.clip)
            if opposite_now_vwap is not None:
                immediate_pair_cost = first_vwap + opposite_now_vwap
        if args.max_immediate_pair_cost is not None:
            if immediate_pair_cost is None or immediate_pair_cost > args.max_immediate_pair_cost:
                continue
        for schedule in schedules:
            completion = base.first_completion_by_schedule(
                l2_by_side[opposite],
                l2_times_by_side[opposite],
                ts_ms,
                end_ms,
                args.clip,
                first_vwap,
                schedule,
            )
            row: dict[str, Any] = {
                "day": base.iso_ms(ts_ms)[:10],
                "slug": market["slug"],
                "condition_id": condition_id,
                "winner_side": market["winner_side"],
                "schedule": base.schedule_name(schedule),
                "trigger_ts_ms": ts_ms,
                "trigger_iso": base.iso_ms(ts_ms),
                "offset_s": round((ts_ms - start_ms) / 1000.0, 3),
                "first_side": side,
                "high_side": high_side,
                "first_is_winner": side == market["winner_side"],
                "trigger_trade_price": round(float(trade["price"]), 6),
                "trigger_trade_size": round(float(trade["size"]), 6),
                "clip": args.clip,
                "first_l2_age_ms": age,
                "first_price_source": args.first_price_source,
                "first_vwap": round(first_vwap, 6),
                "first_worst_px": None if first_worst is None else round(first_worst, 6),
                "first_filled": round(first_filled, 6),
                "l1_immediate_pair_cost": None
                if l1_immediate_pair_cost is None
                else round(l1_immediate_pair_cost, 6),
                "opposite_now_l2_age_ms": opposite_now_age,
                "immediate_pair_cost": None if immediate_pair_cost is None else round(immediate_pair_cost, 6),
                "completion_fill": False,
                "status": "completion_not_filled",
            }
            if completion is not None:
                pair_cost = float(completion["pair_cost"])
                row.update(
                    {
                        "completion_fill": True,
                        "completion_ts_ms": completion["completion_ts_ms"],
                        "completion_iso": base.iso_ms(int(completion["completion_ts_ms"])),
                        "completion_delay_s": round(float(completion["completion_delay_s"]), 3),
                        "completion_vwap": round(float(completion["completion_vwap"]), 6),
                        "pair_cost": round(pair_cost, 6),
                        "pnl": round((1.0 - pair_cost) * args.clip, 6),
                        "status": "closed",
                    }
                )
            else:
                if side == market["winner_side"]:
                    pnl = (1.0 - first_vwap) * args.clip
                else:
                    pnl = -first_vwap * args.clip
                row["pnl"] = round(pnl, 6)
                row["status"] = "residual_settle"
            rows.append(row)
        cursor_ms = ts_ms + args.cooldown_s * 1000
    return rows


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = rows
    closed = [row for row in fills if row.get("completion_fill") is True]
    residual = [row for row in fills if row.get("completion_fill") is not True]
    cost = sum(float(row["clip"]) * float(row["first_vwap"]) for row in fills)
    pnl = sum(float(row["pnl"]) for row in fills)
    return {
        "rows": len(rows),
        "closed": len(closed),
        "closed_rate": rate(len(closed), len(fills)),
        "first_winner_rate": rate(sum(1 for row in fills if row.get("first_is_winner") is True), len(fills)),
        "residual": len(residual),
        "residual_winner_rate": rate(sum(1 for row in residual if row.get("first_is_winner") is True), len(residual)),
        "pnl": round(pnl, 6),
        "roi_on_first_leg_cost": round(pnl / cost, 6) if cost else None,
        "pair_cost_p50": base.summarize([row.get("pair_cost") for row in closed])["p50"],
        "completion_delay_p50": base.summarize([row.get("completion_delay_s") for row in closed])["p50"],
        "status_counts": {status: sum(1 for row in rows if row.get("status") == status) for status in sorted({row.get("status") for row in rows})},
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_schedule": {}, "by_day": {}}
    for schedule in sorted({row["schedule"] for row in rows}):
        out["by_schedule"][schedule] = compact([row for row in rows if row["schedule"] == schedule])
    for day in sorted({row["day"] for row in rows}):
        out["by_day"][day] = compact([row for row in rows if row["day"] == day])
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Taker Flow Follow Backtest",
        "",
        "## Scope",
        "",
        "- First-leg candidates are public `md_trades.taker_side=BUY` events.",
        "- First and completion legs use L2 ask sweep VWAP.",
        "- Settlement is used only to value unclosed residuals.",
        "",
        "## By Schedule",
        "",
        "| schedule | rows | closed | first winner | residual | residual winner | pair p50 | delay p50 | pnl | ROI |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for schedule, item in report["aggregate"]["by_schedule"].items():
        lines.append(
            f"| {schedule} | {item['rows']} | {item['closed_rate']} | {item['first_winner_rate']} | "
            f"{item['residual']} | {item['residual_winner_rate']} | {item['pair_cost_p50']} | "
            f"{item['completion_delay_p50']} | {item['pnl']} | {item['roi_on_first_leg_cost']} |"
        )
    lines.extend(["", "## By Day", "", "| day | rows | closed | first winner | pnl | ROI |", "|---|---:|---:|---:|---:|---:|"])
    for day, item in report["aggregate"]["by_day"].items():
        lines.append(
            f"| {day} | {item['rows']} | {item['closed_rate']} | {item['first_winner_rate']} | "
            f"{item['pnl']} | {item['roi_on_first_leg_cost']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/btc5m_taker_flow_follow")
    parser.add_argument("--schedules", default="30:0.95,90:1.03;30:0.95,120:1.04;30:0.95")
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--first-price-source", choices=("l2", "trade"), default="l2")
    parser.add_argument("--min-trigger-trade-size", type=float, default=0.0)
    parser.add_argument("--max-trigger-trade-size", type=float, default=None)
    parser.add_argument("--min-trade-price", type=float, default=0.50)
    parser.add_argument("--max-trade-price", type=float, default=0.90)
    parser.add_argument("--min-first-vwap", type=float, default=0.50)
    parser.add_argument("--max-first-vwap", type=float, default=0.90)
    parser.add_argument("--max-l1-immediate-pair-cost", type=float, default=None)
    parser.add_argument("--max-immediate-pair-cost", type=float, default=None)
    parser.add_argument("--require-high-side", action="store_true")
    parser.add_argument("--max-markets", type=int, default=0)
    args = parser.parse_args()

    schedules = [base.parse_schedule(item.strip()) for item in args.schedules.split(";") if item.strip()]
    rows: list[dict[str, Any]] = []
    days = [day.strip() for day in args.days.split(",") if day.strip()]
    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        with base.connect_ro(db_path) as conn:
            markets = base.load_markets(conn, base.day_max_ms(conn))
            if args.max_markets:
                markets = markets[: args.max_markets]
            for market in markets:
                rows.extend(simulate_market(conn, market, args, schedules))

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_taker_flow_follow_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": vars(args),
        "aggregate": aggregate(rows),
    }
    (output_dir / "btc5m_taker_flow_follow_summary.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))
    (output_dir / "btc5m_taker_flow_follow_report.md").write_text(render_report(report))
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

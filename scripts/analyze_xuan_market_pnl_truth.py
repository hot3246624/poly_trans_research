#!/usr/bin/env python3
"""Compute xuanxuan008 market-level trade PnL from replay public truth.

This is the preferred PnL truth for strategy research:

- source trades from xuan_trades, not activity cash-flow;
- value positions by settlement winner when available;
- fallback to latest L1 mid mark for unsettled markets;
- decompose PnL into paired Up+Down profit and residual winner/loser PnL;
- read replay SQLite in read-only mode only.

It intentionally does not use MERGE/REDEEM cash flows because public activity
polls can contain lifecycle events without the corresponding full buy history.
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
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


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
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
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


def trade_key(row: sqlite3.Row) -> tuple[Any, ...]:
    if row["tx_hash"]:
        return ("tx", row["tx_hash"])
    if row["trade_id"]:
        return ("trade_id", row["trade_id"])
    return (
        "fields",
        row["condition_id"],
        row["trade_ts_ms"],
        row["side"],
        row["outcome_side"],
        round(float(row["price"] or 0.0), 10),
        round(float(row["size"] or 0.0), 8),
    )


def latest_mid_mark(conn: sqlite3.Connection, condition_id: str) -> dict[str, float | None]:
    row = conn.execute(
        """
        SELECT yes_bid_px, yes_ask_px, no_bid_px, no_ask_px
        FROM md_book_l1
        WHERE condition_id=?
        ORDER BY recv_ms DESC, id DESC
        LIMIT 1
        """,
        (condition_id,),
    ).fetchone()
    if row is None:
        return {"YES": None, "NO": None}
    out: dict[str, float | None] = {}
    for side, bid_col, ask_col in (("YES", "yes_bid_px", "yes_ask_px"), ("NO", "no_bid_px", "no_ask_px")):
        bid = row[bid_col]
        ask = row[ask_col]
        if bid is not None and ask is not None:
            out[side] = (float(bid) + float(ask)) / 2.0
        elif bid is not None:
            out[side] = float(bid)
        elif ask is not None:
            out[side] = float(ask)
        else:
            out[side] = None
    return out


def load_replay(
    replay_root: Path,
    days: list[str],
    start_ms: int,
    end_ms: int | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    trades: list[dict[str, Any]] = []
    markets: dict[str, dict[str, Any]] = {}
    seen: set[tuple[Any, ...]] = set()
    day_summaries: list[dict[str, Any]] = []

    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_summaries.append({"day": day, "exists": False, "raw_trades": 0, "new_trades": 0})
            continue
        conn = connect_ro(db_path)
        try:
            market_rows = conn.execute(
                """
                SELECT m.condition_id, m.slug, m.symbol, m.interval_sec, m.start_ms, m.end_ms,
                       s.winner_side, s.settle_ms, s.resolution_source
                FROM market_meta m
                LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
                WHERE m.symbol='BTC' AND m.interval_sec=300
                """
            ).fetchall()
            for row in market_rows:
                condition_id = str(row["condition_id"])
                mark = latest_mid_mark(conn, condition_id)
                markets[condition_id] = {
                    "condition_id": condition_id,
                    "slug": row["slug"],
                    "start_ms": int(row["start_ms"]),
                    "end_ms": int(row["end_ms"]),
                    "winner_side": row["winner_side"],
                    "settle_ms": row["settle_ms"],
                    "resolution_source": row["resolution_source"],
                    "latest_yes_mark": mark["YES"],
                    "latest_no_mark": mark["NO"],
                }
            rows = conn.execute(
                """
                SELECT id, trade_ts_ms, condition_id, slug, side, outcome_side,
                       price, size, tx_hash, trade_id
                FROM xuan_trades
                WHERE condition_id IS NOT NULL
                  AND trade_ts_ms IS NOT NULL
                  AND trade_ts_ms >= ?
                  AND outcome_side IN ('YES', 'NO')
                ORDER BY trade_ts_ms, id
                """,
                (start_ms,),
            ).fetchall()
        finally:
            conn.close()

        new_count = 0
        for row in rows:
            ts_ms = int(row["trade_ts_ms"])
            if end_ms is not None and ts_ms >= end_ms:
                continue
            if PLANNED_OUTAGE_START_MS <= ts_ms < PLANNED_OUTAGE_END_MS:
                continue
            condition_id = str(row["condition_id"])
            if condition_id not in markets:
                continue
            key = trade_key(row)
            if key in seen:
                continue
            seen.add(key)
            new_count += 1
            side = str(row["side"] or "BUY")
            size = float(row["size"] or 0.0)
            price = float(row["price"] or 0.0)
            signed_qty = size if side == "BUY" else -size
            signed_cost = price * size if side == "BUY" else -price * size
            trades.append(
                {
                    "trade_ts_ms": ts_ms,
                    "trade_iso": iso_ms(ts_ms),
                    "condition_id": condition_id,
                    "slug": row["slug"],
                    "side": side,
                    "outcome_side": row["outcome_side"],
                    "price": price,
                    "size": size,
                    "signed_qty": signed_qty,
                    "signed_cost": signed_cost,
                    "tx_hash": row["tx_hash"],
                    "trade_id": row["trade_id"],
                    "event_day_utc": dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).date().isoformat(),
                }
            )
        day_summaries.append({"day": day, "exists": True, "raw_trades": len(rows), "new_trades": new_count})

    trades.sort(key=lambda row: (row["trade_ts_ms"], row["condition_id"], row["outcome_side"]))
    return trades, markets, day_summaries


def value_side(qty: float, side: str, meta: dict[str, Any]) -> tuple[float, str]:
    winner = meta.get("winner_side")
    if winner in {"YES", "NO"}:
        return (qty if side == winner else 0.0), "settlement"
    mark = meta.get("latest_yes_mark" if side == "YES" else "latest_no_mark")
    if mark is None:
        return 0.0, "missing_mark"
    return qty * float(mark), "latest_l1_mid"


def build_market_rows(trades: list[dict[str, Any]], markets: dict[str, dict[str, Any]], tz_offset_hours: int) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        grouped[trade["condition_id"]].append(trade)

    out = []
    for condition_id, xs in grouped.items():
        meta = markets[condition_id]
        qty = {"YES": 0.0, "NO": 0.0}
        cost = {"YES": 0.0, "NO": 0.0}
        for trade in xs:
            side = trade["outcome_side"]
            qty[side] += float(trade["signed_qty"])
            cost[side] += float(trade["signed_cost"])
        yes_qty = max(qty["YES"], 0.0)
        no_qty = max(qty["NO"], 0.0)
        yes_cost = cost["YES"]
        no_cost = cost["NO"]
        total_cost = yes_cost + no_cost
        yes_avg = yes_cost / yes_qty if yes_qty > 1e-9 else None
        no_avg = no_cost / no_qty if no_qty > 1e-9 else None
        paired_qty = min(yes_qty, no_qty)
        weighted_pair_cost = (yes_avg + no_avg) if yes_avg is not None and no_avg is not None and paired_qty > 0 else None
        paired_profit = paired_qty * (1.0 - weighted_pair_cost) if weighted_pair_cost is not None else 0.0
        winner = meta.get("winner_side")
        yes_value, yes_value_source = value_side(yes_qty, "YES", meta)
        no_value, no_value_source = value_side(no_qty, "NO", meta)
        total_value = yes_value + no_value
        residual_side = None
        residual_qty = abs(yes_qty - no_qty)
        residual_pnl = 0.0
        if residual_qty > 1e-9:
            residual_side = "YES" if yes_qty > no_qty else "NO"
            residual_avg = yes_avg if residual_side == "YES" else no_avg
            residual_value_per_share = 0.0
            if winner in {"YES", "NO"}:
                residual_value_per_share = 1.0 if residual_side == winner else 0.0
            else:
                mark = meta.get("latest_yes_mark" if residual_side == "YES" else "latest_no_mark")
                residual_value_per_share = float(mark or 0.0)
            residual_pnl = residual_qty * (residual_value_per_share - float(residual_avg or 0.0))
        first_ts = min(int(row["trade_ts_ms"]) for row in xs)
        last_ts = max(int(row["trade_ts_ms"]) for row in xs)
        bjt_day = (
            dt.datetime.fromtimestamp(int(meta["start_ms"]) / 1000, tz=dt.timezone.utc)
            + dt.timedelta(hours=tz_offset_hours)
        ).date().isoformat()
        total_pnl = total_value - total_cost
        out.append(
            {
                "condition_id": condition_id,
                "slug": meta["slug"],
                "round_start_ms": meta["start_ms"],
                "round_start_iso": iso_ms(meta["start_ms"]),
                "bucket_day": bjt_day,
                "winner_side": winner,
                "value_source": yes_value_source if yes_value_source == no_value_source else f"{yes_value_source}/{no_value_source}",
                "trade_count": len(xs),
                "buy_count": sum(1 for row in xs if row["side"] == "BUY"),
                "sell_count": sum(1 for row in xs if row["side"] == "SELL"),
                "first_trade_ms": first_ts,
                "first_trade_iso": iso_ms(first_ts),
                "last_trade_ms": last_ts,
                "last_trade_iso": iso_ms(last_ts),
                "first_trade_offset_s": round((first_ts - int(meta["start_ms"])) / 1000.0, 3),
                "yes_qty": round(yes_qty, 6),
                "no_qty": round(no_qty, 6),
                "yes_cost": round(yes_cost, 6),
                "no_cost": round(no_cost, 6),
                "yes_avg": round(yes_avg, 6) if yes_avg is not None else None,
                "no_avg": round(no_avg, 6) if no_avg is not None else None,
                "total_cost": round(total_cost, 6),
                "total_value": round(total_value, 6),
                "trade_pnl": round(total_pnl, 6),
                "roi_on_cost": round(total_pnl / total_cost, 6) if total_cost > 0 else None,
                "paired_qty": round(paired_qty, 6),
                "weighted_pair_cost": round(weighted_pair_cost, 6) if weighted_pair_cost is not None else None,
                "paired_profit": round(paired_profit, 6),
                "residual_side": residual_side,
                "residual_qty": round(residual_qty, 6),
                "residual_is_winner": residual_side == winner if residual_side and winner in {"YES", "NO"} else None,
                "residual_pnl": round(residual_pnl, 6),
            }
        )
    return sorted(out, key=lambda row: (row["round_start_ms"], row["condition_id"]))


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    total_cost = sum(float(row["total_cost"] or 0.0) for row in rows)
    total_value = sum(float(row["total_value"] or 0.0) for row in rows)
    paired_qty = sum(float(row["paired_qty"] or 0.0) for row in rows)
    paired_cost_notional = sum(
        float(row["paired_qty"] or 0.0) * float(row["weighted_pair_cost"] or 0.0)
        for row in rows
        if row["weighted_pair_cost"] is not None
    )
    paired_profit = sum(float(row["paired_profit"] or 0.0) for row in rows)
    pnl = total_value - total_cost
    paired = [row for row in rows if float(row["paired_qty"] or 0.0) > 0]
    profitable_pair = [row for row in paired if float(row["paired_profit"] or 0.0) > 0]
    losing_pair = [row for row in paired if float(row["paired_profit"] or 0.0) < 0]
    residual = [row for row in rows if float(row["residual_qty"] or 0.0) > 1e-9]
    residual_winner = [row for row in residual if row["residual_is_winner"] is True]
    return {
        "market_count": len(rows),
        "paired_market_count": len(paired),
        "profitable_paired_market_count": len(profitable_pair),
        "losing_paired_market_count": len(losing_pair),
        "profitable_pair_market_rate": rate(len(profitable_pair), len(paired)),
        "trade_count": sum(int(row["trade_count"]) for row in rows),
        "buy_count": sum(int(row["buy_count"]) for row in rows),
        "sell_count": sum(int(row["sell_count"]) for row in rows),
        "total_cost": round(total_cost, 6),
        "total_value": round(total_value, 6),
        "trade_pnl": round(pnl, 6),
        "roi_on_cost": round(pnl / total_cost, 6) if total_cost > 0 else None,
        "paired_qty": round(paired_qty, 6),
        "weighted_pair_cost": round(paired_cost_notional / paired_qty, 6) if paired_qty > 0 else None,
        "paired_profit": round(paired_profit, 6),
        "residual_pnl": round(sum(float(row["residual_pnl"] or 0.0) for row in rows), 6),
        "residual_market_count": len(residual),
        "residual_is_winner_rate": rate(len(residual_winner), len(residual)),
        "per_market_pnl": summarize([float(row["trade_pnl"] or 0.0) for row in rows]),
        "per_market_pair_cost": summarize([row["weighted_pair_cost"] for row in paired]),
    }


def bucket_tables(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {
        "day": defaultdict(list),
        "value_source": defaultdict(list),
        "first_trade_offset": defaultdict(list),
        "weighted_pair_cost": defaultdict(list),
    }
    for row in rows:
        grouped["day"][str(row["bucket_day"])].append(row)
        grouped["value_source"][str(row["value_source"])].append(row)
        off = float(row["first_trade_offset_s"])
        if off < 30:
            off_key = "000-030s"
        elif off < 120:
            off_key = "030-120s"
        elif off < 240:
            off_key = "120-240s"
        else:
            off_key = "240s+"
        grouped["first_trade_offset"][off_key].append(row)
        pc = row["weighted_pair_cost"]
        if pc is None:
            pc_key = "unpaired"
        else:
            x = float(pc)
            if x <= 0.95:
                pc_key = "<=0.95"
            elif x <= 0.98:
                pc_key = "0.95-0.98"
            elif x <= 1.00:
                pc_key = "0.98-1.00"
            elif x <= 1.02:
                pc_key = "1.00-1.02"
            else:
                pc_key = ">1.02"
        grouped["weighted_pair_cost"][pc_key].append(row)
    return {name: {key: compact(xs) for key, xs in sorted(items.items())} for name, items in grouped.items()}


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
    s = report["summary"]
    lines = [
        "# Xuan Market-Level Trade PnL Truth",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        f"- trusted_start: `{report['trusted_start_iso']}`",
        "- Source: `xuan_trades` + `settlement_records`; fallback mark is latest L1 mid for unsettled markets.",
        "- This excludes deposits/withdrawals and ignores MERGE/REDEEM cash-flow lifecycle noise.",
        "",
        "## Topline",
        "",
        f"- markets: `{s['market_count']}`",
        f"- paired markets: `{s['paired_market_count']}`",
        f"- profitable / losing paired markets: `{s['profitable_paired_market_count']}` / `{s['losing_paired_market_count']}`",
        f"- trades BUY/SELL: `{s['buy_count']}` / `{s['sell_count']}`",
        f"- total cost: `${s['total_cost']}`",
        f"- total value: `${s['total_value']}`",
        f"- trade PnL: `${s['trade_pnl']}`",
        f"- ROI on cost: `{s['roi_on_cost']}`",
        f"- weighted pair cost: `{s['weighted_pair_cost']}`",
        f"- paired profit: `${s['paired_profit']}`",
        f"- residual PnL: `${s['residual_pnl']}`",
        "",
        "## By Day",
        "",
        "| day | markets | trades | cost | value | pnl | roi | pair cost | paired profit | residual pnl |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["bucket_tables"]["day"].items():
        lines.append(
            f"| {key} | {item['market_count']} | {item['trade_count']} | {item['total_cost']} | "
            f"{item['total_value']} | {item['trade_pnl']} | {item['roi_on_cost']} | "
            f"{item['weighted_pair_cost']} | {item['paired_profit']} | {item['residual_pnl']} |"
        )
    lines.extend(
        [
            "",
            "## Pair-Cost Buckets",
            "",
            "| bucket | markets | cost | pnl | pair cost | paired profit | residual pnl |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in report["bucket_tables"]["weighted_pair_cost"].items():
        lines.append(
            f"| {key} | {item['market_count']} | {item['total_cost']} | {item['trade_pnl']} | "
            f"{item['weighted_pair_cost']} | {item['paired_profit']} | {item['residual_pnl']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--tz-offset-hours", type=int, default=8)
    parser.add_argument("--output-dir", default="data/exports/xuan_market_pnl_truth")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    start_ms = parse_iso_ms(args.start_iso) or TRUSTED_START_MS
    end_ms = parse_iso_ms(args.end_iso)
    trades, markets, day_summaries = load_replay(Path(args.replay_root), days, start_ms, end_ms)
    market_rows = build_market_rows(trades, markets, args.tz_offset_hours)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_market_pnl_trades.csv", trades)
    write_csv(output_dir / "xuan_market_pnl_markets.csv", market_rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "trusted_start_ms": start_ms,
        "trusted_start_iso": iso_ms(start_ms),
        "end_ms": end_ms,
        "end_iso": iso_ms(end_ms),
        "tz_offset_hours": args.tz_offset_hours,
        "day_summaries": day_summaries,
        "summary": compact(market_rows),
        "bucket_tables": bucket_tables(market_rows),
        "outputs": {
            "trades_csv": str((output_dir / "xuan_market_pnl_trades.csv").resolve()),
            "markets_csv": str((output_dir / "xuan_market_pnl_markets.csv").resolve()),
            "summary_json": str((output_dir / "xuan_market_pnl_truth_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_market_pnl_truth_report.md").resolve()),
        },
    }
    (output_dir / "xuan_market_pnl_truth_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_market_pnl_truth_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "trades": len(trades), "markets": len(market_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

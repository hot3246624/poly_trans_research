#!/usr/bin/env python3
"""Analyze xuan winner-side behavior from replay public truth.

This script uses only replay SQLite in read-only mode. It relies on normalized
direction fields:

- xuan_trades.outcome_side
- settlement_records.winner_side

It does not remap raw Up/Down labels and does not use own execution truth.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import Counter, defaultdict
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


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(values)
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


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def size_match(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, max(a, b) * rel_tol)


def dedupe_key(row: sqlite3.Row) -> tuple[Any, ...]:
    if row["tx_hash"]:
        return ("tx", row["tx_hash"])
    if row["trade_id"]:
        return ("trade_id", row["trade_id"])
    return (
        "fields",
        row["condition_id"],
        row["trade_ts_ms"],
        row["outcome_side"],
        row["side"],
        round(float(row["price"] or 0.0), 8),
        round(float(row["size"] or 0.0), 8),
    )


def load_day(conn: sqlite3.Connection, start_ms: int, end_ms: int | None) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, dict[str, Any]]]:
    winner_rows = conn.execute(
        """
        SELECT condition_id, winner_side, resolution_source, settle_ms
        FROM settlement_records
        WHERE winner_side IN ('YES', 'NO')
        """
    ).fetchall()
    winners = {str(row["condition_id"]): str(row["winner_side"]) for row in winner_rows}
    settlement_meta = {
        str(row["condition_id"]): {
            "winner_side": row["winner_side"],
            "resolution_source": row["resolution_source"],
            "settle_ms": row["settle_ms"],
        }
        for row in winner_rows
    }
    rows = conn.execute(
        """
        SELECT t.trade_ts_ms, t.condition_id, t.slug, t.outcome_side, t.side,
               t.price, t.size, t.tx_hash, t.trade_id,
               m.start_ms, m.end_ms
        FROM xuan_trades t
        JOIN market_meta m ON m.condition_id = t.condition_id
        WHERE m.symbol='BTC'
          AND m.interval_sec=300
          AND t.side='BUY'
          AND t.trade_ts_ms IS NOT NULL
          AND t.trade_ts_ms >= ?
          AND t.outcome_side IN ('YES', 'NO')
        ORDER BY t.trade_ts_ms, t.id
        """,
        (start_ms,),
    ).fetchall()
    trades = []
    for row in rows:
        ts_ms = int(row["trade_ts_ms"])
        if end_ms is not None and ts_ms >= end_ms:
            continue
        if PLANNED_OUTAGE_START_MS <= ts_ms < PLANNED_OUTAGE_END_MS:
            continue
        winner = winners.get(str(row["condition_id"]))
        if winner not in {"YES", "NO"}:
            continue
        market_side = str(row["outcome_side"])
        price = float(row["price"])
        size = float(row["size"])
        start = int(row["start_ms"])
        trades.append(
            {
                "trade_ts_ms": ts_ms,
                "trade_iso": iso_ms(ts_ms),
                "condition_id": row["condition_id"],
                "slug": row["slug"],
                "round_start_ms": start,
                "round_end_ms": int(row["end_ms"]),
                "round_offset_s": round((ts_ms - start) / 1000.0, 3),
                "market_side": market_side,
                "winner_side": winner,
                "is_winner_side": market_side == winner,
                "price": price,
                "size": size,
                "usdc": price * size,
                "tx_hash": row["tx_hash"],
                "trade_id": row["trade_id"],
            }
        )
    return trades, winners, settlement_meta


def load_trades(replay_root: Path, days: list[str], start_ms: int, end_ms: int | None) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    seen = set()
    trades = []
    winners: dict[str, str] = {}
    settlement_meta: dict[str, dict[str, Any]] = {}
    day_summaries = []
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_summaries.append({"day": day, "exists": False, "raw_trades": 0, "new_trades": 0})
            continue
        conn = connect_ro(db_path)
        try:
            day_trades, day_winners, day_settlement = load_day(conn, start_ms, end_ms)
        finally:
            conn.close()
        winners.update(day_winners)
        settlement_meta.update(day_settlement)
        new_count = 0
        for row in day_trades:
            key = (
                row.get("tx_hash") and ("tx", row.get("tx_hash"))
                or row.get("trade_id") and ("trade_id", row.get("trade_id"))
                or (
                    "fields",
                    row["condition_id"],
                    row["trade_ts_ms"],
                    row["market_side"],
                    round(float(row["price"]), 8),
                    round(float(row["size"]), 8),
                )
            )
            if key in seen:
                continue
            seen.add(key)
            trades.append(row)
            new_count += 1
        day_summaries.append({"day": day, "exists": True, "raw_trades": len(day_trades), "new_trades": new_count})
    trades.sort(key=lambda row: (row["trade_ts_ms"], row["condition_id"], row["market_side"], row["price"], row["size"]))
    return trades, winners, settlement_meta, day_summaries


def build_tranches(trades: list[dict[str, Any]], abs_tol: float, rel_tol: float) -> list[dict[str, Any]]:
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_condition[trade["condition_id"]].append(trade)
    out = []
    for condition_id, xs in by_condition.items():
        xs = sorted(xs, key=lambda row: (row["trade_ts_ms"], row.get("tx_hash") or ""))
        used = [False] * len(xs)
        tranche_id = 0
        for i, first in enumerate(xs):
            if used[i]:
                continue
            for j in range(i + 1, len(xs)):
                second = xs[j]
                if used[j]:
                    continue
                if first["market_side"] == second["market_side"]:
                    continue
                if not size_match(float(first["size"]), float(second["size"]), abs_tol, rel_tol):
                    continue
                used[i] = True
                used[j] = True
                tranche_id += 1
                pair_cost = float(first["price"]) + float(second["price"])
                delay_s = (int(second["trade_ts_ms"]) - int(first["trade_ts_ms"])) / 1000.0
                if delay_s <= 30:
                    path_label = "fast_control"
                elif pair_cost < 0.95:
                    path_label = "slow_profit_lt95"
                else:
                    path_label = "slow_bad_ge95"
                out.append(
                    {
                        "slug": first["slug"],
                        "condition_id": condition_id,
                        "tranche_id": tranche_id,
                        "winner_side": first["winner_side"],
                        "first_ts_ms": first["trade_ts_ms"],
                        "first_iso": first["trade_iso"],
                        "second_ts_ms": second["trade_ts_ms"],
                        "second_iso": second["trade_iso"],
                        "first_offset_s": first["round_offset_s"],
                        "second_offset_s": second["round_offset_s"],
                        "pair_delay_s": round(delay_s, 3),
                        "size": round(float(first["size"]), 6),
                        "size_diff": round(abs(float(first["size"]) - float(second["size"])), 6),
                        "first_side": first["market_side"],
                        "second_side": second["market_side"],
                        "first_is_winner": first["market_side"] == first["winner_side"],
                        "second_is_winner": second["market_side"] == first["winner_side"],
                        "first_price": round(float(first["price"]), 6),
                        "second_price": round(float(second["price"]), 6),
                        "pair_cost": round(pair_cost, 6),
                        "pair_surplus": round(1.0 - pair_cost, 6),
                        "surplus_usdc": round((1.0 - pair_cost) * float(first["size"]), 6),
                        "path_label": path_label,
                        "first_tx": first.get("tx_hash"),
                        "second_tx": second.get("tx_hash"),
                    }
                )
                break
    return sorted(out, key=lambda row: (row["first_ts_ms"], row["condition_id"], row["tranche_id"]))


def build_market_inventory(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        by_condition[trade["condition_id"]].append(trade)
    rows = []
    for condition_id, xs in by_condition.items():
        qty = defaultdict(float)
        cost = defaultdict(float)
        winner = xs[0]["winner_side"]
        slug = xs[0]["slug"]
        start_ms = xs[0]["round_start_ms"]
        for trade in xs:
            side = trade["market_side"]
            qty[side] += float(trade["size"])
            cost[side] += float(trade["usdc"])
        yes_qty = qty["YES"]
        no_qty = qty["NO"]
        winner_qty = qty[winner]
        loser_qty = qty[opposite(winner)]
        residual_side = "FLAT"
        residual_qty = abs(yes_qty - no_qty)
        if yes_qty > no_qty + 0.02:
            residual_side = "YES"
        elif no_qty > yes_qty + 0.02:
            residual_side = "NO"
        rows.append(
            {
                "slug": slug,
                "condition_id": condition_id,
                "round_start_ms": start_ms,
                "round_start_iso": iso_ms(start_ms),
                "winner_side": winner,
                "trade_count": len(xs),
                "yes_qty": round(yes_qty, 6),
                "no_qty": round(no_qty, 6),
                "winner_qty": round(winner_qty, 6),
                "loser_qty": round(loser_qty, 6),
                "winner_qty_minus_loser_qty": round(winner_qty - loser_qty, 6),
                "winner_overweight": winner_qty > loser_qty + 0.02,
                "residual_side": residual_side,
                "residual_qty": round(residual_qty, 6),
                "residual_is_winner": residual_side == winner,
                "gross_qty": round(yes_qty + no_qty, 6),
                "gross_cost": round(cost["YES"] + cost["NO"], 6),
                "winner_cost": round(cost[winner], 6),
                "loser_cost": round(cost[opposite(winner)], 6),
            }
        )
    return sorted(rows, key=lambda row: row["round_start_ms"])


def compact_trades(rows: list[dict[str, Any]]) -> dict[str, Any]:
    winner = [row for row in rows if row["is_winner_side"]]
    total_size = sum(float(row["size"]) for row in rows)
    winner_size = sum(float(row["size"]) for row in winner)
    total_usdc = sum(float(row["usdc"]) for row in rows)
    winner_usdc = sum(float(row["usdc"]) for row in winner)
    return {
        "trade_count": len(rows),
        "winner_trade_rate": rate(len(winner), len(rows)),
        "winner_size_rate": rate(int(round(winner_size * 1_000_000)), int(round(total_size * 1_000_000))),
        "winner_usdc_rate": rate(int(round(winner_usdc * 1_000_000)), int(round(total_usdc * 1_000_000))),
        "price": summarize([row["price"] for row in rows]),
        "size": summarize([row["size"] for row in rows]),
    }


def compact_tranches(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first_winner = [row for row in rows if row["first_is_winner"]]
    surplus = sum(float(row["surplus_usdc"]) for row in rows)
    return {
        "tranche_count": len(rows),
        "first_winner_rate": rate(len(first_winner), len(rows)),
        "pair_cost": summarize([row["pair_cost"] for row in rows]),
        "pair_delay_s": summarize([row["pair_delay_s"] for row in rows]),
        "size": summarize([row["size"] for row in rows]),
        "surplus_usdc": round(surplus, 6),
        "surplus_per_tranche": round(surplus / len(rows), 6) if rows else None,
        "path_counts": dict(sorted(Counter(row["path_label"] for row in rows).items())),
    }


def compact_markets(rows: list[dict[str, Any]]) -> dict[str, Any]:
    residual = [row for row in rows if row["residual_side"] != "FLAT"]
    winner_over = [row for row in rows if row["winner_overweight"]]
    residual_winner = [row for row in residual if row["residual_is_winner"]]
    return {
        "market_count": len(rows),
        "winner_overweight_market_rate": rate(len(winner_over), len(rows)),
        "residual_market_count": len(residual),
        "residual_market_rate": rate(len(residual), len(rows)),
        "residual_is_winner_rate": rate(len(residual_winner), len(residual)),
        "winner_qty_minus_loser_qty": summarize([row["winner_qty_minus_loser_qty"] for row in rows]),
        "residual_qty": summarize([row["residual_qty"] for row in residual]),
    }


def bucket_tables(tranches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    field_extractors = {
        "path_label": lambda row: str(row["path_label"]),
        "first_is_winner": lambda row: str(row["first_is_winner"]),
        "day": lambda row: dt.datetime.fromtimestamp(int(row["first_ts_ms"]) / 1000, tz=dt.timezone.utc).date().isoformat(),
        "first_price_bucket": lambda row: (
            "<0.40"
            if float(row["first_price"]) < 0.40
            else "0.40-0.50"
            if float(row["first_price"]) < 0.50
            else "0.50-0.55"
            if float(row["first_price"]) < 0.55
            else "0.55-0.70"
            if float(row["first_price"]) < 0.70
            else ">=0.70"
        ),
        "offset_bucket": lambda row: (
            "000-030s"
            if float(row["first_offset_s"]) < 30
            else "030-120s"
            if float(row["first_offset_s"]) < 120
            else "120-240s"
            if float(row["first_offset_s"]) < 240
            else "240-300s"
        ),
    }
    for field, extractor in field_extractors.items():
        groups = defaultdict(list)
        for row in tranches:
            groups[extractor(row)].append(row)
        out[field] = {key: compact_tranches(xs) for key, xs in sorted(groups.items())}
    return out


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


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan Winner Path Analysis",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        "- SQLite opened read-only. No raw data. Direction uses normalized `outcome_side` and `winner_side`.",
        "",
        "## Summary",
        "",
        f"- trades: `{report['trade_summary']['trade_count']}`",
        f"- trade winner rate count/size/usdc: `{report['trade_summary']['winner_trade_rate']}` / `{report['trade_summary']['winner_size_rate']}` / `{report['trade_summary']['winner_usdc_rate']}`",
        f"- tranches: `{report['tranche_summary']['tranche_count']}`",
        f"- first-leg winner rate: `{report['tranche_summary']['first_winner_rate']}`",
        f"- markets: `{report['market_summary']['market_count']}`",
        f"- winner-overweight market rate: `{report['market_summary']['winner_overweight_market_rate']}`",
        f"- residual-is-winner rate: `{report['market_summary']['residual_is_winner_rate']}`",
        "",
        "## By Path",
        "",
        "| path | n | first_winner | pair p50 | delay p50 | surplus |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["bucket_tables"]["path_label"].items():
        lines.append(
            f"| {key} | {item['tranche_count']} | {item['first_winner_rate']} | "
            f"{item['pair_cost']['p50']} | {item['pair_delay_s']['p50']} | {item['surplus_usdc']} |"
        )
    lines.extend(
        [
            "",
            "## First Leg Winner",
            "",
            "| first_is_winner | n | pair p50 | delay p50 | surplus | path_counts |",
            "|---|---:|---:|---:|---:|---|",
        ]
    )
    for key, item in report["bucket_tables"]["first_is_winner"].items():
        lines.append(
            f"| {key} | {item['tranche_count']} | {item['pair_cost']['p50']} | "
            f"{item['pair_delay_s']['p50']} | {item['surplus_usdc']} | `{item['path_counts']}` |"
        )
    for table_name in ["day", "first_price_bucket", "offset_bucket"]:
        lines.extend(
            [
                "",
                f"## By {table_name}",
                "",
                "| bucket | n | first_winner | pair p50 | slow_profit | slow_bad | surplus |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for key, item in report["bucket_tables"][table_name].items():
            path_counts = item["path_counts"]
            n = item["tranche_count"]
            lines.append(
                f"| {key} | {n} | {item['first_winner_rate']} | {item['pair_cost']['p50']} | "
                f"{rate(path_counts.get('slow_profit_lt95', 0), n)} | {rate(path_counts.get('slow_bad_ge95', 0), n)} | "
                f"{item['surplus_usdc']} |"
            )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Trade-level winner rate measures directional selection; market-level winner-overweight measures residual/inventory bias.",
            "- First-leg winner rate answers whether xuan tends to initiate cycles on the eventual winning side.",
            "- Residual-is-winner rate is important for settlement/redeem: winner residuals can be redeemed, loser residuals decay.",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--output-dir", default="data/exports/xuan_winner_path")
    parser.add_argument("--size-match-abs-tol", type=float, default=0.02)
    parser.add_argument("--size-match-rel-tol", type=float, default=0.0005)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    start_ms = parse_iso_ms(args.start_iso) or TRUSTED_START_MS
    end_ms = parse_iso_ms(args.end_iso)
    trades, winners, settlement_meta, day_summaries = load_trades(Path(args.replay_root), days, start_ms, end_ms)
    tranches = build_tranches(trades, args.size_match_abs_tol, args.size_match_rel_tol)
    markets = build_market_inventory(trades)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_winner_path_trades.csv", trades)
    write_csv(output_dir / "xuan_winner_path_tranches.csv", tranches)
    write_csv(output_dir / "xuan_winner_path_markets.csv", markets)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "trusted_start_ms": start_ms,
        "trusted_start_iso": iso_ms(start_ms),
        "end_ms": end_ms,
        "end_iso": iso_ms(end_ms),
        "day_summaries": day_summaries,
        "settled_condition_count": len(winners),
        "trade_summary": compact_trades(trades),
        "tranche_summary": compact_tranches(tranches),
        "market_summary": compact_markets(markets),
        "bucket_tables": bucket_tables(tranches),
        "outputs": {
            "trades_csv": str((output_dir / "xuan_winner_path_trades.csv").resolve()),
            "tranches_csv": str((output_dir / "xuan_winner_path_tranches.csv").resolve()),
            "markets_csv": str((output_dir / "xuan_winner_path_markets.csv").resolve()),
            "summary_json": str((output_dir / "xuan_winner_path_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_winner_path_report.md").resolve()),
        },
    }
    (output_dir / "xuan_winner_path_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_winner_path_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "trades": len(trades), "tranches": len(tranches)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Compare taker BUY signal candidates with xuan public trades.

This is a research-only audit. It reads replay SQLite in read-only mode and
does not read raw capture files.
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


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
    if not xs:
        return None
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    w = pos - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(float(row["pnl"]) for row in rows)
    cost = sum(float(row["trigger_price"]) * float(row["clip"]) for row in rows)
    return {
        "rows": len(rows),
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / cost, 6) if cost else None,
        "first_winner_rate": rate(sum(row.get("first_is_winner") == "True" for row in rows), len(rows)),
        "closed_rate": rate(sum(row.get("completion_fill") == "True" for row in rows), len(rows)),
    }


def load_xuan_trades(replay_root: Path, rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_day[str(row["day"])].append(row)

    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for day, day_rows in by_day.items():
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conds = sorted({row["condition_id"] for row in day_rows})
        if not conds:
            continue
        placeholders = ",".join("?" for _ in conds)
        with ro_connect(db_path) as conn:
            for trade in conn.execute(
                f"""
                SELECT condition_id, trade_ts_ms, outcome_side, side, price, size
                FROM xuan_trades
                WHERE side = 'BUY'
                  AND condition_id IN ({placeholders})
                  AND trade_ts_ms IS NOT NULL
                ORDER BY condition_id, trade_ts_ms
                """,
                conds,
            ):
                out[str(trade["condition_id"])].append(dict(trade))
    return out


def analyze(rows: list[dict[str, Any]], xuan_by_cond: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    counters = defaultdict(int)
    same_near30_delta_s: list[float] = []
    first_delta_s: list[float] = []
    cohorts: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for row in rows:
        ts_ms = int(row["trigger_ts_ms"])
        side = str(row["first_side"])
        trades = xuan_by_cond.get(str(row["condition_id"]), [])
        if trades:
            counters["xuan_any_in_market"] += 1
            first_delta_s.append((int(trades[0]["trade_ts_ms"]) - ts_ms) / 1000.0)
        if any(trade["outcome_side"] == side for trade in trades):
            counters["xuan_same_side_in_market"] += 1

        any_near30 = any(abs(int(trade["trade_ts_ms"]) - ts_ms) <= 30_000 for trade in trades)
        same_near30_trades = [
            trade
            for trade in trades
            if trade["outcome_side"] == side and abs(int(trade["trade_ts_ms"]) - ts_ms) <= 30_000
        ]
        same_near30 = bool(same_near30_trades)
        if any(abs(int(trade["trade_ts_ms"]) - ts_ms) <= 5_000 for trade in trades):
            counters["xuan_any_near5"] += 1
        if any(
            trade["outcome_side"] == side and abs(int(trade["trade_ts_ms"]) - ts_ms) <= 5_000
            for trade in trades
        ):
            counters["xuan_same_near5"] += 1
        if any_near30:
            counters["xuan_any_near30"] += 1
        if same_near30:
            counters["xuan_same_near30"] += 1
            nearest = min(same_near30_trades, key=lambda trade: abs(int(trade["trade_ts_ms"]) - ts_ms))
            same_near30_delta_s.append((int(nearest["trade_ts_ms"]) - ts_ms) / 1000.0)

        cohorts["xuan_same_near30" if same_near30 else "not_xuan_same_near30"].append(row)
        cohorts["xuan_any_near30" if any_near30 else "not_xuan_any_near30"].append(row)

    total = len(rows)
    return {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "rows": total,
        "markets": len({row["condition_id"] for row in rows}),
        "coverage": {
            key: {"count": counters[key], "rate": rate(counters[key], total)}
            for key in [
                "xuan_any_in_market",
                "xuan_same_side_in_market",
                "xuan_any_near5",
                "xuan_same_near5",
                "xuan_any_near30",
                "xuan_same_near30",
            ]
        },
        "timing": {
            "same_near30_delta_s": {
                "count": len(same_near30_delta_s),
                "p25": percentile(same_near30_delta_s, 25),
                "p50": percentile(same_near30_delta_s, 50),
                "p75": percentile(same_near30_delta_s, 75),
                "min": round(min(same_near30_delta_s), 6) if same_near30_delta_s else None,
                "max": round(max(same_near30_delta_s), 6) if same_near30_delta_s else None,
            },
            "xuan_first_minus_candidate_s": {
                "count": len(first_delta_s),
                "p25": percentile(first_delta_s, 25),
                "p50": percentile(first_delta_s, 50),
                "p75": percentile(first_delta_s, 75),
                "min": round(min(first_delta_s), 6) if first_delta_s else None,
                "max": round(max(first_delta_s), 6) if first_delta_s else None,
            },
        },
        "cohorts": {name: compact(cohort_rows) for name, cohort_rows in sorted(cohorts.items())},
    }


def render(report: dict[str, Any]) -> str:
    lines = [
        "# Taker BUY Signal Xuan Overlap",
        "",
        f"- rows: `{report['rows']}`",
        f"- markets: `{report['markets']}`",
        "",
        "## Coverage",
        "",
        "| metric | count | rate |",
        "|---|---:|---:|",
    ]
    for key, item in report["coverage"].items():
        lines.append(f"| `{key}` | {item['count']} | {item['rate']} |")
    lines.extend(
        [
            "",
            "## Cohorts",
            "",
            "| cohort | rows | pnl | ROI | first winner | closed |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for key, item in report["cohorts"].items():
        lines.append(
            f"| `{key}` | {item['rows']} | {item['pnl']} | {item['roi_on_first_cost']} | "
            f"{item['first_winner_rate']} | {item['closed_rate']} |"
        )
    lines.extend(["", "## Timing", "", "```json", json.dumps(report["timing"], indent=2), "```"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows-csv", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_signal_xuan_overlap"))
    args = parser.parse_args()

    with args.rows_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    xuan_by_cond = load_xuan_trades(args.replay_root, rows)
    report = analyze(rows, xuan_by_cond)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "taker_buy_signal_xuan_overlap_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_signal_xuan_overlap_report.md").write_text(render(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

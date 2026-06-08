#!/usr/bin/env python3
"""Counterfactual L2 replay for xuan tranche entries.

Given xuan's public tranche ladder, replay the same first-leg time, side, and
size against local L2 books:

- estimate first-leg taker sweep VWAP at the public first timestamp;
- scan opposite-side L2 asks under staged pair-cost ceilings;
- compare causal L2 fillability with xuan's observed completion.

This isolates state selection from private execution truth. It reads replay
SQLite in read-only mode and does not read raw data.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")
TRUSTED_START_MS = 1_777_274_700_000


@dataclass(frozen=True)
class L2Book:
    recv_ms: int
    side: str
    asks: tuple[tuple[float, float], ...]


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


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


def parse_schedule(value: str) -> list[tuple[int, float]]:
    out = []
    for part in value.split(","):
        if not part.strip():
            continue
        deadline_s, ceiling = part.split(":", 1)
        out.append((int(deadline_s), float(ceiling)))
    return sorted(out)


def schedule_name(schedule: list[tuple[int, float]]) -> str:
    return "_".join(f"{deadline}s_{ceiling:g}" for deadline, ceiling in schedule)


def ask_levels(row: sqlite3.Row) -> tuple[tuple[float, float], ...]:
    levels: list[tuple[float, float]] = []
    for i in range(1, 6):
        px = row[f"ask{i}_px"]
        sz = row[f"ask{i}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        levels.append((float(px), float(sz)))
    return tuple(levels)


def load_latest_l2_before(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    ts_ms: int,
    max_age_ms: int,
) -> L2Book | None:
    row = conn.execute(
        """
        SELECT recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (condition_id, side, ts_ms - max_age_ms, ts_ms),
    ).fetchone()
    if row is None:
        return None
    levels = ask_levels(row)
    if not levels:
        return None
    return L2Book(recv_ms=int(row["recv_ms"]), side=str(row["market_side"]), asks=levels)


def load_l2_window(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    start_ms: int,
    end_ms: int,
) -> list[L2Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, market_side,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2
        WHERE condition_id=?
          AND market_side=?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, id
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    out = []
    for row in rows:
        levels = ask_levels(row)
        if levels:
            out.append(L2Book(recv_ms=int(row["recv_ms"]), side=str(row["market_side"]), asks=levels))
    return out


def sweep_vwap(book: L2Book, target_size: float) -> tuple[float | None, float, float | None]:
    filled = 0.0
    notional = 0.0
    worst_px = None
    for px, sz in book.asks:
        use = min(sz, target_size - filled)
        if use <= 0:
            continue
        filled += use
        notional += use * px
        worst_px = px
        if filled + 1e-9 >= target_size:
            return notional / filled, filled, worst_px
    return None, filled, worst_px


def first_completion_by_schedule(
    l2_books: list[L2Book],
    start_ms: int,
    market_end_ms: int,
    target_size: float,
    first_vwap: float,
    schedule: list[tuple[int, float]],
) -> dict[str, Any] | None:
    cursor_ms = start_ms
    previous_deadline_s = 0
    for deadline_s, pair_cost_ceiling in schedule:
        if deadline_s <= previous_deadline_s:
            continue
        segment_end_ms = min(start_ms + deadline_s * 1000, market_end_ms)
        for book in l2_books:
            if book.recv_ms < cursor_ms:
                continue
            if book.recv_ms > segment_end_ms:
                break
            vwap, filled, worst_px = sweep_vwap(book, target_size)
            if vwap is None:
                continue
            pair_cost = first_vwap + vwap
            if pair_cost <= pair_cost_ceiling + 1e-9:
                return {
                    "completion_ts_ms": book.recv_ms,
                    "completion_vwap": vwap,
                    "completion_worst_px": worst_px,
                    "completion_filled_size": filled,
                    "completion_delay_s": (book.recv_ms - start_ms) / 1000.0,
                    "completion_stage_deadline_s": deadline_s,
                    "completion_pair_cost_ceiling": pair_cost_ceiling,
                    "pair_cost": pair_cost,
                    "pair_surplus": 1.0 - pair_cost,
                }
        previous_deadline_s = deadline_s
        cursor_ms = segment_end_ms
    return None


def load_tranches(path: Path, day_set: set[str]) -> list[dict[str, Any]]:
    out = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            first_ts_ms = int(float(row["first_ts_s"]) * 1000)
            if first_ts_ms < TRUSTED_START_MS:
                continue
            day = dt.datetime.fromtimestamp(first_ts_ms / 1000, tz=dt.timezone.utc).date().isoformat()
            if day not in day_set:
                continue
            try:
                size = float(row["size"])
                first_price = float(row["first_price"])
                pair_cost = float(row["pair_cost"])
                pair_delay_s = float(row["pair_delay_s"])
                round_start_ms = int(float(row["round_start_s"]) * 1000)
            except (TypeError, ValueError):
                continue
            out.append(
                {
                    "day": day,
                    "slug": row["slug"],
                    "condition_id": row["condition_id"],
                    "tranche_id": row["tranche_id"],
                    "round_start_ms": round_start_ms,
                    "round_end_ms": round_start_ms + 300_000,
                    "first_ts_ms": first_ts_ms,
                    "first_iso": row["first_iso"],
                    "first_offset_s": float(row["first_offset_s"]),
                    "first_side": row["first_side"],
                    "opposite_side": other(row["first_side"]),
                    "size": size,
                    "first_price": first_price,
                    "observed_second_price": float(row["second_price"]),
                    "observed_pair_cost": pair_cost,
                    "observed_pair_delay_s": pair_delay_s,
                    "first_tx": row.get("first_tx"),
                    "second_tx": row.get("second_tx"),
                }
            )
    return out


def load_match_index(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    out: dict[str, dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            tx = row.get("tx")
            if not tx or row.get("match_kind") != "price_size_match":
                continue
            try:
                match_trade_ts_ms = int(row["match_trade_ts_ms"])
                match_time_diff_ms = int(row["match_time_diff_ms"])
            except (TypeError, ValueError):
                continue
            out[tx] = {
                "match_trade_ts_ms": match_trade_ts_ms,
                "match_trade_iso": iso_ms(match_trade_ts_ms),
                "match_time_diff_ms": match_time_diff_ms,
                "execution_proxy": row.get("execution_proxy"),
                "match_taker_side": row.get("match_taker_side"),
            }
    return out


def run_counterfactual(
    conn_by_day: dict[str, sqlite3.Connection],
    tranches: list[dict[str, Any]],
    schedule: list[tuple[int, float]],
    max_l2_age_ms: int,
    match_index: dict[str, dict[str, Any]],
    use_matched_trade_ts: bool,
) -> list[dict[str, Any]]:
    max_deadline_s = max(deadline_s for deadline_s, _ in schedule)
    rows = []
    for item in tranches:
        conn = conn_by_day[item["day"]]
        first_match = match_index.get(str(item.get("first_tx") or ""))
        second_match = match_index.get(str(item.get("second_tx") or ""))
        first_exec_ts_ms = int(item["first_ts_ms"])
        first_ts_source = "xuan_data_api_ts"
        if use_matched_trade_ts and first_match is not None:
            first_exec_ts_ms = int(first_match["match_trade_ts_ms"])
            first_ts_source = "matched_public_trade_ts"
        observed_exec_delay_s = float(item["observed_pair_delay_s"])
        if use_matched_trade_ts and first_match is not None and second_match is not None:
            observed_exec_delay_s = (int(second_match["match_trade_ts_ms"]) - int(first_match["match_trade_ts_ms"])) / 1000.0
        first_l2 = load_latest_l2_before(
            conn,
            item["condition_id"],
            item["first_side"],
            first_exec_ts_ms,
            max_l2_age_ms,
        )
        row = {
            **item,
            "first_exec_ts_ms": first_exec_ts_ms,
            "first_exec_iso": iso_ms(first_exec_ts_ms),
            "first_ts_source": first_ts_source,
            "first_match_time_diff_ms": None if first_match is None else first_match["match_time_diff_ms"],
            "first_match_execution_proxy": None if first_match is None else first_match["execution_proxy"],
            "second_match_time_diff_ms": None if second_match is None else second_match["match_time_diff_ms"],
            "observed_exec_delay_s": observed_exec_delay_s,
            "schedule": schedule_name(schedule),
            "first_l2_available": False,
            "first_l2_age_ms": None,
            "first_l2_vwap": None,
            "first_l2_worst_px": None,
            "first_l2_full_size": False,
            "completion_l2_fill": False,
            "completion_l2_delay_s": None,
            "completion_l2_vwap": None,
            "completion_l2_worst_px": None,
            "counterfactual_pair_cost": None,
            "counterfactual_pair_surplus": None,
            "pair_cost_delta_vs_observed": None,
            "status": "no_first_l2",
        }
        if first_l2 is None:
            rows.append(row)
            continue
        row["first_l2_available"] = True
        row["first_l2_age_ms"] = first_exec_ts_ms - first_l2.recv_ms
        first_vwap, first_filled, first_worst_px = sweep_vwap(first_l2, float(item["size"]))
        row["first_l2_full_size"] = first_vwap is not None
        row["first_l2_vwap"] = None if first_vwap is None else round(first_vwap, 6)
        row["first_l2_worst_px"] = None if first_worst_px is None else round(first_worst_px, 6)
        if first_vwap is None:
            row["status"] = "insufficient_first_l2_depth"
            rows.append(row)
            continue
        window_end_ms = min(first_exec_ts_ms + max_deadline_s * 1000, int(item["round_end_ms"]))
        opp_books = load_l2_window(
            conn,
            item["condition_id"],
            item["opposite_side"],
            first_exec_ts_ms,
            window_end_ms,
        )
        completion = first_completion_by_schedule(
            opp_books,
            first_exec_ts_ms,
            int(item["round_end_ms"]),
            float(item["size"]),
            first_vwap,
            schedule,
        )
        if completion is None:
            row["status"] = "schedule_not_filled"
            rows.append(row)
            continue
        row.update(
            {
                "completion_l2_fill": True,
                "completion_l2_ts_ms": completion["completion_ts_ms"],
                "completion_l2_iso": iso_ms(int(completion["completion_ts_ms"])),
                "completion_l2_delay_s": round(float(completion["completion_delay_s"]), 3),
                "completion_l2_vwap": round(float(completion["completion_vwap"]), 6),
                "completion_l2_worst_px": None
                if completion["completion_worst_px"] is None
                else round(float(completion["completion_worst_px"]), 6),
                "completion_stage_deadline_s": completion["completion_stage_deadline_s"],
                "completion_pair_cost_ceiling": completion["completion_pair_cost_ceiling"],
                "counterfactual_pair_cost": round(float(completion["pair_cost"]), 6),
                "counterfactual_pair_surplus": round(float(completion["pair_surplus"]), 6),
                "pair_cost_delta_vs_observed": round(float(completion["pair_cost"]) - float(item["observed_pair_cost"]), 6),
                "status": "closed",
            }
        )
        rows.append(row)
    return rows


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first_ok = [row for row in rows if row["first_l2_full_size"]]
    closed = [row for row in rows if row["completion_l2_fill"]]
    observed_30 = [row for row in rows if float(row["observed_pair_delay_s"]) <= 30]
    return {
        "count": len(rows),
        "first_l2_full_size_count": len(first_ok),
        "first_l2_full_size_rate": rate(len(first_ok), len(rows)),
        "counterfactual_closed_count": len(closed),
        "counterfactual_closed_rate": rate(len(closed), len(rows)),
        "counterfactual_closed_rate_among_first_l2_full": rate(len(closed), len(first_ok)),
        "observed_30s_completion_rate": rate(len(observed_30), len(rows)),
        "counterfactual_30s_completion_rate": rate(
            sum(1 for row in closed if float(row["completion_l2_delay_s"]) <= 30), len(rows)
        ),
        "observed_pair_cost": summarize([row["observed_pair_cost"] for row in rows]),
        "counterfactual_pair_cost": summarize([row["counterfactual_pair_cost"] for row in closed]),
        "pair_cost_delta_vs_observed": summarize([row["pair_cost_delta_vs_observed"] for row in closed]),
        "observed_delay_s": summarize([row["observed_pair_delay_s"] for row in rows]),
        "observed_exec_delay_s": summarize([row["observed_exec_delay_s"] for row in rows]),
        "counterfactual_delay_s": summarize([row["completion_l2_delay_s"] for row in closed]),
        "status_counts": dict(
            sorted({status: sum(1 for row in rows if row["status"] == status) for status in {r["status"] for r in rows}}.items())
        ),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "latest_2026_05_01": compact([r for r in rows if r["day"] == "2026-05-01"])}
    by_offset = {
        "000_030s": lambda r: float(r["first_offset_s"]) < 30,
        "030_120s": lambda r: 30 <= float(r["first_offset_s"]) < 120,
        "120_240s": lambda r: 120 <= float(r["first_offset_s"]) < 240,
        "240_300s": lambda r: float(r["first_offset_s"]) >= 240,
    }
    out["by_offset"] = {name: compact([r for r in rows if pred(r)]) for name, pred in by_offset.items()}
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = []
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
        "# Xuan L2 Counterfactual",
        "",
        "## Scope",
        "",
        f"- tranches_csv: `{report['tranches_csv']}`",
        f"- days: `{report['days']}`",
        f"- schedule: `{report['parameters']['schedule']}`",
        f"- max_l2_age_ms: `{report['parameters']['max_l2_age_ms']}`",
        f"- use_matched_trade_ts: `{report['parameters']['use_matched_trade_ts']}`",
        "- Reads replay SQLite read-only. Does not use raw or own execution truth.",
        "- Uses xuan public first-leg time, side, and size; completion is replay L2 staged sweep.",
        "- If enabled, exact public trade matches replace lagged xuan Data API timestamps.",
        "",
        "## Summary",
        "",
        "| cohort | n | first L2 full | cf closed | obs 30s | cf 30s | obs pair p50 | cf pair p50 | delta p50 | cf delay p50 | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    cohorts = {"all": report["aggregate"]["all"], "latest_2026_05_01": report["aggregate"]["latest_2026_05_01"]}
    cohorts.update({f"offset_{k}": v for k, v in report["aggregate"]["by_offset"].items()})
    for name, item in cohorts.items():
        lines.append(
            f"| {name} | {item['count']} | {item['first_l2_full_size_rate']} | {item['counterfactual_closed_rate']} | "
            f"{item['observed_30s_completion_rate']} | {item['counterfactual_30s_completion_rate']} | "
            f"{item['observed_pair_cost']['p50']} | {item['counterfactual_pair_cost']['p50']} | "
            f"{item['pair_cost_delta_vs_observed']['p50']} | {item['counterfactual_delay_s']['p50']} | "
            f"`{item['status_counts']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- High counterfactual close rate means xuan's public entry timing lands in states where L2 completion is actually available.",
            "- Large negative/positive pair-cost delta vs observed indicates our L2 sweep proxy is not equivalent to xuan's actual execution.",
            "- If first L2 full-size is low, xuan likely uses smaller child orders, maker liquidity, or hidden/fast-changing depth.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument(
        "--tranches-csv",
        default="data/exports/xuan_research_runs/replay_20260502_full/xuan_tranche_ladder/xuan_tranche_ladder_tranches.csv",
    )
    parser.add_argument("--output-dir", default="data/exports/xuan_l2_counterfactual")
    parser.add_argument("--schedule", default="30:0.90,50:0.95,70:1.00")
    parser.add_argument("--max-l2-age-ms", type=int, default=1000)
    parser.add_argument("--match-csv")
    parser.add_argument("--use-matched-trade-ts", action="store_true")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    day_set = set(days)
    schedule = parse_schedule(args.schedule)
    tranches = load_tranches(Path(args.tranches_csv), day_set)
    match_index = load_match_index(Path(args.match_csv)) if args.match_csv else {}
    conn_by_day: dict[str, sqlite3.Connection] = {}
    try:
        for day in days:
            db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
            if db_path.exists():
                conn_by_day[day] = connect_ro(db_path)
        tranches = [row for row in tranches if row["day"] in conn_by_day]
        rows = run_counterfactual(
            conn_by_day,
            tranches,
            schedule,
            args.max_l2_age_ms,
            match_index,
            args.use_matched_trade_ts,
        )
    finally:
        for conn in conn_by_day.values():
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_l2_counterfactual_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "tranches_csv": str(Path(args.tranches_csv).resolve()),
        "days": days,
        "parameters": {
            "schedule": args.schedule,
            "max_l2_age_ms": args.max_l2_age_ms,
            "match_csv": args.match_csv,
            "use_matched_trade_ts": args.use_matched_trade_ts,
            "match_index_size": len(match_index),
        },
        "tranche_count": len(tranches),
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "xuan_l2_counterfactual_rows.csv").resolve()),
            "summary_json": str((output_dir / "xuan_l2_counterfactual_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_l2_counterfactual_report.md").resolve()),
        },
    }
    (output_dir / "xuan_l2_counterfactual_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_l2_counterfactual_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

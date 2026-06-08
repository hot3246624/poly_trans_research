#!/usr/bin/env python3
"""Audit whether xuan's first-leg prices were queueable before public fills.

This script answers a narrow execution question:

    Could we have placed an order at xuan's first-leg price before the public
    fill, based on replay L2 book visibility?

It does not read raw capture, does not use own execution truth, and does not
claim exact queue position. It only measures whether the relevant price level
was visible in the public top-5 bid book before the fill.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
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


def bid_levels(row: sqlite3.Row) -> list[tuple[float, float]]:
    out = []
    for idx in range(1, 6):
        px = row[f"bid{idx}_px"]
        sz = row[f"bid{idx}_sz"]
        if px is None or sz is None or float(sz) <= 0:
            continue
        out.append((float(px), float(sz)))
    return out


def load_l2_bids(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ms,
               bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz,
               bid4_px, bid4_sz, bid5_px, bid5_sz
        FROM md_book_l2
        WHERE condition_id = ?
          AND market_side = ?
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY recv_ms, id
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    out = []
    for row in rows:
        levels = bid_levels(row)
        if levels:
            out.append({"recv_ms": int(row["recv_ms"]), "levels": levels})
    return out


def nearest_sell_trade(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    ts_ms: int,
    lookaround_ms: int,
) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND market_side = ?
          AND taker_side = 'SELL'
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
        """,
        (condition_id, side, ts_ms - lookaround_ms, ts_ms + lookaround_ms),
    ).fetchall()
    if not rows:
        return {
            "nearest_sell_dt_ms": None,
            "nearest_sell_price": None,
            "nearest_sell_size": None,
        }
    best = min(rows, key=lambda row: (abs(int(row["trade_ts_ms"]) - ts_ms), abs(float(row["price"]))))
    return {
        "nearest_sell_dt_ms": int(best["trade_ts_ms"]) - ts_ms,
        "nearest_sell_price": float(best["price"]),
        "nearest_sell_size": float(best["size"]),
    }


def load_sell_trades_window(
    conn: sqlite3.Connection,
    condition_id: str,
    side: str,
    start_ms: int,
    end_ms: int,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND market_side = ?
          AND taker_side = 'SELL'
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, side, start_ms, end_ms),
    ).fetchall()
    return [
        {"trade_ts_ms": int(row["trade_ts_ms"]), "price": float(row["price"]), "size": float(row["size"])}
        for row in rows
    ]


def nearest_sell_trade_from_rows(
    rows: list[dict[str, Any]],
    times: list[int],
    ts_ms: int,
    price: float,
    lookaround_ms: int,
) -> dict[str, Any]:
    lo = bisect.bisect_left(times, ts_ms - lookaround_ms)
    hi = bisect.bisect_right(times, ts_ms + lookaround_ms)
    if lo >= hi:
        return {
            "nearest_sell_dt_ms": None,
            "nearest_sell_price": None,
            "nearest_sell_size": None,
        }
    best = min(rows[lo:hi], key=lambda row: (abs(int(row["trade_ts_ms"]) - ts_ms), abs(float(row["price"]) - price)))
    return {
        "nearest_sell_dt_ms": int(best["trade_ts_ms"]) - ts_ms,
        "nearest_sell_price": float(best["price"]),
        "nearest_sell_size": float(best["size"]),
    }


def level_stats(levels: list[tuple[float, float]], price: float, tol: float) -> dict[str, Any]:
    same = [(px, sz) for px, sz in levels if abs(px - price) <= tol]
    gt = [(px, sz) for px, sz in levels if px > price + tol]
    ge = [(px, sz) for px, sz in levels if px >= price - tol]
    return {
        "best_bid": levels[0][0] if levels else None,
        "best_bid_size": levels[0][1] if levels else None,
        "price_in_top5_bid": bool(same),
        "bid_gte_price_in_top5": bool(ge),
        "same_level_size": round(sum(sz for _px, sz in same), 6),
        "cum_bid_size_gt_price": round(sum(sz for _px, sz in gt), 6),
        "cum_bid_size_gte_price": round(sum(sz for _px, sz in ge), 6),
    }


def visible_duration_ms(
    books: list[dict[str, Any]],
    ts_ms: int,
    price: float,
    tol: float,
    predicate: str,
) -> int | None:
    if not books:
        return None
    segment_start: int | None = None
    last_true_recv: int | None = None
    for book in books:
        stats = level_stats(book["levels"], price, tol)
        ok = bool(stats[predicate])
        if ok:
            if segment_start is None:
                segment_start = int(book["recv_ms"])
            last_true_recv = int(book["recv_ms"])
        else:
            segment_start = None
            last_true_recv = None
    if segment_start is None or last_true_recv is None:
        return 0
    # Duration is measured to fill timestamp because no later book exists before fill.
    return max(0, ts_ms - segment_start)


def classify(row: dict[str, Any]) -> list[str]:
    out = ["all"]
    first_price = as_float(row.get("first_price"))
    first_l2 = as_float(row.get("first_l2_vwap"))
    if first_price is not None and 0.40 <= first_price < 0.55:
        out.append("price_040_055")
        if first_l2 is not None and first_l2 - first_price >= 0.005:
            out.append("price_040_055_l2_edge_ge_0_5c")
    if first_price is not None and 0.80 <= first_price < 0.90:
        out.append("price_080_090")
    if first_l2 is not None and first_price is not None and first_l2 - first_price >= 0.03:
        out.append("l2_edge_ge_3c")
    if row.get("path_label") == "slow_profit_lt95":
        out.append("slow_profit_lt95")
    if row.get("path_label") == "fast_control":
        out.append("fast_control")
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    covered = [row for row in rows if row.get("latest_l2_age_ms") is not None]
    same_visible = [row for row in covered if row.get("latest_price_in_top5_bid") is True]
    bid_gte = [row for row in covered if row.get("latest_bid_gte_price_in_top5") is True]
    nearest_same_price = [
        row
        for row in covered
        if as_float(row.get("nearest_sell_price")) is not None
        and as_float(row.get("first_price")) is not None
        and abs(float(row["nearest_sell_price"]) - float(row["first_price"])) <= 0.001
        and as_float(row.get("nearest_sell_dt_ms")) is not None
        and abs(float(row["nearest_sell_dt_ms"])) <= 5000
    ]
    nearest_same_price_1s = [
        row for row in nearest_same_price if abs(float(row.get("nearest_sell_dt_ms") or 0.0)) <= 1000
    ]
    sweep_possible = [
        row
        for row in nearest_same_price
        if (as_float(row.get("nearest_sell_size")) or 0.0)
        >= (as_float(row.get("latest_cum_bid_size_gt_price")) or 0.0) + (as_float(row.get("size")) or 0.0)
    ]
    same_durations = [as_float(row.get("same_price_visible_duration_s")) for row in covered]
    gte_durations = [as_float(row.get("bid_gte_price_visible_duration_s")) for row in covered]
    return {
        "n": len(rows),
        "l2_coverage_rate": rate(len(covered), len(rows)),
        "latest_same_price_visible_rate": rate(len(same_visible), len(covered)),
        "latest_bid_gte_price_visible_rate": rate(len(bid_gte), len(covered)),
        "nearest_sell_same_price_5s_rate": rate(len(nearest_same_price), len(covered)),
        "nearest_sell_same_price_1s_rate": rate(len(nearest_same_price_1s), len(covered)),
        "nearest_sell_sweep_possible_rate": rate(len(sweep_possible), len(covered)),
        "same_price_duration_s": summarize(same_durations),
        "bid_gte_price_duration_s": summarize(gte_durations),
        "same_price_duration_ge_1s_rate": rate(sum(1 for x in same_durations if x is not None and x >= 1), len(covered)),
        "same_price_duration_ge_5s_rate": rate(sum(1 for x in same_durations if x is not None and x >= 5), len(covered)),
        "same_price_duration_ge_10s_rate": rate(sum(1 for x in same_durations if x is not None and x >= 10), len(covered)),
        "cum_bid_gt_price": summarize([as_float(row.get("latest_cum_bid_size_gt_price")) for row in covered]),
        "cum_bid_gte_price": summarize([as_float(row.get("latest_cum_bid_size_gte_price")) for row in covered]),
        "xuan_first_size": summarize([as_float(row.get("size")) for row in rows]),
    }


def shift_summary(rows: list[dict[str, Any]], shifts_ms: list[int]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for shift in shifts_ms:
        same_key = f"shift_{shift}_price_in_top5_bid"
        gte_key = f"shift_{shift}_bid_gte_price_in_top5"
        age_key = f"shift_{shift}_l2_age_ms"
        covered = [row for row in rows if row.get(age_key) not in (None, "")]
        same = [row for row in covered if row.get(same_key) is True]
        gte = [row for row in covered if row.get(gte_key) is True]
        out[str(shift)] = {
            "n": len(rows),
            "coverage_rate": rate(len(covered), len(rows)),
            "same_price_visible_rate": rate(len(same), len(covered)),
            "bid_gte_price_visible_rate": rate(len(gte), len(covered)),
            "l2_age_ms": summarize([as_float(row.get(age_key)) for row in covered]),
        }
    return out


def build_rows(args: argparse.Namespace) -> list[dict[str, Any]]:
    db_cache: dict[str, sqlite3.Connection] = {}
    prepared: list[dict[str, Any]] = []
    for row in read_csv(Path(args.input_csv)):
        ts = as_int(row.get("first_exec_ts_ms_resolved")) or as_int(row.get("first_exec_ts_ms")) or as_int(row.get("first_ts_ms"))
        first_price = as_float(row.get("first_price"))
        side = row.get("first_side")
        if ts is None or first_price is None or side not in {"YES", "NO"}:
            continue
        cohorts = classify(row)
        if args.only_cohort and args.only_cohort not in cohorts:
            continue
        day = day_from_ms(ts)
        if day not in args.days:
            continue
        prepared.append({"row": row, "ts": ts, "first_price": first_price, "side": side, "day": day, "cohorts": cohorts})

    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for item in prepared:
        key = (item["day"], item["row"]["condition_id"], item["side"])
        groups.setdefault(key, []).append(item)

    out = []
    try:
        for (day, condition_id, side), items in sorted(groups.items()):
            db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
            if not db_path.exists():
                continue
            conn = db_cache.get(day)
            if conn is None:
                conn = connect_ro(db_path)
                db_cache[day] = conn
            shifts_ms = args.time_shifts_ms
            min_shift = min(shifts_ms or [0])
            max_shift = max(shifts_ms or [0])
            min_ts = min(int(item["ts"]) for item in items)
            max_ts = max(int(item["ts"]) for item in items)
            all_books = load_l2_bids(
                conn,
                condition_id,
                side,
                min_ts + min_shift - args.lookback_s * 1000,
                max_ts + max_shift,
            )
            book_times = [int(book["recv_ms"]) for book in all_books]
            all_sells = load_sell_trades_window(
                conn,
                condition_id,
                side,
                min_ts - args.sell_lookaround_ms,
                max_ts + args.sell_lookaround_ms,
            )
            sell_times = [int(row["trade_ts_ms"]) for row in all_sells]
            for prepared_item in items:
                row = prepared_item["row"]
                ts = int(prepared_item["ts"])
                first_price = float(prepared_item["first_price"])
                lo = bisect.bisect_left(book_times, ts - args.lookback_s * 1000)
                hi = bisect.bisect_right(book_times, ts)
                books = all_books[lo:hi]
                latest = books[-1] if books else None
                latest_stats = level_stats(latest["levels"], first_price, args.price_tol) if latest else {}
                same_duration = visible_duration_ms(books, ts, first_price, args.price_tol, "price_in_top5_bid")
                gte_duration = visible_duration_ms(books, ts, first_price, args.price_tol, "bid_gte_price_in_top5")
                sell = nearest_sell_trade_from_rows(all_sells, sell_times, ts, first_price, args.sell_lookaround_ms)
                shifted_stats: dict[str, Any] = {}
                for shift in shifts_ms:
                    shifted_ts = ts + shift
                    s_lo = bisect.bisect_left(book_times, shifted_ts - args.lookback_s * 1000)
                    s_hi = bisect.bisect_right(book_times, shifted_ts)
                    shifted_books = all_books[s_lo:s_hi]
                    shifted_latest = shifted_books[-1] if shifted_books else None
                    stats = level_stats(shifted_latest["levels"], first_price, args.price_tol) if shifted_latest else {}
                    shifted_stats[f"shift_{shift}_l2_recv_ms"] = None if shifted_latest is None else shifted_latest["recv_ms"]
                    shifted_stats[f"shift_{shift}_l2_age_ms"] = None if shifted_latest is None else shifted_ts - int(shifted_latest["recv_ms"])
                    shifted_stats[f"shift_{shift}_best_bid"] = stats.get("best_bid")
                    shifted_stats[f"shift_{shift}_price_in_top5_bid"] = stats.get("price_in_top5_bid")
                    shifted_stats[f"shift_{shift}_bid_gte_price_in_top5"] = stats.get("bid_gte_price_in_top5")
                item = dict(row)
                item.update(
                    {
                        "queue_ts_ms": ts,
                        "queue_iso": iso_ms(ts),
                        "latest_l2_recv_ms": None if latest is None else latest["recv_ms"],
                        "latest_l2_age_ms": None if latest is None else ts - int(latest["recv_ms"]),
                        "latest_best_bid": latest_stats.get("best_bid"),
                        "latest_best_bid_size": latest_stats.get("best_bid_size"),
                        "latest_price_in_top5_bid": latest_stats.get("price_in_top5_bid"),
                        "latest_bid_gte_price_in_top5": latest_stats.get("bid_gte_price_in_top5"),
                        "latest_same_level_size": latest_stats.get("same_level_size"),
                        "latest_cum_bid_size_gt_price": latest_stats.get("cum_bid_size_gt_price"),
                        "latest_cum_bid_size_gte_price": latest_stats.get("cum_bid_size_gte_price"),
                        "same_price_visible_duration_s": None if same_duration is None else round(same_duration / 1000.0, 3),
                        "bid_gte_price_visible_duration_s": None if gte_duration is None else round(gte_duration / 1000.0, 3),
                        **sell,
                        **shifted_stats,
                        "cohorts": ",".join(prepared_item["cohorts"]),
                    }
                )
                out.append(item)
    finally:
        for conn in db_cache.values():
            conn.close()
    return out


def cohort_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for cohort in str(row.get("cohorts") or "").split(","):
            if not cohort:
                continue
            cohorts.setdefault(cohort, []).append(row)
    return {name: compact(xs) for name, xs in sorted(cohorts.items())}


def cohort_shift_summary(rows: list[dict[str, Any]], shifts_ms: list[int]) -> dict[str, Any]:
    cohorts: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        for cohort in str(row.get("cohorts") or "").split(","):
            if not cohort:
                continue
            cohorts.setdefault(cohort, []).append(row)
    return {name: shift_summary(xs, shifts_ms) for name, xs in sorted(cohorts.items())}


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan Queue Timing Audit",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- input_csv: `{report['input_csv']}`",
        f"- lookback_s: `{report['lookback_s']}`",
        "- Read-only replay SQLite. No raw data. No own execution truth.",
        "",
        "## Cohorts",
        "",
        "| cohort | n | l2 coverage | same-price visible | bid>=price visible | same p50 s | same >=5s | bid>= p50 s | cum bid>price p50 | size p50 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for cohort, item in report["cohorts"].items():
        lines.append(
            f"| {cohort} | {item['n']} | {item['l2_coverage_rate']} | "
            f"{item['latest_same_price_visible_rate']} | {item['latest_bid_gte_price_visible_rate']} | "
            f"{item['same_price_duration_s']['p50']} | {item['same_price_duration_ge_5s_rate']} | "
            f"{item['bid_gte_price_duration_s']['p50']} | {item['cum_bid_gt_price']['p50']} | "
            f"{item['xuan_first_size']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Public Sell Match",
            "",
            "| cohort | n | same-price sell within 5s | same-price sell within 1s | sweep-through possible |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for cohort, item in report["cohorts"].items():
        lines.append(
            f"| {cohort} | {item['n']} | {item['nearest_sell_same_price_5s_rate']} | "
            f"{item['nearest_sell_same_price_1s_rate']} | {item['nearest_sell_sweep_possible_rate']} |"
        )
    if report.get("shift_summary"):
        lines.extend(
            [
                "",
                "## Timestamp Shift Sensitivity",
                "",
                "| cohort | shift_ms | coverage | same-price visible | bid>=price visible | l2 age p50 ms |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for cohort, shifts in report["shift_summary"].items():
            for shift, item in shifts.items():
                lines.append(
                    f"| {cohort} | {shift} | {item['coverage_rate']} | {item['same_price_visible_rate']} | "
                    f"{item['bid_gte_price_visible_rate']} | {item['l2_age_ms']['p50']} |"
                )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `same-price visible` means xuan's first price was visible in top-5 bid levels immediately before fill.",
            "- `bid>=price visible` is weaker; higher bid levels may need to be consumed before an order at xuan's price fills.",
            "- Long same-price duration suggests a replicable queueing path; short or missing duration suggests hidden timing/queue edge.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument(
        "--input-csv",
        default="data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_proxy_gate_5d/xuan_winner_proxy_gate_rows.csv",
    )
    parser.add_argument("--output-dir", default="data/exports/xuan_queue_timing_0427_0501")
    parser.add_argument("--lookback-s", type=int, default=30)
    parser.add_argument("--price-tol", type=float, default=0.001)
    parser.add_argument("--sell-lookaround-ms", type=int, default=5000)
    parser.add_argument("--only-cohort", default="")
    parser.add_argument("--time-shifts-ms", default="-5000,-3000,-1000,0,1000,3000,5000")
    args = parser.parse_args()
    args.days = [day.strip() for day in args.days.split(",") if day.strip()]
    args.time_shifts_ms = [int(part.strip()) for part in args.time_shifts_ms.split(",") if part.strip()]

    rows = build_rows(args)
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "input_csv": str(Path(args.input_csv).resolve()),
        "days": args.days,
        "lookback_s": args.lookback_s,
        "price_tol": args.price_tol,
        "sell_lookaround_ms": args.sell_lookaround_ms,
        "cohorts": cohort_summary(rows),
        "shift_summary": cohort_shift_summary(rows, args.time_shifts_ms),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_queue_timing_rows.csv", rows)
    (output_dir / "xuan_queue_timing_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_queue_timing_report.md").write_text(render_report(summary), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

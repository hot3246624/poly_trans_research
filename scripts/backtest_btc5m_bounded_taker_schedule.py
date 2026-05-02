#!/usr/bin/env python3
"""Causal staged completion schedule for bounded-taker BTC 5m.

Schedule example:

    30:0.90,50:0.95,70:1.00

Means:

- from first fill to +30s, complete only if pair_cost <= 0.90;
- from +30s to +50s, complete if pair_cost <= 0.95;
- from +50s to +70s, complete if pair_cost <= 1.00.

This tests the missing trading-system element: profit target, repair widening,
and residual timeout.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any

from backtest_btc5m_bounded_taker_threshold import (
    candidate_from_book,
    first_threshold_completion,
    sample_indices,
)
from backtest_btc5m_high_side_wait_fill_proxy import (
    DEFAULT_DAYS,
    connect_ro,
    day_max_ms,
    iso_ms,
    load_books,
    load_markets,
    load_modes,
    rate,
    side_value,
    summarize,
    write_csv,
)


def parse_schedule(value: str) -> list[tuple[int, float]]:
    out = []
    for part in value.split(","):
        if not part.strip():
            continue
        deadline_s, ceiling = part.split(":", 1)
        out.append((int(deadline_s), float(ceiling)))
    return sorted(out)


def schedule_name(schedule: list[tuple[int, float]]) -> str:
    return "_".join(f"{t}s_{c:g}" for t, c in schedule)


def simulate_candidate(
    market: Any,
    books: list[Any],
    times: list[int],
    mode: dict[str, Any],
    cap: float,
    sample_idx: int,
    schedule: list[tuple[int, float]],
) -> dict[str, Any] | None:
    book = books[sample_idx]
    candidate = candidate_from_book(book, market, mode)
    if candidate is None:
        return None
    clip = float(mode["clip_size"])
    first_px = float(candidate["first_ask"])
    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "schedule": schedule_name(schedule),
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        **candidate,
        "clip_size": clip,
        "first_fill": clip <= cap,
        "first_fill_ts_ms": candidate["candidate_ts_ms"] if clip <= cap else None,
        "first_fill_iso": candidate["candidate_iso"] if clip <= cap else None,
        "first_fill_px": first_px if clip <= cap else None,
        "completion_fill": False,
        "completion_stage_deadline_s": None,
        "completion_pair_cost_ceiling": None,
        "completion_delay_s": None,
        "completion_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row

    segment_start_ms = candidate["candidate_ts_ms"]
    previous_deadline_s = 0
    for deadline_s, pair_cost_ceiling in schedule:
        if deadline_s <= previous_deadline_s:
            continue
        segment_end_ms = min(candidate["candidate_ts_ms"] + deadline_s * 1000, int(market["end_ms"]))
        max_opp_ask = pair_cost_ceiling - first_px
        if max_opp_ask <= 0:
            previous_deadline_s = deadline_s
            segment_start_ms = segment_end_ms
            continue
        completion_book, completion_px = first_threshold_completion(
            books,
            times,
            segment_start_ms,
            segment_end_ms,
            candidate["opposite_side"],
            clip,
            max_opp_ask,
        )
        if completion_book is not None and completion_px is not None:
            pair_cost = first_px + completion_px
            row.update(
                {
                    "completion_fill": True,
                    "completion_stage_deadline_s": deadline_s,
                    "completion_pair_cost_ceiling": pair_cost_ceiling,
                    "completion_ts_ms": completion_book.recv_ms,
                    "completion_iso": iso_ms(completion_book.recv_ms),
                    "completion_delay_s": round((completion_book.recv_ms - candidate["candidate_ts_ms"]) / 1000.0, 3),
                    "completion_px": round(completion_px, 6),
                    "completion_depth": side_value(completion_book, candidate["opposite_side"], "ask_sz"),
                    "pair_cost": round(pair_cost, 6),
                    "pair_surplus": round(1.0 - pair_cost, 6),
                    "status": "closed",
                }
            )
            return row
        previous_deadline_s = deadline_s
        segment_start_ms = segment_end_ms

    row["status"] = "schedule_not_filled"
    return row


def scan_market(
    market: Any,
    books: list[Any],
    modes: list[dict[str, Any]],
    schedules: list[list[tuple[int, float]]],
    sample_interval_ms: int,
) -> list[dict[str, Any]]:
    times = [book.recv_ms for book in books]
    sample_idxs = sample_indices(books, int(market["start_ms"]), sample_interval_ms)
    out = []
    seen: set[tuple[str, float, str]] = set()
    for mode in modes:
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            for schedule in schedules:
                key = (mode["name"], float(cap), schedule_name(schedule))
                if key in seen:
                    continue
                for idx in sample_idxs:
                    row = simulate_candidate(market, books, times, mode, float(cap), idx, schedule)
                    if row is None:
                        continue
                    out.append(row)
                    seen.add(key)
                    break
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row.get("completion_fill") is True]
    stage_counts = {}
    for row in closed:
        key = f"{row.get('completion_stage_deadline_s')}s@{row.get('completion_pair_cost_ceiling')}"
        stage_counts[key] = stage_counts.get(key, 0) + 1
    return {
        "candidate_count": len(rows),
        "closed_count": len(closed),
        "closed_rate_among_candidates": rate(len(closed), len(rows)),
        "completion_delay_s": summarize([row.get("completion_delay_s") for row in closed]),
        "pair_cost": summarize([row.get("pair_cost") for row in closed]),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.90), len(closed)),
        "pair_cost_lt_0_95_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.95), len(closed)),
        "avg_surplus_at_clip": (
            round(sum(float(row["pair_surplus"]) * float(row["clip_size"]) for row in closed) / len(closed), 6)
            if closed
            else None
        ),
        "stage_counts": dict(sorted(stage_counts.items())),
        "status_counts": dict(sorted({status: sum(1 for row in rows if row.get("status") == status) for status in {row.get("status") for row in rows}}.items())),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_mode_cap_schedule": {}}
    keys = sorted({(row["mode"], float(row["cap"]), row["schedule"]) for row in rows})
    for mode, cap, schedule in keys:
        xs = [row for row in rows if row["mode"] == mode and float(row["cap"]) == cap and row["schedule"] == schedule]
        out["by_mode_cap_schedule"][f"{mode}|cap={cap:g}|schedule={schedule}"] = compact(xs)
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Bounded Taker Staged Completion",
        "",
        "## Scope",
        "",
        f"- modes_file: `{report['modes_file']}`",
        f"- days: `{report['days']}`",
        f"- schedules: `{report['parameters']['schedules']}`",
        "- Completion is causal staged threshold, not best-in-window hindsight.",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "",
        "## Results",
        "",
        "| mode/cap/schedule | candidates | closed | pair p50 | <0.90 | delay p50 | avg surplus | stages | status |",
        "|---|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for key, item in report["aggregate"]["by_mode_cap_schedule"].items():
        lines.append(
            f"| {key} | {item['candidate_count']} | {item['closed_rate_among_candidates']} | "
            f"{item['pair_cost']['p50']} | {item['pair_cost_lt_0_90_rate']} | "
            f"{item['completion_delay_s']['p50']} | {item['avg_surplus_at_clip']} | "
            f"`{item['stage_counts']}` | `{item['status_counts']}` |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument(
        "--modes-file",
        default="data/exports/xuan_cycle_feature_gate_20260501/high_side_wait_taker_shadow_candidates.json",
    )
    parser.add_argument("--output-dir", default="data/exports/btc5m_bounded_taker_schedule_20260501")
    parser.add_argument(
        "--schedules",
        default="30:0.90,50:0.95,70:1.00;30:0.90,70:0.95;50:0.90,70:0.95",
    )
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    schedules = [parse_schedule(x.strip()) for x in args.schedules.split(";") if x.strip()]
    modes = load_modes(Path(args.modes_file), max(deadline for schedule in schedules for deadline, _ in schedule))

    rows: list[dict[str, Any]] = []
    db_summaries = []
    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            db_summaries.append({"day": day, "db_path": str(db_path), "markets": len(markets)})
            for market in markets:
                books = load_books(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                if not books:
                    continue
                market_rows = scan_market(market, books, modes, schedules, int(args.sample_interval_s * 1000))
                for row in market_rows:
                    row["day"] = day
                rows.extend(market_rows)
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_bounded_taker_schedule_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "modes_file": str(Path(args.modes_file).resolve()),
        "days": days,
        "parameters": {
            "schedules": args.schedules,
            "sample_interval_s": args.sample_interval_s,
        },
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_bounded_taker_schedule_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_bounded_taker_schedule_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_bounded_taker_schedule_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_bounded_taker_schedule_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_bounded_taker_schedule_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Non-hindsight bounded-taker completion backtest for BTC 5m.

Unlike the earlier "best ask in window" scanner, this script uses a causal
completion rule:

- first leg crosses high-side ask immediately;
- opposite leg is bought at the first L1 ask that satisfies a pair-cost ceiling;
- if the ceiling is never touched within the wait budget, the episode remains
  residual in this proxy.

This is a stricter test of whether bounded taker can be implemented without
knowing the future best opposite ask.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any

from backtest_btc5m_high_side_wait_fill_proxy import (
    DEFAULT_DAYS,
    Book,
    connect_ro,
    day_max_ms,
    first_depth,
    first_price,
    high_side,
    iso_ms,
    load_books,
    load_markets,
    load_modes,
    other,
    rate,
    side_value,
    summarize,
    write_csv,
)


def sample_indices(books: list[Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + sample_interval_ms
    return out


def candidate_from_book(book: Book, market: Any, mode: dict[str, Any]) -> dict[str, Any] | None:
    offset_s = (book.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < mode["offset_start_s"] or offset_s >= mode["offset_end_s"]:
        return None
    side = high_side(book)
    if side is None:
        return None
    ask = first_price(book, side, "ask")
    ask_depth = first_depth(book, side, "ask")
    if ask is None or ask < mode["first_price_min"] or ask >= mode["first_price_max"]:
        return None
    if mode.get("first_depth_required", True) and (ask_depth is None or ask_depth < mode["clip_size"]):
        return None
    opp = other(side)
    opp_ask = side_value(book, opp, "ask_px")
    if opp_ask is None:
        return None
    return {
        "candidate_ts_ms": book.recv_ms,
        "candidate_iso": iso_ms(book.recv_ms),
        "candidate_offset_s": round(offset_s, 3),
        "first_side": side,
        "opposite_side": opp,
        "first_ask": round(ask, 6),
        "first_ask_depth": None if ask_depth is None else round(ask_depth, 6),
        "opposite_ask_now": round(opp_ask, 6),
        "opposite_ask_sz_now": side_value(book, opp, "ask_sz"),
        "immediate_pair_cost": round(ask + opp_ask, 6),
    }


def first_threshold_completion(
    books: list[Book],
    times: list[int],
    start_ms: int,
    end_ms: int,
    side: str,
    target_size: float,
    max_ask_px: float,
) -> tuple[Book | None, float | None]:
    start_idx = bisect.bisect_left(times, start_ms)
    end_idx = bisect.bisect_right(times, end_ms)
    for book in books[start_idx:end_idx]:
        px = side_value(book, side, "ask_px")
        sz = side_value(book, side, "ask_sz")
        if px is None or sz is None:
            continue
        if px <= max_ask_px and sz >= target_size:
            return book, px
    return None, None


def simulate_candidate(
    market: Any,
    books: list[Book],
    times: list[int],
    mode: dict[str, Any],
    cap: float,
    sample_idx: int,
    pair_cost_ceiling: float,
) -> dict[str, Any] | None:
    book = books[sample_idx]
    candidate = candidate_from_book(book, market, mode)
    if candidate is None:
        return None
    clip = float(mode["clip_size"])
    first_px = float(candidate["first_ask"])
    max_opp_ask = pair_cost_ceiling - first_px
    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "pair_cost_ceiling": pair_cost_ceiling,
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        **candidate,
        "clip_size": clip,
        "wait_budget_s": mode["wait_budget_s"],
        "max_opposite_ask_allowed": round(max_opp_ask, 6),
        "first_fill": clip <= cap,
        "first_fill_ts_ms": candidate["candidate_ts_ms"] if clip <= cap else None,
        "first_fill_iso": candidate["candidate_iso"] if clip <= cap else None,
        "first_fill_px": first_px if clip <= cap else None,
        "completion_fill": False,
        "completion_delay_s": None,
        "completion_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row
    if max_opp_ask <= 0:
        row["status"] = "ceiling_below_first_price"
        return row
    end_wait_ms = min(candidate["candidate_ts_ms"] + int(mode["wait_budget_s"]) * 1000, int(market["end_ms"]))
    completion_book, completion_px = first_threshold_completion(
        books,
        times,
        candidate["candidate_ts_ms"],
        end_wait_ms,
        candidate["opposite_side"],
        clip,
        max_opp_ask,
    )
    if completion_book is None or completion_px is None:
        row["status"] = "threshold_not_touched"
        return row
    pair_cost = first_px + completion_px
    row.update(
        {
            "completion_fill": True,
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


def scan_market(
    market: Any,
    books: list[Book],
    modes: list[dict[str, Any]],
    pair_cost_ceilings: list[float],
    sample_interval_ms: int,
) -> list[dict[str, Any]]:
    times = [book.recv_ms for book in books]
    sample_idxs = sample_indices(books, int(market["start_ms"]), sample_interval_ms)
    out = []
    seen: set[tuple[str, float, float]] = set()
    for mode in modes:
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            for ceiling in pair_cost_ceilings:
                key = (mode["name"], float(cap), float(ceiling))
                if key in seen:
                    continue
                for idx in sample_idxs:
                    row = simulate_candidate(market, books, times, mode, float(cap), idx, float(ceiling))
                    if row is None:
                        continue
                    out.append(row)
                    seen.add(key)
                    break
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [row for row in rows if row.get("completion_fill") is True]
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
        "status_counts": dict(sorted({status: sum(1 for row in rows if row.get("status") == status) for status in {row.get("status") for row in rows}}.items())),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_mode_cap_ceiling": {}}
    keys = sorted({(row["mode"], float(row["cap"]), float(row["pair_cost_ceiling"])) for row in rows})
    for mode, cap, ceiling in keys:
        xs = [
            row
            for row in rows
            if row["mode"] == mode and float(row["cap"]) == cap and float(row["pair_cost_ceiling"]) == ceiling
        ]
        out["by_mode_cap_ceiling"][f"{mode}|cap={cap:g}|ceiling={ceiling:g}"] = compact(xs)
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Bounded Taker Threshold Completion",
        "",
        "## Scope",
        "",
        f"- modes_file: `{report['modes_file']}`",
        f"- days: `{report['days']}`",
        f"- pair_cost_ceilings: `{report['parameters']['pair_cost_ceilings']}`",
        f"- override_wait_budget_s: `{report['parameters']['override_wait_budget_s']}`",
        "- Completion is causal: first L1 ask touch at or below pair-cost ceiling.",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "",
        "## Results",
        "",
        "| mode/cap/ceiling | candidates | closed | pair p50 | <0.90 | delay p50 | avg surplus | status |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, item in report["aggregate"]["by_mode_cap_ceiling"].items():
        lines.append(
            f"| {key} | {item['candidate_count']} | {item['closed_rate_among_candidates']} | "
            f"{item['pair_cost']['p50']} | {item['pair_cost_lt_0_90_rate']} | "
            f"{item['completion_delay_s']['p50']} | {item['avg_surplus_at_clip']} | `{item['status_counts']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is stricter than the prior best-in-window scan.",
            "- Low close rate at tight ceiling means the strategy needs repair/abort logic, not just waiting.",
            "- If ceiling `1.00` closes almost all candidates, the market can be flattened, but profitability depends on tighter gates.",
        ]
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
    parser.add_argument("--output-dir", default="data/exports/btc5m_bounded_taker_threshold_20260501")
    parser.add_argument("--pair-cost-ceilings", default="0.90,0.95,1.00")
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--override-wait-budget-s", type=int, default=70)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    modes = load_modes(Path(args.modes_file), args.override_wait_budget_s)
    pair_cost_ceilings = [float(x.strip()) for x in args.pair_cost_ceilings.split(",") if x.strip()]
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
                market_rows = scan_market(
                    market,
                    books,
                    modes,
                    pair_cost_ceilings,
                    int(args.sample_interval_s * 1000),
                )
                for row in market_rows:
                    row["day"] = day
                rows.extend(market_rows)
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_bounded_taker_threshold_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "modes_file": str(Path(args.modes_file).resolve()),
        "days": days,
        "parameters": {
            "pair_cost_ceilings": pair_cost_ceilings,
            "sample_interval_s": args.sample_interval_s,
            "override_wait_budget_s": args.override_wait_budget_s,
        },
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_bounded_taker_threshold_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_bounded_taker_threshold_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_bounded_taker_threshold_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_bounded_taker_threshold_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_bounded_taker_threshold_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

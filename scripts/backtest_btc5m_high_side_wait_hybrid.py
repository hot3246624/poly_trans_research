#!/usr/bin/env python3
"""Hybrid maker-then-bounded-taker shadow for BTC 5m high-side waits.

The script reads replay SQLite in read-only mode and never reads raw capture.

Path:

1. Select a high-side bid candidate from the maker shadow modes.
2. Rest a maker bid for N seconds.
3. If public SELL flow can fill the maker bid, use posted bid as first-leg cost.
4. Otherwise, optionally cross the ask only if the same side is still high-side
   and the ask stays inside the configured bounded price range.
5. Complete the opposite leg by waiting for visible opposite ask depth.

This is still not execution truth. It is a controlled market-side comparison of
maker, taker, and hybrid path assumptions.
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
    best_depth_completion,
    connect_ro,
    day_max_ms,
    first_depth,
    first_price,
    high_side,
    iso_ms,
    load_books,
    load_markets,
    load_modes,
    load_sell_trades,
    other,
    rate,
    sell_fill,
    side_value,
    summarize,
    write_csv,
)


def book_at_or_after(books: list[Book], times: list[int], ts_ms: int, max_wait_ms: int = 1500) -> Book | None:
    idx = bisect.bisect_left(times, ts_ms)
    if idx >= len(books):
        return None
    book = books[idx]
    if book.recv_ms - ts_ms > max_wait_ms:
        return None
    return book


def candidate_from_book(book: Book, market: Any, mode: dict[str, Any]) -> dict[str, Any] | None:
    offset_s = (book.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < mode["offset_start_s"] or offset_s >= mode["offset_end_s"]:
        return None
    side = high_side(book)
    if side is None:
        return None
    bid = first_price(book, side, "bid")
    bid_depth = first_depth(book, side, "bid")
    if bid is None or bid < mode["first_price_min"] or bid >= mode["first_price_max"]:
        return None
    if mode.get("first_depth_required", True) and (bid_depth is None or bid_depth < mode["clip_size"]):
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
        "posted_bid": round(bid, 6),
        "posted_bid_depth": None if bid_depth is None else round(bid_depth, 6),
        "opposite_ask_now": round(opp_ask, 6),
        "opposite_ask_sz_now": side_value(book, opp, "ask_sz"),
    }


def sample_indices(books: list[Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + sample_interval_ms
    return out


def simulate_hybrid(
    market: Any,
    books: list[Book],
    times: list[int],
    sells: dict[str, list[Any]],
    sell_times: dict[str, list[int]],
    mode: dict[str, Any],
    cap: float,
    sample_idx: int,
    maker_wait_s: int,
    queue_ahead_fraction: float,
    fallback_ask_max_extra: float,
    require_same_high_side: bool,
) -> dict[str, Any] | None:
    book = books[sample_idx]
    candidate = candidate_from_book(book, market, mode)
    if candidate is None:
        return None

    clip = float(mode["clip_size"])
    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "maker_wait_s": maker_wait_s,
        "queue_ahead_fraction": queue_ahead_fraction,
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        **candidate,
        "clip_size": clip,
        "wait_budget_s": mode["wait_budget_s"],
        "first_fill": False,
        "first_method": None,
        "first_fill_delay_s": None,
        "first_fill_px": None,
        "first_queue_ahead_size": 0.0,
        "first_flow_required_size": clip,
        "completion_fill": False,
        "completion_delay_s": None,
        "completion_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row

    first_side = candidate["first_side"]
    queue_ahead = max(float(candidate["posted_bid_depth"] or 0.0) * queue_ahead_fraction, 0.0)
    flow_required = clip + queue_ahead
    row["first_queue_ahead_size"] = round(queue_ahead, 6)
    row["first_flow_required_size"] = round(flow_required, 6)
    maker_deadline_ms = min(candidate["candidate_ts_ms"] + maker_wait_s * 1000, int(market["end_ms"]))
    maker_fill = sell_fill(
        sells[first_side],
        sell_times[first_side],
        candidate["candidate_ts_ms"],
        maker_deadline_ms,
        candidate["posted_bid"],
        flow_required,
    )

    if maker_fill is not None:
        row.update(
            {
                "first_fill": True,
                "first_method": "maker_bid",
                "first_fill_ts_ms": maker_fill.ts_ms,
                "first_fill_iso": iso_ms(maker_fill.ts_ms),
                "first_fill_delay_s": round((maker_fill.ts_ms - candidate["candidate_ts_ms"]) / 1000.0, 3),
                "first_fill_px": candidate["posted_bid"],
                "first_fill_proxy_trade_vwap": round(maker_fill.vwap, 6),
                "first_fill_event_count": maker_fill.event_count,
            }
        )
    else:
        fallback_book = book_at_or_after(books, times, maker_deadline_ms)
        if fallback_book is None:
            row["status"] = "no_fallback_book"
            return row
        fallback_side = high_side(fallback_book)
        row["fallback_ts_ms"] = fallback_book.recv_ms
        row["fallback_iso"] = iso_ms(fallback_book.recv_ms)
        row["fallback_high_side"] = fallback_side
        if require_same_high_side and fallback_side != first_side:
            row["status"] = "fallback_side_changed"
            return row
        ask = side_value(fallback_book, first_side, "ask_px")
        ask_sz = side_value(fallback_book, first_side, "ask_sz")
        ask_max = float(mode["first_price_max"]) + fallback_ask_max_extra
        row["fallback_ask"] = ask
        row["fallback_ask_sz"] = ask_sz
        row["fallback_ask_max"] = round(ask_max, 6)
        if ask is None or ask_sz is None or ask_sz < clip:
            row["status"] = "fallback_no_ask_depth"
            return row
        if ask < float(mode["first_price_min"]) or ask >= ask_max:
            row["status"] = "fallback_ask_out_of_bounds"
            return row
        row.update(
            {
                "first_fill": True,
                "first_method": "bounded_taker",
                "first_fill_ts_ms": fallback_book.recv_ms,
                "first_fill_iso": iso_ms(fallback_book.recv_ms),
                "first_fill_delay_s": round((fallback_book.recv_ms - candidate["candidate_ts_ms"]) / 1000.0, 3),
                "first_fill_px": round(ask, 6),
                "first_fill_event_count": 1,
            }
        )

    first_fill_ts = int(row["first_fill_ts_ms"])
    end_wait_ms = min(first_fill_ts + int(mode["wait_budget_s"]) * 1000, int(market["end_ms"]))
    completion_book, completion_px = best_depth_completion(
        books,
        times,
        first_fill_ts,
        end_wait_ms,
        candidate["opposite_side"],
        clip,
    )
    if completion_book is None or completion_px is None:
        row["status"] = "no_opposite_depth_completion"
        return row
    pair_cost = float(row["first_fill_px"]) + completion_px
    row.update(
        {
            "completion_fill": True,
            "completion_ts_ms": completion_book.recv_ms,
            "completion_iso": iso_ms(completion_book.recv_ms),
            "completion_delay_s": round((completion_book.recv_ms - first_fill_ts) / 1000.0, 3),
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
    sells: dict[str, list[Any]],
    modes: list[dict[str, Any]],
    maker_waits_s: list[int],
    queue_ahead_fractions: list[float],
    sample_interval_ms: int,
    fallback_ask_max_extra: float,
    require_same_high_side: bool,
) -> list[dict[str, Any]]:
    times = [book.recv_ms for book in books]
    sell_times = {side: [trade.ts_ms for trade in xs] for side, xs in sells.items()}
    sample_idxs = sample_indices(books, int(market["start_ms"]), sample_interval_ms)
    out = []
    seen: set[tuple[str, float, int, float]] = set()
    for mode in modes:
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            for maker_wait_s in maker_waits_s:
                for queue_ahead_fraction in queue_ahead_fractions:
                    key = (mode["name"], float(cap), maker_wait_s, queue_ahead_fraction)
                    if key in seen:
                        continue
                    for idx in sample_idxs:
                        row = simulate_hybrid(
                            market,
                            books,
                            times,
                            sells,
                            sell_times,
                            mode,
                            float(cap),
                            idx,
                            maker_wait_s,
                            queue_ahead_fraction,
                            fallback_ask_max_extra,
                            require_same_high_side,
                        )
                        if row is None:
                            continue
                        out.append(row)
                        seen.add(key)
                        break
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = [row for row in rows if row.get("first_fill") is True]
    closed = [row for row in rows if row.get("completion_fill") is True]
    maker = [row for row in first if row.get("first_method") == "maker_bid"]
    taker = [row for row in first if row.get("first_method") == "bounded_taker"]
    return {
        "candidate_count": len(rows),
        "first_fill_count": len(first),
        "first_fill_rate": rate(len(first), len(rows)),
        "maker_first_count": len(maker),
        "bounded_taker_first_count": len(taker),
        "maker_first_rate_among_candidates": rate(len(maker), len(rows)),
        "bounded_taker_rate_among_candidates": rate(len(taker), len(rows)),
        "closed_count": len(closed),
        "closed_rate_among_candidates": rate(len(closed), len(rows)),
        "closed_rate_among_first_fills": rate(len(closed), len(first)),
        "first_fill_delay_s": summarize([row.get("first_fill_delay_s") for row in first]),
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
    out = {"all": compact(rows), "by_mode_cap_wait_queue": {}}
    keys = sorted({(row["mode"], float(row["cap"]), int(row["maker_wait_s"]), float(row["queue_ahead_fraction"])) for row in rows})
    for mode, cap, wait, queue in keys:
        xs = [
            row
            for row in rows
            if row["mode"] == mode
            and float(row["cap"]) == cap
            and int(row["maker_wait_s"]) == wait
            and float(row["queue_ahead_fraction"]) == queue
        ]
        out["by_mode_cap_wait_queue"][f"{mode}|cap={cap:g}|maker_wait={wait}|queue={queue:g}"] = compact(xs)
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Hybrid Maker-Then-Bounded-Taker",
        "",
        "## Scope",
        "",
        f"- modes_file: `{report['modes_file']}`",
        f"- days: `{report['days']}`",
        f"- maker_waits_s: `{report['parameters']['maker_waits_s']}`",
        f"- queue_ahead_fractions: `{report['parameters']['queue_ahead_fractions']}`",
        f"- fallback_ask_max_extra: `{report['parameters']['fallback_ask_max_extra']}`",
        f"- require_same_high_side: `{report['parameters']['require_same_high_side']}`",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "- This is market-side proxy, not address-level execution truth.",
        "",
        "## Results",
        "",
        "| mode/cap/wait/queue | candidates | first fill | maker | taker | closed | pair p50 | <0.90 | avg surplus | status |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, item in report["aggregate"]["by_mode_cap_wait_queue"].items():
        lines.append(
            f"| {key} | {item['candidate_count']} | {item['first_fill_rate']} | "
            f"{item['maker_first_rate_among_candidates']} | {item['bounded_taker_rate_among_candidates']} | "
            f"{item['closed_rate_among_candidates']} | {item['pair_cost']['p50']} | "
            f"{item['pair_cost_lt_0_90_rate']} | {item['avg_surplus_at_clip']} | `{item['status_counts']}` |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- A viable hybrid should improve fill rate versus maker-only without paying away most pair-cost edge.",
            "- `fallback_side_changed` is a structural block, not a data failure.",
            "- If bounded taker dominates fills and pair cost remains good, implementation should not remain maker-only.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--modes-file", default="data/exports/xuan_cycle_feature_gate_20260501/high_side_wait_shadow_candidates.json")
    parser.add_argument("--output-dir", default="data/exports/btc5m_high_side_wait_hybrid_20260501")
    parser.add_argument("--maker-waits-s", default="10,20,30")
    parser.add_argument("--queue-ahead-fractions", default="0,0.5")
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--override-wait-budget-s", type=int, default=70)
    parser.add_argument("--fallback-ask-max-extra", type=float, default=0.02)
    parser.add_argument("--allow-side-change", action="store_true")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    modes = load_modes(Path(args.modes_file), args.override_wait_budget_s)
    maker_waits_s = [int(x.strip()) for x in args.maker_waits_s.split(",") if x.strip()]
    queue_ahead_fractions = [float(x.strip()) for x in args.queue_ahead_fractions.split(",") if x.strip()]
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
                sells = load_sell_trades(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                market_rows = scan_market(
                    market,
                    books,
                    sells,
                    modes,
                    maker_waits_s,
                    queue_ahead_fractions,
                    int(args.sample_interval_s * 1000),
                    args.fallback_ask_max_extra,
                    not args.allow_side_change,
                )
                for row in market_rows:
                    row["day"] = day
                rows.extend(market_rows)
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_high_side_wait_hybrid_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "modes_file": str(Path(args.modes_file).resolve()),
        "days": days,
        "parameters": {
            "maker_waits_s": maker_waits_s,
            "queue_ahead_fractions": queue_ahead_fractions,
            "sample_interval_s": args.sample_interval_s,
            "override_wait_budget_s": args.override_wait_budget_s,
            "fallback_ask_max_extra": args.fallback_ask_max_extra,
            "require_same_high_side": not args.allow_side_change,
        },
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_high_side_wait_hybrid_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_high_side_wait_hybrid_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_high_side_wait_hybrid_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_high_side_wait_hybrid_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_high_side_wait_hybrid_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

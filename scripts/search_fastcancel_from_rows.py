#!/usr/bin/env python3
"""Search fast-cancel filters from precomputed maker-fill candidate rows.

This script is intentionally offline: it consumes CSV rows produced by
backtest_btc5m_maker_fill_triggered.py --emit-all-candidates, applies many
filter combinations, then runs the same active-tranche state machine on each
candidate subset. It does not read raw capture data or replay SQLite.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def as_float(row: dict[str, str], key: str) -> float | None:
    value = row.get(key, "")
    return None if value == "" else float(value)


def as_int(row: dict[str, str], key: str) -> int | None:
    value = row.get(key, "")
    return None if value == "" else int(float(value))


def load_rows(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            for raw in csv.DictReader(f):
                row: dict[str, Any] = dict(raw)
                row["first_fill"] = raw.get("first_fill") == "True"
                row["slow_continue_eligible"] = raw.get("slow_continue_eligible") == "True"
                for key in ["candidate_ts_ms", "fill_ts_ms", "completion_ts_ms"]:
                    row[key] = as_int(raw, key)
                for key in [
                    "offset_s",
                    "prev_bid_delta_1s",
                    "side_bid",
                    "side_ask",
                    "spread_ticks",
                    "opp_spread_ticks",
                    "opp_ask_sz",
                    "immediate_pair_cost",
                    "top_bid_sz",
                    "queue_same",
                    "required_size",
                    "clip",
                    "first_price",
                    "second_price",
                    "pair_cost",
                    "min_pair_cost_seen_30s",
                    "pnl",
                ]:
                    row[key] = as_float(raw, key)
                rows.append(row)
    return rows


def parse_float_list(value: str, allow_none: bool = False) -> list[float | None]:
    out: list[float | None] = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        if allow_none and text.lower() in {"none", "null", "na"}:
            out.append(None)
        else:
            out.append(float(text))
    return out


def parse_windows(value: str) -> list[tuple[float, float]]:
    out = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        start, end = text.split("-")
        out.append((float(start), float(end)))
    return out


def row_matches(row: dict[str, Any], params: dict[str, Any]) -> bool:
    offset = row["offset_s"]
    if offset is None or offset < params["offset_start"] or offset >= params["offset_end"]:
        return False
    if row["prev_bid_delta_1s"] is None or row["prev_bid_delta_1s"] < params["min_delta"]:
        return False
    if row["side_bid"] is None or row["side_bid"] < params["min_bid"] or row["side_bid"] >= params["max_bid"]:
        return False
    if row["spread_ticks"] is None or row["spread_ticks"] > params["max_spread"]:
        return False
    if params["max_opp_spread"] is not None and (row["opp_spread_ticks"] is None or row["opp_spread_ticks"] > params["max_opp_spread"]):
        return False
    if params["max_top_bid_sz"] is not None and row["top_bid_sz"] is not None and row["top_bid_sz"] > params["max_top_bid_sz"]:
        return False
    if params["max_immediate_pair_cost"] is not None and (
        row["immediate_pair_cost"] is None or row["immediate_pair_cost"] > params["max_immediate_pair_cost"]
    ):
        return False
    return True


def select_state_machine(
    rows: list[dict[str, Any]],
    params: dict[str, Any],
    no_fill_block_s: int,
    cooldown_s: int,
    non_clean_exit_delay_s: int | None,
) -> list[dict[str, Any]]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row_matches(row, params):
            by_market[str(row["condition_id"])].append(row)

    selected: list[dict[str, Any]] = []
    for market_rows in by_market.values():
        blocked_until = 0
        active_until = 0
        for row in sorted(market_rows, key=lambda r: int(r["candidate_ts_ms"])):
            ts = int(row["candidate_ts_ms"])
            if ts < blocked_until or ts < active_until:
                continue
            selected.append(row)
            if not row["first_fill"]:
                blocked_until = ts + no_fill_block_s * 1000
                continue
            if row["completion_ts_ms"] is not None:
                active_until = int(row["completion_ts_ms"])
                blocked_until = max(blocked_until, active_until + cooldown_s * 1000)
            elif row["fill_ts_ms"] is not None and non_clean_exit_delay_s is not None:
                active_until = int(row["fill_ts_ms"]) + non_clean_exit_delay_s * 1000
                blocked_until = max(blocked_until, active_until + cooldown_s * 1000)
            else:
                active_until = 10**18
                blocked_until = 10**18
    return selected


def summarize_selected(selected: list[dict[str, Any]]) -> dict[str, Any]:
    fills = [row for row in selected if row["first_fill"]]
    paths = Counter(str(row["path"]) for row in selected)
    daily: dict[str, float] = defaultdict(float)
    daily_attempts: dict[str, int] = defaultdict(int)
    daily_fills: dict[str, int] = defaultdict(int)
    spend = 0.0
    for row in selected:
        day = str(row["day"])
        daily_attempts[day] += 1
        if row["first_fill"]:
            daily_fills[day] += 1
            daily[day] += float(row["pnl"] or 0.0)
            first_price = row["first_price"] if row["first_price"] is not None else row["order_price"]
            second_price = row["second_price"]
            clip = float(row["clip"] or 0.0)
            spend += float(first_price or 0.0) * clip
            if second_price is not None:
                spend += float(second_price) * clip
    pnl = sum(daily.values())
    positive_days = sum(1 for value in daily.values() if value > 0)
    completed = [row for row in fills if row["path"] in {"completion", "slow_completion", "repair"}]
    residual = [row for row in fills if row["path"] == "residual_settle"]
    pair_costs = [float(row["pair_cost"]) for row in completed if row["pair_cost"] is not None]
    pair_cost_p50 = None
    if pair_costs:
        xs = sorted(pair_costs)
        pair_cost_p50 = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    return {
        "attempts": len(selected),
        "fills": len(fills),
        "fill_rate": round(len(fills) / len(selected), 6) if selected else None,
        "completed": len(completed),
        "residual": len(residual),
        "paths": dict(paths),
        "pnl": round(pnl, 6),
        "spend_est": round(spend, 6),
        "roi_est": round(pnl / spend, 6) if spend else None,
        "positive_days": f"{positive_days}/{len(daily)}" if daily else "0/0",
        "min_daily_pnl": round(min(daily.values()), 6) if daily else None,
        "daily_pnl": {day: round(daily[day], 6) for day in sorted(daily)},
        "daily_fills": {day: daily_fills[day] for day in sorted(daily_attempts)},
        "daily_attempts": {day: daily_attempts[day] for day in sorted(daily_attempts)},
        "completion_rate": round(len(completed) / len(fills), 6) if fills else None,
        "residual_rate": round(len(residual) / len(fills), 6) if fills else None,
        "pair_cost_p50": None if pair_cost_p50 is None else round(pair_cost_p50, 6),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True, help="Comma-separated candidate row CSV paths.")
    parser.add_argument("--output-dir", default="data/exports/fastcancel_param_search")
    parser.add_argument("--windows", default="0-15,5-20,10-20,10-30,10-40,20-40,30-60,40-60,60-90,60-120")
    parser.add_argument("--min-deltas", default="0.02,0.03,0.04,0.05,0.06")
    parser.add_argument("--min-bids", default="0.35,0.40")
    parser.add_argument("--max-bids", default="0.45,0.50,0.55")
    parser.add_argument("--max-spreads", default="1,2")
    parser.add_argument("--max-opp-spreads", default="1,2,None")
    parser.add_argument("--max-top-bid-szs", default="100,150,250,400,None")
    parser.add_argument("--max-immediate-pair-costs", default="0.98,1.0,1.02,None")
    parser.add_argument("--no-fill-block-s", type=int, default=15)
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--non-clean-exit-delay-s", type=int, default=-1, help="-1 keeps conservative no-reentry after residual; otherwise seconds to force-flat.")
    parser.add_argument("--top-n", type=int, default=100)
    args = parser.parse_args()

    paths = [Path(item.strip()) for item in args.rows.split(",") if item.strip()]
    rows = load_rows(paths)
    windows = parse_windows(args.windows)
    min_deltas = [float(v) for v in args.min_deltas.split(",") if v.strip()]
    min_bids = [float(v) for v in args.min_bids.split(",") if v.strip()]
    max_bids = [float(v) for v in args.max_bids.split(",") if v.strip()]
    max_spreads = [float(v) for v in args.max_spreads.split(",") if v.strip()]
    max_opp_spreads = parse_float_list(args.max_opp_spreads, allow_none=True)
    max_top_bid_szs = parse_float_list(args.max_top_bid_szs, allow_none=True)
    max_immediate_pair_costs = parse_float_list(args.max_immediate_pair_costs, allow_none=True)
    non_clean_exit_delay_s = None if args.non_clean_exit_delay_s < 0 else args.non_clean_exit_delay_s

    results = []
    for offset_start, offset_end in windows:
        for min_delta in min_deltas:
            for min_bid in min_bids:
                for max_bid in max_bids:
                    if min_bid >= max_bid:
                        continue
                    for max_spread in max_spreads:
                        for max_opp_spread in max_opp_spreads:
                            for max_top_bid_sz in max_top_bid_szs:
                                for max_immediate_pair_cost in max_immediate_pair_costs:
                                    params = {
                                        "offset_start": offset_start,
                                        "offset_end": offset_end,
                                        "min_delta": min_delta,
                                        "min_bid": min_bid,
                                        "max_bid": max_bid,
                                        "max_spread": max_spread,
                                        "max_opp_spread": max_opp_spread,
                                        "max_top_bid_sz": max_top_bid_sz,
                                        "max_immediate_pair_cost": max_immediate_pair_cost,
                                    }
                                    selected = select_state_machine(
                                        rows,
                                        params,
                                        args.no_fill_block_s,
                                        args.cooldown_s,
                                        non_clean_exit_delay_s,
                                    )
                                    summary = summarize_selected(selected)
                                    if summary["fills"] < 5:
                                        continue
                                    result = {**params, **summary}
                                    result["score"] = round(
                                        float(summary["pnl"]) + 5.0 * float(summary["min_daily_pnl"] or 0.0) - 10.0 * summary["residual"],
                                        6,
                                    )
                                    results.append(result)

    results.sort(
        key=lambda item: (
            item["positive_days"],
            item["score"],
            item["pnl"],
            item["fills"],
        ),
        reverse=True,
    )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    top = results[: args.top_n]
    (output_dir / "fastcancel_param_search_summary.json").write_text(
        json.dumps(
            {
                "input_rows": [str(path) for path in paths],
                "row_count": len(rows),
                "result_count": len(results),
                "top": top,
                "parameters": vars(args),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    write_csv(output_dir / "fastcancel_param_search_top.csv", top)
    print(json.dumps({"output_dir": str(output_dir), "row_count": len(rows), "result_count": len(results), "top_pnl": top[0]["pnl"] if top else None}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

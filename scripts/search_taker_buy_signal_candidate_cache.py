#!/usr/bin/env python3
"""Search gates over the taker-BUY candidate cache."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_ranges(value: str) -> list[tuple[float, float]]:
    out = []
    for item in value.split(","):
        text = item.strip()
        if not text:
            continue
        lo, hi = text.split("-")
        out.append((float(lo), float(hi)))
    return out


def parse_floats(value: str) -> list[float]:
    return [float(item.strip()) for item in value.split(",") if item.strip()]


def load_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row: dict[str, Any] = dict(raw)
            row["trigger_ts_ms"] = int(float(row["trigger_ts_ms"]))
            row["offset_s"] = parse_float(row.get("offset_s"))
            row["public_trade_price"] = parse_float(row.get("public_trade_price"))
            row["public_trade_size"] = parse_float(row.get("public_trade_size"))
            row["clip"] = parse_float(row.get("clip")) or 0.0
            row["first_l2_vwap"] = parse_float(row.get("first_l2_vwap"))
            row["l1_immediate_pair"] = parse_float(row.get("l1_immediate_pair"))
            row["min_pair_cost_30s"] = parse_float(row.get("min_pair_cost_30s"))
            row["first_is_winner"] = parse_bool(row.get("first_is_winner"))
            for ceiling in ("0_94", "0_95", "0_96", "0_98"):
                row[f"ceil_{ceiling}_hit"] = parse_bool(row.get(f"ceil_{ceiling}_hit"))
                row[f"ceil_{ceiling}_pair_cost"] = parse_float(row.get(f"ceil_{ceiling}_pair_cost"))
                row[f"ceil_{ceiling}_delay_s"] = parse_float(row.get(f"ceil_{ceiling}_delay_s"))
            rows.append(row)
    return rows


def matches(row: dict[str, Any], params: dict[str, Any]) -> bool:
    if params["side_alignment"] != "any" and row.get("side_alignment") != params["side_alignment"]:
        return False
    price = row["public_trade_price"]
    if price is None or price < params["price_lo"] or price >= params["price_hi"]:
        return False
    size = row["public_trade_size"]
    if size is None or size < params["size_lo"] or size >= params["size_hi"]:
        return False
    first = row["first_l2_vwap"]
    if first is None or first < params["first_lo"] or first >= params["first_hi"]:
        return False
    l1_pair = row["l1_immediate_pair"]
    if l1_pair is None or l1_pair > params["max_l1_pair"]:
        return False
    offset = row["offset_s"]
    if offset is None or offset < params["offset_lo"] or offset >= params["offset_hi"]:
        return False
    return True


def ceiling_key(ceiling: float) -> str:
    return f"ceil_{str(ceiling).replace('.', '_')}"


def row_pnl(row: dict[str, Any], ceiling: float) -> tuple[float, bool, float | None, float | None]:
    key = ceiling_key(ceiling)
    pair_cost = row.get(f"{key}_pair_cost")
    if row.get(f"{key}_hit") and pair_cost is not None:
        return (1.0 - float(pair_cost)) * row["clip"], True, float(pair_cost), row.get(f"{key}_delay_s")
    first = row["first_l2_vwap"] or 0.0
    return ((1.0 - first) if row["first_is_winner"] else -first) * row["clip"], False, None, None


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
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def simulate(rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    by_market: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if matches(row, params):
            by_market[str(row["condition_id"])].append(row)
    selected = []
    for market_rows in by_market.values():
        active_until = 0
        for row in sorted(market_rows, key=lambda r: int(r["trigger_ts_ms"])):
            if row["trigger_ts_ms"] < active_until:
                continue
            pnl, closed, pair_cost, delay_s = row_pnl(row, params["pair_ceiling"])
            selected_row = {
                **row,
                "_pnl": pnl,
                "_closed": closed,
                "_pair_cost": pair_cost,
                "_delay_s": delay_s,
            }
            selected.append(selected_row)
            if closed:
                active_until = row["trigger_ts_ms"] + int((delay_s or 0) * 1000) + params["cooldown_s"] * 1000
            elif params["block_after_residual"]:
                active_until = 10**18
            else:
                active_until = row["trigger_ts_ms"] + params["cooldown_s"] * 1000
    pnl = sum(row["_pnl"] for row in selected)
    cost = sum((row["first_l2_vwap"] or 0.0) * row["clip"] for row in selected)
    closed_rows = [row for row in selected if row["_closed"]]
    residual_rows = [row for row in selected if not row["_closed"]]
    by_day: dict[str, float] = defaultdict(float)
    by_day_rows: dict[str, int] = defaultdict(int)
    for row in selected:
        by_day[str(row["day"])] += row["_pnl"]
        by_day_rows[str(row["day"])] += 1
    negative_days = [day for day, value in sorted(by_day.items()) if value < 0]
    return {
        **params,
        "rows": len(selected),
        "closed": len(closed_rows),
        "closed_rate": round(len(closed_rows) / len(selected), 6) if selected else None,
        "first_winner_rate": round(sum(1 for row in selected if row["first_is_winner"]) / len(selected), 6) if selected else None,
        "residual": len(residual_rows),
        "residual_winner_rate": round(sum(1 for row in residual_rows if row["first_is_winner"]) / len(residual_rows), 6) if residual_rows else None,
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / cost, 6) if cost else None,
        "pair_cost_p50": percentile([float(row["_pair_cost"]) for row in closed_rows if row["_pair_cost"] is not None], 50),
        "delay_p50_s": percentile([float(row["_delay_s"]) for row in closed_rows if row["_delay_s"] is not None], 50),
        "negative_days": negative_days,
        "positive_day_count": len(by_day) - len(negative_days),
        "day_count": len(by_day),
        "min_day_pnl": round(min(by_day.values()), 6) if by_day else None,
        "by_day_pnl": {day: round(by_day[day], 6) for day in sorted(by_day)},
        "by_day_rows": {day: by_day_rows[day] for day in sorted(by_day_rows)},
    }


def prefilter_rows(
    rows: list[dict[str, Any]],
    price_ranges: list[tuple[float, float]],
    size_ranges: list[tuple[float, float]],
    first_ranges: list[tuple[float, float]],
    offset_ranges: list[tuple[float, float]],
    max_l1_pairs: list[float],
    side_alignments: list[str],
) -> list[dict[str, Any]]:
    price_lo = min(lo for lo, _hi in price_ranges)
    price_hi = max(hi for _lo, hi in price_ranges)
    size_lo = min(lo for lo, _hi in size_ranges)
    size_hi = max(hi for _lo, hi in size_ranges)
    first_lo = min(lo for lo, _hi in first_ranges)
    first_hi = max(hi for _lo, hi in first_ranges)
    offset_lo = min(lo for lo, _hi in offset_ranges)
    offset_hi = max(hi for _lo, hi in offset_ranges)
    max_l1 = max(max_l1_pairs)
    side_set = set(side_alignments)
    out = []
    for row in rows:
        if "any" not in side_set and row.get("side_alignment") not in side_set:
            continue
        price = row["public_trade_price"]
        if price is None or price < price_lo or price >= price_hi:
            continue
        size = row["public_trade_size"]
        if size is None or size < size_lo or size >= size_hi:
            continue
        first = row["first_l2_vwap"]
        if first is None or first < first_lo or first >= first_hi:
            continue
        offset = row["offset_s"]
        if offset is None or offset < offset_lo or offset >= offset_hi:
            continue
        l1_pair = row["l1_immediate_pair"]
        if l1_pair is None or l1_pair > max_l1:
            continue
        out.append(row)
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(results: list[dict[str, Any]], top_n: int) -> str:
    lines = [
        "# Taker BUY Candidate Cache Search",
        "",
        "## Top Stable Results",
        "",
        "| rank | rows | pnl | ROI | min day pnl | closed | first winner | residual winner | price | size | l1 | first vwap | ceiling | block residual |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|---|---:|---|",
    ]
    for idx, row in enumerate(results[:top_n], start=1):
        lines.append(
            f"| {idx} | {row['rows']} | {row['pnl']} | {row['roi_on_first_cost']} | {row['min_day_pnl']} | "
            f"{row['closed_rate']} | {row['first_winner_rate']} | {row['residual_winner_rate']} | "
            f"{row['price_lo']}-{row['price_hi']} | {row['size_lo']}-{row['size_hi']} | {row['max_l1_pair']} | "
            f"{row['first_lo']}-{row['first_hi']} | {row['pair_ceiling']} | {row['block_after_residual']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_signal_candidate_search"))
    parser.add_argument("--price-ranges", default="0.50-0.55,0.55-0.60,0.60-0.65,0.65-0.70,0.55-0.70")
    parser.add_argument("--size-ranges", default="50-100,100-150,100-200,120-150,150-200")
    parser.add_argument("--first-ranges", default="0.50-0.75,0.55-0.75,0.60-0.75,0.55-0.70,0.60-0.70")
    parser.add_argument("--offset-ranges", default="0-240,0-60,0-120,30-180,60-240")
    parser.add_argument("--max-l1-pairs", default="0.98,0.985,0.99,0.995,1.0")
    parser.add_argument("--pair-ceilings", default="0.94,0.95,0.96,0.98")
    parser.add_argument("--side-alignments", default="high")
    parser.add_argument("--cooldown-s", type=int, default=10)
    parser.add_argument("--min-rows", type=int, default=40)
    parser.add_argument("--top-n", type=int, default=40)
    args = parser.parse_args()

    all_rows = load_rows(args.cache_csv)
    price_ranges = parse_ranges(args.price_ranges)
    size_ranges = parse_ranges(args.size_ranges)
    first_ranges = parse_ranges(args.first_ranges)
    offset_ranges = parse_ranges(args.offset_ranges)
    max_l1_pairs = parse_floats(args.max_l1_pairs)
    pair_ceilings = parse_floats(args.pair_ceilings)
    side_alignments = [x.strip() for x in args.side_alignments.split(",") if x.strip()]
    rows = prefilter_rows(all_rows, price_ranges, size_ranges, first_ranges, offset_ranges, max_l1_pairs, side_alignments)
    results = []
    for price_lo, price_hi in price_ranges:
        for size_lo, size_hi in size_ranges:
            for first_lo, first_hi in first_ranges:
                for offset_lo, offset_hi in offset_ranges:
                    for max_l1_pair in max_l1_pairs:
                        for pair_ceiling in pair_ceilings:
                            for side_alignment in side_alignments:
                                for block_after_residual in (True, False):
                                    params = {
                                        "price_lo": price_lo,
                                        "price_hi": price_hi,
                                        "size_lo": size_lo,
                                        "size_hi": size_hi,
                                        "first_lo": first_lo,
                                        "first_hi": first_hi,
                                        "offset_lo": offset_lo,
                                        "offset_hi": offset_hi,
                                        "max_l1_pair": max_l1_pair,
                                        "pair_ceiling": pair_ceiling,
                                        "side_alignment": side_alignment,
                                        "block_after_residual": block_after_residual,
                                        "cooldown_s": args.cooldown_s,
                                    }
                                    result = simulate(rows, params)
                                    if result["rows"] >= args.min_rows and not result["negative_days"]:
                                        results.append(result)
    results.sort(
        key=lambda r: (
            float(r.get("pnl") or -10**9),
            float(r.get("min_day_pnl") or -10**9),
            float(r.get("roi_on_first_cost") or -10**9),
        ),
        reverse=True,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "taker_buy_signal_candidate_search_results.csv", results)
    summary = {
        "cache_csv": str(args.cache_csv),
        "candidate_rows": len(all_rows),
        "prefiltered_rows": len(rows),
        "result_count": len(results),
        "top": results[: args.top_n],
    }
    (args.output_dir / "taker_buy_signal_candidate_search_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_signal_candidate_search_report.md").write_text(
        render_report(results, args.top_n),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "candidate_rows": len(all_rows), "prefiltered_rows": len(rows), "result_count": len(results)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

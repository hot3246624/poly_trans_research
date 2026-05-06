#!/usr/bin/env python3
"""Export selected policy rows from a taker-BUY candidate cache."""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
from pathlib import Path
from typing import Any


def load_search_module(path: Path):
    spec = importlib.util.spec_from_file_location("search_taker_buy_signal_candidate_cache", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ceiling_key(ceiling: float) -> str:
    return f"ceil_{str(ceiling).replace('.', '_')}"


def row_pnl(row: dict[str, Any], ceiling: float) -> tuple[float, bool, float | None, float | None, float | None]:
    key = ceiling_key(ceiling)
    pair_cost = row.get(f"{key}_pair_cost")
    if row.get(f"{key}_hit") and pair_cost is not None:
        return (1.0 - float(pair_cost)) * row["clip"], True, float(pair_cost), row.get(f"{key}_delay_s"), row.get(f"{key}_vwap")
    first = row["first_l2_vwap"] or 0.0
    return ((1.0 - first) if row["first_is_winner"] else -first) * row["clip"], False, None, None, None


def params_from_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "price_lo": args.price_lo,
        "price_hi": args.price_hi,
        "size_lo": args.size_lo,
        "size_hi": args.size_hi,
        "first_lo": args.first_lo,
        "first_hi": args.first_hi,
        "offset_lo": args.offset_lo,
        "offset_hi": args.offset_hi,
        "max_l1_pair": args.max_l1_pair,
        "pair_ceiling": args.pair_ceiling,
        "side_alignment": args.side_alignment,
        "block_after_residual": args.block_after_residual,
        "cooldown_s": args.cooldown_s,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-csv", type=Path, required=True)
    parser.add_argument("--search-script", type=Path, default=Path("scripts/search_taker_buy_signal_candidate_cache.py"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--price-lo", type=float, required=True)
    parser.add_argument("--price-hi", type=float, required=True)
    parser.add_argument("--size-lo", type=float, required=True)
    parser.add_argument("--size-hi", type=float, required=True)
    parser.add_argument("--first-lo", type=float, required=True)
    parser.add_argument("--first-hi", type=float, required=True)
    parser.add_argument("--offset-lo", type=float, default=0.0)
    parser.add_argument("--offset-hi", type=float, default=240.0)
    parser.add_argument("--max-l1-pair", type=float, required=True)
    parser.add_argument("--pair-ceiling", type=float, required=True)
    parser.add_argument("--side-alignment", default="high")
    parser.add_argument("--block-after-residual", action="store_true")
    parser.add_argument("--cooldown-s", type=int, default=10)
    args = parser.parse_args()

    search_mod = load_search_module(args.search_script)
    rows = search_mod.load_rows(args.cache_csv)
    params = params_from_args(args)
    selected = []
    by_market: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if search_mod.matches(row, params):
            by_market.setdefault(str(row["condition_id"]), []).append(row)
    for market_rows in by_market.values():
        active_until = 0
        for row in sorted(market_rows, key=lambda r: int(r["trigger_ts_ms"])):
            if row["trigger_ts_ms"] < active_until:
                continue
            pnl, closed, pair_cost, delay_s, completion_vwap = row_pnl(row, args.pair_ceiling)
            out = {
                "day": row["day"],
                "slug": row["slug"],
                "condition_id": row["condition_id"],
                "winner_side": row["winner_side"],
                "trigger_ts_ms": row["trigger_ts_ms"],
                "trigger_iso": row["trigger_iso"],
                "offset_s": row["offset_s"],
                "first_side": row["first_side"],
                "side_alignment": row["side_alignment"],
                "first_is_winner": str(bool(row["first_is_winner"])),
                "trigger_price": row["first_l2_vwap"],
                "public_trade_price": row["public_trade_price"],
                "trigger_size": row["public_trade_size"],
                "first_price_source": "candidate_cache_l2",
                "first_price_age_ms": row["first_l2_age_ms"],
                "first_worst_px": row["first_l2_worst_px"],
                "clip": row["clip"],
                "l1_immediate_pair": row["l1_immediate_pair"],
                "completion_fill": str(bool(closed)),
                "status": "closed" if closed else "residual_settle",
                "completion_delay_s": delay_s,
                "completion_vwap": completion_vwap,
                "pair_cost": pair_cost,
                "pnl": round(pnl, 6),
            }
            selected.append(out)
            if closed:
                active_until = row["trigger_ts_ms"] + int((delay_s or 0) * 1000) + args.cooldown_s * 1000
            elif args.block_after_residual:
                active_until = 10**18
            else:
                active_until = row["trigger_ts_ms"] + args.cooldown_s * 1000

    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows_path = args.output_dir / "taker_buy_candidate_policy_rows.csv"
    fields = list(selected[0].keys()) if selected else []
    with rows_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        if fields:
            writer.writeheader()
            writer.writerows(selected)
    summary = {
        "params": params,
        "rows": len(selected),
        "rows_csv": str(rows_path),
        "pnl": round(sum(float(row["pnl"]) for row in selected), 6),
    }
    (args.output_dir / "taker_buy_candidate_policy_rows_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

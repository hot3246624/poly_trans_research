#!/usr/bin/env python3
"""Search gates over the V2 taker-BUY DuckDB/Parquet cache."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import search_taker_buy_signal_candidate_cache as v1  # noqa: E402


def normalize_row(raw: dict[str, Any]) -> dict[str, Any]:
    row = dict(raw)
    if row.get("day") is not None:
        row["day"] = str(row["day"])[:10]
    row["trigger_ts_ms"] = int(float(row["trigger_ts_ms"]))
    row["offset_s"] = v1.parse_float(row.get("offset_s"))
    row["public_trade_price"] = v1.parse_float(row.get("public_trade_price"))
    row["public_trade_size"] = v1.parse_float(row.get("public_trade_size"))
    row["clip"] = v1.parse_float(row.get("clip")) or 0.0
    row["first_l2_vwap"] = v1.parse_float(row.get("first_l2_vwap"))
    row["l1_immediate_pair"] = v1.parse_float(row.get("l1_immediate_pair"))
    row["min_pair_cost_30s"] = v1.parse_float(row.get("min_pair_cost_30s"))
    row["first_is_winner"] = v1.parse_bool(row.get("first_is_winner"))
    for ceiling in ("0_94", "0_95", "0_96", "0_98"):
        row[f"ceil_{ceiling}_hit"] = v1.parse_bool(row.get(f"ceil_{ceiling}_hit"))
        row[f"ceil_{ceiling}_pair_cost"] = v1.parse_float(row.get(f"ceil_{ceiling}_pair_cost"))
        row[f"ceil_{ceiling}_delay_s"] = v1.parse_float(row.get(f"ceil_{ceiling}_delay_s"))
    return row


def load_rows(cache_dir: Path, cache_duckdb: Path | None) -> list[dict[str, Any]]:
    db_path = cache_duckdb or cache_dir / "cache.duckdb"
    if db_path.is_file():
        conn = duckdb.connect(str(db_path), read_only=True)
        rows = conn.execute("SELECT * FROM taker_buy_signal_candidates").fetchall()
        fields = [desc[0] for desc in conn.description]
        conn.close()
        return [normalize_row(dict(zip(fields, row))) for row in rows]
    parquet_glob = cache_dir / "dataset" / "**" / "*.parquet"
    conn = duckdb.connect()
    rows = conn.execute(
        f"SELECT * FROM read_parquet('{str(parquet_glob).replace(chr(39), chr(39) + chr(39))}', hive_partitioning=true)"
    ).fetchall()
    fields = [desc[0] for desc in conn.description]
    conn.close()
    return [normalize_row(dict(zip(fields, row))) for row in rows]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--cache-duckdb", type=Path, default=None)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_signal_candidate_search_v2"))
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

    all_rows = load_rows(args.cache_dir, args.cache_duckdb)
    price_ranges = v1.parse_ranges(args.price_ranges)
    size_ranges = v1.parse_ranges(args.size_ranges)
    first_ranges = v1.parse_ranges(args.first_ranges)
    offset_ranges = v1.parse_ranges(args.offset_ranges)
    max_l1_pairs = v1.parse_floats(args.max_l1_pairs)
    pair_ceilings = v1.parse_floats(args.pair_ceilings)
    side_alignments = [x.strip() for x in args.side_alignments.split(",") if x.strip()]
    rows = v1.prefilter_rows(all_rows, price_ranges, size_ranges, first_ranges, offset_ranges, max_l1_pairs, side_alignments)
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
                                    result = v1.simulate(rows, params)
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
    v1.write_csv(args.output_dir / "taker_buy_signal_candidate_search_results.csv", results)
    summary = {
        "cache_dir": str(args.cache_dir),
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
        v1.render_report(results, args.top_n),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "output_dir": str(args.output_dir),
                "candidate_rows": len(all_rows),
                "prefiltered_rows": len(rows),
                "result_count": len(results),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

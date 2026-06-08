#!/usr/bin/env python3
"""Benchmark SQLite strict verifier vs Parquet/DuckDB verification-store path."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_timed(name: str, cmd: list[str]) -> dict[str, Any]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    elapsed = time.perf_counter() - started
    return {
        "name": name,
        "cmd": cmd,
        "returncode": proc.returncode,
        "elapsed_s": round(elapsed, 3),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def comparable_aggregate(report: dict[str, Any]) -> dict[str, Any]:
    agg = report["aggregate"]
    return {
        "all": agg.get("all"),
        "by_day": agg.get("by_day"),
    }


def close_enough(a: Any, b: Any, tol: float) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(close_enough(a[k], b[k], tol) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(close_enough(x, y, tol) for x, y in zip(a, b))
    if isinstance(a, (int, float)) or isinstance(b, (int, float)):
        if a is None or b is None:
            return a is None and b is None
        return abs(float(a) - float(b)) <= tol
    return a == b


def compare_rank(sqlite_dir: Path, duckdb_dir: Path, rank: int, tol: float) -> dict[str, Any]:
    sqlite_report = load_json(sqlite_dir / f"finalist_{rank:02d}" / "taker_buy_strict_raw_replay_summary.json")
    duckdb_report = load_json(duckdb_dir / f"finalist_{rank:02d}" / "taker_buy_duckdb_verification_summary.json")
    left = comparable_aggregate(sqlite_report)
    right = comparable_aggregate(duckdb_report)
    return {
        "rank": rank,
        "match": close_enough(left, right, tol),
        "sqlite_all": left["all"],
        "duckdb_all": right["all"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--candidate-cache-csv", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--verification-store-dir", type=Path, required=True)
    parser.add_argument("--days", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--rank", type=int, action="append", default=None)
    parser.add_argument("--top-n", type=int, default=1)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--max-l1-age-ms", type=int, default=3000)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--duckdb-threads", type=int, default=4)
    parser.add_argument("--float-tol", type=float, default=1e-6)
    parser.add_argument("--skip-sqlite", action="store_true")
    parser.add_argument("--skip-duckdb", action="store_true")
    parser.add_argument("--progress", action="store_true")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    sqlite_dir = args.output_dir / "sqlite"
    duckdb_dir = args.output_dir / "duckdb"
    ranks = args.rank or list(range(1, args.top_n + 1))

    common_rank_args: list[str] = []
    if args.rank:
        for rank in args.rank:
            common_rank_args.extend(["--rank", str(rank)])
    else:
        common_rank_args.extend(["--top-n", str(args.top_n)])
    progress_args = ["--progress"] if args.progress else []

    runs = []
    if not args.skip_sqlite:
        runs.append(
            run_timed(
                "sqlite_strict",
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify_taker_buy_search_finalists_strict.py")),
                    "--search-results-csv",
                    str(args.search_results_csv),
                    "--candidate-cache-csv",
                    str(args.candidate_cache_csv),
                    "--replay-root",
                    str(args.replay_root),
                    "--days",
                    args.days,
                    "--output-dir",
                    str(sqlite_dir),
                    "--clip",
                    str(args.clip),
                    "--max-l1-age-ms",
                    str(args.max_l1_age_ms),
                    "--max-l2-age-ms",
                    str(args.max_l2_age_ms),
                    "--completion-s",
                    str(args.completion_s),
                    *common_rank_args,
                    *progress_args,
                ],
            )
        )
    if not args.skip_duckdb:
        runs.append(
            run_timed(
                "duckdb_store",
                [
                    sys.executable,
                    str(Path(__file__).with_name("verify_taker_buy_search_finalists_duckdb.py")),
                    "--search-results-csv",
                    str(args.search_results_csv),
                    "--candidate-cache-csv",
                    str(args.candidate_cache_csv),
                    "--verification-store-dir",
                    str(args.verification_store_dir),
                    "--days",
                    args.days,
                    "--output-dir",
                    str(duckdb_dir),
                    "--clip",
                    str(args.clip),
                    "--max-l1-age-ms",
                    str(args.max_l1_age_ms),
                    "--max-l2-age-ms",
                    str(args.max_l2_age_ms),
                    "--completion-s",
                    str(args.completion_s),
                    "--duckdb-threads",
                    str(args.duckdb_threads),
                    *common_rank_args,
                    *progress_args,
                ],
            )
        )

    comparisons = []
    if not args.skip_sqlite and not args.skip_duckdb and all(run["returncode"] == 0 for run in runs):
        comparisons = [compare_rank(sqlite_dir, duckdb_dir, rank, args.float_tol) for rank in ranks]

    speedup = None
    sqlite_run = next((run for run in runs if run["name"] == "sqlite_strict" and run["returncode"] == 0), None)
    duckdb_run = next((run for run in runs if run["name"] == "duckdb_store" and run["returncode"] == 0), None)
    if sqlite_run and duckdb_run and duckdb_run["elapsed_s"] > 0:
        speedup = round(sqlite_run["elapsed_s"] / duckdb_run["elapsed_s"], 3)

    summary = {
        "runs": runs,
        "comparisons": comparisons,
        "all_comparisons_match": bool(comparisons) and all(item["match"] for item in comparisons),
        "speedup_sqlite_over_duckdb": speedup,
    }
    (args.output_dir / "verification_path_benchmark.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if all(run["returncode"] == 0 for run in runs) and (not comparisons or summary["all_comparisons_match"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())

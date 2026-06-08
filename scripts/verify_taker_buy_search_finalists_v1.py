#!/usr/bin/env python3
"""Rerun cache-search finalists against raw replay SQLite.

This is the final accuracy gate for the V1 cache workflow. Search is allowed to
use the shared candidate cache, but conclusions must come from this raw replay
verification step or an equivalent replay rerun.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_DAYS = "2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07"
DEFAULT_BACKTEST = Path("scripts/backtest_btc5m_taker_buy_signal_fast.py")


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes"}


def read_results(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def finalist_command(
    python: str,
    script: Path,
    replay_root: Path,
    days: str,
    output_dir: Path,
    row: dict[str, str],
    clip: float,
    max_l2_age_ms: int,
) -> list[str]:
    cmd = [
        python,
        str(script),
        "--replay-root",
        str(replay_root),
        "--days",
        days,
        "--output-dir",
        str(output_dir),
        "--min-trade-price",
        row["price_lo"],
        "--max-trade-price",
        row["price_hi"],
        "--min-trade-size",
        row["size_lo"],
        "--max-trade-size",
        row["size_hi"],
        "--first-price-source",
        "l2",
        "--min-first-price",
        row["first_lo"],
        "--max-first-price",
        row["first_hi"],
        "--max-l2-age-ms",
        str(max_l2_age_ms),
        "--max-l1-immediate-pair",
        row["max_l1_pair"],
        "--side-filter",
        row.get("side_alignment") or "any",
        "--clip",
        str(clip),
        "--completion-s",
        "30",
        "--max-completion-s",
        "30",
        "--pair-ceiling",
        row["pair_ceiling"],
        "--min-offset-s",
        str(int(float(row["offset_lo"]))),
        "--max-offset-s",
        str(int(float(row["offset_hi"]))),
        "--cooldown-s",
        str(int(float(row.get("cooldown_s") or 10))),
    ]
    if parse_bool(row.get("block_after_residual")):
        cmd.append("--block-after-residual")
    return cmd


def load_backtest_summary(path: Path) -> dict[str, Any] | None:
    summary_path = path / "btc5m_taker_buy_signal_fast_summary.json"
    if not summary_path.is_file():
        return None
    return json.loads(summary_path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-results-csv", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--days", default=DEFAULT_DAYS)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=10)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--backtest-script", type=Path, default=DEFAULT_BACKTEST)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    script = args.backtest_script
    if not script.is_absolute():
        script = repo_root / script
    if not script.is_file():
        raise FileNotFoundError(f"missing backtest script: {script}")

    rows = read_results(args.search_results_csv)
    finalists = rows[: args.top_n]
    args.output_dir.mkdir(parents=True, exist_ok=True)

    verification_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(finalists, start=1):
        finalist_dir = args.output_dir / f"finalist_{rank:02d}"
        cmd = finalist_command(
            sys.executable,
            script,
            args.replay_root,
            args.days,
            finalist_dir,
            row,
            args.clip,
            args.max_l2_age_ms,
        )
        subprocess.run(cmd, cwd=repo_root, check=True)
        summary = load_backtest_summary(finalist_dir)
        aggregate = (summary or {}).get("aggregate", {}).get("all", {})
        verification_rows.append(
            {
                "rank": rank,
                "finalist_dir": str(finalist_dir),
                "cmd": cmd[1:],
                "cache_rows": row.get("rows"),
                "cache_pnl": row.get("pnl"),
                "cache_min_day_pnl": row.get("min_day_pnl"),
                "raw_rows": aggregate.get("rows"),
                "raw_pnl": aggregate.get("pnl"),
                "raw_roi_on_first_cost": aggregate.get("roi_on_first_cost"),
                "raw_min_day_pnl": aggregate.get("min_day_pnl"),
            }
        )

    report = {
        "generated_at_utc": utc_now(),
        "search_results_csv": str(args.search_results_csv),
        "replay_root": str(args.replay_root),
        "days": args.days,
        "top_n": args.top_n,
        "truth_policy": "these raw replay reruns are the verification source for finalist conclusions",
        "results": verification_rows,
    }
    (args.output_dir / "taker_buy_finalist_raw_verification_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(args.output_dir), "verified": len(verification_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

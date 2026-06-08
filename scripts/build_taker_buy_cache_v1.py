#!/usr/bin/env python3
"""Build and publish the V1 taker-BUY candidate cache safely.

This is a wrapper around the existing dense-L2 cache builder. It adds the
operational guarantees needed for shared multi-agent use:

- one builder per cache key via flock
- disk guardrail before and after the heavy build
- source replay metadata in a manifest
- CSV/summary consistency checks
- .tmp directory build followed by atomic publish

The published cache is still an acceleration index, not the final source of
truth. Finalists must be verified against raw replay SQLite.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_DAYS = "2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07"
DEFAULT_BUILDER = Path("scripts/build_taker_buy_signal_candidate_cache_stream.py")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_days(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def run_text(cmd: list[str], cwd: Path) -> str | None:
    try:
        return subprocess.check_output(cmd, cwd=cwd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def repo_meta(repo_root: Path) -> dict[str, Any]:
    status = run_text(["git", "status", "--short"], repo_root)
    return {
        "commit": run_text(["git", "rev-parse", "HEAD"], repo_root),
        "branch": run_text(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_root),
        "dirty": bool(status),
        "status_short": status.splitlines()[:200] if status else [],
    }


def sqlite_sequence(path: Path) -> dict[str, int]:
    with sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True) as conn:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute("SELECT name, seq FROM sqlite_sequence ORDER BY name").fetchall()
    return {str(name): int(seq) for name, seq in rows}


def source_db_meta(replay_root: Path, days: list[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.is_file():
            raise FileNotFoundError(f"missing replay db: {db_path}")
        stat = db_path.stat()
        out.append(
            {
                "day": day,
                "path": str(db_path),
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "sqlite_sequence": sqlite_sequence(db_path),
            }
        )
    return out


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    usage = shutil.disk_usage(target)
    return int(usage.free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def count_csv_rows(path: Path) -> tuple[int, list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return 0, []
        count = sum(1 for _ in reader)
        return count, list(reader.fieldnames)


def load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"cache already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--days", default=DEFAULT_DAYS)
    parser.add_argument("--cache-name", default="taker_buy_signal_core_v1")
    parser.add_argument("--label", default=None, help="Published directory name. Defaults to compact day span.")
    parser.add_argument("--builder-script", type=Path, default=DEFAULT_BUILDER)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--min-trade-price", type=float, default=0.50)
    parser.add_argument("--max-trade-price", type=float, default=0.75)
    parser.add_argument("--min-trade-size", type=float, default=50.0)
    parser.add_argument("--max-trade-size", type=float, default=250.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--clip", type=float, default=60.0)
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    days = parse_days(args.days)
    if not days:
        raise SystemExit("no days provided")
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    publish_root = args.cache_root / args.cache_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    builder_script = args.builder_script
    if not builder_script.is_absolute():
        builder_script = repo_root / builder_script
    if not builder_script.is_file():
        raise FileNotFoundError(f"missing builder script: {builder_script}")

    publish_root.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.cache_root, args.min_free_gb)
        source_meta = source_db_meta(args.replay_root, days)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        builder_args = [
            sys.executable,
            str(builder_script),
            "--replay-root",
            str(args.replay_root),
            "--days",
            ",".join(days),
            "--output-dir",
            str(tmp_dir),
            "--min-trade-price",
            str(args.min_trade_price),
            "--max-trade-price",
            str(args.max_trade_price),
            "--min-trade-size",
            str(args.min_trade_size),
            "--max-trade-size",
            str(args.max_trade_size),
            "--min-offset-s",
            str(args.min_offset_s),
            "--max-offset-s",
            str(args.max_offset_s),
            "--completion-s",
            str(args.completion_s),
            "--max-l2-age-ms",
            str(args.max_l2_age_ms),
            "--clip",
            str(args.clip),
            "--progress-every",
            str(args.progress_every),
        ]
        started_at = utc_now()
        try:
            subprocess.run(builder_args, cwd=repo_root, check=True)
            csv_path = tmp_dir / "taker_buy_signal_candidate_cache.csv"
            summary_path = tmp_dir / "taker_buy_signal_candidate_cache_summary.json"
            if not csv_path.is_file():
                raise RuntimeError(f"builder did not produce {csv_path}")
            if not summary_path.is_file():
                raise RuntimeError(f"builder did not produce {summary_path}")
            row_count, fieldnames = count_csv_rows(csv_path)
            summary = load_summary(summary_path)
            expected_rows = int(summary.get("candidate_rows", -1))
            if row_count != expected_rows:
                raise RuntimeError(f"cache row count mismatch: csv={row_count} summary={expected_rows}")
            source_meta_after = source_db_meta(args.replay_root, days)
            if source_meta_after != source_meta:
                raise RuntimeError("source replay metadata changed during cache build")
            manifest = {
                "schema_version": 1,
                "cache_kind": "taker_buy_signal_candidate_cache",
                "cache_name": args.cache_name,
                "label": label,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "builder_script": str(builder_script),
                "builder_args": builder_args[1:],
                "repo": repo_meta(repo_root),
                "replay_root": str(args.replay_root),
                "days": days,
                "source_replay": source_meta,
                "outputs": {
                    "csv": "taker_buy_signal_candidate_cache.csv",
                    "summary": "taker_buy_signal_candidate_cache_summary.json",
                    "row_count": row_count,
                    "fieldnames": fieldnames,
                },
                "parameters": {
                    "min_trade_price": args.min_trade_price,
                    "max_trade_price": args.max_trade_price,
                    "min_trade_size": args.min_trade_size,
                    "max_trade_size": args.max_trade_size,
                    "min_offset_s": args.min_offset_s,
                    "max_offset_s": args.max_offset_s,
                    "completion_s": args.completion_s,
                    "max_l2_age_ms": args.max_l2_age_ms,
                    "clip": args.clip,
                },
                "feature_policy": {
                    "l1_policy": summary.get("l1_policy"),
                    "strict_l1_max_age_ms": summary.get("strict_l1_max_age_ms"),
                },
                "truth_policy": "cache is an acceleration index; final strategies must be verified against replay_published",
            }
            write_json(tmp_dir / "CACHE_MANIFEST.json", manifest)
            require_free_gb(args.cache_root, max(1.0, args.min_free_gb - 20.0))
            publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "rows": row_count}, indent=2))
            return 0
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

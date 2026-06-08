#!/usr/bin/env python3
"""Submit a taker-BUY strict-L1 finalist verification job.

The job is only a request.  It does not scan replay on the submitting host.  A
collector-local worker consumes jobs from the queue and uses the event-store
backend by default, with shared replay retained as an explicit audit backend.
"""

from __future__ import annotations

import argparse
import json
import re
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_QUEUE_ROOT = Path("/home/ubuntu/poly_trans_research/data/verification_queue/taker_buy_strict_l1")
DEFAULT_REPLAY_ROOT = Path("/home/ubuntu/poly_trans_research/data/replay_published")
DEFAULT_EXPORT_ROOT = Path("/home/ubuntu/poly_trans_research/data/exports/verification_queue")
DEFAULT_CACHE_ROOT = Path("/home/ubuntu/poly_trans_research/data/backtest_cache")
DEFAULT_EVENT_STORE_ROOT = Path("/home/ubuntu/poly_trans_research/data/verification_store/taker_buy_event_store_v1")

WINDOW_DEFAULTS = {
    "20260502_20260507": {
        "days": "2026-05-02,2026-05-03,2026-05-04,2026-05-05,2026-05-06,2026-05-07",
        "candidate_cache_csv": DEFAULT_CACHE_ROOT
        / "taker_buy_signal_core_v1_strict_l1"
        / "20260502_20260507"
        / "taker_buy_signal_candidate_cache.csv",
        "event_store_dir": DEFAULT_EVENT_STORE_ROOT / "20260502_20260507",
    },
    "20260508": {
        "days": "2026-05-08",
        "candidate_cache_csv": DEFAULT_CACHE_ROOT
        / "taker_buy_signal_core_v1_strict_l1"
        / "20260508"
        / "taker_buy_signal_candidate_cache.csv",
        "event_store_dir": DEFAULT_EVENT_STORE_ROOT / "20260508",
    },
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def slug(value: str) -> str:
    out = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return out.strip("._-") or "agent"


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE_ROOT)
    parser.add_argument("--agent-id", required=True, help="Stable agent/user id, used in job_id and output path.")
    parser.add_argument("--label", required=True, help="Cache/replay window label, e.g. 20260502_20260507 or 20260508.")
    parser.add_argument("--search-results-csv", type=Path, required=True, help="Collector-local search result CSV path.")
    parser.add_argument("--top-n", type=int, default=20)
    parser.add_argument("--rank", type=int, action="append", help="Optional rank override; can be repeated.")
    parser.add_argument("--days", help="Override replay days, comma-separated.")
    parser.add_argument("--candidate-cache-csv", type=Path, help="Override strict V1 candidate cache CSV.")
    parser.add_argument("--event-store-dir", type=Path, help="Override taker-buy event verification store.")
    parser.add_argument("--backend", choices=("auto", "event_store", "shared_replay"), default="auto")
    parser.add_argument("--replay-root", type=Path, default=DEFAULT_REPLAY_ROOT)
    parser.add_argument("--output-dir", type=Path, help="Override collector-local output directory.")
    parser.add_argument("--job-id", help="Optional explicit job id. Must be unique.")
    parser.add_argument("--priority", type=int, default=100, help="Lower values sort first for operators.")
    parser.add_argument("--note", default="")
    parser.add_argument("--allow-missing-search-csv", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    defaults = WINDOW_DEFAULTS.get(args.label, {})
    days = args.days or defaults.get("days")
    candidate_cache_csv = args.candidate_cache_csv or defaults.get("candidate_cache_csv")
    event_store_dir = args.event_store_dir or defaults.get("event_store_dir")
    if not days:
        raise SystemExit(f"--days is required for unknown label: {args.label}")
    if not candidate_cache_csv:
        raise SystemExit(f"--candidate-cache-csv is required for unknown label: {args.label}")
    search_results_csv = args.search_results_csv
    if not search_results_csv.exists() and not args.allow_missing_search_csv:
        raise SystemExit(f"search results CSV not found on collector: {search_results_csv}")
    if not Path(candidate_cache_csv).exists():
        raise SystemExit(f"candidate cache CSV not found on collector: {candidate_cache_csv}")
    if not args.replay_root.exists():
        raise SystemExit(f"replay root not found on collector: {args.replay_root}")
    if args.backend == "event_store" and (event_store_dir is None or not Path(event_store_dir).exists()):
        raise SystemExit(f"event-store backend requested but event store not found: {event_store_dir}")

    created_at = utc_now()
    agent = slug(args.agent_id)
    label = slug(args.label)
    rank_suffix = "ranks_" + "_".join(str(r) for r in args.rank) if args.rank else f"top{args.top_n}"
    job_id = slug(args.job_id) if args.job_id else f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}_{agent}_{label}_{rank_suffix}_{uuid.uuid4().hex[:8]}"
    output_dir = args.output_dir or DEFAULT_EXPORT_ROOT / job_id
    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at_utc": created_at,
        "agent_id": args.agent_id,
        "label": args.label,
        "priority": args.priority,
        "note": args.note,
        "search_results_csv": str(search_results_csv),
        "candidate_cache_csv": str(candidate_cache_csv),
        "event_store_dir": None if event_store_dir is None else str(event_store_dir),
        "backend": args.backend,
        "replay_root": str(args.replay_root),
        "days": days,
        "output_dir": str(output_dir),
        "top_n": args.top_n,
        "ranks": args.rank or [],
        "verifier": "auto_event_store_then_shared_replay",
    }
    incoming = args.queue_root / "incoming" / f"{job_id}.json"
    if incoming.exists():
        raise SystemExit(f"job already exists: {incoming}")
    atomic_write_json(incoming, job)
    print(json.dumps({"queued": True, "job_id": job_id, "job_file": str(incoming), "output_dir": str(output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

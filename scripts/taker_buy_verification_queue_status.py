#!/usr/bin/env python3
"""Print status for the taker-BUY strict-L1 verification queue."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


DEFAULT_QUEUE_ROOT = Path("/home/ubuntu/poly_trans_research/data/verification_queue/taker_buy_strict_l1")


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"job_id": path.stem, "status": "unreadable", "error": repr(exc)}


def summarize(path: Path) -> dict[str, Any]:
    job = load_json(path)
    summary = job.get("summary") or {}
    results = summary.get("results") or []
    top = results[0] if results else {}
    return {
        "job_id": job.get("job_id", path.stem),
        "status": job.get("status"),
        "agent_id": job.get("agent_id"),
        "label": job.get("label"),
        "priority": job.get("priority"),
        "created_at_utc": job.get("created_at_utc"),
        "started_at_utc": job.get("started_at_utc"),
        "finished_at_utc": job.get("finished_at_utc"),
        "elapsed_s": job.get("elapsed_s"),
        "top_n": job.get("top_n"),
        "ranks": job.get("ranks"),
        "backend": job.get("backend"),
        "resolved_backend": job.get("resolved_backend"),
        "event_store_dir": job.get("event_store_dir"),
        "output_dir": job.get("output_dir"),
        "log_path": job.get("log_path"),
        "error": job.get("error"),
        "top_rank_result": top,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE_ROOT)
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()

    payload: dict[str, Any] = {"queue_root": str(args.queue_root), "sections": {}}
    for section in ("running", "incoming", "done", "failed"):
        paths = sorted((args.queue_root / section).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=section in {"done", "failed"})
        payload["sections"][section] = {
            "count": len(paths),
            "jobs": [summarize(path) for path in paths[: args.limit]],
        }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

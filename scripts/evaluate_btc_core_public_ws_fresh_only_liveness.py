#!/usr/bin/env python3
"""Evaluate fresh-only liveness from BTC_CORE public WS observer events."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


STATUS_KEEP_SCOPED = "KEEP_BTC_CORE_PUBLIC_WS_FRESH_ONLY_ACTIVE_SUBSET_REVIEW_REQUIRED_NOT_OOS_CLEAN"
STATUS_BLOCKED_FULL = "BLOCKED_BTC_CORE_PUBLIC_WS_FRESH_ONLY_FULL_SCOPE_COVERAGE_INCOMPLETE_NOT_OOS_READY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_targets(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        rows = [row for row in csv.DictReader(f) if row.get("binding_status") == "BOUND"]
    return {row["condition_id"]: row for row in rows}


def iter_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                out.append(json.loads(line))
    return out


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-csv", type=Path, required=True)
    parser.add_argument("--events-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--expected-target-count", type=int, default=215)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    targets = load_targets(args.target_csv)
    events = iter_events(args.events_jsonl)
    if len(targets) != args.expected_target_count:
        raise SystemExit(f"target_count_mismatch {len(targets)} != {args.expected_target_count}")

    fresh_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stale_by_condition: Counter[str] = Counter()
    sessions: Counter[int] = Counter()
    for event in events:
        condition_id = str(event.get("condition_id") or "")
        if condition_id not in targets:
            continue
        session_id = int(event.get("ws_session_index") or 0)
        sessions[session_id] += 1
        if event.get("fresh_after_warmup") and event.get("top_depth_complete"):
            fresh_by_condition[condition_id].append(event)
        elif event.get("is_after_warmup") and (event.get("book_age_ms") or 10**12) > 60_000:
            stale_by_condition[condition_id] += 1

    active_conditions = sorted(fresh_by_condition)
    missing_conditions = sorted(set(targets) - set(active_conditions))
    active_rows: list[dict[str, Any]] = []
    for condition_id in active_conditions:
        target = targets[condition_id]
        evs = fresh_by_condition[condition_id]
        active_rows.append(
            {
                "projection_round_index": target["projection_round_index"],
                "slug": target["slug"],
                "condition_id": condition_id,
                "market_id": target["market_id"],
                "token_id_yes": target["token_id_yes"],
                "token_id_no": target["token_id_no"],
                "fresh_top_depth_event_count": len(evs),
                "first_fresh_recv_ts_ms": min(int(ev["recv_ts_ms"]) for ev in evs),
                "last_fresh_recv_ts_ms": max(int(ev["recv_ts_ms"]) for ev in evs),
                "post_warmup_stale_event_count": stale_by_condition[condition_id],
            }
        )
    missing_rows = [
        {
            "projection_round_index": targets[condition_id]["projection_round_index"],
            "slug": targets[condition_id]["slug"],
            "condition_id": condition_id,
            "post_warmup_stale_event_count": stale_by_condition[condition_id],
        }
        for condition_id in missing_conditions
    ]
    status = STATUS_BLOCKED_FULL
    if active_conditions:
        status = STATUS_KEEP_SCOPED
    summary = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "target_market_count": len(targets),
        "fresh_only_active_market_count": len(active_conditions),
        "fresh_only_missing_market_count": len(missing_conditions),
        "fresh_only_coverage_rate": len(active_conditions) / len(targets),
        "event_rows": len(events),
        "ws_sessions_seen": sorted(sessions),
        "ws_session_event_counts": dict(sorted(sessions.items())),
        "post_warmup_stale_market_count": len(stale_by_condition),
        "interpretation": (
            "Fresh-only liveness coverage counts only post-warmup top-depth rows with book_age<=60000ms. "
            "Stale rows are ignored as evidence and retained as attribution. This is not a clean full-scope OOS pass."
        ),
        "non_claims": {
            "clean_full_scope_oos_pass": False,
            "full_288_market_oos_pass": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    active_path = args.output_dir / "BTC_CORE_PUBLIC_WS_FRESH_ONLY_ACTIVE_MARKETS.csv"
    missing_path = args.output_dir / "BTC_CORE_PUBLIC_WS_FRESH_ONLY_MISSING_MARKETS.csv"
    summary_path = args.output_dir / "BTC_CORE_PUBLIC_WS_FRESH_ONLY_LIVENESS_EVAL.json"
    manifest_path = args.output_dir / "BTC_CORE_PUBLIC_WS_FRESH_ONLY_LIVENESS_HASH_MANIFEST.json"
    write_csv(
        active_path,
        active_rows,
        [
            "projection_round_index",
            "slug",
            "condition_id",
            "market_id",
            "token_id_yes",
            "token_id_no",
            "fresh_top_depth_event_count",
            "first_fresh_recv_ts_ms",
            "last_fresh_recv_ts_ms",
            "post_warmup_stale_event_count",
        ],
    )
    write_csv(missing_path, missing_rows, ["projection_round_index", "slug", "condition_id", "post_warmup_stale_event_count"])
    write_json(summary_path, summary)
    manifest = {
        "schema_version": 1,
        "status": "BTC_CORE_PUBLIC_WS_FRESH_ONLY_LIVENESS_HASH_MANIFEST",
        "files": {},
    }
    for path in [summary_path, active_path, missing_path, args.target_csv, args.events_jsonl]:
        manifest["files"][path.name] = {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
    write_json(manifest_path, manifest)
    print(f"status={status}")
    print(f"fresh_only_active_market_count={len(active_conditions)}")
    print(f"fresh_only_missing_market_count={len(missing_conditions)}")
    print(f"coverage_rate={summary['fresh_only_coverage_rate']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

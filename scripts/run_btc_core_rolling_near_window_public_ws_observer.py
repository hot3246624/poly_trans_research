#!/usr/bin/env python3
"""Rolling near-window public WS observer for BTC_CORE targets.

The reviewed target CSV can contain a long future schedule. This wrapper only
subscribes to markets whose 5m window is current or near-current, so far-future
markets are treated as a scheduling pool rather than evidence obligations.

It is public-only/no-order research:
- one direct public CLOB market WebSocket at a time;
- no shared-ingress/shared-WS;
- no REST book evidence;
- no private keys, imports, orders, cancels, redeems, live, deploy, funding, or
  latest pointer updates.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_OBSERVER = ROOT / "scripts" / "run_btc_core_scoped_public_ws_no_order_observer.py"
STATUS_KEEP = "KEEP_BTC_CORE_ROLLING_NEAR_WINDOW_PUBLIC_WS_RESEARCH_REVIEW_REQUIRED_NOT_OOS_CLEAN"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_ROLLING_NEAR_WINDOW_PUBLIC_WS_RESEARCH_FAIL_CLOSED"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_target_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    required = {"condition_id", "subscribed_asset_ids", "window_start_ts_ms", "window_end_ts_ms", "binding_status"}
    missing = sorted(required - set(fieldnames))
    if missing:
        raise ValueError(f"target CSV missing required columns: {missing}")
    return fieldnames, rows


def row_int(row: dict[str, str], key: str) -> int:
    return int(row[key])


def select_near_rows(
    rows: list[dict[str, str]],
    *,
    at_ms: int,
    selection_mode: str,
    lead_ms: int,
    after_end_ms: int,
    min_remaining_ms: int,
    next_target_count: int,
    max_target_count: int,
) -> list[dict[str, str]]:
    eligible: list[dict[str, str]] = []
    for row in rows:
        if row.get("binding_status") != "BOUND":
            continue
        start_ms = row_int(row, "window_start_ts_ms")
        end_ms = row_int(row, "window_end_ts_ms")
        if end_ms <= at_ms + min_remaining_ms:
            continue
        if selection_mode == "near_window":
            upper = at_ms + lead_ms
            lower = at_ms - after_end_ms
            if start_ms <= upper and end_ms > lower:
                eligible.append(row)
        elif selection_mode == "current_plus_next":
            # Strategy evidence should only come from the current live market.
            # The next market is subscribed for handoff/warm cache, not as a
            # far-future signal.
            if start_ms <= at_ms < end_ms or at_ms < start_ms <= at_ms + lead_ms:
                eligible.append(row)
        else:
            raise ValueError(f"unknown selection_mode: {selection_mode}")
    eligible.sort(key=lambda row: (row_int(row, "window_start_ts_ms"), row.get("condition_id", "")))
    if selection_mode == "current_plus_next":
        current = [
            row
            for row in eligible
            if row_int(row, "window_start_ts_ms") <= at_ms < row_int(row, "window_end_ts_ms")
        ]
        if not current:
            return []
        future = [
            row
            for row in eligible
            if at_ms < row_int(row, "window_start_ts_ms") <= at_ms + lead_ms
        ]
        # One BTC 5m current market should exist at most; keep the nearest if a
        # duplicated schedule ever appears and let downstream duplicate checks
        # fail closed.
        selected: list[dict[str, str]] = []
        for row in current[:1]:
            copied = dict(row)
            copied["target_role"] = "evidence_current"
            selected.append(copied)
        for row in future[: max(0, next_target_count)]:
            copied = dict(row)
            copied["target_role"] = "handoff_next"
            selected.append(copied)
        eligible = selected
    else:
        eligible = [{**row, "target_role": row.get("target_role") or "evidence"} for row in eligible]
    if max_target_count > 0:
        eligible = eligible[:max_target_count]
    return eligible


def write_subset(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    if "target_role" not in fieldnames:
        fieldnames = [*fieldnames, "target_role"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-csv", type=Path, required=True)
    parser.add_argument("--expected-target-csv-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=float, default=3600.0)
    parser.add_argument("--chunk-sec", type=float, default=240.0)
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument(
        "--selection-mode",
        choices=("near_window", "current_plus_next"),
        default="current_plus_next",
        help="current_plus_next is the CE25-like live strategy mode; near_window is broader infrastructure smoke.",
    )
    parser.add_argument("--lead-ms", type=int, default=300_000, help="In current_plus_next mode, only the next market should be within this lead.")
    parser.add_argument("--after-end-ms", type=int, default=0, help="Optional grace after market end; default excludes ended markets.")
    parser.add_argument("--min-remaining-ms", type=int, default=90_000, help="Exclude markets too close to end for warmup plus useful observation.")
    parser.add_argument("--next-target-count", type=int, default=1, help="Number of handoff future targets in current_plus_next mode.")
    parser.add_argument("--max-targets-per-chunk", type=int, default=2, help="Default is current BTC 5m plus one handoff target.")
    parser.add_argument("--observer-duration-sec", type=float, default=0.0, help="Override per-chunk observer duration; 0 uses chunk-sec.")
    parser.add_argument("--warmup-sec", type=float, default=20.0)
    parser.add_argument("--book-max-age-ms", type=int, default=60_000)
    parser.add_argument("--min-top-levels", type=int, default=1)
    parser.add_argument("--allow-sequential-reconnects", action="store_true")
    parser.add_argument("--max-reconnects", type=int, default=0)
    parser.add_argument("--reconnect-backoff-sec", type=float, default=5.0)
    parser.add_argument("--stop-on-blocked-chunk", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        print(f"BLOCKED_OUTPUT_DIR_EXISTS path={args.output_dir}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True)
    subsets_dir = args.output_dir / "subsets"
    chunks_dir = args.output_dir / "chunks"
    subsets_dir.mkdir()
    chunks_dir.mkdir()
    manifest_path = args.output_dir / "BTC_CORE_ROLLING_NEAR_WINDOW_PUBLIC_WS_MANIFEST.json"
    chunk_log_path = args.output_dir / "BTC_CORE_ROLLING_NEAR_WINDOW_PUBLIC_WS_CHUNKS.jsonl"
    chunk_log_path.write_text("", encoding="utf-8")

    errors: list[str] = []
    target_hash = sha256_file(args.target_csv)
    if target_hash != args.expected_target_csv_sha256:
        errors.append("target_csv_hash_mismatch")
    try:
        fieldnames, rows = read_target_rows(args.target_csv)
    except Exception as exc:  # noqa: BLE001
        fieldnames, rows = [], []
        errors.append(f"target_load_error:{exc}")
    if args.max_targets_per_chunk < 1:
        errors.append("max_targets_per_chunk_must_be_positive")
    if args.next_target_count < 0:
        errors.append("next_target_count_negative")
    if args.lead_ms < 0:
        errors.append("lead_ms_negative")
    if args.after_end_ms < 0:
        errors.append("after_end_ms_negative")
    if args.min_remaining_ms < 0:
        errors.append("min_remaining_ms_negative")
    if args.chunk_sec <= 0 or args.duration_sec <= 0:
        errors.append("duration_or_chunk_sec_nonpositive")
    if errors:
        write_json(
            manifest_path,
            {
                "schema_version": 1,
                "created_at": utc_now(),
                "status": STATUS_BLOCKED,
                "errors": errors,
                "target_csv": str(args.target_csv),
                "target_csv_sha256": target_hash,
            },
        )
        return 2

    started = time.monotonic()
    chunk_index = 0
    chunk_returncodes: list[int] = []
    selected_condition_ids: set[str] = set()
    while True:
        elapsed = time.monotonic() - started
        remaining = args.duration_sec - elapsed
        if remaining <= 0:
            break
        selected = select_near_rows(
            rows,
            at_ms=now_ms(),
            selection_mode=args.selection_mode,
            lead_ms=args.lead_ms,
            after_end_ms=args.after_end_ms,
            min_remaining_ms=args.min_remaining_ms,
            next_target_count=args.next_target_count,
            max_target_count=args.max_targets_per_chunk,
        )
        if not selected:
            append_jsonl(
                chunk_log_path,
                {
                    "event": "no_near_window_targets",
                    "ts": utc_now(),
                    "remaining_sec": round(remaining, 3),
                    "lead_ms": args.lead_ms,
                    "after_end_ms": args.after_end_ms,
                },
            )
            time.sleep(min(args.poll_sec, max(0.0, remaining)))
            continue

        chunk_index += 1
        chunk_duration = min(
            args.observer_duration_sec if args.observer_duration_sec > 0 else args.chunk_sec,
            remaining,
        )
        subset_path = subsets_dir / f"BTC_CORE_ROLLING_NEAR_WINDOW_TARGETS_CHUNK_{chunk_index:04d}.csv"
        write_subset(subset_path, fieldnames, selected)
        subset_hash = sha256_file(subset_path)
        chunk_output_dir = chunks_dir / f"chunk_{chunk_index:04d}"
        selected_condition_ids.update(row["condition_id"] for row in selected)
        cmd = [
            sys.executable,
            str(BASE_OBSERVER),
            "--target-csv",
            str(subset_path),
            "--expected-target-csv-sha256",
            subset_hash,
            "--expected-target-count",
            str(len(selected)),
            "--output-dir",
            str(chunk_output_dir),
            "--duration-sec",
            str(round(chunk_duration, 3)),
            "--warmup-sec",
            str(args.warmup_sec),
            "--require-live-fresh-after-warmup",
            "--per-session-warmup",
            "--book-max-age-ms",
            str(args.book_max_age_ms),
            "--min-top-levels",
            str(args.min_top_levels),
            "--max-ws-connections",
            "1",
            "--target-timing-policy",
            "live_or_future_not_ended",
        ]
        if args.allow_sequential_reconnects:
            cmd.extend(
                [
                    "--allow-sequential-reconnects",
                    "--max-reconnects",
                    str(args.max_reconnects),
                    "--reconnect-backoff-sec",
                    str(args.reconnect_backoff_sec),
                ]
            )
        append_jsonl(
            chunk_log_path,
            {
                "event": "chunk_start",
                "chunk_index": chunk_index,
                "ts": utc_now(),
                "target_count": len(selected),
                "window_start_min_ms": min(row_int(row, "window_start_ts_ms") for row in selected),
                "window_start_max_ms": max(row_int(row, "window_start_ts_ms") for row in selected),
                "subset_csv": str(subset_path),
                "subset_csv_sha256": subset_hash,
                "chunk_output_dir": str(chunk_output_dir),
                "cmd": cmd,
            },
        )
        proc = subprocess.run(cmd, check=False)
        chunk_returncodes.append(proc.returncode)
        append_jsonl(
            chunk_log_path,
            {
                "event": "chunk_end",
                "chunk_index": chunk_index,
                "ts": utc_now(),
                "returncode": proc.returncode,
                "chunk_output_dir": str(chunk_output_dir),
            },
        )
        if args.stop_on_blocked_chunk and proc.returncode != 0:
            break

    threshold_failures = []
    if any(code != 0 for code in chunk_returncodes):
        threshold_failures.append("chunk_returncode_nonzero")
    status = STATUS_KEEP if not threshold_failures else STATUS_BLOCKED
    write_json(
        manifest_path,
        {
            "schema_version": 1,
            "created_at": utc_now(),
            "status": status,
            "scope": "rolling_near_window_public_ws_research_not_full_oos",
            "target_csv": str(args.target_csv),
            "target_csv_sha256": target_hash,
            "loaded_target_count": len([row for row in rows if row.get("binding_status") == "BOUND"]),
            "unique_selected_target_count": len(selected_condition_ids),
            "chunk_count": chunk_index,
            "chunk_returncodes": chunk_returncodes,
            "lead_ms": args.lead_ms,
            "after_end_ms": args.after_end_ms,
            "min_remaining_ms": args.min_remaining_ms,
            "selection_mode": args.selection_mode,
            "next_target_count": args.next_target_count,
            "max_targets_per_chunk": args.max_targets_per_chunk,
            "one_ws_at_a_time": True,
            "transport": "direct_public_clob_ws",
            "shared_ingress_used": False,
            "rest_book_used": False,
            "non_claims": {
                "full_215_oos_pass": False,
                "full_288_oos_pass": False,
                "private_truth_ready": False,
                "strategy_promotion_ready": False,
                "live_ready": False,
                "deployable": False,
                "orders_authorized": False,
                "cancels_authorized": False,
                "redeems_authorized": False,
                "private_key_loaded": False,
                "latest_pointer_update_authorized": False,
            },
            "threshold_failure_count": len(threshold_failures),
            "threshold_failures": threshold_failures,
        },
    )
    print(json.dumps({"status": status, "output_dir": str(args.output_dir), "chunks": chunk_index}, indent=2))
    return 0 if not threshold_failures else 2


if __name__ == "__main__":
    raise SystemExit(main())

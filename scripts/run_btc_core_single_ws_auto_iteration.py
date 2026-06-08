#!/usr/bin/env python3
"""BTC_CORE current/live single-WS auto-iteration supervisor.

This wrapper keeps the next executable step inside automation:

1. refresh public Gamma metadata for current/near-future BTC 5m markets;
2. materialize a fresh target CSV that allows the current 5m bucket;
3. fail closed before opening a WebSocket if no target is currently live with
   enough remaining decision-window time;
4. run exactly one bounded direct public CLOB WS observer segment at a time.

It never uses shared-ingress, REST book evidence, private keys, imports, orders,
canary/live/deploy paths, funding, or latest pointers.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STATUS_RUNNING = "KEEP_BTC_CORE_SINGLE_WS_AUTO_ITERATION_RUNNING_RESEARCH_ONLY"
STATUS_DONE = "KEEP_BTC_CORE_SINGLE_WS_AUTO_ITERATION_DONE_RESEARCH_ONLY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_SINGLE_WS_AUTO_ITERATION_FAIL_CLOSED"


def now_ms() -> int:
    return int(time.time() * 1000)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_ms(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def stamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def active_observer_processes() -> list[str]:
    proc = subprocess.run(
        ["ps", "-eo", "pid=,args="],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    out: list[str] = []
    for line in proc.stdout.splitlines():
        # Ignore launcher/shell command lines that mention observer names only
        # inside guard expressions. Only real Python observer processes should
        # block a segment.
        if "run_btc_core_single_ws_auto_iteration.py" in line:
            continue
        if "awk" in line or "ACTIVE=$(ps" in line:
            continue
        if "run_btc_core_single_ws_live_handoff_observer.py" in line:
            out.append(line.strip())
        elif "run_btc_core_scoped_public_ws_no_order_observer.py" in line:
            out.append(line.strip())
    return out


def run_command(cmd: list[str], *, cwd: Path, stdout_path: Path, stderr_path: Path) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open("w", encoding="utf-8") as stderr:
        proc = subprocess.run(cmd, cwd=cwd, text=True, stdout=stdout, stderr=stderr)
    return proc.returncode


def load_current_eligible_targets(target_csv: Path, *, at_ms: int, min_remaining_ms: int) -> list[dict[str, str]]:
    with target_csv.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    eligible: list[dict[str, str]] = []
    for row in rows:
        if row.get("binding_status") != "BOUND":
            continue
        try:
            start_ms = int(row["window_start_ts_ms"])
            end_ms = int(row["window_end_ts_ms"])
        except (KeyError, ValueError):
            continue
        if start_ms <= at_ms < end_ms and end_ms > at_ms + min_remaining_ms:
            eligible.append(row)
    return eligible


def non_claims() -> dict[str, bool]:
    return {
        "orders_authorized": False,
        "cancels_authorized": False,
        "redeems_authorized": False,
        "candidate_import_authorized": False,
        "private_key_loaded": False,
        "latest_pointer_update_authorized": False,
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
    }


def write_state(path: Path, payload: dict[str, Any]) -> None:
    write_json(
        path,
        {
            "schema_version": 1,
            "updated_at": utc_now(),
            "status": payload.get("status", STATUS_RUNNING),
            "non_claims": non_claims(),
            **payload,
        },
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--max-segments", type=int, default=6)
    parser.add_argument("--segment-duration-sec", type=float, default=480.0)
    parser.add_argument("--warmup-sec", type=float, default=20.0)
    parser.add_argument("--target-round-count", type=int, default=36)
    parser.add_argument("--start-round-offset", type=int, default=0)
    parser.add_argument("--min-target-start-delay-ms", type=int, default=-300_000)
    parser.add_argument("--min-remaining-ms", type=int, default=90_000)
    parser.add_argument("--coverage-wait-sec", type=float, default=900.0)
    parser.add_argument("--coverage-poll-sec", type=float, default=20.0)
    parser.add_argument("--resolver-timeout-sec", type=float, default=4.0)
    parser.add_argument("--resolver-sleep-sec", type=float, default=0.02)
    parser.add_argument("--selection-poll-ms", type=int, default=2_000)
    parser.add_argument("--book-max-age-ms", type=int, default=60_000)
    parser.add_argument("--min-top-levels", type=int, default=1)
    parser.add_argument("--max-decision-depth-gap-ms", type=int, default=2_000)
    parser.add_argument("--terminal-close-grace-sec", type=float, default=15.0)
    parser.add_argument("--next-target-count", type=int, default=0)
    parser.add_argument("--max-active-targets", type=int, default=1)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_root = args.output_root.expanduser().resolve()
    if output_root.exists() and any(output_root.iterdir()):
        raise SystemExit(f"BLOCKED_OUTPUT_ROOT_EXISTS:{output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    state_path = output_root / "BTC_CORE_SINGLE_WS_AUTO_ITERATION_STATE.json"
    segment_results: list[dict[str, Any]] = []

    if args.next_target_count != 0 or args.max_active_targets != 1:
        write_state(
            state_path,
            {
                "status": STATUS_BLOCKED,
                "status_reason": "current_live_only_contract_violation",
                "next_target_count": args.next_target_count,
                "max_active_targets": args.max_active_targets,
            },
        )
        return 2

    write_state(
        state_path,
        {
            "status": STATUS_RUNNING,
            "status_reason": "started",
            "output_root": str(output_root),
            "max_segments": args.max_segments,
            "current_live_only": True,
        },
    )

    for segment_index in range(args.max_segments):
        coverage_deadline = time.monotonic() + args.coverage_wait_sec
        target_csv: Path | None = None
        target_sha = ""
        eligible_count = 0
        refresh_attempts = 0
        last_refresh: dict[str, Any] = {}

        while time.monotonic() <= coverage_deadline:
            active = active_observer_processes()
            if active:
                write_state(
                    state_path,
                    {
                        "status": STATUS_BLOCKED,
                        "status_reason": "active_observer_conflict",
                        "active_observers": active,
                        "segment_results": segment_results,
                    },
                )
                return 2

            ts = stamp()
            projection_created_ms = now_ms()
            resolver_dir = output_root / "resolver" / f"{ts}_seg{segment_index:02d}_attempt{refresh_attempts:02d}"
            target_dir = output_root / "targets" / f"{ts}_seg{segment_index:02d}_attempt{refresh_attempts:02d}"
            log_dir = output_root / "logs" / f"{ts}_seg{segment_index:02d}_attempt{refresh_attempts:02d}"
            refresh_attempts += 1

            resolver_rc = run_command(
                [
                    args.python,
                    "scripts/resolve_btc_core_current_future_gamma_metadata.py",
                    "--output-dir",
                    str(resolver_dir),
                    "--projection-created-ts-ms",
                    str(projection_created_ms),
                    "--start-round-offset",
                    str(args.start_round_offset),
                    "--target-round-count",
                    str(args.target_round_count),
                    "--timeout-sec",
                    str(args.resolver_timeout_sec),
                    "--sleep-sec",
                    str(args.resolver_sleep_sec),
                ],
                cwd=ROOT,
                stdout_path=log_dir / "resolver.stdout.log",
                stderr_path=log_dir / "resolver.stderr.log",
            )
            if resolver_rc != 0:
                last_refresh = {
                    "resolver_returncode": resolver_rc,
                    "resolver_dir": str(resolver_dir),
                    "log_dir": str(log_dir),
                }
                time.sleep(args.coverage_poll_sec)
                continue

            metadata_json = resolver_dir / "BTC_CORE_REVIEWED_RESOLVER_METADATA.json"
            producer_rc = run_command(
                [
                    args.python,
                    "scripts/produce_btc_core_current_future_targets.py",
                    "--resolver-metadata-json",
                    str(metadata_json),
                    "--output-dir",
                    str(target_dir),
                    "--projection-created-ts-ms",
                    str(projection_created_ms),
                    "--projection-created-ts-utc",
                    iso_from_ms(projection_created_ms),
                    "--start-round-offset",
                    str(args.start_round_offset),
                    "--target-round-count",
                    str(args.target_round_count),
                    "--min-target-start-delay-ms",
                    str(args.min_target_start_delay_ms),
                    "--resolver-source",
                    "public_gamma_current_live_auto_iteration",
                ],
                cwd=ROOT,
                stdout_path=log_dir / "producer.stdout.log",
                stderr_path=log_dir / "producer.stderr.log",
            )
            candidate_csv = target_dir / "BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
            validation_json = target_dir / "BTC_CORE_TARGET_PROJECTION_VALIDATION_RESULT.json"
            if producer_rc != 0 or not candidate_csv.is_file():
                last_refresh = {
                    "resolver_returncode": resolver_rc,
                    "producer_returncode": producer_rc,
                    "resolver_dir": str(resolver_dir),
                    "target_dir": str(target_dir),
                    "log_dir": str(log_dir),
                }
                time.sleep(args.coverage_poll_sec)
                continue

            target_csv = candidate_csv
            target_sha = sha256_file(target_csv)
            eligible = load_current_eligible_targets(target_csv, at_ms=now_ms(), min_remaining_ms=args.min_remaining_ms)
            eligible_count = len(eligible)
            last_refresh = {
                "resolver_returncode": resolver_rc,
                "producer_returncode": producer_rc,
                "resolver_dir": str(resolver_dir),
                "target_dir": str(target_dir),
                "target_csv": str(target_csv),
                "target_csv_sha256": target_sha,
                "validation_json": str(validation_json),
                "validation": read_json(validation_json) if validation_json.is_file() else None,
                "eligible_current_target_count": eligible_count,
                "eligible_current_slugs": [row.get("slug") for row in eligible],
            }
            write_state(
                state_path,
                {
                    "status": STATUS_RUNNING,
                    "status_reason": "coverage_probe",
                    "segment_index": segment_index,
                    "refresh_attempts": refresh_attempts,
                    "last_refresh": last_refresh,
                    "segment_results": segment_results,
                },
            )
            if eligible_count > 0:
                break
            time.sleep(args.coverage_poll_sec)

        if not target_csv or eligible_count <= 0:
            write_state(
                state_path,
                {
                    "status": STATUS_BLOCKED,
                    "status_reason": "no_current_live_target_coverage_before_ws",
                    "segment_index": segment_index,
                    "last_refresh": last_refresh,
                    "segment_results": segment_results,
                },
            )
            return 2

        active = active_observer_processes()
        if active:
            write_state(
                state_path,
                {
                    "status": STATUS_BLOCKED,
                    "status_reason": "active_observer_conflict_before_segment",
                    "active_observers": active,
                    "segment_results": segment_results,
                },
            )
            return 2

        ts = stamp()
        segment_dir = output_root / "segments" / f"{ts}_seg{segment_index:02d}_current_live_only"
        log_dir = output_root / "logs" / f"{ts}_seg{segment_index:02d}_observer"
        observer_rc = run_command(
            [
                args.python,
                "scripts/run_btc_core_single_ws_live_handoff_observer.py",
                "--target-csv",
                str(target_csv),
                "--expected-target-csv-sha256",
                target_sha,
                "--output-dir",
                str(segment_dir),
                "--duration-sec",
                str(args.segment_duration_sec),
                "--warmup-sec",
                str(args.warmup_sec),
                "--selection-poll-ms",
                str(args.selection_poll_ms),
                "--lead-ms",
                "0",
                "--min-remaining-ms",
                str(args.min_remaining_ms),
                "--next-target-count",
                "0",
                "--max-active-targets",
                "1",
                "--book-max-age-ms",
                str(args.book_max_age_ms),
                "--min-top-levels",
                str(args.min_top_levels),
                "--max-decision-depth-gap-ms",
                str(args.max_decision_depth_gap_ms),
                "--max-ws-connections",
                "1",
                "--terminal-close-grace-sec",
                str(args.terminal_close_grace_sec),
            ],
            cwd=ROOT,
            stdout_path=log_dir / "observer.stdout.log",
            stderr_path=log_dir / "observer.stderr.log",
        )
        eval_path = segment_dir / "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_EVAL.json"
        eval_payload = read_json(eval_path) if eval_path.is_file() else {}
        result = {
            "segment_index": segment_index,
            "observer_returncode": observer_rc,
            "segment_dir": str(segment_dir),
            "observer_log_dir": str(log_dir),
            "target_csv": str(target_csv),
            "target_csv_sha256": target_sha,
            "ok": bool(eval_payload.get("ok")),
            "status": eval_payload.get("status"),
            "threshold_failures": eval_payload.get("threshold_failures", []),
            "evidence_target_market_count": eval_payload.get("evidence_target_market_count"),
            "ws_disconnect_count": eval_payload.get("ws_disconnect_count"),
            "decision_depth_gap_max_ms": eval_payload.get("decision_depth_gap_max_ms"),
            "pending_depth_never_ready_condition_count": eval_payload.get("pending_depth_never_ready_condition_count"),
        }
        segment_results.append(result)
        write_state(
            state_path,
            {
                "status": STATUS_RUNNING,
                "status_reason": "segment_completed",
                "last_refresh": last_refresh,
                "last_segment_result": result,
                "segment_results": segment_results,
            },
        )

    write_state(
        state_path,
        {
            "status": STATUS_DONE,
            "status_reason": "max_segments_completed",
            "segment_results": segment_results,
            "clean_segment_count": sum(1 for row in segment_results if row.get("ok") is True),
            "failed_segment_count": sum(1 for row in segment_results if row.get("ok") is not True),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the collector-local taker-BUY verification queue.

This worker is intentionally single-concurrency by default.  The point is to
let many research agents submit finalist jobs while keeping replay SQLite access
serialized and collector-local.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEFAULT_QUEUE_ROOT = Path("/home/ubuntu/poly_trans_research/data/verification_queue/taker_buy_strict_l1")
DEFAULT_VERIFIER = Path("/home/ubuntu/poly_trans_research/scripts/verify_taker_buy_search_finalists_shared_replay.py")
DEFAULT_EVENT_STORE_VERIFIER = Path("/home/ubuntu/poly_trans_research/scripts/verify_taker_buy_search_finalists_event_store.py")
DEFAULT_REPO_ROOT = Path("/home/ubuntu/poly_trans_research_ops")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as handle:
        tmp = Path(handle.name)
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp.replace(path)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def ensure_dirs(queue_root: Path) -> None:
    for name in ("incoming", "running", "done", "failed", "logs"):
        (queue_root / name).mkdir(parents=True, exist_ok=True)


def sort_key(path: Path) -> tuple[int, str, str]:
    try:
        job = load_json(path)
        return int(job.get("priority", 100)), str(job.get("created_at_utc", "")), path.name
    except Exception:
        return 9999, "", path.name


def next_job(queue_root: Path) -> Path | None:
    incoming = sorted((queue_root / "incoming").glob("*.json"), key=sort_key)
    return incoming[0] if incoming else None


def heavy_processes(pattern: str) -> list[dict[str, Any]]:
    if not pattern:
        return []
    regex = re.compile(pattern)
    proc = subprocess.run(["ps", "-eo", "pid=,args="], text=True, capture_output=True, check=False)
    matches = []
    for line in proc.stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        pid_text, _, command = stripped.partition(" ")
        try:
            pid = int(pid_text)
        except ValueError:
            continue
        if pid == os.getpid():
            continue
        if "run_taker_buy_verification_queue.py" in command:
            continue
        if regex.search(command):
            matches.append({"pid": pid, "command": command})
    return matches


def wait_for_no_heavy_processes(args: argparse.Namespace) -> None:
    while args.heavy_process_regex:
        matches = heavy_processes(args.heavy_process_regex)
        if not matches:
            return
        print(
            json.dumps(
                {
                    "status": "waiting_heavy_process",
                    "matches": matches[:10],
                    "check_again_in_s": args.heavy_check_seconds,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(args.heavy_check_seconds)


def choose_backend(job: dict[str, Any], args: argparse.Namespace) -> tuple[str, Path]:
    requested = str(job.get("backend") or "auto")
    event_store_dir = job.get("event_store_dir")
    event_store_ok = bool(event_store_dir and Path(str(event_store_dir)).exists())
    if requested == "event_store":
        if not event_store_ok:
            raise FileNotFoundError(f"event_store_dir does not exist: {event_store_dir}")
        return "event_store", args.event_store_verifier_script
    if requested == "shared_replay":
        return "shared_replay", args.verifier_script
    if event_store_ok:
        return "event_store", args.event_store_verifier_script
    return "shared_replay", args.verifier_script


def validate_job(job: dict[str, Any], backend: str) -> None:
    required = ["job_id", "search_results_csv", "candidate_cache_csv", "replay_root", "days", "output_dir"]
    missing = [key for key in required if not job.get(key)]
    if missing:
        raise ValueError(f"job missing required fields: {missing}")
    for key in ("search_results_csv", "candidate_cache_csv", "replay_root"):
        path = Path(str(job[key]))
        if not path.exists():
            raise FileNotFoundError(f"{key} does not exist: {path}")
    if backend == "event_store":
        event_store_dir = Path(str(job.get("event_store_dir") or ""))
        if not event_store_dir.exists():
            raise FileNotFoundError(f"event_store_dir does not exist: {event_store_dir}")


def build_command(job: dict[str, Any], verifier_script: Path, backend: str) -> list[str]:
    cmd = [sys.executable, str(verifier_script), "--search-results-csv", str(job["search_results_csv"])]
    if backend == "event_store":
        cmd.extend(["--event-store-dir", str(job["event_store_dir"])])
    else:
        cmd.extend(
            [
                "--candidate-cache-csv",
                str(job["candidate_cache_csv"]),
                "--replay-root",
                str(job["replay_root"]),
            ]
        )
    cmd.extend(["--days", str(job["days"]), "--output-dir", str(job["output_dir"]), "--progress"])
    ranks = [int(r) for r in job.get("ranks") or []]
    if ranks:
        for rank in ranks:
            cmd.extend(["--rank", str(rank)])
    else:
        cmd.extend(["--top-n", str(int(job.get("top_n") or 20))])
    return cmd


def read_summary(output_dir: Path) -> dict[str, Any] | None:
    candidates = [
        output_dir / "taker_buy_event_store_verification_summary.json",
        output_dir / "taker_buy_shared_replay_verification_summary.json",
    ]
    path = next((candidate for candidate in candidates if candidate.exists()), candidates[0])
    if not path.exists():
        return None
    try:
        data = load_json(path)
        return {
            "summary_path": str(path),
            "elapsed_s": data.get("elapsed_s"),
            "selected_ranks": data.get("selected_ranks"),
            "results": data.get("results", [])[:10],
        }
    except Exception as exc:
        return {"summary_path": str(path), "summary_read_error": str(exc)}


def move_job(src: Path, dst_dir: Path, job: dict[str, Any]) -> Path:
    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / src.name
    atomic_write_json(src, job)
    shutil.move(str(src), str(dst))
    return dst


def run_job(job_file: Path, args: argparse.Namespace) -> dict[str, Any]:
    job = load_json(job_file)
    job_id = str(job.get("job_id") or job_file.stem)
    running_file = args.queue_root / "running" / f"{job_id}.json"
    if running_file.exists():
        running_file.unlink()
    shutil.move(str(job_file), str(running_file))
    log_path = args.queue_root / "logs" / f"{job_id}.log"
    started = time.perf_counter()
    job.update(
        {
            "status": "running",
            "started_at_utc": utc_now(),
            "worker_host": os.uname().nodename,
            "worker_pid": os.getpid(),
            "log_path": str(log_path),
        }
    )
    atomic_write_json(running_file, job)

    try:
        backend, verifier_script = choose_backend(job, args)
        validate_job(job, backend)
        output_dir = Path(str(job["output_dir"]))
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = build_command(job, verifier_script, backend)
        job["command"] = cmd
        job["resolved_backend"] = backend
        atomic_write_json(running_file, job)
        with log_path.open("w", encoding="utf-8") as log:
            log.write(json.dumps({"stage": "start", "job_id": job_id, "started_at_utc": job["started_at_utc"], "command": cmd}) + "\n")
            log.flush()
            proc = subprocess.run(cmd, cwd=args.repo_root, stdout=log, stderr=subprocess.STDOUT, text=True)
        elapsed = round(time.perf_counter() - started, 3)
        summary = read_summary(output_dir)
        job.update(
            {
                "finished_at_utc": utc_now(),
                "elapsed_s": elapsed,
                "returncode": proc.returncode,
                "summary": summary,
                "resolved_backend": backend,
            }
        )
        if proc.returncode == 0:
            job["status"] = "done"
            dst = move_job(running_file, args.queue_root / "done", job)
            return {"job_id": job_id, "status": "done", "elapsed_s": elapsed, "job_file": str(dst), "log_path": str(log_path), "summary": summary}
        job["status"] = "failed"
        job["error"] = f"verifier exited nonzero: {proc.returncode}"
        dst = move_job(running_file, args.queue_root / "failed", job)
        return {"job_id": job_id, "status": "failed", "elapsed_s": elapsed, "job_file": str(dst), "log_path": str(log_path), "error": job["error"]}
    except Exception as exc:
        elapsed = round(time.perf_counter() - started, 3)
        job.update({"status": "failed", "finished_at_utc": utc_now(), "elapsed_s": elapsed, "error": repr(exc)})
        dst = move_job(running_file, args.queue_root / "failed", job)
        with log_path.open("a", encoding="utf-8") as log:
            log.write(json.dumps({"stage": "failed", "job_id": job_id, "error": repr(exc)}) + "\n")
        return {"job_id": job_id, "status": "failed", "elapsed_s": elapsed, "job_file": str(dst), "log_path": str(log_path), "error": repr(exc)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-root", type=Path, default=DEFAULT_QUEUE_ROOT)
    parser.add_argument("--verifier-script", type=Path, default=DEFAULT_VERIFIER)
    parser.add_argument("--event-store-verifier-script", type=Path, default=DEFAULT_EVENT_STORE_VERIFIER)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--once", action="store_true", help="Run at most one queued job, then exit.")
    parser.add_argument("--poll-seconds", type=float, default=20.0)
    parser.add_argument("--max-jobs", type=int, default=0, help="0 means unlimited until --once or interruption.")
    parser.add_argument("--heavy-process-regex", default="", help="If set, wait while any non-worker process command matches this regex.")
    parser.add_argument("--heavy-check-seconds", type=float, default=60.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_dirs(args.queue_root)
    if not args.verifier_script.exists():
        raise SystemExit(f"verifier script not found: {args.verifier_script}")
    if not args.event_store_verifier_script.exists():
        raise SystemExit(f"event-store verifier script not found: {args.event_store_verifier_script}")
    lock_path = args.queue_root / "worker.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "locked", "message": "another verification worker is active", "lock_path": str(lock_path)}))
            return 2
        completed = 0
        while True:
            job_file = next_job(args.queue_root)
            if job_file is None:
                print(json.dumps({"status": "idle", "queue_root": str(args.queue_root), "completed": completed}))
                if args.once:
                    return 0
                time.sleep(args.poll_seconds)
                continue
            wait_for_no_heavy_processes(args)
            result = run_job(job_file, args)
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True), flush=True)
            completed += 1
            if args.once or (args.max_jobs and completed >= args.max_jobs):
                return 0 if result.get("status") == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())

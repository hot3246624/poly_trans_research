#!/usr/bin/env python3
"""Orchestrate ce25/nagi public-activity research iterations.

This wrapper fetches missing market-sequence profiles for fixed windows and
then runs the account autoresearch scorer over the generated profiles.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROFILE_SCRIPT = ROOT / "scripts" / "profile_ce25_execution_pattern.py"
ITERATION_SCRIPT = ROOT / "scripts" / "run_account_autoresearch_iteration.py"
DEFAULT_ACCOUNTS = {
    "ce25": "0xce25e214d5cfe4f459cf67f08df581885aae7fdc",
    "nagi": "0xbf337426aa856996b8bb79b238345dd1a0276bf7",
}
DEFAULT_ACTIVITY_TYPES = "TRADE,MERGE,REDEEM,MAKER_REBATE,SPLIT"


def parse_iso(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def bjt_tag(value: dt.datetime) -> str:
    bjt = value.astimezone(dt.timezone(dt.timedelta(hours=8)))
    return bjt.strftime("%Y%m%d_%H%M")


def parse_account(spec: str) -> tuple[str, str]:
    if "=" not in spec:
        raise SystemExit(f"invalid --account {spec!r}; expected label=0xwallet")
    label, wallet = spec.split("=", 1)
    label = label.strip()
    wallet = wallet.strip().lower()
    if not label or not wallet:
        raise SystemExit(f"invalid --account {spec!r}")
    return label, wallet


def build_windows(start: dt.datetime, end: dt.datetime, step_hours: int) -> list[tuple[dt.datetime, dt.datetime, str]]:
    if end <= start:
        raise SystemExit("--end-iso must be after --start-iso")
    step = dt.timedelta(hours=step_hours)
    windows: list[tuple[dt.datetime, dt.datetime, str]] = []
    cursor = start
    while cursor < end:
        win_end = min(cursor + step, end)
        windows.append((cursor, win_end, f"{bjt_tag(cursor)}_to_{bjt_tag(win_end)}_bjt"))
        cursor = win_end
    return windows


def run(cmd: list[str], cwd: Path, *, dry_run: bool) -> int:
    print("[cmd]", " ".join(cmd), flush=True)
    if dry_run:
        return 0
    return subprocess.run(cmd, cwd=str(cwd), check=True).returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--step-hours", type=int, default=24)
    parser.add_argument("--account", action="append", default=[], help="label=0xwallet; defaults to ce25 and nagi")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--fetch-window-hours", type=int, default=1)
    parser.add_argument("--activity-types", default=DEFAULT_ACTIVITY_TYPES)
    parser.add_argument("--retries", type=int, default=10)
    parser.add_argument("--timeout", type=int, default=35)
    parser.add_argument("--pause-ms", type=int, default=300)
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    start = parse_iso(args.start_iso)
    end = parse_iso(args.end_iso)
    accounts = dict(DEFAULT_ACCOUNTS)
    if args.account:
        accounts = dict(parse_account(spec) for spec in args.account)
    stamp = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d_%H%M%S_bjt")
    output_root = Path(args.output_root) if args.output_root else ROOT / "data" / "exports" / f"ce25_nagi_activity_research_{stamp}"
    profiles_root = output_root / "profiles"
    iteration_dir = output_root / "autoresearch"
    profiles_root.mkdir(parents=True, exist_ok=True)

    windows = build_windows(start, end, args.step_hours)
    profile_specs: list[str] = []
    command_log: list[dict[str, Any]] = []
    for label, wallet in accounts.items():
        for win_start, win_end, tag in windows:
            profile_dir = profiles_root / f"{label}_{tag}"
            profile_specs.append(f"{label}={profile_dir}")
            cmd = [
                sys.executable,
                str(PROFILE_SCRIPT),
                "--user",
                wallet,
                "--start-iso",
                iso_z(win_start),
                "--end-iso",
                iso_z(win_end),
                "--window-hours",
                str(args.fetch_window_hours),
                "--retries",
                str(args.retries),
                "--timeout",
                str(args.timeout),
                "--pause-ms",
                str(args.pause_ms),
                "--activity-types",
                args.activity_types,
                "--output-dir",
                str(profile_dir),
            ]
            command_log.append({"kind": "profile", "account": label, "window": tag, "cmd": cmd})
            if args.skip_fetch or (profile_dir / "summary.json").exists():
                print(f"[skip] {label} {tag}", flush=True)
                continue
            run(cmd, ROOT, dry_run=args.dry_run)

    iteration_cmd = [sys.executable, str(ITERATION_SCRIPT), "--output-dir", str(iteration_dir)]
    for spec in profile_specs:
        if args.dry_run or (Path(spec.split("=", 1)[1]) / "summary.json").exists():
            iteration_cmd.extend(["--profile", spec])
    command_log.append({"kind": "iteration", "cmd": iteration_cmd})
    run(iteration_cmd, ROOT, dry_run=args.dry_run)

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "start_iso": iso_z(start),
        "end_iso": iso_z(end),
        "step_hours": args.step_hours,
        "accounts": accounts,
        "output_root": str(output_root),
        "profiles_root": str(profiles_root),
        "iteration_dir": str(iteration_dir),
        "commands": command_log,
    }
    (output_root / "command_log.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_root": str(output_root), "iteration_dir": str(iteration_dir)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

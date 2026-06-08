#!/usr/bin/env python3
"""Run the xuan evening research refresh pipeline.

This script orchestrates existing research scripts. It does not read raw market
capture and does not write replay DBs. Each child script opens replay SQLite in
read-only mode.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = "2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01"


@dataclass(frozen=True)
class Step:
    name: str
    cmd: list[str]
    outputs: dict[str, str]


def utc_tag() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")


def cmd_text(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def script_cmd(script: str, *args: str) -> list[str]:
    return [sys.executable, script, *args]


def build_steps(args: argparse.Namespace, run_dir: Path) -> list[Step]:
    replay_days = args.days
    common_replay = ["--replay-root", args.replay_root]
    replay_xuan_dir = run_dir / "xuan_replay_extract"
    tranche_dir = run_dir / "xuan_tranche_ladder"
    cycle_dir = run_dir / "xuan_cycle_feature_gate"
    match_dir = run_dir / "xuan_public_trade_match"
    maker_q00_dir = run_dir / "maker_fill_proxy_q00_70s"
    maker_q50_dir = run_dir / "maker_fill_proxy_q50_70s"
    taker_wait30_dir = run_dir / "taker_first_wait30"
    taker_wait50_dir = run_dir / "taker_first_wait50"
    taker_wait70_dir = run_dir / "taker_first_wait70"
    threshold_wait30_dir = run_dir / "bounded_taker_threshold_wait30"
    threshold_wait50_dir = run_dir / "bounded_taker_threshold_wait50"
    threshold_wait70_dir = run_dir / "bounded_taker_threshold_wait70"
    schedule_dir = run_dir / "bounded_taker_schedule"
    hybrid_dir = run_dir / "hybrid_maker_then_bounded_taker"

    extract_cmd = script_cmd(
        "scripts/extract_xuan_trades_from_replay.py",
        "--replay-root",
        args.replay_root,
        "--days",
        replay_days,
        "--output-json",
        str(replay_xuan_dir / "xuan_trades_raw.json"),
        "--summary-json",
        str(replay_xuan_dir / "xuan_trades_from_replay_summary.json"),
    )
    if args.start_iso:
        extract_cmd.extend(["--start-iso", args.start_iso])
    if args.end_iso:
        extract_cmd.extend(["--end-iso", args.end_iso])

    xuan_input_json = args.xuan_input_json or str(replay_xuan_dir / "xuan_trades_raw.json")
    tranche_cmd = script_cmd(
        "scripts/analyze_xuan_tranche_ladder.py",
        "--input-json",
        xuan_input_json,
        "--output-dir",
        str(tranche_dir),
        "--max-rows",
        str(args.max_rows),
        "--latest-window-start-iso",
        args.latest_window_start_iso,
        "--replay-root",
        args.replay_root,
        "--replay-days",
        replay_days,
    )
    if args.start_iso:
        tranche_cmd.extend(["--start-iso", args.start_iso])
    if args.end_iso:
        tranche_cmd.extend(["--end-iso", args.end_iso])

    steps = [
        Step(
            "extract_xuan_trades_from_replay",
            extract_cmd,
            {
                "trades_raw_json": str(replay_xuan_dir / "xuan_trades_raw.json"),
                "summary_json": str(replay_xuan_dir / "xuan_trades_from_replay_summary.json"),
            },
        ),
        Step(
            "xuan_tranche_ladder",
            tranche_cmd,
            {
                "summary_json": str(tranche_dir / "xuan_tranche_ladder_summary.json"),
                "report_md": str(tranche_dir / "xuan_tranche_ladder_report.md"),
                "cycles_csv": str(tranche_dir / "xuan_inventory_cycles.csv"),
                "events_csv": str(tranche_dir / "xuan_inventory_events.csv"),
                "trades_raw_json": str(tranche_dir / "xuan_trades_raw.json"),
            },
        ),
        Step(
            "xuan_cycle_feature_gate",
            script_cmd(
                "scripts/analyze_xuan_cycle_features.py",
                "--cycles-csv",
                str(tranche_dir / "xuan_inventory_cycles.csv"),
                "--replay-root",
                args.replay_root,
                "--replay-days",
                replay_days,
                "--output-dir",
                str(cycle_dir),
            ),
            {
                "summary_json": str(cycle_dir / "xuan_cycle_gate_summary.json"),
                "report_md": str(cycle_dir / "xuan_cycle_gate_report.md"),
                "defaults_json": str(cycle_dir / "xuan_slow_improvement_gate_defaults.json"),
            },
        ),
        Step(
            "xuan_public_trade_match",
            script_cmd(
                "scripts/infer_xuan_public_trade_match.py",
                "--xuan-trades",
                str(tranche_dir / "xuan_trades_raw.json"),
                "--events-csv",
                str(tranche_dir / "xuan_inventory_events.csv"),
                "--replay-root",
                args.replay_root,
                "--days",
                replay_days,
                "--output-dir",
                str(match_dir),
            ),
            {
                "summary_json": str(match_dir / "xuan_public_trade_match_summary.json"),
                "report_md": str(match_dir / "xuan_public_trade_match_report.md"),
            },
        ),
        Step(
            "maker_fill_proxy_q00_70s",
            script_cmd(
                "scripts/backtest_btc5m_high_side_wait_fill_proxy.py",
                *common_replay,
                "--days",
                replay_days,
                "--modes-file",
                args.maker_modes_file,
                "--output-dir",
                str(maker_q00_dir),
                "--first-fill-timeout-s",
                "70",
                "--queue-ahead-fraction",
                "0",
                "--override-wait-budget-s",
                "70",
            ),
            {
                "summary_json": str(maker_q00_dir / "btc5m_high_side_wait_fill_proxy_summary.json"),
                "report_md": str(maker_q00_dir / "btc5m_high_side_wait_fill_proxy_report.md"),
            },
        ),
        Step(
            "maker_fill_proxy_q50_70s",
            script_cmd(
                "scripts/backtest_btc5m_high_side_wait_fill_proxy.py",
                *common_replay,
                "--days",
                replay_days,
                "--modes-file",
                args.maker_modes_file,
                "--output-dir",
                str(maker_q50_dir),
                "--first-fill-timeout-s",
                "70",
                "--queue-ahead-fraction",
                "0.5",
                "--override-wait-budget-s",
                "70",
            ),
            {
                "summary_json": str(maker_q50_dir / "btc5m_high_side_wait_fill_proxy_summary.json"),
                "report_md": str(maker_q50_dir / "btc5m_high_side_wait_fill_proxy_report.md"),
            },
        ),
    ]

    for wait_s, output_dir in [(30, taker_wait30_dir), (50, taker_wait50_dir), (70, taker_wait70_dir)]:
        steps.append(
            Step(
                f"taker_first_wait{wait_s}",
                script_cmd(
                    "scripts/backtest_btc5m_high_side_wait_fill_proxy.py",
                    *common_replay,
                    "--days",
                    replay_days,
                    "--modes-file",
                    args.taker_modes_file,
                    "--output-dir",
                    str(output_dir),
                    "--first-fill-timeout-s",
                    "70",
                    "--queue-ahead-fraction",
                    "0",
                    "--override-wait-budget-s",
                    str(wait_s),
                ),
                {
                    "summary_json": str(output_dir / "btc5m_high_side_wait_fill_proxy_summary.json"),
                    "report_md": str(output_dir / "btc5m_high_side_wait_fill_proxy_report.md"),
                },
            )
        )

    for wait_s, output_dir in [(30, threshold_wait30_dir), (50, threshold_wait50_dir), (70, threshold_wait70_dir)]:
        steps.append(
            Step(
                f"bounded_taker_threshold_wait{wait_s}",
                script_cmd(
                    "scripts/backtest_btc5m_bounded_taker_threshold.py",
                    *common_replay,
                    "--days",
                    replay_days,
                    "--modes-file",
                    args.taker_modes_file,
                    "--output-dir",
                    str(output_dir),
                    "--pair-cost-ceilings",
                    args.pair_cost_ceilings,
                    "--override-wait-budget-s",
                    str(wait_s),
                ),
                {
                    "summary_json": str(output_dir / "btc5m_bounded_taker_threshold_summary.json"),
                    "report_md": str(output_dir / "btc5m_bounded_taker_threshold_report.md"),
                },
            )
        )

    steps.append(
        Step(
            "bounded_taker_schedule",
            script_cmd(
                "scripts/backtest_btc5m_bounded_taker_schedule.py",
                *common_replay,
                "--days",
                replay_days,
                "--modes-file",
                args.taker_modes_file,
                "--output-dir",
                str(schedule_dir),
                "--schedules",
                args.schedules,
            ),
            {
                "summary_json": str(schedule_dir / "btc5m_bounded_taker_schedule_summary.json"),
                "report_md": str(schedule_dir / "btc5m_bounded_taker_schedule_report.md"),
            },
        )
    )

    if args.include_hybrid:
        steps.append(
            Step(
                "hybrid_maker_then_bounded_taker",
                script_cmd(
                    "scripts/backtest_btc5m_high_side_wait_hybrid.py",
                    *common_replay,
                    "--days",
                    replay_days,
                    "--modes-file",
                    args.maker_modes_file,
                    "--output-dir",
                    str(hybrid_dir),
                    "--maker-waits-s",
                    args.hybrid_maker_waits,
                    "--queue-ahead-fractions",
                    args.hybrid_queue_ahead_fractions,
                    "--override-wait-budget-s",
                    "70",
                ),
                {
                    "summary_json": str(hybrid_dir / "btc5m_high_side_wait_hybrid_summary.json"),
                    "report_md": str(hybrid_dir / "btc5m_high_side_wait_hybrid_report.md"),
                },
            )
        )

    if args.compare_to_baseline:
        compare_dir = run_dir / "delta_vs_baseline"
        steps.append(
            Step(
                "compare_to_baseline",
                script_cmd(
                    "scripts/compare_xuan_research_runs.py",
                    "--current-run-dir",
                    str(run_dir),
                    "--output-dir",
                    str(compare_dir),
                ),
                {
                    "summary_json": str(compare_dir / "xuan_research_delta_summary.json"),
                    "report_md": str(compare_dir / "xuan_research_delta_report.md"),
                },
            )
        )

    return steps


def write_runbook(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# Xuan Evening Research Pipeline Runbook",
        "",
        "## Scope",
        "",
        f"- run_dir: `{manifest['run_dir']}`",
        f"- replay_root: `{manifest['parameters']['replay_root']}`",
        f"- days: `{manifest['parameters']['days']}`",
        "- Replay SQLite is read-only through child scripts.",
        "- Raw market capture is not read.",
        "",
        "## Steps",
        "",
        "| step | command |",
        "|---|---|",
    ]
    for step in manifest["steps"]:
        lines.append(f"| {step['name']} | `{step['cmd']}` |")
    lines.extend(
        [
            "",
            "## After Run",
            "",
            "1. Open `manifest.json` and check every step status is `ok`.",
            "2. Open `delta_vs_baseline/xuan_research_delta_report.md` if `--compare-to-baseline` was used.",
            "3. Treat `best-in-window` metrics as opportunity upper bounds; causal threshold/schedule metrics are the implementable view.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_step(step: Step, log_dir: Path, plan_only: bool) -> dict[str, Any]:
    started = time.time()
    result: dict[str, Any] = {
        "name": step.name,
        "cmd": cmd_text(step.cmd),
        "outputs": step.outputs,
        "status": "planned" if plan_only else "running",
        "duration_s": None,
        "log": str(log_dir / f"{step.name}.log"),
    }
    if plan_only:
        return result
    log_dir.mkdir(parents=True, exist_ok=True)
    with (log_dir / f"{step.name}.log").open("w", encoding="utf-8") as log:
        log.write(f"$ {cmd_text(step.cmd)}\n\n")
        proc = subprocess.run(step.cmd, stdout=log, stderr=subprocess.STDOUT, text=True)
    result["duration_s"] = round(time.time() - started, 3)
    result["returncode"] = proc.returncode
    result["status"] = "ok" if proc.returncode == 0 else "failed"
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", default=utc_tag())
    parser.add_argument("--output-root", default="data/exports/xuan_research_runs")
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=DEFAULT_DAYS)
    parser.add_argument("--xuan-input-json")
    parser.add_argument("--max-rows", type=int, default=3000)
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--latest-window-start-iso", default="2026-05-01T00:00:00Z")
    parser.add_argument("--maker-modes-file", default="configs/xuan/high_side_wait_shadow_candidates.json")
    parser.add_argument("--taker-modes-file", default="configs/xuan/high_side_wait_taker_shadow_candidates.json")
    parser.add_argument("--pair-cost-ceilings", default="0.90,0.95,1.00")
    parser.add_argument("--schedules", default="30:0.90,50:0.95,70:1.00;30:0.90,70:0.95;50:0.90,70:0.95")
    parser.add_argument("--include-hybrid", action="store_true")
    parser.add_argument("--hybrid-maker-waits", default="10,20,30")
    parser.add_argument("--hybrid-queue-ahead-fractions", default="0,0.5")
    parser.add_argument("--compare-to-baseline", action="store_true")
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.output_root) / args.tag
    run_dir.mkdir(parents=True, exist_ok=True)
    steps = build_steps(args, run_dir)
    manifest: dict[str, Any] = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "run_dir": str(run_dir.resolve()),
        "plan_only": args.plan_only,
        "parameters": {
            "replay_root": str(Path(args.replay_root).resolve()),
            "days": args.days,
            "xuan_input_json": args.xuan_input_json,
            "latest_window_start_iso": args.latest_window_start_iso,
            "maker_modes_file": args.maker_modes_file,
            "taker_modes_file": args.taker_modes_file,
            "pair_cost_ceilings": args.pair_cost_ceilings,
            "schedules": args.schedules,
            "include_hybrid": args.include_hybrid,
        },
        "steps": [],
    }
    write_runbook(run_dir / "RUNBOOK.md", {**manifest, "steps": [{"name": s.name, "cmd": cmd_text(s.cmd)} for s in steps]})

    exit_code = 0
    for step in steps:
        result = run_step(step, run_dir / "logs", args.plan_only)
        manifest["steps"].append(result)
        (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"step": step.name, "status": result["status"], "duration_s": result["duration_s"]}, ensure_ascii=False))
        if result["status"] == "failed":
            exit_code = 1
            if not args.continue_on_error:
                break

    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"run_dir": str(run_dir), "status": "planned" if args.plan_only else ("failed" if exit_code else "ok")}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Run the Fast-Cancel replay-to-shadow pipeline from a config file.

Primary data source is replay SQLite. The script invokes the existing replay
backtest and combo tools, then emits shadow-shaped events and a summary report.
It does not read raw data and does not modify replay DBs.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path("configs/xuan/fastcancel_shadow_sidecar_v1.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--replay-root", type=Path, help="Override config data_window.replay_root")
    parser.add_argument("--days", help="Comma-separated UTC days. Requires --rebuild-rows when overriding config days.")
    parser.add_argument("--output-root", type=Path, default=Path("data/exports"))
    parser.add_argument("--tag", default="", help="Optional output tag suffix")
    parser.add_argument(
        "--rebuild-rows",
        action="store_true",
        help="Rebuild early/late rows from replay. Default reuses config.source_outputs.leader selected rows.",
    )
    parser.add_argument("--max-markets", type=int, default=0, help="Optional smoke cap forwarded to row backtests.")
    parser.add_argument("--skip-existing", action="store_true", help="Skip row generation when output CSV exists")
    parser.add_argument("--require-replay-ready", action="store_true", help="Forward gate requirement to shadow report.")
    parser.add_argument("--require-enforce-ready", action="store_true", help="Forward enforce gate requirement to shadow report.")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running")
    return parser.parse_args()


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def run(cmd: list[str], *, dry_run: bool) -> None:
    print(" ".join(cmd))
    if dry_run:
        return
    subprocess.run(cmd, check=True)


def run_with_exit_code(cmd: list[str], *, dry_run: bool) -> int:
    print(" ".join(cmd))
    if dry_run:
        return 0
    return subprocess.run(cmd, check=False).returncode


def suffix(window: dict[str, Any], *, clip: int, suffix_tag: str) -> str:
    name = str(window["name"]).replace("early_", "early").replace("mid_", "mid")
    return f"{name}_clip{clip}{suffix_tag}"


def output_dir_for(output_root: Path, short_days: str, suffix_value: str) -> Path:
    return output_root / f"backtest_btc5m_maker_fill_triggered_{short_days}_{suffix_value}"


def days_short(days: list[str]) -> str:
    first = days[0][5:].replace("-", "")
    last = days[-1][5:].replace("-", "")
    return f"{first}_{last}"


def parse_days(value: str | None, config_days: list[str]) -> list[str]:
    if value is None:
        return config_days
    days = [day.strip() for day in value.split(",") if day.strip()]
    if not days:
        raise SystemExit("--days was provided but no valid day was found")
    return days


def dynamic_rule_to_combo_rule(condition: str | None) -> str:
    if not condition:
        return "none"
    parts = condition.strip().split()
    if len(parts) != 3:
        raise SystemExit(f"unsupported dynamic_upclip.condition: {condition!r}")
    field, op, value = parts
    op_map = {">=": "ge", ">": "gt", "<=": "le", "<": "lt"}
    if op not in op_map:
        raise SystemExit(f"unsupported dynamic_upclip.condition operator: {op!r}")
    return f"{field}:{op_map[op]}:{value}"


def common_backtest_args(
    *,
    replay_root: Path,
    days_csv: str,
    output_dir: Path,
    window: dict[str, Any],
    clip: int,
    strategy: dict[str, Any],
    max_markets: int = 0,
) -> list[str]:
    first_leg = strategy["first_leg"]
    completion = strategy["completion_controller"]
    primary = completion["primary"]
    slow_path = completion["slow_path"]
    repair = completion["repair"]

    cmd = [
        sys.executable,
        "scripts/backtest_btc5m_maker_fill_triggered.py",
        "--replay-root",
        str(replay_root),
        "--days",
        days_csv,
        "--output-dir",
        str(output_dir),
        "--sample-interval-s",
        "1",
        "--price-offsets",
        "0",
        "--fill-models",
        "queue_full",
        "--clip",
        str(float(clip)),
        "--min-offset-s",
        str(int(window["min_offset_s"])),
        "--max-offset-s",
        str(int(window["max_offset_s"])),
        "--min-side-bid",
        str(float(window["min_side_bid"])),
        "--max-side-bid",
        str(float(window["max_side_bid"])),
        "--max-spread-ticks",
        str(float(window["max_spread_ticks"])),
        "--min-prev-bid-delta-1s",
        str(float(window["min_prev_bid_delta_1s"])),
        "--max-top-bid-sz",
        str(float(window["max_top_bid_sz"])),
        "--first-fill-timeout-s",
        str(int(first_leg["fill_timeout_s"])),
        "--completion-pair-ceiling",
        str(float(primary["pair_cost_ceiling"])),
        "--completion-deadline-s",
        str(int(primary["deadline_s"])),
        "--slow-continue-evidence-ceiling",
        str(float(slow_path["allow_if_min_pair_cost_seen_30s_lte"])),
        "--slow-completion-pair-ceiling",
        str(float(slow_path["pair_cost_ceiling"])),
        "--slow-completion-deadline-s",
        str(int(slow_path["deadline_s"])),
        "--repair-pair-ceiling",
        str(float(repair["pair_cost_ceiling"])),
        "--repair-deadline-s",
        str(int(repair["deadline_s"])),
        "--cooldown-s",
        str(int(strategy["state_machine"]["cooldown_after_close_s"])),
        "--tail-freeze-s",
        str(int(strategy["state_machine"]["tail_freeze_s"])),
    ]
    if "max_opp_spread_ticks" in window:
        cmd.extend(["--max-opp-spread-ticks", str(float(window["max_opp_spread_ticks"]))])
    if "max_immediate_pair_cost" in window:
        cmd.extend(["--max-immediate-pair-cost", str(float(window["max_immediate_pair_cost"]))])
    if max_markets > 0:
        cmd.extend(["--max-markets", str(max_markets)])
    return cmd


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    strategy = config["strategy"]
    config_days = list(config["data_window"]["days"])
    days = parse_days(args.days, config_days)
    if args.days is not None and not args.rebuild_rows:
        raise SystemExit("--days override requires --rebuild-rows; reuse mode is tied to config.source_outputs.leader")
    days_csv = ",".join(days)
    short = days_short(days)
    replay_root = args.replay_root or Path(config["data_window"]["replay_root"])
    suffix_tag = f"_{args.tag}" if args.tag else "_pipeline"
    base_clip = int(strategy["sizing"]["base_clip"])
    windows = strategy["open_windows"]
    if not windows:
        raise SystemExit("strategy.open_windows must contain at least one window")
    dynamic_upclip = strategy["sizing"].get("dynamic_upclip", {})
    dynamic_enabled = bool(dynamic_upclip.get("enabled"))
    upclip = int(dynamic_upclip["effective_clip"]) if dynamic_enabled else None
    dynamic_rule = dynamic_rule_to_combo_rule(dynamic_upclip.get("condition")) if dynamic_enabled else "none"
    window_suffixes = [(str(window["name"]), suffix(window, clip=base_clip, suffix_tag=suffix_tag)) for window in windows]
    upclip_suffixes: list[tuple[str, str]] = []
    if dynamic_enabled:
        upclip_suffixes = [(str(window["name"]), suffix(window, clip=int(upclip), suffix_tag=suffix_tag)) for window in windows]

    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    combo_dir = args.output_root / f"fastcancel_shadow_replay_{short}{suffix_tag}"
    event_dir = combo_dir / "shadow_events"

    if not args.rebuild_rows:
        if "source_outputs" not in config or "leader" not in config["source_outputs"]:
            raise SystemExit("config has no source_outputs.leader; pass --rebuild-rows to generate replay rows")
        leader_dir = Path(config["source_outputs"]["leader"])
        selected_csv = leader_dir / "dual_window_fastcancel_combo_selected_rows.csv"
        leader_summary = leader_dir / "dual_window_fastcancel_combo_summary.json"
        if not selected_csv.exists():
            raise SystemExit(f"leader selected rows not found: {selected_csv}; pass --rebuild-rows to regenerate")
        combo_dir.mkdir(parents=True, exist_ok=True)
        copied_csv = combo_dir / "dual_window_fastcancel_combo_selected_rows.csv"
        copied_summary = combo_dir / "dual_window_fastcancel_combo_summary.json"
        if not args.dry_run:
            shutil.copy2(selected_csv, copied_csv)
            if leader_summary.exists():
                shutil.copy2(leader_summary, copied_summary)
        else:
            print(f"copy {selected_csv} -> {copied_csv}")
            if leader_summary.exists():
                print(f"copy {leader_summary} -> {copied_summary}")
        events_cmd = [
            sys.executable,
            "scripts/emit_fastcancel_shadow_events_from_replay.py",
            "--config",
            str(args.config),
            "--input-csv",
            str(copied_csv),
            "--output-dir",
            str(event_dir),
        ]
        run(events_cmd, dry_run=args.dry_run)
        report_cmd = [
            sys.executable,
            "scripts/summarize_fastcancel_shadow_events.py",
            "--events",
            str(event_dir / "fastcancel_shadow_events.jsonl"),
            "--config",
            str(args.config),
            "--combo-summary",
            str(copied_summary),
            "--output-json",
            str(event_dir / "fastcancel_shadow_report.json"),
            "--output-md",
            str(event_dir / "fastcancel_shadow_report.md"),
        ]
        if args.require_replay_ready:
            report_cmd.append("--require-replay-ready")
        if args.require_enforce_ready:
            report_cmd.append("--require-enforce-ready")
        report_rc = run_with_exit_code(report_cmd, dry_run=args.dry_run)
        if report_rc != 0:
            return report_rc
        pipeline_summary = {
            "generated_at": generated_at,
            "mode": "reuse_leader_selected_rows",
            "config": str(args.config),
            "leader_dir": str(leader_dir),
            "selected_csv": str(selected_csv),
            "combo_summary": str(leader_summary) if leader_summary.exists() else None,
            "days": days,
            "output_dir": str(combo_dir),
            "events_dir": str(event_dir),
            "commands_dry_run": bool(args.dry_run),
        }
        if not args.dry_run:
            (combo_dir / "fastcancel_shadow_replay_pipeline_summary.json").write_text(
                json.dumps(pipeline_summary, indent=2, sort_keys=True) + "\n"
            )
        print(json.dumps(pipeline_summary, indent=2, sort_keys=True))
        return 0

    row_jobs: list[tuple[dict[str, Any], int, str]] = []
    row_jobs.extend((window, base_clip, suffix_value) for window, (_, suffix_value) in zip(windows, window_suffixes))
    if dynamic_enabled:
        row_jobs.extend((window, int(upclip), suffix_value) for window, (_, suffix_value) in zip(windows, upclip_suffixes))

    for window, clip, suffix_value in row_jobs:
        out_dir = output_dir_for(args.output_root, short, suffix_value)
        rows_csv = out_dir / "btc5m_maker_fill_triggered_rows.csv"
        if args.skip_existing and rows_csv.exists():
            print(f"skip existing {rows_csv}")
            continue
        run(
            common_backtest_args(
                replay_root=replay_root,
                days_csv=days_csv,
                output_dir=out_dir,
                window=window,
                clip=clip,
                strategy=strategy,
                max_markets=args.max_markets,
            ),
            dry_run=args.dry_run,
        )

    combo_cmd = [
        sys.executable,
        "scripts/analyze_dual_window_fastcancel_combo.py",
        "--replay-root",
        str(replay_root),
        "--output-root",
        str(args.output_root),
        "--output-dir",
        str(combo_dir),
        "--days",
        days_csv,
        "--cooldown-ms",
        str(int(strategy["state_machine"]["cooldown_after_close_s"]) * 1000),
        "--l2-completion-exit-delay-s",
        str(int(strategy["residual_exit"]["default_exit_delay_s"])),
        "--l2-residual-exit-policy",
        "price_lt_050_180_else_default",
        "--l2-completion-slippage",
        "0,0.005,0.01,0.02,0.03,0.04,0.05",
    ]
    for kind, suffix_value in window_suffixes:
        combo_cmd.extend(["--window-suffix", f"{kind}={suffix_value}"])
    if dynamic_enabled:
        combo_cmd.extend(["--dynamic-upclip-rule", dynamic_rule])
        for kind, suffix_value in upclip_suffixes:
            combo_cmd.extend(["--upclip-window-suffix", f"{kind}={suffix_value}"])
    run(combo_cmd, dry_run=args.dry_run)

    events_cmd = [
        sys.executable,
        "scripts/emit_fastcancel_shadow_events_from_replay.py",
        "--config",
        str(args.config),
        "--input-csv",
        str(combo_dir / "dual_window_fastcancel_combo_selected_rows.csv"),
        "--output-dir",
        str(event_dir),
    ]
    run(events_cmd, dry_run=args.dry_run)

    report_cmd = [
        sys.executable,
        "scripts/summarize_fastcancel_shadow_events.py",
        "--events",
        str(event_dir / "fastcancel_shadow_events.jsonl"),
        "--config",
        str(args.config),
        "--combo-summary",
        str(combo_dir / "dual_window_fastcancel_combo_summary.json"),
        "--output-json",
        str(event_dir / "fastcancel_shadow_report.json"),
        "--output-md",
        str(event_dir / "fastcancel_shadow_report.md"),
    ]
    if args.require_replay_ready:
        report_cmd.append("--require-replay-ready")
    if args.require_enforce_ready:
        report_cmd.append("--require-enforce-ready")
    report_rc = run_with_exit_code(report_cmd, dry_run=args.dry_run)
    if report_rc != 0:
        return report_rc

    pipeline_summary = {
        "generated_at": generated_at,
        "mode": "rebuild_rows_from_replay",
        "config": str(args.config),
        "replay_root": str(replay_root),
        "days": days,
        "output_dir": str(combo_dir),
        "events_dir": str(event_dir),
        "commands_dry_run": bool(args.dry_run),
        "max_markets": int(args.max_markets),
        "window_suffixes": dict(window_suffixes),
        "upclip_suffixes": dict(upclip_suffixes),
        "dynamic_upclip_rule": dynamic_rule,
    }
    if not args.dry_run:
        combo_dir.mkdir(parents=True, exist_ok=True)
        (combo_dir / "fastcancel_shadow_replay_pipeline_summary.json").write_text(
            json.dumps(pipeline_summary, indent=2, sort_keys=True) + "\n"
        )
    print(json.dumps(pipeline_summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Refresh the xuan Backtest V1 research control-plane artifacts in order."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OUTPUT_DIR = DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_backtest_v1_refresh_latest"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run_step(name: str, cmd: list[str], cwd: Path, stop_on_error: bool) -> dict[str, Any]:
    started = time.monotonic()
    proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=False)
    elapsed = round(time.monotonic() - started, 3)
    row = {
        "name": name,
        "command": cmd,
        "returncode": proc.returncode,
        "elapsed_s": elapsed,
        "ok": proc.returncode == 0,
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
    }
    if proc.returncode != 0 and stop_on_error:
        raise RuntimeError(json.dumps(row, ensure_ascii=False, indent=2))
    return row


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--continue-on-error", action="store_true")
    parser.add_argument("--run-tests", action="store_true")
    parser.add_argument("--skip-heavy-l2-rescue", action="store_true")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    data_root = args.data_root.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    py = sys.executable
    stop_on_error = not args.continue_on_error

    steps: list[tuple[str, list[str]]] = []
    if not args.skip_heavy_l2_rescue:
        steps.append(("multiasset_strict_rescue", [py, "scripts/build_multiasset_strict_rescue_opportunity_report.py"]))
    steps.extend(
        [
            ("multiasset_merge_turnover", [py, "scripts/build_multiasset_merge_turnover_report.py"]),
            ("multiasset_coverage_scorecard", [py, "scripts/build_multiasset_backtest_coverage_scorecard.py"]),
            ("xuan_completion_candidate_rescore", [py, "scripts/build_xuan_completion_candidate_rescore.py"]),
            ("xuan_capital_ledger", [py, "scripts/build_xuan_capital_ledger_report.py"]),
            ("btc_merge_turnover", [py, "scripts/build_btc_merge_turnover_report.py"]),
            ("btc_parity_gate", [py, "scripts/build_backtest_v1_btc_parity_gate.py"]),
            ("xuan_bridge_scorecard", [py, "scripts/build_xuan_bridge_scorecard.py"]),
            (
                "pre_strategy_local_install_validation",
                [
                    py,
                    "scripts/validate_multiasset_backtest_v1_local_install.py",
                    "--strict-duckdb",
                    "--output-json",
                    str(data_root / "derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json"),
                ],
            ),
            ("xuan_strategy_readiness_gate", [py, "scripts/build_xuan_backtest_v1_strategy_readiness_gate.py"]),
            (
                "final_local_install_validation",
                [
                    py,
                    "scripts/validate_multiasset_backtest_v1_local_install.py",
                    "--strict-duckdb",
                    "--output-json",
                    str(data_root / "derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json"),
                ],
            ),
        ]
    )
    if args.run_tests:
        steps.append(("pytest", [py, "-m", "pytest"]))

    results: list[dict[str, Any]] = []
    for name, cmd in steps:
        print(f"[{utc_now()}] {name}", flush=True)
        try:
            results.append(run_step(name, cmd, repo_root, stop_on_error))
        except RuntimeError as exc:
            results.append({"name": name, "ok": False, "error": str(exc)})
            break

    install_gate = read_json(data_root / "derived/contract_examples/multiasset_backtest_v1_local_install_validation_latest.json")
    readiness_gate = read_json(
        data_root / "derived/contract_examples/xuan_backtest_v1_strategy_readiness_latest/XUAN_BACKTEST_V1_STRATEGY_READINESS_GATE.json"
    )
    rescore = read_json(
        data_root / "derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json"
    )
    capital = read_json(data_root / "derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json")
    btc_parity = read_json(
        data_root / "derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json"
    )
    failed = [row for row in results if not row.get("ok")]
    summary = {
        "step_count": len(results),
        "failed_step_count": len(failed),
        "failed_steps": [row.get("name") for row in failed],
        "install_gate_status": install_gate.get("status"),
        "install_fail_count": (install_gate.get("summary") or {}).get("fail_count"),
        "install_warn_count": (install_gate.get("summary") or {}).get("warn_count"),
        "xuan_strategy_readiness_status": readiness_gate.get("status"),
        "strategy_research_ready": readiness_gate.get("strategy_research_ready"),
        "strategy_promotion_ready": readiness_gate.get("strategy_promotion_ready"),
        "private_truth_ready": readiness_gate.get("private_truth_ready"),
        "positive_xuan_candidate_count": (rescore.get("summary") or {}).get("positive_xuan_candidate_count"),
        "max_capital_tied": (capital.get("summary") or {}).get("max_capital_tied"),
        "daily_capacity_estimate_at_1000": (capital.get("summary") or {}).get("daily_capacity_estimate_at_notional"),
        "btc_parity_status": btc_parity.get("status"),
    }
    manifest = {
        "schema_version": "xuan_backtest_v1_research_refresh_v1",
        "created_utc": utc_now(),
        "status": "OK_XUAN_BACKTEST_V1_RESEARCH_REFRESH" if not failed else "FAILED_XUAN_BACKTEST_V1_RESEARCH_REFRESH",
        "data_root": str(data_root),
        "summary": summary,
        "steps": results,
    }
    manifest_path = output_dir / "XUAN_BACKTEST_V1_RESEARCH_REFRESH_SUMMARY.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "summary": summary, "manifest": str(manifest_path)}, indent=2, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())

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
XUAN_FRONTIER_REPO = Path("/Users/hot/web3Scientist/pm_as_ofi-xuan-frontier")


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
            ("btc_semantic_alignment", [py, "scripts/build_btc_parity_semantic_alignment_experiment.py"]),
            (
                "btc_v1_old_baseline_overlap_decomposition",
                [py, "scripts/build_btc_v1_old_baseline_overlap_decomposition.py"],
            ),
            ("btc_tiny_canary_preflight", [py, "scripts/build_btc_same_window_canary_preflight.py"]),
            (
                "btc_tiny_canary_public_l2_no_order_shadow_report",
                [py, "scripts/build_xuan_btc_tiny_canary_public_l2_no_order_shadow_report.py"],
            ),
            (
                "btc_tiny_canary_no_order_shadow_eval",
                [
                    py,
                    "scripts/evaluate_xuan_btc_tiny_canary_no_order_shadow.py",
                    "--report-dir",
                    str(data_root / "derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_report_latest"),
                ],
            ),
            ("btc_parity_gate", [py, "scripts/build_backtest_v1_btc_parity_gate.py"]),
            ("xuan_bridge_scorecard", [py, "scripts/build_xuan_bridge_scorecard.py"]),
            (
                "xuan_same_window_shadow_start_preflight",
                [py, "scripts/build_xuan_same_window_shadow_start_preflight.py"],
            ),
            (
                "xuan_same_window_no_order_shadow_manual_approval_packet",
                [py, str(XUAN_FRONTIER_REPO / "scripts/xuan_same_window_no_order_shadow_approval_packet.py")],
            ),
            (
                "xuan_same_window_no_order_shadow_manual_approval_decision",
                [py, "scripts/apply_xuan_same_window_no_order_shadow_manual_approval.py"],
            ),
            (
                "xuan_same_window_real_ws_no_order_shadow_eval",
                [py, str(XUAN_FRONTIER_REPO / "scripts/evaluate_xuan_same_window_no_order_shadow.py")],
            ),
            (
                "xuan_same_window_real_ws_no_order_shadow_start_scope_eval",
                [
                    py,
                    str(XUAN_FRONTIER_REPO / "scripts/evaluate_xuan_same_window_no_order_shadow.py"),
                    "--runner-report-dir",
                    str(
                        data_root
                        / "derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_report_latest"
                    ),
                    "--output-dir",
                    str(
                        data_root
                        / "derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest"
                    ),
                    "--min-row-count",
                    "12",
                    "--min-candidate-count",
                    "12",
                    "--min-market-count",
                    "3",
                    "--scope-kind",
                    "start_scope",
                ],
            ),
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
    rescore_handoff = rescore.get("same_window_handoff") or {}
    capital = read_json(data_root / "derived/contract_examples/xuan_capital_ledger_latest/XUAN_CAPITAL_LEDGER_REPORT.json")
    btc_parity = read_json(
        data_root / "derived/contract_examples/backtest_v1_btc_parity_latest/BACKTEST_V1_BTC_PARITY_GATE.json"
    )
    btc_semantic_alignment = read_json(
        data_root
        / "derived/contract_examples/btc_parity_semantic_alignment_latest/BTC_PARITY_SEMANTIC_ALIGNMENT_EXPERIMENT.json"
    )
    btc_semantic_alignment_summary = btc_semantic_alignment.get("summary") or {}
    btc_tiny_canary = read_json(
        data_root
        / "derived/contract_examples/btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/manifest.json"
    )
    btc_tiny_canary_summary = btc_tiny_canary.get("summary") or {}
    btc_tiny_canary_shadow_eval = read_json(
        data_root
        / "derived/contract_examples/xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json"
    )
    btc_tiny_canary_shadow_eval_summary = btc_tiny_canary_shadow_eval.get("summary") or {}
    real_no_order_shadow_eval = read_json(
        data_root
        / "derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_runner_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json"
    )
    real_no_order_shadow_eval_summary = real_no_order_shadow_eval.get("summary") or {}
    real_no_order_shadow_start_scope_eval = read_json(
        data_root
        / "derived/contract_examples/xuan_same_window_no_order_shadow_real_ws_start_scope_eval_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_EVAL.json"
    )
    real_no_order_shadow_start_scope_eval_summary = (
        real_no_order_shadow_start_scope_eval.get("summary") or {}
    )
    btc_overlap_decomposition = read_json(
        data_root
        / "derived/contract_examples/btc_v1_old_baseline_overlap_decomposition_latest/BTC_V1_OLD_BASELINE_OVERLAP_DECOMPOSITION_REPORT.json"
    )
    btc_overlap_buckets = {
        row.get("decomposition_bucket"): row for row in btc_overlap_decomposition.get("buckets") or []
    }
    shadow_start_preflight = read_json(
        data_root
        / "derived/contract_examples/xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json"
    )
    manual_approval_packet = read_json(
        data_root
        / "derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_packet_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json"
    )
    manual_approval_decision = read_json(
        data_root
        / "derived/contract_examples/xuan_same_window_no_order_shadow_manual_approval_decision_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json"
    )
    shadow_start_layer = (readiness_gate.get("readiness_layers") or {}).get("shadow_start_ready") or {}
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
        "xuan_same_window_handoff_market_count": rescore_handoff.get("handoff_market_count"),
        "xuan_same_window_handoff_action_rows": rescore_handoff.get("handoff_action_rows"),
        "xuan_same_window_handoff_residual_lot_rows": rescore_handoff.get("handoff_residual_lot_rows"),
        "max_capital_tied": (capital.get("summary") or {}).get("max_capital_tied"),
        "daily_capacity_estimate_at_1000": (capital.get("summary") or {}).get("daily_capacity_estimate_at_notional"),
        "btc_parity_status": btc_parity.get("status"),
        "btc_semantic_alignment_status": btc_semantic_alignment.get("status"),
        "btc_semantic_alignment_old_action_match_rate": btc_semantic_alignment_summary.get(
            "primary_old_action_match_rate"
        ),
        "btc_semantic_alignment_new_action_match_rate": btc_semantic_alignment_summary.get(
            "primary_new_action_match_rate"
        ),
        "btc_tiny_canary_preflight_status": btc_tiny_canary.get("status"),
        "btc_tiny_canary_preflight_ready": btc_tiny_canary.get("canary_preflight_ready"),
        "btc_tiny_canary_start_ready": btc_tiny_canary.get("tiny_canary_start_ready"),
        "btc_tiny_canary_candidate_count": btc_tiny_canary_summary.get("candidate_count"),
        "btc_tiny_canary_zero_stress_after_fee_pnl": btc_tiny_canary_summary.get("zero_stress_after_fee_pnl"),
        "btc_tiny_canary_recommended_max_notional_cap": btc_tiny_canary_summary.get(
            "recommended_max_notional_cap"
        ),
        "btc_tiny_canary_no_order_shadow_eval_status": btc_tiny_canary_shadow_eval.get("status"),
        "btc_tiny_canary_no_order_shadow_eval_passed": btc_tiny_canary_shadow_eval_summary.get(
            "evaluation_passed"
        ),
        "btc_tiny_canary_no_order_shadow_eval_failed_thresholds": btc_tiny_canary_shadow_eval_summary.get(
            "failed_thresholds"
        ),
        "btc_overlap_decomposition_status": btc_overlap_decomposition.get("status"),
        "btc_overlap_fee_after_pnl": (btc_overlap_buckets.get("old_baseline_overlap") or {}).get(
            "fee_after_pnl"
        ),
        "btc_new_only_fee_after_pnl": (btc_overlap_buckets.get("v1_normalized_new_only") or {}).get(
            "fee_after_pnl"
        ),
        "btc_v1_all_fee_after_pnl": (btc_overlap_buckets.get("v1_normalized_all") or {}).get(
            "fee_after_pnl"
        ),
        "shadow_start_preflight_status": shadow_start_preflight.get("status"),
        "shadow_start_engineering_preflight_ready": shadow_start_preflight.get("engineering_preflight_ready"),
        "shadow_start_preconditions_met": shadow_start_layer.get("preconditions_met"),
        "shadow_start_ready": readiness_gate.get("shadow_start_ready"),
        "shadow_start_remaining_blockers": shadow_start_layer.get("blockers"),
        "manual_approval_packet_status": manual_approval_packet.get("status"),
        "manual_approval_packet_ready": manual_approval_packet.get("approval_packet_ready"),
        "manual_approval_decision_status": manual_approval_decision.get("status"),
        "manual_approval_granted": manual_approval_decision.get("manual_approval_granted"),
        "runner_start_allowed_by_approval_gate": manual_approval_decision.get(
            "runner_start_allowed_by_approval_gate"
        ),
        "real_no_order_shadow_eval_status": real_no_order_shadow_eval.get("status"),
        "real_no_order_shadow_eval_passed": real_no_order_shadow_eval_summary.get("evaluation_passed"),
        "real_no_order_shadow_contract_passed": readiness_gate.get("real_no_order_shadow_contract_passed"),
        "real_no_order_shadow_sample_sufficient": readiness_gate.get("real_no_order_shadow_sample_sufficient"),
        "real_no_order_shadow_row_count": real_no_order_shadow_eval_summary.get("row_count"),
        "real_no_order_shadow_candidate_count": real_no_order_shadow_eval_summary.get("candidate_count"),
        "real_no_order_shadow_market_count": real_no_order_shadow_eval_summary.get("market_count"),
        "real_no_order_shadow_start_scope_eval_status": real_no_order_shadow_start_scope_eval.get("status"),
        "real_no_order_shadow_start_scope_validated": readiness_gate.get(
            "real_no_order_shadow_start_scope_validated"
        ),
        "real_no_order_shadow_start_scope_row_count": real_no_order_shadow_start_scope_eval_summary.get(
            "row_count"
        ),
        "real_no_order_shadow_start_scope_candidate_count": real_no_order_shadow_start_scope_eval_summary.get(
            "candidate_count"
        ),
        "real_no_order_shadow_start_scope_market_count": real_no_order_shadow_start_scope_eval_summary.get(
            "market_count"
        ),
        "real_no_order_shadow_start_scope_start_authorizing": real_no_order_shadow_start_scope_eval_summary.get(
            "start_authorizing"
        ),
        "real_no_order_shadow_start_scope_ws_start_scope_validated": real_no_order_shadow_start_scope_eval_summary.get(
            "ws_start_scope_validated"
        ),
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

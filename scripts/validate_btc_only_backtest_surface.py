#!/usr/bin/env python3
"""Validate the restored BTC-only backtest surface.

This gate intentionally reads only local source files and published manifest
JSON/CSV summaries. It does not scan raw, replay, collector, AWS, or remote
stores. The result is research-only readiness, not deployability.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import py_compile
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPECTED_STRICT_LABELS = (
    "20260502_20260507",
    "20260508",
    "20260509",
    "20260510",
    "20260511",
    "20260512",
    "20260513",
    "20260516",
    "20260517",
    "20260518",
)
BLOCKED_DAYS = {"2026-05-14", "2026-05-15", "2026-05-19"}
REQUIRED_BTC_ONLY_SCRIPTS = (
    "scripts/backtest_btc5m_high_side_wait.py",
    "scripts/backtest_btc5m_market_side.py",
    "scripts/backtest_btc5m_maker_first_proxy.py",
    "scripts/search_btc5m_high_side_wait_params.py",
    "scripts/run_completion_candidate_state_machine.py",
)
DISALLOWED_V1_SURFACES = (
    "docs/BACKTEST_ARCHITECTURE_V1_RUNBOOK_ZH.md",
    "configs/backtest",
    "scripts/run_backtest_experiment_suite.py",
    "scripts/build_backtest_candidate_audit_pack.py",
    "scripts/validate_backtest_search_configs.py",
    "src/completion_first_data/backtest",
    "tests/test_backtest_experiment_suite.py",
    ".tmp/BACKTEST_DATASET_REGISTRY.jsonl",
    ".tmp/BACKTEST_RUN_REGISTRY.jsonl",
    ".tmp/backtest_architecture_runs",
    ".tmp/backtest_architecture_state",
)
COMPLETION_PIPELINE = (
    "derived/completion_candidate_pipeline_v1/"
    "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def add_issue(issues: list[dict[str, str]], severity: str, path: Path | str, message: str) -> None:
    issues.append({"severity": severity, "path": str(path), "message": message})


def check_v1_surfaces(repo_root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    present = []
    for rel in DISALLOWED_V1_SURFACES:
        p = repo_root / rel
        if p.exists():
            present.append(rel)
            add_issue(issues, "fail", p, "V1/multiasset backtest surface is present")
    return {"disallowed_count": len(DISALLOWED_V1_SURFACES), "present": present}


def check_required_scripts(repo_root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    compiled = []
    for rel in REQUIRED_BTC_ONLY_SCRIPTS:
        p = repo_root / rel
        if not p.exists():
            add_issue(issues, "fail", p, "required BTC-only script is missing")
            continue
        try:
            py_compile.compile(str(p), doraise=True)
        except py_compile.PyCompileError as exc:
            add_issue(issues, "fail", p, f"py_compile failed: {exc.msg}")
        else:
            compiled.append(rel)
    return {"required": list(REQUIRED_BTC_ONLY_SCRIPTS), "compiled": compiled}


def check_strict_cache(data_root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    base = data_root / "backtest_cache/taker_buy_signal_core_v2_strict_l1"
    labels: list[str] = []
    total_rows = 0
    day_counts: dict[str, int] = {}
    validation_error_count = 0
    if not base.exists():
        add_issue(issues, "fail", base, "strict V2 cache root is missing")
        return {"root": str(base), "labels": labels, "total_rows": total_rows, "day_counts": day_counts}

    for label in EXPECTED_STRICT_LABELS:
        manifest_path = base / label / "CACHE_MANIFEST.json"
        if not manifest_path.exists():
            add_issue(issues, "fail", manifest_path, "strict V2 cache manifest is missing")
            continue
        manifest = read_json(manifest_path)
        labels.append(label)
        if manifest.get("cache_name") != "taker_buy_signal_core_v2_strict_l1":
            add_issue(issues, "fail", manifest_path, "unexpected cache_name")
        if manifest.get("schema_version") != 2:
            add_issue(issues, "fail", manifest_path, "unexpected schema_version")
        days = set(manifest.get("days") or [])
        blocked = sorted(days & BLOCKED_DAYS)
        if blocked:
            add_issue(issues, "fail", manifest_path, f"blocked days included: {blocked}")
        outputs = manifest.get("outputs") or {}
        row_count = int(outputs.get("row_count") or 0)
        if row_count <= 0:
            add_issue(issues, "fail", manifest_path, "cache row_count is not positive")
        total_rows += row_count
        for day, count in (outputs.get("day_counts") or {}).items():
            day_counts[day] = day_counts.get(day, 0) + int(count)
        source_cache = manifest.get("source_cache") or {}
        validation_error_count += int(source_cache.get("validation_error_count") or 0)

    if validation_error_count:
        add_issue(issues, "fail", base, f"strict cache validation errors: {validation_error_count}")
    missing = sorted(set(EXPECTED_STRICT_LABELS) - set(labels))
    if missing:
        add_issue(issues, "fail", base, f"missing expected strict labels: {missing}")
    return {
        "root": str(base),
        "labels": labels,
        "expected_labels": list(EXPECTED_STRICT_LABELS),
        "total_rows": total_rows,
        "day_counts": day_counts,
        "validation_error_count": validation_error_count,
    }


def check_completion_pipeline(data_root: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    base = data_root / COMPLETION_PIPELINE
    result_path = base / "RESULT_SUMMARY_MANIFEST.json"
    compliance_path = base / "COMPLIANCE_MANIFEST.json"
    registry_path = base / "CANDIDATE_REGISTRY_MANIFEST.json"
    summary_path = base / "summary_by_day.csv"
    out: dict[str, Any] = {"root": str(base)}

    for p in (result_path, compliance_path, registry_path, summary_path):
        if not p.exists():
            add_issue(issues, "fail", p, "required completion pipeline artifact is missing")
    if not result_path.exists() or not compliance_path.exists() or not registry_path.exists():
        return out

    result = read_json(result_path)
    compliance = read_json(compliance_path)
    registry = read_json(registry_path)
    days = list(result.get("days") or [])
    blocked = sorted(set(days) & BLOCKED_DAYS)
    if blocked:
        add_issue(issues, "fail", result_path, f"blocked days included: {blocked}")
    for path, manifest in ((result_path, result), (compliance_path, compliance), (registry_path, registry)):
        if manifest.get("assets") != ["BTC"]:
            add_issue(issues, "fail", path, "manifest assets are not BTC-only")
        if manifest.get("market_prefix") != ["btc-updown-5m-"]:
            add_issue(issues, "fail", path, "manifest market_prefix is not btc-updown-5m-")
        for flag in ("raw_scanned", "replay_scanned", "collector_scanned"):
            if manifest.get(flag) is not False:
                add_issue(issues, "fail", path, f"{flag} must be false")
        if manifest.get("public_account_execution_truth_v1_private_truth") is not False:
            add_issue(issues, "fail", path, "public account truth must remain non-private")

    if result.get("status") != "PASS_LOCAL_COMPLETION_RESEARCH_ONLY":
        add_issue(issues, "fail", result_path, "unexpected result status")
    if result.get("can_support_strategy_promotion") is not False:
        add_issue(issues, "fail", result_path, "strategy promotion must remain false")
    if compliance.get("compliance_pass") is not True:
        add_issue(issues, "fail", compliance_path, "compliance_pass is not true")
    if compliance.get("promotion_gate_pass") is not False:
        add_issue(issues, "fail", compliance_path, "promotion_gate_pass must remain false")
    if int(registry.get("row_count") or 0) <= 0:
        add_issue(issues, "fail", registry_path, "candidate registry row_count is not positive")

    day_rows = []
    if summary_path.exists():
        with summary_path.open(newline="", encoding="utf-8") as f:
            day_rows = list(csv.DictReader(f))
        if len(day_rows) != int(result.get("summary_by_day_row_count") or -1):
            add_issue(issues, "fail", summary_path, "summary_by_day row count does not match manifest")
        if len(day_rows) < 5:
            add_issue(issues, "fail", summary_path, "summary_by_day has too few days")

    core = result.get("core_metrics") or {}
    out.update(
        {
            "days": days,
            "labels": result.get("labels") or [],
            "summary_by_day_rows": len(day_rows),
            "status": result.get("status"),
            "row_count": result.get("row_count"),
            "candidate_registry_rows": registry.get("row_count"),
            "compliance_pass": compliance.get("compliance_pass"),
            "promotion_gate_pass": compliance.get("promotion_gate_pass"),
            "can_support_strategy_promotion": result.get("can_support_strategy_promotion"),
            "core_metrics": {
                "candidate_count": core.get("candidate_count"),
                "pair_actions": core.get("pair_actions"),
                "pair_qty": core.get("pair_qty"),
                "net_pair_cost_wavg": core.get("net_pair_cost_wavg"),
                "gross_pnl": core.get("gross_pnl"),
                "official_taker_fee": core.get("official_taker_fee"),
                "fee_after_pnl": core.get("fee_after_pnl"),
                "net_pnl": core.get("net_pnl"),
                "stress100_worst_pnl": core.get("stress100_worst_pnl"),
                "qty_residual_rate": core.get("qty_residual_rate"),
            },
        }
    )
    return out


def decision_from_issues(issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "fail" for issue in issues):
        return "BLOCKED_BTC_ONLY_BACKTEST_SURFACE_CHECK_FAILED"
    return "KEEP_BTC_ONLY_BACKTEST_SURFACE_READY_RESEARCH_ONLY"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    data_root = args.data_root.resolve()
    output_dir = args.output_dir
    if output_dir is None:
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = repo_root / ".tmp" / f"btc_only_backtest_surface_readiness_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)

    issues: list[dict[str, str]] = []
    report = {
        "generated_at_utc": utc_now(),
        "repo_root": str(repo_root),
        "data_root": str(data_root),
        "scope": "btc_only_backtest_surface_research_only",
        "raw_scanned": False,
        "replay_scanned": False,
        "collector_scanned": False,
        "remote_scanned": False,
        "deployable": False,
        "private_truth_ready": False,
        "promotion_gate_pass": False,
        "checks": {
            "v1_surfaces": check_v1_surfaces(repo_root, issues),
            "required_scripts": check_required_scripts(repo_root, issues),
            "strict_cache": check_strict_cache(data_root, issues),
            "completion_pipeline": check_completion_pipeline(data_root, issues),
        },
        "issues": issues,
    }
    report["decision"] = decision_from_issues(issues)
    report["ok"] = report["decision"].startswith("KEEP_")
    report["outputs"] = {"manifest": str((output_dir / "BTC_ONLY_BACKTEST_SURFACE_READINESS.json").resolve())}
    manifest_path = output_dir / "BTC_ONLY_BACKTEST_SURFACE_READINESS.json"
    manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"decision": report["decision"], "ok": report["ok"], "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

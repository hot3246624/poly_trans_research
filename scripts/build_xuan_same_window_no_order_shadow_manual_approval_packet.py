#!/usr/bin/env python3
"""Build the manual approval packet for a same-window no-order shadow start.

The packet is deliberately non-executable: it binds the existing engineering
preflight, public L2 proxy no-order evaluation, runner config, and candidate
binding into one review artifact. It never grants approval and never starts a
runner.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_START_PREFLIGHT = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_start_preflight_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT.json"
)
DEFAULT_NO_ORDER_EVAL = (
    DEFAULT_CONTRACT
    / "xuan_btc_tiny_canary_no_order_shadow_eval_latest/XUAN_BTC_TINY_CANARY_NO_ORDER_SHADOW_EVAL.json"
)
DEFAULT_REPORT_DIR = DEFAULT_CONTRACT / "xuan_btc_tiny_canary_no_order_shadow_report_latest"
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT / "xuan_same_window_no_order_shadow_manual_approval_packet_latest"

MANUAL_BLOCKER = "manual_shadow_start_approval_missing"
KEEP_START_PREFLIGHT = "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_START_PREFLIGHT_ENGINEERING_READY_APPROVAL_REQUIRED"
KEEP_NO_ORDER_EVAL = (
    "KEEP_XUAN_BTC_TINY_CANARY_PUBLIC_L2_PROXY_NO_ORDER_SHADOW_EVALUATED_PROMOTION_BLOCKED_OWNER_TRUTH"
)
KEEP_AUDIT = "KEEP_NO_ORDER_SHADOW_AUDIT_READY"
KEEP_GATE_SUMMARY = "KEEP_XUAN_BTC_TINY_CANARY_PUBLIC_L2_PROXY_NO_ORDER_SHADOW_GATE_PASS_RESEARCH_ONLY"

FORBIDDEN_AFTER_APPROVAL = [
    "orders_sent",
    "cancels_sent",
    "redeems_sent",
    "candidate_import",
    "live_orders",
    "canary_or_production_trading",
    "private_key_loading",
    "funding_or_balance_mutation",
    "promotion_gate_pass_claim",
    "private_truth_ready_claim",
    "deployable_claim",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def csv_row_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(newline="", encoding="utf-8") as f:
        return sum(1 for _ in csv.DictReader(f))


def check(rows: list[dict[str, Any]], name: str, passed: bool, detail: str, severity: str = "blocker") -> None:
    rows.append({"check": name, "passed": bool(passed), "severity": severity, "detail": detail})


def path_info(path: Path, kind: str) -> dict[str, Any]:
    return {
        "kind": kind,
        "path": str(path),
        "present": path.is_file(),
        "sha256": sha256_file(path),
    }


def approval_rows(output_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "approval_item": "approve_start_read_only_ws_no_order_runner",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": "Start only a read-only WS/no-order runner for the reviewed config.",
        },
        {
            "approval_item": "approve_candidate_read_only_binding",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": "Read candidate_binding.csv only; no candidate import or live registry mutation.",
        },
        {
            "approval_item": "approve_public_book_latency_fillability_collection",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": "Collect public book, latency, and fillability proxies only.",
        },
        {
            "approval_item": "approve_runtime_log_write_under_packet_dir",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": f"Write runtime logs only under {output_dir}/runtime_logs or the reviewed runner output dir.",
        },
        {
            "approval_item": "approve_stop_conditions_and_kill_switch",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": "Accept fail-closed stop conditions and manual kill-switch before start.",
        },
        {
            "approval_item": "approve_no_orders_no_import_no_private_key",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_APPROVAL",
            "scope": "Confirm NullOrderClient/stub, no private key, and zero order/cancel/redeem/import calls.",
        },
        {
            "approval_item": "acknowledge_proxy_not_private_truth",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_ACK",
            "scope": "No-order shadow validates public proxy behavior only; it cannot set private_truth_ready.",
        },
        {
            "approval_item": "acknowledge_no_promotion_or_live_ready",
            "required": True,
            "current": False,
            "status": "MISSING_MANUAL_ACK",
            "scope": "Even after approval, promotion/live/deployable gates remain false until future owner truth.",
        },
    ]


def build_summary_md(manifest: dict[str, Any]) -> str:
    summary = manifest.get("summary") or {}
    approvals = manifest.get("required_manual_approvals") or []
    approval_names = "\n".join(f"- {row['approval_item']}: {row['status']}" for row in approvals)
    forbidden = "\n".join(f"- {item}" for item in manifest.get("forbidden_after_manual_approval") or [])
    return "\n".join(
        [
            "# Xuan Same-Window No-Order Shadow Manual Approval Packet",
            "",
            f"status: {manifest['status']}",
            f"approval_packet_ready: {manifest['approval_packet_ready']}",
            "manual_approval_granted: false",
            "shadow_start_ready: false",
            "runner_start_allowed: false",
            "",
            "## Evidence",
            "",
            f"- start_preflight_ready: {summary.get('start_preflight_ready')}",
            f"- post_run_no_order_eval_passed: {summary.get('post_run_no_order_eval_passed')}",
            f"- active_runner_conflict_check_passed: {summary.get('active_runner_conflict_check_passed')}",
            f"- no_order_report_rows: {summary.get('no_order_report_rows')}",
            f"- candidate_binding_rows: {summary.get('candidate_binding_rows')}",
            f"- no_order_eval_status: {summary.get('no_order_eval_status')}",
            "",
            "## Required Manual Approvals",
            "",
            approval_names,
            "",
            "## Forbidden Even After Manual Approval",
            "",
            forbidden,
            "",
            "## Exact Next Action",
            "",
            manifest["next_executable_action"],
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-preflight", type=Path, default=DEFAULT_START_PREFLIGHT)
    parser.add_argument("--no-order-eval", type=Path, default=DEFAULT_NO_ORDER_EVAL)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    start_preflight_path = args.start_preflight.expanduser()
    no_order_eval_path = args.no_order_eval.expanduser()
    report_dir = args.report_dir.expanduser()

    start_preflight = read_json(start_preflight_path)
    no_order_eval = read_json(no_order_eval_path)
    audit_manifest_path = report_dir / "no_order_shadow_audit_manifest.json"
    gate_summary_path = report_dir / "no_order_shadow_gate_summary.json"
    main_report_path = report_dir / "no_order_shadow_report.csv"
    legacy_report_path = report_dir / "btc_same_window_tiny_canary_no_order_shadow_report.csv"
    audit_manifest = read_json(audit_manifest_path)
    gate_summary = read_json(gate_summary_path)
    eval_audit_manifest_path = Path(
        ((no_order_eval.get("input_audit_manifest") or {}).get("path") or "")
    ).expanduser()
    eval_audit_manifest = read_json(eval_audit_manifest_path)
    eval_input_hashes = (
        eval_audit_manifest.get("input_hashes")
        if isinstance(eval_audit_manifest.get("input_hashes"), dict)
        else {}
    )
    audit_input_hashes = (
        audit_manifest.get("input_hashes") if isinstance(audit_manifest.get("input_hashes"), dict) else {}
    )
    runtime_binding_fingerprints = {
        key: (eval_input_hashes.get(key) or audit_input_hashes.get(key) or "")
        for key in [
            "runtime_config_sha256",
            "runtime_candidate_binding_sha256",
            "source_semantics_contract_sha256",
            "source_import_contract_sha256",
        ]
    }
    runtime_binding_fingerprints = {
        key: value for key, value in runtime_binding_fingerprints.items() if value
    }

    runner_config_path = Path(
        ((start_preflight.get("runner_config") or {}).get("path") or "")
    ).expanduser()
    runner_config = read_json(runner_config_path)
    candidate_binding_path = Path(
        ((start_preflight.get("outputs") or {}).get("candidate_binding_csv") or "")
    ).expanduser()
    preflight_checklist_path = Path(
        ((start_preflight.get("outputs") or {}).get("preflight_checklist_csv") or "")
    ).expanduser()
    start_command_path = Path(
        ((start_preflight.get("outputs") or {}).get("start_command_preview") or "")
    ).expanduser()

    checks: list[dict[str, Any]] = []
    remaining = start_preflight.get("remaining_blockers") if isinstance(start_preflight.get("remaining_blockers"), list) else []
    non_manual_blockers = [item for item in remaining if item != MANUAL_BLOCKER]
    start_conflict_passed = bool((start_preflight.get("active_runner_conflict_check") or {}).get("passed"))
    eval_summary = no_order_eval.get("summary") or {}
    eval_gate = no_order_eval.get("promotion_gate") or {}
    audit_safety = audit_manifest.get("safety") or {}
    audit_continuity = audit_manifest.get("source_continuity") or {}
    gate_promotion = gate_summary.get("promotion_gate") or {}

    check(checks, "start_preflight_present", bool(start_preflight), str(start_preflight_path))
    check(checks, "start_preflight_keep_status", start_preflight.get("status") == KEEP_START_PREFLIGHT, str(start_preflight.get("status")))
    check(checks, "engineering_preflight_ready", start_preflight.get("engineering_preflight_ready") is True, "start preflight is engineering-ready")
    check(checks, "start_preflight_does_not_grant_start", start_preflight.get("shadow_start_ready") is False, "shadow_start_ready remains false")
    check(checks, "manual_approval_is_only_start_blocker", remaining == [MANUAL_BLOCKER] and not non_manual_blockers, str(remaining))
    check(checks, "active_runner_conflict_check_passed", start_conflict_passed, "no conflicting runner process detected by preflight")

    check(checks, "no_order_eval_present", bool(no_order_eval), str(no_order_eval_path))
    check(
        checks,
        "post_run_no_order_eval_not_required_before_start",
        True,
        (
            "strict real-runner no-order eval is post-run evidence and is not a prerequisite "
            "for preparing the manual start approval packet"
        ),
        severity="info",
    )
    check(checks, "no_order_eval_promotion_gate_false", eval_gate.get("private_truth_ready") is False and eval_gate.get("strategy_promotion_ready") is False and eval_gate.get("live_orders_allowed") is False, str(eval_gate))

    check(checks, "strict_main_report_present", main_report_path.exists(), str(main_report_path))
    check(checks, "legacy_main_report_present", legacy_report_path.exists(), str(legacy_report_path))
    check(checks, "audit_manifest_present", audit_manifest_path.exists(), str(audit_manifest_path))
    check(checks, "gate_summary_present", gate_summary_path.exists(), str(gate_summary_path))
    check(checks, "audit_manifest_keep_status", audit_manifest.get("status") == KEEP_AUDIT, str(audit_manifest.get("status")))
    check(checks, "audit_manifest_columns_match_required", (audit_manifest.get("main_report") or {}).get("columns_match_required") is True, "main CSV has exactly required columns")
    check(checks, "audit_manifest_safety_no_order", audit_safety.get("no_order") is True and audit_safety.get("dry_run_only") is True, str(audit_safety))
    check(checks, "audit_manifest_import_disabled", audit_safety.get("import_enabled") is False and audit_safety.get("candidate_import_allowed") is False, str(audit_safety))
    check(checks, "audit_manifest_no_private_key", audit_safety.get("no_private_key_loaded") is True and audit_safety.get("private_key_loaded") is False, str(audit_safety))
    check(checks, "audit_manifest_null_order_client", audit_safety.get("null_order_client_or_stub") is True, str(audit_safety.get("order_client_type")))
    check(checks, "audit_manifest_zero_mutation_calls", all(int(audit_safety.get(key) or 0) == 0 for key in ["order_api_call_count", "cancel_api_call_count", "redeem_api_call_count", "candidate_import_call_count"]), str(audit_safety))
    check(checks, "audit_manifest_source_continuity", audit_continuity.get("passed") is True, str(audit_continuity))
    check(checks, "gate_summary_keep_status", gate_summary.get("status") == KEEP_GATE_SUMMARY, str(gate_summary.get("status")))
    check(checks, "gate_summary_passed", gate_summary.get("evaluation_passed") is True, str(gate_summary.get("evaluation_passed")))
    check(checks, "gate_summary_promotion_gate_false", gate_promotion.get("private_truth_ready") is False and gate_promotion.get("strategy_promotion_ready") is False and gate_promotion.get("live_orders_allowed") is False, str(gate_promotion))

    runner_config_sha = sha256_file(runner_config_path)
    expected_runner_config_sha = (start_preflight.get("runner_config") or {}).get("sha256")
    check(checks, "runner_config_present", runner_config_path.exists(), str(runner_config_path))
    check(checks, "runner_config_sha_matches_preflight", bool(runner_config_sha) and runner_config_sha == expected_runner_config_sha, f"{runner_config_sha} == {expected_runner_config_sha}")
    check(checks, "runner_config_dry_run_only", runner_config.get("dry_run_only") is True, str(runner_config.get("dry_run_only")))
    check(checks, "runner_config_mutation_flags_false", all(runner_config.get(key) is False for key in ["candidate_import_allowed", "live_import_enabled", "orders_allowed", "live_orders_allowed", "redeems_allowed", "cancels_allowed", "remote_runner_allowed"]), "all mutation flags false")
    check(checks, "runner_config_start_without_approval_false", runner_config.get("start_allowed_without_manual_approval") is False and runner_config.get("requires_manual_approval_before_start") is True, "manual approval required")
    check(checks, "runner_config_stop_conditions_present", bool(runner_config.get("stop_conditions")), "stop conditions are configured")
    check(checks, "runner_config_kill_switch_present", bool(runner_config.get("kill_switch")), "kill-switch is configured")

    candidate_binding_rows = csv_row_count(candidate_binding_path)
    no_order_report_rows = csv_row_count(main_report_path)
    check(checks, "candidate_binding_present", candidate_binding_path.exists(), str(candidate_binding_path))
    check(checks, "candidate_binding_nonempty", candidate_binding_rows > 0, str(candidate_binding_rows))
    check(checks, "preflight_checklist_present", preflight_checklist_path.exists(), str(preflight_checklist_path))
    check(checks, "start_command_preview_present", start_command_path.exists(), str(start_command_path))

    failed_checks = [row for row in checks if not row["passed"] and row["severity"] == "blocker"]
    packet_ready = not failed_checks
    approvals = approval_rows(output_dir)
    remaining_blockers = [MANUAL_BLOCKER]
    if failed_checks:
        remaining_blockers.extend([f"approval_packet_check_failed:{row['check']}" for row in failed_checks])

    manifest_path = output_dir / "XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json"
    checklist_csv_path = output_dir / "manual_approval_checklist.csv"
    prerequisite_csv_path = output_dir / "manual_approval_prerequisite_checks.csv"
    summary_md_path = output_dir / "manual_approval_summary.md"

    manifest: dict[str, Any] = {
        "schema_version": "xuan_same_window_no_order_shadow_manual_approval_packet_v1",
        "created_utc": utc_now(),
        "status": (
            "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET_READY_APPROVAL_REQUIRED"
            if packet_ready
            else "BLOCKED_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET_NOT_READY"
        ),
        "approval_packet_ready": packet_ready,
        "manual_approval_required": True,
        "manual_approval_granted": False,
        "shadow_start_ready": False,
        "runner_start_allowed": False,
        "runtime_binding_fingerprints": runtime_binding_fingerprints,
        "remaining_blockers": remaining_blockers,
        "failed_checks": failed_checks,
        "summary": {
            "start_preflight_ready": start_preflight.get("engineering_preflight_ready") is True,
            "post_run_no_order_eval_passed": eval_summary.get("evaluation_passed") is True,
            "active_runner_conflict_check_passed": start_conflict_passed,
            "no_order_report_rows": no_order_report_rows,
            "candidate_binding_rows": candidate_binding_rows,
            "no_order_eval_status": no_order_eval.get("status") or "MISSING",
            "book_age_p95_ms": eval_summary.get("book_age_p95_ms"),
            "top5_supports_seed_qty_rate": eval_summary.get("top5_supports_seed_qty_rate"),
            "observed_residual_cost_share": eval_summary.get("observed_residual_cost_share"),
            "threshold_failure_count": eval_summary.get("threshold_failure_count"),
            "stop_condition_event_count": eval_summary.get("stop_condition_event_count"),
        },
        "research_ranking": {
            "post_run_no_order_eval_passed": eval_summary.get("evaluation_passed") is True,
            "row_count": eval_summary.get("row_count"),
            "candidate_count": eval_summary.get("candidate_count"),
            "day_count": eval_summary.get("day_count"),
            "fee_stress_metric_sum": eval_summary.get("fee_stress_metric_sum"),
            "pair_edge_haircut_metric_sum": eval_summary.get("pair_edge_haircut_metric_sum"),
        },
        "promotion_gate": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "live_orders_allowed": False,
            "deployable": False,
            "future_owner_execution_reconciliation_required": True,
            "manual_approval_granted": False,
        },
        "required_manual_approvals": approvals,
        "forbidden_after_manual_approval": FORBIDDEN_AFTER_APPROVAL,
        "next_executable_action": (
            "Human reviewer may explicitly approve starting only the reviewed read-only WS/no-order runner "
            "with this packet and runner config; without that exact approval, regenerate this packet and wait."
        ),
        "inputs": {
            "start_preflight": path_info(start_preflight_path, "json"),
            "no_order_eval": path_info(no_order_eval_path, "json"),
            "main_report": path_info(main_report_path, "csv"),
            "legacy_report": path_info(legacy_report_path, "csv"),
            "audit_manifest": path_info(audit_manifest_path, "json"),
            "gate_summary": path_info(gate_summary_path, "json"),
            "runner_config": path_info(runner_config_path, "json"),
            "candidate_binding": path_info(candidate_binding_path, "csv"),
            "preflight_checklist": path_info(preflight_checklist_path, "csv"),
            "start_command_preview": path_info(start_command_path, "text"),
        },
        "outputs": {
            "manifest": str(manifest_path),
            "manual_approval_checklist_csv": str(checklist_csv_path),
            "prerequisite_checks_csv": str(prerequisite_csv_path),
            "manual_approval_summary_md": str(summary_md_path),
        },
        "prerequisite_checks": checks,
        "policy": {
            "research_only_until_future_owner_truth": True,
            "same_window_handoff_is_research_material_only": True,
            "public_l2_proxy_shadow_is_private_truth": False,
            "historical_shadow_or_v1_is_private_truth": False,
            "residual_settlement_pnl_is_strategy_edge": False,
            "approval_packet_can_start_runner_by_itself": False,
        },
    }

    write_csv(
        prerequisite_csv_path,
        checks,
        ["check", "passed", "severity", "detail"],
    )
    write_csv(
        checklist_csv_path,
        approvals,
        ["approval_item", "required", "current", "status", "scope"],
    )
    write_json(manifest_path, manifest)
    summary_md_path.write_text(build_summary_md(manifest), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": manifest["status"],
                "approval_packet_ready": packet_ready,
                "manual_approval_granted": False,
                "shadow_start_ready": False,
                "remaining_blockers": remaining_blockers,
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if packet_ready else 1


if __name__ == "__main__":
    raise SystemExit(main())

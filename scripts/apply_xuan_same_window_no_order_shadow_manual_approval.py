#!/usr/bin/env python3
"""Apply an explicit manual approval decision without starting any runner.

This is a guardrail, not a start script. With no approval text it emits a
waiting decision. With the exact current approval text it records that the
review gate is approved, but still does not start a runner or claim private
truth/promotion readiness.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_APPROVAL_PACKET = (
    DEFAULT_CONTRACT
    / "xuan_same_window_no_order_shadow_manual_approval_packet_latest/XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT / "xuan_same_window_no_order_shadow_manual_approval_decision_latest"

READY_PACKET_STATUS = "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_PACKET_READY_APPROVAL_REQUIRED"
APPROVED_STATUS = "KEEP_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_GRANTED_NOT_STARTED"
WAITING_STATUS = "WAITING_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_NOT_GRANTED"
BLOCKED_STATUS = "BLOCKED_XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION"
MANUAL_BLOCKER = "manual_shadow_start_approval_missing"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_review_material(packet: dict[str, Any]) -> dict[str, Any]:
    inputs = packet.get("inputs") or {}
    required_rows = packet.get("required_manual_approvals") or packet.get("manual_approval_checklist") or []
    return {
        "approval_packet_status": packet.get("status"),
        "summary": packet.get("summary") or {},
        "promotion_gate": packet.get("promotion_gate") or {},
        "policy": packet.get("policy") or {},
        "remaining_blockers": packet.get("remaining_blockers") or [],
        "runtime_binding_fingerprints": packet.get("runtime_binding_fingerprints") or {},
        "exact_approval_text": packet.get("exact_approval_text") or "",
        "input_hashes": {
            key: (value or {}).get("sha256")
            for key, value in sorted(inputs.items())
            if isinstance(value, dict)
        },
        "forbidden_after_manual_approval": packet.get("forbidden_after_manual_approval") or [],
        "required_manual_approvals": [
            {
                "approval_item": row.get("approval_item"),
                "required": row.get("required"),
                "scope": row.get("scope"),
            }
            for row in required_rows
        ],
    }


def review_fingerprint(packet: dict[str, Any]) -> str:
    material = json.dumps(stable_review_material(packet), ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def required_approval_text(fingerprint: str) -> str:
    return (
        "APPROVE_XUAN_READ_ONLY_WS_NO_ORDER_SHADOW "
        f"review_fingerprint={fingerprint[:24]} "
        "scope=NO_ORDER_READ_ONLY_WS "
        "NO_IMPORT NO_PRIVATE_KEY NO_ORDERS NO_CANCELS NO_REDEEMS "
        "NO_PROMOTION_CLAIM NO_PRIVATE_TRUTH_CLAIM"
    )


def validate_packet(packet: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    gate = packet.get("promotion_gate") or {}
    policy = packet.get("policy") or {}
    if packet.get("status") != READY_PACKET_STATUS:
        errors.append("approval_packet_status_not_ready")
    if packet.get("approval_packet_ready") is not True:
        errors.append("approval_packet_ready_not_true")
    if packet.get("manual_approval_granted") is not False:
        errors.append("approval_packet_must_not_pregrant_manual_approval")
    if packet.get("shadow_start_ready") is not False:
        errors.append("approval_packet_must_not_set_shadow_start_ready")
    if packet.get("runner_start_allowed") is not False:
        errors.append("approval_packet_must_not_allow_runner_start")
    if packet.get("failed_checks"):
        errors.append("approval_packet_failed_checks_not_empty")
    remaining_blockers = packet.get("remaining_blockers")
    if remaining_blockers not in (None, [MANUAL_BLOCKER]):
        errors.append("approval_packet_remaining_blockers_not_manual_only")
    private_truth_ready = gate.get("private_truth_ready", policy.get("private_truth_ready"))
    strategy_promotion_ready = gate.get(
        "strategy_promotion_ready", policy.get("strategy_promotion_ready", policy.get("promotion_ready"))
    )
    live_orders_allowed = gate.get("live_orders_allowed", policy.get("live_orders_allowed"))
    deployable = gate.get("deployable", policy.get("deployable"))
    if private_truth_ready is not False:
        errors.append("private_truth_ready_must_remain_false")
    if strategy_promotion_ready is not False:
        errors.append("strategy_promotion_ready_must_remain_false")
    if live_orders_allowed is not False:
        errors.append("live_orders_allowed_must_remain_false")
    if deployable is not False:
        errors.append("deployable_must_remain_false")
    required = packet.get("required_manual_approvals") or packet.get("manual_approval_checklist") or []
    missing_required = [
        row.get("approval_item")
        for row in required
        if row.get("required") is not True or row.get("current") is not False
    ]
    if missing_required:
        errors.append("required_approval_rows_not_pending")
    return errors


def build_summary_md(decision: dict[str, Any]) -> str:
    return "\n".join(
        [
            "# Xuan Same-Window No-Order Shadow Manual Approval Decision",
            "",
            f"status: {decision['status']}",
            f"manual_approval_granted: {decision['manual_approval_granted']}",
            f"runner_start_allowed_by_approval_gate: {decision['runner_start_allowed_by_approval_gate']}",
            "runner_started: false",
            f"review_fingerprint: {decision['review_fingerprint']}",
            "",
            "## Required Approval Text",
            "",
            "```text",
            decision["required_approval_text"],
            "```",
            "",
            "## Remaining Blockers",
            "",
            "\n".join(f"- {item}" for item in decision.get("remaining_blockers") or []),
            "",
            "## Policy",
            "",
            "- This script never starts a runner.",
            "- Approval does not enable orders, imports, private keys, cancels, redeems, live trading, or promotion claims.",
            "- Private truth and strategy promotion stay false until future owner execution reconciliation.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approval-packet", type=Path, default=DEFAULT_APPROVAL_PACKET)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--approval-text", default="")
    parser.add_argument("--approved-by", default="")
    parser.add_argument("--clear-approval", action="store_true")
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    approval_packet_path = args.approval_packet.expanduser()
    manifest_path = output_dir / "XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json"
    packet = read_json(approval_packet_path)
    existing_decision = read_json(manifest_path)
    packet_sha = sha256_file(approval_packet_path)
    errors = validate_packet(packet)
    fingerprint = review_fingerprint(packet) if packet else ""
    packet_exact_text = str(packet.get("exact_approval_text") or "").strip()
    approval_text = packet_exact_text or (required_approval_text(fingerprint) if fingerprint else "")
    supplied_text = args.approval_text.strip()
    approval_text_matches = bool(supplied_text) and supplied_text == approval_text

    existing_approval_still_valid = (
        not args.clear_approval
        and existing_decision.get("status") == APPROVED_STATUS
        and existing_decision.get("manual_approval_granted") is True
        and existing_decision.get("runner_started") is False
        and existing_decision.get("review_fingerprint") == fingerprint
    )

    if errors:
        status = BLOCKED_STATUS
        approval_granted = False
        remaining_blockers = [f"approval_packet_invalid:{item}" for item in errors]
    elif existing_approval_still_valid and not supplied_text:
        status = APPROVED_STATUS
        approval_granted = True
        remaining_blockers = []
    elif not supplied_text:
        status = WAITING_STATUS
        approval_granted = False
        remaining_blockers = [MANUAL_BLOCKER]
    elif not approval_text_matches:
        status = BLOCKED_STATUS
        approval_granted = False
        remaining_blockers = ["manual_approval_text_mismatch"]
    else:
        status = APPROVED_STATUS
        approval_granted = True
        remaining_blockers = []

    decision = {
        "schema_version": "xuan_same_window_no_order_shadow_manual_approval_decision_v1",
        "created_utc": utc_now(),
        "status": status,
        "manual_approval_granted": approval_granted,
        "runner_start_allowed_by_approval_gate": approval_granted,
        "runner_started": False,
        "approval_text_matches": approval_text_matches,
        "approved_by": (
            args.approved_by
            if approval_granted and args.approved_by
            else existing_decision.get("approved_by", "")
            if existing_approval_still_valid
            else ""
        ),
        "approval_packet": {
            "path": str(approval_packet_path),
            "present": approval_packet_path.is_file(),
            "sha256": packet_sha,
            "status": packet.get("status") or "MISSING",
        },
        "review_fingerprint": fingerprint,
        "required_approval_text": approval_text,
        "remaining_blockers": remaining_blockers,
        "validation_errors": errors,
        "preserved_existing_approval": existing_approval_still_valid and not supplied_text,
        "promotion_gate": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "live_orders_allowed": False,
            "deployable": False,
            "future_owner_execution_reconciliation_required": True,
        },
        "policy": {
            "script_starts_runner": False,
            "approval_enables_orders": False,
            "approval_enables_import": False,
            "approval_loads_private_key": False,
            "approval_claims_private_truth": False,
            "approval_claims_promotion_ready": False,
            "approval_scope": "read_only_ws_no_order_shadow_only",
        },
        "next_executable_action": (
            "If approved, a separate start command must still be reviewed and run manually; "
            "otherwise wait for the exact approval text above."
        ),
    }
    manifest_path = output_dir / "XUAN_SAME_WINDOW_NO_ORDER_SHADOW_MANUAL_APPROVAL_DECISION.json"
    summary_path = output_dir / "manual_approval_decision_summary.md"
    write_json(manifest_path, decision)
    summary_path.write_text(build_summary_md(decision), encoding="utf-8")
    print(
        json.dumps(
            {
                "status": status,
                "manual_approval_granted": approval_granted,
                "runner_start_allowed_by_approval_gate": approval_granted,
                "runner_started": False,
                "remaining_blockers": remaining_blockers,
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    if errors or (supplied_text and not approval_text_matches):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

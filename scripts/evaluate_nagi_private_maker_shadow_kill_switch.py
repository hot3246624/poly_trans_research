#!/usr/bin/env python3
"""Offline kill-switch evaluator for future NAGI private maker-shadow telemetry.

The evaluator never connects to Polymarket, never loads credentials, and never
sends cancels. It converts telemetry/audit evidence into a fail-closed decision
that a separately authorized runtime would have to honor.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_APPROVAL_PACKET = (
    ROOT
    / "data/exports/nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
DEFAULT_RUNNER_AUDIT = (
    ROOT
    / "data/exports/nagi_private_maker_shadow_runner_preflight_latest"
    / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_PRIVATE_SHADOW_AUDIT.json"
)

STATUS_REVIEWED_SAMPLE_REQUIRED = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_EVALUATOR_REVIEWED_"
    "PRIVATE_SAMPLE_REQUIRED_NOT_EXECUTION_READY"
)
STATUS_TRIGGERED = (
    "BLOCKED_NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_TRIGGERED_NOT_EXECUTION_READY"
)
STATUS_CLEAR_REVIEW_ONLY = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_CLEAR_REVIEW_ONLY_NOT_OOS_READY"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    approval = read_json(Path(args.approval_packet))
    telemetry_review: dict[str, Any] | None = None
    runner_audit: dict[str, Any] | None = None
    triggers: list[str] = []
    warnings: list[str] = []

    if args.telemetry_review_json:
        telemetry_path = Path(args.telemetry_review_json)
        if telemetry_path.exists():
            telemetry_review = read_json(telemetry_path)
        else:
            triggers.append("TELEMETRY_REVIEW_JSON_MISSING")
    else:
        warnings.append("PRIVATE_SHADOW_TELEMETRY_SAMPLE_NOT_PRESENT")

    if args.runner_audit_json:
        audit_path = Path(args.runner_audit_json)
        if audit_path.exists():
            runner_audit = read_json(audit_path)
        else:
            triggers.append("RUNNER_AUDIT_JSON_MISSING")

    if telemetry_review:
        summary = telemetry_review.get("summary") or {}
        issues = telemetry_review.get("issues") or []
        if telemetry_review.get("ok") is not True:
            triggers.append("TELEMETRY_REVIEW_NOT_OK")
        if issues:
            triggers.append("TELEMETRY_REVIEW_HAS_ISSUES")
        if as_int(summary.get("taker_or_ambiguous_fill_count")) > 0:
            triggers.append("TAKER_OR_AMBIGUOUS_FILL_OBSERVED")
        if as_int(summary.get("nonzero_fee_fill_count")) > 0:
            triggers.append("NONZERO_MAKER_FEE_OBSERVED")
        if as_int(summary.get("stale_or_misaligned_fill_count")) > 0:
            triggers.append("STALE_OR_MISALIGNED_COUNTED_SAMPLE")
        if as_int(summary.get("missing_order_count")) > 0:
            triggers.append("MISSING_ORDER_TELEMETRY")
        if as_int(summary.get("missing_ack_or_post_only_count")) > 0:
            triggers.append("MISSING_ACK_OR_POST_ONLY")
        if as_int(summary.get("missing_cancel_or_full_fill_count")) > 0:
            triggers.append("MISSING_CANCEL_OR_FULL_FILL")
        if as_int(summary.get("policy_decision_breach_count")) > 0:
            triggers.append("POLICY_DECISION_BREACH")
        if as_int(summary.get("inventory_drift_count")) > 0:
            triggers.append("INVENTORY_DRIFT_OBSERVED")
        if as_float(summary.get("pair_cost_p50"), 999.0) > 0.995:
            triggers.append("PAIR_COST_P50_ABOVE_0_995")
        if as_float(summary.get("residual_cost_rate_proxy"), 999.0) > 0.20:
            triggers.append("RESIDUAL_COST_RATE_ABOVE_0_20")
        if as_float(summary.get("taker_fill_share"), 1.0) > 0.0:
            triggers.append("TAKER_FILL_SHARE_NONZERO")

    if runner_audit:
        if runner_audit.get("private_key_loaded") is not False:
            triggers.append("RUNNER_PRIVATE_KEY_LOADED")
        if runner_audit.get("api_creds_loaded") is not False:
            triggers.append("RUNNER_API_CREDS_LOADED")
        if runner_audit.get("network_used") is not False:
            triggers.append("RUNNER_NETWORK_USED")
        if runner_audit.get("ws_started") is not False:
            triggers.append("RUNNER_WS_STARTED")
        if as_int(runner_audit.get("orders_sent")) > 0:
            triggers.append("RUNNER_ORDERS_SENT")
        if as_int(runner_audit.get("cancels_sent")) > 0:
            triggers.append("RUNNER_CANCELS_SENT")

    if triggers:
        status = STATUS_TRIGGERED
        stop_new_orders = True
        cancel_all_open_orders_required = True
    elif telemetry_review is None:
        status = STATUS_REVIEWED_SAMPLE_REQUIRED
        stop_new_orders = True
        cancel_all_open_orders_required = False
    else:
        status = STATUS_CLEAR_REVIEW_ONLY
        stop_new_orders = False
        cancel_all_open_orders_required = False

    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "ok": not triggers,
        "approval_packet": str(args.approval_packet),
        "approval_status": approval.get("status"),
        "telemetry_review_json": str(args.telemetry_review_json) if args.telemetry_review_json else None,
        "runner_audit_json": str(args.runner_audit_json) if args.runner_audit_json else None,
        "triggers": sorted(set(triggers)),
        "warnings": warnings,
        "decision": {
            "stop_new_orders_required": stop_new_orders,
            "cancel_all_open_orders_required": cancel_all_open_orders_required,
            "cancel_action_authorized": False,
            "network_action_authorized": False,
            "execution_ready": False,
            "private_truth_ready": False,
        },
        "bound_contract": approval.get("kill_switch_contract"),
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "private_key_authorized": False,
            "ws_authorized": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-packet", default=str(DEFAULT_APPROVAL_PACKET))
    parser.add_argument("--telemetry-review-json")
    parser.add_argument("--runner-audit-json", default=str(DEFAULT_RUNNER_AUDIT))
    parser.add_argument("--out-json")
    args = parser.parse_args()
    result = evaluate(args)
    text = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.out_json:
        Path(args.out_json).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

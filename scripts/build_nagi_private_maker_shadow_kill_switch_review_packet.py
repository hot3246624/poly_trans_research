#!/usr/bin/env python3
"""Build a review packet for the offline NAGI maker-shadow kill-switch evaluator."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_maker_shadow_kill_switch_review_packet_20260608"

APPROVAL_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
EVALUATOR = ROOT / "scripts/evaluate_nagi_private_maker_shadow_kill_switch.py"
NO_SAMPLE_EVAL = (
    EXPORTS
    / "nagi_private_maker_shadow_runner_preflight_latest"
    / "NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_EVAL_NO_SAMPLE.json"
)
RUNNER_DENY_AUDIT = (
    EXPORTS
    / "nagi_private_maker_shadow_runner_preflight_latest"
    / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_PRIVATE_SHADOW_AUDIT.json"
)
BUILDER = ROOT / "scripts/build_nagi_private_maker_shadow_kill_switch_review_packet.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_REVIEW_PACKET_PREPARED_"
    "OFFLINE_EVALUATOR_BOUND_RUNTIME_PROCESS_REQUIRED_NOT_EXECUTION_READY"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.is_file():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_report(path: Path, packet: dict[str, Any]) -> None:
    lines = [
        "# NAGI Private Maker Shadow Kill-Switch Review",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "- Offline kill-switch evaluator is implemented and hash-bound.",
        "- Current no-sample evaluation requires `stop_new_orders_required=true` and keeps cancel/network authorization false.",
        "- This does not install or authorize a runtime cancel process.",
        "",
        "## Remaining Blockers",
        "",
        *[f"- {item}" for item in packet["remaining_blockers"]],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_packet() -> dict[str, Any]:
    approval = read_json(APPROVAL_PACKET)
    no_sample = read_json(NO_SAMPLE_EVAL)
    runner_deny = read_json(RUNNER_DENY_AUDIT)
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "approval_packet": binding(APPROVAL_PACKET),
            "kill_switch_evaluator": binding(EVALUATOR),
            "no_sample_eval": binding(NO_SAMPLE_EVAL),
            "runner_deny_audit": binding(RUNNER_DENY_AUDIT),
            "builder": binding(BUILDER),
        },
        "review_result": {
            "no_sample_status": no_sample.get("status"),
            "no_sample_ok": no_sample.get("ok"),
            "no_sample_warnings": no_sample.get("warnings"),
            "stop_new_orders_required": (no_sample.get("decision") or {}).get("stop_new_orders_required"),
            "cancel_all_open_orders_required": (no_sample.get("decision") or {}).get("cancel_all_open_orders_required"),
            "cancel_action_authorized": (no_sample.get("decision") or {}).get("cancel_action_authorized"),
            "network_action_authorized": (no_sample.get("decision") or {}).get("network_action_authorized"),
            "runner_private_key_loaded": runner_deny.get("private_key_loaded"),
            "runner_network_used": runner_deny.get("network_used"),
            "runner_orders_sent": runner_deny.get("orders_sent"),
            "runner_cancels_sent": runner_deny.get("cancels_sent"),
        },
        "bound_kill_switch_contract": approval.get("kill_switch_contract"),
        "remaining_blockers": [
            "EXACT_USER_APPROVAL_NOT_ISSUED",
            "CONCRETE_PRIVATE_KEY_SHA256_NOT_BOUND",
            "CONCRETE_API_CREDS_FINGERPRINT_NOT_BOUND",
            "CONCRETE_FUNDER_ADDRESS_NOT_BOUND",
            "CONCRETE_REMOTE_HOST_NOT_BOUND",
            "ORDER_CLIENT_NOT_IMPLEMENTED",
            "KILL_SWITCH_RUNTIME_PROCESS_NOT_BOUND",
            "FUNDING_AND_MAX_LOSS_LIMITS_NOT_BOUND_TO_RUNTIME",
            "PRIVATE_SHADOW_TELEMETRY_SAMPLE_NOT_PRESENT",
        ],
        "decision": {
            "offline_evaluator_bound": EVALUATOR.exists(),
            "runtime_kill_switch_ready": False,
            "cancel_action_authorized": False,
            "network_action_authorized": False,
            "execution_ready": False,
            "next_step": "implement/order-review actual runtime process that can honor evaluator decisions after exact approval; do not execute from this packet",
        },
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
    missing = [str(path) for path in [APPROVAL_PACKET, EVALUATOR, NO_SAMPLE_EVAL, RUNNER_DENY_AUDIT, BUILDER] if not path.exists()]
    if missing:
        raise SystemExit("missing required inputs: " + ", ".join(missing))
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_REVIEW_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_REVIEW_REPORT.md"
    write_json(packet_path, packet)
    write_report(report_path, packet)
    write_sha256sums(OUT, [packet_path, report_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "decision": packet["decision"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

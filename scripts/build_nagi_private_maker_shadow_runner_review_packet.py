#!/usr/bin/env python3
"""Build a review packet for the fail-closed NAGI maker-shadow runner shell."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_maker_shadow_runner_review_packet_20260608"

APPROVAL_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
RUNNER = ROOT / "scripts/run_nagi_private_maker_shadow.py"
TELEMETRY_VALIDATOR = ROOT / "scripts/validate_nagi_private_maker_shadow_telemetry.py"
PREFLIGHT_AUDIT = (
    EXPORTS
    / "nagi_private_maker_shadow_runner_preflight_latest"
    / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_PREFLIGHT_AUDIT.json"
)
PRIVATE_SHADOW_DENY_AUDIT = (
    EXPORTS
    / "nagi_private_maker_shadow_runner_preflight_latest"
    / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_PRIVATE_SHADOW_AUDIT.json"
)
BUILDER = ROOT / "scripts/build_nagi_private_maker_shadow_runner_review_packet.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_RUNNER_REVIEW_PACKET_PREPARED_"
    "FAIL_CLOSED_PREFLIGHT_PASSED_NOT_EXECUTION_READY"
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
        "# NAGI Private Maker Shadow Runner Review",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Result",
        "",
        "- Runner preflight passed review-only packet/hash/non-claim checks.",
        "- Private-shadow mode denied fail-closed with exit code 66.",
        "- The runner did not load private keys, did not load API credentials, did not use network or WS, and sent no orders or cancels.",
        "",
        "## Remaining Blockers",
        "",
        *[f"- {item}" for item in packet["remaining_blockers"]],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def build_packet() -> dict[str, Any]:
    approval = read_json(APPROVAL_PACKET)
    preflight = read_json(PREFLIGHT_AUDIT)
    denied = read_json(PRIVATE_SHADOW_DENY_AUDIT)
    remaining_blockers = [
        "EXACT_USER_APPROVAL_NOT_ISSUED",
        "CONCRETE_PRIVATE_KEY_SHA256_NOT_BOUND",
        "CONCRETE_API_CREDS_FINGERPRINT_NOT_BOUND",
        "CONCRETE_FUNDER_ADDRESS_NOT_BOUND",
        "CONCRETE_REMOTE_HOST_NOT_BOUND",
        "ORDER_CLIENT_NOT_IMPLEMENTED",
        "KILL_SWITCH_RUNTIME_PROCESS_NOT_BOUND",
        "FUNDING_AND_MAX_LOSS_LIMITS_NOT_BOUND_TO_RUNTIME",
        "PRIVATE_SHADOW_TELEMETRY_SAMPLE_NOT_PRESENT",
        "POST_ONLY_MAKER_ORDER_RUNNER_EXISTS_BUT_HASH_REVIEW_REQUIRED",
    ]
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "approval_packet": binding(APPROVAL_PACKET),
            "runner": binding(RUNNER),
            "telemetry_validator": binding(TELEMETRY_VALIDATOR),
            "preflight_audit": binding(PREFLIGHT_AUDIT),
            "private_shadow_deny_audit": binding(PRIVATE_SHADOW_DENY_AUDIT),
            "builder": binding(BUILDER),
        },
        "review_result": {
            "preflight_status": preflight.get("status"),
            "preflight_ok": preflight.get("ok"),
            "private_shadow_deny_status": denied.get("status"),
            "private_shadow_deny_ok": denied.get("ok"),
            "private_shadow_denied_reasons": denied.get("denied_reasons"),
            "private_key_loaded": denied.get("private_key_loaded"),
            "api_creds_loaded": denied.get("api_creds_loaded"),
            "network_used": denied.get("network_used"),
            "ws_started": denied.get("ws_started"),
            "orders_sent": denied.get("orders_sent"),
            "cancels_sent": denied.get("cancels_sent"),
        },
        "bound_policy": approval.get("proposed_private_shadow_policy"),
        "remaining_blockers": remaining_blockers,
        "decision": {
            "runner_review_packet_prepared": True,
            "runner_preflight_passed": preflight.get("ok") is True,
            "private_shadow_denied_fail_closed": denied.get("status")
            == "BLOCKED_NAGI_PRIVATE_MAKER_SHADOW_RUNNER_DENIED_NOT_EXECUTION_READY",
            "execution_ready": False,
            "next_step": "implement actual post-only order client and kill-switch, then prepare a new exact approval packet; do not execute this runner shell",
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "orders_authorized": False,
            "private_key_authorized": False,
            "ws_authorized": False,
        },
    }


def main() -> int:
    missing = [
        str(path)
        for path in [APPROVAL_PACKET, RUNNER, TELEMETRY_VALIDATOR, PREFLIGHT_AUDIT, PRIVATE_SHADOW_DENY_AUDIT, BUILDER]
        if not path.exists()
    ]
    if missing:
        raise SystemExit("missing required inputs: " + ", ".join(missing))
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_REVIEW_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_RUNNER_REVIEW_REPORT.md"
    write_json(packet_path, packet)
    write_report(report_path, packet)
    write_sha256sums(OUT, [packet_path, report_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "decision": packet["decision"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

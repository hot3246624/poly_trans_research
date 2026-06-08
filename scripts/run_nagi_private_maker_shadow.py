#!/usr/bin/env python3
"""Fail-closed runner shell for future NAGI private maker-shadow review.

This is not an order runner yet. It intentionally avoids private-key loading,
network connections, CLOB SDK imports, order placement, and cancellation. Its
job is to bind the approval packet and make unsafe execution attempts fail
closed with an audit artifact.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_PACKET = (
    ROOT
    / "data/exports/nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
DEFAULT_OUT = ROOT / "data/exports/nagi_private_maker_shadow_runner_preflight_latest"
EXPECTED_STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET_PREPARED_REVIEW_ONLY_"
    "IMPLEMENTATION_AND_EXACT_APPROVAL_REQUIRED_NOT_EXECUTION_READY"
)
DENIED_STATUS = "BLOCKED_NAGI_PRIVATE_MAKER_SHADOW_RUNNER_DENIED_NOT_EXECUTION_READY"
PREFLIGHT_STATUS = "KEEP_NAGI_PRIVATE_MAKER_SHADOW_RUNNER_PREFLIGHT_REVIEWED_NOT_EXECUTION_READY"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def check_bound_file(label: str, binding: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    path = Path(str(binding.get("path") or ""))
    expected = binding.get("sha256")
    result = {"label": label, "path": str(path), "exists": path.exists(), "sha256_ok": None}
    if not path.exists():
        issues.append(f"{label}:MISSING:{path}")
        result["sha256_ok"] = False
        return result
    if expected:
        actual = sha256_file(path)
        result["actual_sha256"] = actual
        result["expected_sha256"] = expected
        result["sha256_ok"] = actual == expected
        if actual != expected:
            issues.append(f"{label}:SHA256_MISMATCH")
    return result


def validate_packet(packet_path: Path) -> tuple[dict[str, Any], list[str], list[dict[str, Any]]]:
    packet = read_json(packet_path)
    issues: list[str] = []
    bound_checks: list[dict[str, Any]] = []

    if packet.get("status") != EXPECTED_STATUS:
        issues.append("PACKET_STATUS_NOT_EXPECTED_REVIEW_ONLY")
    readiness = packet.get("execution_readiness") or {}
    if readiness.get("execution_ready") is not False:
        issues.append("PACKET_EXECUTION_READY_NOT_FALSE")
    if readiness.get("execution_authorized") is not False:
        issues.append("PACKET_EXECUTION_AUTHORIZED_NOT_FALSE")
    for key in (
        "private_key_loading_authorized",
        "api_creds_use_authorized",
        "orders_authorized",
        "cancels_authorized",
    ):
        if readiness.get(key) is not False:
            issues.append(f"PACKET_{key.upper()}_NOT_FALSE")

    for key, value in (packet.get("non_claims") or {}).items():
        if value is not False:
            issues.append(f"NON_CLAIM_{key}_NOT_FALSE")

    source_bindings = packet.get("source_bindings") or {}
    for label in (
        "requirements_packet",
        "residual_matrix_packet",
        "maker_queue_proxy_packet",
        "pivot_packet",
        "telemetry_validator",
        "builder",
    ):
        binding = source_bindings.get(label)
        if isinstance(binding, dict):
            bound_checks.append(check_bound_file(label, binding, issues))
        else:
            issues.append(f"{label}:BINDING_MISSING")

    runner_binding = source_bindings.get("future_runner_expected_path")
    if isinstance(runner_binding, dict) and runner_binding.get("exists") is True and runner_binding.get("sha256"):
        bound_checks.append(check_bound_file("future_runner_expected_path", runner_binding, issues))
    else:
        issues.append("RUNNER_NOT_HASH_BOUND_IN_PACKET")

    blockers = set(readiness.get("blockers") or [])
    required_blockers = {
        "EXACT_USER_APPROVAL_NOT_ISSUED",
        "CONCRETE_PRIVATE_KEY_SHA256_NOT_BOUND",
        "CONCRETE_API_CREDS_FINGERPRINT_NOT_BOUND",
        "CONCRETE_FUNDER_ADDRESS_NOT_BOUND",
        "CONCRETE_REMOTE_HOST_NOT_BOUND",
        "KILL_SWITCH_RUNTIME_PROCESS_NOT_BOUND",
        "FUNDING_AND_MAX_LOSS_LIMITS_NOT_BOUND_TO_RUNTIME",
        "PRIVATE_SHADOW_TELEMETRY_SAMPLE_NOT_PRESENT",
        "POST_ONLY_MAKER_ORDER_RUNNER_EXISTS_BUT_HASH_REVIEW_REQUIRED",
    }
    missing_blockers = sorted(required_blockers - blockers)
    if missing_blockers:
        issues.append("REQUIRED_BLOCKERS_MISSING:" + ",".join(missing_blockers))

    return packet, issues, bound_checks


def build_audit(args: argparse.Namespace, packet: dict[str, Any], issues: list[str], bound_checks: list[dict[str, Any]]) -> dict[str, Any]:
    mode = args.mode
    unsafe_mode = mode in {"private-shadow", "emergency-cancel"}
    denied_reasons = list(issues)
    if unsafe_mode:
        denied_reasons.extend(
            [
                "THIS_RUNNER_IS_REVIEW_ONLY",
                "ORDER_CLIENT_NOT_IMPLEMENTED",
                "PRIVATE_KEY_LOADING_DISABLED",
                "NETWORK_DISABLED",
                "EXACT_APPROVAL_REQUIRED",
            ]
        )
    status = DENIED_STATUS if unsafe_mode or issues else PREFLIGHT_STATUS
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "mode": mode,
        "status": status,
        "ok": not issues and not unsafe_mode,
        "packet_path": str(args.packet),
        "packet_sha256": sha256_file(Path(args.packet)),
        "packet_status": packet.get("status"),
        "execution_ready": False,
        "execution_authorized": False,
        "private_key_loaded": False,
        "api_creds_loaded": False,
        "network_used": False,
        "ws_started": False,
        "orders_sent": 0,
        "cancels_sent": 0,
        "bound_checks": bound_checks,
        "issues": issues,
        "denied_reasons": denied_reasons,
        "next_step": "prepare a new exact approval packet only after runner, kill-switch, key/host/limit bindings, and telemetry samples are reviewed",
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["preflight", "self-test", "private-shadow", "emergency-cancel"], default="preflight")
    parser.add_argument("--packet", default=str(DEFAULT_PACKET))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    packet_path = Path(args.packet)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    packet, issues, bound_checks = validate_packet(packet_path)
    if args.mode == "self-test":
        issues = list(issues)
    audit = build_audit(args, packet, issues, bound_checks)
    audit_path = out_dir / f"NAGI_PRIVATE_MAKER_SHADOW_RUNNER_{args.mode.replace('-', '_').upper()}_AUDIT.json"
    write_json(audit_path, audit)
    print(json.dumps({"audit": str(audit_path), "status": audit["status"], "ok": audit["ok"], "issues": audit["issues"], "denied_reasons": audit["denied_reasons"]}, indent=2, sort_keys=True))
    if args.mode in {"private-shadow", "emergency-cancel"}:
        return 66
    return 0 if audit["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

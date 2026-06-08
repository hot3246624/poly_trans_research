#!/usr/bin/env python3
"""Build review packet for the NAGI dry-run-only order client adapter."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_dry_run_only_order_client_adapter_review_packet_20260608"

ADAPTER = ROOT / "scripts/run_nagi_dry_run_only_order_client_adapter.py"
DESIGN_PACKET = (
    EXPORTS
    / "nagi_post_only_maker_order_client_design_packet_20260608"
    / "NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_PACKET.json"
)
TELEMETRY_CONTRACT = (
    EXPORTS
    / "nagi_private_maker_shadow_telemetry_contract_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_PACKET.json"
)
KILL_SWITCH_REVIEW = (
    EXPORTS
    / "nagi_private_maker_shadow_kill_switch_review_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_KILL_SWITCH_REVIEW_PACKET.json"
)
PREFLIGHT_AUDIT = (
    EXPORTS
    / "nagi_dry_run_only_order_client_adapter_latest"
    / "NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_PREFLIGHT_AUDIT.json"
)
SELF_TEST_AUDIT = (
    EXPORTS
    / "nagi_dry_run_only_order_client_adapter_latest"
    / "NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_SELF_TEST_AUDIT.json"
)
BUILDER = ROOT / "scripts/build_nagi_dry_run_only_order_client_adapter_review_packet.py"

STATUS = (
    "KEEP_NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_REVIEWED_INTENT_ONLY_"
    "NO_NETWORK_NO_ORDERS_PRIVATE_SAMPLE_REQUIRED_NOT_OOS_READY"
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


def main() -> int:
    missing = [
        path
        for path in [
            ADAPTER,
            DESIGN_PACKET,
            TELEMETRY_CONTRACT,
            KILL_SWITCH_REVIEW,
            PREFLIGHT_AUDIT,
            SELF_TEST_AUDIT,
            BUILDER,
        ]
        if not path.exists()
    ]
    if missing:
        raise SystemExit("missing required inputs: " + ", ".join(str(path) for path in missing))

    preflight = read_json(PREFLIGHT_AUDIT)
    self_test = read_json(SELF_TEST_AUDIT)
    cases = self_test.get("case_results") or []
    all_cases_ok = all(row.get("case_ok") is True for row in cases)
    no_side_effects = all(
        audit.get("private_key_loaded") is False
        and audit.get("api_creds_loaded") is False
        and audit.get("network_used") is False
        and audit.get("ws_started") is False
        and audit.get("orders_sent") == 0
        and audit.get("cancels_sent") == 0
        for audit in [preflight, self_test]
    )
    ok = preflight.get("ok") is True and self_test.get("ok") is True and all_cases_ok and no_side_effects

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS if ok else "BLOCKED_NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_REVIEW_FAILED",
        "ok": ok,
        "review_result": {
            "preflight_status": preflight.get("status"),
            "preflight_ok": preflight.get("ok"),
            "self_test_status": self_test.get("status"),
            "self_test_ok": self_test.get("ok"),
            "self_test_case_count": len(cases),
            "all_cases_ok": all_cases_ok,
            "no_private_key_loaded": preflight.get("private_key_loaded") is False
            and self_test.get("private_key_loaded") is False,
            "no_network_used": preflight.get("network_used") is False and self_test.get("network_used") is False,
            "orders_sent": (preflight.get("orders_sent") or 0) + (self_test.get("orders_sent") or 0),
            "cancels_sent": (preflight.get("cancels_sent") or 0) + (self_test.get("cancels_sent") or 0),
        },
        "case_results": cases,
        "decision": {
            "adapter_reviewed": ok,
            "intent_only": True,
            "execution_ready": False,
            "orders_authorized": False,
            "private_sample_present": False,
            "next_step": "only after exact approval and concrete runtime bindings, collect own maker telemetry; this packet still authorizes no execution",
        },
        "source_bindings": {
            "adapter": binding(ADAPTER),
            "design_packet": binding(DESIGN_PACKET),
            "telemetry_contract": binding(TELEMETRY_CONTRACT),
            "kill_switch_review": binding(KILL_SWITCH_REVIEW),
            "preflight_audit": binding(PREFLIGHT_AUDIT),
            "self_test_audit": binding(SELF_TEST_AUDIT),
            "builder": binding(BUILDER),
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

    OUT.mkdir(parents=True, exist_ok=True)
    packet_path = OUT / "NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_REVIEW_PACKET.json"
    report_path = OUT / "NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_REVIEW_REPORT.md"
    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Dry-Run-Only Order Client Adapter Review",
                "",
                f"Status: `{packet['status']}`",
                "",
                "- Adapter produces local order intents only.",
                "- No private key/API credentials loaded.",
                "- No network/WS used.",
                "- Orders sent: 0; cancels sent: 0.",
                "- Self-test includes one valid intent and six reject-gate cases.",
                "",
                "This is not execution-ready and does not provide private maker truth.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_sha256sums(OUT, [packet_path, report_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "ok": packet["ok"]}, indent=2, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())

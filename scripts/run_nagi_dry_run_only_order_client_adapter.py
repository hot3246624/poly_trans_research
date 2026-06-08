#!/usr/bin/env python3
"""Dry-run-only post-only maker order-intent adapter for NAGI review.

This adapter intentionally has no network, key loading, SDK import, order
submission, or cancellation path. It only proves that future decision rows can
be transformed into local order intents after the review-only reject gates pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_DESIGN_PACKET = (
    ROOT
    / "data/exports/nagi_post_only_maker_order_client_design_packet_20260608"
    / "NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_PACKET.json"
)
DEFAULT_OUT = ROOT / "data/exports/nagi_dry_run_only_order_client_adapter_latest"

STATUS_PREFLIGHT = "KEEP_NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_PREFLIGHT_REVIEWED_NOT_EXECUTION_READY"
STATUS_SELF_TEST = "KEEP_NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_SELF_TEST_REVIEWED_NOT_EXECUTION_READY"
STATUS_DENIED = "BLOCKED_NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_DENIED_NOT_EXECUTION_READY"


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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def validate_design(packet: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if packet.get("status") != "KEEP_NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_PACKET_PREPARED_REVIEW_ONLY_IMPLEMENTATION_REQUIRED_NOT_EXECUTION_READY":
        issues.append("DESIGN_PACKET_STATUS_UNEXPECTED")
    decision = packet.get("decision") or {}
    if decision.get("execution_ready") is not False:
        issues.append("DESIGN_EXECUTION_READY_NOT_FALSE")
    if decision.get("orders_authorized") is not False:
        issues.append("DESIGN_ORDERS_AUTHORIZED_NOT_FALSE")
    contract = packet.get("client_design_contract") or {}
    if contract.get("default_mode") != "dry_run_only":
        issues.append("DESIGN_DEFAULT_MODE_NOT_DRY_RUN_ONLY")
    for key, value in (packet.get("non_claims") or {}).items():
        if value is not False:
            issues.append(f"NON_CLAIM_{key}_NOT_FALSE")
    return issues


def base_decision() -> dict[str, Any]:
    return {
        "decision_id": "dry_run_decision_001",
        "condition_id": "dry_run_condition_001",
        "decision_ts_ms": 1000000000,
        "remaining_ms": 45000,
        "variant_id": "up_35_50_all__pc0.995__qmin0",
        "side": "YES",
        "asset_id": "dry_run_asset_yes",
        "bid_px": 0.42,
        "opp_bid_px": 0.57,
        "pair_cost_at_decision": 0.99,
        "l1_age_ms": 10,
        "l2_age_ms": 10,
        "align_lag_ms": 10,
        "size": 1.0,
        "kill_switch_clear": True,
        "telemetry_sink_available": True,
    }


def evaluate_decision(design: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    policy = (design.get("strategy_context") or {}).get("primary_lane", {}).get("policy_hint", {})
    band = policy.get("bid_price_band") or [0.35, 0.50]
    bid_lo = float(band[0])
    bid_hi = float(band[1])
    pair_cap = float(policy.get("pair_cost_cap") or 0.995)
    reasons: list[str] = []

    if str(decision.get("variant_id") or "") != "up_35_50_all__pc0.995__qmin0":
        reasons.append("variant_id_not_primary")
    if str(decision.get("side") or "").upper() != "YES":
        reasons.append("side_not_yes")
    bid_px = as_float(decision.get("bid_px"), -1.0)
    if bid_px < bid_lo or bid_px >= bid_hi:
        reasons.append("bid_px_outside_bound_band")
    if as_float(decision.get("remaining_ms"), 1e18) <= 0 or as_float(decision.get("remaining_ms"), 1e18) > 60000:
        reasons.append("remaining_ms_outside_policy_window")
    if as_float(decision.get("pair_cost_at_decision"), 1e18) > pair_cap:
        reasons.append("pair_cost_at_decision_gt_policy_cap")
    if as_float(decision.get("l1_age_ms"), 1e18) > 500 or as_float(decision.get("l2_age_ms"), 1e18) > 500:
        reasons.append("l1_or_l2_age_ms_gt_500")
    if as_float(decision.get("align_lag_ms"), 1e18) > 500:
        reasons.append("l1_l2_align_lag_ms_gt_500")
    if not decision.get("kill_switch_clear"):
        reasons.append("kill_switch_triggered_or_unavailable")
    if not decision.get("telemetry_sink_available"):
        reasons.append("telemetry_sink_unavailable")
    if as_float(decision.get("size"), 0.0) <= 0:
        reasons.append("order_size_invalid")

    if reasons:
        return {
            "intent_ok": False,
            "reject_reasons": reasons,
            "order_intent": None,
        }

    client_order_id = (
        f"NAGI_PRIVATE_MAKER_SHADOW_V1:{decision['condition_id']}:"
        f"{decision['side']}:{bid_px:.3f}:{as_float(decision.get('size'), 0.0):.4f}:dryrun"
    )
    return {
        "intent_ok": True,
        "reject_reasons": [],
        "order_intent": {
            "client_order_id": client_order_id,
            "condition_id": decision["condition_id"],
            "asset_id": decision["asset_id"],
            "side": decision["side"],
            "price": bid_px,
            "size": as_float(decision.get("size"), 0.0),
            "order_type": "post-only maker-only limit bid",
            "dry_run_only": True,
            "network_authorized": False,
            "orders_authorized": False,
        },
    }


def self_test(design: dict[str, Any]) -> list[dict[str, Any]]:
    cases: list[tuple[str, dict[str, Any], bool]] = []
    valid = base_decision()
    cases.append(("valid_intent", valid, True))
    for case_id, key, value in [
        ("reject_paircost", "pair_cost_at_decision", 1.0),
        ("reject_price_band", "bid_px", 0.51),
        ("reject_stale_l1", "l1_age_ms", 501),
        ("reject_remaining_ms", "remaining_ms", 61000),
        ("reject_kill_switch", "kill_switch_clear", False),
        ("reject_no_telemetry", "telemetry_sink_available", False),
    ]:
        row = dict(valid)
        row["decision_id"] = case_id
        row[key] = value
        cases.append((case_id, row, False))

    results: list[dict[str, Any]] = []
    for case_id, decision, expect_ok in cases:
        result = evaluate_decision(design, decision)
        results.append(
            {
                "case_id": case_id,
                "expect_ok": expect_ok,
                "intent_ok": result["intent_ok"],
                "reject_reasons": result["reject_reasons"],
                "order_intent": result["order_intent"],
                "case_ok": result["intent_ok"] is expect_ok,
            }
        )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["preflight", "self-test", "build-intent"], default="preflight")
    parser.add_argument("--design-packet", default=str(DEFAULT_DESIGN_PACKET))
    parser.add_argument("--decision-json")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    design_path = Path(args.design_packet)
    design = read_json(design_path)
    issues = validate_design(design)

    result: dict[str, Any] = {
        "schema_version": 1,
        "created_at": utc_now(),
        "mode": args.mode,
        "design_packet": str(design_path),
        "design_packet_sha256": sha256_file(design_path),
        "private_key_loaded": False,
        "api_creds_loaded": False,
        "network_used": False,
        "ws_started": False,
        "orders_sent": 0,
        "cancels_sent": 0,
        "execution_ready": False,
        "orders_authorized": False,
        "issues": issues,
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

    if args.mode == "preflight":
        result["status"] = STATUS_PREFLIGHT if not issues else STATUS_DENIED
        result["ok"] = not issues
    elif args.mode == "self-test":
        cases = self_test(design)
        result["case_results"] = cases
        result["status"] = STATUS_SELF_TEST if not issues and all(row["case_ok"] for row in cases) else STATUS_DENIED
        result["ok"] = not issues and all(row["case_ok"] for row in cases)
    else:
        if not args.decision_json:
            issues.append("DECISION_JSON_REQUIRED")
            decision = {}
        else:
            decision = read_json(Path(args.decision_json))
        intent = evaluate_decision(design, decision) if not issues else {"intent_ok": False, "reject_reasons": issues, "order_intent": None}
        result["intent_result"] = intent
        result["status"] = STATUS_DENIED if issues or not intent["intent_ok"] else STATUS_PREFLIGHT
        result["ok"] = not issues and intent["intent_ok"]

    out_path = out_dir / f"NAGI_DRY_RUN_ONLY_ORDER_CLIENT_ADAPTER_{args.mode.replace('-', '_').upper()}_AUDIT.json"
    write_json(out_path, result)
    print(json.dumps({"audit": str(out_path), "status": result["status"], "ok": result["ok"], "issues": result["issues"]}, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

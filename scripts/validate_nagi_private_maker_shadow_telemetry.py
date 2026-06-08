#!/usr/bin/env python3
"""Validate future NAGI private maker-shadow telemetry artifacts.

This validator is deliberately offline-only. It never loads private keys, never
connects to CLOB, and never places or cancels orders. Its job is to fail-close
future telemetry evidence before any private-truth or OOS discussion.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


DEFAULT_PACKET = (
    Path("/Users/hot/web3Scientist/poly_trans_research")
    / "data/exports/nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)


REQUIRED_DECISION_COLUMNS = {
    "decision_id",
    "condition_id",
    "decision_ts_ms",
    "remaining_ms",
    "variant_id",
    "side",
    "asset_id",
    "bid_px",
    "opp_bid_px",
    "pair_cost_at_decision",
    "l1_age_ms",
    "l2_age_ms",
    "align_lag_ms",
}
REQUIRED_ORDER_COLUMNS = {
    "decision_id",
    "client_order_id",
    "order_id",
    "submit_ts_ms",
    "ack_ts_ms",
    "post_only_flag",
    "price",
    "size",
    "status",
}
REQUIRED_FILL_COLUMNS = {
    "decision_id",
    "client_order_id",
    "order_id",
    "trade_id",
    "fill_ts_ms",
    "maker_or_taker",
    "fill_px",
    "fill_qty",
    "fee_paid",
    "fee_rate_bps",
}
REQUIRED_INVENTORY_COLUMNS = {
    "condition_id",
    "asset_id",
    "outcome",
    "source_kind",
    "size",
    "recv_ms",
    "drift_flag",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def as_float(value: Any, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def require_columns(name: str, rows: list[dict[str, str]], required: set[str], issues: list[str]) -> None:
    if not rows:
        issues.append(f"{name}:EMPTY")
        return
    got = set(rows[0].keys())
    missing = sorted(required - got)
    if missing:
        issues.append(f"{name}:MISSING_COLUMNS:{','.join(missing)}")


def validate(args: argparse.Namespace) -> dict[str, Any]:
    packet = json.loads(Path(args.packet).read_text(encoding="utf-8"))
    issues: list[str] = []
    warnings: list[str] = []

    decisions = read_csv(Path(args.decisions_csv))
    orders = read_csv(Path(args.orders_csv))
    fills = read_csv(Path(args.fills_csv))
    inventory = read_csv(Path(args.inventory_csv))

    require_columns("decisions", decisions, REQUIRED_DECISION_COLUMNS, issues)
    require_columns("orders", orders, REQUIRED_ORDER_COLUMNS, issues)
    require_columns("fills", fills, REQUIRED_FILL_COLUMNS, issues)
    require_columns("inventory", inventory, REQUIRED_INVENTORY_COLUMNS, issues)

    policy = packet.get("proposed_private_shadow_policy") or {}
    expected_variant_id = str(policy.get("primary_variant_id") or "up_35_50_all__pc0.995__qmin0")
    expected_side = str(policy.get("primary_side") or "YES").upper()
    bid_band = policy.get("bid_price_band") or [0.35, 0.50]
    bid_lo = float(bid_band[0])
    bid_hi = float(bid_band[1])
    pair_cost_cap = float(policy.get("pair_cost_cap") or 0.995)
    max_remaining_ms = 60000.0

    decision_by_id = {row.get("decision_id"): row for row in decisions if row.get("decision_id")}
    orders_by_decision: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in orders:
        if row.get("decision_id"):
            orders_by_decision[row["decision_id"]].append(row)

    counted: list[dict[str, Any]] = []
    taker_or_ambiguous = 0
    nonzero_fee = 0
    stale_or_misaligned = 0
    policy_decision_breach = 0
    missing_ack = 0
    missing_cancel = 0
    missing_order = 0
    inventory_drift = 0
    pair_costs: list[float] = []
    residual_conditions: set[str] = set()
    filled_conditions: set[str] = set()

    for row in decisions:
        variant_id = str(row.get("variant_id") or "")
        side = str(row.get("side") or "").upper()
        bid_px = as_float(row.get("bid_px"), -1.0)
        remaining_ms = as_float(row.get("remaining_ms"), 1e18)
        pair_cost = as_float(row.get("pair_cost_at_decision"), 1e18)
        l1_age = as_float(row.get("l1_age_ms"), 1e18)
        l2_age = as_float(row.get("l2_age_ms"), 1e18)
        align_lag = as_float(row.get("align_lag_ms"), 1e18)
        if (
            variant_id != expected_variant_id
            or side != expected_side
            or bid_px is None
            or bid_px < bid_lo
            or bid_px >= bid_hi
            or remaining_ms is None
            or remaining_ms <= 0
            or remaining_ms > max_remaining_ms
            or pair_cost is None
            or pair_cost > pair_cost_cap
            or l1_age is None
            or l2_age is None
            or align_lag is None
            or l1_age > 500
            or l2_age > 500
            or align_lag > 500
        ):
            policy_decision_breach += 1

    for row in inventory:
        drift_flag = as_bool(row.get("drift_flag")) or str(row.get("source_kind") or "").strip().upper() in {
            "DRIFT",
            "UNRECONCILED",
            "UNKNOWN",
        }
        if drift_flag:
            inventory_drift += 1

    for fill in fills:
        decision_id = fill.get("decision_id")
        decision = decision_by_id.get(decision_id)
        if not decision:
            issues.append(f"fill:{fill.get('trade_id') or '<missing_trade_id>'}:DECISION_MISSING")
            continue
        condition_id = decision.get("condition_id") or ""
        maker_or_taker = str(fill.get("maker_or_taker") or "").strip().upper()
        fee_paid = as_float(fill.get("fee_paid"), 0.0) or 0.0
        fee_rate_bps = as_float(fill.get("fee_rate_bps"), 0.0) or 0.0
        l1_age = as_float(decision.get("l1_age_ms"), 1e18) or 1e18
        l2_age = as_float(decision.get("l2_age_ms"), 1e18) or 1e18
        align_lag = as_float(decision.get("align_lag_ms"), 1e18) or 1e18
        pair_cost = as_float(decision.get("pair_cost_at_decision"))
        if pair_cost is not None:
            pair_costs.append(pair_cost)
        variant_id = str(decision.get("variant_id") or "")
        side = str(decision.get("side") or "").upper()
        bid_px = as_float(decision.get("bid_px"), -1.0)
        remaining_ms = as_float(decision.get("remaining_ms"), 1e18)

        decision_orders = orders_by_decision.get(decision_id, [])
        if not decision_orders:
            missing_order += 1
            continue
        post_only_ok = any(as_bool(order.get("post_only_flag")) for order in decision_orders)
        ack_ok = any(str(order.get("ack_ts_ms") or "").strip() for order in decision_orders)
        cancel_or_full_ok = any(
            str(order.get("cancel_ack_ts_ms") or "").strip()
            or str(order.get("remaining_open_qty") or "").strip() in {"0", "0.0"}
            or str(order.get("status") or "").strip().lower() in {"filled", "matched"}
            for order in decision_orders
        )

        if maker_or_taker != "MAKER":
            taker_or_ambiguous += 1
            continue
        if fee_paid != 0.0 or fee_rate_bps != 0.0:
            nonzero_fee += 1
            continue
        if (
            variant_id != expected_variant_id
            or side != expected_side
            or bid_px is None
            or bid_px < bid_lo
            or bid_px >= bid_hi
            or remaining_ms is None
            or remaining_ms <= 0
            or remaining_ms > max_remaining_ms
            or pair_cost is None
            or pair_cost > pair_cost_cap
        ):
            policy_decision_breach += 1
            continue
        if l1_age > 500 or l2_age > 500 or align_lag > 500:
            stale_or_misaligned += 1
            continue
        if not post_only_ok or not ack_ok:
            missing_ack += 1
            continue
        if not cancel_or_full_ok:
            missing_cancel += 1
            continue

        filled_conditions.add(condition_id)
        counted.append({"decision_id": decision_id, "condition_id": condition_id, "pair_cost": pair_cost})

    filled_decisions = {row["decision_id"] for row in counted}
    for row in decisions:
        if row.get("decision_id") not in filled_decisions and row.get("condition_id"):
            residual_conditions.add(row["condition_id"])

    filled_actions = len(counted)
    filled_markets = len(filled_conditions)
    decision_markets = len({row.get("condition_id") for row in decisions if row.get("condition_id")})
    residual_cost_rate_proxy = (
        len(residual_conditions - filled_conditions) / decision_markets if decision_markets else 1.0
    )
    pair_cost_p50 = median([p for p in pair_costs if p is not None]) if pair_costs else None
    taker_fill_share = taker_or_ambiguous / len(fills) if fills else 1.0

    targets = packet.get("review_targets_before_any_oos_discussion") or {}
    pass_minimum = (
        filled_markets >= 100
        and filled_actions >= 500
        and taker_or_ambiguous == 0
        and nonzero_fee == 0
        and stale_or_misaligned == 0
        and missing_order == 0
        and missing_ack == 0
        and missing_cancel == 0
        and policy_decision_breach == 0
        and inventory_drift == 0
        and pair_cost_p50 is not None
        and pair_cost_p50 <= pair_cost_cap
        and residual_cost_rate_proxy <= 0.20
    )

    if taker_or_ambiguous:
        issues.append("TAKER_OR_AMBIGUOUS_FILL_OBSERVED")
    if nonzero_fee:
        issues.append("NONZERO_MAKER_FEE_OBSERVED")
    if stale_or_misaligned:
        issues.append("STALE_OR_MISALIGNED_COUNTED_SAMPLE")
    if missing_order:
        issues.append("MISSING_ORDER_TELEMETRY")
    if missing_ack:
        issues.append("MISSING_ACK_OR_POST_ONLY")
    if missing_cancel:
        issues.append("MISSING_CANCEL_OR_FULL_FILL")
    if policy_decision_breach:
        issues.append("POLICY_DECISION_BREACH")
    if inventory_drift:
        issues.append("INVENTORY_DRIFT_OBSERVED")
    if pair_cost_p50 is not None and pair_cost_p50 > pair_cost_cap:
        issues.append("PAIR_COST_P50_ABOVE_POLICY_CAP")
    if residual_cost_rate_proxy > 0.20:
        issues.append("RESIDUAL_COST_RATE_ABOVE_0_20")
    if not pass_minimum:
        issues.append("MINIMUM_PRIVATE_MAKER_SHADOW_TARGETS_NOT_MET")
    if targets:
        warnings.append("targets_loaded_from_packet")

    status = (
        "KEEP_NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_REVIEW_PASSED_NOT_OOS_READY"
        if not issues
        else "BLOCKED_NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_REVIEW_FAILED_NOT_OOS_READY"
    )
    return {
        "status": status,
        "ok": not issues,
        "issues": issues,
        "warnings": warnings,
        "summary": {
            "decision_rows": len(decisions),
            "order_rows": len(orders),
            "fill_rows": len(fills),
            "inventory_rows": len(inventory),
            "counted_maker_fee0_fill_actions": filled_actions,
            "counted_maker_fee0_fill_markets": filled_markets,
            "decision_markets": decision_markets,
            "taker_or_ambiguous_fill_count": taker_or_ambiguous,
            "nonzero_fee_fill_count": nonzero_fee,
            "stale_or_misaligned_fill_count": stale_or_misaligned,
            "policy_decision_breach_count": policy_decision_breach,
            "missing_order_count": missing_order,
            "missing_ack_or_post_only_count": missing_ack,
            "missing_cancel_or_full_fill_count": missing_cancel,
            "inventory_drift_count": inventory_drift,
            "taker_fill_share": round(taker_fill_share, 6),
            "pair_cost_p50": round(pair_cost_p50, 6) if pair_cost_p50 is not None else None,
            "residual_cost_rate_proxy": round(residual_cost_rate_proxy, 6),
        },
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
    parser.add_argument("--packet", default=str(DEFAULT_PACKET))
    parser.add_argument("--decisions-csv", required=True)
    parser.add_argument("--orders-csv", required=True)
    parser.add_argument("--fills-csv", required=True)
    parser.add_argument("--inventory-csv", required=True)
    parser.add_argument("--out-json")
    args = parser.parse_args()

    result = validate(args)
    text = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.out_json:
        Path(args.out_json).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

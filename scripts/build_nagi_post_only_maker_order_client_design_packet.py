#!/usr/bin/env python3
"""Build the review-only design packet for a NAGI post-only maker order client."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_post_only_maker_order_client_design_packet_20260608"
BUILDER = ROOT / "scripts/build_nagi_post_only_maker_order_client_design_packet.py"

SYNTHESIS_PACKET = (
    EXPORTS
    / "nagi_strategy_synthesis_packet_20260608"
    / "NAGI_STRATEGY_SYNTHESIS_PACKET.json"
)
APPROVAL_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_approval_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
RUNNER_SHELL = ROOT / "scripts/run_nagi_private_maker_shadow.py"
KILL_SWITCH_EVALUATOR = ROOT / "scripts/evaluate_nagi_private_maker_shadow_kill_switch.py"
TELEMETRY_VALIDATOR = ROOT / "scripts/validate_nagi_private_maker_shadow_telemetry.py"

STATUS = (
    "KEEP_NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_PACKET_PREPARED_REVIEW_ONLY_"
    "IMPLEMENTATION_REQUIRED_NOT_EXECUTION_READY"
)

NON_CLAIMS = {
    "api_creds_authorized": False,
    "cancels_authorized": False,
    "deployable": False,
    "live_ready": False,
    "maker_fill_proven": False,
    "network_authorized": False,
    "oos_authorized": False,
    "oos_ready": False,
    "orders_authorized": False,
    "private_key_authorized": False,
    "private_truth_ready": False,
    "queue_priority_proven": False,
    "runner_authorized": False,
    "strategy_promotion_ready": False,
    "ws_authorized": False,
}


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


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: order-client design packet is review-only; no SDK, no keys, no orders.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    synthesis = load_json(SYNTHESIS_PACKET)
    approval = load_json(APPROVAL_PACKET)

    primary_lane = next(
        lane for lane in synthesis["candidate_lanes"] if lane["lane_id"] == "NAGI_ANCHOR_LAST60_YES_35_50"
    )
    secondary_lane = next(
        lane for lane in synthesis["candidate_lanes"] if lane["lane_id"] == "BROAD_MAKER_FULL300_YES_35_50"
    )

    reject_gates = [
        "exact_approval_json_missing_or_sha_mismatch",
        "private_key_sha256_not_bound",
        "api_creds_fingerprint_not_bound",
        "funder_address_not_bound",
        "remote_host_not_bound",
        "funding_or_max_loss_limit_not_bound",
        "current_live_btc5m_market_missing",
        "market_remaining_ms_outside_policy_window",
        "l1_or_l2_age_ms_gt_500",
        "l1_l2_align_lag_ms_gt_500",
        "bid_px_outside_bound_band",
        "pair_cost_at_decision_gt_policy_cap",
        "post_only_flag_not_supported_by_client",
        "would_cross_or_take_liquidity",
        "order_size_exceeds_bound_microlot_or_market_cap",
        "open_order_or_inventory_drift_unreconciled",
        "kill_switch_triggered_or_unavailable",
        "telemetry_sink_unavailable",
    ]

    telemetry_contract = {
        "decisions_csv_required_columns": [
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
        ],
        "orders_csv_required_columns": [
            "decision_id",
            "client_order_id",
            "order_id",
            "submit_ts_ms",
            "ack_ts_ms",
            "post_only_flag",
            "price",
            "size",
            "status",
            "cancel_ack_ts_ms",
            "remaining_open_qty",
        ],
        "fills_csv_required_columns": [
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
        ],
        "inventory_csv_required_columns": [
            "condition_id",
            "asset_id",
            "outcome",
            "source_kind",
            "size",
            "recv_ms",
        ],
        "validator": str(TELEMETRY_VALIDATOR),
    }

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "highest_allowed_status": "review-only design, not execution-ready",
        "decision": {
            "design_packet_prepared": True,
            "implementation_required": True,
            "execution_ready": False,
            "orders_authorized": False,
            "next_step": "implement fail-closed dry-run-only order-client adapter and bind a runtime kill-switch packet; exact user approval still required before any private shadow execution",
        },
        "strategy_context": {
            "stage": synthesis["decision"]["strategy_stage"],
            "primary_lane": primary_lane,
            "secondary_lane": secondary_lane,
            "official_fee_boundary": synthesis["official_fee_boundary"],
            "private_truth_gate": synthesis["private_truth_gate"],
        },
        "client_design_contract": {
            "client_id": "NAGI_POST_ONLY_MAKER_ORDER_CLIENT_V1_DESIGN",
            "scope": "BTC 5m current/live market only; review-only design",
            "default_mode": "dry_run_only",
            "network_default_authorized": False,
            "private_key_default_authorized": False,
            "orders_default_authorized": False,
            "cancels_default_authorized": False,
            "allowed_order_type_if_later_approved": "post-only maker-only limit bid",
            "forbidden_order_types": [
                "market_order",
                "ioc",
                "fok",
                "crossing_limit",
                "non_post_only_limit",
                "auto_retry_that_can_take_liquidity",
            ],
            "reject_gates": reject_gates,
            "required_runtime_bindings_before_execution": approval["execution_readiness"]["blockers"],
            "kill_switch_integration": {
                "offline_evaluator": str(KILL_SWITCH_EVALUATOR),
                "must_run_before_every_new_order": True,
                "must_run_after_every_fill_or_cancel_event": True,
                "runtime_cancel_action_not_authorized_by_this_packet": True,
            },
            "telemetry_contract": telemetry_contract,
        },
        "implementation_review_checklist": [
            "No private key or API credential load unless exact approval file is present and hash-bound.",
            "No network connection in dry-run/preflight mode.",
            "Every candidate decision writes telemetry before order submission.",
            "Client order id must encode policy_id, condition_id, side, price, size, and monotonic nonce.",
            "Post-only/maker-only support must be proven by client API contract and own fill telemetry.",
            "Any taker/ambiguous fill is excluded from positive evidence and triggers kill-switch.",
            "Any nonzero maker fee in counted samples triggers kill-switch.",
            "Unfilled open orders must be canceled before terminal window or on stale/edge-loss.",
            "Readiness/OOS/private-truth flags must remain false until validator sample targets pass.",
        ],
        "source_bindings": {
            "synthesis_packet": binding(SYNTHESIS_PACKET),
            "approval_packet": binding(APPROVAL_PACKET),
            "runner_shell": binding(RUNNER_SHELL),
            "kill_switch_evaluator": binding(KILL_SWITCH_EVALUATOR),
            "telemetry_validator": binding(TELEMETRY_VALIDATOR),
            "builder": binding(BUILDER),
        },
        "non_claims": NON_CLAIMS,
    }

    packet_path = OUT / "NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_PACKET.json"
    report_path = OUT / "NAGI_POST_ONLY_MAKER_ORDER_CLIENT_DESIGN_REPORT.md"
    spec_path = OUT / "ORDER_CLIENT_INTERFACE_SPEC.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Post-Only Maker Order Client Design",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet is review-only. It does not authorize private keys, network, WS, orders, cancels, OOS, or readiness claims.",
                "",
                "## Primary Candidate",
                "",
                f"- Lane: `{primary_lane['lane_id']}`",
                f"- Variant: `{primary_lane['public_proxy_evidence']['variant_id']}`",
                f"- Queue markets: {primary_lane['public_proxy_evidence']['queue_markets']}",
                f"- Fee0 edge qty: {primary_lane['public_proxy_evidence']['fee0_edge_qty_sum']}",
                f"- Taker07 edge qty: {primary_lane['public_proxy_evidence']['taker_fee07_edge_qty_sum']}",
                "",
                "## Execution Boundary",
                "",
                "- Required implementation: dry-run-first post-only maker adapter and runtime kill-switch binding.",
                "- Required evidence before OOS discussion: own maker fee0 telemetry, 100 filled markets, 500 filled actions, zero counted taker fills.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    spec_path.write_text(
        "\n".join(
            [
                "# Order Client Interface Spec",
                "",
                "Required methods if implemented later:",
                "",
                "- `preflight(context) -> audit`: validates exact approval, bindings, stale gates, pair-cost gate, kill-switch state, and telemetry sink.",
                "- `build_post_only_bid(decision) -> order_intent`: creates a non-network order intent only after all reject gates pass.",
                "- `submit_post_only_bid(order_intent) -> order_ack`: disabled unless separately exact-approved; must prove post-only maker-only semantics.",
                "- `cancel_open_order(order_id, reason) -> cancel_ack`: disabled unless separately exact-approved; required by runtime kill-switch.",
                "- `write_telemetry(event)`: writes decision/order/fill/cancel/inventory events with validator-compatible columns.",
                "",
                "Hard rejects:",
                "",
                *(f"- `{gate}`" for gate in reject_gates),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, spec_path, preview_path])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a review-only NAGI private maker shadow approval draft packet.

This packet is intentionally not executable. It binds the local NAGI maker
queue evidence and turns the next private-shadow discussion into concrete
implementation, policy, telemetry, and safety gates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_maker_shadow_approval_packet_20260608"

REQUIREMENTS_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_requirements_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_REQUIREMENTS_PACKET.json"
)
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json"
)
MAKER_PROXY_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_PACKET.json"
)
PIVOT_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_fastpair_pivot_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json"
)
CE25_TAKER_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json"
)
CE25_MAKER_PACKET = (
    EXPORTS
    / "ce25_btc5m_maker_queue_public_shadow_staging_packet_20260608"
    / "CE25_BTC5M_MAKER_QUEUE_PUBLIC_SHADOW_STAGING_PACKET.json"
)

USER_TRUTH = ROOT / "src/completion_first_data/user_truth.py"
CLI = ROOT / "src/completion_first_data/cli.py"
WEBSOCKET_SIDECAR = ROOT / "src/completion_first_data/capture/websocket_sidecar.py"
RUNBOOK = ROOT / "docs/RUNBOOK.md"
PLAN = ROOT / "docs/PLAN_Codex_New.md"
BUILDER = ROOT / "scripts/build_nagi_private_maker_shadow_approval_packet.py"
TELEMETRY_VALIDATOR = ROOT / "scripts/validate_nagi_private_maker_shadow_telemetry.py"
KILL_SWITCH_EVALUATOR = ROOT / "scripts/evaluate_nagi_private_maker_shadow_kill_switch.py"
FUTURE_RUNNER = ROOT / "scripts/run_nagi_private_maker_shadow.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET_PREPARED_REVIEW_ONLY_"
    "IMPLEMENTATION_AND_EXACT_APPROVAL_REQUIRED_NOT_EXECUTION_READY"
)
HIGHEST_FUTURE_SUCCESS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_REVIEW_REQUIRED_NOT_OOS_READY"
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


def write_deny_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI private maker shadow approval packet is not execution-ready.' >&2\n"
        "echo 'Missing reviewed post-only maker runner, concrete key hash binding, exact host binding, and explicit approval.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def load_evidence() -> dict[str, Any]:
    requirements = read_json(REQUIREMENTS_PACKET)
    matrix = read_json(RESIDUAL_MATRIX_PACKET)
    proxy = read_json(MAKER_PROXY_PACKET)
    pivot = read_json(PIVOT_PACKET)
    best = matrix["summary"]["best_fee0_variant"]
    return {
        "requirements_status": requirements["status"],
        "matrix_status": matrix["status"],
        "proxy_status": proxy["status"],
        "pivot_status": pivot["status"],
        "primary_variant": {
            "variant_id": best["variant_id"],
            "side": best["side"],
            "px_lo": best["px_lo"],
            "px_hi": best["px_hi"],
            "pair_cap": best["pair_cap"],
            "queue_min_qty": best["queue_min_qty"],
            "queue_markets": best["queue_markets"],
            "queue_market_share": best["queue_market_share"],
            "queue_edge_qty_sum_fee0": best["queue_edge_qty_sum_fee0"],
            "queue_edge_qty_sum_taker_fee07": best["queue_edge_qty_sum_taker_fee07"],
            "pair_cost_p50": best["queue_pair_cost_p50"],
            "queue_qty_p50": best["queue_qty_p50"],
            "queue_qty_p90": best["queue_qty_p90"],
            "touch_after_quote_ms_p99": best["touch_after_quote_ms_p99"],
            "align_lag_ms_p99": best["align_lag_ms_p99"],
        },
        "requirements_minimum_review_targets": requirements["shadow_requirements"][
            "minimum_review_targets_before_any_oos_discussion"
        ],
        "official_fee_boundary": "crypto taker fee rate 0.07, maker fee rate 0, fee = C * feeRate * p * (1-p)",
    }


def blockers() -> list[str]:
    out = [
        "EXACT_USER_APPROVAL_NOT_ISSUED",
        "CONCRETE_PRIVATE_KEY_SHA256_NOT_BOUND",
        "CONCRETE_API_CREDS_FINGERPRINT_NOT_BOUND",
        "CONCRETE_FUNDER_ADDRESS_NOT_BOUND",
        "CONCRETE_REMOTE_HOST_NOT_BOUND",
        "POST_ONLY_MAKER_ORDER_RUNNER_NOT_REVIEWED",
        "KILL_SWITCH_RUNTIME_PROCESS_NOT_BOUND",
        "FUNDING_AND_MAX_LOSS_LIMITS_NOT_BOUND_TO_RUNTIME",
        "PRIVATE_SHADOW_TELEMETRY_SAMPLE_NOT_PRESENT",
    ]
    if not TELEMETRY_VALIDATOR.exists():
        out.append("TELEMETRY_REPLAY_VALIDATOR_NOT_IMPLEMENTED_FOR_NAGI_PRIVATE_SHADOW")
    if FUTURE_RUNNER.exists():
        out.remove("POST_ONLY_MAKER_ORDER_RUNNER_NOT_REVIEWED")
        out.append("POST_ONLY_MAKER_ORDER_RUNNER_EXISTS_BUT_HASH_REVIEW_REQUIRED")
    return out


def approval_draft_text(packet_root: Path, packet: dict[str, Any]) -> str:
    e = packet["bound_evidence"]["primary_variant"]
    blocker_text = ", ".join(packet["execution_readiness"]["blockers"])
    return "\n".join(
        [
            "DRAFT_NOT_ISSUED",
            "I authorize preparation only for exactly one NAGI_PRIVATE_MAKER_SHADOW_V1 review run packet; I do not authorize execution from this draft.",
            f"The packet root is `{packet_root}`.",
            f"This draft is not issuable while blockers remain: {blocker_text}.",
            "A future exact execution approval, if ever requested, must bind concrete host, concrete public key/API credential fingerprints, concrete installed runner/checker/kill-switch hashes, concrete max spend/loss limits, and exact post-only maker-only commands.",
            "The research scope must be BTC 5m current/live markets only, final 60 seconds only, no future-round market dependency, no shared-ingress/shared-WS, and no REST book evidence for counted queue samples.",
            f"Primary policy candidate: variant `{e['variant_id']}`, side `{e['side']}`, bid price in [{e['px_lo']}, {e['px_hi']}), pair_cost <= {e['pair_cap']}, queue_min_qty > {e['queue_min_qty']}.",
            "Any counted order must be post-only maker-only; any taker fill, ambiguous maker/taker fill, missing fee proof, missing ack, missing cancel ack, stale book, or missing telemetry must fail closed.",
            "This does not authorize private key loading, API credential use, order/cancel/redeem, funding, canary, live, deploy, OOS, latest pointer update, private_truth_ready, strategy_promotion_ready, live_ready, or deployable.",
            f"Highest future success is capped at `{HIGHEST_FUTURE_SUCCESS}`.",
            "",
        ]
    )


def telemetry_schema() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "tables": {
            "nagi_private_shadow_decisions": [
                "decision_id",
                "condition_id",
                "slug",
                "market_end_ts_ms",
                "decision_ts_ms",
                "remaining_ms",
                "variant_id",
                "side",
                "asset_id",
                "bid_px",
                "bid_size_visible",
                "opp_bid_px",
                "opp_bid_size_visible",
                "pair_cost_at_decision",
                "l1_age_ms",
                "l2_age_ms",
                "align_lag_ms",
                "decision_reason",
            ],
            "nagi_private_shadow_order_lifecycle": [
                "decision_id",
                "client_order_id",
                "order_id",
                "submit_ts_ms",
                "ack_ts_ms",
                "cancel_submit_ts_ms",
                "cancel_ack_ts_ms",
                "post_only_flag",
                "price",
                "size",
                "status",
                "reject_reason",
                "remaining_open_qty",
            ],
            "nagi_private_shadow_fills": [
                "decision_id",
                "client_order_id",
                "order_id",
                "trade_id",
                "fill_ts_ms",
                "side",
                "maker_or_taker",
                "fill_px",
                "fill_qty",
                "fee_paid",
                "fee_rate_bps",
                "tx_hash",
                "raw_json_sha256",
            ],
            "nagi_private_shadow_inventory": [
                "condition_id",
                "asset_id",
                "outcome",
                "source_kind",
                "size",
                "avg_price",
                "recv_ms",
                "drift_flag",
            ],
        },
        "counted_sample_requirements": [
            "maker_or_taker == MAKER",
            "fee_paid == 0 or fee_rate_bps == 0",
            "post_only_flag == true",
            "ack_ts_ms is present",
            "cancel_ack_ts_ms is present unless fully filled before cancel",
            "l1_age_ms <= 500 and l2_age_ms <= 500 and align_lag_ms <= 500",
            "pair_cost_at_decision <= 0.995 for primary variant",
        ],
    }


def build_packet() -> dict[str, Any]:
    evidence = load_evidence()
    current_blockers = blockers()
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "scope": "review-only approval draft for a future NAGI private maker shadow; not executable",
        "source_bindings": {
            "requirements_packet": binding(REQUIREMENTS_PACKET),
            "residual_matrix_packet": binding(RESIDUAL_MATRIX_PACKET),
            "maker_queue_proxy_packet": binding(MAKER_PROXY_PACKET),
            "pivot_packet": binding(PIVOT_PACKET),
            "ce25_taker_context_packet": binding(CE25_TAKER_PACKET),
            "ce25_maker_context_packet": binding(CE25_MAKER_PACKET),
            "user_truth_module": binding(USER_TRUTH),
            "cli_module": binding(CLI),
            "websocket_sidecar_module": binding(WEBSOCKET_SIDECAR),
            "runbook": binding(RUNBOOK),
            "truth_upgrade_plan": binding(PLAN),
            "telemetry_validator": binding(TELEMETRY_VALIDATOR),
            "kill_switch_evaluator": binding(KILL_SWITCH_EVALUATOR),
            "future_runner_expected_path": binding(FUTURE_RUNNER),
            "builder": binding(BUILDER),
        },
        "bound_evidence": evidence,
        "execution_readiness": {
            "exact_approval_issued": False,
            "execution_authorized": False,
            "execution_ready": False,
            "private_key_loading_authorized": False,
            "api_creds_use_authorized": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "blockers": current_blockers,
        },
        "proposed_private_shadow_policy": {
            "policy_id": "NAGI_PRIVATE_MAKER_SHADOW_V1",
            "asset": "BTC",
            "timeframe": "5m up/down",
            "market_scope": "current/live BTC 5m market only; no future-round dependency",
            "decision_window": "last60 seconds before close",
            "primary_variant_id": evidence["primary_variant"]["variant_id"],
            "primary_side": evidence["primary_variant"]["side"],
            "bid_price_band": [evidence["primary_variant"]["px_lo"], evidence["primary_variant"]["px_hi"]],
            "pair_cost_cap": evidence["primary_variant"]["pair_cap"],
            "order_type": "post-only maker-only limit bid",
            "maker_fee_required": 0,
            "taker_fill_policy": "fail closed for counted evidence; cancel and exclude any taker/ambiguous fill",
            "cancel_policy": "cancel all open unfilled maker-shadow orders before market terminal window or on stale/edge-loss/kill-switch",
            "default_sample_size_policy": "microlot only; concrete size must be separately bound before any execution approval",
        },
        "proposed_command_contract": {
            "runner_expected_path": str(FUTURE_RUNNER),
            "runner_exists": FUTURE_RUNNER.exists(),
            "command_templates_not_authorized": [
                "uv run python scripts/run_nagi_private_maker_shadow.py --mode preflight --config <BOUND_CONFIG>",
                "uv run python scripts/run_nagi_private_maker_shadow.py --mode private-shadow --config <BOUND_CONFIG> --require-post-only --deny-taker --fail-closed",
                "uv run python scripts/run_nagi_private_maker_shadow.py --mode emergency-cancel --config <BOUND_CONFIG> --all-open-orders",
            ],
            "pre_execution_mandatory_checks": [
                "verify this packet SHA256SUMS.txt",
                "verify future runner/checker/kill-switch hashes",
                "verify exact host and workspace path",
                "verify private key sha256/fingerprint without printing secret",
                "verify API credential fingerprint without printing secret",
                "verify max spend and max loss limits are bound",
                "verify no shared-ingress/shared-WS path",
                "verify exactly one strategy process and no stale open orders before start",
            ],
        },
        "kill_switch_contract": {
            "must_exist_before_execution": True,
            "triggers": [
                "any taker fill observed",
                "any maker fee nonzero for counted sample",
                "book or depth staleness above 500 ms for counted decisions",
                "missing order ack or cancel ack",
                "unreconciled inventory drift",
                "public touch to own fill conversion materially below reviewed threshold",
                "realized pair_cost p50 above 0.995 after minimum sample",
                "residual_cost_rate above 0.20 after minimum sample",
                "single market loss above separately bound cap",
                "network reconnect or user WS truth gap during active orders",
            ],
            "actions": [
                "stop new orders",
                "cancel all open maker-shadow orders",
                "write fail-closed audit",
                "keep readiness/private-truth/OOS flags false",
            ],
        },
        "telemetry_schema": telemetry_schema(),
        "review_targets_before_any_oos_discussion": evidence["requirements_minimum_review_targets"],
        "decision": {
            "approval_packet_prepared": True,
            "execution_ready": False,
            "primary_blocker": "reviewed post-only maker order runner and concrete key/host/limit bindings are missing",
            "next_step": "implement/review runner and kill-switch, then prepare a new exact approval packet; do not execute this draft",
        },
        "highest_allowed_status": HIGHEST_FUTURE_SUCCESS,
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "ws_authorized": False,
            "private_key_authorized": False,
            "api_creds_authorized": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "maker_fill_proven": False,
            "queue_priority_proven": False,
        },
    }


def write_report(path: Path, packet: dict[str, Any]) -> None:
    e = packet["bound_evidence"]["primary_variant"]
    lines = [
        "# NAGI Private Maker Shadow Approval Draft",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Decision",
        "",
        "This is not executable. It is a review-only approval draft for the next engineering step.",
        "",
        "## Bound Primary Candidate",
        "",
        f"- Variant: `{e['variant_id']}`",
        f"- Side: `{e['side']}`",
        f"- Bid band: [{e['px_lo']}, {e['px_hi']})",
        f"- Pair cap: {e['pair_cap']}",
        f"- Queue markets: {e['queue_markets']}",
        f"- Fee0 queue edge qty sum: {e['queue_edge_qty_sum_fee0']}",
        f"- Taker-fee07 queue edge qty sum: {e['queue_edge_qty_sum_taker_fee07']}",
        "",
        "## Blockers",
        "",
        *[f"- {item}" for item in packet["execution_readiness"]["blockers"]],
        "",
        "## Boundary",
        "",
        "No private key, API credential, order, cancel, WS, OOS, canary, live, deploy, or readiness claim is authorized by this packet.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    missing = [
        str(path)
        for path in [REQUIREMENTS_PACKET, RESIDUAL_MATRIX_PACKET, MAKER_PROXY_PACKET, PIVOT_PACKET, BUILDER]
        if not path.exists()
    ]
    if missing:
        raise SystemExit("missing required inputs: " + ", ".join(missing))

    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_REPORT.md"
    approval_path = OUT / "EXACT_APPROVAL_CANDIDATE_DRAFT_NOT_ISSUED.txt"
    deny_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    schema_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_SCHEMA.json"
    write_json(packet_path, packet)
    write_report(report_path, packet)
    approval_path.write_text(approval_draft_text(OUT, packet), encoding="utf-8")
    write_deny_preview(deny_path)
    write_json(schema_path, packet["telemetry_schema"])
    write_sha256sums(OUT, [packet_path, report_path, approval_path, deny_path, schema_path])
    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": packet["status"],
                "execution_ready": packet["execution_readiness"]["execution_ready"],
                "blockers": packet["execution_readiness"]["blockers"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

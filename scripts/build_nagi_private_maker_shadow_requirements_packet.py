#!/usr/bin/env python3
"""Build a review-only NAGI private maker shadow requirements packet."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_maker_shadow_requirements_packet_20260608"

PIVOT_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_fastpair_pivot_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_FASTPAIR_PIVOT_PACKET.json"
)
MAKER_PROXY_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_proxy_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_PROXY_PACKET.json"
)
RESIDUAL_MATRIX_PACKET = (
    EXPORTS
    / "nagi_last60_midprice_maker_queue_residual_matrix_packet_20260608"
    / "NAGI_LAST60_MIDPRICE_MAKER_QUEUE_RESIDUAL_MATRIX_PACKET.json"
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
BUILDER = ROOT / "scripts/build_nagi_private_maker_shadow_requirements_packet.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_MAKER_SHADOW_REQUIREMENTS_PACKET_PREPARED_"
    "REVIEW_ONLY_PRIVATE_QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
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
        "echo 'NOT_AUTHORIZED: this requirements packet does not authorize private keys, orders, cancels, WS, OOS, canary, or live.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def extract_summary() -> dict[str, Any]:
    pivot = read_json(PIVOT_PACKET)
    proxy = read_json(MAKER_PROXY_PACKET)
    matrix = read_json(RESIDUAL_MATRIX_PACKET)
    best_fee0 = matrix["summary"]["best_fee0_variant"]
    best_taker = matrix["summary"]["best_taker_fee07_variant"]
    return {
        "nagi_public_profile_bucket": {
            "label": "nagi_last60_first35_50_fastpair",
            "markets": 195,
            "buy_actual": 157044.494936,
            "cash_pnl": 2969.114622,
            "roi": 0.018906,
            "pair_cost": 0.971983,
            "resid_rate": 0.072695,
            "bad_pair_cost_ge_1_share": 0.436187,
        },
        "base_book_shadow": pivot.get("summary", {}),
        "maker_proxy": proxy.get("summary", {}),
        "residual_matrix": {
            "variant_count": matrix["summary"]["variant_count"],
            "fee0_proxy_pass_count": matrix["summary"]["fee0_proxy_pass_count"],
            "taker_fee07_pass_count": matrix["summary"]["taker_fee07_pass_count"],
            "best_fee0_variant_id": best_fee0["variant_id"],
            "best_fee0_queue_markets": best_fee0["queue_markets"],
            "best_fee0_queue_market_share": best_fee0["queue_market_share"],
            "best_fee0_queue_edge_qty_sum_fee0": best_fee0["queue_edge_qty_sum_fee0"],
            "best_fee0_queue_edge_qty_sum_taker_fee07": best_fee0["queue_edge_qty_sum_taker_fee07"],
            "best_fee0_pair_cost_p50": best_fee0["queue_pair_cost_p50"],
            "best_fee0_queue_qty_p50": best_fee0["queue_qty_p50"],
            "best_fee0_queue_qty_p90": best_fee0["queue_qty_p90"],
            "best_fee0_touch_after_quote_ms_p99": best_fee0["touch_after_quote_ms_p99"],
            "best_taker_fee07_variant_id": best_taker["variant_id"],
            "best_taker_fee07_queue_markets": best_taker["queue_markets"],
            "best_taker_fee07_edge_qty_sum": best_taker["queue_edge_qty_sum_taker_fee07"],
        },
    }


def build_packet() -> dict[str, Any]:
    summary = extract_summary()
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "source_bindings": {
            "nagi_pivot_packet": binding(PIVOT_PACKET),
            "nagi_maker_queue_proxy_packet": binding(MAKER_PROXY_PACKET),
            "nagi_residual_matrix_packet": binding(RESIDUAL_MATRIX_PACKET),
            "ce25_taker_supply_context_packet": binding(CE25_TAKER_PACKET),
            "ce25_maker_queue_context_packet": binding(CE25_MAKER_PACKET),
            "builder": binding(BUILDER),
        },
        "evidence_summary": summary,
        "interpretation": {
            "ce25_taker_replication": "blocked by executable taker pair-edge supply",
            "nagi_full_account_copy": "rejected by high bad pair-cost share",
            "nagi_narrow_template": "strong only as a last60 midprice maker/no-fee queue template",
            "current_blocker": "public touch/visible depth does not prove our own maker queue fill, queue priority, cancel behavior, or realized maker-only fee0 execution",
        },
        "shadow_requirements": {
            "scope": {
                "asset": "BTC",
                "timeframe": "5m up/down",
                "decision_window": "last60 seconds before market close",
                "primary_review_variant": summary["residual_matrix"]["best_fee0_variant_id"],
                "price_band": "YES/UP bid 0.35-0.50 with pair_cost <= 0.995 for primary review",
                "public_proxy_queue_model": "post-quote SELL touch at/through our bid minus visible bid depth",
            },
            "required_public_fields": [
                "condition_id",
                "slug",
                "market_end_ts",
                "side",
                "remaining_ms",
                "l1_recv_ms",
                "l2_recv_ms",
                "bid_px",
                "bid_size_visible",
                "opp_bid_px",
                "opp_bid_size_visible",
                "ask_px",
                "ask_size_visible",
                "public_trade_recv_ms",
                "public_trade_price",
                "public_trade_size",
                "public_trade_taker_side",
            ],
            "required_private_fields_if_separately_authorized": [
                "order_id",
                "client_order_id",
                "submit_ts_ms",
                "ack_ts_ms",
                "side",
                "price",
                "size",
                "post_only_flag",
                "maker_or_taker_fill_flag",
                "partial_fill_ts_ms",
                "partial_fill_qty",
                "partial_fill_px",
                "fee_paid",
                "cancel_submit_ts_ms",
                "cancel_ack_ts_ms",
                "remaining_open_qty",
                "reject_reason",
                "self_trade_prevention_reason",
            ],
            "hard_fail_closed_gates": [
                "private_truth_ready must remain false until own authenticated order/fill/cancel telemetry is present",
                "any taker fill in maker-only shadow evidence must be excluded or fail the maker-only claim",
                "maker fee must be observed as 0 for counted maker fills",
                "book age > 500 ms fails the counted sample",
                "L1/L2 align lag > 500 ms fails the counted sample",
                "missing order ack, missing cancel ack, or ambiguous fill side fails the private-truth sample",
                "public touch without own fill cannot be counted as our queue fill",
                "shared-ingress/shared-WS evidence is forbidden for this lane",
                "readiness, OOS, promotion, deploy, live, and private-truth flags must remain false in this packet family",
            ],
            "review_metrics": [
                "own_maker_fill_rate_by_variant",
                "public_touch_to_own_fill_conversion_rate",
                "visible_depth_ahead_estimate",
                "fill_latency_ms_distribution",
                "cancel_latency_ms_distribution",
                "maker_only_fee0_pnl",
                "pair_cost_realized",
                "residual_qty_rate",
                "residual_cost_rate",
                "touch_no_fill_false_positive_rate",
                "adverse_selection_after_fill_pnl",
                "market_coverage",
                "action_rate_per_market",
            ],
            "minimum_review_targets_before_any_oos_discussion": {
                "own_private_maker_filled_markets": ">= 100",
                "own_private_maker_filled_actions": ">= 500",
                "maker_only_fee0_confirmed": True,
                "taker_fill_share_for_counted_samples": 0,
                "positive_realized_maker_edge_after_fees": True,
                "residual_cost_rate_review_target": "<= 0.20",
                "pair_cost_p50_review_target": "<= 0.995",
                "public_touch_to_own_fill_conversion_reported": True,
            },
        },
        "review_workflow": [
            {
                "stage": "requirements_review",
                "authorized_now": True,
                "description": "This packet only fixes evidence requirements and fail-closed gates.",
            },
            {
                "stage": "private_maker_shadow_approval_packet",
                "authorized_now": False,
                "description": "A separate packet would bind exact keys policy, post-only maker-only commands, limits, kill switch, and telemetry outputs.",
            },
            {
                "stage": "private_maker_shadow_execution",
                "authorized_now": False,
                "description": "Not authorized by this packet; would require explicit user approval.",
            },
            {
                "stage": "oos_or_canary",
                "authorized_now": False,
                "description": "Blocked until own private maker truth passes review; no readiness claim is allowed here.",
            },
        ],
        "decision": {
            "nagi_research_lane": "maker_queue_shadow_requirements",
            "taker_lane": "blocked",
            "fee0_maker_proxy_lane": "worth_review",
            "private_queue_truth_required": True,
            "prepare_order_execution": False,
            "next_step": "review NAGI maker shadow requirements; if accepted, prepare a separate private-maker-shadow approval packet, not execution",
        },
        "highest_allowed_status": "local research/review-only, not OOS-ready",
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
            "orders_authorized": False,
            "maker_fill_proven": False,
            "queue_priority_proven": False,
        },
    }


def write_report(path: Path, packet: dict[str, Any]) -> None:
    s = packet["evidence_summary"]["residual_matrix"]
    lines = [
        "# NAGI Private Maker Shadow Requirements",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Why This Exists",
        "",
        "CE25 taker/high-participation replication is blocked at executable taker prices. NAGI's narrow last60 template is still useful, but only as a maker/no-fee queue hypothesis.",
        "",
        "## Bound Evidence",
        "",
        f"- Residual matrix variants: {s['variant_count']}",
        f"- Fee0 maker-proxy pass count: {s['fee0_proxy_pass_count']}",
        f"- Taker-fee07 pass count: {s['taker_fee07_pass_count']} (low-coverage diagnostic only)",
        f"- Primary fee0 review variant: `{s['best_fee0_variant_id']}`",
        f"- Primary queue markets: {s['best_fee0_queue_markets']}",
        f"- Primary queue market share: {s['best_fee0_queue_market_share']}",
        f"- Primary fee0 queue edge qty sum: {s['best_fee0_queue_edge_qty_sum_fee0']}",
        f"- Primary taker-fee07 queue edge qty sum: {s['best_fee0_queue_edge_qty_sum_taker_fee07']}",
        "",
        "## Gate",
        "",
        "This packet does not authorize private keys, orders, cancels, WS, OOS, canary, live, deploy, or readiness claims. It only fixes the evidence requirements for a future separately approved private maker shadow.",
        "",
        "## Next Review Step",
        "",
        "If accepted, prepare a separate private-maker-shadow approval packet that binds exact post-only maker-only commands, key policy, limits, kill switch, telemetry outputs, and rollback behavior. Do not execute it from this packet.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    required = [PIVOT_PACKET, MAKER_PROXY_PACKET, RESIDUAL_MATRIX_PACKET, BUILDER]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit("missing required inputs: " + ", ".join(missing))

    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_REQUIREMENTS_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_MAKER_SHADOW_REQUIREMENTS_REPORT.md"
    deny_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_json(packet_path, packet)
    write_report(report_path, packet)
    write_deny_preview(deny_path)
    write_sha256sums(OUT, [packet_path, report_path, deny_path])
    print(json.dumps({"packet": str(packet_path), "status": packet["status"], "decision": packet["decision"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

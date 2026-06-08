#!/usr/bin/env python3
"""Build a review-only maker queue shadow design packet for CE25 BTC5M."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "ce25_btc5m_maker_queue_shadow_design_packet_20260608"

CANDIDATE_BASE = (
    BT_ROOT
    / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102/candidate_base.duckdb"
)
CANDIDATE_MANIFEST = CANDIDATE_BASE.parent / "CANDIDATE_BASE_MANIFEST.json"
MAKER_SUPPLY_PACKET = (
    EXPORTS
    / "ce25_btc5m_maker_bid_edge_supply_packet_20260607"
    / "CE25_BTC5M_MAKER_BID_EDGE_SUPPLY_PACKET.json"
)
TAKER_SUPPLY_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_taker_pair_edge_supply_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_TAKER_PAIR_EDGE_SUPPLY_PACKET.json"
)
EXECUTABLE_ADAPTER_GRID_PACKET = (
    EXPORTS
    / "ce25_btc5m_executable_price_adapter_grid_packet_20260607"
    / "CE25_BTC5M_EXECUTABLE_PRICE_ADAPTER_GRID_PACKET.json"
)
BUILDER = ROOT / "scripts/build_ce25_btc5m_maker_queue_shadow_design_packet.py"
VALIDATOR = ROOT / "scripts/validate_ce25_btc5m_research_packet_chain.py"

STATUS = (
    "KEEP_CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_PACKET_PREPARED_REVIEW_ONLY_"
    "QUEUE_TRUTH_REQUIRED_NOT_OOS_READY"
)
HIGHEST_ALLOWED_STATUS = (
    "KEEP_CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_REVIEWED_NOT_OOS_READY_PRIVATE_TRUTH_REQUIRED"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def binding(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if path.exists():
        out.update({"sha256": sha256_file(path), "size": path.stat().st_size})
    return out


def bool_str(value: bool) -> str:
    return "true" if value else "false"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M maker queue shadow design is review-only; no WS, OOS, or orders are authorized.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_evidence_field_rows(fields: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for item in fields:
        rows.append(
            {
                "field": str(item["field"]),
                "required_for_public_shadow": bool_str(bool(item["required_for_public_shadow"])),
                "required_for_private_truth": bool_str(bool(item["required_for_private_truth"])),
                "purpose": str(item["purpose"]),
            }
        )
    return rows


def build_gate_rows(gates: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "gate_id": str(item["gate_id"]),
            "lane": str(item["lane"]),
            "fail_closed_condition": str(item["fail_closed_condition"]),
            "claim_allowed_if_passes": str(item["claim_allowed_if_passes"]),
        }
        for item in gates
    ]


def build_packet() -> dict[str, Any]:
    maker_supply = load_json(MAKER_SUPPLY_PACKET)
    taker_supply = load_json(TAKER_SUPPLY_PACKET)
    adapter_grid = load_json(EXECUTABLE_ADAPTER_GRID_PACKET)

    maker_ceiling = maker_supply.get("supply_ceiling") or {}
    taker_ceiling = taker_supply.get("supply_ceiling") or {}
    adapter_meta = adapter_grid.get("grid_metadata") or {}
    adapter_best = adapter_grid.get("best_variant") or {}

    required_evidence_fields = [
        {
            "field": "candidate_row_id",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Stable join key from candidate_base to quote, trade, and later own-order telemetry.",
        },
        {
            "field": "condition_id",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Market-level grouping, residual accounting, and pair completion.",
        },
        {
            "field": "side",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "YES/NO leg identity for bid placement and paired inventory.",
        },
        {
            "field": "quote_ts_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Time at which the hypothetical maker quote is observed or placed.",
        },
        {
            "field": "side_bid_px",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Hypothetical maker bid price used for edge, fee, inventory, and queue checks.",
        },
        {
            "field": "opposite_bid_px",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Opposite-side maker bid price used for pair-cost and completion accounting.",
        },
        {
            "field": "bid_visible_depth_at_or_ahead",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Conservative queue-ahead estimate from public book depth.",
        },
        {
            "field": "public_sell_touch_ts_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Weak public proxy that a SELL trade touched the bid level after quote observation.",
        },
        {
            "field": "public_sell_trade_price",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Checks whether public sell activity traded at or through the hypothetical bid.",
        },
        {
            "field": "public_sell_trade_size",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Conservative fill proxy after subtracting visible queue ahead.",
        },
        {
            "field": "touch_after_quote_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Latency and causality guard; pre-quote touches do not count.",
        },
        {
            "field": "l1_age_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Fail-closed stale quote guard.",
        },
        {
            "field": "l2_age_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Fail-closed stale depth guard.",
        },
        {
            "field": "align_lag_ms",
            "required_for_public_shadow": True,
            "required_for_private_truth": True,
            "purpose": "Fail-closed candidate-to-book alignment guard.",
        },
        {
            "field": "hypothetical_fill_qty_touch_only",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Weak upper-bound proxy; never proves own fill.",
        },
        {
            "field": "hypothetical_fill_qty_after_visible_depth",
            "required_for_public_shadow": True,
            "required_for_private_truth": False,
            "purpose": "Conservative public proxy using visible queue ahead.",
        },
        {
            "field": "own_order_id",
            "required_for_public_shadow": False,
            "required_for_private_truth": True,
            "purpose": "Required before private maker fill truth can be claimed.",
        },
        {
            "field": "own_order_ack_ts_ms",
            "required_for_public_shadow": False,
            "required_for_private_truth": True,
            "purpose": "Required to prove placement timing and latency.",
        },
        {
            "field": "own_order_fill_qty",
            "required_for_public_shadow": False,
            "required_for_private_truth": True,
            "purpose": "Required to prove our maker order filled.",
        },
        {
            "field": "own_order_cancel_ts_ms",
            "required_for_public_shadow": False,
            "required_for_private_truth": True,
            "purpose": "Required for cancellation and stale-exposure accounting.",
        },
    ]

    queue_models = [
        {
            "model_id": "TOUCH_ONLY_NOT_FILL_PROOF",
            "proof_level": "weak_public_upper_bound",
            "fill_rule": "Public SELL trade at or below side_bid after quote observation marks a touch, not a fill.",
            "private_truth_ready_if_positive": False,
        },
        {
            "model_id": "SIZE_AFTER_VISIBLE_DEPTH_CONSERVATIVE",
            "proof_level": "public_conservative_proxy",
            "fill_rule": "Hypothetical fill is max(0, public sell size at/through bid minus visible bid depth at or ahead).",
            "private_truth_ready_if_positive": False,
        },
        {
            "model_id": "TRADE_SIZE_MINUS_VISIBLE_BID_DEPTH",
            "proof_level": "public_conservative_proxy",
            "fill_rule": "Aggregate trade size within a bounded quote lifetime is reduced by public visible queue ahead.",
            "private_truth_ready_if_positive": False,
        },
        {
            "model_id": "OWN_ORDER_TELEMETRY_REQUIRED",
            "proof_level": "private_truth_only",
            "fill_rule": "Only authenticated own order ack/fill/cancel telemetry can prove our maker queue fill.",
            "private_truth_ready_if_positive": True,
        },
    ]

    fail_closed_gates = [
        {
            "gate_id": "NO_ORDER_AUTHORIZATION",
            "lane": "current_packet",
            "fail_closed_condition": "Any order, cancel, redeem, import, funding, live, canary, deploy, or private key action is attempted.",
            "claim_allowed_if_passes": "review-only design packet may be read.",
        },
        {
            "gate_id": "PUBLIC_TOUCH_NOT_PRIVATE_FILL",
            "lane": "public_shadow",
            "fail_closed_condition": "A public SELL touch proxy is converted into maker_fill_proven or private_truth_ready.",
            "claim_allowed_if_passes": "public no-order fill proxy only.",
        },
        {
            "gate_id": "QUOTE_STALENESS",
            "lane": "public_shadow",
            "fail_closed_condition": "l1_age_ms, l2_age_ms, or align_lag_ms exceed packet-specific thresholds.",
            "claim_allowed_if_passes": "fresh public quote/depth proxy sample.",
        },
        {
            "gate_id": "QUEUE_AHEAD_MISSING",
            "lane": "public_shadow",
            "fail_closed_condition": "bid_visible_depth_at_or_ahead is missing for a claimed conservative queue sample.",
            "claim_allowed_if_passes": "queue-adjusted public proxy sample.",
        },
        {
            "gate_id": "TRADE_CAUSALITY",
            "lane": "public_shadow",
            "fail_closed_condition": "public_sell_touch_ts_ms is missing or less than quote_ts_ms.",
            "claim_allowed_if_passes": "post-quote public touch proxy sample.",
        },
        {
            "gate_id": "PRIVATE_TELEMETRY_ABSENT",
            "lane": "private_order_lane",
            "fail_closed_condition": "own_order_id, ack, fill, and cancel telemetry are absent.",
            "claim_allowed_if_passes": "no private truth claim.",
        },
        {
            "gate_id": "READINESS_FLAGS",
            "lane": "all",
            "fail_closed_condition": "private_truth_ready, strategy_promotion_ready, live_ready, deployable, or oos_ready is true.",
            "claim_allowed_if_passes": "local research/review-only status.",
        },
    ]

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "highest_allowed_status": HIGHEST_ALLOWED_STATUS,
        "source_bindings": {
            "candidate_base": binding(CANDIDATE_BASE),
            "candidate_base_manifest": binding(CANDIDATE_MANIFEST),
            "maker_bid_edge_supply_packet": binding(MAKER_SUPPLY_PACKET),
            "executable_taker_pair_edge_supply_packet": binding(TAKER_SUPPLY_PACKET),
            "executable_price_adapter_grid_packet": binding(EXECUTABLE_ADAPTER_GRID_PACKET),
            "builder": binding(BUILDER),
            "validator": binding(VALIDATOR),
        },
        "current_research_state": {
            "source_window": "local Backtest V1 compact artifacts, 2026-05-02..2026-05-18 valid local days",
            "taker_backbone_state": {
                "status": taker_supply.get("status"),
                "positive_net_edge_rows": taker_ceiling.get("positive_net_edge_rows"),
                "positive_net_edge_markets": taker_ceiling.get("positive_net_edge_markets"),
                "positive_net_edge_market_share": taker_ceiling.get("positive_net_edge_market_share"),
                "conclusion": "Executable taker pair-edge supply is far below high-participation requirements.",
            },
            "executable_adapter_state": {
                "status": adapter_grid.get("status"),
                "variant_count": adapter_meta.get("variant_count"),
                "quality_positive_variant_count": adapter_meta.get("quality_positive_variant_count"),
                "best_variant_id": adapter_best.get("variant_id"),
                "best_variant_pair_share_rate": adapter_best.get("pair_share_rate"),
                "best_variant_residual_cost_rate": adapter_best.get("residual_cost_rate"),
            },
            "maker_bid_supply_state": {
                "status": maker_supply.get("status"),
                "positive_edge_rows": maker_ceiling.get("positive_edge_rows"),
                "positive_edge_markets": maker_ceiling.get("positive_edge_markets"),
                "positive_edge_market_share": maker_ceiling.get("positive_edge_market_share"),
                "positive_edge_touch_markets": maker_ceiling.get("positive_edge_touch_markets"),
                "positive_edge_touch_market_share": maker_ceiling.get("positive_edge_touch_market_share"),
                "conclusion": "Maker/bid edge supply is broad enough to justify queue-shadow research, not enough to claim fills.",
            },
        },
        "design_objective": {
            "objective": "Translate the maker/bid edge supply result into a bounded maker queue shadow evidence contract.",
            "public_shadow_can_prove_private_fill": False,
            "private_order_lane_authorized": False,
            "orders_authorized": False,
            "oos_authorized": False,
            "ws_authorized": False,
            "review_only_now": True,
        },
        "evidence_lanes": {
            "public_no_order_shadow": {
                "allowed_in_this_packet": False,
                "may_be_prepared_by_next_review_packet": True,
                "description": "No-order public evidence can measure quote freshness, public SELL touches, and conservative queue-adjusted hypothetical fills.",
                "cannot_claim": [
                    "own maker fill",
                    "private queue priority",
                    "private truth",
                    "promotion readiness",
                    "OOS readiness",
                ],
            },
            "private_order_telemetry": {
                "allowed_in_this_packet": False,
                "requires_separate_exact_approval": True,
                "description": "Only authenticated own order ack/fill/cancel telemetry can prove maker queue fillability.",
                "minimum_required_records": [
                    "own_order_id",
                    "own_order_ack_ts_ms",
                    "own_order_price",
                    "own_order_size",
                    "own_order_fill_qty",
                    "own_order_fill_ts_ms",
                    "own_order_cancel_ts_ms",
                    "own_order_remaining_qty",
                ],
            },
        },
        "required_evidence_fields": required_evidence_fields,
        "queue_models": queue_models,
        "fail_closed_gates": fail_closed_gates,
        "future_public_shadow_staging_contract": {
            "next_packet_name": "ce25_btc5m_maker_queue_public_shadow_staging_packet",
            "allowed_actions_in_next_packet": [
                "materialize local replay public no-order queue proxy rows",
                "compute touch-only and queue-adjusted hypothetical fill metrics",
                "report stale/depth/lag/cancel-bound failures",
                "preserve all readiness flags false",
            ],
            "not_allowed_without_separate_approval": [
                "direct WS",
                "OOS observer",
                "runner",
                "private key",
                "order",
                "cancel",
                "redeem",
                "canary",
                "live",
                "deploy",
                "latest pointer update",
            ],
            "minimum_output_metrics": [
                "candidate_rows",
                "quote_fresh_rows",
                "public_sell_touch_rows",
                "touch_market_share",
                "queue_adjusted_hypothetical_fill_rows",
                "queue_adjusted_hypothetical_fill_market_share",
                "hypothetical_pair_cost",
                "hypothetical_net_edge_after_fee",
                "residual_cost_rate_under_hypothetical_fill",
                "stale_reject_count",
                "depth_missing_reject_count",
                "pre_quote_touch_reject_count",
            ],
        },
        "decision": {
            "maker_queue_shadow_design_prepared": True,
            "maker_bid_edge_supply_remains_research_lane": True,
            "taker_high_participation_backbone_remains_blocked": True,
            "public_shadow_can_prove_private_fill": False,
            "private_order_lane_authorized": False,
            "orders_authorized": False,
            "oos_discussion_allowed": False,
            "primary_blocker": "queue_priority_and_private_fill_truth_missing",
            "next_step": "prepare_public_no_order_maker_queue_shadow_staging_packet_or_new_strategy_family_packet",
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "ws_authorized": False,
            "orders_authorized": False,
            "maker_fill_proven": False,
            "queue_priority_proven": False,
        },
    }
    return packet


def render_report(packet: dict[str, Any]) -> str:
    maker = packet["current_research_state"]["maker_bid_supply_state"]
    taker = packet["current_research_state"]["taker_backbone_state"]
    lines = [
        "# CE25 BTC5M Maker Queue Shadow Design",
        "",
        f"Status: `{packet['status']}`",
        "",
        "## Current State",
        "",
        "The taker high-participation path is blocked at executable prices. The maker/bid path still has broad edge supply, but it is only an upper-bound research lane until queue and own-fill truth exist.",
        "",
        f"- Taker positive fee-inclusive edge markets: {taker['positive_net_edge_markets']}",
        f"- Taker positive market share: {taker['positive_net_edge_market_share']}",
        f"- Maker positive edge markets: {maker['positive_edge_markets']}",
        f"- Maker positive edge market share: {maker['positive_edge_market_share']}",
        f"- Maker public SELL touch proxy market share: {maker['positive_edge_touch_market_share']}",
        "",
        "## Design Contract",
        "",
        "This packet does not authorize WS, OOS, runner, private keys, orders, cancels, redeems, canary, live, deploy, or latest pointer updates. It defines the evidence fields and fail-closed gates required before a maker queue shadow packet can be reviewed.",
        "",
        "Public SELL touches are not private maker fills. The next review-only packet may materialize public no-order queue proxies, but private truth still requires authenticated own order telemetry.",
        "",
        "## Next Step",
        "",
        "`prepare_public_no_order_maker_queue_shadow_staging_packet_or_new_strategy_family_packet`",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    packet = build_packet()
    packet_path = OUT / "CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_PACKET.json"
    report_path = OUT / "CE25_BTC5M_MAKER_QUEUE_SHADOW_DESIGN_REPORT.md"
    fields_path = OUT / "ce25_btc5m_maker_queue_required_evidence_fields.csv"
    gates_path = OUT / "ce25_btc5m_maker_queue_fail_closed_gates.csv"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_json(packet_path, packet)
    report_path.write_text(render_report(packet), encoding="utf-8")
    write_csv(fields_path, build_evidence_field_rows(packet["required_evidence_fields"]))
    write_csv(gates_path, build_gate_rows(packet["fail_closed_gates"]))
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, fields_path, gates_path, preview_path])
    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": packet["status"],
                "highest_allowed_status": packet["highest_allowed_status"],
                "next_step": packet["decision"]["next_step"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

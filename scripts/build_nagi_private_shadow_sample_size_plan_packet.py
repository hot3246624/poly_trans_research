#!/usr/bin/env python3
"""Build NAGI private maker-shadow sample-size plan from local review packets."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
OUT = EXPORTS / "nagi_private_shadow_sample_size_plan_packet_20260608"

FRONTIER_ROOT = EXPORTS / "nagi_maker_queue_exhaustive_frontier_packet_20260608"
FRONTIER_CSV = FRONTIER_ROOT / "nagi_maker_queue_exhaustive_frontier.csv"
FRONTIER_PACKET = FRONTIER_ROOT / "NAGI_MAKER_QUEUE_EXHAUSTIVE_FRONTIER_PACKET.json"
SENSITIVITY_ROOT = EXPORTS / "nagi_queue_model_sensitivity_packet_20260608"
SENSITIVITY_PACKET = SENSITIVITY_ROOT / "NAGI_QUEUE_MODEL_SENSITIVITY_PACKET.json"
SENSITIVITY_SUMMARY_CSV = SENSITIVITY_ROOT / "nagi_queue_model_sensitivity_summary.csv"
SYNTHESIS_PACKET = (
    EXPORTS / "nagi_strategy_synthesis_packet_20260608" / "NAGI_STRATEGY_SYNTHESIS_PACKET.json"
)
TELEMETRY_CONTRACT_PACKET = (
    EXPORTS
    / "nagi_private_maker_shadow_telemetry_contract_packet_20260608"
    / "NAGI_PRIVATE_MAKER_SHADOW_TELEMETRY_CONTRACT_PACKET.json"
)
APPROVAL_PACKET = (
    EXPORTS / "nagi_private_maker_shadow_approval_packet_20260608" / "NAGI_PRIVATE_MAKER_SHADOW_APPROVAL_PACKET.json"
)
BUILDER = ROOT / "scripts/build_nagi_private_shadow_sample_size_plan_packet.py"

STATUS = (
    "KEEP_NAGI_PRIVATE_SHADOW_SAMPLE_SIZE_PLAN_REVIEWED_"
    "PRIVATE_TELEMETRY_REQUIRED_NOT_EXECUTION_READY"
)

MIN_FILLED_MARKETS = 100
MIN_FILLED_ACTIONS = 500
RESIDUAL_COST_RATE_MAX = 0.20
PAIR_COST_P50_MAX = 0.995
COUNTED_TAKER_FILL_SHARE_MAX = 0.0
MAKER_FEE_REQUIRED = 0.0

SCALE_EDGE_MIN = 100.0
HIGH_COVERAGE_EDGE_MIN = 250.0

EFFECTIVE_CONVERSION_GRID = [1.0, 0.5, 0.25, 0.1, 0.05, 0.025, 0.01]
LANE_IDS = [
    "last60__yes__p35_50__pc0.995__qmin0",
    "last60__yes__p35_50__pc1.000__qmin0",
    "full300__yes__p35_50__pc1.000__qmin0",
]


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


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: NAGI private shadow sample-size plan is review-only.' >&2\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def write_sha256sums(root: Path, files: list[Path]) -> None:
    lines = [f"{sha256_file(path)}  {path.relative_to(root)}" for path in files]
    (root / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def as_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def read_frontier(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("variant_id") == "variant_id":
                continue
            out: dict[str, Any] = dict(row)
            for key in (
                "start_s",
                "end_s",
                "px_lo",
                "px_hi",
                "pair_cap",
                "queue_min_qty",
                "queue_qty_sum",
                "queue_edge_qty_sum_fee0",
                "queue_edge_qty_sum_taker_fee07",
                "queue_pair_cost_avg",
                "queue_pair_cost_p50",
                "queue_net_edge_fee0_avg",
                "queue_net_edge_fee0_p50",
                "queue_qty_p50",
                "queue_qty_p90",
                "touch_after_quote_ms_p99",
                "align_lag_ms_p99",
                "queue_row_share",
                "queue_market_share",
            ):
                out[key] = as_float(row.get(key))
            for key in (
                "eligible_touch_rows",
                "eligible_touch_markets",
                "queue_rows",
                "queue_markets",
                "queue_pos_fee0_rows",
                "queue_pos_fee0_markets",
                "queue_pos_taker_fee07_rows",
                "queue_pos_taker_fee07_markets",
            ):
                out[key] = as_int(row.get(key))
            for key in (
                "passes_fee0_scale_gate",
                "passes_fee0_high_coverage_gate",
                "survives_taker_fee07_scale_gate",
                "nagi_anchor_like",
                "private_truth_ready",
                "oos_ready",
            ):
                out[key] = as_bool(row.get(key))
            rows.append(out)
    return rows


def min_conversion(required: float, available: float) -> float | None:
    if available <= 0:
        return None
    return required / available


def ceil_cycles(required: float, available_per_cycle: float, conversion: float) -> int | None:
    if available_per_cycle <= 0 or conversion <= 0:
        return None
    return int(math.ceil(required / (available_per_cycle * conversion)))


def lane_plan(row: dict[str, Any]) -> dict[str, Any]:
    action_conv = min_conversion(MIN_FILLED_ACTIONS, row["queue_rows"])
    market_conv = min_conversion(MIN_FILLED_MARKETS, row["queue_markets"])
    scale_edge_conv = min_conversion(SCALE_EDGE_MIN, row["queue_edge_qty_sum_fee0"])
    high_edge_conv = min_conversion(HIGH_COVERAGE_EDGE_MIN, row["queue_edge_qty_sum_fee0"])
    conversions = [v for v in (action_conv, market_conv) if v is not None]
    private_gate_conversion = max(conversions) if conversions else None
    review_high_conversion = max([v for v in (action_conv, market_conv, high_edge_conv) if v is not None])

    scenario_rows: list[dict[str, Any]] = []
    for conversion in EFFECTIVE_CONVERSION_GRID:
        own_filled_actions_est = row["queue_rows"] * conversion
        own_filled_markets_est = row["queue_markets"] * conversion
        own_fee0_edge_qty_est = row["queue_edge_qty_sum_fee0"] * conversion
        scenario_rows.append(
            {
                "variant_id": row["variant_id"],
                "effective_conversion": conversion,
                "own_filled_actions_est": round(own_filled_actions_est, 6),
                "own_filled_markets_est": round(own_filled_markets_est, 6),
                "own_fee0_edge_qty_est": round(own_fee0_edge_qty_est, 6),
                "cycles_to_500_actions": ceil_cycles(MIN_FILLED_ACTIONS, row["queue_rows"], conversion),
                "cycles_to_100_markets": ceil_cycles(MIN_FILLED_MARKETS, row["queue_markets"], conversion),
                "cycles_to_250_edge_qty": ceil_cycles(HIGH_COVERAGE_EDGE_MIN, row["queue_edge_qty_sum_fee0"], conversion),
                "single_cycle_private_gate_by_count": (
                    own_filled_actions_est >= MIN_FILLED_ACTIONS
                    and own_filled_markets_est >= MIN_FILLED_MARKETS
                ),
                "single_cycle_review_high_gate": (
                    own_filled_actions_est >= MIN_FILLED_ACTIONS
                    and own_filled_markets_est >= MIN_FILLED_MARKETS
                    and own_fee0_edge_qty_est >= HIGH_COVERAGE_EDGE_MIN
                ),
            }
        )

    return {
        "variant_id": row["variant_id"],
        "time_id": row["time_id"],
        "side": row["side"],
        "band_id": row["band_id"],
        "pair_cap": row["pair_cap"],
        "queue_min_qty": row["queue_min_qty"],
        "public_proxy_queue_rows": row["queue_rows"],
        "public_proxy_queue_markets": row["queue_markets"],
        "public_proxy_fee0_edge_qty_sum": round(row["queue_edge_qty_sum_fee0"], 6),
        "public_proxy_taker_fee07_edge_qty_sum": round(row["queue_edge_qty_sum_taker_fee07"], 6),
        "queue_market_share": row["queue_market_share"],
        "queue_pair_cost_p50": row["queue_pair_cost_p50"],
        "min_effective_conversion_for_500_actions": round(action_conv, 6) if action_conv is not None else None,
        "min_effective_conversion_for_100_markets": round(market_conv, 6) if market_conv is not None else None,
        "min_effective_conversion_for_100_fee0_edge_qty": round(scale_edge_conv, 6)
        if scale_edge_conv is not None
        else None,
        "min_effective_conversion_for_250_fee0_edge_qty": round(high_edge_conv, 6)
        if high_edge_conv is not None
        else None,
        "min_effective_conversion_for_private_truth_count_gate": round(private_gate_conversion, 6)
        if private_gate_conversion is not None
        else None,
        "min_effective_conversion_for_review_high_gate": round(review_high_conversion, 6)
        if review_high_conversion is not None
        else None,
        "single_cycle_passes_count_gate_at_0p05": bool(private_gate_conversion is not None and 0.05 >= private_gate_conversion),
        "single_cycle_passes_review_high_gate_at_0p05": bool(review_high_conversion is not None and 0.05 >= review_high_conversion),
        "scenario_rows": scenario_rows,
    }


def flatten_lane_rows(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for plan in plans:
        row = {k: v for k, v in plan.items() if k != "scenario_rows"}
        out.append(row)
    return out


def flatten_scenarios(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for plan in plans:
        base = {
            "variant_id": plan["variant_id"],
            "time_id": plan["time_id"],
            "side": plan["side"],
            "band_id": plan["band_id"],
        }
        for row in plan["scenario_rows"]:
            merged = dict(base)
            merged.update(row)
            out.append(merged)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    frontier = read_frontier(FRONTIER_CSV)
    by_id = {row["variant_id"]: row for row in frontier}
    missing = [variant_id for variant_id in LANE_IDS if variant_id not in by_id]
    if missing:
        raise SystemExit(f"missing expected variants: {missing}")

    plans = [lane_plan(by_id[variant_id]) for variant_id in LANE_IDS]
    anchor = plans[0]
    broad = plans[2]

    hard_fail_conditions = [
        "any counted fill is TAKER or ambiguous maker/taker",
        "any counted fill has fee_paid != 0 or fee_rate_bps != 0",
        "orders, cancels, network, private key, or API authorization appears without exact user approval",
        "post_only acknowledgement missing for any counted order",
        "neither cancel acknowledgement nor full-fill proof is present for any counted order",
        "l1_age_ms, l2_age_ms, or align_lag_ms exceeds 500 on counted decisions",
        "inventory drift appears in reconciled telemetry",
        "pair_cost_p50 exceeds 0.995 after the gate sample is reached",
        "residual_cost_rate exceeds 0.20 after the gate sample is reached",
        "realized maker edge after fees is <= 0 after the gate sample is reached",
    ]

    milestones = [
        {
            "milestone": "M0_REVIEW_ONLY_PREFLIGHT",
            "required_own_filled_markets": 0,
            "required_own_filled_actions": 0,
            "purpose": "hash, packet, schema, kill-switch, and dry-run checks only",
            "pass_fail_decision_allowed": False,
        },
        {
            "milestone": "M1_TELEMETRY_SANITY",
            "required_own_filled_markets": 20,
            "required_own_filled_actions": 100,
            "purpose": "detect maker fee, taker contamination, stale evidence, ack/cancel, and inventory defects early",
            "pass_fail_decision_allowed": False,
        },
        {
            "milestone": "M2_DIRECTIONAL_PRIVATE_SIGNAL",
            "required_own_filled_markets": 50,
            "required_own_filled_actions": 250,
            "purpose": "estimate public_touch_to_own_fill_conversion and early realized maker edge",
            "pass_fail_decision_allowed": False,
        },
        {
            "milestone": "M3_PRIVATE_TRUTH_GATE",
            "required_own_filled_markets": MIN_FILLED_MARKETS,
            "required_own_filled_actions": MIN_FILLED_ACTIONS,
            "purpose": "minimum private telemetry gate for pass/fail discussion",
            "pass_fail_decision_allowed": True,
        },
        {
            "milestone": "M4_MARGIN_EXTENSION_IF_MARGINAL",
            "required_own_filled_markets": 200,
            "required_own_filled_actions": 1000,
            "purpose": "only if M3 is marginal, conversion is below 0.10, or edge/residual is unstable",
            "pass_fail_decision_allowed": True,
        },
    ]

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "method": {
            "scope": "review-only local sample-size planning; no execution, network, private key, or orders",
            "private_truth_gate": {
                "minimum_own_maker_filled_markets": MIN_FILLED_MARKETS,
                "minimum_own_maker_filled_actions": MIN_FILLED_ACTIONS,
                "maker_fee_must_equal": MAKER_FEE_REQUIRED,
                "counted_taker_fill_share_must_equal": COUNTED_TAKER_FILL_SHARE_MAX,
                "pair_cost_p50_max": PAIR_COST_P50_MAX,
                "residual_cost_rate_max": RESIDUAL_COST_RATE_MAX,
                "positive_realized_maker_edge_after_fees_required": True,
                "public_touch_to_own_fill_conversion_required": True,
            },
            "conversion_model": (
                "effective_conversion is a planning proxy for public queue-touch rows/markets that become own "
                "counted maker fills; actual pass/fail must use own authenticated telemetry."
            ),
            "source_limit": "public-proxy conversion estimates cannot prove maker fill or queue priority",
        },
        "summary": {
            "lane_count": len(plans),
            "primary_anchor_variant_id": anchor["variant_id"],
            "broad_reference_variant_id": broad["variant_id"],
            "anchor_min_effective_conversion_for_private_truth_count_gate": anchor[
                "min_effective_conversion_for_private_truth_count_gate"
            ],
            "anchor_min_effective_conversion_for_review_high_gate": anchor[
                "min_effective_conversion_for_review_high_gate"
            ],
            "broad_min_effective_conversion_for_private_truth_count_gate": broad[
                "min_effective_conversion_for_private_truth_count_gate"
            ],
            "broad_min_effective_conversion_for_review_high_gate": broad[
                "min_effective_conversion_for_review_high_gate"
            ],
            "no_high_coverage_lane_survives_effective_conversion_lte_0p05_from_queue_sensitivity": True,
            "sample_plan_decision": (
                "anchor lane needs roughly 0.346 effective conversion in one local-horizon equivalent to reach "
                "500 own filled actions; broad lane needs roughly 0.054. Treat <=0.05 as fail-closed for "
                "single-cycle high-coverage unless additional cycles accumulate private telemetry."
            ),
        },
        "lane_plans": [{k: v for k, v in plan.items() if k != "scenario_rows"} for plan in plans],
        "milestones": milestones,
        "hard_fail_conditions": hard_fail_conditions,
        "stopping_rules": {
            "immediate_fail_closed": hard_fail_conditions[:7],
            "gate_fail_at_m3_or_later": hard_fail_conditions[7:],
            "continue_to_m4": [
                "M3 passes hard gates but realized edge is small or unstable by market cluster",
                "observed public_touch_to_own_fill_conversion is below 0.10",
                "anchor lane requires multi-cycle accumulation to reach 500 fills",
            ],
            "do_not_start_execution_from_this_packet": True,
        },
        "source_bindings": {
            "frontier_csv": binding(FRONTIER_CSV),
            "frontier_packet": binding(FRONTIER_PACKET),
            "queue_sensitivity_packet": binding(SENSITIVITY_PACKET),
            "queue_sensitivity_summary_csv": binding(SENSITIVITY_SUMMARY_CSV),
            "synthesis_packet": binding(SYNTHESIS_PACKET),
            "telemetry_contract_packet": binding(TELEMETRY_CONTRACT_PACKET),
            "approval_packet": binding(APPROVAL_PACKET),
            "builder": binding(BUILDER),
        },
        "non_claims": {
            "private_truth_ready": False,
            "queue_priority_proven": False,
            "maker_fill_proven": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_ready": False,
            "orders_authorized": False,
            "cancels_authorized": False,
            "private_key_authorized": False,
            "api_creds_authorized": False,
            "ws_authorized": False,
        },
    }

    plan_csv = OUT / "nagi_private_shadow_sample_size_lane_plan.csv"
    scenario_csv = OUT / "nagi_private_shadow_sample_size_conversion_grid.csv"
    packet_path = OUT / "NAGI_PRIVATE_SHADOW_SAMPLE_SIZE_PLAN_PACKET.json"
    report_path = OUT / "NAGI_PRIVATE_SHADOW_SAMPLE_SIZE_PLAN_REPORT.md"
    preview_path = OUT / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"

    write_csv(plan_csv, flatten_lane_rows(plans))
    write_csv(scenario_csv, flatten_scenarios(plans))
    write_json(packet_path, packet)
    report_path.write_text(
        "\n".join(
            [
                "# NAGI Private Shadow Sample-Size Plan",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet is review-only. It does not authorize private keys, API credentials, network access, orders, cancels, WS, OOS, canary, live, or deployment.",
                "",
                "## Core Result",
                "",
                f"- Private-truth minimum: {MIN_FILLED_MARKETS} own maker-filled markets and {MIN_FILLED_ACTIONS} own maker-filled actions.",
                f"- Anchor lane: `{anchor['variant_id']}`.",
                f"- Anchor one-cycle count gate conversion: {anchor['min_effective_conversion_for_private_truth_count_gate']}.",
                f"- Anchor one-cycle review-high conversion: {anchor['min_effective_conversion_for_review_high_gate']}.",
                f"- Broad reference lane: `{broad['variant_id']}`.",
                f"- Broad one-cycle count gate conversion: {broad['min_effective_conversion_for_private_truth_count_gate']}.",
                f"- Broad one-cycle review-high conversion: {broad['min_effective_conversion_for_review_high_gate']}.",
                "- Queue sensitivity already shows no high-coverage lane survives effective_conversion <= 0.05.",
                "",
                "## Stopping Rules",
                "",
                "- Immediate fail-closed for taker/ambiguous fills, nonzero maker fee, missing post-only ack, missing cancel/full-fill proof, stale evidence, or inventory drift.",
                "- At M3 or later, fail closed if pair_cost_p50 > 0.995, residual_cost_rate > 0.20, or realized maker edge after fees <= 0.",
                "- Continue to M4 only if M3 is marginal or observed conversion is below 0.10.",
                "",
                "## Next",
                "",
                "Use this packet to decide whether an exact private maker-shadow approval packet is worth preparing. Do not execute from this packet.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    write_preview(preview_path)
    write_sha256sums(OUT, [packet_path, report_path, plan_csv, scenario_csv, preview_path])

    print(
        json.dumps(
            {
                "packet": str(packet_path),
                "status": STATUS,
                "summary": packet["summary"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a review-only CE25 high-price top1-qty candidate ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_L2_DIR = Path(
    "/Users/hot/web3Scientist/poly_backtest_data/derived/"
    "ce25_high_price_l2_top_aligned_validation_v0/"
    "entry_paircap_top1_qty_target_qty_8_cap_0p970_pxhi_0p80_iter4_l2_v2_leg_evidence"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604"
)

STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
STRATEGY_VERSION = "target_qty8_l2_clean_review_v1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"
BRANCH_ID = "one_to_five_min_any_65_80_entry_paircap_top1_qty_target_qty_8_cap_0.970_pxhi_0.80"

LEDGER_FIELDS = [
    "strategy_owner_line",
    "strategy_id",
    "strategy_version",
    "strategy_family",
    "candidate_granularity",
    "binding_status",
    "candidate_id",
    "source_candidate_id",
    "variant_id",
    "policy_id",
    "branch_id",
    "asset",
    "timeframe",
    "condition_id",
    "slug",
    "day",
    "first_leg_side",
    "completion_leg_side",
    "first_leg_ts_ms",
    "completion_leg_ts_ms",
    "first_source_candidate_row_id",
    "completion_source_candidate_row_id",
    "pair_delay_s",
    "target_qty",
    "paired_qty",
    "resid_qty",
    "first_leg_price",
    "completion_leg_price",
    "l1_pair_cost",
    "top5_pair_vwap_cost",
    "top5_pair_worst_cost",
    "buy_actual_est",
    "cash_pnl_est",
    "fee_model",
    "fee_rate",
    "l1_top_pair_pass",
    "depth_assisted_pair_pass",
    "l2_top_aligned_vwap_pass",
    "top1_depth_pair_fillable",
    "top5_depth_pair_fillable",
    "raw_l2_age_ok_pair",
    "top_overlay_required",
    "max_raw_l2_age_ms",
    "l2_top_aligned_fail_reason",
    "private_truth_ready",
    "strategy_promotion_ready",
    "live_ready",
    "deployable",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def stable_candidate_id(source_candidate_id: str) -> str:
    digest = hashlib.sha256(f"{STRATEGY_ID}|{STRATEGY_VERSION}|{source_candidate_id}".encode()).hexdigest()
    return f"ce25_tq8_{digest[:24]}"


def parse_slug(slug: str) -> tuple[str, str]:
    if slug.startswith("btc-updown-5m-"):
        return "BTC", "5m"
    return "UNKNOWN", "UNKNOWN"


def build_rows(
    l2_rows: list[dict[str, str]],
    source_rows_by_candidate: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for l2 in sorted(l2_rows, key=lambda r: (r.get("slug", ""), r.get("candidate_id", ""))):
        source_candidate_id = l2["candidate_id"]
        src = source_rows_by_candidate[source_candidate_id]
        asset, timeframe = parse_slug(l2.get("slug", ""))
        out.append(
            {
                "strategy_owner_line": OWNER_LINE,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_family": "ce25_high_price_strict_paircap_top1_qty_gate",
                "candidate_granularity": "HISTORICAL_REPLAY_ACTION",
                "binding_status": "REPLAY_BOUND_NOT_OOS_READY",
                "candidate_id": stable_candidate_id(source_candidate_id),
                "source_candidate_id": source_candidate_id,
                "variant_id": l2.get("variant_id", ""),
                "policy_id": l2.get("policy_id", ""),
                "branch_id": l2.get("branch_id", ""),
                "asset": asset,
                "timeframe": timeframe,
                "condition_id": l2.get("condition_id", ""),
                "slug": l2.get("slug", ""),
                "day": l2.get("day", ""),
                "first_leg_side": src.get("first_leg_side", ""),
                "completion_leg_side": src.get("completion_leg_side", ""),
                "first_leg_ts_ms": src.get("first_leg_ts_ms", ""),
                "completion_leg_ts_ms": src.get("completion_leg_ts_ms", ""),
                "first_source_candidate_row_id": src.get("first_source_candidate_row_id", ""),
                "completion_source_candidate_row_id": src.get("completion_source_candidate_row_id", ""),
                "pair_delay_s": src.get("pair_delay_s", ""),
                "target_qty": "8.0",
                "paired_qty": l2.get("paired_qty", ""),
                "resid_qty": src.get("resid_qty", ""),
                "first_leg_price": src.get("first_leg_price", ""),
                "completion_leg_price": src.get("completion_leg_price", ""),
                "l1_pair_cost": l2.get("l1_pair_cost", ""),
                "top5_pair_vwap_cost": l2.get("top5_pair_vwap_cost", ""),
                "top5_pair_worst_cost": l2.get("top5_pair_worst_cost", ""),
                "buy_actual_est": l2.get("buy_actual_est", ""),
                "cash_pnl_est": l2.get("cash_pnl_est", ""),
                "fee_model": src.get("fee_model", ""),
                "fee_rate": src.get("fee_rate", ""),
                "l1_top_pair_pass": str(truthy(l2.get("l1_top_pair_pass"))).lower(),
                "depth_assisted_pair_pass": str(truthy(l2.get("depth_assisted_pair_pass"))).lower(),
                "l2_top_aligned_vwap_pass": str(truthy(l2.get("l2_top_aligned_vwap_pass"))).lower(),
                "top1_depth_pair_fillable": str(truthy(l2.get("top1_depth_pair_fillable"))).lower(),
                "top5_depth_pair_fillable": str(truthy(l2.get("top5_depth_pair_fillable"))).lower(),
                "raw_l2_age_ok_pair": str(truthy(l2.get("raw_l2_age_ok_pair"))).lower(),
                "top_overlay_required": str(truthy(l2.get("any_top_overlay_required"))).lower(),
                "max_raw_l2_age_ms": l2.get("max_raw_l2_age_ms", ""),
                "l2_top_aligned_fail_reason": l2.get("l2_top_aligned_fail_reason", ""),
                "private_truth_ready": "false",
                "strategy_promotion_ready": "false",
                "live_ready": "false",
                "deployable": "false",
            }
        )
    return out


def validate_inputs(
    l2_rows: list[dict[str, str]],
    leg_rows: list[dict[str, str]],
    source_rows: list[dict[str, str]],
    l2_manifest: dict[str, Any],
    expected_rows: int,
) -> list[str]:
    errors: list[str] = []
    if len(l2_rows) != expected_rows:
        errors.append(f"l2_action_count_mismatch:{len(l2_rows)}!={expected_rows}")
    if len(source_rows) != expected_rows:
        errors.append(f"source_action_count_mismatch:{len(source_rows)}!={expected_rows}")
    if len(leg_rows) != expected_rows * 2:
        errors.append(f"leg_evidence_count_mismatch:{len(leg_rows)}!={expected_rows * 2}")
    l2_ids = [row.get("candidate_id", "") for row in l2_rows]
    leg_ids = [row.get("candidate_id", "") for row in leg_rows]
    src_ids = [row.get("candidate_id", "") for row in source_rows]
    if len(set(l2_ids)) != len(l2_ids):
        errors.append("duplicate_l2_candidate_id")
    if len(set(src_ids)) != len(src_ids):
        errors.append("duplicate_source_candidate_id")
    if set(l2_ids) != set(src_ids):
        errors.append("candidate_id_set_mismatch")
    if set(leg_ids) != set(l2_ids):
        errors.append("leg_candidate_id_set_mismatch")
    if any(leg_ids.count(candidate_id) != 2 for candidate_id in set(leg_ids)):
        errors.append("leg_candidate_count_not_two")
    if {row.get("leg_side") for row in leg_rows} != {"YES", "NO"}:
        errors.append("leg_side_semantics_drift")
    if {row.get("leg_role") for row in leg_rows} != {"first", "completion"}:
        errors.append("leg_role_semantics_drift")
    if not all(row.get("l1_source_row_id") for row in leg_rows):
        errors.append("missing_leg_l1_source_row_id")
    if not all(row.get("raw_l2_source_row_id") for row in leg_rows):
        errors.append("missing_leg_raw_l2_source_row_id")
    if {row.get("policy_id") for row in l2_rows} != {"CE25_BTC5M_HIGH_PRICE_STOP_1_5M_V1"}:
        errors.append("policy_id_drift")
    if {row.get("branch_id") for row in l2_rows} != {BRANCH_ID}:
        errors.append("branch_id_drift")
    if not all(str(row.get("slug", "")).startswith("btc-updown-5m-") for row in l2_rows):
        errors.append("non_btc_5m_slug")
    if not all(truthy(row.get("l2_top_aligned_vwap_pass")) for row in l2_rows):
        errors.append("l2_top_aligned_non_pass_row")
    if not all(row.get("l2_top_aligned_fail_reason") == "PASS" for row in l2_rows):
        errors.append("l2_fail_reason_non_pass")
    if not all(truthy(row.get("has_two_legs")) for row in l2_rows):
        errors.append("missing_two_leg_evidence")
    if not all(truthy(row.get("top_price_match_pair")) for row in l2_rows):
        errors.append("top_price_pair_mismatch")
    if not all(truthy(row.get("top1_depth_pair_fillable")) for row in l2_rows):
        errors.append("top1_depth_pair_not_fillable")
    if not all(truthy(row.get("l1_top_pair_pass")) for row in l2_rows):
        errors.append("l1_top_pair_non_pass")
    if not all(as_float(row.get("top5_pair_vwap_cost")) <= 0.9700001 for row in l2_rows):
        errors.append("top5_vwap_cost_gt_cap")
    if not all(as_float(row.get("top5_pair_worst_cost")) <= 0.9700001 for row in l2_rows):
        errors.append("top5_worst_cost_gt_cap")
    if not all(as_float(row.get("resid_qty")) == 0.0 for row in source_rows):
        errors.append("residual_qty_nonzero")
    if not all(0.0 < as_float(row.get("paired_qty")) <= 8.0 for row in source_rows):
        errors.append("paired_qty_outside_0_8")
    if not all(
        {row.get("first_leg_side"), row.get("completion_leg_side")} == {"YES", "NO"}
        for row in source_rows
    ):
        errors.append("leg_side_not_opposite_yes_no")
    if not all(
        0.65 <= max(as_float(row.get("first_leg_price")), as_float(row.get("completion_leg_price"))) <= 0.80
        for row in source_rows
    ):
        errors.append("high_price_leg_outside_0p65_0p80")
    if not all(as_float(row.get("pair_cost")) <= 0.9700001 for row in source_rows):
        errors.append("source_pair_cost_gt_cap")
    summary = l2_manifest.get("summary", {})
    if as_int(summary.get("action_count")) != expected_rows:
        errors.append("manifest_action_count_mismatch")
    if as_int(summary.get("market_count")) != 129:
        errors.append("manifest_market_count_mismatch")
    if as_int(summary.get("l2_top_aligned_vwap_pass_count")) != expected_rows:
        errors.append("manifest_l2_pass_count_mismatch")
    if as_float(summary.get("total_cash_pnl_est")) <= 0.0:
        errors.append("manifest_pnl_nonpositive")
    non_claims = l2_manifest.get("non_claims", {})
    for key in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable"):
        if non_claims.get(key) is not False:
            errors.append(f"non_claim_true_or_missing:{key}")
    return errors


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy_actual = sum(as_float(row.get("buy_actual_est")) for row in rows)
    cash_pnl = sum(as_float(row.get("cash_pnl_est")) for row in rows)
    active_days = len({row.get("day") for row in rows})
    return {
        "candidate_count": len(rows),
        "market_count": len({row.get("condition_id") for row in rows}),
        "slug_count": len({row.get("slug") for row in rows}),
        "active_days": active_days,
        "buy_actual_est": round(buy_actual, 6),
        "cash_pnl_est": round(cash_pnl, 6),
        "roi_est": round(cash_pnl / buy_actual, 6) if buy_actual else None,
        "max_rows_per_market": max(
            (
                sum(1 for row in rows if row.get("condition_id") == condition_id)
                for condition_id in {row.get("condition_id") for row in rows}
            ),
            default=0,
        ),
        "top_overlay_required_count": sum(1 for row in rows if row.get("top_overlay_required") == "true"),
        "raw_l2_age_ok_pair_count": sum(1 for row in rows if row.get("raw_l2_age_ok_pair") == "true"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l2-dir", type=Path, default=DEFAULT_L2_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-rows", type=int, default=134)
    args = parser.parse_args()

    l2_manifest_path = args.l2_dir / "CE25_L2_TOP_ALIGNED_VALIDATION_MANIFEST.json"
    l2_action_path = args.l2_dir / "ce25_l2_top_aligned_action_evidence.csv"
    l2_leg_path = args.l2_dir / "ce25_l2_top_aligned_leg_evidence.csv"
    l2_manifest = json.loads(l2_manifest_path.read_text())
    source_action_path = Path(l2_manifest["inputs"]["actions_csv"])
    source_manifest_path = Path(l2_manifest["inputs"]["result_manifest"])

    l2_rows = read_csv(l2_action_path)
    leg_rows = read_csv(l2_leg_path)
    source_rows = read_csv(source_action_path)
    source_rows_by_candidate = {row["candidate_id"]: row for row in source_rows}
    errors = validate_inputs(l2_rows, leg_rows, source_rows, l2_manifest, args.expected_rows)
    if errors:
        print(json.dumps({"ok": False, "errors": errors}, indent=2))
        return 2

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ledger_rows = build_rows(l2_rows, source_rows_by_candidate)
    validation_summary = summarize(ledger_rows)

    ledger_path = args.output_dir / "ce25_high_price_top1_qty_target_qty8_candidate_ledger.csv"
    strategy_path = args.output_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json"
    note_path = args.output_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_REVIEW_NOTE.md"
    manifest_path = args.output_dir / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_HASH_MANIFEST.json"

    write_csv(ledger_path, ledger_rows, LEDGER_FIELDS)
    strategy_input = {
        "schema_version": 1,
        "status": "KEEP_CE25_TARGET_QTY8_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY",
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_family": "ce25_high_price_strict_paircap_top1_qty_gate",
        "candidate_input_mode": "EXTERNAL_FILE",
        "candidate_csv": ledger_path.name,
        "candidate_csv_sha256": sha256_file(ledger_path),
        "candidate_count": validation_summary["candidate_count"],
        "expected_market_count": validation_summary["market_count"],
        "asset_universe": ["BTC"],
        "timeframe": "5m",
        "source_semantics": {
            "candidate_granularity": "HISTORICAL_REPLAY_ACTION",
            "binding_status": "REPLAY_BOUND_NOT_OOS_READY",
            "fresh_current_future_window": False,
            "public_replay_only": True,
            "private_order_truth": False,
        },
        "policy_contract": {
            "branch_id": BRANCH_ID,
            "target_qty": 8.0,
            "seed_px_lo": 0.65,
            "seed_px_hi": 0.80,
            "seed_l1_pair_cap": 0.970,
            "entry_requires_opposite_qty": True,
            "entry_requires_pair_cap": True,
            "fee_model": "official_taker",
            "fee_rate": 0.03,
        },
        "source_artifacts": {
            "l2_validation_dir": str(args.l2_dir),
            "l2_validation_manifest": str(l2_manifest_path),
            "l2_validation_manifest_sha256": sha256_file(l2_manifest_path),
            "l2_action_evidence_csv": str(l2_action_path),
            "l2_action_evidence_csv_sha256": sha256_file(l2_action_path),
            "l2_leg_evidence_csv": str(l2_leg_path),
            "l2_leg_evidence_csv_sha256": sha256_file(l2_leg_path),
            "source_book_shadow_actions_csv": str(source_action_path),
            "source_book_shadow_actions_csv_sha256": sha256_file(source_action_path),
            "source_book_shadow_manifest": str(source_manifest_path),
            "source_book_shadow_manifest_sha256": sha256_file(source_manifest_path),
        },
        "validation_summary": validation_summary,
        "fail_closed_checks": [
            "expected_row_count_134",
            "candidate_id_set_matches_l2_and_source_actions",
            "unique_candidate_ids",
            "btc_5m_slug_only",
            "opposite_yes_no_legs",
            "high_price_leg_in_0p65_0p80",
            "paired_qty_gt_0_and_le_8",
            "source_pair_cost_le_0p970",
            "all_l2_top_aligned_vwap_pass",
            "all_fail_reason_PASS",
            "exactly_two_leg_evidence_rows_per_candidate",
            "leg_side_semantics_yes_no",
            "leg_role_semantics_first_completion",
            "leg_l1_and_l2_source_rows_present",
            "has_two_legs",
            "top_price_match_pair",
            "top1_depth_pair_fillable",
            "l1_top_pair_pass",
            "top5_vwap_and_worst_cost_le_0p970",
            "residual_qty_zero",
            "branch_id_exact",
            "positive_pnl",
            "non_claims_false",
        ],
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "canary_authorized": False,
            "orders_authorized": False,
        },
    }
    write_json(strategy_path, strategy_input)

    note = f"""# CE25 High-Price Top1 Qty Target Qty 8 Candidate Ledger

Status: `KEEP_CE25_TARGET_QTY8_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY`

This package freezes the local public/replay target_qty=8 L2-clean action set as a review-only candidate ledger.

- candidate rows: {validation_summary['candidate_count']}
- markets: {validation_summary['market_count']}
- active days: {validation_summary['active_days']}
- buy_actual_est: {validation_summary['buy_actual_est']}
- cash_pnl_est: {validation_summary['cash_pnl_est']}
- ROI estimate: {validation_summary['roi_est']}
- top overlay review rows: {validation_summary['top_overlay_required_count']}
- raw L2 age OK pair rows: {validation_summary['raw_l2_age_ok_pair_count']}

Boundaries:

- public replay only
- historical replay-bound rows, not current/future OOS targets
- no private key, import, order, cancel, redeem, live, deploy, funding
- no private truth, promotion, live-ready, or deployable claim
- top overlay and raw-L2 freshness limitations keep this at review-required status
"""
    note_path.write_text(note)

    artifacts = [
        ledger_path,
        strategy_path,
        note_path,
        l2_manifest_path,
        l2_action_path,
        l2_leg_path,
        source_action_path,
        source_manifest_path,
    ]
    manifest = {
        "schema_version": 1,
        "created_at": l2_manifest.get("created_at") or datetime.now(timezone.utc).isoformat(),
        "status": strategy_input["status"],
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "validation_summary": validation_summary,
        "artifacts": [
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in artifacts
        ],
        "non_claims": strategy_input["non_claims"],
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": True,
                "status": strategy_input["status"],
                "output_dir": str(args.output_dir),
                "candidate_count": validation_summary["candidate_count"],
                "market_count": validation_summary["market_count"],
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

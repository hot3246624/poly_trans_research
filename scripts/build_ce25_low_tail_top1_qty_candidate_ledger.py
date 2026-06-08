#!/usr/bin/env python3
"""Build a review-only CE25 low-tail side-split top1-qty candidate ledger."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_L2_ROOT = ROOT / "data/exports/ce25_low_tail_side_split_v2_top1_qty_l2_validation_20260606"
DEFAULT_OFFICIAL07_RUN = Path(
    "/Users/hot/web3Scientist/poly_backtest_data/derived/"
    "ce25_nagi_shadow_policy_autoresearch_v0/"
    "ce25_low_tail_side_split_v2_top1_qty_official07_full_20260606"
)
DEFAULT_OUTPUT_DIR = ROOT / "data/exports/ce25_low_tail_top1_qty_candidate_ledger_20260606"

STATUS = "KEEP_CE25_LOW_TAIL_TOP1_QTY_NORMALIZED_CANDIDATE_LEDGER_REVIEW_REQUIRED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2"
STRATEGY_VERSION = "l2_clean_official07_review_v1"
OWNER_LINE = "CE25_LOW_TAIL_RESEARCH"
POLICY_ID = "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_V2"

EXPECTED_BRANCHES = {
    "last60_down_20_35_side_split_entry_paircap_top1_qty_cap_0.965": ("DOWN", "entry_paircap", 5.0, 38, 38),
    "last60_down_20_35_side_split_same_row_top1_qty_cap_0.965": ("DOWN", "same_row", 5.0, 38, 38),
    "last60_up_20_35_side_split_entry_paircap_top1_qty_cap_0.965": ("UP", "entry_paircap", 5.0, 30, 29),
    "last60_up_20_35_side_split_same_row_top1_qty_cap_0.965": ("UP", "same_row", 5.0, 30, 29),
    "last60_down_20_35_side_split_entry_paircap_top1_qty_target_qty_8_cap_0.965": (
        "DOWN",
        "entry_paircap",
        8.0,
        37,
        37,
    ),
    "last60_down_20_35_side_split_same_row_top1_qty_target_qty_8_cap_0.965": ("DOWN", "same_row", 8.0, 37, 37),
    "last60_up_20_35_side_split_entry_paircap_top1_qty_target_qty_8_cap_0.965": ("UP", "entry_paircap", 8.0, 29, 28),
    "last60_up_20_35_side_split_same_row_top1_qty_target_qty_8_cap_0.965": ("UP", "same_row", 8.0, 29, 28),
}

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
    "side_split",
    "strict_mode",
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
    "buy_actual_est_official07",
    "cash_pnl_est_official07",
    "buy_actual_est_l2_fee03",
    "cash_pnl_est_l2_fee03",
    "fee_model",
    "fee_rate_stress",
    "l2_validation_fee_rate",
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

SUMMARY_FIELDS = [
    "branch_id",
    "side_split",
    "strict_mode",
    "target_qty",
    "action_count",
    "market_count",
    "l2_pass_count",
    "top_overlay_required_count",
    "raw_l2_age_ok_pair_count",
    "official07_buy_actual_est",
    "official07_cash_pnl_est",
    "official07_roi_est",
    "l2_fee03_buy_actual_est",
    "l2_fee03_cash_pnl_est",
    "l2_fee03_roi_est",
    "max_l1_pair_cost",
    "max_top5_pair_vwap_cost",
    "max_top5_pair_worst_cost",
    "status",
]

OVERLAY_FIELDS = [
    "candidate_id",
    "source_candidate_id",
    "branch_id",
    "side_split",
    "strict_mode",
    "target_qty",
    "condition_id",
    "slug",
    "day",
    "top_overlay_required",
    "raw_l2_age_ok_pair",
    "dependency_category",
    "max_raw_l2_age_ms",
    "first_leg_raw_l2_age_ms",
    "completion_leg_raw_l2_age_ms",
    "first_leg_top_overlay_required",
    "completion_leg_top_overlay_required",
    "l1_top_pair_pass",
    "top1_depth_pair_fillable",
    "top5_depth_pair_fillable",
    "top5_pair_vwap_cost",
    "top5_pair_worst_cost",
    "l2_top_aligned_fail_reason",
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


def stable_candidate_id(branch_id: str, source_candidate_id: str) -> str:
    payload = f"{STRATEGY_ID}|{STRATEGY_VERSION}|{branch_id}|{source_candidate_id}"
    return f"ce25_lt_{hashlib.sha256(payload.encode()).hexdigest()[:24]}"


def parse_asset_timeframe(slug: str) -> tuple[str, str]:
    if slug.startswith("btc-updown-5m-"):
        return "BTC", "5m"
    return "UNKNOWN", "UNKNOWN"


def non_claims_false(payload: dict[str, Any]) -> bool:
    non_claims = payload.get("non_claims") if isinstance(payload.get("non_claims"), dict) else {}
    return all(
        non_claims.get(key) is False
        for key in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable")
    )


def locate_l2_dirs(l2_root: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for child in sorted(l2_root.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "CE25_L2_TOP_ALIGNED_VALIDATION_MANIFEST.json"
        if not manifest_path.is_file():
            continue
        manifest = json.loads(manifest_path.read_text())
        branch_id = manifest.get("summary", {}).get("branch_id")
        if branch_id:
            out[str(branch_id)] = child
    return out


def locate_official07_action_paths(run_dir: Path) -> dict[str, Path]:
    ledger_path = run_dir / "autoresearch_ledger.csv"
    rows = read_csv(ledger_path)
    out: dict[str, Path] = {}
    for row in rows:
        branch_id = row.get("branch_id", "")
        if branch_id not in EXPECTED_BRANCHES:
            continue
        output_dir = Path(row["output_dir"])
        action_path = output_dir / "book_shadow_actions.csv"
        if action_path.is_file():
            out[branch_id] = action_path
    return out


def validate_branch(
    branch_id: str,
    l2_dir: Path,
    official07_actions_path: Path,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[Path]]:
    errors: list[str] = []
    side_split, strict_mode, target_qty, expected_actions, expected_markets = EXPECTED_BRANCHES[branch_id]
    expected_first_side = "YES" if side_split == "UP" else "NO"
    expected_completion_side = "NO" if expected_first_side == "YES" else "YES"

    manifest_path = l2_dir / "CE25_L2_TOP_ALIGNED_VALIDATION_MANIFEST.json"
    action_path = l2_dir / "ce25_l2_top_aligned_action_evidence.csv"
    leg_path = l2_dir / "ce25_l2_top_aligned_leg_evidence.csv"
    summary_path = l2_dir / "ce25_l2_top_aligned_summary.csv"
    manifest = json.loads(manifest_path.read_text())
    source03_action_path = Path(manifest["inputs"]["actions_csv"])
    source03_manifest_path = Path(manifest["inputs"]["result_manifest"])
    official07_manifest_path = official07_actions_path.with_name("BOOK_SHADOW_RESULT_MANIFEST.json")

    l2_rows = read_csv(action_path)
    leg_rows = read_csv(leg_path)
    summary_rows = read_csv(summary_path)
    source03_rows = read_csv(source03_action_path)
    official07_rows = read_csv(official07_actions_path)
    source03_by_id = {row["candidate_id"]: row for row in source03_rows}
    official07_by_id = {row["candidate_id"]: row for row in official07_rows}
    legs_by_id: dict[str, list[dict[str, str]]] = {}
    for leg in leg_rows:
        legs_by_id.setdefault(leg["candidate_id"], []).append(leg)

    if len(l2_rows) != expected_actions:
        errors.append(f"{branch_id}:l2_action_count_mismatch:{len(l2_rows)}!={expected_actions}")
    if len({row["condition_id"] for row in l2_rows}) != expected_markets:
        errors.append(f"{branch_id}:market_count_mismatch")
    if len(leg_rows) != expected_actions * 2:
        errors.append(f"{branch_id}:leg_count_mismatch")
    l2_ids = {row["candidate_id"] for row in l2_rows}
    if l2_ids != set(source03_by_id):
        errors.append(f"{branch_id}:source03_candidate_id_set_mismatch")
    if l2_ids != set(official07_by_id):
        errors.append(f"{branch_id}:official07_candidate_id_set_mismatch")
    if any(len(legs_by_id.get(candidate_id, [])) != 2 for candidate_id in l2_ids):
        errors.append(f"{branch_id}:not_two_legs_per_candidate")
    if manifest.get("status") != "KEEP_L2_TOP_ALIGNED_ACTIONS_VALIDATED_REVIEW_REQUIRED":
        errors.append(f"{branch_id}:manifest_status_mismatch")
    if not non_claims_false(manifest):
        errors.append(f"{branch_id}:manifest_non_claims_not_false")
    if summary_rows and as_int(summary_rows[0].get("l2_top_aligned_vwap_pass_count")) != expected_actions:
        errors.append(f"{branch_id}:summary_l2_pass_count_mismatch")

    ledger_rows: list[dict[str, Any]] = []
    leg_out_rows: list[dict[str, Any]] = []
    for l2 in sorted(l2_rows, key=lambda row: (row["slug"], row["candidate_id"])):
        source_candidate_id = l2["candidate_id"]
        src03 = source03_by_id.get(source_candidate_id, {})
        src07 = official07_by_id.get(source_candidate_id, {})
        legs = legs_by_id.get(source_candidate_id, [])
        asset, timeframe = parse_asset_timeframe(l2.get("slug", ""))

        row_errors: list[str] = []
        if l2.get("policy_id") != POLICY_ID or src03.get("policy_id") != POLICY_ID or src07.get("policy_id") != POLICY_ID:
            row_errors.append("policy_id_mismatch")
        if l2.get("branch_id") != branch_id or src03.get("branch_id") != branch_id or src07.get("branch_id") != branch_id:
            row_errors.append("branch_id_mismatch")
        if asset != "BTC" or timeframe != "5m":
            row_errors.append("non_btc_5m_slug")
        if src03.get("first_leg_side") != expected_first_side or src07.get("first_leg_side") != expected_first_side:
            row_errors.append("first_leg_side_mismatch")
        if src03.get("completion_leg_side") != expected_completion_side or src07.get("completion_leg_side") != expected_completion_side:
            row_errors.append("completion_leg_side_mismatch")
        if not (0.20 <= as_float(src03.get("first_leg_price")) <= 0.35):
            row_errors.append("first_leg_price_outside_0p20_0p35")
        if not (0.0 < as_float(src03.get("paired_qty")) <= target_qty):
            row_errors.append("paired_qty_outside_target")
        if as_float(src03.get("resid_qty")) != 0.0 or as_float(src07.get("resid_qty")) != 0.0:
            row_errors.append("residual_nonzero")
        if as_float(src03.get("pair_cost")) > 0.9650001 or as_float(l2.get("top5_pair_vwap_cost")) > 0.9650001:
            row_errors.append("pair_cost_gt_cap")
        if not truthy(l2.get("l2_top_aligned_vwap_pass")) or l2.get("l2_top_aligned_fail_reason") != "PASS":
            row_errors.append("l2_not_pass")
        if not truthy(l2.get("top1_depth_pair_fillable")):
            row_errors.append("top1_depth_not_fillable")
        if sorted(leg.get("leg_role") for leg in legs) != ["completion", "first"]:
            row_errors.append("leg_role_mismatch")
        if {leg.get("leg_side") for leg in legs} != {"YES", "NO"}:
            row_errors.append("leg_side_mismatch")
        if not all(truthy(leg.get("top1_depth_ge_qty")) for leg in legs):
            row_errors.append("leg_top1_depth_not_fillable")
        if row_errors:
            errors.extend(f"{branch_id}:{source_candidate_id}:{err}" for err in row_errors)

        normalized_candidate_id = stable_candidate_id(branch_id, source_candidate_id)
        ledger_rows.append(
            {
                "strategy_owner_line": OWNER_LINE,
                "strategy_id": STRATEGY_ID,
                "strategy_version": STRATEGY_VERSION,
                "strategy_family": "ce25_btc5m_low_tail_side_split_top1_qty",
                "candidate_granularity": "HISTORICAL_REPLAY_ACTION",
                "binding_status": "REPLAY_BOUND_NOT_OOS_READY",
                "candidate_id": normalized_candidate_id,
                "source_candidate_id": source_candidate_id,
                "variant_id": l2.get("variant_id", ""),
                "policy_id": POLICY_ID,
                "branch_id": branch_id,
                "side_split": side_split,
                "strict_mode": strict_mode,
                "asset": asset,
                "timeframe": timeframe,
                "condition_id": l2.get("condition_id", ""),
                "slug": l2.get("slug", ""),
                "day": l2.get("day", ""),
                "first_leg_side": src03.get("first_leg_side", ""),
                "completion_leg_side": src03.get("completion_leg_side", ""),
                "first_leg_ts_ms": src03.get("first_leg_ts_ms", ""),
                "completion_leg_ts_ms": src03.get("completion_leg_ts_ms", ""),
                "first_source_candidate_row_id": src03.get("first_source_candidate_row_id", ""),
                "completion_source_candidate_row_id": src03.get("completion_source_candidate_row_id", ""),
                "pair_delay_s": src03.get("pair_delay_s", ""),
                "target_qty": f"{target_qty:.1f}",
                "paired_qty": l2.get("paired_qty", ""),
                "resid_qty": src03.get("resid_qty", ""),
                "first_leg_price": src03.get("first_leg_price", ""),
                "completion_leg_price": src03.get("completion_leg_price", ""),
                "l1_pair_cost": l2.get("l1_pair_cost", ""),
                "top5_pair_vwap_cost": l2.get("top5_pair_vwap_cost", ""),
                "top5_pair_worst_cost": l2.get("top5_pair_worst_cost", ""),
                "buy_actual_est_official07": src07.get("buy_actual_est", ""),
                "cash_pnl_est_official07": src07.get("cash_pnl_est", ""),
                "buy_actual_est_l2_fee03": l2.get("buy_actual_est", ""),
                "cash_pnl_est_l2_fee03": l2.get("cash_pnl_est", ""),
                "fee_model": src07.get("fee_model", "official_taker"),
                "fee_rate_stress": src07.get("fee_rate", "0.07"),
                "l2_validation_fee_rate": "0.03",
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
        for leg in legs:
            leg_out = {
                "candidate_id": normalized_candidate_id,
                "source_candidate_id": source_candidate_id,
                "side_split": side_split,
                "strict_mode": strict_mode,
                "target_qty": f"{target_qty:.1f}",
            }
            leg_out.update(leg)
            leg_out_rows.append(leg_out)

    branch_rows = ledger_rows
    buy07 = sum(as_float(row["buy_actual_est_official07"]) for row in branch_rows)
    pnl07 = sum(as_float(row["cash_pnl_est_official07"]) for row in branch_rows)
    buy03 = sum(as_float(row["buy_actual_est_l2_fee03"]) for row in branch_rows)
    pnl03 = sum(as_float(row["cash_pnl_est_l2_fee03"]) for row in branch_rows)
    branch_summary = {
        "branch_id": branch_id,
        "side_split": side_split,
        "strict_mode": strict_mode,
        "target_qty": f"{target_qty:.1f}",
        "action_count": len(branch_rows),
        "market_count": len({row["condition_id"] for row in branch_rows}),
        "l2_pass_count": sum(1 for row in branch_rows if row["l2_top_aligned_vwap_pass"] == "true"),
        "top_overlay_required_count": sum(1 for row in branch_rows if row["top_overlay_required"] == "true"),
        "raw_l2_age_ok_pair_count": sum(1 for row in branch_rows if row["raw_l2_age_ok_pair"] == "true"),
        "official07_buy_actual_est": round(buy07, 6),
        "official07_cash_pnl_est": round(pnl07, 6),
        "official07_roi_est": round(pnl07 / buy07, 6) if buy07 else None,
        "l2_fee03_buy_actual_est": round(buy03, 6),
        "l2_fee03_cash_pnl_est": round(pnl03, 6),
        "l2_fee03_roi_est": round(pnl03 / buy03, 6) if buy03 else None,
        "max_l1_pair_cost": max(as_float(row["l1_pair_cost"]) for row in branch_rows),
        "max_top5_pair_vwap_cost": max(as_float(row["top5_pair_vwap_cost"]) for row in branch_rows),
        "max_top5_pair_worst_cost": max(as_float(row["top5_pair_worst_cost"]) for row in branch_rows),
        "status": "KEEP_L2_CLEAN_OFFICIAL07_POSITIVE_REVIEW_REQUIRED",
    }
    artifacts = [
        manifest_path,
        action_path,
        leg_path,
        summary_path,
        source03_action_path,
        source03_manifest_path,
        official07_actions_path,
        official07_manifest_path,
    ]
    return errors, ledger_rows, leg_out_rows, branch_summary, artifacts


def summarize_all(rows: list[dict[str, Any]]) -> dict[str, Any]:
    buy07 = sum(as_float(row["buy_actual_est_official07"]) for row in rows)
    pnl07 = sum(as_float(row["cash_pnl_est_official07"]) for row in rows)
    return {
        "candidate_count": len(rows),
        "unique_source_candidate_count": len({row["source_candidate_id"] for row in rows}),
        "unique_market_count": len({row["condition_id"] for row in rows}),
        "branch_count": len({row["branch_id"] for row in rows}),
        "side_counts": {
            side: sum(1 for row in rows if row["side_split"] == side)
            for side in sorted({row["side_split"] for row in rows})
        },
        "target_qty_counts": {
            qty: sum(1 for row in rows if row["target_qty"] == qty)
            for qty in sorted({row["target_qty"] for row in rows})
        },
        "official07_buy_actual_est": round(buy07, 6),
        "official07_cash_pnl_est": round(pnl07, 6),
        "official07_roi_est": round(pnl07 / buy07, 6) if buy07 else None,
        "l2_pass_count": sum(1 for row in rows if row["l2_top_aligned_vwap_pass"] == "true"),
        "top_overlay_required_count": sum(1 for row in rows if row["top_overlay_required"] == "true"),
        "raw_l2_age_ok_pair_count": sum(1 for row in rows if row["raw_l2_age_ok_pair"] == "true"),
        "max_rows_per_market": max(
            (
                sum(1 for row in rows if row["condition_id"] == condition_id)
                for condition_id in {row["condition_id"] for row in rows}
            ),
            default=0,
        ),
    }


def overlay_category(overlay: bool, raw_ok: bool) -> str:
    if overlay and not raw_ok:
        return "OVERLAY_AND_RAW_L2_STALE"
    if overlay:
        return "OVERLAY_ONLY"
    if not raw_ok:
        return "RAW_L2_STALE_ONLY"
    return "NO_OVERLAY_RAW_L2_OK"


def build_overlay_audit_rows(
    ledger_rows: list[dict[str, Any]],
    leg_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    legs_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for leg in leg_rows:
        legs_by_candidate.setdefault(str(leg["candidate_id"]), []).append(leg)
    out: list[dict[str, Any]] = []
    for row in ledger_rows:
        candidate_legs = legs_by_candidate.get(str(row["candidate_id"]), [])
        first_leg = next((leg for leg in candidate_legs if leg.get("leg_role") == "first"), {})
        completion_leg = next((leg for leg in candidate_legs if leg.get("leg_role") == "completion"), {})
        overlay = truthy(row.get("top_overlay_required"))
        raw_ok = truthy(row.get("raw_l2_age_ok_pair"))
        out.append(
            {
                "candidate_id": row["candidate_id"],
                "source_candidate_id": row["source_candidate_id"],
                "branch_id": row["branch_id"],
                "side_split": row["side_split"],
                "strict_mode": row["strict_mode"],
                "target_qty": row["target_qty"],
                "condition_id": row["condition_id"],
                "slug": row["slug"],
                "day": row["day"],
                "top_overlay_required": row["top_overlay_required"],
                "raw_l2_age_ok_pair": row["raw_l2_age_ok_pair"],
                "dependency_category": overlay_category(overlay, raw_ok),
                "max_raw_l2_age_ms": row["max_raw_l2_age_ms"],
                "first_leg_raw_l2_age_ms": first_leg.get("raw_l2_age_ms", ""),
                "completion_leg_raw_l2_age_ms": completion_leg.get("raw_l2_age_ms", ""),
                "first_leg_top_overlay_required": str(truthy(first_leg.get("top_overlay_required"))).lower(),
                "completion_leg_top_overlay_required": str(truthy(completion_leg.get("top_overlay_required"))).lower(),
                "l1_top_pair_pass": row["l1_top_pair_pass"],
                "top1_depth_pair_fillable": row["top1_depth_pair_fillable"],
                "top5_depth_pair_fillable": row["top5_depth_pair_fillable"],
                "top5_pair_vwap_cost": row["top5_pair_vwap_cost"],
                "top5_pair_worst_cost": row["top5_pair_worst_cost"],
                "l2_top_aligned_fail_reason": row["l2_top_aligned_fail_reason"],
            }
        )
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--l2-root", type=Path, default=DEFAULT_L2_ROOT)
    parser.add_argument("--official07-run-dir", type=Path, default=DEFAULT_OFFICIAL07_RUN)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    l2_dirs = locate_l2_dirs(args.l2_root.expanduser().resolve())
    official07_paths = locate_official07_action_paths(args.official07_run_dir.expanduser().resolve())
    errors: list[str] = []
    missing_l2 = sorted(set(EXPECTED_BRANCHES) - set(l2_dirs))
    missing_official07 = sorted(set(EXPECTED_BRANCHES) - set(official07_paths))
    if missing_l2:
        errors.append(f"missing_l2_branches:{missing_l2}")
    if missing_official07:
        errors.append(f"missing_official07_branches:{missing_official07}")

    ledger_rows: list[dict[str, Any]] = []
    leg_rows: list[dict[str, Any]] = []
    branch_summaries: list[dict[str, Any]] = []
    artifacts: list[Path] = [
        args.official07_run_dir.expanduser().resolve() / "AUTORESEARCH_MANIFEST.json",
        args.official07_run_dir.expanduser().resolve() / "autoresearch_ledger.csv",
        args.official07_run_dir.expanduser().resolve() / "branch_control_summary.csv",
        args.official07_run_dir.expanduser().resolve() / "policy_fee_summary.csv",
    ]
    for branch_id in sorted(EXPECTED_BRANCHES):
        if branch_id not in l2_dirs or branch_id not in official07_paths:
            continue
        branch_errors, branch_rows, branch_legs, branch_summary, branch_artifacts = validate_branch(
            branch_id, l2_dirs[branch_id], official07_paths[branch_id]
        )
        errors.extend(branch_errors)
        ledger_rows.extend(branch_rows)
        leg_rows.extend(branch_legs)
        branch_summaries.append(branch_summary)
        artifacts.extend(branch_artifacts)

    if len(ledger_rows) != 268:
        errors.append(f"candidate_count_mismatch:{len(ledger_rows)}!=268")
    if len(leg_rows) != 536:
        errors.append(f"leg_evidence_count_mismatch:{len(leg_rows)}!=536")
    if len({row["candidate_id"] for row in ledger_rows}) != len(ledger_rows):
        errors.append("duplicate_normalized_candidate_id")
    if any(row["private_truth_ready"] != "false" or row["live_ready"] != "false" for row in ledger_rows):
        errors.append("readiness_flag_true")
    if any(as_float(row["cash_pnl_est_official07"]) <= 0.0 for row in ledger_rows):
        errors.append("nonpositive_official07_action_pnl")

    if errors:
        print(json.dumps({"ok": False, "errors": errors[:50], "error_count": len(errors)}, indent=2, sort_keys=True))
        return 2

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ce25_low_tail_top1_qty_candidate_ledger.csv"
    leg_path = output_dir / "ce25_low_tail_top1_qty_leg_evidence.csv"
    summary_path = output_dir / "ce25_low_tail_top1_qty_side_qty_summary.csv"
    overlay_path = output_dir / "ce25_low_tail_top1_qty_overlay_freshness_action_audit.csv"
    strategy_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_STRATEGY_INPUT.json"
    note_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_REVIEW_NOTE.md"
    manifest_path = output_dir / "CE25_LOW_TAIL_TOP1_QTY_HASH_MANIFEST.json"

    write_csv(ledger_path, ledger_rows, LEDGER_FIELDS)
    leg_fields = ["candidate_id", "source_candidate_id", "side_split", "strict_mode", "target_qty"]
    if leg_rows:
        leg_fields.extend([field for field in leg_rows[0].keys() if field not in leg_fields])
    write_csv(leg_path, leg_rows, leg_fields)
    write_csv(summary_path, branch_summaries, SUMMARY_FIELDS)
    overlay_rows = build_overlay_audit_rows(ledger_rows, leg_rows)
    write_csv(overlay_path, overlay_rows, OVERLAY_FIELDS)
    validation_summary = summarize_all(ledger_rows)
    overlay_category_counts = {
        category: sum(1 for row in overlay_rows if row["dependency_category"] == category)
        for category in sorted({row["dependency_category"] for row in overlay_rows})
    }

    strategy_input = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_version": STRATEGY_VERSION,
        "strategy_family": "ce25_btc5m_low_tail_side_split_top1_qty",
        "candidate_input_mode": "EXTERNAL_FILE",
        "candidate_csv": ledger_path.name,
        "candidate_csv_sha256": sha256_file(ledger_path),
        "leg_evidence_csv": leg_path.name,
        "leg_evidence_csv_sha256": sha256_file(leg_path),
        "side_qty_summary_csv": summary_path.name,
        "side_qty_summary_csv_sha256": sha256_file(summary_path),
        "overlay_freshness_action_audit_csv": overlay_path.name,
        "overlay_freshness_action_audit_csv_sha256": sha256_file(overlay_path),
        "candidate_count": validation_summary["candidate_count"],
        "unique_source_candidate_count": validation_summary["unique_source_candidate_count"],
        "expected_market_count": validation_summary["unique_market_count"],
        "asset_universe": ["BTC"],
        "timeframe": "5m",
        "source_semantics": {
            "candidate_granularity": "HISTORICAL_REPLAY_ACTION",
            "binding_status": "REPLAY_BOUND_NOT_OOS_READY",
            "fresh_current_future_window": False,
            "public_replay_only": True,
            "private_order_truth": False,
            "oos_ready": False,
        },
        "policy_contract": {
            "time_to_close_window_s": [0, 60],
            "strategy_input_offset_s": [240, 300],
            "first_leg_price_range": [0.20, 0.35],
            "side_split_required": True,
            "side_ledgers": ["UP", "DOWN"],
            "paircap": 0.965,
            "entry_requires_opposite_qty": True,
            "entry_requires_pair_cap": True,
            "strict_modes": ["same_row", "entry_paircap"],
            "target_qty_default": 5.0,
            "target_qty_validation_lane": 8.0,
            "fee_model": "polymarket_official_taker_formula",
            "fee_rate_stress": 0.07,
            "l2_validation_fee_rate": 0.03,
        },
        "validation_summary": validation_summary,
        "overlay_freshness_summary": {
            "dependency_category_counts": overlay_category_counts,
            "top_overlay_required_count": validation_summary["top_overlay_required_count"],
            "raw_l2_age_ok_pair_count": validation_summary["raw_l2_age_ok_pair_count"],
            "raw_l2_stale_pair_count": validation_summary["candidate_count"]
            - validation_summary["raw_l2_age_ok_pair_count"],
            "interpretation": (
                "Top1 qty and L1 paircap are clean, but overlay/raw-L2 freshness remains review-required "
                "before any OOS-style packet."
            ),
        },
        "branch_summaries": branch_summaries,
        "source_artifacts": {
            "l2_validation_root": str(args.l2_root.expanduser().resolve()),
            "official07_run_dir": str(args.official07_run_dir.expanduser().resolve()),
            "official07_run_manifest_sha256": sha256_file(
                args.official07_run_dir.expanduser().resolve() / "AUTORESEARCH_MANIFEST.json"
            ),
            "official07_autoresearch_ledger_sha256": sha256_file(
                args.official07_run_dir.expanduser().resolve() / "autoresearch_ledger.csv"
            ),
        },
        "fail_closed_checks": [
            "expected_8_branch_lanes_present",
            "candidate_count_268",
            "leg_evidence_count_536",
            "candidate_id_sets_match_l2_fee03_source_and_official07_source",
            "unique_normalized_candidate_ids",
            "btc_5m_slug_only",
            "side_split_first_leg_yes_no_mapping",
            "first_leg_price_in_0p20_0p35",
            "paired_qty_gt_0_and_le_target_qty",
            "residual_qty_zero",
            "pair_cost_le_0p965",
            "top5_vwap_and_worst_cost_le_0p965",
            "all_l2_top_aligned_vwap_pass",
            "all_fail_reason_PASS",
            "top1_depth_pair_fillable",
            "exactly_two_leg_evidence_rows_per_candidate",
            "leg_top1_depth_ge_qty",
            "official07_action_pnl_positive",
            "readiness_flags_false",
        ],
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "canary_authorized": False,
            "orders_authorized": False,
            "candidate_import_authorized": False,
        },
    }
    write_json(strategy_path, strategy_input)

    note_path.write_text(
        "\n".join(
            [
                "# CE25 Low-Tail Top1 Qty Candidate Ledger",
                "",
                f"Status: `{STATUS}`",
                "",
                "This package freezes the low-tail side-split + top1 qty L2-clean research set as review-only candidates.",
                "",
                f"- branch-lane candidates: {validation_summary['candidate_count']}",
                f"- unique source actions: {validation_summary['unique_source_candidate_count']}",
                f"- unique markets: {validation_summary['unique_market_count']}",
                f"- official 7% fee stress buy_actual_est: {validation_summary['official07_buy_actual_est']}",
                f"- official 7% fee stress cash_pnl_est: {validation_summary['official07_cash_pnl_est']}",
                f"- official 7% fee stress ROI: {validation_summary['official07_roi_est']}",
                f"- top overlay review rows: {validation_summary['top_overlay_required_count']}",
                f"- raw L2 age OK pair rows: {validation_summary['raw_l2_age_ok_pair_count']}",
                f"- overlay dependency categories: {json.dumps(overlay_category_counts, sort_keys=True)}",
                "",
                "Boundaries:",
                "",
                "- historical public replay/book-shadow/L2 evidence only",
                "- not current/future OOS target materialization",
                "- no private key, candidate import, order, cancel, redeem, canary, live, deploy, funding, or latest pointer",
                "- no private truth, promotion-ready, live-ready, or deployable claim",
                "- overlay/freshness attribution remains review-required before any OOS-style packet",
                "",
            ]
        )
    )

    artifacts.extend([ledger_path, leg_path, summary_path, overlay_path, strategy_path, note_path])
    unique_artifacts = []
    seen: set[Path] = set()
    for path in artifacts:
        path = path.expanduser().resolve()
        if path in seen:
            continue
        seen.add(path)
        unique_artifacts.append(path)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "validation_summary": validation_summary,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in unique_artifacts
        ],
        "non_claims": strategy_input["non_claims"],
    }
    write_json(manifest_path, manifest)

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(output_dir),
                "candidate_count": validation_summary["candidate_count"],
                "unique_market_count": validation_summary["unique_market_count"],
                "official07_roi_est": validation_summary["official07_roi_est"],
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

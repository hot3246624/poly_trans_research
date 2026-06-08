#!/usr/bin/env python3
"""Summarize overlay/freshness dependencies for CE25 target_qty=8."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_STRATEGY_INPUT = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604/"
    "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json"
)
DEFAULT_SOURCE_BRIDGE_SUMMARY = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_source_bridge_20260604/"
    "CE25_TARGET_QTY8_SOURCE_BRIDGE_SUMMARY.json"
)
DEFAULT_OUTPUT_DIR = Path(
    "/Users/hot/web3Scientist/poly_trans_research/data/exports/"
    "ce25_high_price_top1_qty_target_qty8_overlay_freshness_attribution_20260604"
)
STATUS = "KEEP_CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_REVIEW_REQUIRED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    pos = (len(xs) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1 - frac) + xs[hi] * frac


def group_counts(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        key = str(row.get(field, ""))
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda item: (-item[1], item[0])))


def category(overlay: bool, raw_ok: bool) -> str:
    if overlay and not raw_ok:
        return "OVERLAY_AND_RAW_L2_STALE"
    if overlay:
        return "OVERLAY_ONLY"
    if not raw_ok:
        return "RAW_L2_STALE_ONLY"
    return "NO_OVERLAY_RAW_L2_OK"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strategy-input", type=Path, default=DEFAULT_STRATEGY_INPUT)
    parser.add_argument("--source-bridge-summary", type=Path, default=DEFAULT_SOURCE_BRIDGE_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    strategy_path = args.strategy_input.expanduser().resolve()
    source_bridge_path = args.source_bridge_summary.expanduser().resolve()
    strategy = read_json(strategy_path)
    source_bridge = read_json(source_bridge_path)
    source_artifacts = strategy["source_artifacts"]
    ledger_path = strategy_path.parent / strategy["candidate_csv"]
    leg_path = Path(source_artifacts["l2_leg_evidence_csv"]).expanduser().resolve()
    l2_manifest_path = Path(source_artifacts["l2_validation_manifest"]).expanduser().resolve()
    ledger_rows = read_csv(ledger_path)
    leg_rows = read_csv(leg_path)

    legs_by_candidate: dict[str, list[dict[str, str]]] = {}
    for leg in leg_rows:
        legs_by_candidate.setdefault(leg["candidate_id"], []).append(leg)

    audit_rows: list[dict[str, Any]] = []
    for row in ledger_rows:
        source_candidate_id = row["source_candidate_id"]
        legs = legs_by_candidate.get(source_candidate_id, [])
        leg_ages = [as_float(leg.get("raw_l2_age_ms")) for leg in legs]
        overlay = truthy(row.get("top_overlay_required"))
        raw_ok = truthy(row.get("raw_l2_age_ok_pair"))
        audit_rows.append(
            {
                "candidate_id": row["candidate_id"],
                "source_candidate_id": source_candidate_id,
                "condition_id": row["condition_id"],
                "slug": row["slug"],
                "day": row["day"],
                "paired_qty": row["paired_qty"],
                "buy_actual_est": row["buy_actual_est"],
                "cash_pnl_est": row["cash_pnl_est"],
                "top_overlay_required": str(overlay).lower(),
                "raw_l2_age_ok_pair": str(raw_ok).lower(),
                "dependency_category": category(overlay, raw_ok),
                "max_raw_l2_age_ms": max(leg_ages) if leg_ages else "",
                "first_leg_raw_l2_age_ms": next((leg.get("raw_l2_age_ms") for leg in legs if leg.get("leg_role") == "first"), ""),
                "completion_leg_raw_l2_age_ms": next(
                    (leg.get("raw_l2_age_ms") for leg in legs if leg.get("leg_role") == "completion"), ""
                ),
                "l1_top_pair_pass": row["l1_top_pair_pass"],
                "top1_depth_pair_fillable": row["top1_depth_pair_fillable"],
                "top5_depth_pair_fillable": row["top5_depth_pair_fillable"],
                "top5_pair_vwap_cost": row["top5_pair_vwap_cost"],
                "top5_pair_worst_cost": row["top5_pair_worst_cost"],
                "l2_top_aligned_fail_reason": row["l2_top_aligned_fail_reason"],
            }
        )

    max_ages = [as_float(row["max_raw_l2_age_ms"]) for row in audit_rows if row["max_raw_l2_age_ms"] != ""]
    stale_rows = [row for row in audit_rows if row["raw_l2_age_ok_pair"] != "true"]
    overlay_rows = [row for row in audit_rows if row["top_overlay_required"] == "true"]
    category_counts = group_counts(audit_rows, "dependency_category")
    summary = {
        "status": STATUS,
        "strategy_input": str(strategy_path),
        "source_bridge_summary": str(source_bridge_path),
        "candidate_count": len(audit_rows),
        "market_count": len({row["condition_id"] for row in audit_rows}),
        "top_overlay_required_count": len(overlay_rows),
        "raw_l2_age_ok_pair_count": sum(1 for row in audit_rows if row["raw_l2_age_ok_pair"] == "true"),
        "raw_l2_stale_pair_count": len(stale_rows),
        "dependency_category_counts": category_counts,
        "max_raw_l2_age_ms_p50": quantile(max_ages, 0.50),
        "max_raw_l2_age_ms_p95": quantile(max_ages, 0.95),
        "max_raw_l2_age_ms_max": max(max_ages) if max_ages else None,
        "stale_pair_day_counts": group_counts(stale_rows, "day"),
        "overlay_day_counts": group_counts(overlay_rows, "day"),
        "stale_pair_market_count": len({row["condition_id"] for row in stale_rows}),
        "overlay_market_count": len({row["condition_id"] for row in overlay_rows}),
        "all_l1_top_pair_pass": all(row["l1_top_pair_pass"] == "true" for row in audit_rows),
        "all_top1_depth_pair_fillable": all(row["top1_depth_pair_fillable"] == "true" for row in audit_rows),
        "all_l2_fail_reason_pass": all(row["l2_top_aligned_fail_reason"] == "PASS" for row in audit_rows),
        "source_bridge_status": source_bridge.get("status"),
        "interpretation": (
            "Source bridge is clean, but overlay/freshness dependencies remain review-required. "
            "All rows still pass via canonical L1 top1 depth and pair cap; this is not OOS/live/private truth."
        ),
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "canary_authorized": False,
            "orders_authorized": False,
        },
    }

    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_path = output_dir / "ce25_target_qty8_overlay_freshness_action_audit.csv"
    summary_path = output_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_SUMMARY.json"
    note_path = output_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_NOTE.md"
    manifest_path = output_dir / "CE25_TARGET_QTY8_OVERLAY_FRESHNESS_ATTRIBUTION_HASH_MANIFEST.json"

    write_csv(
        audit_path,
        audit_rows,
        [
            "candidate_id",
            "source_candidate_id",
            "condition_id",
            "slug",
            "day",
            "paired_qty",
            "buy_actual_est",
            "cash_pnl_est",
            "top_overlay_required",
            "raw_l2_age_ok_pair",
            "dependency_category",
            "max_raw_l2_age_ms",
            "first_leg_raw_l2_age_ms",
            "completion_leg_raw_l2_age_ms",
            "l1_top_pair_pass",
            "top1_depth_pair_fillable",
            "top5_depth_pair_fillable",
            "top5_pair_vwap_cost",
            "top5_pair_worst_cost",
            "l2_top_aligned_fail_reason",
        ],
    )
    write_json(summary_path, summary)
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Overlay/Freshness Attribution",
                "",
                f"Status: `{STATUS}`",
                "",
                f"- candidates: {summary['candidate_count']}",
                f"- markets: {summary['market_count']}",
                f"- top overlay rows: {summary['top_overlay_required_count']}",
                f"- raw L2 stale pairs: {summary['raw_l2_stale_pair_count']}",
                f"- dependency categories: {summary['dependency_category_counts']}",
                "",
                "All rows still pass canonical L1 top/depth and pair-cap checks. This remains review-only local replay evidence.",
                "",
            ]
        )
    )
    artifacts = [audit_path, summary_path, note_path, strategy_path, source_bridge_path, ledger_path, leg_path, l2_manifest_path]
    manifest = {
        "schema_version": 1,
        "created_at": read_json(l2_manifest_path).get("created_at") or datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "summary_sha256": sha256_file(summary_path),
        "audit_csv_sha256": sha256_file(audit_path),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "non_claims": summary["non_claims"],
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(output_dir),
                "candidate_count": summary["candidate_count"],
                "dependency_category_counts": summary["dependency_category_counts"],
                "manifest_sha256": sha256_file(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

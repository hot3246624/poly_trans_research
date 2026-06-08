#!/usr/bin/env python3
"""Build CE25 broad participation frontier report.

This moves research beyond the narrow target_qty8 branch by measuring public
profile coverage and profitability for high-participation asset/timeframe
controllers over the current CE25 rolling windows.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
ROLLING_ROOT = EXPORTS / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt"
LATEST_PROFILE = EXPORTS / "profile_ce25_latest_24h_20260603_1145_to_20260604_1145_bjt" / "ce25_market_sequence.csv"
PARTICIPATION_REPORT = EXPORTS / "ce25_participation_coverage_report_20260604" / "CE25_PARTICIPATION_COVERAGE_SUMMARY.json"
STRICT_FEE_REPORT = (
    EXPORTS
    / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
    / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json"
)
OUTPUT_DIR = EXPORTS / "ce25_broad_participation_frontier_20260604"

STATUS = "KEEP_CE25_BROAD_PARTICIPATION_FRONTIER_REVIEW_REQUIRED_NOT_OOS_READY"
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
EXPECTED_PER_24H = {"5m": 288, "15m": 96}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def fnum(value: str | None) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except ValueError:
        return 0.0


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def profile_paths() -> list[Path]:
    paths = sorted(ROLLING_ROOT.glob("ce25_*_bjt/ce25_market_sequence.csv"))
    if LATEST_PROFILE.exists():
        paths.append(LATEST_PROFILE)
    return paths


def label_for(path: Path) -> str:
    name = path.parent.name
    return name.removeprefix("ce25_") if name.startswith("ce25_") else name


def group_defs() -> list[dict[str, Any]]:
    groups: list[dict[str, Any]] = []
    for asset in ASSETS:
        for tf in ["5m", "15m"]:
            groups.append(
                {
                    "group_id": f"{asset}_{tf}",
                    "label": f"{asset} {tf}",
                    "expected_per_window": EXPECTED_PER_24H[tf],
                    "predicate": lambda row, asset=asset, tf=tf: row.get("asset") == asset and row.get("tf") == tf,
                }
            )
    for tf in ["5m", "15m"]:
        groups.append(
            {
                "group_id": f"ALL_CRYPTO_{tf}",
                "label": f"BTC/ETH/SOL/XRP {tf}",
                "expected_per_window": len(ASSETS) * EXPECTED_PER_24H[tf],
                "predicate": lambda row, tf=tf: row.get("asset") in ASSETS and row.get("tf") == tf,
            }
        )
    groups.append(
        {
            "group_id": "ALL_CRYPTO_5M_15M",
            "label": "BTC/ETH/SOL/XRP 5m+15m",
            "expected_per_window": len(ASSETS) * (EXPECTED_PER_24H["5m"] + EXPECTED_PER_24H["15m"]),
            "predicate": lambda row: row.get("asset") in ASSETS and row.get("tf") in {"5m", "15m"},
        }
    )
    return groups


def summarize_rows(rows: list[dict[str, str]]) -> dict[str, float]:
    buy_actual = sum(fnum(row.get("buy_actual")) for row in rows)
    cash_pnl = sum(fnum(row.get("cash_pnl")) for row in rows)
    paired_qty = sum(fnum(row.get("paired_qty")) for row in rows)
    buy_qty = sum(fnum(row.get("buy_qty")) for row in rows)
    resid_qty = sum(fnum(row.get("resid_qty")) for row in rows)
    pair_cost_weighted = (
        sum(fnum(row.get("pair_cost")) * fnum(row.get("paired_qty")) for row in rows) / paired_qty
        if paired_qty > 0
        else 0.0
    )
    fee = sum(fnum(row.get("fee")) for row in rows)
    return {
        "market_count": float(len(rows)),
        "buy_actual": buy_actual,
        "cash_pnl": cash_pnl,
        "roi": cash_pnl / buy_actual if buy_actual > 0 else 0.0,
        "fee": fee,
        "fee_rate_on_gross_proxy": fee / max(buy_actual - fee, 1e-12),
        "paired_qty": paired_qty,
        "buy_qty": buy_qty,
        "resid_qty": resid_qty,
        "resid_rate": resid_qty / buy_qty if buy_qty > 0 else 0.0,
        "pair_cost_weighted": pair_cost_weighted,
    }


def summarize_group(group: dict[str, Any], profiles: list[Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predicate: Callable[[dict[str, str]], bool] = group["predicate"]
    rows_by_window: list[dict[str, Any]] = []
    totals = defaultdict(float)
    coverage_rates: list[float] = []
    roi_rates: list[float] = []
    recent_rows: list[dict[str, Any]] = []

    for profile in profiles:
        all_rows = read_csv(profile)
        rows = [row for row in all_rows if predicate(row)]
        stats = summarize_rows(rows)
        expected = group["expected_per_window"]
        coverage = len(rows) / expected if expected else 0.0
        window = {
            "group_id": group["group_id"],
            "window_label": label_for(profile),
            "profile_path": str(profile),
            "expected_market_count": expected,
            "coverage_rate": coverage,
            **stats,
        }
        rows_by_window.append(window)
        coverage_rates.append(coverage)
        roi_rates.append(stats["roi"])
        for key in ["market_count", "buy_actual", "cash_pnl", "fee", "paired_qty", "buy_qty", "resid_qty"]:
            totals[key] += stats[key]

    recent_rows = rows_by_window[-3:]
    recent_buy = sum(row["buy_actual"] for row in recent_rows)
    recent_pnl = sum(row["cash_pnl"] for row in recent_rows)
    recent_expected = group["expected_per_window"] * len(recent_rows)
    recent_market_count = sum(row["market_count"] for row in recent_rows)
    all_buy = totals["buy_actual"]
    all_pnl = totals["cash_pnl"]
    all_expected = group["expected_per_window"] * len(rows_by_window)
    all_market_count = totals["market_count"]
    score = (
        (recent_market_count / recent_expected if recent_expected else 0.0) * 2.0
        + (recent_pnl / recent_buy if recent_buy else 0.0) * 10.0
        + (sum(1 for row in recent_rows if row["cash_pnl"] > 0) / max(len(recent_rows), 1))
        - (sum(row["resid_qty"] for row in recent_rows) / max(sum(row["buy_qty"] for row in recent_rows), 1e-12))
    )
    aggregate = {
        "group_id": group["group_id"],
        "label": group["label"],
        "window_count": len(rows_by_window),
        "expected_per_window": group["expected_per_window"],
        "market_count": int(all_market_count),
        "expected_market_count": all_expected,
        "coverage_rate": round(all_market_count / all_expected, 8) if all_expected else 0.0,
        "coverage_rate_min": round(min(coverage_rates), 8) if coverage_rates else 0.0,
        "coverage_rate_median": round(median(coverage_rates), 8) if coverage_rates else 0.0,
        "coverage_rate_latest": round(coverage_rates[-1], 8) if coverage_rates else 0.0,
        "buy_actual": round(all_buy, 6),
        "cash_pnl": round(all_pnl, 6),
        "roi": round(all_pnl / all_buy, 8) if all_buy else 0.0,
        "win_windows": sum(1 for row in rows_by_window if row["cash_pnl"] > 0),
        "loss_windows": sum(1 for row in rows_by_window if row["cash_pnl"] <= 0),
        "resid_rate": round(totals["resid_qty"] / totals["buy_qty"], 8) if totals["buy_qty"] else 0.0,
        "pair_cost_weighted": round(
            sum(row["pair_cost_weighted"] * row["paired_qty"] for row in rows_by_window)
            / max(sum(row["paired_qty"] for row in rows_by_window), 1e-12),
            8,
        ),
        "recent3_market_count": int(recent_market_count),
        "recent3_expected_market_count": recent_expected,
        "recent3_coverage_rate": round(recent_market_count / recent_expected, 8) if recent_expected else 0.0,
        "recent3_cash_pnl": round(recent_pnl, 6),
        "recent3_roi": round(recent_pnl / recent_buy, 8) if recent_buy else 0.0,
        "recent3_win_windows": sum(1 for row in recent_rows if row["cash_pnl"] > 0),
        "recent3_resid_rate": round(
            sum(row["resid_qty"] for row in recent_rows) / max(sum(row["buy_qty"] for row in recent_rows), 1e-12),
            8,
        ),
        "frontier_score": round(score, 8),
    }
    return aggregate, rows_by_window


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    profiles = profile_paths()
    aggregates: list[dict[str, Any]] = []
    window_rows: list[dict[str, Any]] = []
    for group in group_defs():
        agg, rows = summarize_group(group, profiles)
        aggregates.append(agg)
        window_rows.extend(rows)
    aggregates.sort(key=lambda row: row["frontier_score"], reverse=True)

    frontier_path = OUTPUT_DIR / "ce25_broad_participation_frontier.tsv"
    frontier_fields = list(aggregates[0].keys())
    with frontier_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=frontier_fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(aggregates)

    window_path = OUTPUT_DIR / "ce25_broad_participation_by_window.tsv"
    window_fields = [
        "group_id",
        "window_label",
        "expected_market_count",
        "market_count",
        "coverage_rate",
        "buy_actual",
        "cash_pnl",
        "roi",
        "resid_rate",
        "pair_cost_weighted",
        "profile_path",
    ]
    with window_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=window_fields, delimiter="\t")
        writer.writeheader()
        for row in window_rows:
            writer.writerow({key: row[key] for key in window_fields})

    strict = read_json(STRICT_FEE_REPORT)
    participation = read_json(PARTICIPATION_REPORT)
    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "profile_window_count": len(profiles),
        "top_frontier_groups": aggregates[:5],
        "recommended_mainline": {
            "group_id": "BTC_5m",
            "reason": "best balance of high recent participation, 6/7 profitable windows, and positive aggregate ROI; closest to user's high-participation goal.",
            "caveat": "public account profile only; needs ex-ante controller and OOS no-order observation before any execution claim.",
        },
        "secondary_lane": {
            "group_id": "ALL_CRYPTO_5m",
            "reason": "broader participation and 6/7 profitable windows, but weaker latest ROI and more cross-asset complexity.",
        },
        "deprioritized_lane": {
            "group_id": "BTC_15m",
            "reason": "coverage is high but 7-window profitability is unstable; 3/7 profitable only.",
        },
        "strict_target_qty8_contrast": {
            "participation_rate": strict["participation_rate_by_round"],
            "official_crypto_fee_cash_pnl": strict["cash_pnl_official_fee_0p07"],
            "official_crypto_fee_capital_300_roi": strict["capital_300_roi_official_fee_0p07"],
            "interpretation": "safe seed/filter only; too narrow for main strategy.",
        },
        "latest_ce25_coverage_context": participation["latest_window"],
        "next_research_steps": [
            "Build BTC_5m broad controller candidate ledger from profile rows.",
            "Split pair-cost/residual controller from participation controller.",
            "Stress official crypto feeRate=0.07 and capacity ladders.",
            "Prepare no-order OOS prep only after ex-ante features replace outcome-only public profile fields.",
        ],
        "non_claims": non_claims(),
    }
    summary_path = OUTPUT_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    note_path = OUTPUT_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Broad Participation Frontier",
                "",
                f"Status: `{STATUS}`",
                "",
                "The current target_qty8 branch is too narrow. This report treats CE25 broad public profile coverage as the next research target.",
                "",
                "BTC 5m is the recommended mainline because it is closest to the high-participation goal while staying more profitable than broad 15m lanes over the available rolling windows.",
                "",
                "This remains public-only/review-only. Pair delay, residual side, and realized cash PnL are profile outcomes, not fully ex-ante execution signals.",
                "",
            ]
        )
    )

    artifacts = [summary_path, frontier_path, window_path, note_path, Path(__file__).resolve(), PARTICIPATION_REPORT, STRICT_FEE_REPORT]
    artifacts.extend(profiles)
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "summary_sha256": sha256_file(summary_path),
        "frontier_tsv_sha256": sha256_file(frontier_path),
        "window_tsv_sha256": sha256_file(window_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
                "top_group": aggregates[0]["group_id"],
                "recommended_mainline": "BTC_5m",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

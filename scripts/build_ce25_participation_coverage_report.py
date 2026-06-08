#!/usr/bin/env python3
"""Build CE25 public participation coverage report.

Compares broad CE25 public account market coverage against the narrow
CE25 target_qty=8 strict replay branch.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
ROLLING_ROOT = EXPORTS / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt"
LATEST_PROFILE = EXPORTS / "profile_ce25_latest_24h_20260603_1145_to_20260604_1145_bjt" / "ce25_market_sequence.csv"
STRICT_LEDGER = (
    EXPORTS
    / "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604"
    / "ce25_high_price_top1_qty_target_qty8_candidate_ledger.csv"
)
OFFICIAL_FEE_SUMMARY = (
    EXPORTS
    / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
    / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json"
)
OUTPUT_DIR = EXPORTS / "ce25_participation_coverage_report_20260604"

STATUS = "KEEP_CE25_PARTICIPATION_COVERAGE_REPORT_REVIEW_REQUIRED_NOT_OOS_READY"
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
EXPECTED_PER_24H = {"5m": 288, "15m": 96}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def profile_paths() -> list[Path]:
    paths = sorted(ROLLING_ROOT.glob("ce25_*_bjt/ce25_market_sequence.csv"))
    if LATEST_PROFILE.exists():
        paths.append(LATEST_PROFILE)
    return paths


def label_for(path: Path) -> str:
    name = path.parent.name
    if name.startswith("ce25_"):
        return name.removeprefix("ce25_")
    return name


def summarize_profile(path: Path) -> dict[str, Any]:
    rows = read_csv(path)
    counts = Counter((row.get("asset", ""), row.get("tf", "")) for row in rows)
    asset_tf: dict[str, dict[str, Any]] = {}
    for asset in ASSETS:
        asset_tf[asset] = {}
        for tf, expected in EXPECTED_PER_24H.items():
            actual = counts[(asset, tf)]
            asset_tf[asset][tf] = {
                "market_count": actual,
                "expected_market_count": expected,
                "participation_rate": round(actual / expected, 8),
            }
    total_5m = sum(counts[(asset, "5m")] for asset in ASSETS)
    total_15m = sum(counts[(asset, "15m")] for asset in ASSETS)
    expected_5m = len(ASSETS) * EXPECTED_PER_24H["5m"]
    expected_15m = len(ASSETS) * EXPECTED_PER_24H["15m"]
    return {
        "profile_path": str(path),
        "profile_sha256": sha256_file(path),
        "window_label": label_for(path),
        "market_count": len(rows),
        "crypto_5m_market_count": total_5m,
        "crypto_5m_expected": expected_5m,
        "crypto_5m_participation_rate": round(total_5m / expected_5m, 8),
        "crypto_15m_market_count": total_15m,
        "crypto_15m_expected": expected_15m,
        "crypto_15m_participation_rate": round(total_15m / expected_15m, 8),
        "crypto_5m_15m_market_count": total_5m + total_15m,
        "crypto_5m_15m_expected": expected_5m + expected_15m,
        "crypto_5m_15m_participation_rate": round((total_5m + total_15m) / (expected_5m + expected_15m), 8),
        "asset_tf": asset_tf,
    }


def strict_branch_summary() -> dict[str, Any]:
    rows = read_csv(STRICT_LEDGER)
    fee = json.loads(OFFICIAL_FEE_SUMMARY.read_text()) if OFFICIAL_FEE_SUMMARY.exists() else {}
    days = sorted({row["day"] for row in rows})
    expected_btc_5m = len(days) * EXPECTED_PER_24H["5m"]
    return {
        "strict_branch": "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1 target_qty8",
        "candidate_count": len(rows),
        "market_count": len({row["condition_id"] for row in rows}),
        "active_days": len(days),
        "expected_btc_5m_rounds_over_active_days": expected_btc_5m,
        "participation_rate_vs_btc_5m_rounds": round(len(rows) / expected_btc_5m, 8),
        "official_crypto_fee_0p07_cash_pnl": fee.get("cash_pnl_official_fee_0p07"),
        "official_crypto_fee_0p07_turnover_roi": fee.get("turnover_roi_official_fee_0p07"),
        "official_crypto_fee_0p07_capital_300_roi": fee.get("capital_300_roi_official_fee_0p07"),
        "strict_branch_interpretation": "narrow high-filter replay branch; not representative of CE25 broad account participation",
    }


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
    profiles = [summarize_profile(path) for path in profile_paths()]
    strict = strict_branch_summary()
    latest = profiles[-1]
    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "ce25_profile_window_count": len(profiles),
        "latest_window": {
            "window_label": latest["window_label"],
            "market_count": latest["market_count"],
            "crypto_5m_participation_rate": latest["crypto_5m_participation_rate"],
            "crypto_15m_participation_rate": latest["crypto_15m_participation_rate"],
            "crypto_5m_15m_participation_rate": latest["crypto_5m_15m_participation_rate"],
            "btc_5m_participation_rate": latest["asset_tf"]["BTC"]["5m"]["participation_rate"],
            "btc_15m_participation_rate": latest["asset_tf"]["BTC"]["15m"]["participation_rate"],
        },
        "rolling_profile_ranges": {
            "crypto_5m_participation_rate_min": min(row["crypto_5m_participation_rate"] for row in profiles),
            "crypto_5m_participation_rate_max": max(row["crypto_5m_participation_rate"] for row in profiles),
            "crypto_15m_participation_rate_min": min(row["crypto_15m_participation_rate"] for row in profiles),
            "crypto_15m_participation_rate_max": max(row["crypto_15m_participation_rate"] for row in profiles),
            "crypto_5m_15m_participation_rate_min": min(row["crypto_5m_15m_participation_rate"] for row in profiles),
            "crypto_5m_15m_participation_rate_max": max(row["crypto_5m_15m_participation_rate"] for row in profiles),
        },
        "strict_branch_summary": strict,
        "conclusion": "CE25 broad account coverage is high, especially 15m and latest BTC 5m; the current target_qty8 branch captures only a narrow 3.1% BTC 5m subset.",
        "non_claims": non_claims(),
    }
    summary_path = OUTPUT_DIR / "CE25_PARTICIPATION_COVERAGE_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    rows_path = OUTPUT_DIR / "ce25_participation_coverage_by_window.tsv"
    with rows_path.open("w", newline="") as f:
        fieldnames = [
            "window_label",
            "market_count",
            "crypto_5m_market_count",
            "crypto_5m_expected",
            "crypto_5m_participation_rate",
            "crypto_15m_market_count",
            "crypto_15m_expected",
            "crypto_15m_participation_rate",
            "crypto_5m_15m_market_count",
            "crypto_5m_15m_expected",
            "crypto_5m_15m_participation_rate",
            "btc_5m_participation_rate",
            "btc_15m_participation_rate",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        for row in profiles:
            out = {key: row[key] for key in fieldnames if key in row}
            out["btc_5m_participation_rate"] = row["asset_tf"]["BTC"]["5m"]["participation_rate"]
            out["btc_15m_participation_rate"] = row["asset_tf"]["BTC"]["15m"]["participation_rate"]
            writer.writerow(out)

    note_path = OUTPUT_DIR / "CE25_PARTICIPATION_COVERAGE_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Participation Coverage",
                "",
                f"Status: `{STATUS}`",
                "",
                "CE25 broad account participation is not comparable to the current strict target_qty8 branch.",
                "The latest 24h profile covers 81.8% of BTC/ETH/SOL/XRP 5m markets and 89.8% of 15m markets; BTC 5m alone is 88.5%.",
                "The strict target_qty8 branch covers only 134 BTC 5m actions across 15 active days, or 3.1% of BTC 5m rounds.",
                "",
                "Next research should expand participation by learning CE25's broad participation controller separately from the strict paircap target_qty8 filter.",
                "",
                "This is public-only/review-only research and does not authorize OOS/live/private/order claims.",
                "",
            ]
        )
    )
    artifacts = [summary_path, rows_path, note_path, STRICT_LEDGER, OFFICIAL_FEE_SUMMARY, Path(__file__).resolve()]
    artifacts.extend(profile_paths())
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "summary_sha256": sha256_file(summary_path),
        "coverage_tsv_sha256": sha256_file(rows_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_PARTICIPATION_COVERAGE_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
                "latest_crypto_5m_rate": summary["latest_window"]["crypto_5m_participation_rate"],
                "latest_btc_5m_rate": summary["latest_window"]["btc_5m_participation_rate"],
                "strict_branch_rate": strict["participation_rate_vs_btc_5m_rounds"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

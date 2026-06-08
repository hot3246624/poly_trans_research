#!/usr/bin/env python3
"""Probe CE25 BTC 5m broad ledger for ex-ante feature candidates."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
LEDGER_DIR = EXPORTS / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
LEDGER = LEDGER_DIR / "ce25_btc5m_broad_profile_candidate_ledger.csv"
FIELD_CLASSIFICATION = LEDGER_DIR / "CE25_BTC5M_BROAD_FIELD_CLASSIFICATION.json"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_exante_feature_probe_20260604"

STATUS = "KEEP_CE25_BTC5M_EXANTE_FEATURE_PROBE_REVIEW_REQUIRED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1"


GROUP_SPECS = [
    ("baseline", ["asset", "timeframe"], "EX_ANTE_STATIC"),
    ("source_first_delta_bucket", ["source_first_delta_bucket"], "NEEDS_CONTROLLER_REWRITE"),
    ("source_last_delta_bucket", ["source_last_delta_bucket"], "NEEDS_CLOCK_RULE_REWRITE"),
    ("source_first_price_bucket", ["source_first_price_bucket"], "OBSERVABLE_AT_DECISION_TIME_IF_SCHEDULE_DEFINED"),
    ("source_first_side", ["source_first_side"], "OBSERVABLE_AT_DECISION_TIME_IF_SIDE_RULE_DEFINED"),
    ("first_price_x_side", ["source_first_price_bucket", "source_first_side"], "OBSERVABLE_AT_DECISION_TIME_IF_SCHEDULE_AND_SIDE_RULE_DEFINED"),
    ("last_delta_x_first_price", ["source_last_delta_bucket", "source_first_price_bucket"], "NEEDS_FIXED_CLOCK_AND_BOOK_RULE_REWRITE"),
]


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


def read_rows() -> list[dict[str, str]]:
    with LEDGER.open(newline="") as f:
        return list(csv.DictReader(f))


def summarize(rows: list[dict[str, str]]) -> dict[str, float]:
    buy = sum(fnum(row["source_buy_actual"]) for row in rows)
    pnl = sum(fnum(row["source_cash_pnl"]) for row in rows)
    paired = sum(fnum(row["source_paired_qty"]) for row in rows)
    buy_qty = sum(fnum(row["source_yes_qty"]) + fnum(row["source_no_qty"]) for row in rows)
    resid = sum(fnum(row["source_resid_qty"]) for row in rows)
    pc = sum(fnum(row["source_pair_cost"]) * fnum(row["source_paired_qty"]) for row in rows) / paired if paired else 0.0
    return {
        "market_count": float(len(rows)),
        "buy_actual": buy,
        "cash_pnl": pnl,
        "roi": pnl / buy if buy else 0.0,
        "pair_cost_weighted": pc,
        "resid_rate": resid / buy_qty if buy_qty else 0.0,
        "win_market_count": float(sum(1 for row in rows if fnum(row["source_cash_pnl"]) > 0)),
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
    rows = read_rows()
    probe_rows: list[dict[str, Any]] = []
    for group_id, cols, feature_status in GROUP_SPECS:
        buckets: dict[tuple[str, ...], list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            buckets[tuple(row[col] for col in cols)].append(row)
        for key, bucket_rows in buckets.items():
            stats = summarize(bucket_rows)
            if stats["market_count"] < 20:
                continue
            probe_rows.append(
                {
                    "group_id": group_id,
                    "bucket_key": "|".join(key),
                    "columns": ",".join(cols),
                    "feature_status": feature_status,
                    "market_count": int(stats["market_count"]),
                    "buy_actual": round(stats["buy_actual"], 6),
                    "cash_pnl": round(stats["cash_pnl"], 6),
                    "roi": round(stats["roi"], 8),
                    "pair_cost_weighted": round(stats["pair_cost_weighted"], 8),
                    "resid_rate": round(stats["resid_rate"], 8),
                    "win_market_count": int(stats["win_market_count"]),
                    "score": round(stats["roi"] * 10.0 + (stats["market_count"] / len(rows)) - stats["resid_rate"], 8),
                }
            )
    probe_rows.sort(key=lambda row: row["score"], reverse=True)

    probe_path = OUTPUT_DIR / "ce25_btc5m_exante_feature_probe.tsv"
    with probe_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(probe_rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(probe_rows)

    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": STRATEGY_ID,
        "source_ledger_sha256": sha256_file(LEDGER),
        "candidate_count": len(rows),
        "top_probe_rows": probe_rows[:12],
        "recommended_controller_hypotheses": [
            {
                "hypothesis_id": "BTC5M_BROAD_BASELINE",
                "rule_shape": "participate in BTC 5m markets with scheduled public-book observations",
                "reason": "highest coverage baseline; 1071 markets, 6/7 profile windows profitable in frontier report",
            },
            {
                "hypothesis_id": "BTC5M_20_35_ALPHA_SEED",
                "rule_shape": "book-observed price bucket 20-35 at reviewed clock observations",
                "reason": "strong pair-cost and ROI, but lower coverage; should be a sub-controller, not full participation controller",
            },
            {
                "hypothesis_id": "BTC5M_65_80_RISK_SEED",
                "rule_shape": "book-observed price bucket 65-80 with pair-cost/residual guard",
                "reason": "strong risk-control seed; compatible with prior strict branch but must be widened",
            },
            {
                "hypothesis_id": "BTC5M_LAST60_CLOCK_REWRITE",
                "rule_shape": "fixed last-60s observation schedule, not CE25 source_last_delta reuse",
                "reason": "high PnL in profile labels, but current field is outcome timing and must be rewritten",
            },
        ],
        "fail_closed_rules": [
            "using_source_first_trade_time_as_live_signal",
            "using_source_pair_delay_or_source_cash_pnl_as_entry_signal",
            "using_private_truth_or_order_path",
            "claiming_oos_ready_from_public_profile_probe",
        ],
        "non_claims": non_claims(),
    }
    summary_path = OUTPUT_DIR / "CE25_BTC5M_EXANTE_FEATURE_PROBE_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    note_path = OUTPUT_DIR / "CE25_BTC5M_EXANTE_FEATURE_PROBE_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Ex-Ante Feature Probe",
                "",
                f"Status: `{STATUS}`",
                "",
                "This probe ranks public-profile feature buckets but does not authorize OOS/live execution.",
                "Any source timing or outcome field must be rewritten into a deterministic clock/book observation rule before OOS.",
                "",
            ]
        )
    )
    artifacts = [probe_path, summary_path, note_path, LEDGER, FIELD_CLASSIFICATION, Path(__file__).resolve()]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "summary_sha256": sha256_file(summary_path),
        "probe_tsv_sha256": sha256_file(probe_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_EXANTE_FEATURE_PROBE_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
                "top_bucket": probe_rows[0]["bucket_key"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

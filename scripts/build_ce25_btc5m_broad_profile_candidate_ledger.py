#!/usr/bin/env python3
"""Build CE25 BTC 5m broad profile candidate ledger.

The ledger converts CE25 public-profile BTC 5m market rows into a research
candidate table and explicitly separates ex-ante/static fields from outcome
labels. It remains public-only and review-only.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
ROLLING_ROOT = EXPORTS / "rolling_profiles_ce25_nagi_20260528_1145_to_20260604_1145_bjt"
LATEST_PROFILE = EXPORTS / "profile_ce25_latest_24h_20260603_1145_to_20260604_1145_bjt" / "ce25_market_sequence.csv"
FRONTIER_SUMMARY = EXPORTS / "ce25_broad_participation_frontier_20260604" / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json"
CONTROLLER_PACKET = EXPORTS / "ce25_broad_controller_research_packet_20260604" / "CE25_BROAD_CONTROLLER_RESEARCH_PACKET.json"
DEFAULT_OUTPUT_DIR = EXPORTS / "ce25_btc5m_broad_profile_candidate_ledger_20260604"

STATUS = "KEEP_CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_PREPARED_REVIEW_ONLY_NOT_OOS_READY"
OWNER_LINE = "CE25_BROAD_RESEARCH"
STRATEGY_ID = "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1"
STRATEGY_FAMILY = "ce25_broad_participation_controller_public_profile_v1"
EXPECTED_BTC5M_ROUNDS_PER_24H = 288


LEDGER_COLUMNS = [
    "strategy_owner_line",
    "strategy_id",
    "strategy_family",
    "candidate_id",
    "source_profile_label",
    "source_profile_path",
    "source_profile_sha256",
    "source_condition_id",
    "slug",
    "title",
    "asset",
    "timeframe",
    "market_start_s",
    "market_end_s",
    "source_first_trade_s",
    "source_last_trade_s",
    "source_first_delta_s",
    "source_last_delta_s",
    "source_first_delta_bucket",
    "source_last_delta_bucket",
    "source_pair_delay_s",
    "source_pair_delay_bucket",
    "source_first_price",
    "source_first_price_bucket",
    "source_first_side",
    "source_resid_side",
    "source_trade_count",
    "source_buy_actual",
    "source_cash_pnl",
    "source_roi",
    "source_fee",
    "source_pair_cost",
    "source_paired_qty",
    "source_resid_qty",
    "source_resid_rate",
    "source_pair_pnl",
    "source_residual_pnl_est",
    "source_yes_qty",
    "source_no_qty",
    "private_truth_ready",
    "strategy_promotion_ready",
    "live_ready",
    "deployable",
]


FIELD_CLASSIFICATION = {
    "EX_ANTE_STATIC": [
        "asset",
        "timeframe",
        "slug",
        "title",
        "market_start_s",
        "market_end_s",
    ],
    "OBSERVABLE_AT_OUR_DECISION_TIME_IF_CONTROLLER_DEFINES_TIME": [
        "book_price",
        "book_depth",
        "spread",
        "clock_seconds_to_close",
    ],
    "NEEDS_CONTROLLER_REWRITE": [
        "source_first_trade_s",
        "source_last_trade_s",
        "source_first_delta_s",
        "source_last_delta_s",
        "source_first_delta_bucket",
        "source_last_delta_bucket",
        "source_first_price",
        "source_first_price_bucket",
    ],
    "OUTCOME_LABEL": [
        "source_pair_delay_s",
        "source_pair_delay_bucket",
        "source_first_side",
        "source_resid_side",
        "source_trade_count",
        "source_buy_actual",
        "source_cash_pnl",
        "source_roi",
        "source_fee",
        "source_pair_cost",
        "source_paired_qty",
        "source_resid_qty",
        "source_resid_rate",
        "source_pair_pnl",
        "source_residual_pnl_est",
        "source_yes_qty",
        "source_no_qty",
    ],
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


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


def fnum(value: str | None) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except ValueError:
        return 0.0


def candidate_id(condition_id: str) -> str:
    digest = hashlib.sha256(f"{STRATEGY_ID}|{condition_id}".encode()).hexdigest()[:24]
    return f"ce25_btc5m_broad_{digest}"


def market_start_from_slug(slug: str) -> int:
    match = re.search(r"-(\d{10})$", slug)
    if not match:
        raise ValueError(f"cannot parse market start from slug: {slug}")
    return int(match.group(1))


def bool_false() -> str:
    return "false"


def build_rows() -> tuple[list[dict[str, Any]], list[Path]]:
    rows_out: list[dict[str, Any]] = []
    profiles = profile_paths()
    seen_conditions: set[str] = set()
    duplicate_conditions: list[str] = []
    for profile in profiles:
        source_sha = sha256_file(profile)
        label = label_for(profile)
        for src in read_csv(profile):
            if src.get("asset") != "BTC" or src.get("tf") != "5m":
                continue
            condition_id = src["condition_id"]
            if condition_id in seen_conditions:
                duplicate_conditions.append(condition_id)
            seen_conditions.add(condition_id)
            start_s = market_start_from_slug(src["slug"])
            buy_actual = fnum(src.get("buy_actual"))
            cash_pnl = fnum(src.get("cash_pnl"))
            out = {
                "strategy_owner_line": OWNER_LINE,
                "strategy_id": STRATEGY_ID,
                "strategy_family": STRATEGY_FAMILY,
                "candidate_id": candidate_id(condition_id),
                "source_profile_label": label,
                "source_profile_path": str(profile),
                "source_profile_sha256": source_sha,
                "source_condition_id": condition_id,
                "slug": src.get("slug", ""),
                "title": src.get("title", ""),
                "asset": src.get("asset", ""),
                "timeframe": src.get("tf", ""),
                "market_start_s": start_s,
                "market_end_s": start_s + 300,
                "source_first_trade_s": src.get("first_trade_s", ""),
                "source_last_trade_s": src.get("last_trade_s", ""),
                "source_first_delta_s": src.get("first_delta_s", ""),
                "source_last_delta_s": src.get("last_delta_s", ""),
                "source_first_delta_bucket": src.get("first_delta_bucket", ""),
                "source_last_delta_bucket": src.get("last_delta_bucket", ""),
                "source_pair_delay_s": src.get("pair_delay_s", ""),
                "source_pair_delay_bucket": src.get("pair_delay_bucket", ""),
                "source_first_price": src.get("first_price", ""),
                "source_first_price_bucket": src.get("first_price_bucket", ""),
                "source_first_side": src.get("first_side", ""),
                "source_resid_side": src.get("resid_side", ""),
                "source_trade_count": src.get("trade_count", ""),
                "source_buy_actual": src.get("buy_actual", ""),
                "source_cash_pnl": src.get("cash_pnl", ""),
                "source_roi": round(cash_pnl / buy_actual, 8) if buy_actual > 0 else 0.0,
                "source_fee": src.get("fee", ""),
                "source_pair_cost": src.get("pair_cost", ""),
                "source_paired_qty": src.get("paired_qty", ""),
                "source_resid_qty": src.get("resid_qty", ""),
                "source_resid_rate": src.get("resid_rate", ""),
                "source_pair_pnl": src.get("pair_pnl", ""),
                "source_residual_pnl_est": src.get("residual_pnl_est", ""),
                "source_yes_qty": src.get("yes_qty", ""),
                "source_no_qty": src.get("no_qty", ""),
                "private_truth_ready": bool_false(),
                "strategy_promotion_ready": bool_false(),
                "live_ready": bool_false(),
                "deployable": bool_false(),
            }
            rows_out.append(out)
    if duplicate_conditions:
        raise SystemExit(json.dumps({"ok": False, "errors": ["duplicate_condition_ids"], "duplicates": duplicate_conditions[:10]}))
    return rows_out, profiles


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "orders_authorized": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows, profiles = build_rows()
    if not rows:
        raise SystemExit(json.dumps({"ok": False, "errors": ["no_btc5m_rows"]}))

    ledger_path = output_dir / "ce25_btc5m_broad_profile_candidate_ledger.csv"
    with ledger_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=LEDGER_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    by_window: dict[str, dict[str, Any]] = defaultdict(lambda: {"market_count": 0, "buy_actual": 0.0, "cash_pnl": 0.0, "resid_qty": 0.0, "buy_qty": 0.0})
    for row in rows:
        w = by_window[row["source_profile_label"]]
        w["market_count"] += 1
        w["buy_actual"] += fnum(str(row["source_buy_actual"]))
        w["cash_pnl"] += fnum(str(row["source_cash_pnl"]))
        w["resid_qty"] += fnum(str(row["source_resid_qty"]))
        buy_qty = fnum(str(row["source_yes_qty"])) + fnum(str(row["source_no_qty"]))
        w["buy_qty"] += buy_qty

    window_rows = []
    for label, stats in sorted(by_window.items()):
        window_rows.append(
            {
                "window_label": label,
                "market_count": stats["market_count"],
                "expected_btc5m_rounds": EXPECTED_BTC5M_ROUNDS_PER_24H,
                "coverage_rate": round(stats["market_count"] / EXPECTED_BTC5M_ROUNDS_PER_24H, 8),
                "buy_actual": round(stats["buy_actual"], 6),
                "cash_pnl": round(stats["cash_pnl"], 6),
                "roi": round(stats["cash_pnl"] / stats["buy_actual"], 8) if stats["buy_actual"] else 0.0,
                "resid_rate": round(stats["resid_qty"] / stats["buy_qty"], 8) if stats["buy_qty"] else 0.0,
            }
        )
    window_path = output_dir / "ce25_btc5m_broad_profile_window_summary.tsv"
    with window_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(window_rows[0].keys()), delimiter="\t")
        writer.writeheader()
        writer.writerows(window_rows)

    total_buy = sum(fnum(str(row["source_buy_actual"])) for row in rows)
    total_pnl = sum(fnum(str(row["source_cash_pnl"])) for row in rows)
    total_buy_qty = sum(fnum(str(row["source_yes_qty"])) + fnum(str(row["source_no_qty"])) for row in rows)
    total_resid_qty = sum(fnum(str(row["source_resid_qty"])) for row in rows)
    expected_rounds = len(profiles) * EXPECTED_BTC5M_ROUNDS_PER_24H
    frontier = read_json(FRONTIER_SUMMARY)
    packet = read_json(CONTROLLER_PACKET)
    summary = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_owner_line": OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "strategy_family": STRATEGY_FAMILY,
        "candidate_count": len(rows),
        "market_count": len({row["source_condition_id"] for row in rows}),
        "source_profile_count": len(profiles),
        "expected_btc5m_rounds": expected_rounds,
        "coverage_rate": round(len(rows) / expected_rounds, 8),
        "latest_window_coverage_rate": window_rows[-1]["coverage_rate"],
        "buy_actual": round(total_buy, 6),
        "cash_pnl": round(total_pnl, 6),
        "roi": round(total_pnl / total_buy, 8) if total_buy else 0.0,
        "resid_rate": round(total_resid_qty / total_buy_qty, 8) if total_buy_qty else 0.0,
        "field_classification": FIELD_CLASSIFICATION,
        "critical_contract": {
            "public_profile_rows_are_labels_not_private_truth": True,
            "source_first_trade_time_is_not_a_free_ex_ante_signal": True,
            "future_controller_must_define_its_own_observation_schedule": True,
            "outcome_label_fields_must_not_be_used_as_live_entry_conditions": True,
        },
        "next_step": "derive_ex_ante_btc5m_controller_features_from_clock_and_public_book_state",
        "source_hashes": {
            "frontier_summary": sha256_file(FRONTIER_SUMMARY),
            "broad_controller_packet": sha256_file(CONTROLLER_PACKET),
        },
        "frontier_context_status": frontier["status"],
        "controller_packet_status": packet["status"],
        "non_claims": non_claims(),
    }
    summary_path = output_dir / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    field_contract_path = output_dir / "CE25_BTC5M_BROAD_FIELD_CLASSIFICATION.json"
    field_contract_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "REVIEW_ONLY_FIELD_CLASSIFICATION",
                "strategy_id": STRATEGY_ID,
                "field_classification": FIELD_CLASSIFICATION,
                "non_claims": non_claims(),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    command_path = output_dir / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 BTC 5m broad profile ledger is review-only; no OOS/live/runner command is issued.' >&2",
                "exit 66",
                "",
            ]
        )
    )
    command_path.chmod(0o755)

    note_path = output_dir / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Broad Profile Candidate Ledger",
                "",
                f"Status: `{STATUS}`",
                "",
                "This ledger captures CE25 public BTC 5m market participation over the reviewed rolling windows.",
                "It explicitly separates ex-ante/static fields from outcome labels. CE25 first-trade timing, pair delay, residual side, and PnL are labels, not live entry conditions.",
                "",
                "No OOS, runner/observer start, private key, import, order/cancel/redeem, canary/live/deploy/funding, latest pointer update, private truth, promotion, live-ready, or deployable claim is authorized.",
                "",
            ]
        )
    )

    artifacts = [
        ledger_path,
        window_path,
        summary_path,
        field_contract_path,
        command_path,
        note_path,
        FRONTIER_SUMMARY,
        CONTROLLER_PACKET,
        Path(__file__).resolve(),
        *profiles,
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "candidate_ledger_sha256": sha256_file(ledger_path),
        "summary_sha256": sha256_file(summary_path),
        "field_contract_sha256": sha256_file(field_contract_path),
        "non_claims": non_claims(),
    }
    manifest_path = output_dir / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(output_dir),
                "candidate_count": len(rows),
                "coverage_rate": summary["coverage_rate"],
                "latest_window_coverage_rate": summary["latest_window_coverage_rate"],
                "summary_sha256": sha256_file(summary_path),
                "manifest_sha256": sha256_file(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

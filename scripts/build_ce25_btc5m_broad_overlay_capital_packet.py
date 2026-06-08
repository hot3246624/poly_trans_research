#!/usr/bin/env python3
"""Build CE25 BTC 5m broad+overlay capital research packet.

This packet keeps the current mainline explicit:

* BTC 5m broad participation is the high-coverage backbone.
* 20-35 / 65-80 / last-60s buckets are overlays, not separate mainlines.
* Public profile cash PnL is fee-inclusive evidence, but exact official-fee
  replay still requires matching fill/source data for 2026-05-28..2026-06-04.

No replay, OOS, WS, private key, or order path is touched.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
LEDGER_DIR = EXPORTS / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
LEDGER_CSV = LEDGER_DIR / "ce25_btc5m_broad_profile_candidate_ledger.csv"
LEDGER_SUMMARY = LEDGER_DIR / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_SUMMARY.json"
CONTROLLER_PACKET = (
    EXPORTS
    / "ce25_btc5m_controller_v0_review_packet_20260604"
    / "CE25_BTC5M_CONTROLLER_V0_REVIEW_PACKET.json"
)
MATCHING_SOURCE_PACKET = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
)
SOURCE_CROSSWALK = (
    EXPORTS
    / "ce25_btc5m_replay_source_crosswalk_20260604"
    / "CE25_BTC5M_REPLAY_SOURCE_CROSSWALK_SUMMARY.json"
)
OUTPUT_DIR = EXPORTS / "ce25_btc5m_broad_overlay_capital_packet_20260606"

EXPECTED_BTC5M_ROUNDS_PER_PROFILE = 288
INITIAL_BANKROLL = 300.0
OFFICIAL_CRYPTO_FEE_RATE = 0.07
STATUS = "KEEP_CE25_BTC5M_BROAD_OVERLAY_CAPITAL_PACKET_REVIEW_ONLY_MATCHING_SOURCE_REQUIRED_NOT_OOS_READY"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow({key: row.get(key, "") for key in fieldnames})


def fnum(row: dict[str, str], key: str) -> float:
    try:
        value = row.get(key)
        return float(value) if value not in (None, "") else 0.0
    except ValueError:
        return 0.0


def in_bucket(row: dict[str, str], bucket: str) -> bool:
    return row.get("source_first_price_bucket") == bucket


def latest_label(rows: list[dict[str, str]]) -> str:
    labels = sorted({row["source_profile_label"] for row in rows})
    return labels[-1]


@dataclass(frozen=True)
class ControllerSpec:
    controller_id: str
    role: str
    feature_status: str
    predicate: Callable[[dict[str, str]], bool]
    interpretation: str


def specs() -> list[ControllerSpec]:
    return [
        ControllerSpec(
            "BTC5M_BROAD_BASELINE",
            "high_participation_backbone",
            "EX_ANTE_STATIC_PLUS_CONTROLLER_CLOCK_REQUIRED",
            lambda r: True,
            "Use BTC 5m schedule as the coverage backbone; overlays may modulate size/risk.",
        ),
        ControllerSpec(
            "BTC5M_20_35_ALPHA_OVERLAY",
            "alpha_overlay",
            "BOOK_OBSERVABLE_IF_FIXED_CLOCK_DEFINED",
            lambda r: in_bucket(r, "20-35"),
            "Observed low-price bucket can boost size after fixed-clock public-book rewrite.",
        ),
        ControllerSpec(
            "BTC5M_65_80_RISK_OVERLAY",
            "risk_control_overlay",
            "BOOK_OBSERVABLE_IF_FIXED_CLOCK_DEFINED",
            lambda r: in_bucket(r, "65-80"),
            "Observed high-price bucket is a risk/control overlay, not a standalone broad mainline.",
        ),
        ControllerSpec(
            "BTC5M_LAST60_CLOCK_OVERLAY",
            "late_window_overlay",
            "NEEDS_FIXED_CLOCK_REWRITE",
            lambda r: r.get("source_last_delta_bucket") == "last_60s",
            "Profile last-trade timing must be rewritten as our own fixed last-60s clock.",
        ),
        ControllerSpec(
            "BTC5M_LAST60_20_35_ALPHA_OVERLAY",
            "late_low_price_overlay",
            "NEEDS_FIXED_CLOCK_AND_BOOK_RULE_REWRITE",
            lambda r: r.get("source_last_delta_bucket") == "last_60s" and in_bucket(r, "20-35"),
            "High ROI label bucket; usable only after fixed-clock and public-book validation.",
        ),
        ControllerSpec(
            "BTC5M_20_35_DOWN_SIDE_RESEARCH_OVERLAY",
            "side_split_research_overlay",
            "SIDE_RULE_REQUIRED_DO_NOT_USE_SOURCE_SIDE_AS_FREE_SIGNAL",
            lambda r: in_bucket(r, "20-35") and r.get("source_first_side") == "DOWN",
            "Side split is research evidence; live policy must define side selection itself.",
        ),
    ]


def weighted_average(rows: list[dict[str, str]], value_key: str, weight_key: str) -> float | None:
    total_weight = sum(fnum(row, weight_key) for row in rows)
    if total_weight <= 0:
        return None
    return sum(fnum(row, value_key) * fnum(row, weight_key) for row in rows) / total_weight


def max_capital_tied(rows: list[dict[str, str]], row_scale: Callable[[dict[str, str]], float]) -> float:
    events: list[tuple[int, float]] = []
    for row in rows:
        start = int(fnum(row, "source_first_trade_s") or fnum(row, "market_start_s"))
        end = int(fnum(row, "market_end_s"))
        notional = fnum(row, "source_buy_actual") * row_scale(row)
        if notional <= 0:
            continue
        events.append((start, notional))
        events.append((end, -notional))
    current = 0.0
    peak = 0.0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        peak = max(peak, current)
    return peak


def summarize_cap(rows: list[dict[str, str]], per_market_cap: float) -> dict[str, Any]:
    def scale(row: dict[str, str]) -> float:
        buy = fnum(row, "source_buy_actual")
        if buy <= 0:
            return 0.0
        return min(1.0, per_market_cap / buy)

    scaled_buy = sum(fnum(row, "source_buy_actual") * scale(row) for row in rows)
    scaled_pnl = sum(fnum(row, "source_cash_pnl") * scale(row) for row in rows)
    peak = max_capital_tied(rows, scale)
    return {
        "per_market_cap": round(per_market_cap, 6),
        "scaled_buy_actual": round(scaled_buy, 6),
        "scaled_cash_pnl": round(scaled_pnl, 6),
        "max_capital_tied": round(peak, 6),
        "bankroll_feasible": peak <= INITIAL_BANKROLL,
        "roi_on_initial_300": round(scaled_pnl / INITIAL_BANKROLL, 8),
        "turnover_on_initial_300": round(scaled_buy / INITIAL_BANKROLL, 8),
    }


def summarize_controller(all_rows: list[dict[str, str]], spec: ControllerSpec, latest: str) -> dict[str, Any]:
    rows = [row for row in all_rows if spec.predicate(row)]
    buy = sum(fnum(row, "source_buy_actual") for row in rows)
    pnl = sum(fnum(row, "source_cash_pnl") for row in rows)
    profiles: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        profiles[row["source_profile_label"]].append(row)
    profile_pnls = {
        label: sum(fnum(row, "source_cash_pnl") for row in label_rows)
        for label, label_rows in profiles.items()
    }
    source_peak = max_capital_tied(rows, lambda _row: 1.0)
    bankroll_scale = INITIAL_BANKROLL / source_peak if source_peak > 0 else 0.0
    bad_pc_buy = sum(fnum(row, "source_buy_actual") for row in rows if fnum(row, "source_pair_cost") >= 1.0)
    latest_count = len([row for row in rows if row["source_profile_label"] == latest])
    return {
        "controller_id": spec.controller_id,
        "role": spec.role,
        "feature_status": spec.feature_status,
        "interpretation": spec.interpretation,
        "market_count": len(rows),
        "latest_window_market_count": latest_count,
        "latest_window_participation_rate": round(latest_count / EXPECTED_BTC5M_ROUNDS_PER_PROFILE, 8),
        "active_profile_count": len(profiles),
        "winning_profile_count": sum(1 for value in profile_pnls.values() if value > 0),
        "source_buy_actual": round(buy, 6),
        "source_cash_pnl": round(pnl, 6),
        "source_roi": round(pnl / buy, 8) if buy else None,
        "source_weighted_pair_cost": (
            round(weighted_average(rows, "source_pair_cost", "source_paired_qty"), 8)
            if weighted_average(rows, "source_pair_cost", "source_paired_qty") is not None
            else None
        ),
        "source_weighted_resid_rate_by_buy": (
            round(weighted_average(rows, "source_resid_rate", "source_buy_actual"), 8)
            if weighted_average(rows, "source_resid_rate", "source_buy_actual") is not None
            else None
        ),
        "bad_pair_cost_ge_1_buy_share": round(bad_pc_buy / buy, 8) if buy else None,
        "source_max_capital_tied_proxy": round(source_peak, 6),
        "proportional_scale_to_300_factor": round(bankroll_scale, 10),
        "proportional_scaled_cash_pnl_on_300": round(pnl * bankroll_scale, 6),
        "proportional_roi_on_initial_300": round((pnl * bankroll_scale) / INITIAL_BANKROLL, 8),
        "proportional_turnover_on_initial_300": round((buy * bankroll_scale) / INITIAL_BANKROLL, 8),
        "cap_10": summarize_cap(rows, 10.0),
        "cap_20": summarize_cap(rows, 20.0),
        "cap_30": summarize_cap(rows, 30.0),
    }


def flatten_summary_row(row: dict[str, Any]) -> dict[str, Any]:
    flat = {key: value for key, value in row.items() if not isinstance(value, dict)}
    for cap_key in ("cap_10", "cap_20", "cap_30"):
        for key, value in row[cap_key].items():
            flat[f"{cap_key}_{key}"] = value
    return flat


def command_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: review-only CE25 broad overlay capital packet'\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_rows(LEDGER_CSV)
    latest = latest_label(rows)
    summaries = [summarize_controller(rows, spec, latest) for spec in specs()]
    csv_rows = [flatten_summary_row(row) for row in summaries]
    fieldnames = list(csv_rows[0].keys())

    summary_csv = OUTPUT_DIR / "ce25_btc5m_broad_overlay_capital_summary.csv"
    write_csv(summary_csv, csv_rows, fieldnames)

    command_preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    command_preview(command_preview_path)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "strategy_owner_line": "CE25_BROAD_RESEARCH",
        "mainline_decision": {
            "backbone": "BTC5M_BROAD_BASELINE",
            "overlays": [
                "BTC5M_20_35_ALPHA_OVERLAY",
                "BTC5M_65_80_RISK_OVERLAY",
                "BTC5M_LAST60_CLOCK_OVERLAY",
                "BTC5M_LAST60_20_35_ALPHA_OVERLAY",
                "BTC5M_20_35_DOWN_SIDE_RESEARCH_OVERLAY",
            ],
            "low_tail_top1_qty_role": "overlay/filter only, not high-participation mainline",
        },
        "capital_model": {
            "initial_bankroll_usdc": INITIAL_BANKROLL,
            "capital_tied_proxy": "sum scaled source_buy_actual from source_first_trade_s until market_end_s; assumes capital is reusable after merge/redeem/settlement at market end",
            "proportional_scale_to_300": "scale all historical public-profile notionals by 300 / source_max_capital_tied_proxy",
            "per_market_cap_modes_usdc": [10.0, 20.0, 30.0],
            "limitations": [
                "public profile cash_pnl is account-label evidence, not own execution truth",
                "capital model is a proxy until matching replay source exists",
                "exact fee recomputation requires fill-level prices and sizes from matching replay/source data",
            ],
        },
        "official_fee_contract": {
            "source_url": "https://docs.polymarket.com/polymarket-learn/trading/fees",
            "crypto_taker_fee_rate": OFFICIAL_CRYPTO_FEE_RATE,
            "formula": "fee = C * feeRate * p * (1 - p)",
            "maker_fee_rate": 0,
            "exact_recompute_status": "BLOCKED_MATCHING_FILL_LEVEL_REPLAY_SOURCE_REQUIRED",
        },
        "source_bindings": {
            "ledger_csv": {"path": str(LEDGER_CSV), "sha256": sha256_file(LEDGER_CSV)},
            "ledger_summary": {"path": str(LEDGER_SUMMARY), "sha256": sha256_file(LEDGER_SUMMARY)},
            "controller_packet": {"path": str(CONTROLLER_PACKET), "sha256": sha256_file(CONTROLLER_PACKET)},
            "matching_source_packet": {"path": str(MATCHING_SOURCE_PACKET), "sha256": sha256_file(MATCHING_SOURCE_PACKET)},
            "source_crosswalk": {"path": str(SOURCE_CROSSWALK), "sha256": sha256_file(SOURCE_CROSSWALK)},
        },
        "matching_source_blocker": {
            "source_crosswalk_status": read_json(SOURCE_CROSSWALK).get("status"),
            "matched_condition_count": read_json(SOURCE_CROSSWALK).get("matched_condition_count"),
            "matching_source_packet_status": read_json(MATCHING_SOURCE_PACKET).get("status"),
            "archive_root_available_now": read_json(MATCHING_SOURCE_PACKET).get("environment_preflight", {}).get("archive_root_available_now"),
            "replay_builder_target_days_allowlisted_now": read_json(MATCHING_SOURCE_PACKET).get("environment_preflight", {}).get("replay_builder_target_days_allowlisted_now"),
        },
        "controller_summaries": summaries,
        "outputs": {
            "summary_csv": str(summary_csv),
            "command_preview_not_authorized": str(command_preview_path),
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
        },
        "highest_allowed_status": STATUS,
        "next_step": "unlock matching replay source for 2026-05-28..2026-06-04, then replace public-profile proxy with official-fee fill-level replay and capital ledger",
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_CAPITAL_PACKET.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for file in sorted(OUTPUT_DIR.iterdir()):
        if file.name == "CE25_BTC5M_BROAD_OVERLAY_CAPITAL_HASH_MANIFEST.json":
            continue
        if file.is_file():
            manifest["files"][file.name] = {"path": str(file), "sha256": sha256_file(file), "size": file.stat().st_size}
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_CAPITAL_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "summary_csv": str(summary_csv),
                "packet": str(packet_path),
                "manifest": str(manifest_path),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

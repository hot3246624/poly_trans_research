#!/usr/bin/env python3
"""Build CE25 BTC 5m broad+overlay controller V1 review packet.

V1 turns the current evidence into concrete public-profile proxy sizing
variants. The variants are still research-only: source first/last timing and
price buckets must be rewritten into fixed-clock public-book rules before any
OOS or live path.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
LEDGER_CSV = (
    EXPORTS
    / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
    / "ce25_btc5m_broad_profile_candidate_ledger.csv"
)
LEDGER_SUMMARY = (
    EXPORTS
    / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
    / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_SUMMARY.json"
)
CAPITAL_PACKET = (
    EXPORTS
    / "ce25_btc5m_broad_overlay_capital_packet_20260606"
    / "CE25_BTC5M_BROAD_OVERLAY_CAPITAL_PACKET.json"
)
MATCHING_SOURCE_PACKET = (
    EXPORTS
    / "ce25_btc5m_matching_source_build_packet_20260605"
    / "CE25_BTC5M_MATCHING_SOURCE_BUILD_PACKET.json"
)
OUTPUT_DIR = EXPORTS / "ce25_btc5m_broad_overlay_controller_v1_packet_20260606"

STATUS = "KEEP_CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_REVIEW_ONLY_MATCHING_SOURCE_REQUIRED_NOT_OOS_READY"
INITIAL_BANKROLL = 300.0
EXPECTED_LATEST_BTC5M_ROUNDS = 288
OFFICIAL_FEE_RATE = 0.07
RESID_PASS_MAX = 0.12
RESID_WATCH_MAX = 0.16
BAD_PAIR_COST_WATCH_MAX = 0.32


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
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def fnum(row: dict[str, str], key: str) -> float:
    try:
        value = row.get(key)
        return float(value) if value not in ("", None) else 0.0
    except ValueError:
        return 0.0


def latest_label(rows: list[dict[str, str]]) -> str:
    return sorted({row["source_profile_label"] for row in rows})[-1]


def is_last60(row: dict[str, str]) -> bool:
    return row.get("source_last_delta_bucket") == "last_60s"


def is_20_35(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "20-35"


def is_65_80(row: dict[str, str]) -> bool:
    return row.get("source_first_price_bucket") == "65-80"


def is_down(row: dict[str, str]) -> bool:
    return row.get("source_first_side") == "DOWN"


@dataclass(frozen=True)
class Variant:
    variant_id: str
    role: str
    description: str
    cap_fn: Callable[[dict[str, str]], float]
    live_rewrite_contract: str


def variants() -> list[Variant]:
    return [
        Variant(
            "BASE10_ALL_BTC5M",
            "minimum_high_participation_baseline",
            "10 USDC cap in every BTC 5m public-profile market.",
            lambda r: 10.0,
            "asset/timeframe schedule only; fixed-clock public-book validation still required",
        ),
        Variant(
            "BASE10_LAST60_PLUS10_CAP20",
            "balanced_backbone_plus_late_overlay",
            "10 USDC base, add 10 USDC when the profile label is last60.",
            lambda r: 20.0 if is_last60(r) else 10.0,
            "last60 label must be rewritten as our own fixed-clock final-minute observation schedule",
        ),
        Variant(
            "BASE10_LAST60_PLUS20_CAP30",
            "primary_broad_plus_late_overlay_candidate",
            "10 USDC base, add 20 USDC when the profile label is last60.",
            lambda r: 30.0 if is_last60(r) else 10.0,
            "last60 label must be rewritten as our own fixed-clock final-minute observation schedule",
        ),
        Variant(
            "BASE10_LAST60_PLUS10_2035_PLUS10_CAP30",
            "broad_late_low_price_alpha_blend",
            "10 USDC base, add 10 for last60, add 10 for 20-35, cap 30.",
            lambda r: min(30.0, 10.0 + (10.0 if is_last60(r) else 0.0) + (10.0 if is_20_35(r) else 0.0)),
            "last60 and first-price buckets must both be rewritten to fixed-clock public-book rules",
        ),
        Variant(
            "BASE10_LAST60_PLUS10_2035_PLUS10_6580_PLUS5_CAP30",
            "broad_three_overlay_blend",
            "10 USDC base, add 10 for last60, add 10 for 20-35, add 5 for 65-80, cap 30.",
            lambda r: min(
                30.0,
                10.0
                + (10.0 if is_last60(r) else 0.0)
                + (10.0 if is_20_35(r) else 0.0)
                + (5.0 if is_65_80(r) else 0.0),
            ),
            "all overlay labels require fixed-clock public-book rewrite; 65-80 remains risk-control overlay",
        ),
        Variant(
            "BASE5_LAST60_PLUS15_2035_PLUS10_6580_PLUS10_CAP30",
            "overlay_weighted_lower_base",
            "5 USDC base, stronger last60/20-35/65-80 overlays, cap 30.",
            lambda r: min(
                30.0,
                5.0
                + (15.0 if is_last60(r) else 0.0)
                + (10.0 if is_20_35(r) else 0.0)
                + (10.0 if is_65_80(r) else 0.0),
            ),
            "lower base reduces broad participation capital while keeping overlay emphasis",
        ),
        Variant(
            "LAST60_2035_CAP30_OVERLAY_ONLY",
            "pure_alpha_watch_not_mainline",
            "30 USDC only when profile label is both last60 and 20-35.",
            lambda r: 30.0 if is_last60(r) and is_20_35(r) else 0.0,
            "too low coverage for mainline; use as alpha overlay watch only",
        ),
        Variant(
            "LAST60_2035_DOWN_CAP30_OVERLAY_ONLY",
            "side_split_alpha_watch_not_mainline",
            "30 USDC only when profile label is last60, 20-35, and DOWN.",
            lambda r: 30.0 if is_last60(r) and is_20_35(r) and is_down(r) else 0.0,
            "source side is not a free live signal; side rule must be independently defined",
        ),
    ]


def weighted(rows: list[dict[str, str]], value_key: str, weights: dict[str, float]) -> float | None:
    total_weight = sum(weights.get(row["candidate_id"], 0.0) for row in rows)
    if total_weight <= 0:
        return None
    return sum(fnum(row, value_key) * weights.get(row["candidate_id"], 0.0) for row in rows) / total_weight


def peak_capital(rows: list[dict[str, str]], scaled_buy: dict[str, float]) -> float:
    events: list[tuple[int, float]] = []
    for row in rows:
        buy = scaled_buy.get(row["candidate_id"], 0.0)
        if buy <= 0:
            continue
        start = int(fnum(row, "source_first_trade_s") or fnum(row, "market_start_s"))
        end = int(fnum(row, "market_end_s"))
        events.append((start, buy))
        events.append((end, -buy))
    current = 0.0
    peak = 0.0
    for _, delta in sorted(events, key=lambda item: (item[0], item[1])):
        current += delta
        peak = max(peak, current)
    return peak


def summarize_variant(rows: list[dict[str, str]], latest: str, variant: Variant) -> dict[str, Any]:
    selected = [row for row in rows if variant.cap_fn(row) > 0]
    scaled_buy: dict[str, float] = {}
    scaled_pnl: dict[str, float] = {}
    scaled_pair_qty: dict[str, float] = {}
    source_buy_total = 0.0
    for row in selected:
        source_buy = fnum(row, "source_buy_actual")
        cap = variant.cap_fn(row)
        scale = min(1.0, cap / source_buy) if source_buy > 0 else 0.0
        buy = source_buy * scale
        scaled_buy[row["candidate_id"]] = buy
        scaled_pnl[row["candidate_id"]] = fnum(row, "source_cash_pnl") * scale
        scaled_pair_qty[row["candidate_id"]] = fnum(row, "source_paired_qty") * scale
        source_buy_total += source_buy

    buy_total = sum(scaled_buy.values())
    pnl_total = sum(scaled_pnl.values())
    peak = peak_capital(selected, scaled_buy)
    bad_buy = sum(
        scaled_buy[row["candidate_id"]]
        for row in selected
        if fnum(row, "source_pair_cost") >= 1.0
    )
    latest_count = len([row for row in selected if row["source_profile_label"] == latest])
    profile_pnls: dict[str, float] = {}
    for row in selected:
        profile_pnls[row["source_profile_label"]] = profile_pnls.get(row["source_profile_label"], 0.0) + scaled_pnl[row["candidate_id"]]

    resid_rate = weighted(selected, "source_resid_rate", scaled_buy)
    bad_pair_share = bad_buy / buy_total if buy_total else None
    if resid_rate is None:
        resid_gate = "BLOCKED_NO_RESID_EVIDENCE"
    elif resid_rate <= RESID_PASS_MAX:
        resid_gate = "PASS_RESID_LE_12PCT_PUBLIC_PROXY"
    elif resid_rate <= RESID_WATCH_MAX:
        resid_gate = "WATCH_RESID_12_TO_16PCT_REPLAY_REQUIRED"
    else:
        resid_gate = "BLOCKED_RESID_GT_16PCT_PUBLIC_PROXY"
    if bad_pair_share is None:
        bad_pair_gate = "BLOCKED_NO_PAIR_COST_EVIDENCE"
    elif bad_pair_share <= BAD_PAIR_COST_WATCH_MAX:
        bad_pair_gate = "WATCH_BAD_PAIR_COST_SHARE_REPLAY_REQUIRED"
    else:
        bad_pair_gate = "BLOCKED_BAD_PAIR_COST_SHARE_HIGH_PUBLIC_PROXY"

    return {
        "variant_id": variant.variant_id,
        "role": variant.role,
        "description": variant.description,
        "live_rewrite_contract": variant.live_rewrite_contract,
        "selected_market_count": len(selected),
        "latest_window_market_count": latest_count,
        "latest_window_participation_rate": round(latest_count / EXPECTED_LATEST_BTC5M_ROUNDS, 8),
        "active_profile_count": len(profile_pnls),
        "winning_profile_count": sum(1 for value in profile_pnls.values() if value > 0),
        "source_buy_before_cap": round(source_buy_total, 6),
        "scaled_buy_actual": round(buy_total, 6),
        "scaled_cash_pnl": round(pnl_total, 6),
        "scaled_roi_on_buy": round(pnl_total / buy_total, 8) if buy_total else None,
        "max_capital_tied_proxy": round(peak, 6),
        "bankroll_feasible_300": peak <= INITIAL_BANKROLL,
        "roi_on_initial_300": round(pnl_total / INITIAL_BANKROLL, 8),
        "turnover_on_initial_300": round(buy_total / INITIAL_BANKROLL, 8),
        "weighted_pair_cost": (
            round(weighted(selected, "source_pair_cost", scaled_pair_qty), 8)
            if weighted(selected, "source_pair_cost", scaled_pair_qty) is not None
            else None
        ),
        "weighted_resid_rate_by_buy": (
            round(resid_rate, 8)
            if resid_rate is not None
            else None
        ),
        "residual_gate_status": resid_gate,
        "bad_pair_cost_ge_1_buy_share": round(bad_pair_share, 8) if bad_pair_share is not None else None,
        "bad_pair_cost_gate_status": bad_pair_gate,
        "exact_official_fee_replay_status": "BLOCKED_MATCHING_FILL_LEVEL_REPLAY_SOURCE_REQUIRED",
    }


def write_preview(path: Path) -> None:
    path.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        "echo 'NOT_AUTHORIZED: CE25 BTC5M broad overlay controller V1 is review-only'\n"
        "exit 66\n",
        encoding="utf-8",
    )
    path.chmod(0o755)


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = read_rows(LEDGER_CSV)
    latest = latest_label(rows)
    variant_rows = [summarize_variant(rows, latest, variant) for variant in variants()]
    variant_rows.sort(
        key=lambda row: (
            row["latest_window_participation_rate"] >= 0.4,
            row["residual_gate_status"] != "BLOCKED_RESID_GT_16PCT_PUBLIC_PROXY",
            row["bad_pair_cost_gate_status"] != "BLOCKED_BAD_PAIR_COST_SHARE_HIGH_PUBLIC_PROXY",
            row["roi_on_initial_300"],
            row["scaled_roi_on_buy"] or 0.0,
        ),
        reverse=True,
    )

    summary_csv = OUTPUT_DIR / "ce25_btc5m_broad_overlay_controller_v1_variant_summary.csv"
    write_csv(summary_csv, variant_rows, list(variant_rows[0].keys()))

    preview = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    write_preview(preview)

    high_participation_rows = [
        row for row in variant_rows if row["latest_window_participation_rate"] >= 0.4
    ]
    replay_candidate_rows = [
        row
        for row in high_participation_rows
        if row["residual_gate_status"] != "BLOCKED_RESID_GT_16PCT_PUBLIC_PROXY"
        and row["bad_pair_cost_gate_status"] != "BLOCKED_BAD_PAIR_COST_SHARE_HIGH_PUBLIC_PROXY"
    ]
    best_high_participation = max(
        replay_candidate_rows or high_participation_rows,
        key=lambda row: (row["roi_on_initial_300"], row["scaled_roi_on_buy"] or 0.0),
    )
    best_alpha_watch = max(
        variant_rows,
        key=lambda row: (row["scaled_roi_on_buy"] or 0.0, -(row["weighted_resid_rate_by_buy"] or 9.0)),
    )
    capital_packet = read_json(CAPITAL_PACKET)
    matching_packet = read_json(MATCHING_SOURCE_PACKET)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_id": "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1",
        "strategy_owner_line": "CE25_BROAD_RESEARCH",
        "decision": {
            "primary_controller_variant": best_high_participation["variant_id"],
            "primary_reason": "best high-participation review candidate after residual/bad-pair public-proxy gates and initial-capital proxy; still matching-source blocked",
            "alpha_watch_variant": best_alpha_watch["variant_id"],
            "alpha_watch_reason": "highest scaled ROI, but coverage too low for mainline",
            "broad_backbone_required": True,
            "low_tail_top1_qty_role": "overlay/filter only",
        },
        "source_bindings": {
            "ledger_csv": {"path": str(LEDGER_CSV), "sha256": sha256_file(LEDGER_CSV)},
            "ledger_summary": {"path": str(LEDGER_SUMMARY), "sha256": sha256_file(LEDGER_SUMMARY)},
            "capital_packet": {"path": str(CAPITAL_PACKET), "sha256": sha256_file(CAPITAL_PACKET)},
            "matching_source_packet": {"path": str(MATCHING_SOURCE_PACKET), "sha256": sha256_file(MATCHING_SOURCE_PACKET)},
            "build_script": {
                "path": str(ROOT / "scripts" / "build_ce25_btc5m_broad_overlay_controller_v1_packet.py"),
                "sha256": sha256_file(ROOT / "scripts" / "build_ce25_btc5m_broad_overlay_controller_v1_packet.py"),
            },
        },
        "official_fee_contract": {
            "fee_rate": OFFICIAL_FEE_RATE,
            "formula": "fee = C * feeRate * p * (1 - p)",
            "source_profile_fee_status": "fee-inclusive public activity proxy",
            "exact_replay_status": "BLOCKED_MATCHING_FILL_LEVEL_REPLAY_SOURCE_REQUIRED",
        },
        "matching_source_gate": {
            "status": matching_packet.get("status"),
            "archive_root_available_now": matching_packet.get("environment_preflight", {}).get("archive_root_available_now"),
            "replay_builder_target_days_allowlisted_now": matching_packet.get("environment_preflight", {}).get("replay_builder_target_days_allowlisted_now"),
            "required_before_oos": [
                "matching replay source for 2026-05-28..2026-06-04",
                "source crosswalk overlap > 0 and reviewed",
                "official-fee fill-level replay",
                "capital/merge/reuse ledger from replay actions",
            ],
        },
        "field_fail_closed_contract": {
            "forbidden_as_live_entry_signal": [
                "source_first_trade_s",
                "source_last_trade_s",
                "source_first_delta_bucket",
                "source_last_delta_bucket",
                "source_first_price_bucket",
                "source_first_side",
                "source_pair_delay_bucket",
                "source_cash_pnl",
                "source_pair_cost",
                "source_resid_rate",
                "winner",
                "private_order_truth",
            ],
            "rewrite_required": [
                "last60 -> fixed clock observations inside final 60s",
                "20-35/65-80 -> contemporaneous public-book executable price bucket",
                "DOWN/UP side split -> independently defined side selection rule",
            ],
        },
        "variant_summary_csv": str(summary_csv),
        "variant_summaries": variant_rows,
        "capital_packet_status": capital_packet.get("status"),
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "oos_authorized": False,
            "runner_authorized": False,
            "orders_authorized": False,
            "canary_authorized": False,
        },
        "highest_allowed_status": STATUS,
        "next_step": "when archive is available, regenerate matching source packet and run only after separate approval; then replace public-profile proxy variant summary with replay-backed official-fee capital ledger",
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_PACKET.json"
    packet_path.write_text(json.dumps(packet, indent=2, sort_keys=True), encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "files": {},
    }
    for path in sorted(OUTPUT_DIR.iterdir()):
        if path.name == "CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_HASH_MANIFEST.json":
            continue
        if path.is_file():
            manifest["files"][path.name] = {
                "path": str(path),
                "sha256": sha256_file(path),
                "size": path.stat().st_size,
            }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_BROAD_OVERLAY_CONTROLLER_V1_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print(
        json.dumps(
            {
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet": str(packet_path),
                "summary_csv": str(summary_csv),
                "primary_variant": best_high_participation["variant_id"],
                "alpha_watch_variant": best_alpha_watch["variant_id"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

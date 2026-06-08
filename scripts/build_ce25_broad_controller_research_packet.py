#!/usr/bin/env python3
"""Build CE25 broad controller research packet.

This packet turns the broad participation frontier into concrete research
lanes. It remains public-only/review-only and does not authorize OOS/live work.
"""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
EXPORTS = ROOT / "data" / "exports"
FRONTIER_DIR = EXPORTS / "ce25_broad_participation_frontier_20260604"
PARTICIPATION_DIR = EXPORTS / "ce25_participation_coverage_report_20260604"
STRICT_FEE_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
AUTORESEARCH_DIR = EXPORTS / "account_autoresearch_iter_ce25_7win_nagi_4win_20260604_hb2_bjt"
OUTPUT_DIR = EXPORTS / "ce25_broad_controller_research_packet_20260604"

STATUS = "KEEP_CE25_BROAD_CONTROLLER_RESEARCH_PACKET_PREPARED_REVIEW_ONLY_NOT_OOS_READY"
STRATEGY_FAMILY = "ce25_broad_participation_controller_public_profile_v1"
OWNER_LINE = "CE25_BROAD_RESEARCH"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def find_scoreboard(value: str) -> dict[str, Any]:
    rows = read_tsv(AUTORESEARCH_DIR / "proxy_scoreboard.tsv")
    for row in rows:
        if row.get("account") == "ce25" and row.get("value") == value:
            return row
    raise SystemExit(f"missing scoreboard value: {value}")


def find_frontier(group_id: str) -> dict[str, Any]:
    rows = read_tsv(FRONTIER_DIR / "ce25_broad_participation_frontier.tsv")
    for row in rows:
        if row.get("group_id") == group_id:
            return row
    raise SystemExit(f"missing frontier group: {group_id}")


def f(row: dict[str, Any], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "orders_authorized": False,
    }


def lane_from_frontier(group_id: str, role: str, rationale: str) -> dict[str, Any]:
    row = find_frontier(group_id)
    return {
        "lane_id": group_id,
        "role": role,
        "rationale": rationale,
        "market_count": int(float(row["market_count"])),
        "expected_market_count": int(float(row["expected_market_count"])),
        "coverage_rate": f(row, "coverage_rate"),
        "coverage_rate_latest": f(row, "coverage_rate_latest"),
        "recent3_coverage_rate": f(row, "recent3_coverage_rate"),
        "cash_pnl": f(row, "cash_pnl"),
        "roi": f(row, "roi"),
        "recent3_cash_pnl": f(row, "recent3_cash_pnl"),
        "recent3_roi": f(row, "recent3_roi"),
        "win_windows": int(float(row["win_windows"])),
        "loss_windows": int(float(row["loss_windows"])),
        "resid_rate": f(row, "resid_rate"),
        "pair_cost_weighted": f(row, "pair_cost_weighted"),
        "status": "REVIEW_LANE_NOT_OOS_READY",
    }


def seed_from_scoreboard(value: str, role: str, ex_ante_status: str, caveat: str) -> dict[str, Any]:
    row = find_scoreboard(value)
    return {
        "seed_id": value.replace("|", "_"),
        "scoreboard_value": value,
        "role": role,
        "market_count": int(float(row["markets"])),
        "buy_actual": f(row, "buy_actual"),
        "cash_pnl": f(row, "cash_pnl"),
        "roi": f(row, "roi"),
        "pair_cost": f(row, "pair_cost"),
        "resid_rate": f(row, "resid_rate"),
        "wins": int(float(row["wins"])),
        "losses": int(float(row["losses"])),
        "quality_score": f(row, "quality_score"),
        "ex_ante_status": ex_ante_status,
        "caveat": caveat,
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frontier_summary = read_json(FRONTIER_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json")
    participation_summary = read_json(PARTICIPATION_DIR / "CE25_PARTICIPATION_COVERAGE_SUMMARY.json")
    strict_fee_summary = read_json(STRICT_FEE_DIR / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json")

    lanes = [
        lane_from_frontier(
            "BTC_5m",
            "primary_broad_controller",
            "High recent participation and strongest aggregate PnL/ROI among broad high-coverage lanes.",
        ),
        lane_from_frontier(
            "ALL_CRYPTO_5m",
            "secondary_broader_coverage_controller",
            "Broader 5m crypto coverage; lower ROI and more cross-asset complexity than BTC_5m.",
        ),
        lane_from_frontier(
            "ETH_15m",
            "watchlist_high_coverage_controller",
            "High frontier score and 6/7 winning windows, but lower relevance to BTC 5m target and needs separate asset semantics.",
        ),
    ]
    seeds = [
        seed_from_scoreboard(
            "BTC|5m|20-35",
            "alpha_seed",
            "PARTLY_EX_ANTE",
            "first_price bucket is observable, but public profile includes realized account behavior and needs runner translation.",
        ),
        seed_from_scoreboard(
            "BTC|65-80",
            "risk_control_seed",
            "PARTLY_EX_ANTE",
            "high-price band is observable; must separate pair-cost/residual guard from CE25 outcome profile.",
        ),
        seed_from_scoreboard(
            "BTC|5m|last_60s",
            "late_window_control_seed",
            "OUTCOME_TIMING_NEEDS_EX_ANTE_REWRITE",
            "last_delta/late-window timing can be translated to a clock rule, but cannot rely on post-facto market close fields.",
        ),
        seed_from_scoreboard(
            "BTC|5m",
            "broad_baseline",
            "PUBLIC_PROFILE_ONLY",
            "broad profile baseline, not yet a deterministic entry/exit controller.",
        ),
    ]
    packet = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_owner_line": OWNER_LINE,
        "strategy_family": STRATEGY_FAMILY,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_context": {
            "frontier_summary_sha256": sha256_file(FRONTIER_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json"),
            "participation_summary_sha256": sha256_file(PARTICIPATION_DIR / "CE25_PARTICIPATION_COVERAGE_SUMMARY.json"),
            "official_fee_summary_sha256": sha256_file(STRICT_FEE_DIR / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json"),
            "proxy_scoreboard_sha256": sha256_file(AUTORESEARCH_DIR / "proxy_scoreboard.tsv"),
        },
        "accepted_reframe": {
            "old_target_qty8_role": "safe_seed_filter_only",
            "new_mainline": "BTC_5m_broad_participation_controller",
            "reason": "CE25 broad participation is near 90% in latest BTC 5m, while target_qty8 strict branch only participates in 3.1% of BTC 5m rounds.",
        },
        "lanes": lanes,
        "research_seeds": seeds,
        "engineering_plan_review_only": [
            {
                "step": "build_btc5m_broad_profile_candidate_ledger",
                "goal": "create one row per CE25 BTC 5m public-profile market with coverage/profit/risk labels",
                "not_allowed": "treat public profile rows as private truth or OOS-ready strategy",
            },
            {
                "step": "split_ex_ante_vs_outcome_features",
                "goal": "mark first_price/clock/asset/tf as candidate ex-ante; mark pair_delay/resid_side/cash_pnl as outcome-only labels",
                "not_allowed": "use outcome-only fields as live entry conditions",
            },
            {
                "step": "learn_participation_controller",
                "goal": "derive deterministic high-participation filters from observable clock/market/book state",
                "not_allowed": "copy CE25 fills without public no-order validation",
            },
            {
                "step": "stress_official_fee_and_capacity",
                "goal": "apply official crypto feeRate=0.07 and target notional ladders",
                "not_allowed": "reuse old 0.03 fee as current crypto fee",
            },
            {
                "step": "prepare_no_order_public_oos_after_controller",
                "goal": "only after ex-ante controller exists, prepare review-only OOS packet",
                "not_allowed": "start runner/observer/OOS/live from this packet",
            },
        ],
        "current_numeric_context": {
            "latest_ce25_btc_5m_participation_rate": participation_summary["latest_window"]["btc_5m_participation_rate"],
            "latest_ce25_crypto_5m_participation_rate": participation_summary["latest_window"]["crypto_5m_participation_rate"],
            "target_qty8_participation_rate": strict_fee_summary["participation_rate_by_round"],
            "target_qty8_official_crypto_fee_cash_pnl": strict_fee_summary["cash_pnl_official_fee_0p07"],
        },
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }
    packet_path = OUTPUT_DIR / "CE25_BROAD_CONTROLLER_RESEARCH_PACKET.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    command_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 broad controller packet is review-only; no OOS/live/runner command is issued.' >&2",
                "exit 66",
                "",
            ]
        )
    )
    command_path.chmod(0o755)
    note_path = OUTPUT_DIR / "CE25_BROAD_CONTROLLER_BOUNDARY_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Broad Controller Boundary",
                "",
                f"Status: `{STATUS}`",
                "",
                "The mainline is now BTC 5m broad participation, not the 3.1% target_qty8 strict branch.",
                "This packet is public profile research only. It does not authorize OOS, runner/observer start, private key, import, order/cancel/redeem, canary/live/deploy/funding, latest pointer update, private truth, promotion, live-ready, or deployable claim.",
                "",
            ]
        )
    )
    artifacts = [
        packet_path,
        command_path,
        note_path,
        FRONTIER_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json",
        FRONTIER_DIR / "ce25_broad_participation_frontier.tsv",
        PARTICIPATION_DIR / "CE25_PARTICIPATION_COVERAGE_SUMMARY.json",
        STRICT_FEE_DIR / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json",
        AUTORESEARCH_DIR / "proxy_scoreboard.tsv",
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [{"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size} for path in artifacts],
        "packet_sha256": sha256_file(packet_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BROAD_CONTROLLER_RESEARCH_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(manifest_path),
                "mainline": "BTC_5m_broad_participation_controller",
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

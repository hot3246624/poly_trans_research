#!/usr/bin/env python3
"""Package existing BTC 5m local replay bridge evidence for CE25 research.

This script does not run replay. It audits an existing official-fee completion
state-machine result and estimates a simple market-end capital cashflow.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BACKTEST_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
RESULT_DIR = (
    BACKTEST_ROOT
    / "derived"
    / "completion_candidate_pipeline_v1"
    / "pass_local_completion_residual_cooldown_officialfee_e055_t5_imb125_rc30_050_20260502_20260518_publicfull_v2"
)
CONTROLLER_PACKET_DIR = ROOT / "data" / "exports" / "ce25_btc5m_controller_v0_review_packet_20260604"
OUTPUT_DIR = ROOT / "data" / "exports" / "ce25_btc5m_local_replay_bridge_assessment_20260604"

STATUS = "KEEP_CE25_BTC5M_LOCAL_REPLAY_BRIDGE_ASSESSED_REVIEW_ONLY_NOT_OOS_READY"
OFFICIAL_FEE_RATE = 0.07


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def fnum(value: Any) -> float:
    try:
        if value is None or value == "":
            return 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def market_start_s(slug: str) -> int:
    match = re.search(r"-(\d{10})$", slug)
    if not match:
        raise ValueError(f"cannot parse market start from slug: {slug}")
    return int(match.group(1))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_authorized": False,
        "runner_authorized": False,
        "orders_authorized": False,
    }


def estimate_market_end_cashflow(actions: list[dict[str, str]]) -> dict[str, Any]:
    by_market: dict[str, dict[str, Any]] = defaultdict(lambda: {"YES": 0.0, "NO": 0.0, "cost": 0.0, "fee": 0.0})
    events: list[tuple[int, str, float, str]] = []
    for row in actions:
        condition_id = row["condition_id"]
        side = row["side"]
        qty = fnum(row["seed_qty"])
        seed_cost = fnum(row["seed_cost"])
        fee = fnum(row["fee"])
        ts_ms = int(fnum(row["ts_ms"]))
        slug = row["slug"]
        winner = row["winner_side"]
        if side not in {"YES", "NO"}:
            raise ValueError(f"unexpected side {side!r}")
        market = by_market[condition_id]
        market["YES"] += qty if side == "YES" else 0.0
        market["NO"] += qty if side == "NO" else 0.0
        market["cost"] += seed_cost
        market["fee"] += fee
        market["slug"] = slug
        market["winner_side"] = winner
        market["market_end_ms"] = (market_start_s(slug) + 300) * 1000
        events.append((ts_ms, "buy", -(seed_cost + fee), condition_id))

    payout_total = 0.0
    pair_qty_total = 0.0
    residual_payout_total = 0.0
    for condition_id, market in by_market.items():
        yes_qty = float(market["YES"])
        no_qty = float(market["NO"])
        pair_qty = min(yes_qty, no_qty)
        winner = market.get("winner_side")
        residual_payout = 0.0
        if winner == "YES":
            residual_payout = max(0.0, yes_qty - no_qty)
        elif winner == "NO":
            residual_payout = max(0.0, no_qty - yes_qty)
        payout = pair_qty + residual_payout
        pair_qty_total += pair_qty
        residual_payout_total += residual_payout
        payout_total += payout
        events.append((int(market["market_end_ms"]), "market_end_payout", payout, condition_id))

    events.sort(key=lambda item: (item[0], 0 if item[1] == "buy" else 1))
    cash = 0.0
    min_cash = 0.0
    max_cash = 0.0
    min_event: dict[str, Any] | None = None
    for ts_ms, event_type, amount, condition_id in events:
        cash += amount
        if cash < min_cash:
            min_cash = cash
            min_event = {
                "ts_ms": ts_ms,
                "event_type": event_type,
                "condition_id": condition_id,
                "cash_after": round(cash, 6),
            }
        max_cash = max(max_cash, cash)
    total_buy_cost_with_fee = -sum(amount for _, event_type, amount, _ in events if event_type == "buy")
    return {
        "cashflow_model": "buy outflow at action ts; pair/residual payout at 5m market end; approximate capital stress, not exchange settlement truth",
        "market_count": len(by_market),
        "cashflow_event_count": len(events),
        "total_buy_cost_with_fee": round(total_buy_cost_with_fee, 6),
        "payout_total": round(payout_total, 6),
        "final_cash_pnl": round(cash, 6),
        "peak_capital_required_usdc": round(-min_cash, 6),
        "roi_on_peak_capital": round(cash / (-min_cash), 6) if min_cash < 0 else None,
        "turnover_multiple_on_peak_capital": round(total_buy_cost_with_fee / (-min_cash), 6) if min_cash < 0 else None,
        "capital_300_sufficient": -min_cash <= 300.0 + 1e-9,
        "roi_on_300_if_unscaled_and_sufficient": round(cash / 300.0, 6) if -min_cash <= 300.0 + 1e-9 else None,
        "linear_scaled_pnl_for_300_capital": round(cash * 300.0 / (-min_cash), 6) if min_cash < 0 else None,
        "linear_scaled_roi_for_300_capital": round((cash * 300.0 / (-min_cash)) / 300.0, 6) if min_cash < 0 else None,
        "linear_scaled_capacity_validated": False,
        "linear_scaled_capacity_caveat": "linear scaling uses peak cash stress only; it does not prove order-book capacity, queue fill, slippage, or live settlement timing",
        "pair_qty_total": round(pair_qty_total, 6),
        "residual_payout_total": round(residual_payout_total, 6),
        "min_cash_event": min_event,
        "max_cash_after_all_events": round(max_cash, 6),
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    result_manifest_path = RESULT_DIR / "RESULT_SUMMARY_MANIFEST.json"
    compliance_manifest_path = RESULT_DIR / "COMPLIANCE_MANIFEST.json"
    registry_manifest_path = RESULT_DIR / "CANDIDATE_REGISTRY_MANIFEST.json"
    actions_path = RESULT_DIR / "actions.csv"
    summary_by_day_path = RESULT_DIR / "summary_by_day.csv"
    residual_lots_path = RESULT_DIR / "residual_lots.csv"
    controller_packet_path = CONTROLLER_PACKET_DIR / "CE25_BTC5M_CONTROLLER_V0_REVIEW_PACKET.json"

    result_manifest = read_json(result_manifest_path)
    core = result_manifest["core_metrics"]
    day_rows = read_csv(summary_by_day_path)
    actions = read_csv(actions_path)
    cashflow = estimate_market_end_cashflow(actions)

    assessment = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_scope": "existing local completion-store state-machine result; not a newly executed replay; public/replay research only",
        "result_dir": str(RESULT_DIR),
        "controller_packet_sha256": sha256_file(controller_packet_path),
        "official_fee": {
            "fee_model": core["fee_model"],
            "official_fee_rate": core["official_fee_rate"],
            "official_fee_formula": core["official_fee_formula"],
            "official_fee_source": core["official_fee_source"],
        },
        "core_metrics": {
            "days": result_manifest["days"],
            "day_count": len(result_manifest["days"]),
            "active_markets": core["active_markets"],
            "candidate_count": core["candidate_count"],
            "selected_candidate_count": core["selected_candidate_count"],
            "seed_actions": core["seed_actions"],
            "pair_actions": core["pair_actions"],
            "gross_buy_cost": core["gross_buy_cost"],
            "official_taker_fee": core["official_taker_fee"],
            "fee_after_pnl": core["fee_after_pnl"],
            "net_roi": core["net_roi"],
            "actual_settle_pnl": core["actual_settle_pnl"],
            "actual_settle_roi": core["actual_settle_roi"],
            "weighted_pair_cost": core["weighted_pair_cost"],
            "pair_share_rate": core["pair_share_rate"],
            "residual_qty_rate": core["residual_qty_rate"],
            "residual_cost_rate": core["residual_cost_rate"],
            "worst_day_fee_after_pnl": core["worst_day_fee_after_pnl"],
            "positive_fee_after_days": sum(1 for row in day_rows if fnum(row.get("fee_after_pnl")) > 0),
        },
        "capital_cashflow_estimate": cashflow,
        "interpretation": {
            "good_news": [
                "existing BTC 5m completion-store replay has broad market coverage and positive official-fee net PnL across all 15 included days",
                "weighted pair cost 0.887251 and residual cost rate 0.044285 are materially better than broad public-profile CE25 pair-cost labels",
                "the local result uses official taker fee model at feeRate 0.07",
            ],
            "hard_limits": [
                "this is not CE25 private truth and not live/OOS execution evidence",
                "max_open_cost is a per-market inventory cap, not a global bankroll cap",
                "capital cashflow assumes market-end payout and needs stricter settlement/merge timing validation before bankroll claims",
                "linear scaled 300 USDC figures are not capacity validated and must not be used as expected live ROI",
                "selected candidates are event-layer replay rows, not the public-profile ledger rows",
            ],
            "next_review_step": "build a source-of-truth replay bridge mapping CE25 BTC5M controller V0 fixed-clock rules to this local event-layer state machine, then validate row/market coverage drift",
        },
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
    }
    assessment_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_BRIDGE_ASSESSMENT.json"
    assessment_path.write_text(json.dumps(assessment, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    note_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_BRIDGE_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Local Replay Bridge Assessment",
                "",
                f"Status: `{STATUS}`",
                "",
                "This package audits an existing local completion-store replay result with official taker fee 0.07.",
                "It is review-only and does not authorize OOS, live, private key, import, order, cancel, redeem, canary, deploy, or promotion.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        assessment_path,
        note_path,
        result_manifest_path,
        compliance_manifest_path,
        registry_manifest_path,
        actions_path,
        summary_by_day_path,
        residual_lots_path,
        controller_packet_path,
        Path(__file__).resolve(),
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "assessment_sha256": sha256_file(assessment_path),
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_LOCAL_REPLAY_BRIDGE_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "assessment_sha256": sha256_file(assessment_path),
                "manifest_sha256": sha256_file(manifest_path),
                "fee_after_pnl": core["fee_after_pnl"],
                "peak_capital_required_usdc": cashflow["peak_capital_required_usdc"],
                "linear_scaled_roi_for_300_capital": cashflow["linear_scaled_roi_for_300_capital"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build a BTC core completion strategy review packet from Backtest V1 artifacts.

The packet freezes the current research-grade evidence for the BTC core
completion lane. It is deliberately review-only: no OOS, no runner, no live
orders, and no private-truth claims.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
BT_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
EXPORTS = ROOT / "data/exports"
OUTPUT_DIR = EXPORTS / "btc_core_completion_strategy_review_packet_20260605"
SHORTLIST = EXPORTS / "xuan_backtest_v1_strategy_shortlist_20260605/XUAN_BACKTEST_V1_STRATEGY_SHORTLIST_PACKET.json"
LEDGER_AUDIT = EXPORTS / "btc_core_exante_action_ledger_20260605/BTC_CORE_EXANTE_ACTION_LEDGER_AUDIT.json"
LEDGER_CSV = EXPORTS / "btc_core_exante_action_ledger_20260605/btc_core_exante_action_ledger.csv"
LEAKAGE_PACKET = (
    EXPORTS
    / "btc_core_exante_controller_leakage_audit_packet_20260605/"
    "BTC_CORE_EXANTE_CONTROLLER_LEAKAGE_AUDIT_PACKET.json"
)
REPLAY_VERIFIER = (
    EXPORTS
    / "btc_core_local_replay_verifier_20260605/BTC_CORE_LOCAL_REPLAY_VERIFIER_SUMMARY.json"
)
STATE_MACHINE_MANIFEST = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "RESULT_SUMMARY_MANIFEST.json"
)
STATE_MACHINE_REGISTRY = (
    BT_ROOT
    / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1/"
    "candidate_registry.csv"
)

STATUS = "KEEP_BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET_PREPARED_NOT_OOS_READY"
STRATEGY_ID = "BTC_CORE_COMPLETION_V1"
OWNER_LINE = "xuan_research_local"
INITIAL_CAPITAL_USD = 300.0
OFFICIAL_FEE_DOC = "https://docs.polymarket.com/trading/fees"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "oos_ready": False,
        "runner_authorized": False,
        "observer_authorized": False,
        "orders_authorized": False,
        "latest_pointer_update_authorized": False,
    }


def command_preview(path: Path) -> None:
    path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: BTC core strategy packet is review-only; no OOS/runner/live path is authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    path.chmod(0o755)


def lane_from_shortlist(shortlist: dict[str, Any]) -> dict[str, Any]:
    for lane in shortlist.get("strategy_lanes", []):
        if lane.get("lane_id") == STRATEGY_ID:
            return lane
    raise SystemExit(f"missing {STRATEGY_ID} in shortlist")


def capital_math(core: dict[str, Any], initial_capital: float) -> dict[str, Any]:
    fee_after_pnl = float(core["fee_after_pnl"])
    gross_buy_cost = float(core["gross_buy_cost"])
    day_count = 15
    return {
        "initial_capital_usd": initial_capital,
        "fee_after_pnl_usd": round(fee_after_pnl, 6),
        "gross_buy_cost_usd": round(gross_buy_cost, 6),
        "gross_cost_roi": round(fee_after_pnl / gross_buy_cost, 6),
        "gross_cost_turnover_vs_initial_capital": round(gross_buy_cost / initial_capital, 6),
        "capital_normalized_roi_if_state_machine_recycling_is_feasible": round(fee_after_pnl / initial_capital, 6),
        "capital_normalized_pnl_per_day_if_recycling_is_feasible": round(fee_after_pnl / day_count, 6),
        "capital_normalized_daily_roi_if_recycling_is_feasible": round((fee_after_pnl / day_count) / initial_capital, 6),
        "interpretation": (
            "This is research accounting only. It assumes the state-machine pairing/recycling economics are executable "
            "with sufficient public-book depth, fillability, and operational timing. It is not owner private-truth PnL."
        ),
    }


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    shortlist = read_json(SHORTLIST)
    ledger_audit = read_json(LEDGER_AUDIT)
    leakage = read_json(LEAKAGE_PACKET)
    verifier = read_json(REPLAY_VERIFIER)
    state_manifest = read_json(STATE_MACHINE_MANIFEST)
    lane = lane_from_shortlist(shortlist)
    core = state_manifest["core_metrics"]
    config = state_manifest["config"]
    active_markets = int(core["active_markets"])
    theoretical_btc_5m_markets = 15 * 288
    active_market_coverage_rate = round(active_markets / theoretical_btc_5m_markets, 6)

    controller_contract = {
        "schema_version": 1,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "status": STATUS,
        "mode": "local_replay_research_only",
        "market_universe": {
            "asset": "BTC",
            "timeframe": "5m",
            "source_window": "2026-05-02..2026-05-18",
            "active_markets": active_markets,
            "theoretical_btc_5m_markets_for_15_days": theoretical_btc_5m_markets,
            "active_market_coverage_rate": active_market_coverage_rate,
        },
        "entry_policy_as_backtested": {
            "source": "completion candidate state machine over public L1 flow candidate base",
            "public_trade_taker_side": config["public_trade_taker_side"],
            "offset_s": [config["offset_min_s"], config["seed_offset_max_s"]],
            "seed_px_band": [config["seed_px_lo"], config["seed_px_hi"]],
            "edge": config["edge"],
            "target_qty": config["target_qty"],
            "imbalance_qty_cap": config["imbalance_qty_cap"],
            "max_open_cost": config["max_open_cost"],
            "seed_l1_pair_cap": config["seed_l1_pair_cap"],
            "residual_cooldown_age_s": config["residual_cooldown_age_s"],
            "residual_cooldown_cost_cap": config["residual_cooldown_cost_cap"],
        },
        "fee_model": {
            "type": "official_polymarket_crypto_taker_fee",
            "formula": core["official_fee_formula"],
            "fee_rate": core["official_fee_rate"],
            "source": core.get("official_fee_source") or OFFICIAL_FEE_DOC,
            "official_taker_fee_usd": core["official_taker_fee"],
            "maker_fee_claim": "not_assumed",
        },
        "allowed_runtime_inputs": {
            "primary_input": str(LEDGER_CSV),
            "allowed_exante_fields": leakage["field_contract"]["proposed_controller_inputs"],
            "must_recompute_or_audit_fee": True,
            "must_not_consume_outcome_fields": leakage["field_contract"]["forbidden_outcome_fields"],
            "must_not_consume_registry_post_action_fields": [
                "inventory_yes_qty_after",
                "inventory_yes_cost_after",
                "inventory_no_qty_after",
                "inventory_no_cost_after",
                "pair_qty_after_seed",
                "pair_actions_after_seed",
                "pair_cost_wavg_after_seed",
            ],
            "post_action_field_policy": (
                "Pair and terminal metrics are review evidence only. Runtime strategy decisions must use contemporaneous "
                "public inputs and internally maintained state, not source registry post-action labels."
            ),
        },
        "merge_recycle_assumption": {
            "state_machine_pair_share_rate": core["pair_share_rate"],
            "paired_qty": core["pair_qty"],
            "residual_qty": core["residual_qty"],
            "residual_qty_rate": core["residual_qty_rate"],
            "economic_interpretation": "Complete-set pairing/recycling is the core capital-efficiency hypothesis.",
            "required_before_oos": [
                "source-of-truth replay bridge for exact order/fill/merge timing",
                "public fillability/top-depth validation for target size",
                "owner private truth for actual fills, fees, inventory, and redeem/merge/cancel behavior before promotion",
            ],
        },
        "non_claims": non_claims(),
    }

    metrics = {
        "schema_version": 1,
        "strategy_id": STRATEGY_ID,
        "status": STATUS,
        "core_backtest_metrics": {
            "day_count": 15,
            "active_markets": active_markets,
            "selected_seed_actions": core["seed_actions"],
            "pair_actions": core["pair_actions"],
            "gross_buy_cost_usd": core["gross_buy_cost"],
            "official_taker_fee_usd": core["official_taker_fee"],
            "fee_after_pnl_usd": core["fee_after_pnl"],
            "gross_cost_roi": core["net_roi"],
            "weighted_pair_cost": core["weighted_pair_cost"],
            "pair_share_rate": core["pair_share_rate"],
            "residual_cost_rate": core["residual_cost_rate"],
            "worst_day_fee_after_pnl_usd": core["worst_day_fee_after_pnl"],
            "positive_day_count": lane["positive_day_count"],
        },
        "capital_math_300_usd": capital_math(core, INITIAL_CAPITAL_USD),
        "local_replay_verifier": {
            "status": verifier["status"],
            "compared_rows": verifier["summary"]["compared_rows"],
            "missing_or_invalid_rows": verifier["summary"]["missing_or_invalid_rows"],
            "terminal_metric_mismatch_count": verifier["summary"]["terminal_metric_mismatch_count"],
            "post_action_drift_rows": verifier["summary"]["drift_rows"],
            "non_inventory_yes_drift_count": verifier["summary"]["non_inventory_yes_drift_count"],
            "interpretation": verifier["summary"]["drift_attribution"]["interpretation"],
        },
        "readiness": {
            "research_ready": True,
            "oos_ready": False,
            "promotion_ready": False,
            "private_truth_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }

    blockers = {
        "schema_version": 1,
        "status": "BLOCKED_BTC_CORE_COMPLETION_V1_OOS_PREP_REQUIRED_NOT_OOS_READY",
        "strategy_id": STRATEGY_ID,
        "blockers_before_oos": [
            {
                "blocker": "runtime_controller_not_implemented",
                "required_fix": "implement a thin local replay runner that consumes the sanitized ex-ante ledger contract and recomputes internal state without registry post-action fields",
            },
            {
                "blocker": "source_of_truth_fillability_not_proven",
                "required_fix": "bind public book/top-depth evidence or own no-order shadow for the exact target size and timing; no REST-only substitution for WS/OOS if contract requires WS",
            },
            {
                "blocker": "registry_post_action_yes_inventory_field_drift",
                "required_fix": "exclude inventory_yes_qty_after/inventory_yes_cost_after from runtime inputs or regenerate registry with current schema/hash-bound source",
            },
            {
                "blocker": "owner_private_truth_missing",
                "required_fix": "before promotion/live, collect own authenticated truth for accepted order, fill, fee, residual inventory, merge/redeem/cancel/error events",
            },
        ],
        "fail_closed_if": [
            "any outcome/winner/truth label is used for ex-ante decisioning",
            "official taker fee formula or fee_rate drifts without review",
            "terminal metric replay mismatch appears",
            "candidate/source row hash coverage drifts",
            "private_truth_ready/strategy_promotion_ready/live_ready/deployable is true in research packet",
        ],
        "non_claims": non_claims(),
    }

    packet = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "scope": "review_only_local_backtest_v1_public_l1_completion_store_research",
        "highest_allowed_status": STATUS,
        "decision": "PRIMARY_LOCAL_REPLAY_RESEARCH_CANDIDATE_OOS_BLOCKED_UNTIL_RUNTIME_AND_FILLABILITY_VALIDATED",
        "metrics": metrics,
        "controller_contract": controller_contract,
        "oos_blockers": blockers,
        "inputs": {
            "shortlist_packet": str(SHORTLIST),
            "shortlist_packet_sha256": sha256_file(SHORTLIST),
            "ledger_csv": str(LEDGER_CSV),
            "ledger_csv_sha256": sha256_file(LEDGER_CSV),
            "ledger_audit": str(LEDGER_AUDIT),
            "ledger_audit_sha256": sha256_file(LEDGER_AUDIT),
            "leakage_packet": str(LEAKAGE_PACKET),
            "leakage_packet_sha256": sha256_file(LEAKAGE_PACKET),
            "local_replay_verifier": str(REPLAY_VERIFIER),
            "local_replay_verifier_sha256": sha256_file(REPLAY_VERIFIER),
            "state_machine_manifest": str(STATE_MACHINE_MANIFEST),
            "state_machine_manifest_sha256": sha256_file(STATE_MACHINE_MANIFEST),
            "state_machine_registry": str(STATE_MACHINE_REGISTRY),
            "state_machine_registry_sha256": sha256_file(STATE_MACHINE_REGISTRY),
        },
        "non_claims": non_claims(),
    }

    packet_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_STRATEGY_REVIEW_PACKET.json"
    contract_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_CONTROLLER_CONTRACT.json"
    metrics_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_METRICS.json"
    blockers_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_OOS_BLOCKERS.json"
    metric_csv_path = OUTPUT_DIR / "btc_core_completion_v1_metric_summary.csv"
    note_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_REVIEW_NOTE.md"
    preview_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    hash_manifest_path = OUTPUT_DIR / "BTC_CORE_COMPLETION_V1_HASH_MANIFEST.json"

    write_json(packet_path, packet)
    write_json(contract_path, controller_contract)
    write_json(metrics_path, metrics)
    write_json(blockers_path, blockers)
    write_csv(
        metric_csv_path,
        [
            {
                "strategy_id": STRATEGY_ID,
                "active_markets": active_markets,
                "market_coverage_rate_vs_15d_288_per_day": active_market_coverage_rate,
                "selected_seed_actions": core["seed_actions"],
                "fee_after_pnl_usd": core["fee_after_pnl"],
                "gross_cost_roi": core["net_roi"],
                "official_taker_fee_usd": core["official_taker_fee"],
                "capital_normalized_roi_300_if_recycling_feasible": metrics["capital_math_300_usd"][
                    "capital_normalized_roi_if_state_machine_recycling_is_feasible"
                ],
                "oos_ready": False,
                "private_truth_ready": False,
                "strategy_promotion_ready": False,
            }
        ],
    )
    note_path.write_text(
        "\n".join(
            [
                "# BTC Core Completion V1 Strategy Review",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet preserves the current best local Backtest V1 strategy candidate: BTC 5m completion-style pairing over public L1 flow source rows. Official Polymarket crypto taker fees are included with the formula `shares * 0.07 * price * (1 - price)`.",
                "",
                "The 15-day local replay-backed accounting is strong as research evidence, but it is not OOS-ready or live-ready. Runtime implementation, public fillability/top-depth, stale-window handling, and owner private truth remain blocked.",
                "",
                "The local replay verifier matched all terminal pair/residual/gross PnL metrics while isolating drift to registry YES post-action inventory display fields. Runtime controllers must not consume those fields.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_preview(preview_path)

    files = [
        packet_path,
        contract_path,
        metrics_path,
        blockers_path,
        metric_csv_path,
        note_path,
        preview_path,
    ]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)

    packet["outputs"] = {
        "packet": str(packet_path),
        "controller_contract": str(contract_path),
        "metrics": str(metrics_path),
        "oos_blockers": str(blockers_path),
        "metric_summary_csv": str(metric_csv_path),
        "hash_manifest": str(hash_manifest_path),
        "hash_manifest_sha256": sha256_file(hash_manifest_path),
    }
    write_json(packet_path, packet)
    hash_manifest["files"][packet_path.name] = {
        "path": str(packet_path),
        "sha256": sha256_file(packet_path),
        "size": packet_path.stat().st_size,
    }
    write_json(hash_manifest_path, hash_manifest)

    print(f"status={STATUS}")
    print(f"output_dir={OUTPUT_DIR}")
    print(f"fee_after_pnl={core['fee_after_pnl']}")
    print(f"gross_cost_roi={core['net_roi']}")
    print(f"capital_roi_300_if_recycling_feasible={metrics['capital_math_300_usd']['capital_normalized_roi_if_state_machine_recycling_is_feasible']}")
    print(f"oos_ready=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

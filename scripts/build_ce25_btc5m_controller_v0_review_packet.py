#!/usr/bin/env python3
"""Build CE25 BTC 5m controller V0 review packet.

This packet translates CE25 public-profile evidence into ex-ante controller
hypotheses and a local replay bridge plan. It does not run replay/OOS/live.
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
LEDGER_DIR = EXPORTS / "ce25_btc5m_broad_profile_candidate_ledger_20260604"
PROBE_DIR = EXPORTS / "ce25_btc5m_exante_feature_probe_20260604"
FRONTIER_DIR = EXPORTS / "ce25_broad_participation_frontier_20260604"
OFFICIAL_FEE_DIR = EXPORTS / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
OUTPUT_DIR = EXPORTS / "ce25_btc5m_controller_v0_review_packet_20260604"

STATUS = "KEEP_CE25_BTC5M_CONTROLLER_V0_REVIEW_PACKET_PREPARED_NOT_OOS_READY"
STRATEGY_OWNER_LINE = "CE25_BROAD_RESEARCH"
STRATEGY_ID = "CE25_BTC5M_BROAD_PARTICIPATION_CONTROLLER_V1"
OFFICIAL_POLYMARKET_CRYPTO_FEE_RATE = 0.07


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def fnum(row: dict[str, str], key: str) -> float:
    try:
        return float(row.get(key) or 0.0)
    except ValueError:
        return 0.0


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


def probe_lookup(rows: list[dict[str, str]], group_id: str, bucket_key: str) -> dict[str, Any]:
    for row in rows:
        if row["group_id"] == group_id and row["bucket_key"] == bucket_key:
            return {
                "group_id": row["group_id"],
                "bucket_key": row["bucket_key"],
                "feature_status": row["feature_status"],
                "market_count": int(float(row["market_count"])),
                "buy_actual": fnum(row, "buy_actual"),
                "cash_pnl": fnum(row, "cash_pnl"),
                "roi": fnum(row, "roi"),
                "pair_cost_weighted": fnum(row, "pair_cost_weighted"),
                "resid_rate": fnum(row, "resid_rate"),
                "win_market_count": int(float(row["win_market_count"])),
                "score": fnum(row, "score"),
            }
    raise SystemExit(f"missing probe row {group_id=} {bucket_key=}")


def controller_specs(probe_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    baseline = probe_lookup(probe_rows, "baseline", "BTC|5m")
    price20_35 = probe_lookup(probe_rows, "source_first_price_bucket", "20-35")
    price65_80 = probe_lookup(probe_rows, "source_first_price_bucket", "65-80")
    last60 = probe_lookup(probe_rows, "source_last_delta_bucket", "last_60s")
    last60_20_35 = probe_lookup(probe_rows, "last_delta_x_first_price", "last_60s|20-35")
    return [
        {
            "controller_id": "BTC5M_BROAD_FIXED_CLOCK_GRID_V0",
            "role": "primary_high_participation_controller",
            "source_probe": baseline,
            "eligible_market_rule": {
                "asset": "BTC",
                "timeframe": "5m",
                "slug_prefix": "btc-updown-5m-",
            },
            "observation_schedule": {
                "clock_offsets_before_close_s": [240, 180, 120, 60, 30],
                "decision_time_contract": "fixed_clock_public_book_only",
            },
            "entry_hypothesis": "observe both YES/NO public book prices and top-depth at fixed clocks; decide participation without CE25 source trade timing",
            "fee_model": {
                "type": "polymarket_official_crypto_taker_fee",
                "fee_rate": OFFICIAL_POLYMARKET_CRYPTO_FEE_RATE,
                "formula": "fee = shares * fee_rate * price * (1 - price)",
                "maker_fee_claim": "not_assumed",
            },
            "capital_stress_plan": {
                "initial_capital_usdc": 300.0,
                "target_notional_ladder_usdc": [2.0, 5.0, 8.0, 15.0],
                "merge_reuse_model": "must_be_verified_in_replay; public profile turnover ROI is not initial-capital ROI",
            },
        },
        {
            "controller_id": "BTC5M_PRICE_20_35_ALPHA_SEED_V0",
            "role": "sub_controller_alpha_seed",
            "source_probe": price20_35,
            "eligible_market_rule": {
                "asset": "BTC",
                "timeframe": "5m",
                "observed_side_price_bucket": "20-35",
            },
            "observation_schedule": {
                "clock_offsets_before_close_s": [240, 180, 120, 60],
                "decision_time_contract": "fixed_clock_public_book_price_bucket",
            },
            "entry_hypothesis": "enter only when public book shows a reviewed side price in [0.20,0.35]; side must be chosen from contemporaneous book rule, not CE25 source_first_side",
            "profile_caveat": "source_first_price_bucket is CE25's realized first-trade profile label until rewritten into fixed-clock observation",
        },
        {
            "controller_id": "BTC5M_PRICE_65_80_RISK_CONTROL_SEED_V0",
            "role": "sub_controller_risk_control_seed",
            "source_probe": price65_80,
            "eligible_market_rule": {
                "asset": "BTC",
                "timeframe": "5m",
                "observed_side_price_bucket": "65-80",
            },
            "observation_schedule": {
                "clock_offsets_before_close_s": [240, 180, 120, 60],
                "decision_time_contract": "fixed_clock_public_book_price_bucket",
            },
            "entry_hypothesis": "test high-price participation only with strict pair-cap, residual cap, and stop/merge reuse audit",
            "profile_caveat": "high ROI/risk-control profile is promising but still public-only; private maker/taker truth is unknown",
        },
        {
            "controller_id": "BTC5M_LAST60_FIXED_CLOCK_REWRITE_V0",
            "role": "late_window_rewrite_candidate",
            "source_probe": last60,
            "supporting_probe": last60_20_35,
            "eligible_market_rule": {
                "asset": "BTC",
                "timeframe": "5m",
                "clock_window": "last_60s",
            },
            "observation_schedule": {
                "clock_offsets_before_close_s": [60, 45, 30, 15],
                "decision_time_contract": "fixed_clock_public_book_only",
            },
            "entry_hypothesis": "rewrite CE25 source_last_delta last_60s profile into explicit public-book observations inside the final minute",
            "profile_caveat": "source_last_delta_bucket is outcome timing unless transformed into a fixed-clock rule; direct reuse must fail closed",
        },
    ]


def replay_bridge_commands() -> list[dict[str, Any]]:
    base = "data/derived/ce25_btc5m_controller_v0_local_replay"
    return [
        {
            "command_id": "build_candidate_base_btc5m_fixed_clock_proxy",
            "execution_status": "NOT_AUTHORIZED_REVIEW_ONLY",
            "command": (
                "uv run --with duckdb python scripts/build_completion_candidate_base.py "
                "--market-prefix btc-updown-5m- --offset-min-s 0 --offset-max-s 300 "
                "--max-pair-cost 1.02 --include-public-sell --include-bid-drop --include-ask-lift "
                f"--output-dir {base}/candidate_base"
            ),
        },
        {
            "command_id": "run_state_machine_official_fee_target_qty8",
            "execution_status": "NOT_AUTHORIZED_REVIEW_ONLY",
            "command": (
                "uv run --with duckdb python scripts/run_completion_candidate_state_machine.py "
                f"--candidate-base-dir {base}/candidate_base --output-dir {base}/state_machine_qty8 "
                "--target-qty 8 --max-open-cost 300 --fee-model official_taker --official-fee-rate 0.07 "
                "--seed-l1-pair-cap 1.02 --residual-cooldown-age-s 30 --residual-cooldown-cost-cap 0.5"
            ),
        },
        {
            "command_id": "run_state_machine_official_fee_target_ladder",
            "execution_status": "NOT_AUTHORIZED_REVIEW_ONLY",
            "command": (
                "for QTY in 2 5 8 15; do "
                "uv run --with duckdb python scripts/run_completion_candidate_state_machine.py "
                f"--candidate-base-dir {base}/candidate_base --output-dir {base}/state_machine_qty_${{QTY}} "
                "--target-qty ${QTY} --max-open-cost 300 --fee-model official_taker --official-fee-rate 0.07 "
                "--seed-l1-pair-cap 1.02 --residual-cooldown-age-s 30 --residual-cooldown-cost-cap 0.5; "
                "done"
            ),
        },
    ]


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ledger_summary_path = LEDGER_DIR / "CE25_BTC5M_BROAD_PROFILE_CANDIDATE_LEDGER_SUMMARY.json"
    feature_probe_summary_path = PROBE_DIR / "CE25_BTC5M_EXANTE_FEATURE_PROBE_SUMMARY.json"
    feature_probe_tsv_path = PROBE_DIR / "ce25_btc5m_exante_feature_probe.tsv"
    official_fee_summary_path = OFFICIAL_FEE_DIR / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json"
    frontier_summary_path = FRONTIER_DIR / "CE25_BROAD_PARTICIPATION_FRONTIER_SUMMARY.json"

    ledger_summary = read_json(ledger_summary_path)
    feature_summary = read_json(feature_probe_summary_path)
    official_fee_summary = read_json(official_fee_summary_path)
    probe_rows = read_tsv(feature_probe_tsv_path)

    packet = {
        "schema_version": 1,
        "status": STATUS,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strategy_owner_line": STRATEGY_OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "source_binding": {
            "ledger_summary_sha256": sha256_file(ledger_summary_path),
            "ledger_csv_sha256": sha256_file(LEDGER_DIR / "ce25_btc5m_broad_profile_candidate_ledger.csv"),
            "field_classification_sha256": sha256_file(LEDGER_DIR / "CE25_BTC5M_BROAD_FIELD_CLASSIFICATION.json"),
            "feature_probe_summary_sha256": sha256_file(feature_probe_summary_path),
            "feature_probe_tsv_sha256": sha256_file(feature_probe_tsv_path),
            "frontier_summary_sha256": sha256_file(frontier_summary_path),
            "official_fee_summary_sha256": sha256_file(official_fee_summary_path),
            "build_completion_candidate_base_sha256": sha256_file(ROOT / "scripts" / "build_completion_candidate_base.py"),
            "run_completion_candidate_state_machine_sha256": sha256_file(ROOT / "scripts" / "run_completion_candidate_state_machine.py"),
        },
        "public_profile_context": {
            "candidate_count": ledger_summary["candidate_count"],
            "market_count": ledger_summary["market_count"],
            "coverage_rate": ledger_summary["coverage_rate"],
            "latest_window_coverage_rate": ledger_summary["latest_window_coverage_rate"],
            "cash_pnl": ledger_summary["cash_pnl"],
            "roi": ledger_summary["roi"],
            "resid_rate": ledger_summary["resid_rate"],
            "official_fee_rate_used_for_target_qty8_recalc": official_fee_summary["official_crypto_taker_fee_rate"],
            "target_qty8_official_fee_cash_pnl": official_fee_summary["cash_pnl_official_fee_0p07"],
        },
        "field_contract": {
            "allowed_entry_inputs": [
                "asset",
                "timeframe",
                "slug",
                "market_start_s",
                "market_end_s",
                "fixed_clock_seconds_to_close",
                "public_book_yes_price",
                "public_book_no_price",
                "public_book_top_depth",
                "spread",
            ],
            "profile_labels_for_training_or_review_only": [
                "source_first_price_bucket",
                "source_first_delta_bucket",
                "source_last_delta_bucket",
                "source_first_side",
            ],
            "forbidden_entry_inputs": [
                "source_pair_delay_bucket",
                "source_cash_pnl",
                "source_roi",
                "source_pair_cost",
                "source_resid_side",
                "source_resid_rate",
                "winner",
                "private_order_truth",
            ],
        },
        "controller_specs": controller_specs(probe_rows),
        "replay_bridge_plan": {
            "purpose": "local replay bridge only; convert fixed-clock public-book controller specs into candidate base and state-machine replay",
            "commands": replay_bridge_commands(),
            "must_preserve": [
                "official crypto taker feeRate 0.07",
                "300 USDC initial-capital stress",
                "merge/redeem capital reuse accounting",
                "candidate/source row coverage audit",
                "no private truth or live order path",
            ],
        },
        "decision": {
            "primary_next_step": "implement local replay bridge for BTC5M_BROAD_FIXED_CLOCK_GRID_V0 and 20-35/65-80 sub-controllers",
            "do_not_continue": [
                "target_qty8 as primary strategy; keep only as seed/filter",
                "9F5F/Username123123 account chasing before CE25 BTC5M replay",
                "direct OOS from public profile labels",
            ],
        },
        "highest_allowed_status": STATUS,
        "non_claims": non_claims(),
        "feature_probe_top_rows": feature_summary["top_probe_rows"][:8],
    }

    packet_path = OUTPUT_DIR / "CE25_BTC5M_CONTROLLER_V0_REVIEW_PACKET.json"
    packet_path.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    command_path = OUTPUT_DIR / "COMMAND_PREVIEW_NOT_AUTHORIZED.sh"
    command_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                "set -euo pipefail",
                "echo 'NOT_AUTHORIZED: CE25 BTC5M controller V0 is review-only; replay/OOS/live runner is not authorized.' >&2",
                "exit 66",
                "",
            ]
        ),
        encoding="utf-8",
    )
    command_path.chmod(0o755)

    note_path = OUTPUT_DIR / "CE25_BTC5M_CONTROLLER_V0_BOUNDARY_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 BTC 5m Controller V0 Boundary",
                "",
                f"Status: `{STATUS}`",
                "",
                "This packet turns CE25 public-profile BTC 5m evidence into fixed-clock public-book controller hypotheses.",
                "It is not a backtest result, not OOS, not private truth, and not live/deployable.",
                "Profile timing/outcome fields must be rewritten into deterministic observation rules before replay/OOS.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    artifacts = [
        packet_path,
        command_path,
        note_path,
        ledger_summary_path,
        feature_probe_summary_path,
        feature_probe_tsv_path,
        frontier_summary_path,
        official_fee_summary_path,
        ROOT / "scripts" / "build_ce25_btc5m_controller_v0_review_packet.py",
    ]
    manifest = {
        "schema_version": 1,
        "status": STATUS,
        "artifacts": [
            {
                "path": str(path),
                "sha256": sha256_file(path),
                "size_bytes": path.stat().st_size,
            }
            for path in artifacts
        ],
        "packet_sha256": sha256_file(packet_path),
        "command_preview_exit_code": 66,
        "non_claims": non_claims(),
    }
    manifest_path = OUTPUT_DIR / "CE25_BTC5M_CONTROLLER_V0_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": True,
                "status": STATUS,
                "output_dir": str(OUTPUT_DIR),
                "packet_sha256": sha256_file(packet_path),
                "manifest_sha256": sha256_file(manifest_path),
                "candidate_count": ledger_summary["candidate_count"],
                "controller_count": len(packet["controller_specs"]),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

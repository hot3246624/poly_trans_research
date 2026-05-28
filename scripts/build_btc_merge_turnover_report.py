#!/usr/bin/env python3
"""Build BTC pair-merge/redeem turnover attribution for the completion adapter."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_BTC_ADAPTER = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_state_machine_from_l1_flow_taker_normalized_v1"
)
DEFAULT_RESCUE_LEDGER = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_rescue_adjusted_capital_ledger_latest/BTC_RESCUE_ADJUSTED_CAPITAL_LEDGER.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc-adapter-dir", type=Path, default=DEFAULT_BTC_ADAPTER)
    parser.add_argument("--rescue-ledger", type=Path, default=DEFAULT_RESCUE_LEDGER)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_merge_turnover_latest",
    )
    args = parser.parse_args()

    adapter_manifest_path = args.btc_adapter_dir.expanduser() / "RESULT_SUMMARY_MANIFEST.json"
    adapter = read_json(adapter_manifest_path)
    metrics = adapter.get("core_metrics") or {}
    rescue_ledger_path = args.rescue_ledger.expanduser()
    rescue_ledger = read_json(rescue_ledger_path)
    scenarios = rescue_ledger.get("scenarios") or {}
    rescue_all = scenarios.get("strict_rescue_all_best_quote") or {}

    gross_buy_cost = float(metrics.get("gross_buy_cost") or 0.0)
    pair_qty = float(metrics.get("pair_qty") or 0.0)
    pair_cost_sum = pair_qty - float(metrics.get("pair_pnl") or 0.0)
    residual_cost = float(metrics.get("residual_cost") or 0.0)
    settlement_payout = float(metrics.get("residual_settle_payout") or 0.0)
    rescue_residual_pnl = float(rescue_all.get("residual_pnl") or 0.0)
    rescue_recovery = residual_cost + rescue_residual_pnl
    active_markets = float(metrics.get("active_markets") or 0.0)
    pair_actions = float(metrics.get("pair_actions") or 0.0)

    baseline_return = pair_qty + settlement_payout
    rescue_return = pair_qty + rescue_recovery
    report = {
        "schema_version": "btc_merge_turnover_report_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_MERGE_TURNOVER_READY",
        "btc_adapter_manifest": str(adapter_manifest_path),
        "rescue_adjusted_ledger": str(rescue_ledger_path),
        "metrics": {
            "gross_buy_cost": rounded(gross_buy_cost),
            "pair_qty": rounded(pair_qty),
            "pair_cost_sum": rounded(pair_cost_sum),
            "pair_merge_redeem_value": rounded(pair_qty),
            "pair_pnl": rounded(float(metrics.get("pair_pnl") or 0.0)),
            "pair_actions": int(pair_actions),
            "active_markets": int(active_markets),
            "rounds_per_market": rounded(pair_actions / active_markets) if active_markets else None,
            "residual_cost": rounded(residual_cost),
            "settlement_payout": rounded(settlement_payout),
            "strict_rescue_recovery_value": rounded(rescue_recovery),
            "capital_return_settlement": rounded(baseline_return),
            "capital_return_strict_rescue": rounded(rescue_return),
            "capital_return_settlement_over_gross_cost": rounded(baseline_return / gross_buy_cost)
            if gross_buy_cost
            else None,
            "capital_return_strict_rescue_over_gross_cost": rounded(rescue_return / gross_buy_cost)
            if gross_buy_cost
            else None,
            "pair_merge_value_over_gross_cost": rounded(pair_qty / gross_buy_cost) if gross_buy_cost else None,
            "residual_cost_share": rounded(residual_cost / gross_buy_cost) if gross_buy_cost else None,
        },
        "semantics": {
            "pair_merge_redeem_value": "For each paired YES/NO share, merge/redeem returns 1.0 quote currency before fees already accounted in seed fills.",
            "capital_return_settlement": "pair_merge_redeem_value plus residual settlement payout.",
            "capital_return_strict_rescue": "pair_merge_redeem_value plus strict-rescue residual recovery scenario.",
            "research_only": True,
            "private_truth": False,
        },
    }
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "BTC_MERGE_TURNOVER_REPORT.json"
    manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "metrics": report["metrics"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

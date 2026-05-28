#!/usr/bin/env python3
"""Build rescue-adjusted BTC capital ledger scenarios from the BTC adapter."""

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
DEFAULT_RESCUE_REPORT = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/btc_strict_rescue_opportunity_latest/BTC_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc-adapter-dir", type=Path, default=DEFAULT_BTC_ADAPTER)
    parser.add_argument("--rescue-report", type=Path, default=DEFAULT_RESCUE_REPORT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_rescue_adjusted_capital_ledger_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    btc_adapter_dir = args.btc_adapter_dir.expanduser()
    adapter_manifest_path = btc_adapter_dir / "RESULT_SUMMARY_MANIFEST.json"
    adapter_manifest = read_json(adapter_manifest_path)
    adapter_metrics = adapter_manifest.get("core_metrics") or {}
    rescue_report_path = args.rescue_report.expanduser()
    rescue_report = read_json(rescue_report_path)
    rescue_csv = Path(str((rescue_report.get("outputs") or {}).get("csv") or "")).expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "btc_rescue_adjusted_capital_ledger.duckdb"

    con = duckdb.connect(str(output_db))
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE rescue_lots AS
            SELECT * FROM read_csv_auto({quote(rescue_csv)}, HEADER=TRUE)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE rescue_adjusted_lots AS
            SELECT
              *,
              best_after_fee_recovery_value - settlement_payout AS rescue_vs_settlement_delta,
              greatest(best_after_fee_recovery_value, settlement_payout) AS oracle_best_residual_value,
              greatest(best_after_fee_recovery_value, settlement_payout) - cost AS oracle_best_residual_pnl
            FROM rescue_lots
            """
        )
        row = con.execute(
            """
            SELECT
              count(*) AS residual_lot_count,
              sum(cost) AS residual_cost,
              sum(settlement_payout) AS settlement_payout,
              sum(settlement_pnl) AS settlement_residual_pnl,
              sum(best_after_fee_recovery_value) AS rescue_all_recovery_value,
              sum(best_after_fee_rescue_pnl) AS rescue_all_residual_pnl,
              sum(oracle_best_residual_value) AS oracle_residual_value,
              sum(oracle_best_residual_pnl) AS oracle_residual_pnl,
              sum(greatest(rescue_vs_settlement_delta, 0.0)) AS oracle_incremental_rescue_value,
              count(*) FILTER (WHERE break_even_after_fee_seen) AS break_even_lots,
              count(*) FILTER (WHERE rescue_beats_settlement) AS rescue_beats_settlement_lots,
              quantile_cont(best_rescue_delay_ms, 0.5) AS p50_rescue_delay_ms,
              quantile_cont(best_rescue_delay_ms, 0.9) AS p90_rescue_delay_ms
            FROM rescue_adjusted_lots
            """
        ).fetchone()
        names = [item[0] for item in con.description]
        lot_summary = dict(zip(names, row))
        con.execute(
            f"COPY rescue_adjusted_lots TO {quote(output_dir / 'btc_rescue_adjusted_lots.csv')} (HEADER, DELIMITER ',')"
        )
        con.execute(
            f"COPY rescue_adjusted_lots TO {quote(output_dir / 'btc_rescue_adjusted_lots.parquet')} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    pair_pnl = float(adapter_metrics.get("pair_pnl") or 0.0)
    total_fee = float(adapter_metrics.get("official_taker_fee") or 0.0)
    gross_buy_cost = float(adapter_metrics.get("gross_buy_cost") or 0.0)
    settlement_residual_pnl = float(lot_summary.get("settlement_residual_pnl") or 0.0)
    rescue_all_residual_pnl = float(lot_summary.get("rescue_all_residual_pnl") or 0.0)
    oracle_residual_pnl = float(lot_summary.get("oracle_residual_pnl") or 0.0)

    scenarios = {
        "adapter_settlement_baseline": {
            "pair_pnl": rounded(pair_pnl),
            "residual_pnl": rounded(settlement_residual_pnl),
            "total_fee": rounded(total_fee),
            "fee_after_pnl": rounded(pair_pnl + settlement_residual_pnl - total_fee),
            "net_roi": rounded((pair_pnl + settlement_residual_pnl - total_fee) / gross_buy_cost)
            if gross_buy_cost
            else None,
        },
        "strict_rescue_all_best_quote": {
            "pair_pnl": rounded(pair_pnl),
            "residual_pnl": rounded(rescue_all_residual_pnl),
            "total_fee": rounded(total_fee),
            "fee_after_pnl": rounded(pair_pnl + rescue_all_residual_pnl - total_fee),
            "net_roi": rounded((pair_pnl + rescue_all_residual_pnl - total_fee) / gross_buy_cost)
            if gross_buy_cost
            else None,
            "incremental_fee_after_pnl_vs_baseline": rounded(rescue_all_residual_pnl - settlement_residual_pnl),
        },
        "oracle_rescue_if_beats_settlement": {
            "pair_pnl": rounded(pair_pnl),
            "residual_pnl": rounded(oracle_residual_pnl),
            "total_fee": rounded(total_fee),
            "fee_after_pnl": rounded(pair_pnl + oracle_residual_pnl - total_fee),
            "net_roi": rounded((pair_pnl + oracle_residual_pnl - total_fee) / gross_buy_cost)
            if gross_buy_cost
            else None,
            "incremental_fee_after_pnl_vs_baseline": rounded(oracle_residual_pnl - settlement_residual_pnl),
        },
    }

    manifest = {
        "schema_version": "btc_rescue_adjusted_capital_ledger_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_RESCUE_ADJUSTED_LEDGER_READY",
        "btc_adapter_manifest": str(adapter_manifest_path),
        "rescue_report": str(rescue_report_path),
        "outputs": {
            "duckdb": str(output_db),
            "table": "rescue_adjusted_lots",
            "csv": str(output_dir / "btc_rescue_adjusted_lots.csv"),
            "parquet": str(output_dir / "btc_rescue_adjusted_lots.parquet"),
        },
        "lot_summary": lot_summary,
        "scenarios": scenarios,
        "semantics": {
            "adapter_settlement_baseline": "Original BTC adapter residual lots settle by final winner.",
            "strict_rescue_all_best_quote": "Research upper-bound close: every residual lot closes at its best top-aligned after-fee bid inside the rescue window.",
            "oracle_rescue_if_beats_settlement": "Hindsight upper bound: choose rescue only when it beats settlement; not deployable.",
            "private_truth": False,
            "deployable": False,
        },
    }
    manifest_path = output_dir / "BTC_RESCUE_ADJUSTED_CAPITAL_LEDGER.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "scenarios": scenarios}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

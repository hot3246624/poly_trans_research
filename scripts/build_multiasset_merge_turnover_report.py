#!/usr/bin/env python3
"""Build multiasset pair-merge/redeem turnover and residual attribution."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_ADAPTER = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"
DEFAULT_RESCUE_REPORT = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/multiasset_strict_rescue_opportunity_latest/"
    / "MULTIASSET_STRICT_RESCUE_OPPORTUNITY_REPORT.json"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--strict-rescue-report", type=Path, default=DEFAULT_RESCUE_REPORT)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_merge_turnover_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    adapter_dir = args.adapter_dir.expanduser()
    adapter_manifest_path = adapter_dir / "RESULT_SUMMARY_MANIFEST.json"
    adapter = read_json(adapter_manifest_path)
    metrics = adapter.get("core_metrics") or {}
    rescue_report_path = args.strict_rescue_report.expanduser()
    rescue_report = read_json(rescue_report_path)
    rescue_summary = rescue_report.get("summary") or {}
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    actions_csv = adapter_dir / "actions.csv"
    residual_csv = adapter_dir / "residual_lots.csv"
    con = duckdb.connect()
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE actions AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed
            FROM read_csv_auto({quote(actions_csv)}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE residuals AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              CAST(qty AS DOUBLE) AS qty,
              CAST(cost AS DOUBLE) AS cost,
              CAST(payout AS DOUBLE) AS payout,
              CAST(pnl AS DOUBLE) AS pnl
            FROM read_csv_auto({quote(residual_csv)}, HEADER=TRUE)
            """
        )
        asset_rows = con.execute(
            """
            WITH action_by_asset AS (
              SELECT
                asset,
                count(*) AS selected_seed_actions,
                sum(seed_cost) AS gross_buy_cost,
                sum(pair_qty_after_seed) AS paired_mergeable_qty,
                sum(pair_actions_after_seed) AS pair_actions
              FROM actions
              GROUP BY 1
            ),
            residual_by_asset AS (
              SELECT
                asset,
                count(*) AS residual_lot_count,
                sum(qty) AS market_end_residual_qty,
                sum(cost) AS market_end_residual_cost,
                sum(payout) AS actual_settlement_residual_payout,
                sum(pnl) AS actual_settlement_residual_pnl
              FROM residuals
              GROUP BY 1
            )
            SELECT
              coalesce(a.asset, r.asset) AS asset,
              a.selected_seed_actions,
              a.gross_buy_cost,
              a.paired_mergeable_qty,
              a.pair_actions,
              r.residual_lot_count,
              r.market_end_residual_qty,
              r.market_end_residual_cost,
              r.actual_settlement_residual_payout,
              r.actual_settlement_residual_pnl
            FROM action_by_asset a
            FULL OUTER JOIN residual_by_asset r USING (asset)
            ORDER BY asset
            """
        ).fetchall()
        columns = [item[0] for item in con.description]
    finally:
        con.close()

    by_asset: dict[str, dict[str, Any]] = {}
    for raw in asset_rows:
        row = dict(zip(columns, raw))
        gross = float(row.get("gross_buy_cost") or 0.0)
        paired = float(row.get("paired_mergeable_qty") or 0.0)
        residual_cost = float(row.get("market_end_residual_cost") or 0.0)
        residual_qty = float(row.get("market_end_residual_qty") or 0.0)
        row["merge_recovered_capital"] = paired
        row["paired_mergeable_cost_proxy"] = max(gross - residual_cost, 0.0)
        row["capital_turnover"] = paired / gross if gross else None
        row["market_end_residual_cost_share"] = residual_cost / gross if gross else None
        row["market_end_residual_qty_share"] = residual_qty / (paired + residual_qty) if (paired + residual_qty) else None
        row["residual_zero_stress_loss"] = -residual_cost
        by_asset[str(row["asset"])] = {
            key: rounded(float(value)) if isinstance(value, float) else value
            for key, value in row.items()
        }

    gross_buy_cost = float(metrics.get("gross_buy_cost") or 0.0)
    paired_mergeable_qty = float(metrics.get("pair_qty") or 0.0)
    pair_pnl = float(metrics.get("pair_pnl") or 0.0)
    paired_mergeable_cost = paired_mergeable_qty - pair_pnl
    residual_cost = float(metrics.get("residual_cost") or 0.0)
    residual_qty = float(metrics.get("residual_qty") or 0.0)
    settlement_payout = float(metrics.get("residual_settle_payout") or 0.0)
    settlement_residual_pnl = float(metrics.get("residual_settle_pnl") or 0.0)
    active_markets = float(metrics.get("active_markets") or 0.0)
    pair_actions = float(metrics.get("pair_actions") or 0.0)
    strict_rescue_recovery_value = rescue_summary.get("best_after_fee_recovery_value")
    strict_rescue_pnl = rescue_summary.get("best_after_fee_rescue_pnl")

    report = {
        "schema_version": "multiasset_merge_turnover_report_v1",
        "created_utc": utc_now(),
        "status": "OK_MULTIASSET_MERGE_TURNOVER_READY",
        "adapter_manifest": str(adapter_manifest_path),
        "adapter_status": adapter.get("status") if adapter else "MISSING",
        "strict_rescue_report": str(rescue_report_path),
        "strict_rescue_status": rescue_report.get("status") if rescue_report else "MISSING",
        "metrics": {
            "gross_buy_cost": rounded(gross_buy_cost),
            "selected_candidate_count": metrics.get("selected_candidate_count"),
            "pair_actions": int(pair_actions),
            "active_markets": int(active_markets),
            "rounds_per_market": rounded(pair_actions / active_markets) if active_markets else None,
            "paired_mergeable_qty": rounded(paired_mergeable_qty),
            "paired_mergeable_cost": rounded(paired_mergeable_cost),
            "merge_recovered_capital": rounded(paired_mergeable_qty),
            "capital_turnover": rounded(paired_mergeable_qty / gross_buy_cost) if gross_buy_cost else None,
            "pair_pnl": rounded(pair_pnl),
            "market_end_residual_qty": rounded(residual_qty),
            "market_end_residual_cost": rounded(residual_cost),
            "market_end_residual_cost_share": rounded(residual_cost / gross_buy_cost) if gross_buy_cost else None,
            "market_end_residual_qty_share": rounded(residual_qty / (paired_mergeable_qty + residual_qty))
            if (paired_mergeable_qty + residual_qty)
            else None,
            "residual_zero_stress_loss": rounded(-residual_cost),
            "actual_settlement_residual_payout": rounded(settlement_payout),
            "actual_settlement_residual_pnl": rounded(settlement_residual_pnl),
            "strict_rescue_recovery_value": rounded(float(strict_rescue_recovery_value))
            if strict_rescue_recovery_value is not None
            else None,
            "strict_rescue_pnl": rounded(float(strict_rescue_pnl)) if strict_rescue_pnl is not None else None,
            "capital_return_settlement": rounded(paired_mergeable_qty + settlement_payout),
            "capital_return_strict_rescue": rounded(paired_mergeable_qty + float(strict_rescue_recovery_value))
            if strict_rescue_recovery_value is not None
            else None,
            "capital_return_settlement_over_gross_cost": rounded((paired_mergeable_qty + settlement_payout) / gross_buy_cost)
            if gross_buy_cost
            else None,
            "capital_return_strict_rescue_over_gross_cost": rounded(
                (paired_mergeable_qty + float(strict_rescue_recovery_value)) / gross_buy_cost
            )
            if gross_buy_cost and strict_rescue_recovery_value is not None
            else None,
        },
        "by_asset": by_asset,
        "semantics": {
            "paired_mergeable_qty": "min(YES inventory, NO inventory) matched inside the adapter pair cycle.",
            "merge_recovered_capital": "Matched pair quantity redeemable at 1.0; this is merge capital recovery, not residual edge.",
            "market_end_residual_qty": "abs(YES inventory - NO inventory) unpaired market-end inventory risk.",
            "residual_zero_stress_loss": "Stress attribution if market-end residual is marked to zero; not the realized strategy edge.",
            "actual_settlement_residual_pnl": "Settlement attribution for residual lots; usable for ex-post accounting only.",
            "redeem_is_settlement_action_not_strategy_edge": True,
            "research_only": True,
            "private_truth_ready": False,
            "private_promotion_ready_count": 0,
            "deployable": False,
            "live_orders_allowed": False,
        },
    }
    manifest_path = output_dir / "MULTIASSET_MERGE_TURNOVER_REPORT.json"
    manifest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "metrics": report["metrics"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

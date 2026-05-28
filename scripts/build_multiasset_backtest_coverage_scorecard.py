#!/usr/bin/env python3
"""Build per-asset coverage and adapter outcome scorecard for Backtest V1."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_COVERAGE = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_backtest_coverage_latest/MULTIASSET_BACKTEST_COVERAGE.json"
DEFAULT_ADAPTER = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def rounded(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None


def clean_value(value: Any) -> Any:
    if isinstance(value, float):
        return rounded(value)
    if isinstance(value, (dt.date, dt.datetime)):
        return value.isoformat()
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--coverage-json", type=Path, default=DEFAULT_COVERAGE)
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_backtest_coverage_scorecard_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    coverage_path = args.coverage_json.expanduser()
    adapter_dir = args.adapter_dir.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    coverage = read_json(coverage_path)
    coverage_by_asset = {str(row.get("asset")): row for row in coverage.get("by_asset") or []}

    con = duckdb.connect()
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE actions AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              slug,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed,
              CAST(coalesce(official_taker_fee, fee, 0) AS DOUBLE) AS fee
            FROM read_csv_auto({quote(adapter_dir / 'actions.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE residuals AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              CAST(qty AS DOUBLE) AS qty,
              CAST(cost AS DOUBLE) AS cost,
              CAST(payout AS DOUBLE) AS payout,
              CAST(pnl AS DOUBLE) AS pnl
            FROM read_csv_auto({quote(adapter_dir / 'residual_lots.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TEMP TABLE daily AS
            WITH a AS (
              SELECT
                asset,
                day,
                count(*) AS selected_count,
                count(DISTINCT condition_id) AS selected_market_count,
                sum(seed_cost) AS gross_buy_cost,
                sum(pair_qty_after_seed) AS pair_qty,
                sum(pair_actions_after_seed) AS pair_actions,
                sum(fee) AS official_taker_fee
              FROM actions
              GROUP BY 1, 2
            ),
            r AS (
              SELECT
                asset,
                day,
                count(*) AS residual_lot_count,
                sum(qty) AS residual_qty,
                sum(cost) AS residual_cost,
                sum(payout) AS residual_settlement_payout,
                sum(pnl) AS residual_settlement_pnl
              FROM residuals
              GROUP BY 1, 2
            )
            SELECT
              coalesce(a.asset, r.asset) AS asset,
              coalesce(a.day, r.day) AS day,
              coalesce(selected_count, 0) AS selected_count,
              coalesce(selected_market_count, 0) AS selected_market_count,
              coalesce(gross_buy_cost, 0.0) AS gross_buy_cost,
              coalesce(pair_qty, 0.0) AS pair_qty,
              coalesce(pair_actions, 0) AS pair_actions,
              coalesce(official_taker_fee, 0.0) AS official_taker_fee,
              coalesce(residual_lot_count, 0) AS residual_lot_count,
              coalesce(residual_qty, 0.0) AS residual_qty,
              coalesce(residual_cost, 0.0) AS residual_cost,
              coalesce(residual_settlement_payout, 0.0) AS residual_settlement_payout,
              coalesce(residual_settlement_pnl, 0.0) AS residual_settlement_pnl,
              coalesce(pair_qty, 0.0) - (coalesce(gross_buy_cost, 0.0) - coalesce(residual_cost, 0.0)) AS pair_pnl,
              (
                coalesce(pair_qty, 0.0)
                - (coalesce(gross_buy_cost, 0.0) - coalesce(residual_cost, 0.0))
                + coalesce(residual_settlement_pnl, 0.0)
                - coalesce(official_taker_fee, 0.0)
              ) AS fee_after_pnl
            FROM a
            FULL OUTER JOIN r USING (asset, day)
            """
        )
        rows = con.execute(
            """
            SELECT
              asset,
              count(DISTINCT day) AS adapter_day_count,
              sum(selected_count) AS selected_count,
              sum(selected_market_count) AS selected_market_count,
              sum(gross_buy_cost) AS gross_buy_cost,
              sum(pair_qty) AS pair_qty,
              sum(pair_actions) AS pair_actions,
              sum(official_taker_fee) AS official_taker_fee,
              sum(residual_lot_count) AS residual_lot_count,
              sum(residual_qty) AS residual_qty,
              sum(residual_cost) AS residual_cost,
              sum(residual_settlement_payout) AS residual_settlement_payout,
              sum(residual_settlement_pnl) AS residual_settlement_pnl,
              sum(pair_pnl) AS pair_pnl,
              sum(fee_after_pnl) AS fee_after_pnl,
              min(fee_after_pnl) AS stress_worst_day_fee_after_pnl,
              arg_min(day, fee_after_pnl) AS stress_worst_day
            FROM daily
            GROUP BY 1
            ORDER BY 1
            """
        ).fetchall()
        columns = [item[0] for item in con.description]
        con.execute(
            f"COPY daily TO {quote(output_dir / 'multiasset_backtest_coverage_scorecard_by_day.csv')} "
            "(HEADER, DELIMITER ',')"
        )
    finally:
        con.close()

    by_asset: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(zip(columns, raw))
        asset = str(row["asset"])
        cov = coverage_by_asset.get(asset, {})
        gross = float(row.get("gross_buy_cost") or 0.0)
        row.update(
            {
                "search_safe_row_count": cov.get("row_count"),
                "search_safe_market_count": cov.get("market_count"),
                "search_safe_day_count": cov.get("day_count"),
                "net_roi": (float(row.get("fee_after_pnl") or 0.0) / gross) if gross else None,
                "residual_cost_share": (float(row.get("residual_cost") or 0.0) / gross) if gross else None,
            }
        )
        by_asset.append({key: clean_value(value) for key, value in row.items()})

    total_gross = sum(float(row.get("gross_buy_cost") or 0.0) for row in by_asset)
    total_fee_after = sum(float(row.get("fee_after_pnl") or 0.0) for row in by_asset)
    manifest = {
        "schema_version": "multiasset_backtest_coverage_scorecard_v1",
        "created_utc": utc_now(),
        "status": "OK_MULTIASSET_BACKTEST_COVERAGE_SCORECARD_READY",
        "coverage_json": str(coverage_path),
        "adapter_dir": str(adapter_dir),
        "summary": {
            "asset_count": len(by_asset),
            "search_safe_total_rows": coverage.get("total_rows"),
            "adapter_gross_buy_cost": rounded(total_gross),
            "adapter_fee_after_pnl": rounded(total_fee_after),
            "adapter_net_roi": rounded(total_fee_after / total_gross) if total_gross else None,
            "uneven_coverage_warning": "Do not interpret 7 assets as equal-weight samples; use per-asset row/market/day counts.",
        },
        "by_asset": by_asset,
        "outputs": {
            "by_day_csv": str(output_dir / "multiasset_backtest_coverage_scorecard_by_day.csv"),
        },
        "private_truth_ready": False,
        "deployable": False,
        "live_orders_allowed": False,
    }
    (output_dir / "MULTIASSET_BACKTEST_COVERAGE_SCORECARD.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": manifest["status"], "summary": manifest["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

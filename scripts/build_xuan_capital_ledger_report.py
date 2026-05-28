#!/usr/bin/env python3
"""Build capital-at-risk ledger metrics for xuan Backtest V1 adapter."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_ADAPTER = DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_state_machine_from_l1_flow_v1"
DEFAULT_RESCORING = (
    DEFAULT_DATA_ROOT
    / "derived/contract_examples/xuan_completion_candidate_rescore_latest/XUAN_COMPLETION_CANDIDATE_RESCORE_MANIFEST.json"
)


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
    parser.add_argument("--adapter-dir", type=Path, default=DEFAULT_ADAPTER)
    parser.add_argument("--candidate-rescore-manifest", type=Path, default=DEFAULT_RESCORING)
    parser.add_argument("--capacity-notional", type=float, default=1000.0)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/xuan_capital_ledger_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    adapter_dir = args.adapter_dir.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    out_db = output_dir / "xuan_capital_ledger.duckdb"
    rescore = read_json(args.candidate_rescore_manifest.expanduser())

    con = duckdb.connect(str(out_db))
    try:
        con.execute(
            f"""
            CREATE OR REPLACE TABLE actions AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              condition_id,
              slug,
              CAST(ts_ms AS BIGINT) AS ts_ms,
              CAST(seed_cost AS DOUBLE) AS seed_cost,
              CAST(pair_qty_after_seed AS DOUBLE) AS pair_qty_after_seed,
              CAST(pair_actions_after_seed AS BIGINT) AS pair_actions_after_seed,
              CAST(coalesce(official_taker_fee, fee, 0) AS DOUBLE) AS fee,
              CAST(inventory_yes_cost_after AS DOUBLE) + CAST(inventory_no_cost_after AS DOUBLE) AS capital_tied_after
            FROM read_csv_auto({quote(adapter_dir / 'actions.csv')}, HEADER=TRUE)
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE action_deltas AS
            SELECT
              *,
              capital_tied_after
                - coalesce(lag(capital_tied_after) OVER (PARTITION BY condition_id ORDER BY ts_ms), 0.0)
                AS capital_tied_delta
            FROM actions
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE global_capital_curve AS
            SELECT
              ts_ms,
              day,
              sum(capital_tied_delta) OVER (ORDER BY ts_ms ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                AS capital_tied
            FROM action_deltas
            ORDER BY ts_ms
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE asset_capital_curve AS
            SELECT
              asset,
              ts_ms,
              day,
              sum(capital_tied_delta) OVER (
                PARTITION BY asset ORDER BY ts_ms ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
              ) AS capital_tied
            FROM action_deltas
            ORDER BY asset, ts_ms
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE residuals AS
            SELECT
              upper(split_part(slug, '-', 1)) AS asset,
              day,
              sum(CAST(pnl AS DOUBLE)) AS residual_settlement_pnl,
              sum(CAST(cost AS DOUBLE)) AS residual_cost,
              sum(CAST(qty AS DOUBLE)) AS residual_qty
            FROM read_csv_auto({quote(adapter_dir / 'residual_lots.csv')}, HEADER=TRUE)
            GROUP BY 1, 2
            """
        )
        con.execute(
            """
            CREATE OR REPLACE TABLE pnl_by_asset_day AS
            WITH a AS (
              SELECT
                asset,
                day,
                sum(seed_cost) AS gross_buy_cost,
                sum(pair_qty_after_seed) AS paired_mergeable_qty,
                sum(pair_actions_after_seed) AS pair_actions,
                sum(fee) AS official_taker_fee,
                sum(seed_cost) - coalesce(sum(pair_qty_after_seed), 0.0) AS seed_minus_merge_proxy
              FROM actions
              GROUP BY 1, 2
            )
            SELECT
              a.asset,
              a.day,
              a.gross_buy_cost,
              a.paired_mergeable_qty,
              a.pair_actions,
              a.official_taker_fee,
              coalesce(r.residual_cost, 0.0) AS residual_cost,
              coalesce(r.residual_qty, 0.0) AS residual_qty,
              coalesce(r.residual_settlement_pnl, 0.0) AS residual_settlement_pnl,
              a.paired_mergeable_qty - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0)) AS pair_pnl,
              (
                a.paired_mergeable_qty
                - (a.gross_buy_cost - coalesce(r.residual_cost, 0.0))
                + coalesce(r.residual_settlement_pnl, 0.0)
                - a.official_taker_fee
              ) AS fee_after_pnl
            FROM a
            LEFT JOIN residuals r USING (asset, day)
            """
        )
        global_capital = con.execute(
            """
            SELECT
              max(capital_tied) AS max_capital_tied,
              avg(capital_tied) AS average_capital_tied,
              quantile_cont(capital_tied, 0.95) AS p95_capital_tied
            FROM global_capital_curve
            """
        ).fetchone()
        total_pnl = con.execute(
            """
            SELECT
              count(DISTINCT day) AS day_count,
              sum(gross_buy_cost) AS gross_buy_cost,
              sum(paired_mergeable_qty) AS merge_recovered_capital,
              sum(pair_actions) AS pair_actions,
              sum(official_taker_fee) AS official_taker_fee,
              sum(residual_cost) AS residual_cost,
              sum(residual_qty) AS residual_qty,
              sum(pair_pnl) AS pair_pnl,
              sum(fee_after_pnl) AS fee_after_pnl,
              min(fee_after_pnl) AS stress_worst_day_fee_after_pnl,
              arg_min(day, fee_after_pnl) AS stress_worst_day
            FROM pnl_by_asset_day
            """
        ).fetchone()
        total_cols = [item[0] for item in con.description]
        total = dict(zip(total_cols, total_pnl))
        asset_rows = con.execute(
            """
            WITH cap AS (
              SELECT
                asset,
                max(capital_tied) AS max_capital_tied,
                avg(capital_tied) AS average_capital_tied,
                quantile_cont(capital_tied, 0.95) AS p95_capital_tied
              FROM asset_capital_curve
              GROUP BY 1
            ),
            pnl AS (
              SELECT
                asset,
                count(DISTINCT day) AS day_count,
                sum(gross_buy_cost) AS gross_buy_cost,
                sum(paired_mergeable_qty) AS merge_recovered_capital,
                sum(pair_actions) AS pair_actions,
                sum(official_taker_fee) AS official_taker_fee,
                sum(residual_cost) AS residual_cost,
                sum(residual_qty) AS residual_qty,
                sum(pair_pnl) AS pair_pnl,
                sum(fee_after_pnl) AS fee_after_pnl,
                min(fee_after_pnl) AS stress_worst_day_fee_after_pnl,
                arg_min(day, fee_after_pnl) AS stress_worst_day
              FROM pnl_by_asset_day
              GROUP BY 1
            )
            SELECT *
            FROM pnl
            LEFT JOIN cap USING (asset)
            ORDER BY asset
            """
        ).fetchall()
        asset_cols = [item[0] for item in con.description]
        con.execute(
            f"COPY global_capital_curve TO {quote(output_dir / 'global_capital_curve.csv')} "
            "(HEADER, DELIMITER ',')"
        )
        con.execute(
            f"COPY pnl_by_asset_day TO {quote(output_dir / 'capital_ledger_by_asset_day.csv')} "
            "(HEADER, DELIMITER ',')"
        )
        con.execute("CHECKPOINT")
    finally:
        con.close()

    max_capital = float(global_capital[0] or 0.0)
    avg_capital = float(global_capital[1] or 0.0)
    p95_capital = float(global_capital[2] or 0.0)
    fee_after = float(total.get("fee_after_pnl") or 0.0)
    gross_buy_cost = float(total.get("gross_buy_cost") or 0.0)
    fee_drag = float(total.get("official_taker_fee") or 0.0)
    day_count = int(total.get("day_count") or 0)
    capacity_period_pnl = fee_after / max_capital * float(args.capacity_notional) if max_capital else None
    summary = {
        **total,
        "max_capital_tied": max_capital,
        "average_capital_tied": avg_capital,
        "p95_capital_tied": p95_capital,
        "gross_cost_roi": fee_after / gross_buy_cost if gross_buy_cost else None,
        "turnover_adjusted_roi_on_max_capital": fee_after / max_capital if max_capital else None,
        "turnover_adjusted_roi_on_avg_capital": fee_after / avg_capital if avg_capital else None,
        "capacity_period_pnl_at_notional": capacity_period_pnl,
        "daily_capacity_estimate_at_notional": (capacity_period_pnl / day_count)
        if capacity_period_pnl is not None and day_count
        else None,
        "fee_drag": fee_drag,
        "fee_drag_over_gross_cost": fee_drag / gross_buy_cost if gross_buy_cost else None,
        "stress_capital_drawdown": abs(float(total.get("stress_worst_day_fee_after_pnl") or 0.0)),
    }
    by_asset: dict[str, dict[str, Any]] = {}
    for raw in asset_rows:
        row = dict(zip(asset_cols, raw))
        row_max = float(row.get("max_capital_tied") or 0.0)
        row_avg = float(row.get("average_capital_tied") or 0.0)
        row_fee = float(row.get("fee_after_pnl") or 0.0)
        row_gross = float(row.get("gross_buy_cost") or 0.0)
        row["gross_cost_roi"] = row_fee / row_gross if row_gross else None
        row["turnover_adjusted_roi_on_max_capital"] = row_fee / row_max if row_max else None
        row["turnover_adjusted_roi_on_avg_capital"] = row_fee / row_avg if row_avg else None
        asset_day_count = int(row.get("day_count") or 0)
        row["capacity_period_pnl_at_notional"] = row_fee / row_max * float(args.capacity_notional) if row_max else None
        row["daily_capacity_estimate_at_notional"] = (
            row["capacity_period_pnl_at_notional"] / asset_day_count
            if row["capacity_period_pnl_at_notional"] is not None and asset_day_count
            else None
        )
        by_asset[str(row["asset"])] = {
            key: clean_value(value) for key, value in row.items()
        }

    manifest = {
        "schema_version": "xuan_capital_ledger_report_v1",
        "created_utc": utc_now(),
        "status": "OK_XUAN_CAPITAL_LEDGER_READY",
        "adapter_dir": str(adapter_dir),
        "candidate_rescore_manifest": str(args.candidate_rescore_manifest.expanduser()),
        "candidate_rescore_status": rescore.get("status") if rescore else "MISSING",
        "capacity_notional": args.capacity_notional,
        "summary": {key: clean_value(value) for key, value in summary.items()},
        "by_asset": by_asset,
        "outputs": {
            "duckdb": str(out_db),
            "global_capital_curve_csv": str(output_dir / "global_capital_curve.csv"),
            "by_asset_day_csv": str(output_dir / "capital_ledger_by_asset_day.csv"),
        },
        "semantics": {
            "capital_tied_after": "inventory_yes_cost_after + inventory_no_cost_after after each adapter action",
            "max_capital_tied": "Cumulative global open inventory cost after replaying per-market inventory deltas over time",
            "daily_capacity_estimate_at_notional": "fee_after_pnl / max_capital_tied * capacity_notional; research-only capacity proxy",
            "private_truth_ready": False,
            "deployable": False,
            "live_orders_allowed": False,
        },
    }
    (output_dir / "XUAN_CAPITAL_LEDGER_REPORT.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"status": manifest["status"], "summary": manifest["summary"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

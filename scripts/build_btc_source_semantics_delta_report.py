#!/usr/bin/env python3
"""Explain source/event-generation differences between old BTC base and V1 BTC adapter."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_OLD_BASE = (
    DEFAULT_DATA_ROOT / "derived/completion_candidate_pipeline_v1/local_20260502_20260518_paircap102"
)
DEFAULT_NEW_BASE = (
    DEFAULT_DATA_ROOT / "derived/contract_examples/btc_completion_candidate_base_from_l1_flow_taker_normalized_v1"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def as_manifest_db(manifest_path: Path) -> tuple[Path, str, dict[str, Any]]:
    manifest = read_json(manifest_path)
    outputs = manifest.get("outputs") or {}
    db_name = outputs.get("duckdb") or "candidate_base.duckdb"
    table = outputs.get("duckdb_table") or outputs.get("table") or "candidate_base"
    return manifest_path.parent / str(db_name), str(table), manifest


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-base-dir", type=Path, default=DEFAULT_OLD_BASE)
    parser.add_argument("--new-base-dir", type=Path, default=DEFAULT_NEW_BASE)
    parser.add_argument("--old-runner-taker-side", choices=["SELL", "BUY", "ANY"], default="SELL")
    parser.add_argument("--new-runner-taker-side", choices=["SELL", "BUY", "ANY"], default="BUY")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/btc_source_semantics_delta_latest",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    old_manifest_path = args.old_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"
    new_manifest_path = args.new_base_dir.expanduser() / "CANDIDATE_BASE_MANIFEST.json"
    old_db, old_table, old_manifest = as_manifest_db(old_manifest_path)
    new_db, new_table, new_manifest = as_manifest_db(new_manifest_path)
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {quote(old_db)} AS old_base (READ_ONLY)")
        con.execute(f"ATTACH {quote(new_db)} AS new_base (READ_ONLY)")
        summary_rows: list[dict[str, Any]] = []
        for label, schema, table, runner_side in (
            ("old_btc_baseline_base", "old_base", old_table, args.old_runner_taker_side),
            ("btc_v1_adapter_base", "new_base", new_table, args.new_runner_taker_side),
        ):
            runner_taker_filter = "1=1" if runner_side == "ANY" else f"public_trade_taker_side='{runner_side}'"
            row = con.execute(
                f"""
                SELECT
                  count(*) AS row_count,
                  count(DISTINCT condition_id) AS condition_count,
                  count(*) FILTER (WHERE event_kind='public_trade') AS public_trade_rows,
                  count(*) FILTER (WHERE event_kind='l1_price_change') AS l1_price_change_rows,
                  count(*) FILTER (WHERE public_trade_taker_side='SELL') AS taker_sell_rows,
                  count(*) FILTER (WHERE public_trade_taker_side='BUY') AS taker_buy_rows,
                  count(*) FILTER (WHERE public_trade_taker_side IS NULL) AS taker_null_rows,
                  count(*) FILTER (
                    WHERE event_kind='public_trade'
                      AND {runner_taker_filter}
                      AND side IN ('YES','NO')
                      AND offset_s >= 0
                      AND offset_s < 300
                  ) AS runner_candidate_rows,
                  count(*) FILTER (
                    WHERE event_kind='public_trade'
                      AND {runner_taker_filter}
                      AND side IN ('YES','NO')
                      AND offset_s >= 0
                      AND offset_s < 120
                  ) AS seed_offset_rows,
                  count(*) FILTER (
                    WHERE event_kind='public_trade'
                      AND {runner_taker_filter}
                      AND public_trade_price BETWEEN 0.05 AND 0.90
                  ) AS seed_price_band_rows,
                  count(*) FILTER (
                    WHERE event_kind='public_trade'
                      AND {runner_taker_filter}
                      AND l1_pair_ask <= 1.02
                  ) AS pair_cap_rows,
                  quantile_cont(offset_s, 0.5) AS p50_offset_s,
                  quantile_cont(offset_s, 0.9) AS p90_offset_s
                FROM {schema}.main.{table}
                """
            ).fetchone()
            fields = [item[0] for item in con.description]
            summary_rows.append({"source": label, **dict(zip(fields, row))})

        by_day_rows = con.execute(
            f"""
            WITH old_day AS (
              SELECT day, count(*) AS old_rows,
                     count(*) FILTER (
                       WHERE event_kind='public_trade'
                         AND {'1=1' if args.old_runner_taker_side == 'ANY' else "public_trade_taker_side='" + args.old_runner_taker_side + "'"}
                         AND side IN ('YES','NO') AND offset_s>=0 AND offset_s<300
                     ) AS old_runner_candidates
              FROM old_base.main.{old_table}
              GROUP BY day
            ),
            new_day AS (
              SELECT day, count(*) AS new_rows,
                     count(*) FILTER (
                       WHERE event_kind='public_trade'
                         AND {'1=1' if args.new_runner_taker_side == 'ANY' else "public_trade_taker_side='" + args.new_runner_taker_side + "'"}
                         AND side IN ('YES','NO') AND offset_s>=0 AND offset_s<300
                     ) AS new_runner_candidates
              FROM new_base.main.{new_table}
              GROUP BY day
            )
            SELECT
              coalesce(o.day, n.day) AS day,
              o.old_rows,
              n.new_rows,
              n.new_rows::DOUBLE / NULLIF(o.old_rows, 0) AS row_ratio,
              o.old_runner_candidates,
              n.new_runner_candidates,
              n.new_runner_candidates::DOUBLE / NULLIF(o.old_runner_candidates, 0) AS runner_candidate_ratio
            FROM old_day o
            FULL OUTER JOIN new_day n USING(day)
            ORDER BY day
            """
        ).fetchall()
        by_day_fields = [item[0] for item in con.description]
    finally:
        con.close()

    summary_csv = output_dir / "btc_source_semantics_summary.csv"
    summary_fields = [
        "source",
        "row_count",
        "condition_count",
        "public_trade_rows",
        "l1_price_change_rows",
        "taker_sell_rows",
        "taker_buy_rows",
        "taker_null_rows",
        "runner_candidate_rows",
        "seed_offset_rows",
        "seed_price_band_rows",
        "pair_cap_rows",
        "p50_offset_s",
        "p90_offset_s",
    ]
    write_csv(summary_csv, summary_rows, summary_fields)
    by_day_csv = output_dir / "btc_source_semantics_by_day.csv"
    write_csv(by_day_csv, [dict(zip(by_day_fields, row)) for row in by_day_rows], by_day_fields)

    old_summary = summary_rows[0]
    new_summary = summary_rows[1]
    old_runner = float(old_summary.get("runner_candidate_rows") or 0.0)
    new_runner = float(new_summary.get("runner_candidate_rows") or 0.0)
    manifest = {
        "schema_version": "btc_source_semantics_delta_report_v1",
        "created_utc": utc_now(),
        "status": "OK_BTC_SOURCE_SEMANTICS_DELTA_READY",
        "old_candidate_base_manifest": str(old_manifest_path),
        "new_candidate_base_manifest": str(new_manifest_path),
        "outputs": {
            "summary_csv": str(summary_csv),
            "by_day_csv": str(by_day_csv),
        },
        "summary": {
            "old_row_count": old_summary.get("row_count"),
            "new_row_count": new_summary.get("row_count"),
            "old_runner_candidate_rows": old_summary.get("runner_candidate_rows"),
            "new_runner_candidate_rows": new_summary.get("runner_candidate_rows"),
            "runner_candidate_ratio_new_over_old": (new_runner / old_runner) if old_runner else None,
            "old_public_trade_sell_rows": old_summary.get("taker_sell_rows"),
            "new_public_trade_sell_rows": new_summary.get("taker_sell_rows"),
            "old_runner_taker_side": args.old_runner_taker_side,
            "new_runner_taker_side": args.new_runner_taker_side,
            "new_taker_side_policy": (new_manifest.get("semantics") or {}).get("public_trade_taker_side"),
        },
        "interpretation": {
            "primary_delta": "The normalized BTC adapter recovers taker_side from core md_trades and runs the legacy state machine with public_trade_taker_side=BUY, while the old baseline runs SELL over a mixed public_trade/l1_price_change candidate base.",
            "impact": "This removes the all-SELL compatibility hack, but BTC parity still cannot be marked proven until the BUY-vs-SELL source-event semantics are explicitly accepted or bridged.",
            "not_private_truth": True,
        },
    }
    manifest_path = output_dir / "BTC_SOURCE_SEMANTICS_DELTA_REPORT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "summary": manifest["summary"], "interpretation": manifest["interpretation"]}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

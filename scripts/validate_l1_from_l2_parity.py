#!/usr/bin/env python3
"""Validate L1 top-of-book parity reconstructed from a local L2 replay store."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_L2_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/20260502_20260518_l2/REPLAY_STORE_V2_MANIFEST.json"
)
DEFAULT_L1_DB = (
    DEFAULT_DATA_ROOT
    / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/event_store.duckdb"
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "asset",
        "sample_rows",
        "matched_rows",
        "match_rate",
        "bid_ask_mismatch_rows",
        "bid_ask_mismatch_rate",
        "top_overlay_required_rows",
        "top_overlay_required_rate",
        "overlay_matched_rows",
        "overlay_match_rate",
        "overlay_bid_ask_mismatch_rows",
        "overlay_bid_ask_mismatch_rate",
        "crossed_or_locked_rows",
        "crossed_or_locked_rate",
        "stale_rows",
        "stale_rate",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def blocked_report(args: argparse.Namespace, reason: str) -> dict[str, Any]:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "schema_version": "l1_from_l2_parity_report_v1",
        "created_utc": utc_now(),
        "status": reason,
        "l2_manifest": str(args.l2_manifest),
        "l1_event_db": str(args.l1_event_db),
        "sample_per_asset": args.sample_per_asset,
        "outputs": {},
        "notes": [
            "This gate must pass before V1 L2 can be treated as equivalent to L1 source truth.",
            "A blocked status is expected before replay_store_multiasset_l2_v1 is built.",
        ],
    }
    path = args.output_dir / "L1_FROM_L2_PARITY_REPORT.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(path)}, indent=2, sort_keys=True))
    return report


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    if not args.l2_manifest.exists():
        return blocked_report(args, "BLOCKED_L2_STORE_MISSING")
    if not args.l1_event_db.exists():
        return blocked_report(args, "BLOCKED_L1_EVENT_DB_MISSING")
    manifest = read_json(args.l2_manifest)
    if "md_book_l2" not in set(manifest.get("tables") or []):
        return blocked_report(args, "BLOCKED_L2_TABLE_MISSING")
    l2_db = args.l2_manifest.parent / str((manifest.get("outputs") or {}).get("duckdb") or "store.duckdb")
    if not l2_db.exists():
        return blocked_report(args, "BLOCKED_L2_DUCKDB_MISSING")

    import duckdb  # type: ignore

    args.output_dir.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    try:
        con.execute(f"ATTACH {quote_literal(args.l1_event_db)} AS l1db (READ_ONLY)")
        con.execute(f"ATTACH {quote_literal(l2_db)} AS l2db (READ_ONLY)")
        rows = con.execute(
            """
            WITH sample AS (
              SELECT *
              FROM (
                SELECT
                  m.symbol AS market_symbol,
                  b.day,
                  b.condition_id,
                  b.recv_ms AS l1_recv_ms,
                  b.capture_seq AS l1_capture_seq,
                  b.recv_ms - b.source_ts_ms AS l1_age_ms,
                  b.yes_bid_px,
                  b.yes_ask_px,
                  b.no_bid_px,
                  b.no_ask_px,
                  row_number() OVER (
                    PARTITION BY m.symbol
                    ORDER BY b.day, b.condition_id, b.recv_ms, b.source_row_id
                  ) AS rn
                FROM l2db.main.md_book_l1 b
                JOIN l2db.main.market_meta m
                  ON b.day = m.day
                 AND b.condition_id = m.condition_id
              )
              WHERE rn <= ?
            ),
            joined AS (
              SELECT
                s.*,
                (
                  SELECT y.bid1_px
                  FROM l2db.main.md_book_l2 y
                  WHERE y.day = s.day
                    AND y.condition_id = s.condition_id
                    AND y.market_side = 'YES'
                    AND (y.capture_seq < s.l1_capture_seq OR (y.capture_seq = s.l1_capture_seq AND y.recv_ms <= s.l1_recv_ms))
                  ORDER BY y.capture_seq DESC, y.recv_ms DESC, y.source_row_id DESC
                  LIMIT 1
                ) AS yes_bid_px_l2,
                (
                  SELECT y.ask1_px
                  FROM l2db.main.md_book_l2 y
                  WHERE y.day = s.day
                    AND y.condition_id = s.condition_id
                    AND y.market_side = 'YES'
                    AND (y.capture_seq < s.l1_capture_seq OR (y.capture_seq = s.l1_capture_seq AND y.recv_ms <= s.l1_recv_ms))
                  ORDER BY y.capture_seq DESC, y.recv_ms DESC, y.source_row_id DESC
                  LIMIT 1
                ) AS yes_ask_px_l2,
                (
                  SELECT n.bid1_px
                  FROM l2db.main.md_book_l2 n
                  WHERE n.day = s.day
                    AND n.condition_id = s.condition_id
                    AND n.market_side = 'NO'
                    AND (n.capture_seq < s.l1_capture_seq OR (n.capture_seq = s.l1_capture_seq AND n.recv_ms <= s.l1_recv_ms))
                  ORDER BY n.capture_seq DESC, n.recv_ms DESC, n.source_row_id DESC
                  LIMIT 1
                ) AS no_bid_px_l2,
                (
                  SELECT n.ask1_px
                  FROM l2db.main.md_book_l2 n
                  WHERE n.day = s.day
                    AND n.condition_id = s.condition_id
                    AND n.market_side = 'NO'
                    AND (n.capture_seq < s.l1_capture_seq OR (n.capture_seq = s.l1_capture_seq AND n.recv_ms <= s.l1_recv_ms))
                  ORDER BY n.capture_seq DESC, n.recv_ms DESC, n.source_row_id DESC
                  LIMIT 1
                ) AS no_ask_px_l2
              FROM sample s
            ),
            scored AS (
              SELECT
                *,
                (
                  yes_bid_px_l2 IS NOT NULL AND yes_ask_px_l2 IS NOT NULL AND
                  no_bid_px_l2 IS NOT NULL AND no_ask_px_l2 IS NOT NULL
                ) AS matched,
                (
                  abs(coalesce(yes_bid_px, -999) - coalesce(yes_bid_px_l2, -999)) > 1e-9 OR
                  abs(coalesce(yes_ask_px, -999) - coalesce(yes_ask_px_l2, -999)) > 1e-9 OR
                  abs(coalesce(no_bid_px, -999) - coalesce(no_bid_px_l2, -999)) > 1e-9 OR
                  abs(coalesce(no_ask_px, -999) - coalesce(no_ask_px_l2, -999)) > 1e-9
                ) AS bid_ask_mismatch,
                (
                  yes_bid_px >= yes_ask_px OR
                  no_bid_px >= no_ask_px OR
                  yes_bid_px_l2 >= yes_ask_px_l2 OR
                  no_bid_px_l2 >= no_ask_px_l2
                ) AS crossed_or_locked,
                l1_age_ms > ? AS stale
              FROM joined
            )
            SELECT
              market_symbol AS asset,
              count(*) AS sample_rows,
              sum(CASE WHEN matched THEN 1 ELSE 0 END) AS matched_rows,
              sum(CASE WHEN matched AND bid_ask_mismatch THEN 1 ELSE 0 END) AS bid_ask_mismatch_rows,
              sum(CASE WHEN crossed_or_locked THEN 1 ELSE 0 END) AS crossed_or_locked_rows,
              sum(CASE WHEN stale THEN 1 ELSE 0 END) AS stale_rows
            FROM scored
            GROUP BY 1
            ORDER BY 1
            """,
            [args.sample_per_asset, args.stale_ms],
        ).fetchall()
    finally:
        con.close()

    report_rows: list[dict[str, Any]] = []
    for asset, sample_rows, matched_rows, mismatch_rows, crossed_rows, stale_rows in rows:
        sample = int(sample_rows or 0)
        matched = int(matched_rows or 0)
        mismatch = int(mismatch_rows or 0)
        crossed = int(crossed_rows or 0)
        stale = int(stale_rows or 0)
        report_rows.append(
            {
                "asset": asset,
                "sample_rows": sample,
                "matched_rows": matched,
                "match_rate": round(matched / sample, 6) if sample else None,
                "bid_ask_mismatch_rows": mismatch,
                "bid_ask_mismatch_rate": round(mismatch / matched, 6) if matched else None,
                "top_overlay_required_rows": mismatch,
                "top_overlay_required_rate": round(mismatch / matched, 6) if matched else None,
                "overlay_matched_rows": matched,
                "overlay_match_rate": round(matched / sample, 6) if sample else None,
                "overlay_bid_ask_mismatch_rows": 0,
                "overlay_bid_ask_mismatch_rate": 0.0 if matched else None,
                "crossed_or_locked_rows": crossed,
                "crossed_or_locked_rate": round(crossed / sample, 6) if sample else None,
                "stale_rows": stale,
                "stale_rate": round(stale / sample, 6) if sample else None,
            }
        )

    pure_failed = [
        row
        for row in report_rows
        if (row["match_rate"] or 0) < args.min_match_rate
        or (row["bid_ask_mismatch_rate"] or 0) > args.max_mismatch_rate
        or (row["crossed_or_locked_rate"] or 0) > args.max_crossed_rate
    ]
    overlay_failed = [
        row
        for row in report_rows
        if (row["overlay_match_rate"] or 0) < args.min_overlay_match_rate
        or (row["overlay_bid_ask_mismatch_rate"] or 0) > args.max_mismatch_rate
    ]
    if report_rows and not pure_failed:
        status = "OK"
    elif report_rows and not overlay_failed:
        status = "OK_L1_TOP_OVERLAY_REQUIRED"
    else:
        status = "FAIL_L1_FROM_L2_PARITY"
    metrics_csv = args.output_dir / "l1_from_l2_parity_by_asset.csv"
    write_csv(metrics_csv, report_rows)
    report = {
        "schema_version": "l1_from_l2_parity_report_v1",
        "created_utc": utc_now(),
        "status": status,
        "l2_manifest": str(args.l2_manifest),
        "l2_duckdb": str(l2_db),
        "l1_event_db": str(args.l1_event_db),
        "sample_per_asset": args.sample_per_asset,
        "stale_ms": args.stale_ms,
        "thresholds": {
            "min_match_rate": args.min_match_rate,
            "min_overlay_match_rate": args.min_overlay_match_rate,
            "max_mismatch_rate": args.max_mismatch_rate,
            "max_crossed_rate": args.max_crossed_rate,
        },
        "parity_models": {
            "pure_l2": "Reconstruct top-of-book from latest md_book_l2 side snapshots at or before each md_book_l1 event.",
            "l1_top_overlay": "Use md_book_l1 as canonical top level and md_book_l2 as depth/coverage source. This is required for legacy replay archives where price-change events updated L1 without a full L2 depth refresh.",
        },
        "outputs": {"by_asset_csv": str(metrics_csv)},
        "by_asset": report_rows,
        "failed_assets": [row["asset"] for row in pure_failed],
        "overlay_failed_assets": [row["asset"] for row in overlay_failed],
    }
    path = args.output_dir / "L1_FROM_L2_PARITY_REPORT.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(path), "failed_assets": report["failed_assets"]}, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2-manifest", type=Path, default=DEFAULT_L2_MANIFEST)
    parser.add_argument("--l1-event-db", type=Path, default=DEFAULT_L1_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DATA_ROOT / "derived/contract_examples/l1_from_l2_parity_latest")
    parser.add_argument("--sample-per-asset", type=int, default=1000)
    parser.add_argument("--stale-ms", type=int, default=1500)
    parser.add_argument("--min-match-rate", type=float, default=0.995)
    parser.add_argument("--min-overlay-match-rate", type=float, default=0.95)
    parser.add_argument("--max-mismatch-rate", type=float, default=0.001)
    parser.add_argument("--max-crossed-rate", type=float, default=0.001)
    args = parser.parse_args()
    args.l2_manifest = args.l2_manifest.expanduser()
    args.l1_event_db = args.l1_event_db.expanduser()
    args.output_dir = args.output_dir.expanduser()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

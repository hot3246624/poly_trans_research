#!/usr/bin/env python3
"""Build a state-machine-compatible candidate_base from multiasset L1 flow events."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_SEARCH_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "derived/multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_SEARCH_SAFE_VIEW_MANIFEST.json"
)
DEFAULT_L2_REPLAY_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "verification_store/replay_store_multiasset_l2_v1/20260502_20260518_l2/REPLAY_STORE_V2_MANIFEST.json"
)
DEFAULT_CORE_REPLAY_MANIFEST = (
    DEFAULT_DATA_ROOT
    / "verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/REPLAY_STORE_V2_MANIFEST.json"
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sql_string_list(values: list[str] | tuple[str, ...]) -> str:
    return "(" + ", ".join(quote(value) for value in values) + ")"


def fetch_one_dict(con: Any, sql: str) -> dict[str, Any]:
    cur = con.execute(sql)
    row = cur.fetchone()
    if row is None:
        return {}
    names = [item[0] for item in cur.description]
    return dict(zip(names, row))


def manifest_duckdb(manifest_path: Path, manifest: dict[str, Any]) -> Path:
    outputs = manifest.get("outputs") or {}
    value = outputs.get("duckdb") or manifest.get("duckdb") or manifest.get("output_duckdb") or "store.duckdb"
    path = Path(str(value)).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--search-manifest", type=Path, default=DEFAULT_SEARCH_MANIFEST)
    parser.add_argument("--l2-replay-manifest", type=Path, default=DEFAULT_L2_REPLAY_MANIFEST)
    parser.add_argument("--core-replay-manifest", type=Path, default=DEFAULT_CORE_REPLAY_MANIFEST)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/multiasset_completion_candidate_base_from_l1_flow_v1",
    )
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument(
        "--assets",
        default="",
        help="Optional comma-separated asset filter, e.g. BTC or BTC,ETH. Empty means all assets.",
    )
    parser.add_argument(
        "--taker-side-source",
        choices=["all_sell", "core_md_trades"],
        default="all_sell",
        help="Use all SELL for legacy compatibility, or recover taker_side from core replay md_trades.",
    )
    args = parser.parse_args()

    import duckdb  # type: ignore

    search_manifest_path = args.search_manifest.expanduser()
    search_manifest = read_json(search_manifest_path)
    search_db = search_manifest_path.parent / str((search_manifest.get("outputs") or {}).get("duckdb") or "event_store.duckdb")
    search_table = str((search_manifest.get("outputs") or {}).get("table") or "l1_taker_buy_events_search_safe")
    l2_replay_manifest_path = args.l2_replay_manifest.expanduser()
    replay_manifest = read_json(l2_replay_manifest_path)
    replay_db = manifest_duckdb(l2_replay_manifest_path, replay_manifest)
    core_replay_manifest_path = args.core_replay_manifest.expanduser()
    core_replay_manifest = read_json(core_replay_manifest_path)
    core_db = manifest_duckdb(core_replay_manifest_path, core_replay_manifest)
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "candidate_base.duckdb"
    requested_assets = sorted({item.strip().upper() for item in str(args.assets or "").split(",") if item.strip()})
    asset_filter_sql = ""
    if requested_assets:
        asset_filter_sql = f" AND e.market_symbol IN {sql_string_list(requested_assets)}"

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.duckdb_threads)}")
        con.execute(f"ATTACH {quote(search_db)} AS src (READ_ONLY)")
        con.execute(f"ATTACH {quote(replay_db)} AS replay (READ_ONLY)")
        taker_join_sql = ""
        taker_side_sql = "'SELL'"
        if args.taker_side_source == "core_md_trades":
            con.execute(f"ATTACH {quote(core_db)} AS core (READ_ONLY)")
            taker_join_sql = (
                "LEFT JOIN core.main.md_trades t "
                "ON e.day = t.day "
                "AND e.condition_id = t.condition_id "
                "AND e.source_trade_row_id = t.source_row_id"
            )
            taker_side_sql = "upper(coalesce(t.taker_side, 'UNKNOWN'))"
        con.execute(
            f"""
            CREATE OR REPLACE TABLE candidate_base AS
            SELECT
              row_number() OVER (
                ORDER BY e.day, e.market_symbol, e.trigger_ts_ms, e.source_trade_row_id, e.first_side
              ) AS candidate_row_id,
              'multiasset_l1_flow_search_safe_candidate_base_v1' AS dataset_type,
              '20260502_20260518_multiasset_l1_flow_search_safe' AS source_label,
              e.market_symbol AS asset,
              'public_trade' AS event_kind,
              e.source_trade_row_id AS event_id,
              e.day,
              e.condition_id,
              e.market_slug AS slug,
              e.trigger_ts_ms AS ts_ms,
              '' AS ts_iso,
              e.offset_s,
              e.first_side AS side,
              e.opposite_side,
              s.winner_side,
              e.first_side = s.winner_side AS side_is_winner,
              e.side_alignment,
              e.high_side,
              e.l1_source_row_id AS strict_l1_row_id,
              e.l1_recv_ms AS strict_l1_recv_ms,
              e.l1_age_ms AS strict_l1_age_ms,
              NULL::BIGINT AS strict_l2_row_id,
              NULL::BIGINT AS strict_l2_recv_ms,
              NULL::BIGINT AS strict_l2_age_ms,
              e.first_bid_px AS side_bid,
              e.first_ask_px AS side_ask,
              e.first_bid_sz AS side_bid_sz,
              e.first_ask_sz AS side_ask_sz,
              e.opposite_bid_px AS opp_bid,
              e.opposite_ask_px AS opp_ask,
              e.opposite_bid_sz AS opp_bid_sz,
              e.opposite_ask_sz AS opp_ask_sz,
              e.l1_pair_ask,
              e.l1_pair_bid,
              e.first_ask_sz >= 10 AS buy_full_10,
              e.first_ask_px AS buy_vwap_10,
              least(e.first_ask_sz, 10) AS buy_filled_10,
              e.first_ask_sz >= 25 AS buy_full_25,
              e.first_ask_px AS buy_vwap_25,
              least(e.first_ask_sz, 25) AS buy_filled_25,
              e.first_ask_sz >= 60 AS buy_full_60,
              e.first_ask_px AS buy_vwap_60,
              least(e.first_ask_sz, 60) AS buy_filled_60,
              e.first_ask_px AS buy_best_px,
              e.first_ask_sz AS buy_best_sz,
              e.first_ask_sz AS buy_available_qty,
              e.first_bid_px AS sell_best_px,
              e.first_bid_sz AS sell_best_sz,
              e.first_bid_sz AS sell_available_qty,
              0.0 AS side_bid_level_drop_qty,
              0.0 AS side_ask_level_lift_qty,
              0.0 AS side_bid_delta_qty,
              0.0 AS side_ask_delta_qty,
              'search_safe_l1_flow_event' AS book_update_reason,
              e.source_trade_row_id AS public_trade_row_id,
              {taker_side_sql} AS public_trade_taker_side,
              e.public_trade_price,
              e.public_trade_size,
              e.trade_recv_ms AS public_trade_recv_ms,
              'l1_flow_public_trade_search_safe' AS candidate_reason,
              least(e.first_ask_sz, e.opposite_ask_sz) AS l1_pair_available_qty
            FROM src.main.{search_table} e
            LEFT JOIN replay.main.settlement_records s
              ON e.condition_id = s.condition_id
            {taker_join_sql}
            WHERE e.day NOT IN {BLOCKLISTED_DAYS}
              AND e.first_side IN ('YES', 'NO')
              AND e.opposite_side IN ('YES', 'NO')
              AND e.l1_pair_ask IS NOT NULL
              AND e.public_trade_price IS NOT NULL
              AND e.public_trade_size IS NOT NULL
              {asset_filter_sql}
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_candidate_base_condition_ts ON candidate_base(condition_id, ts_ms)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_candidate_base_asset_day ON candidate_base(asset, day)")
        counts = fetch_one_dict(
            con,
            """
            SELECT
              count(*) AS row_count,
              count(DISTINCT condition_id) AS condition_count,
              count(DISTINCT asset) AS asset_count,
              count(*) FILTER (WHERE day IN ('2026-05-14', '2026-05-15', '2026-05-19')) AS blocklisted_rows
            FROM candidate_base
            """,
        )
        by_asset = {
            str(asset): int(count)
            for asset, count in con.execute(
                "SELECT asset, count(*) FROM candidate_base GROUP BY asset ORDER BY asset"
            ).fetchall()
        }
        by_day = {
            str(day): int(count)
            for day, count in con.execute(
                "SELECT day, count(*) FROM candidate_base GROUP BY day ORDER BY day"
            ).fetchall()
        }
        con.execute("CHECKPOINT")
    finally:
        con.close()

    manifest = {
        "schema_version": "completion_candidate_base_v1",
        "dataset_type": "multiasset_l1_flow_search_safe_candidate_base_v1",
        "created_utc": utc_now(),
        "status": "OK",
        "data_root": str(DEFAULT_DATA_ROOT),
        "output_dir": str(output_dir),
        "search_manifest": str(search_manifest_path),
        "l2_replay_manifest": str(l2_replay_manifest_path),
        "core_replay_manifest": str(core_replay_manifest_path),
        "asset_filter": requested_assets,
        "taker_side_source": args.taker_side_source,
        "outputs": {
            "duckdb": "candidate_base.duckdb",
            "table": "candidate_base",
        },
        "row_count": counts.get("row_count"),
        "condition_count": counts.get("condition_count"),
        "asset_count": counts.get("asset_count"),
        "blocklisted_rows": counts.get("blocklisted_rows"),
        "labels": ["multiasset_l1_flow_search_safe_candidate_base_v1"],
        "days": sorted(by_day.keys()),
        "assets": sorted(by_asset.keys()),
        "market_prefix": "multiasset",
        "excluded_labels_or_days": {
            "blocklisted_days": list(BLOCKLISTED_DAYS),
            "excluded_labels": [],
        },
        "by_asset": by_asset,
        "by_day": by_day,
        "semantics": {
            "source": "search-safe public L1 flow events mapped into the legacy completion state-machine schema",
            "public_trade_taker_side": (
                "recovered from core replay md_trades.taker_side"
                if args.taker_side_source == "core_md_trades"
                else "mapped to SELL for compatibility with the legacy state-machine filter; treat as V1 adapter evidence, not old BTC parity"
            ),
            "not_private_truth": True,
        },
        "sha256": {
            "duckdb": sha256_file(output_db),
        },
    }
    manifest_path = output_dir / "CANDIDATE_BASE_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": manifest["status"], "row_count": manifest["row_count"], "by_asset": by_asset}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

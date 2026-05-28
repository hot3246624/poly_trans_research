#!/usr/bin/env python3
"""Build a crosswalk/funnel report for multiasset backtest V1."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_REPO_ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
VALID_DAYS = tuple(
    [f"2026-05-{day:02d}" for day in range(2, 14)]
    + ["2026-05-16", "2026-05-17", "2026-05-18"]
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")
EXPECTED_CONFIGS = (
    "configs/backtest/search_multiasset_l1_flow_7asset_smoke_contract.json",
    "configs/backtest/search_multiasset_l1_flow_matrix_formal_v1.json",
    "configs/backtest/search_multiasset_l1_flow_matrix_deep_v1.json",
    "configs/backtest/search_multiasset_l1_flow_batch_formal_v1.json",
    "configs/backtest/search_multiasset_l1_flow_batch_deep_v1.json",
)
EXPECTED_RUNNER_SCRIPTS = (
    "scripts/run_backtest_search_matrix.py",
    "scripts/run_backtest_search_shards.py",
    "scripts/run_backtest_matrix_batch.py",
    "scripts/run_backtest_batch_pipeline.py",
    "scripts/build_backtest_result_catalog.py",
    "scripts/compare_backtest_result_catalog.py",
    "scripts/select_backtest_candidate_shortlist.py",
    "scripts/build_backtest_validation_queue.py",
    "scripts/run_backtest_validation_queue.py",
    "scripts/build_backtest_validation_result_catalog.py",
    "scripts/build_backtest_candidate_audit_pack.py",
)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: Any) -> int | None:
    number = to_float(value)
    return int(number) if number is not None else None


def count_by(rows: list[dict[str, Any]], field: str) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for row in rows:
        value = str(row.get(field) or "")
        if value:
            counter[value] += 1
    return dict(sorted(counter.items()))


def sum_by(rows: list[dict[str, Any]], group_field: str, value_field: str) -> dict[str, float]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows:
        group = str(row.get(group_field) or "")
        value = to_float(row.get(value_field))
        if group and value is not None:
            totals[group] += value
    return {key: round(value, 6) for key, value in sorted(totals.items())}


def query_group_counts(db_path: Path, table: str, group_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    if not db_path.exists():
        return []
    import duckdb  # type: ignore

    select_expr = ", ".join(group_fields)
    group_expr = ", ".join(str(i + 1) for i in range(len(group_fields)))
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        rows = con.execute(
            f"""
            SELECT {select_expr}, COUNT(*) AS rows
            FROM {table}
            GROUP BY {group_expr}
            ORDER BY {group_expr}
            """
        ).fetchall()
    finally:
        con.close()
    return [
        {**{field: row[i] for i, field in enumerate(group_fields)}, "rows": int(row[len(group_fields)])}
        for row in rows
    ]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def asset_summary_from_csv(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for asset, count in count_by(rows, "asset").items():
        asset_rows = [row for row in rows if row.get("asset") == asset]
        pnl_values = [value for value in (to_float(row.get("pnl")) for row in asset_rows) if value is not None]
        best_pnl_values = [value for value in (to_float(row.get("best_pnl")) for row in asset_rows) if value is not None]
        row_sum = sum(value or 0 for value in (to_float(row.get("rows")) for row in asset_rows))
        out[asset] = {
            "candidate_or_result_rows": count,
            "event_rows_sum": round(row_sum, 6),
            "best_pnl": max(pnl_values) if pnl_values else (max(best_pnl_values) if best_pnl_values else None),
            "min_pnl": min(pnl_values) if pnl_values else (min(best_pnl_values) if best_pnl_values else None),
        }
    return out


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    data_root = args.data_root
    repo_root = args.repo_root
    derived = data_root / "derived"
    contract = derived / "contract_examples"
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    core_manifest_path = (
        data_root
        / "verification_store/replay_store_multiasset_core_v1/20260502_20260518_core/REPLAY_STORE_V2_MANIFEST.json"
    )
    search_manifest_path = (
        derived
        / "multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_SEARCH_SAFE_VIEW_MANIFEST.json"
    )
    event_manifest_path = (
        derived
        / "multiasset_l1_flow_event_store_v1/20260502_20260518_minsz10/L1_FLOW_EVENT_STORE_MANIFEST.json"
    )
    matrix_manifest_path = contract / "search_multiasset_l1_flow_matrix_formal_v1/SEARCH_MATRIX_MANIFEST.json"
    batch_manifest_path = contract / "backtest_batch_pipeline_deep_v1/BACKTEST_BATCH_PIPELINE_MANIFEST.json"
    catalog_manifest_path = contract / "backtest_result_catalog_deep_v1/BACKTEST_RESULT_CATALOG_MANIFEST.json"
    shortlist_manifest_path = contract / "backtest_candidate_shortlist_deep_v1/BACKTEST_CANDIDATE_SHORTLIST_MANIFEST.json"
    queue_manifest_path = contract / "backtest_validation_queue_deep_v1/BACKTEST_VALIDATION_QUEUE_MANIFEST.json"
    validation_catalog_path = contract / "backtest_validation_result_catalog_deep_v1/backtest_validation_result_catalog.csv"
    audit_manifest_path = contract / "backtest_candidate_audit_pack_latest/BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json"

    core_manifest = read_json(core_manifest_path)
    search_manifest = read_json(search_manifest_path)
    event_manifest = read_json(event_manifest_path)
    matrix_manifest = read_json(matrix_manifest_path)
    batch_manifest = read_json(batch_manifest_path)
    catalog_manifest = read_json(catalog_manifest_path)
    shortlist_manifest = read_json(shortlist_manifest_path)
    queue_manifest = read_json(queue_manifest_path)
    audit_manifest = read_json(audit_manifest_path)

    event_db = search_manifest_path.parent / str((search_manifest.get("outputs") or {}).get("duckdb") or "event_store.duckdb")
    event_rows = query_group_counts(event_db, "l1_taker_buy_events", ("market_symbol", "day"))
    search_safe_rows = query_group_counts(event_db, "l1_taker_buy_events_search_safe", ("market_symbol", "day"))

    matrix_csv = Path(str(matrix_manifest.get("result_csv") or matrix_manifest_path.parent / "search_matrix_results.csv"))
    catalog_csv = Path(str(catalog_manifest.get("catalog_csv") or catalog_manifest_path.parent / "backtest_result_catalog.csv"))
    shortlist_csv = Path(str(shortlist_manifest.get("shortlist_csv") or shortlist_manifest_path.parent / "backtest_candidate_shortlist.csv"))
    queue_jsonl = Path(str(queue_manifest.get("queue_jsonl") or queue_manifest_path.parent / "backtest_validation_queue.jsonl"))
    audit_csv = Path(str(audit_manifest.get("candidate_audit_pack_csv") or audit_manifest_path.parent / "backtest_candidate_audit_pack.csv"))

    matrix_rows = read_csv(matrix_csv)
    catalog_rows = read_csv(catalog_csv)
    shortlist_rows = read_csv(shortlist_csv)
    queue_rows = read_jsonl(queue_jsonl)
    validation_rows = read_csv(validation_catalog_path)
    audit_rows = read_csv(audit_csv)

    layer_rows = [
        {
            "layer_order": 10,
            "layer": "core_replay_market_meta",
            "row_count": (core_manifest.get("table_totals") or {}).get("market_meta"),
            "candidate_count": "",
            "asset_count": len(core_manifest.get("assets") or []),
            "status": "source_truth_core",
            "attrition_from_previous": "",
            "notes": "Market universe from compact replay core; no L2.",
        },
        {
            "layer_order": 20,
            "layer": "core_replay_md_book_l1",
            "row_count": (core_manifest.get("table_totals") or {}).get("md_book_l1"),
            "candidate_count": "",
            "asset_count": len(core_manifest.get("assets") or []),
            "status": "source_truth_core",
            "attrition_from_previous": "",
            "notes": "L1 book source rows, not strategy candidates.",
        },
        {
            "layer_order": 30,
            "layer": "core_replay_md_trades",
            "row_count": (core_manifest.get("table_totals") or {}).get("md_trades"),
            "candidate_count": "",
            "asset_count": len(core_manifest.get("assets") or []),
            "status": "source_truth_core",
            "attrition_from_previous": "",
            "notes": "Public trade source rows, not owner private truth.",
        },
        {
            "layer_order": 40,
            "layer": "l1_taker_buy_event_store",
            "row_count": (event_manifest.get("outputs") or {}).get("row_count"),
            "candidate_count": "",
            "asset_count": len((search_manifest.get("assets") or [])),
            "status": "search_input",
            "attrition_from_previous": "filtered_to_l1_flow_events",
            "notes": "Public L1/trade event layer used by the screener.",
        },
        {
            "layer_order": 50,
            "layer": "search_safe_view",
            "row_count": (search_manifest.get("outputs") or {}).get("row_count"),
            "candidate_count": "",
            "asset_count": len((search_manifest.get("assets") or [])),
            "status": "search_safe",
            "attrition_from_previous": "forbidden_private_outcome_columns_removed",
            "notes": "Search-safe view; winner/outcome/private/residual columns are forbidden.",
        },
        {
            "layer_order": 60,
            "layer": "matrix_formal_results",
            "row_count": len(matrix_rows),
            "candidate_count": matrix_manifest.get("matrix_result_count"),
            "asset_count": len(count_by(matrix_rows, "asset")),
            "status": "parameter_screen",
            "attrition_from_previous": "parameter_grid_aggregation",
            "notes": "Rows are parameter-result summaries, not fills/actions.",
        },
        {
            "layer_order": 70,
            "layer": "batch_and_catalog_deep",
            "row_count": catalog_manifest.get("catalog_rows"),
            "candidate_count": catalog_manifest.get("candidate_count"),
            "asset_count": len(count_by(catalog_rows, "asset")),
            "status": "cross_batch_catalog",
            "attrition_from_previous": "batch_topn_and_candidate_grouping",
            "notes": "Catalog combines formal/deep batches and groups equivalent parameter rows.",
        },
        {
            "layer_order": 80,
            "layer": "shortlist_deep",
            "row_count": shortlist_manifest.get("shortlist_rows"),
            "candidate_count": len(shortlist_rows),
            "asset_count": len(count_by(shortlist_rows, "asset")),
            "status": "shortlisted",
            "attrition_from_previous": f"rejected_rows={shortlist_manifest.get('rejected_rows')}",
            "notes": "Stability/support shortlist. Current shortlist has no BTC candidate.",
        },
        {
            "layer_order": 90,
            "layer": "validation_queue_deep",
            "row_count": queue_manifest.get("job_count"),
            "candidate_count": len(queue_rows),
            "asset_count": len(count_by(queue_rows, "asset")),
            "status": "pending_or_completed_validation",
            "attrition_from_previous": f"limit={queue_manifest.get('limit')}",
            "notes": "Queue validates source/search-safe lineage only; no owner private truth.",
        },
        {
            "layer_order": 100,
            "layer": "validation_result_catalog_deep",
            "row_count": len(validation_rows),
            "candidate_count": len({row.get("candidate_key") for row in validation_rows if row.get("candidate_key")}),
            "asset_count": len(count_by(validation_rows, "asset")),
            "status": "validated_search_safe",
            "attrition_from_previous": "validation_result_catalog",
            "notes": "Search-safe validation result catalog; deployable remains false.",
        },
        {
            "layer_order": 110,
            "layer": "audit_pack_latest",
            "row_count": audit_manifest.get("selected_candidate_count"),
            "candidate_count": len(audit_rows),
            "asset_count": len(count_by(audit_rows, "asset")),
            "status": "SEARCH_SAFE_READY_PRIVATE_BLOCKED",
            "attrition_from_previous": f"input_candidate_count={audit_manifest.get('input_candidate_count')}",
            "notes": "Final audit pack is not trade edge proof; all selected candidates remain private blocked.",
        },
    ]

    by_asset_rows: list[dict[str, Any]] = []
    for layer, rows in (
        ("l1_taker_buy_event_store", event_rows),
        ("search_safe_view", search_safe_rows),
    ):
        by_asset: dict[str, int] = defaultdict(int)
        for row in rows:
            by_asset[str(row["market_symbol"])] += int(row["rows"])
        for asset, count in sorted(by_asset.items()):
            by_asset_rows.append({"layer": layer, "asset": asset, "row_count": count, "event_rows_sum": count, "best_pnl": ""})
    for layer, rows in (
        ("matrix_formal_results", matrix_rows),
        ("batch_and_catalog_deep", catalog_rows),
        ("shortlist_deep", shortlist_rows),
        ("validation_queue_deep", queue_rows),
        ("validation_result_catalog_deep", validation_rows),
        ("audit_pack_latest", audit_rows),
    ):
        for asset, summary in asset_summary_from_csv(rows).items():
            by_asset_rows.append(
                {
                    "layer": layer,
                    "asset": asset,
                    "row_count": summary["candidate_or_result_rows"],
                    "event_rows_sum": summary["event_rows_sum"],
                    "best_pnl": summary["best_pnl"],
                }
            )

    by_day_rows: list[dict[str, Any]] = []
    for layer, rows in (
        ("l1_taker_buy_event_store", event_rows),
        ("search_safe_view", search_safe_rows),
    ):
        for row in rows:
            by_day_rows.append(
                {
                    "layer": layer,
                    "asset": row["market_symbol"],
                    "day": row["day"],
                    "row_count": row["rows"],
                }
            )

    config_status = [
        {
            "path": str(repo_root / rel),
            "exists": (repo_root / rel).exists(),
            "sha256": sha256_file(repo_root / rel) if (repo_root / rel).exists() else None,
            "kind": "config",
        }
        for rel in EXPECTED_CONFIGS
    ]
    script_status = [
        {
            "path": str(repo_root / rel),
            "exists": (repo_root / rel).exists(),
            "sha256": sha256_file(repo_root / rel) if (repo_root / rel).exists() else None,
            "kind": "script",
        }
        for rel in EXPECTED_RUNNER_SCRIPTS
    ]
    reproducibility_blockers = [
        item for item in config_status + script_status if not item["exists"]
    ]

    btc_path = {
        "search_safe_rows": sum(row["rows"] for row in search_safe_rows if row["market_symbol"] == "BTC"),
        "matrix_rows": count_by(matrix_rows, "asset").get("BTC", 0),
        "catalog_rows": count_by(catalog_rows, "asset").get("BTC", 0),
        "shortlist_rows": count_by(shortlist_rows, "asset").get("BTC", 0),
        "validation_rows": count_by(validation_rows, "asset").get("BTC", 0),
        "audit_rows": count_by(audit_rows, "asset").get("BTC", 0),
    }

    layer_csv = output_dir / "backtest_v1_crosswalk_layers.csv"
    by_asset_csv = output_dir / "backtest_v1_crosswalk_by_asset.csv"
    by_day_csv = output_dir / "backtest_v1_crosswalk_by_asset_day.csv"
    write_csv(
        layer_csv,
        layer_rows,
        ["layer_order", "layer", "row_count", "candidate_count", "asset_count", "status", "attrition_from_previous", "notes"],
    )
    write_csv(by_asset_csv, by_asset_rows, ["layer", "asset", "row_count", "event_rows_sum", "best_pnl"])
    write_csv(by_day_csv, by_day_rows, ["layer", "asset", "day", "row_count"])

    manifest = {
        "schema_version": "backtest_v1_crosswalk_report_v1",
        "created_utc": utc_now(),
        "status": "OK_WITH_REPRODUCIBILITY_BLOCKERS" if reproducibility_blockers else "OK",
        "system_positioning": "multiasset_search_safe_screener_not_full_btc_completion_residual_baseline",
        "data_root": str(data_root),
        "output_dir": str(output_dir),
        "valid_days": list(VALID_DAYS),
        "blocklisted_days_excluded": list(BLOCKLISTED_DAYS),
        "outputs": {
            "layers_csv": str(layer_csv),
            "by_asset_csv": str(by_asset_csv),
            "by_asset_day_csv": str(by_day_csv),
        },
        "inputs": {
            "core_manifest": str(core_manifest_path),
            "search_manifest": str(search_manifest_path),
            "matrix_manifest": str(matrix_manifest_path),
            "catalog_manifest": str(catalog_manifest_path),
            "shortlist_manifest": str(shortlist_manifest_path),
            "queue_manifest": str(queue_manifest_path),
            "audit_manifest": str(audit_manifest_path),
        },
        "btc_attrition_path": btc_path,
        "best_queue_pnl_semantics": {
            "meaning": "V1 best_queue_pnl is a search-safe queue-screening metric from public/proxy evidence.",
            "not_included": [
                "pair-completion state machine PnL",
                "strict rescue close opportunity",
                "residual FIFO lots",
                "mature after-fee mark recovery",
                "merge capital reuse / turnover",
                "owner private fills",
            ],
            "comparison_rule": "Do not compare directly to old BTC pair_pnl/ROI until xuan bridge parity is complete.",
        },
        "reproducibility": {
            "configs": config_status,
            "runner_scripts": script_status,
            "blockers": reproducibility_blockers,
        },
        "layer_rows": layer_rows,
    }
    manifest_path = output_dir / "BACKTEST_V1_CROSSWALK_REPORT.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("status", "system_positioning", "btc_attrition_path", "outputs")}, indent=2, sort_keys=True))
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    args.data_root = args.data_root.expanduser()
    args.repo_root = args.repo_root.expanduser()
    if args.output_dir is None:
        args.output_dir = args.data_root / "derived/contract_examples/backtest_v1_crosswalk_latest"
    args.output_dir = args.output_dir.expanduser()
    build_report(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

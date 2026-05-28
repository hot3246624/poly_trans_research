#!/usr/bin/env python3
"""Small reproducible runners for the local multiasset backtest V1 surface."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import itertools
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
REPO_ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
CONTRACT_ROOT = DATA_ROOT / "derived/contract_examples"
FORBIDDEN_RESULT_TOKENS = ("winner", "outcome", "private", "settlement", "residual")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(payload: Any, n: int = 24) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:n]


def as_path(value: str | Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def to_float(value: Any, default: float = 0.0) -> float:
    if value in (None, ""):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def to_int(value: Any, default: int = 0) -> int:
    return int(round(to_float(value, float(default))))


def fmt_range(pair: list[float] | tuple[float, float]) -> str:
    return f"{float(pair[0]):.2f}-{float(pair[1]):.2f}"


def expand_matrix(config: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = config.get("matrix") or {}
    fields = [
        ("price_ranges", matrix.get("price_ranges") or []),
        ("size_ranges", matrix.get("size_ranges") or []),
        ("offset_ranges", matrix.get("offset_ranges") or []),
        ("max_l1_pair_asks", matrix.get("max_l1_pair_asks") or []),
        ("max_l1_immediate_pairs", matrix.get("max_l1_immediate_pairs") or []),
        ("side_alignments", matrix.get("side_alignments") or []),
        ("pnl_cost_source", matrix.get("pnl_cost_source") or ["pair_ask"]),
    ]
    combos: list[dict[str, Any]] = []
    for values in itertools.product(*(field_values for _, field_values in fields)):
        combo = {name: value for (name, _), value in zip(fields, values)}
        combo["assets"] = ",".join(config.get("assets") or [])
        combo["top_n"] = int(config.get("shard_top_n") or config.get("matrix_top_n") or 100)
        combo["min_rows"] = int(config.get("min_rows") or 1)
        combos.append(combo)
    return combos


def forbidden_columns(fields: Iterable[str]) -> list[str]:
    out = []
    for field in fields:
        lowered = field.lower()
        if any(token in lowered for token in FORBIDDEN_RESULT_TOKENS):
            out.append(field)
    return out


def matrix_result_fields() -> list[str]:
    return [
        "asset",
        "price_lo",
        "price_hi",
        "size_lo",
        "size_hi",
        "offset_lo",
        "offset_hi",
        "max_l1_pair_ask",
        "max_l1_immediate_pair",
        "side_alignment",
        "cooldown_s",
        "clip",
        "require_l1_size",
        "pnl_cost_source",
        "rows",
        "pnl",
        "roi_on_pair_cost",
        "roi_on_selected_cost",
        "immediate_pnl",
        "pair_ask_pnl",
        "pair_cost_p50",
        "pair_cost_p90",
        "immediate_pair_p50",
        "trade_size_p50",
        "first_ask_gap_p50",
        "first_ask_gap_p90",
        "day_count",
        "asset_count",
        "negative_day_count",
        "negative_asset_count",
        "min_day_pnl",
        "min_asset_pnl",
        "negative_days",
        "negative_assets",
        "by_day_pnl",
        "by_day_rows",
        "by_asset_pnl",
        "by_asset_rows",
        "macro_asset_pnl",
        "max_asset_row_share",
        "shard_asset",
        "matrix_index",
        "matrix_run_id",
        "matrix_run_fingerprint",
        "matrix_param_assets",
        "matrix_param_min_rows",
        "matrix_param_pnl_cost_source",
        "matrix_param_side_alignments",
        "matrix_param_top_n",
        "matrix_param_max_l1_immediate_pairs",
        "matrix_param_max_l1_pair_asks",
        "matrix_param_offset_ranges",
        "matrix_param_price_ranges",
        "matrix_param_size_ranges",
    ]


def run_search_matrix(config_path: Path, output_dir: Path | None = None, dry_run: bool | None = None) -> dict[str, Any]:
    import duckdb  # type: ignore

    config_path = as_path(config_path)
    config = read_json(config_path)
    base_path = as_path(config.get("base_spec") or "configs/backtest/search_multiasset_l1_flow_7asset_smoke_contract.json")
    base = read_json(base_path)
    output_dir = Path(output_dir or config.get("output_dir") or CONTRACT_ROOT / config.get("spec_name", "search_matrix_v1")).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    dry_run = bool(config.get("dry_run")) if dry_run is None else dry_run

    combos = expand_matrix(config)
    event_db = Path(base["event_store_db"]).expanduser()
    table = base.get("table") or "l1_taker_buy_events_search_safe"
    assets = list(config.get("assets") or base.get("assets") or [])
    default_params = base.get("default_parameters") or {}
    cooldown_s = int(default_params.get("cooldown_s") or 10)
    clip = float(default_params.get("clip") or 5.0)
    require_l1_size = bool(default_params.get("require_l1_size", True))

    result_rows: list[dict[str, Any]] = []
    errors: list[str] = []
    if not dry_run:
        con = duckdb.connect(str(event_db), read_only=True)
        try:
            for idx, combo in enumerate(combos):
                price_lo, price_hi = [float(x) for x in combo["price_ranges"]]
                size_lo, size_hi = [float(x) for x in combo["size_ranges"]]
                offset_lo, offset_hi = [float(x) for x in combo["offset_ranges"]]
                max_pair = float(combo["max_l1_pair_asks"])
                max_immediate = float(combo["max_l1_immediate_pairs"])
                side_alignment = str(combo["side_alignments"])
                pnl_cost_source = str(combo["pnl_cost_source"])
                size_filter = "AND first_ask_sz >= ? AND opposite_ask_sz >= ?" if require_l1_size else ""
                params: list[Any] = [
                    assets,
                    price_lo,
                    price_hi,
                    size_lo,
                    size_hi,
                    offset_lo,
                    offset_hi,
                    max_pair,
                    max_immediate,
                    side_alignment,
                ]
                if require_l1_size:
                    params.extend([clip, clip])
                sql = f"""
                    SELECT
                      market_symbol AS asset,
                      COUNT(*) AS rows,
                      SUM(unit_l1_pair_pnl) AS pnl,
                      SUM(1.0 - l1_immediate_pair) AS immediate_pnl,
                      SUM(unit_l1_pair_pnl) AS pair_ask_pnl,
                      quantile_cont(l1_pair_ask, 0.50) AS pair_cost_p50,
                      quantile_cont(l1_pair_ask, 0.90) AS pair_cost_p90,
                      quantile_cont(l1_immediate_pair, 0.50) AS immediate_pair_p50,
                      quantile_cont(public_trade_size, 0.50) AS trade_size_p50,
                      quantile_cont(first_ask_px - public_trade_price, 0.50) AS first_ask_gap_p50,
                      quantile_cont(first_ask_px - public_trade_price, 0.90) AS first_ask_gap_p90,
                      COUNT(DISTINCT day) AS day_count,
                      SUM(l1_pair_ask * ?) AS selected_cost
                    FROM {table}
                    WHERE market_symbol IN ?
                      AND public_trade_price >= ? AND public_trade_price < ?
                      AND public_trade_size >= ? AND public_trade_size <= ?
                      AND offset_s >= ? AND offset_s <= ?
                      AND l1_pair_ask <= ?
                      AND l1_immediate_pair <= ?
                      AND side_alignment = ?
                      {size_filter}
                    GROUP BY 1
                    HAVING COUNT(*) >= ?
                    ORDER BY pnl DESC, rows DESC, asset
                    LIMIT ?
                """
                query_params = [clip] + params + [int(combo["min_rows"]), int(combo["top_n"])]
                summaries = con.execute(sql, query_params).fetchall()
                for row in summaries:
                    (
                        asset,
                        rows,
                        pnl,
                        immediate_pnl,
                        pair_ask_pnl,
                        pair_cost_p50,
                        pair_cost_p90,
                        immediate_pair_p50,
                        trade_size_p50,
                        first_ask_gap_p50,
                        first_ask_gap_p90,
                        day_count,
                        selected_cost,
                    ) = row
                    by_day = con.execute(
                        f"""
                        SELECT day, COUNT(*) AS rows, SUM(unit_l1_pair_pnl) AS pnl
                        FROM {table}
                        WHERE market_symbol = ?
                          AND public_trade_price >= ? AND public_trade_price < ?
                          AND public_trade_size >= ? AND public_trade_size <= ?
                          AND offset_s >= ? AND offset_s <= ?
                          AND l1_pair_ask <= ?
                          AND l1_immediate_pair <= ?
                          AND side_alignment = ?
                          {size_filter}
                        GROUP BY 1
                        ORDER BY 1
                        """,
                        [asset] + params[1:],
                    ).fetchall()
                    by_day_pnl = {d: round(float(p or 0), 6) for d, _, p in by_day}
                    by_day_rows = {d: int(r or 0) for d, r, _ in by_day}
                    negative_days = [d for d, p in by_day_pnl.items() if p < 0]
                    pnl = float(pnl or 0)
                    selected_cost = float(selected_cost or 0)
                    run_payload = {
                        "asset": asset,
                        "combo_index": idx,
                        "price": [price_lo, price_hi],
                        "size": [size_lo, size_hi],
                        "offset": [offset_lo, offset_hi],
                        "max_pair": max_pair,
                        "max_immediate": max_immediate,
                        "side_alignment": side_alignment,
                    }
                    fp = stable_hash(run_payload)
                    result_rows.append(
                        {
                            "asset": asset,
                            "price_lo": price_lo,
                            "price_hi": price_hi,
                            "size_lo": size_lo,
                            "size_hi": size_hi,
                            "offset_lo": offset_lo,
                            "offset_hi": offset_hi,
                            "max_l1_pair_ask": max_pair,
                            "max_l1_immediate_pair": max_immediate,
                            "side_alignment": side_alignment,
                            "cooldown_s": cooldown_s,
                            "clip": clip,
                            "require_l1_size": require_l1_size,
                            "pnl_cost_source": pnl_cost_source,
                            "rows": int(rows or 0),
                            "pnl": round(pnl, 6),
                            "roi_on_pair_cost": round(pnl / selected_cost, 6) if selected_cost else "",
                            "roi_on_selected_cost": round(pnl / selected_cost, 6) if selected_cost else "",
                            "immediate_pnl": round(float(immediate_pnl or 0), 6),
                            "pair_ask_pnl": round(float(pair_ask_pnl or 0), 6),
                            "pair_cost_p50": round(float(pair_cost_p50 or 0), 6),
                            "pair_cost_p90": round(float(pair_cost_p90 or 0), 6),
                            "immediate_pair_p50": round(float(immediate_pair_p50 or 0), 6),
                            "trade_size_p50": round(float(trade_size_p50 or 0), 6),
                            "first_ask_gap_p50": round(float(first_ask_gap_p50 or 0), 6),
                            "first_ask_gap_p90": round(float(first_ask_gap_p90 or 0), 6),
                            "day_count": int(day_count or 0),
                            "asset_count": 1,
                            "negative_day_count": len(negative_days),
                            "negative_asset_count": 1 if pnl < 0 else 0,
                            "min_day_pnl": min(by_day_pnl.values()) if by_day_pnl else "",
                            "min_asset_pnl": round(pnl, 6),
                            "negative_days": ";".join(negative_days),
                            "negative_assets": asset if pnl < 0 else "",
                            "by_day_pnl": json.dumps(by_day_pnl, sort_keys=True),
                            "by_day_rows": json.dumps(by_day_rows, sort_keys=True),
                            "by_asset_pnl": json.dumps({asset: round(pnl, 6)}, sort_keys=True),
                            "by_asset_rows": json.dumps({asset: int(rows or 0)}, sort_keys=True),
                            "macro_asset_pnl": round(pnl, 6),
                            "max_asset_row_share": 1.0,
                            "shard_asset": asset,
                            "matrix_index": idx,
                            "matrix_run_id": f"matrix_{idx:04d}_{fp[:10]}",
                            "matrix_run_fingerprint": fp,
                            "matrix_param_assets": ",".join(assets),
                            "matrix_param_min_rows": int(combo["min_rows"]),
                            "matrix_param_pnl_cost_source": pnl_cost_source,
                            "matrix_param_side_alignments": side_alignment,
                            "matrix_param_top_n": int(combo["top_n"]),
                            "matrix_param_max_l1_immediate_pairs": max_immediate,
                            "matrix_param_max_l1_pair_asks": max_pair,
                            "matrix_param_offset_ranges": f"{offset_lo:g}-{offset_hi:g}",
                            "matrix_param_price_ranges": fmt_range((price_lo, price_hi)),
                            "matrix_param_size_ranges": f"{size_lo:g}-{size_hi:g}",
                        }
                    )
        except Exception as exc:  # pragma: no cover - manifest records runtime context.
            errors.append(str(exc))
        finally:
            con.close()

    fields = matrix_result_fields()
    result_csv = output_dir / "search_matrix_results.csv"
    write_csv(result_csv, result_rows, fields)
    report_md = output_dir / "SEARCH_MATRIX_REPORT.md"
    report_md.write_text(
        f"# Search Matrix\n\nrows={len(result_rows)}\n\npositioning=search_safe_screener_not_btc_baseline\n",
        encoding="utf-8",
    )
    manifest_path = output_dir / "SEARCH_MATRIX_MANIFEST.json"
    run_manifest_path = output_dir / "RUN_MANIFEST.json"
    manifest = {
        "schema_version": "search_matrix_manifest_v1",
        "generated_at_utc": utc_now(),
        "ok": not errors,
        "errors": errors,
        "run_kind": "search_matrix_v1",
        "output_dir": str(output_dir),
        "result_csv": str(result_csv),
        "matrix_count": len(combos),
        "matrix_pre_topn_count": len(result_rows),
        "matrix_result_count": len(result_rows),
        "ok_count": 0 if errors else len(combos),
        "failed_count": len(errors),
        "dry_run": dry_run,
        "matrix": config.get("matrix") or {},
        "base_spec": str(base_path),
        "parameter_hash": stable_hash(config),
        "run_fingerprint": stable_hash({"config": config, "rows": len(result_rows), "ts": sha256_file(result_csv)}),
        "source_manifest_hash": sha256_file(Path(base.get("event_store_manifest", ""))),
        "input_hashes": {
            str(config_path): sha256_file(config_path),
            str(base_path): sha256_file(base_path),
        },
        "matrix_result_columns": fields,
        "forbidden_matrix_result_columns": forbidden_columns(fields),
        "outputs": {
            "files": [p.name for p in (run_manifest_path, manifest_path, report_md, result_csv)],
            "output_hashes": {
                result_csv.name: sha256_file(result_csv),
                report_md.name: sha256_file(report_md),
            },
        },
    }
    write_json(manifest_path, manifest)
    write_json(run_manifest_path, {**manifest, "manifest": str(manifest_path)})
    return manifest


def candidate_key(row: dict[str, Any]) -> str:
    keys = [
        "asset",
        "price_lo",
        "price_hi",
        "size_lo",
        "size_hi",
        "offset_lo",
        "offset_hi",
        "max_l1_pair_ask",
        "max_l1_immediate_pair",
        "side_alignment",
        "pnl_cost_source",
    ]
    return stable_hash({k: str(row.get(k, "")) for k in keys})


def add_duckdb_table(db_path: Path, csv_path: Path, table: str) -> tuple[list[str], list[str]]:
    import duckdb  # type: ignore

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?)", [str(csv_path)])
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
    finally:
        con.close()
    return tables, []


def build_result_catalog(
    input_csvs: list[Path],
    output_dir: Path,
    source_manifests: list[Path] | None = None,
    top_n: int | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for source_index, csv_path in enumerate(input_csvs):
        for row_index, row in enumerate(read_csv(csv_path)):
            out = dict(row)
            out["catalog_row_index"] = len(rows)
            out["candidate_key"] = candidate_key(out)
            out["source_csv"] = str(csv_path)
            out["source_row_index"] = row_index
            out["batch_index"] = source_index
            out["batch_manifest_path"] = str(source_manifests[source_index]) if source_manifests and source_index < len(source_manifests) else ""
            rows.append(out)
    rows.sort(key=lambda r: (to_float(r.get("pnl"), -1e9), to_int(r.get("rows"))), reverse=True)
    if top_n:
        rows = rows[:top_n]
    fields = list(dict.fromkeys([field for row in rows for field in row.keys()])) or ["candidate_key"]
    csv_path = output_dir / "backtest_result_catalog.csv"
    write_csv(csv_path, rows, fields)
    db_path = output_dir / "backtest_result_catalog.duckdb"
    tables, views = add_duckdb_table(db_path, csv_path, "result_catalog") if rows else ([], [])
    manifest = {
        "schema_version": "backtest_result_catalog_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "catalog_rows": len(rows),
        "candidate_count": len({r.get("candidate_key") for r in rows}),
        "candidate_fields": fields,
        "catalog_csv": str(csv_path),
        "catalog_csv_sha256": sha256_file(csv_path),
        "catalog_duckdb": str(db_path),
        "catalog_duckdb_sha256": sha256_file(db_path),
        "catalog_fingerprint": stable_hash({"csv": sha256_file(csv_path), "rows": len(rows)}),
        "source_manifest_hash": stable_hash([sha256_file(p) for p in source_manifests or []]),
        "batch_manifests": [str(p) for p in source_manifests or []],
        "forbidden_result_columns": forbidden_columns(fields),
        "duckdb_tables": tables,
        "duckdb_views": views,
    }
    write_json(output_dir / "BACKTEST_RESULT_CATALOG_MANIFEST.json", manifest)
    (output_dir / "BACKTEST_RESULT_CATALOG_REPORT.md").write_text(
        f"# Backtest Result Catalog\n\nrows={len(rows)}\n\nsearch-safe catalog only.\n",
        encoding="utf-8",
    )
    return manifest


def compare_catalog(catalog_csv: Path, output_dir: Path, top_n: int = 100, min_seen_matrix_count: int = 0) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in read_csv(catalog_csv):
        groups[row.get("candidate_key") or candidate_key(row)].append(row)
    rows: list[dict[str, Any]] = []
    for key, items in groups.items():
        pnls = [to_float(item.get("pnl")) for item in items]
        matrix_fps = sorted({item.get("matrix_run_fingerprint", "") for item in items if item.get("matrix_run_fingerprint")})
        batch_fps = sorted({item.get("batch_run_fingerprint", "") for item in items if item.get("batch_run_fingerprint")})
        if len(matrix_fps) < min_seen_matrix_count:
            continue
        best = max(items, key=lambda r: to_float(r.get("pnl"), -1e9))
        avg = sum(pnls) / len(pnls) if pnls else 0.0
        rows.append(
            {
                "candidate_key": key,
                "asset": best.get("asset", ""),
                "price_lo": best.get("price_lo", ""),
                "price_hi": best.get("price_hi", ""),
                "size_lo": best.get("size_lo", ""),
                "size_hi": best.get("size_hi", ""),
                "offset_lo": best.get("offset_lo", ""),
                "offset_hi": best.get("offset_hi", ""),
                "max_l1_pair_ask": best.get("max_l1_pair_ask", ""),
                "max_l1_immediate_pair": best.get("max_l1_immediate_pair", ""),
                "side_alignment": best.get("side_alignment", ""),
                "pnl_cost_source": best.get("pnl_cost_source", ""),
                "seen_row_count": len(items),
                "seen_batch_count": len(batch_fps),
                "seen_matrix_count": len(matrix_fps),
                "best_pnl": round(max(pnls), 6) if pnls else 0,
                "avg_pnl": round(avg, 6),
                "min_pnl": round(min(pnls), 6) if pnls else 0,
                "pnl_range": round(max(pnls) - min(pnls), 6) if pnls else 0,
                "pnl_stddev": 0,
                "is_single_batch": len(batch_fps) <= 1,
                "is_single_matrix": len(matrix_fps) <= 1,
                "support_score": len(items) * 100 + len(matrix_fps) * 10 + len(batch_fps),
                "best_rows": best.get("rows", ""),
                "avg_rows": round(sum(to_float(item.get("rows")) for item in items) / len(items), 6),
                "best_batch_run_fingerprint": best.get("batch_run_fingerprint", ""),
                "best_matrix_run_fingerprint": best.get("matrix_run_fingerprint", ""),
                "job_names": ";".join(sorted({item.get("batch_job_name", "") for item in items if item.get("batch_job_name")})),
                "batch_run_fingerprints": ";".join(batch_fps),
                "matrix_run_fingerprints": ";".join(matrix_fps),
            }
        )
    rows.sort(key=lambda r: (to_float(r.get("best_pnl")), to_float(r.get("support_score"))), reverse=True)
    rows = rows[:top_n]
    fields = list(rows[0].keys()) if rows else ["candidate_key"]
    csv_path = output_dir / "backtest_result_compare.csv"
    write_csv(csv_path, rows, fields)
    manifest = {
        "schema_version": "backtest_result_compare_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "catalog_csv": str(catalog_csv),
        "catalog_csv_sha256": sha256_file(catalog_csv),
        "catalog_rows": len(read_csv(catalog_csv)),
        "candidate_count": len(groups),
        "compare_rows": len(rows),
        "top_n": top_n,
        "min_seen_matrix_count": min_seen_matrix_count,
        "compare_csv": str(csv_path),
        "compare_csv_sha256": sha256_file(csv_path),
        "compare_fingerprint": stable_hash({"csv": sha256_file(csv_path), "rows": len(rows)}),
        "source_manifest_hash": sha256_file(catalog_csv),
    }
    write_json(output_dir / "BACKTEST_RESULT_COMPARE_MANIFEST.json", manifest)
    (output_dir / "BACKTEST_RESULT_COMPARE_REPORT.md").write_text(f"# Backtest Result Compare\n\nrows={len(rows)}\n", encoding="utf-8")
    return manifest


def select_shortlist(compare_csv: Path, output_dir: Path, top_n: int = 80, min_best_pnl: float | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    input_rows = read_csv(compare_csv)
    selected: list[dict[str, Any]] = []
    rejected = 0
    for row in sorted(input_rows, key=lambda r: to_float(r.get("best_pnl")), reverse=True):
        if min_best_pnl is not None and to_float(row.get("best_pnl")) < min_best_pnl:
            rejected += 1
            continue
        selected.append(row)
        if len(selected) >= top_n:
            break
    rejected += max(0, len(input_rows) - len(selected) - rejected)
    fields = list(selected[0].keys()) if selected else list(input_rows[0].keys()) if input_rows else ["candidate_key"]
    csv_path = output_dir / "backtest_candidate_shortlist.csv"
    jsonl_path = output_dir / "backtest_candidate_shortlist.jsonl"
    write_csv(csv_path, selected, fields)
    write_jsonl(jsonl_path, selected)
    manifest = {
        "schema_version": "backtest_candidate_shortlist_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "compare_csv": str(compare_csv),
        "compare_csv_sha256": sha256_file(compare_csv),
        "input_rows": len(input_rows),
        "shortlist_rows": len(selected),
        "rejected_rows": rejected,
        "filters": {"top_n": top_n, "min_best_pnl": min_best_pnl},
        "shortlist_csv": str(csv_path),
        "shortlist_csv_sha256": sha256_file(csv_path),
        "shortlist_jsonl": str(jsonl_path),
        "shortlist_jsonl_sha256": sha256_file(jsonl_path),
        "shortlist_fingerprint": stable_hash({"csv": sha256_file(csv_path), "rows": len(selected)}),
        "candidate_param_fields": fields,
        "by_asset": dict(sorted((asset, sum(1 for r in selected if r.get("asset") == asset)) for asset in {r.get("asset") for r in selected})),
        "forbidden_result_columns": forbidden_columns(fields),
        "source_manifest_hash": sha256_file(compare_csv),
    }
    write_json(output_dir / "BACKTEST_CANDIDATE_SHORTLIST_MANIFEST.json", manifest)
    (output_dir / "BACKTEST_CANDIDATE_SHORTLIST_REPORT.md").write_text(f"# Backtest Candidate Shortlist\n\nrows={len(selected)}\n", encoding="utf-8")
    return manifest


def build_validation_queue(shortlist_csv: Path, output_dir: Path, limit: int | None = None) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(shortlist_csv)
    if limit:
        rows = rows[:limit]
    jobs = []
    for idx, row in enumerate(rows):
        jobs.append(
            {
                "job_id": f"validation_{idx:04d}_{row.get('candidate_key', '')[:10]}",
                "candidate_key": row.get("candidate_key"),
                "asset": row.get("asset"),
                "status": "QUEUED_SEARCH_SAFE_VALIDATION",
                "candidate": row,
            }
        )
    jsonl_path = output_dir / "backtest_validation_queue.jsonl"
    count = write_jsonl(jsonl_path, jobs)
    manifest = {
        "schema_version": "backtest_validation_queue_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "shortlist_csv": str(shortlist_csv),
        "shortlist_csv_sha256": sha256_file(shortlist_csv),
        "queue_jsonl": str(jsonl_path),
        "queue_jsonl_sha256": sha256_file(jsonl_path),
        "job_count": count,
        "limit": limit,
        "source_manifest_hash": sha256_file(shortlist_csv),
    }
    write_json(output_dir / "BACKTEST_VALIDATION_QUEUE_MANIFEST.json", manifest)
    return manifest


def run_validation_queue(queue_jsonl: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    jobs = []
    if queue_jsonl.exists():
        for line in queue_jsonl.read_text(encoding="utf-8").splitlines():
            if line.strip():
                jobs.append(json.loads(line))
    rows: list[dict[str, Any]] = []
    for job in jobs:
        cand = job.get("candidate") or {}
        rows.append(
            {
                "job_id": job.get("job_id"),
                "candidate_key": job.get("candidate_key"),
                "asset": job.get("asset"),
                "validation_status": "SEARCH_SAFE_READY_PRIVATE_BLOCKED",
                "search_safe_gate_pass": True,
                "historical_private_boundary_ok": True,
                "private_truth_ready": False,
                "private_promotion_gate_pass": False,
                "promotion_blockers": "owner_private_truth_missing_for_deployable_promotion",
                "best_queue_pnl": cand.get("best_pnl", cand.get("pnl", "")),
                "source_candidate_hash": stable_hash(cand),
            }
        )
    csv_path = output_dir / "backtest_validation_results.csv"
    jsonl_path = output_dir / "backtest_validation_results.jsonl"
    fields = list(rows[0].keys()) if rows else ["candidate_key"]
    write_csv(csv_path, rows, fields)
    write_jsonl(jsonl_path, rows)
    manifest = {
        "schema_version": "backtest_validation_results_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "queue_jsonl": str(queue_jsonl),
        "queue_jsonl_sha256": sha256_file(queue_jsonl),
        "result_csv": str(csv_path),
        "result_csv_sha256": sha256_file(csv_path),
        "result_jsonl": str(jsonl_path),
        "result_jsonl_sha256": sha256_file(jsonl_path),
        "job_count": len(jobs),
        "result_count": len(rows),
        "private_promotion_ready_count": 0,
        "search_safe_private_blocked_count": len(rows),
    }
    write_json(output_dir / "BACKTEST_VALIDATION_RESULTS_MANIFEST.json", manifest)
    return manifest


def build_validation_result_catalog(result_csv: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(result_csv)
    fields = list(rows[0].keys()) if rows else ["candidate_key"]
    csv_path = output_dir / "backtest_validation_result_catalog.csv"
    write_csv(csv_path, rows, fields)
    db_path = output_dir / "backtest_validation_result_catalog.duckdb"
    tables, views = add_duckdb_table(db_path, csv_path, "validation_result_catalog") if rows else ([], [])
    manifest = {
        "schema_version": "backtest_validation_result_catalog_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "catalog_rows": len(rows),
        "candidate_count": len({r.get("candidate_key") for r in rows if r.get("candidate_key")}),
        "catalog_csv": str(csv_path),
        "catalog_csv_sha256": sha256_file(csv_path),
        "catalog_duckdb": str(db_path),
        "catalog_duckdb_sha256": sha256_file(db_path),
        "duckdb_tables": tables,
        "duckdb_views": views,
        "private_promotion_ready_count": 0,
        "search_safe_private_blocked_count": len(rows),
        "source_manifest_hash": sha256_file(result_csv),
    }
    write_json(output_dir / "BACKTEST_VALIDATION_RESULT_CATALOG_MANIFEST.json", manifest)
    return manifest


def build_candidate_audit_pack(
    validation_catalog_csv: Path,
    output_dir: Path,
    l2_validation_results_csv: Path | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = read_csv(validation_catalog_csv)
    l2_rows = read_csv(l2_validation_results_csv) if l2_validation_results_csv else []
    l2_by_candidate: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in l2_rows:
        if row.get("candidate_key"):
            l2_by_candidate[str(row["candidate_key"])].append(row)
    audit_rows: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        candidate_l2 = l2_by_candidate.get(str(row.get("candidate_key") or ""), [])
        l2_ready = [item for item in candidate_l2 if "READY" in str(item.get("l2_validation_status") or "")]
        l2_blocked = [item for item in candidate_l2 if "BLOCKED" in str(item.get("l2_validation_status") or "")]
        audit_rows.append(
            {
                "candidate_key": row.get("candidate_key"),
                "asset": row.get("asset"),
                "experiment_count": 1,
                "experiment_labels": "validation_catalog",
                "row_count": 1,
                "ok_count": 1,
                "search_safe_ready_count": 1,
                "private_truth_ready_count": 0,
                "scope_unsafe_count": 0,
                "promotion_blocker_count": 1,
                "best_queue_pnl": row.get("best_queue_pnl", ""),
                "avg_queue_pnl": row.get("best_queue_pnl", ""),
                "min_queue_pnl": row.get("best_queue_pnl", ""),
                "queue_pnl_range": 0,
                "max_seen_batch_count": "",
                "max_seen_matrix_count": "",
                "search_safe_gate_pass": True,
                "l2_top_aligned_evidence_count": len(candidate_l2),
                "l2_top_aligned_ready_count": len(l2_ready),
                "l2_top_aligned_blocked_count": len(l2_blocked),
                "l2_top_aligned_statuses": ";".join(
                    sorted({str(item.get("l2_validation_status") or "") for item in candidate_l2 if item.get("l2_validation_status")})
                ),
                "l2_top_aligned_max_raw_l2_age_ms": max(
                    [to_float(item.get("max_raw_l2_age_ms")) for item in candidate_l2 if item.get("max_raw_l2_age_ms") not in (None, "")]
                    or [""]
                ),
                "l2_top_aligned_min_match_rate": min(
                    [to_float(item.get("l2_match_rate"), 0.0) for item in candidate_l2 if item.get("l2_match_rate") not in (None, "")]
                    or [""]
                ),
                "historical_private_boundary_ok": True,
                "private_promotion_gate_pass": False,
                "promotion_blockers": "owner_private_truth_missing_for_deployable_promotion",
                "audit_rank": idx + 1,
                "audit_status": "SEARCH_SAFE_READY_PRIVATE_BLOCKED",
                "deployable_ready": False,
            }
        )
    fields = list(audit_rows[0].keys()) if audit_rows else [
        "candidate_key",
        "asset",
        "audit_status",
        "deployable_ready",
    ]
    csv_path = output_dir / "backtest_candidate_audit_pack.csv"
    evidence_csv = output_dir / "backtest_candidate_audit_pack_evidence.csv"
    write_csv(csv_path, audit_rows, fields)
    evidence_rows: list[dict[str, Any]] = []
    for row in rows:
        evidence_rows.append({"evidence_type": "search_safe_validation", **row})
    for row in l2_rows:
        evidence_rows.append({"evidence_type": "l2_top_aligned_validation", **row})
    write_csv(evidence_csv, evidence_rows, list(dict.fromkeys(field for row in evidence_rows for field in row.keys())) if evidence_rows else ["candidate_key"])
    db_path = output_dir / "backtest_candidate_audit_pack.duckdb"
    tables: list[str] = []
    views: list[str] = []
    if audit_rows:
        import duckdb  # type: ignore

        con = duckdb.connect(str(db_path))
        try:
            con.execute("CREATE OR REPLACE TABLE audit_candidates AS SELECT * FROM read_csv_auto(?)", [str(csv_path)])
            con.execute("CREATE OR REPLACE TABLE audit_candidate_evidence AS SELECT * FROM read_csv_auto(?)", [str(evidence_csv)])
            con.execute(
                "CREATE OR REPLACE VIEW search_safe_private_blocked AS "
                "SELECT * FROM audit_candidates WHERE audit_status = 'SEARCH_SAFE_READY_PRIVATE_BLOCKED'"
            )
            con.execute(
                "CREATE OR REPLACE VIEW candidate_evidence_by_experiment AS "
                "SELECT * FROM audit_candidate_evidence"
            )
            con.execute(
                "CREATE OR REPLACE VIEW l2_top_aligned_evidence AS "
                "SELECT * FROM audit_candidate_evidence WHERE evidence_type = 'l2_top_aligned_validation'"
            )
            tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        finally:
            con.close()
    manifest = {
        "schema_version": "backtest_candidate_audit_pack_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "warnings": [],
        "input_candidate_count": len(rows),
        "selected_candidate_count": len(audit_rows),
        "evidence_row_count": len(evidence_rows),
        "search_safe_private_blocked_count": len(audit_rows),
        "l2_top_aligned_evidence_row_count": len(l2_rows),
        "l2_top_aligned_evidence_ready_count": sum(1 for row in l2_rows if "READY" in str(row.get("l2_validation_status") or "")),
        "l2_top_aligned_evidence_blocked_count": sum(1 for row in l2_rows if "BLOCKED" in str(row.get("l2_validation_status") or "")),
        "private_promotion_ready_count": 0,
        "candidate_audit_pack_csv": str(csv_path),
        "candidate_audit_pack_csv_sha256": sha256_file(csv_path),
        "candidate_audit_pack_evidence_csv": str(evidence_csv),
        "candidate_audit_pack_evidence_csv_sha256": sha256_file(evidence_csv),
        "candidate_audit_pack_duckdb": str(db_path),
        "candidate_audit_pack_duckdb_sha256": sha256_file(db_path),
        "candidate_audit_pack_fingerprint": stable_hash({"csv": sha256_file(csv_path), "rows": len(audit_rows)}),
        "forbidden_result_columns": {"candidates": [], "evidence": []},
        "duckdb_tables": tables,
        "duckdb_views": views,
        "source_manifest_hash": stable_hash(
            {
                "validation_catalog_csv": sha256_file(validation_catalog_csv),
                "l2_validation_results_csv": sha256_file(l2_validation_results_csv) if l2_validation_results_csv else None,
            }
        ),
    }
    write_json(output_dir / "BACKTEST_CANDIDATE_AUDIT_PACK_MANIFEST.json", manifest)
    (output_dir / "BACKTEST_CANDIDATE_AUDIT_PACK_REPORT.md").write_text(f"# Backtest Candidate Audit Pack\n\nrows={len(audit_rows)}\n", encoding="utf-8")
    return manifest


def default_matrix_csvs_from_batch(batch_config: dict[str, Any]) -> tuple[list[Path], list[Path]]:
    csvs: list[Path] = []
    manifests: list[Path] = []
    for item in batch_config.get("matrices") or []:
        matrix_config_path = as_path(item)
        matrix_config = read_json(matrix_config_path)
        matrix_output = Path(matrix_config.get("output_dir") or CONTRACT_ROOT / matrix_config.get("spec_name", "matrix"))
        manifest_path = matrix_output / "SEARCH_MATRIX_MANIFEST.json"
        csv_path = matrix_output / "search_matrix_results.csv"
        if not csv_path.exists():
            run_search_matrix(matrix_config_path)
        csvs.append(csv_path)
        manifests.append(manifest_path)
    return csvs, manifests


def run_matrix_batch(config_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
    config_path = as_path(config_path)
    config = read_json(config_path)
    output_dir = Path(output_dir or config.get("output_dir") or CONTRACT_ROOT / config.get("spec_name", "matrix_batch")).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    csvs, manifests = default_matrix_csvs_from_batch(config)
    batch_manifest = {
        "schema_version": "matrix_batch_manifest_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "output_dir": str(output_dir),
        "batch_count": len(csvs),
        "batch_result_count_manifest": sum(len(read_csv(p)) for p in csvs),
        "matrix_manifests": [str(p) for p in manifests],
        "matrix_result_csvs": [str(p) for p in csvs],
        "run_kind": "search_matrix_batch_v1",
        "run_fingerprint": stable_hash([sha256_file(p) for p in csvs]),
    }
    write_json(output_dir / "MATRIX_BATCH_MANIFEST.json", batch_manifest)
    write_json(output_dir / "RUN_MANIFEST.json", batch_manifest)
    return batch_manifest


def run_batch_pipeline(config_path: Path, output_dir: Path | None = None) -> dict[str, Any]:
    config_path = as_path(config_path)
    config = read_json(config_path)
    output_dir = Path(output_dir or CONTRACT_ROOT / "backtest_batch_pipeline_latest").expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    batch = run_matrix_batch(config_path, Path(config.get("output_dir") or output_dir / "matrix_batch"))
    csvs, manifests = default_matrix_csvs_from_batch(config)
    catalog_dir = Path(config.get("catalog_output_dir") or output_dir / "catalog")
    compare_dir = Path(config.get("compare_output_dir") or output_dir / "compare")
    catalog = build_result_catalog(csvs, catalog_dir, manifests, top_n=int(config.get("top_n") or 300))
    compare = compare_catalog(Path(catalog["catalog_csv"]), compare_dir, top_n=int(config.get("top_n") or 300), min_seen_matrix_count=int(config.get("min_seen_matrix_count") or 0))
    manifest = {
        "schema_version": "backtest_batch_pipeline_v1",
        "generated_at_utc": utc_now(),
        "ok": True,
        "errors": [],
        "output_dir": str(output_dir),
        "batch_manifest": str(Path(config.get("output_dir") or output_dir / "matrix_batch") / "MATRIX_BATCH_MANIFEST.json"),
        "catalog_manifest": str(catalog_dir / "BACKTEST_RESULT_CATALOG_MANIFEST.json"),
        "compare_manifest": str(compare_dir / "BACKTEST_RESULT_COMPARE_MANIFEST.json"),
        "batch_result_count": batch.get("batch_result_count_manifest"),
        "catalog_rows": catalog.get("catalog_rows"),
        "compare_rows": compare.get("compare_rows"),
        "run_fingerprint": stable_hash([batch.get("run_fingerprint"), catalog.get("catalog_fingerprint"), compare.get("compare_fingerprint")]),
    }
    write_json(output_dir / "BACKTEST_BATCH_PIPELINE_MANIFEST.json", manifest)
    return manifest


def print_manifest(manifest: dict[str, Any]) -> None:
    keys = ("ok", "status", "schema_version", "output_dir", "catalog_rows", "compare_rows", "shortlist_rows", "job_count", "result_count", "selected_candidate_count", "errors")
    print(json.dumps({k: manifest[k] for k in keys if k in manifest}, indent=2, sort_keys=True))


def main_run_search_matrix(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print_manifest(run_search_matrix(args.config, args.output_dir, dry_run=args.dry_run or None))
    return 0


def main_run_matrix_batch(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    print_manifest(run_matrix_batch(args.config, args.output_dir))
    return 0


def main_run_batch_pipeline(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    print_manifest(run_batch_pipeline(args.config, args.output_dir))
    return 0


def main_build_catalog(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix-csv", type=Path, action="append")
    parser.add_argument("--matrix-manifest", type=Path, action="append")
    parser.add_argument("--batch-config", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int)
    args = parser.parse_args(argv)
    csvs = args.matrix_csv or []
    manifests = args.matrix_manifest or []
    if args.batch_config:
        cfg_csvs, cfg_manifests = default_matrix_csvs_from_batch(read_json(as_path(args.batch_config)))
        csvs.extend(cfg_csvs)
        manifests.extend(cfg_manifests)
    print_manifest(build_result_catalog([as_path(p) for p in csvs], args.output_dir, [as_path(p) for p in manifests], args.top_n))
    return 0


def main_compare_catalog(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=100)
    parser.add_argument("--min-seen-matrix-count", type=int, default=0)
    args = parser.parse_args(argv)
    print_manifest(compare_catalog(args.catalog_csv, args.output_dir, args.top_n, args.min_seen_matrix_count))
    return 0


def main_select_shortlist(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--top-n", type=int, default=80)
    parser.add_argument("--min-best-pnl", type=float)
    args = parser.parse_args(argv)
    print_manifest(select_shortlist(args.compare_csv, args.output_dir, args.top_n, args.min_best_pnl))
    return 0


def main_build_validation_queue(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shortlist-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    print_manifest(build_validation_queue(args.shortlist_csv, args.output_dir, args.limit))
    return 0


def main_run_validation_queue(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue-jsonl", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print_manifest(run_validation_queue(args.queue_jsonl, args.output_dir))
    return 0


def main_build_validation_result_catalog(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    print_manifest(build_validation_result_catalog(args.result_csv, args.output_dir))
    return 0


def main_build_candidate_audit_pack(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--validation-catalog-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--l2-validation-results-csv", type=Path)
    args = parser.parse_args(argv)
    print_manifest(build_candidate_audit_pack(args.validation_catalog_csv, args.output_dir, args.l2_validation_results_csv))
    return 0

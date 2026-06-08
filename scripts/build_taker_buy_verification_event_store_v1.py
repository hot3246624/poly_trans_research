#!/usr/bin/env python3
"""Build a taker-BUY event-level verification store from strict V1 cache.

This is the fast final-verification backend.  It does not replace
``replay_published`` as the source of truth; it publishes a typed Parquet/DuckDB
event store only from a strict-L1 V1 cache whose replay validation passed with
``error_count=0``.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import verify_taker_buy_search_finalists_batched_sqlite as batched  # noqa: E402
import verify_taker_buy_search_finalists_strict as strict  # noqa: E402


STRING_COLUMNS = {
    "day",
    "slug",
    "condition_id",
    "winner_side",
    "trigger_iso",
    "first_side",
    "side_alignment",
    "strict_side_alignment",
    "cache_side_alignment_old",
}
BOOL_COLUMNS = {"first_is_winner"}
INT_COLUMNS = {
    "trigger_ts_ms",
    "first_l2_age_ms",
    "strict_l1_recv_ms",
    "strict_l1_age_ms",
    "verify_trade_row_id",
    "verify_strict_l1_recv_ms",
    "verify_strict_l1_age_ms",
    "verify_first_l2_age_ms",
    "verify_ceil_0_94_ts_ms",
    "verify_ceil_0_95_ts_ms",
    "verify_ceil_0_96_ts_ms",
    "verify_ceil_0_98_ts_ms",
}
PAIR_CEILINGS = (0.94, 0.95, 0.96, 0.98)


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_literal(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def count_csv_rows(path: Path) -> tuple[int, list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            return 0, []
        return sum(1 for _ in reader), list(reader.fieldnames)


def read_csv_rows(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def write_csv(path: Path, rows: list[dict[str, Any]], base_fields: list[str]) -> list[str]:
    fields = list(base_fields)
    seen = set(fields)
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    return fields


def parse_days(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(day) for day in value]
    return [part.strip() for part in str(value or "").split(",") if part.strip()]


def event_from_cache_row(row: dict[str, str]) -> dict[str, Any]:
    return {
        "condition_id": str(row["condition_id"]),
        "trigger_ts_ms": int(float(row["trigger_ts_ms"])),
        "first_side": str(row["first_side"]),
        "public_trade_price": strict.parse_float(row.get("public_trade_price"), "public_trade_price"),
        "public_trade_size": strict.parse_float(row.get("public_trade_size"), "public_trade_size"),
    }


def ceiling_prefix(ceiling: float) -> str:
    return f"verify_ceil_{str(ceiling).replace('.', '_')}"


def enrich_rows_from_replay(
    source_csv: Path,
    replay_root: Path,
    out_csv: Path,
    base_fields: list[str],
    progress_every_conditions: int,
) -> tuple[int, list[str]]:
    raw_rows, _ = read_csv_rows(source_csv)
    rows_by_day_condition: dict[tuple[str, str], list[tuple[int, dict[str, str], dict[str, Any]]]] = {}
    for idx, row in enumerate(raw_rows):
        day = str(row.get("day") or "")[:10]
        condition_id = str(row.get("condition_id") or "")
        if not day or not condition_id:
            raise RuntimeError(f"source cache row missing day/condition_id at index {idx}")
        rows_by_day_condition.setdefault((day, condition_id), []).append((idx, row, event_from_cache_row(row)))

    enriched: list[dict[str, Any] | None] = [None] * len(raw_rows)
    rows_by_day: dict[str, list[tuple[str, list[tuple[int, dict[str, str], dict[str, Any]]]]]] = {}
    for (day, condition_id), items in sorted(rows_by_day_condition.items()):
        rows_by_day.setdefault(day, []).append((condition_id, items))

    processed_conditions = 0
    for day, condition_items in sorted(rows_by_day.items()):
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.is_file():
            raise FileNotFoundError(f"missing replay SQLite for {day}: {db_path}")
        with batched.ro_connect(db_path) as conn:
            for condition_id, items in condition_items:
                events = [event for _idx, _row, event in items]
                market = batched.fetch_market(conn, condition_id)
                if market is None:
                    raise RuntimeError(f"missing BTC 5m settled market in replay: day={day} condition_id={condition_id}")
                trades = batched.resolve_trades(conn, condition_id, events, market)
                if len(trades) != len(events):
                    raise RuntimeError(f"trade resolution mismatch: day={day} condition_id={condition_id} events={len(events)} trades={len(trades)}")
                min_ts = min(int(trade["trade_ts_ms"]) for trade in trades)
                max_ts = max(int(trade["trade_ts_ms"]) for trade in trades)
                l1_keys, l1_rows = batched.load_l1_rows(conn, condition_id, min_ts, max_ts, 3000)
                l2_by_side = batched.load_l2_rows(conn, condition_id, min_ts, max_ts, 750, 30)
                for (idx, source_row, _event), trade in zip(items, trades, strict=False):
                    ts_ms = int(trade["trade_ts_ms"])
                    side = str(trade["market_side"])
                    book = batched.l1_at(l1_keys, l1_rows, ts_ms, 3000)
                    if book is None:
                        raise RuntimeError(f"missing strict L1 for cache event: day={day} condition_id={condition_id} ts={ts_ms}")
                    alignment = strict.side_alignment(book, side)
                    first_price, first_age_ms, first_worst_px, first_filled = batched.latest_l2_sweep_cached(
                        l2_by_side, side, ts_ms, float(source_row.get("clip") or 60.0), 750
                    )
                    if first_price is None:
                        raise RuntimeError(f"missing first L2 for cache event: day={day} condition_id={condition_id} ts={ts_ms} side={side}")
                    opp = strict.other(side)
                    opp_ask = book[opp]["ask"]
                    if opp_ask is None:
                        raise RuntimeError(f"missing opposite L1 ask: day={day} condition_id={condition_id} ts={ts_ms} side={side}")
                    out: dict[str, Any] = dict(source_row)
                    out.update(
                        {
                            "verify_trade_row_id": int(trade["trade_row_id"]),
                            "verify_public_trade_price": float(trade["price"]),
                            "verify_public_trade_size": float(trade["size"]),
                            "verify_strict_l1_recv_ms": int(book["recv_ms"]),
                            "verify_strict_l1_age_ms": int(book["age_ms"]),
                            "verify_side_alignment": alignment,
                            "verify_first_l2_vwap": float(first_price),
                            "verify_first_l2_age_ms": None if first_age_ms is None else int(first_age_ms),
                            "verify_first_l2_worst_px": first_worst_px,
                            "verify_first_l2_filled": first_filled,
                            "verify_opp_l1_ask": float(opp_ask),
                            "verify_l1_immediate_pair": float(first_price) + float(opp_ask),
                        }
                    )
                    for ceiling in PAIR_CEILINGS:
                        completion = batched.completion_cached(
                            l2_by_side,
                            opp,
                            ts_ms,
                            min(int(market["end_ms"]), ts_ms + 30_000),
                            float(first_price),
                            float(source_row.get("clip") or 60.0),
                            ceiling,
                        )
                        prefix = ceiling_prefix(ceiling)
                        out[f"{prefix}_hit"] = completion is not None
                        out[f"{prefix}_ts_ms"] = None if completion is None else int(completion["completion_ts_ms"])
                        out[f"{prefix}_pair_cost"] = None if completion is None else float(completion["pair_cost"])
                        out[f"{prefix}_delay_s"] = None if completion is None else float(completion["completion_delay_s"])
                        out[f"{prefix}_vwap"] = None if completion is None else float(completion["completion_vwap"])
                        out[f"{prefix}_worst_px"] = None if completion is None else completion["completion_worst_px"]
                        out[f"{prefix}_filled"] = None if completion is None else completion["completion_filled"]
                    enriched[idx] = out
                processed_conditions += 1
                if progress_every_conditions > 0 and processed_conditions % progress_every_conditions == 0:
                    done_rows = sum(1 for row in enriched if row is not None)
                    print(
                        json.dumps(
                            {
                                "stage": "replay_enrich",
                                "day": day,
                                "processed_conditions": processed_conditions,
                                "total_conditions": len(rows_by_day_condition),
                                "enriched_rows": done_rows,
                                "total_rows": len(raw_rows),
                            }
                        ),
                        flush=True,
                    )
    final_rows = [row for row in enriched if row is not None]
    if len(final_rows) != len(raw_rows):
        raise RuntimeError(f"enriched row count mismatch: source={len(raw_rows)} enriched={len(final_rows)}")
    fields = write_csv(out_csv, final_rows, base_fields)
    return len(final_rows), fields


def column_type(name: str) -> str:
    if name in STRING_COLUMNS:
        return "VARCHAR"
    if name in BOOL_COLUMNS or name.endswith("_hit"):
        return "BOOLEAN"
    if name in INT_COLUMNS:
        return "BIGINT"
    return "DOUBLE"


def typed_select(fieldnames: list[str], table: str) -> str:
    parts = []
    for name in fieldnames:
        ident = quote_ident(name)
        typ = column_type(name)
        if typ == "VARCHAR":
            expr = f"CAST({table}.{ident} AS VARCHAR)"
        else:
            expr = f"TRY_CAST({table}.{ident} AS {typ})"
        parts.append(f"{expr} AS {ident}")
    return ",\n  ".join(parts)


def validate_source(source_cache_dir: Path, allow_unvalidated: bool) -> tuple[dict[str, Any], dict[str, Any] | None]:
    manifest_path = source_cache_dir / "CACHE_MANIFEST.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"missing strict V1 cache manifest: {manifest_path}")
    manifest = load_json(manifest_path)
    validation_path = source_cache_dir / "CACHE_VALIDATION.json"
    validation = load_json(validation_path) if validation_path.is_file() else None
    if not allow_unvalidated:
        if validation is None:
            raise RuntimeError(f"strict V1 cache validation is required: {validation_path}")
        if int(validation.get("error_count", -1)) != 0:
            raise RuntimeError(f"strict V1 cache validation failed: error_count={validation.get('error_count')}")
        if validation.get("require_strict_l1") is False:
            raise RuntimeError("strict V1 cache validation did not require strict L1")
    return manifest, validation


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"event store already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-cache-dir", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, default=None, help="When set, enrich exact verification fields from replay SQLite.")
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default="taker_buy_event_store_v1")
    parser.add_argument("--label", default=None)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--allow-unvalidated-source", action="store_true")
    parser.add_argument("--progress-every-conditions", type=int, default=50)
    args = parser.parse_args()

    source_cache_dir = args.source_cache_dir.resolve()
    source_manifest, source_validation = validate_source(source_cache_dir, args.allow_unvalidated_source)
    source_csv = source_cache_dir / str(source_manifest["outputs"]["csv"])
    if not source_csv.is_file():
        raise FileNotFoundError(f"missing strict V1 cache CSV: {source_csv}")
    row_count, fieldnames = count_csv_rows(source_csv)
    expected_rows = int(source_manifest["outputs"]["row_count"])
    if row_count != expected_rows:
        raise RuntimeError(f"strict V1 cache CSV row count mismatch: csv={row_count} manifest={expected_rows}")

    label = args.label or str(source_manifest.get("label") or source_cache_dir.name)
    publish_root = args.store_root / args.store_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    publish_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_free_gb(args.store_root, args.min_free_gb)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)
        input_csv = source_csv
        input_row_count = row_count
        input_fieldnames = fieldnames
        if args.replay_root is not None:
            enriched_csv = tmp_dir / "taker_buy_verification_events_enriched.csv"
            input_row_count, input_fieldnames = enrich_rows_from_replay(
                source_csv,
                args.replay_root,
                enriched_csv,
                fieldnames,
                args.progress_every_conditions,
            )
            input_csv = enriched_csv
        dataset_dir = tmp_dir / "dataset"
        db_path = tmp_dir / "event_store.duckdb"
        started_at = utc_now()
        try:
            conn = duckdb.connect(str(db_path))
            conn.execute(f"PRAGMA threads={max(1, int(args.duckdb_threads))}")
            conn.execute(
                f"""
                CREATE VIEW raw_events AS
                SELECT * FROM read_csv(
                  {quote_literal(input_csv)},
                  header=true,
                  all_varchar=true,
                  nullstr=''
                )
                """
            )
            conn.execute(
                f"""
                CREATE TABLE taker_buy_verification_events AS
                SELECT
                  row_number() OVER () AS event_store_row_id,
                  {typed_select(input_fieldnames, "raw_events")}
                FROM raw_events
                """
            )
            db_rows = int(conn.execute("SELECT COUNT(*) FROM taker_buy_verification_events").fetchone()[0])
            if db_rows != input_row_count:
                raise RuntimeError(f"DuckDB row count mismatch: db={db_rows} csv={input_row_count}")
            day_counts = {
                str(day): int(count)
                for day, count in conn.execute(
                    "SELECT CAST(day AS VARCHAR), COUNT(*) FROM taker_buy_verification_events GROUP BY 1 ORDER BY 1"
                ).fetchall()
            }
            conn.execute(
                f"""
                COPY taker_buy_verification_events TO {quote_literal(dataset_dir)}
                (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day))
                """
            )
            conn.execute("CHECKPOINT")
            conn.close()

            parquet_files = sorted(p.relative_to(tmp_dir).as_posix() for p in dataset_dir.rglob("*.parquet"))
            if not parquet_files:
                raise RuntimeError("event store build produced no parquet files")

            validation_path = source_cache_dir / "CACHE_VALIDATION.json"
            manifest = {
                "schema_version": "taker_buy_event_store_v1",
                "store_kind": "taker_buy_event_level_final_verification",
                "store_name": args.store_name,
                "label": label,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "source_cache": {
                    "cache_dir": str(source_cache_dir),
                    "csv": str(source_csv),
                    "csv_sha256": sha256_file(source_csv),
                    "manifest_sha256": sha256_file(source_cache_dir / "CACHE_MANIFEST.json"),
                    "validation_sha256": sha256_file(validation_path) if validation_path.is_file() else None,
                    "validation_error_count": None if source_validation is None else source_validation.get("error_count"),
                    "source_row_count": row_count,
                    "replay_enriched": args.replay_root is not None,
                    "replay_root": None if args.replay_root is None else str(args.replay_root),
                },
                "days": source_manifest.get("days", sorted(day_counts)),
                "outputs": {
                    "duckdb": "event_store.duckdb",
                    "duckdb_table": "taker_buy_verification_events",
                    "parquet_glob": "dataset/**/*.parquet",
                    "parquet_files": parquet_files,
                    "row_count": input_row_count,
                    "fieldnames": ["event_store_row_id", *input_fieldnames],
                    "day_counts": day_counts,
                },
                "truth_policy": (
                    "Event store is derived from strict-L1 V1 cache validated against replay_published; "
                    "SQLite replay remains the audit source."
                ),
            }
            write_json(tmp_dir / "EVENT_STORE_MANIFEST.json", manifest)
            (tmp_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Taker-BUY Event Verification Store V1",
                        "",
                        "Typed event-level store for fast finalist verification.",
                        "Built only from strict-L1 V1 cache with replay validation error_count=0.",
                        "Use SQLite shared replay verifier for independent audit samples.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "rows": row_count, "day_counts": day_counts}, indent=2, sort_keys=True))
            return 0
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

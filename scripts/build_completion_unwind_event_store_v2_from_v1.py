#!/usr/bin/env python3
"""Upgrade completion_unwind_event_store_v1 to V2 by joining L1 delta fields.

This avoids rebuilding the expensive L2 VWAP features.  V1 remains the source
for event rows and L2-derived columns; replay_published SQLite is scanned only
for md_book_l1 adjacent-row deltas.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
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

import build_completion_unwind_event_store_v2 as v2  # noqa: E402


DEFAULT_STORE_NAME = "completion_unwind_event_store_v2"
DELTA_FIELDS = [
    "prev_side_bid",
    "prev_side_bid_sz",
    "prev_side_ask",
    "prev_side_ask_sz",
    "side_bid_delta_qty",
    "side_bid_level_drop_qty",
    "side_ask_delta_qty",
    "side_ask_level_lift_qty",
    "book_update_reason",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_days(value: str) -> list[str]:
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    return days


def parse_paths(value: str) -> list[Path]:
    paths = [Path(part.strip()) for part in value.split(",") if part.strip()]
    if not paths:
        raise ValueError("at least one v1 store dir is required")
    return paths


def free_bytes(path: Path) -> int:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    return int(shutil.disk_usage(target).free)


def require_free_gb(path: Path, min_free_gb: float) -> None:
    free_gb = free_bytes(path) / 1024**3
    if free_gb < min_free_gb:
        raise RuntimeError(f"disk guardrail failed for {path}: {free_gb:.1f}G free < {min_free_gb:.1f}G")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parquet_files_for_store(store_dir: Path) -> list[Path]:
    files = sorted((store_dir / "dataset").rglob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"no v1 parquet files under {store_dir / 'dataset'}")
    return files


def parquet_files_for_stores(store_dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for store_dir in store_dirs:
        files.extend(parquet_files_for_store(store_dir))
    return files


def load_needed_l1_keys(v1_store_dirs: list[Path], days: list[str], threads: int) -> dict[str, dict[str, dict[int, set[str]]]]:
    """Load the strict L1 row ids that V1 event rows actually reference."""
    v1_parquets = parquet_files_for_stores(v1_store_dirs)
    v1_literal = "[" + ", ".join(quote_literal(path) for path in v1_parquets) + "]"
    day_literal = "[" + ", ".join(quote_literal(day) for day in days) + "]"
    conn = duckdb.connect(":memory:")
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    rows = conn.execute(
        f"""
        SELECT CAST(day AS VARCHAR) AS day,
               condition_id,
               CAST(strict_l1_row_id AS BIGINT) AS strict_l1_row_id,
               side
        FROM read_parquet({v1_literal}, union_by_name=true)
        WHERE CAST(day AS VARCHAR) IN (SELECT * FROM UNNEST({day_literal}))
          AND strict_l1_row_id IS NOT NULL
          AND side IN ('YES', 'NO')
        GROUP BY 1, 2, 3, 4
        ORDER BY 1, 2, 3, 4
        """
    ).fetchall()
    conn.close()

    needed: dict[str, dict[str, dict[int, set[str]]]] = {}
    for day, condition_id, row_id, side in rows:
        needed.setdefault(str(day), {}).setdefault(str(condition_id), {}).setdefault(int(row_id), set()).add(str(side))
    return needed


def count_needed_keys(needed_for_day: dict[str, dict[int, set[str]]]) -> int:
    return sum(len(sides) for rows in needed_for_day.values() for sides in rows.values())


def write_l1_delta_csv(
    replay_root: Path,
    day: str,
    csv_path: Path,
    progress_every_markets: int,
    needed_for_day: dict[str, dict[int, set[str]]],
) -> dict[str, int]:
    db_path = replay_root / day / "crypto_5m.sqlite"
    if not db_path.is_file():
        raise FileNotFoundError(f"missing replay SQLite for {day}: {db_path}")

    counts = {
        "markets": 0,
        "markets_with_needed_l1": 0,
        "l1_rows_scanned": 0,
        "needed_keys": count_needed_keys(needed_for_day),
        "matched_needed_keys": 0,
        "delta_rows": 0,
    }
    fields = ["day", "condition_id", "strict_l1_row_id", "side", *DELTA_FIELDS]
    with v2.connect_ro(db_path) as conn, csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        markets = v2.fetch_markets(conn, None)
        counts["markets"] = len(markets)
        for idx, market in enumerate(markets, start=1):
            market_needed = needed_for_day.get(market.condition_id)
            if not market_needed:
                continue
            counts["markets_with_needed_l1"] += 1
            books = v2.load_l1(conn, market)
            prev = None
            for book in books:
                counts["l1_rows_scanned"] += 1
                needed_sides = market_needed.get(book.row_id)
                if not needed_sides:
                    prev = book
                    continue
                for side in sorted(needed_sides):
                    row = {
                        "day": day,
                        "condition_id": market.condition_id,
                        "strict_l1_row_id": book.row_id,
                        "side": side,
                    }
                    row.update(v2.l1_delta_fields(prev, book, side))
                    writer.writerow(row)
                    counts["matched_needed_keys"] += 1
                    counts["delta_rows"] += 1
                prev = book
            if progress_every_markets > 0 and idx % progress_every_markets == 0:
                print(
                    json.dumps(
                        {
                            "stage": "l1_delta_day",
                            "day": day,
                            "markets_done": idx,
                            "markets_total": len(markets),
                            "counts": counts,
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
    return counts


def publish_tmp(tmp_dir: Path, final_dir: Path, force: bool) -> None:
    if final_dir.exists():
        if not force:
            raise FileExistsError(f"store already exists: {final_dir}")
        backup = final_dir.with_name(f"{final_dir.name}.replaced.{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
        final_dir.rename(backup)
    tmp_dir.rename(final_dir)
def build_duckdb(
    *,
    tmp_dir: Path,
    v1_parquets: list[Path],
    delta_csv_paths: list[Path],
    threads: int,
) -> dict[str, Any]:
    db_path = tmp_dir / "event_store.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")

    v1_literal = "[" + ", ".join(quote_literal(path) for path in v1_parquets) + "]"
    delta_literal = "[" + ", ".join(quote_literal(path) for path in delta_csv_paths) + "]"
    type_literal = "{'day': 'VARCHAR', 'condition_id': 'VARCHAR', 'side': 'VARCHAR', 'book_update_reason': 'VARCHAR'}"

    conn.execute(
        f"""
        CREATE TABLE v1_events AS
        SELECT CAST(day AS VARCHAR) AS day, * EXCLUDE(day)
        FROM read_parquet({v1_literal}, union_by_name=true)
        """
    )
    conn.execute(
        f"""
        CREATE TABLE l1_delta AS
        SELECT *
        FROM read_csv({delta_literal}, header=true, union_by_name=true, auto_detect=true, types={type_literal})
        """
    )
    delta_select = ",\n               ".join(f"d.{field}" for field in DELTA_FIELDS)
    conn.execute(
        f"""
        CREATE TABLE completion_unwind_events AS
        SELECT v1.*,
               {delta_select}
        FROM v1_events v1
        LEFT JOIN l1_delta d
          ON d.day = v1.day
         AND d.condition_id = v1.condition_id
         AND d.strict_l1_row_id = v1.strict_l1_row_id
         AND d.side = v1.side
        """
    )

    total_rows = int(conn.execute("SELECT COUNT(*) FROM completion_unwind_events").fetchone()[0])
    kind_counts = {
        str(kind): int(count)
        for kind, count in conn.execute(
            "SELECT event_kind, COUNT(*) FROM completion_unwind_events GROUP BY event_kind ORDER BY event_kind"
        ).fetchall()
    }
    day_counts = {
        str(day): int(count)
        for day, count in conn.execute(
            "SELECT day, COUNT(*) FROM completion_unwind_events GROUP BY day ORDER BY day"
        ).fetchall()
    }
    reason_counts = {
        str(reason): int(count)
        for reason, count in conn.execute(
            "SELECT book_update_reason, COUNT(*) FROM completion_unwind_events GROUP BY book_update_reason ORDER BY book_update_reason"
        ).fetchall()
    }
    unmatched_delta_rows = int(
        conn.execute("SELECT COUNT(*) FROM completion_unwind_events WHERE book_update_reason IS NULL").fetchone()[0]
    )

    dataset_dir = tmp_dir / "dataset"
    dataset_dir.mkdir(exist_ok=True)
    conn.execute(
        f"""
        COPY (SELECT * FROM completion_unwind_events)
        TO {quote_literal(dataset_dir)}
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (day), OVERWRITE_OR_IGNORE TRUE)
        """
    )
    conn.execute("CHECKPOINT")
    conn.close()

    parquet_files = sorted(path.relative_to(tmp_dir).as_posix() for path in dataset_dir.rglob("*.parquet"))
    return {
        "duckdb": "event_store.duckdb",
        "duckdb_table": "completion_unwind_events",
        "parquet_glob": "dataset/**/*.parquet",
        "parquet_files": parquet_files,
        "row_count": total_rows,
        "event_kind_counts": kind_counts,
        "day_counts": day_counts,
        "book_update_reason_counts": reason_counts,
        "unmatched_delta_rows": unmatched_delta_rows,
        "source_v1_parquet_files": [str(path) for path in v1_parquets],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--v1-store-dirs", required=True)
    parser.add_argument("--store-name", default=DEFAULT_STORE_NAME)
    parser.add_argument("--days", required=True)
    parser.add_argument("--label")
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--progress-every-markets", type=int, default=100)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    days = parse_days(args.days)
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    v1_store_dirs = parse_paths(args.v1_store_dirs)
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
        started_at = utc_now()
        delta_dir = tmp_dir / "l1_delta_csv"
        delta_dir.mkdir()
        try:
            delta_csv_paths: list[Path] = []
            delta_counts: dict[str, Any] = {}
            source_replay: list[dict[str, Any]] = []
            v1_parquets = parquet_files_for_stores(v1_store_dirs)
            for day in days:
                needed_by_day = load_needed_l1_keys(v1_store_dirs, [day], args.duckdb_threads)
                needed_for_day = needed_by_day.get(day, {})
                print(
                    json.dumps(
                        {
                            "stage": "needed_l1_keys_loaded",
                            "day": day,
                            "needed_keys": count_needed_keys(needed_for_day),
                            "v1_parquet_files": len(v1_parquets),
                        },
                        sort_keys=True,
                    ),
                    flush=True,
                )
                db_path = args.replay_root / day / "crypto_5m.sqlite"
                stat = db_path.stat()
                source_replay.append(
                    {
                        "day": day,
                        "path": str(db_path),
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sqlite_sequence": v2.load_sqlite_sequence(db_path),
                    }
                )
                csv_path = delta_dir / f"{day}.l1_delta.csv"
                delta_csv_paths.append(csv_path)
                delta_counts[day] = write_l1_delta_csv(
                    args.replay_root,
                    day,
                    csv_path,
                    args.progress_every_markets,
                    needed_for_day,
                )
                del needed_by_day
                del needed_for_day

            outputs = build_duckdb(
                tmp_dir=tmp_dir,
                v1_parquets=v1_parquets,
                delta_csv_paths=delta_csv_paths,
                threads=args.duckdb_threads,
            )
            manifests = []
            for store_dir in v1_store_dirs:
                manifest_path = store_dir / "EVENT_STORE_MANIFEST.json"
                manifests.append(load_json(manifest_path) if manifest_path.exists() else {"path": str(store_dir), "missing_manifest": True})

            manifest = {
                "schema_version": "completion_unwind_event_store_v2",
                "store_name": args.store_name,
                "label": label,
                "days": days,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "source": "completion_unwind_event_store_v1_plus_replay_l1_delta",
                "source_v1_store_dirs": [str(path) for path in v1_store_dirs],
                "source_v1_manifests": manifests,
                "source_replay": source_replay,
                "l1_delta_counts": delta_counts,
                "outputs": outputs,
                "truth_policy": (
                    "V2 preserves V1 event and L2 semantics, and adds adjacent L1 delta evidence. "
                    "Delta fields are public book evidence, not private queue truth."
                ),
            }
            (tmp_dir / "EVENT_STORE_MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (tmp_dir / "README.md").write_text(
                "\n".join(
                    [
                        "# Completion/Unwind Event Store V2",
                        "",
                        "V1 completion/unwind events with adjacent L1 delta fields.",
                        "",
                        "DuckDB table: `completion_unwind_events`.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "outputs": outputs, "l1_delta_counts": delta_counts}, indent=2, sort_keys=True))
            return 0
        except Exception:
            if tmp_dir.exists():
                failed_dir = publish_root / f".{label}.failed.{os.getpid()}"
                if failed_dir.exists():
                    shutil.rmtree(failed_dir)
                tmp_dir.rename(failed_dir)
                print(json.dumps({"failed_tmp_dir": str(failed_dir)}, sort_keys=True), file=sys.stderr)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Combine per-day top-aligned L2 mart DuckDB files into one query surface."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb  # type: ignore


VALID_DAYS = tuple(
    [f"2026-05-{day:02d}" for day in range(2, 14)]
    + ["2026-05-16", "2026-05-17", "2026-05-18"]
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def find_manifests(input_root: Path) -> list[Path]:
    return sorted(input_root.glob("*/L2_TOP_ALIGNED_MART_MANIFEST.json"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()

    input_root = args.input_root.expanduser()
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    output_db = output_dir / "l2_top_aligned_mart.duckdb"
    if output_db.exists():
        output_db.unlink()

    manifests = find_manifests(input_root)
    if not manifests:
        raise SystemExit(f"no per-day L2_TOP_ALIGNED_MART_MANIFEST.json files under {input_root}")

    summaries: list[dict[str, Any]] = []
    for manifest_path in manifests:
        manifest = read_json(manifest_path)
        if manifest.get("status") != "OK":
            raise SystemExit(f"per-day mart is not OK: {manifest_path}")
        db_path = Path(str(manifest.get("output_duckdb") or "")).expanduser()
        if not db_path.exists():
            raise SystemExit(f"per-day DuckDB missing: {db_path}")
        summaries.append({"manifest_path": manifest_path, "manifest": manifest, "db_path": db_path})

    con = duckdb.connect(str(output_db))
    try:
        con.execute(f"PRAGMA threads={int(args.threads)}")
        first = True
        for idx, item in enumerate(summaries):
            alias = f"d{idx}"
            con.execute(f"ATTACH {quote_literal(item['db_path'])} AS {alias} (READ_ONLY)")
            if first:
                con.execute(
                    f"""
                    CREATE TABLE md_book_l2_top_aligned AS
                    SELECT *
                    FROM {alias}.main.md_book_l2_top_aligned
                    """
                )
                first = False
            else:
                con.execute(
                    f"""
                    INSERT INTO md_book_l2_top_aligned
                    SELECT *
                    FROM {alias}.main.md_book_l2_top_aligned
                    """
                )
            con.execute(f"DETACH {alias}")

        con.execute("CREATE INDEX IF NOT EXISTS idx_l2_aligned_asset_day ON md_book_l2_top_aligned(asset, day)")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_l2_aligned_cond_side_recv "
            "ON md_book_l2_top_aligned(condition_id, market_side, recv_ms)"
        )
        row_count = int(con.execute("SELECT count(*) FROM md_book_l2_top_aligned").fetchone()[0])
        missing_depth_rows = int(
            con.execute(
                "SELECT count(*) FROM md_book_l2_top_aligned WHERE raw_l2_source_row_id IS NULL"
            ).fetchone()[0]
        )
        top_overlay_required_rows = int(
            con.execute(
                "SELECT count(*) FROM md_book_l2_top_aligned WHERE top_overlay_required"
            ).fetchone()[0]
        )
        max_raw_l2_age_ms = int(
            con.execute("SELECT max(raw_l2_age_ms) FROM md_book_l2_top_aligned").fetchone()[0] or 0
        )
        by_asset_day = [
            {
                "asset": row[0],
                "day": row[1],
                "rows": int(row[2]),
                "top_overlay_required_rows": int(row[3] or 0),
                "missing_depth_rows": int(row[4] or 0),
                "max_raw_l2_age_ms": int(row[5] or 0),
            }
            for row in con.execute(
                """
                SELECT asset, day, count(*) AS rows,
                       sum(CASE WHEN top_overlay_required THEN 1 ELSE 0 END) AS overlay_rows,
                       sum(CASE WHEN raw_l2_source_row_id IS NULL THEN 1 ELSE 0 END) AS missing_depth_rows,
                       max(raw_l2_age_ms) AS max_age
                FROM md_book_l2_top_aligned
                GROUP BY 1, 2
                ORDER BY 1, 2
                """
            ).fetchall()
        ]
    finally:
        con.close()

    assets = sorted({row["asset"] for row in by_asset_day})
    days = sorted({row["day"] for row in by_asset_day})
    manifest = {
        "schema_version": "l2_top_aligned_mart_v1",
        "created_utc": utc_now(),
        "status": "OK",
        "combination_mode": "per_day_duckdb_append",
        "input_root": str(input_root),
        "output_dir": str(output_dir),
        "output_duckdb": str(output_db),
        "output_duckdb_sha256": sha256_file(output_db),
        "table": "md_book_l2_top_aligned",
        "assets": assets,
        "days": days,
        "valid_days": list(VALID_DAYS),
        "missing_valid_days": [day for day in VALID_DAYS if day not in set(days)],
        "blocklisted_days_excluded": list(BLOCKLISTED_DAYS),
        "row_count": row_count,
        "missing_depth_rows": missing_depth_rows,
        "top_overlay_required_rows": top_overlay_required_rows,
        "top_overlay_required_rate": round(top_overlay_required_rows / row_count, 6) if row_count else 0.0,
        "max_raw_l2_age_ms": max_raw_l2_age_ms,
        "by_asset_day": by_asset_day,
        "source_manifests": [str(item["manifest_path"]) for item in summaries],
        "semantics": {
            "top_source": "md_book_l1",
            "depth_source": "latest md_book_l2 side snapshot at or before L1 capture_seq",
            "warning": "Depth beyond top remains best-effort for legacy price-change records where raw_l2 did not refresh with the L1 top change.",
        },
    }
    out_manifest = output_dir / "L2_TOP_ALIGNED_MART_MANIFEST.json"
    out_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: manifest[k] for k in ("status", "row_count", "missing_valid_days", "output_duckdb")}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build the multiasset V1 L2 validation plan.

This script does not extract L2 rows. It publishes the control-plane artifact
that tells operators which shortlist candidates need L2, whether local L2 is
already available, and whether PolyData replay archives can satisfy the next
targeted/full L2 build.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_ARCHIVE_ROOT = Path("/Volumes/PolyData/poly_replay_archive/_archives")
VALID_DAYS = tuple(
    [f"2026-05-{day:02d}" for day in range(2, 14)]
    + ["2026-05-16", "2026-05-17", "2026-05-18"]
)
BLOCKLISTED_DAYS = ("2026-05-14", "2026-05-15", "2026-05-19")
DEFAULT_L2_STORE_NAME = "replay_store_multiasset_l2_v1"
DEFAULT_L2_LABEL = "20260502_20260518_l2"
DEFAULT_L2_TABLES = (
    "market_meta",
    "settlement_records",
    "md_trades",
    "md_book_l1",
    "md_book_l2",
    "xuan_trades",
    "xuan_activity",
    "xuan_poll_log",
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
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, str]]:
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


def free_bytes(path: Path) -> int | None:
    target = path
    while not target.exists() and target != target.parent:
        target = target.parent
    if not target.exists():
        return None
    return int(shutil.disk_usage(target).free)


def archive_inventory(archive_root: Path) -> dict[str, Any]:
    days: dict[str, dict[str, Any]] = {}
    for day in VALID_DAYS:
        day_dir = archive_root / day
        archive = day_dir / "crypto_5m.sqlite.zst"
        complete = day_dir / ".complete"
        sha = day_dir / "SHA256SUMS"
        days[day] = {
            "day": day,
            "archive": str(archive),
            "archive_present": archive.exists(),
            "complete_present": complete.exists(),
            "sha256sums_present": sha.exists(),
            "archive_size_bytes": archive.stat().st_size if archive.exists() else None,
        }
    available = [day for day, item in days.items() if item["archive_present"] and item["complete_present"]]
    return {
        "archive_root": str(archive_root),
        "valid_day_count": len(VALID_DAYS),
        "available_day_count": len(available),
        "missing_days": [day for day in VALID_DAYS if day not in set(available)],
        "available_days": available,
        "archive_bytes": sum(int(item["archive_size_bytes"] or 0) for item in days.values()),
        "days": days,
    }


def manifest_l2_capability(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "manifest": str(path),
            "present": False,
            "has_md_book_l2": False,
            "assets": [],
            "days": [],
            "row_count": None,
        }
    manifest = read_json(path)
    tables = set(manifest.get("tables") or [])
    table_totals = manifest.get("table_totals") or {}
    return {
        "manifest": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "has_md_book_l2": "md_book_l2" in tables,
        "assets": sorted(manifest.get("assets") or []),
        "days": sorted(manifest.get("days") or []),
        "row_count": manifest.get("row_count"),
        "md_book_l2_rows": table_totals.get("md_book_l2"),
        "tables": sorted(tables),
    }


def optional_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return read_json(path)


def summarize_l1_from_l2_parity(path: Path) -> dict[str, Any]:
    data = optional_json(path)
    if not data:
        return {
            "report": str(path),
            "present": False,
            "status": "MISSING",
            "pure_l2_ready": False,
            "l1_top_overlay_required": False,
        }
    status = str(data.get("status") or "")
    return {
        "report": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "schema_version": data.get("schema_version"),
        "status": status,
        "pure_l2_ready": status == "OK",
        "l1_top_overlay_required": status == "OK_L1_TOP_OVERLAY_REQUIRED",
        "failed_assets": sorted(data.get("failed_assets") or []),
        "overlay_failed_assets": sorted(data.get("overlay_failed_assets") or []),
        "sample_per_asset": data.get("sample_per_asset"),
        "thresholds": data.get("thresholds") or {},
        "parity_models": data.get("parity_models") or {},
        "by_asset": data.get("by_asset") or [],
    }


def summarize_l2_top_aligned_mart(path: Path) -> dict[str, Any]:
    data = optional_json(path)
    if not data:
        return {
            "manifest": str(path),
            "present": False,
            "status": "MISSING",
            "assets": [],
            "days": [],
            "row_count": None,
            "ready_for_all_valid_days": False,
        }
    by_asset_day = data.get("by_asset_day") or []
    assets = data.get("assets")
    if assets == "all" or not isinstance(assets, list):
        assets = sorted({str(row.get("asset") or "").upper() for row in by_asset_day if row.get("asset")})
    days = sorted({str(row.get("day") or "") for row in by_asset_day if row.get("day")})
    if data.get("days") and data.get("days") != "all":
        days = sorted(data.get("days") or days)
    return {
        "manifest": str(path),
        "present": True,
        "sha256": sha256_file(path),
        "schema_version": data.get("schema_version"),
        "status": data.get("status"),
        "table": data.get("table"),
        "output_duckdb": data.get("output_duckdb"),
        "assets": sorted(set(assets or [])),
        "days": days,
        "row_count": data.get("row_count"),
        "missing_depth_rows": data.get("missing_depth_rows"),
        "top_overlay_required_rows": data.get("top_overlay_required_rows"),
        "top_overlay_required_rate": data.get("top_overlay_required_rate"),
        "max_raw_l2_age_ms": data.get("max_raw_l2_age_ms"),
        "ready_for_all_valid_days": bool(
            data.get("status") == "OK"
            and set(VALID_DAYS).issubset(set(days))
            and set(assets or [])
        ),
        "semantics": data.get("semantics") or {},
    }


def first_non_empty(*values: str | None) -> str:
    for value in values:
        if value is not None and str(value).strip():
            return str(value)
    return ""


def candidate_rows(shortlist: list[dict[str, str]], audit: list[dict[str, str]], top_n: int | None) -> list[dict[str, Any]]:
    audit_by_key = {row.get("candidate_key", ""): row for row in audit}
    rows: list[dict[str, Any]] = []
    source = shortlist if shortlist else audit
    for idx, row in enumerate(source, start=1):
        rank_raw = first_non_empty(row.get("shortlist_rank"), row.get("audit_rank"), str(idx))
        try:
            rank = int(float(rank_raw))
        except ValueError:
            rank = idx
        if top_n is not None and rank > top_n:
            continue
        key = row.get("candidate_key") or ""
        audit_row = audit_by_key.get(key, {})
        params = {
            name: row.get(name)
            for name in (
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
            )
            if row.get(name) not in (None, "")
        }
        rows.append(
            {
                "candidate_key": key,
                "asset": first_non_empty(row.get("asset"), audit_row.get("asset")).upper(),
                "shortlist_rank": rank,
                "audit_rank": first_non_empty(audit_row.get("audit_rank")),
                "audit_status": first_non_empty(audit_row.get("audit_status")),
                "deployable_ready": first_non_empty(audit_row.get("deployable_ready")),
                "best_queue_pnl": first_non_empty(audit_row.get("best_queue_pnl"), row.get("best_pnl")),
                "candidate_params": params,
            }
        )
    rows.sort(key=lambda item: (int(item["shortlist_rank"]), item["candidate_key"]))
    return rows


def validation_jobs_by_candidate(queue: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in queue:
        key = str(row.get("candidate_key") or "")
        if key and key not in out:
            out[key] = row
    return out


def l2_status_for_candidate(
    candidate: dict[str, Any],
    full_l2: dict[str, Any],
    btc_l2: dict[str, Any],
    l1_from_l2_parity: dict[str, Any],
    l2_top_aligned_mart: dict[str, Any],
    archives: dict[str, Any],
) -> tuple[str, str, bool]:
    asset = str(candidate.get("asset") or "").upper()
    parity_status = str(l1_from_l2_parity.get("status") or "")
    pure_l2_ready = bool(l1_from_l2_parity.get("pure_l2_ready"))
    l1_top_overlay_required = bool(l1_from_l2_parity.get("l1_top_overlay_required"))
    top_aligned_assets = set(l2_top_aligned_mart.get("assets") or [])
    top_aligned_days = set(l2_top_aligned_mart.get("days") or [])
    top_aligned_ready = (
        l2_top_aligned_mart.get("present")
        and l2_top_aligned_mart.get("status") == "OK"
        and asset in top_aligned_assets
        and set(VALID_DAYS).issubset(top_aligned_days)
    )
    full_l2_ready = (
        full_l2.get("present")
        and full_l2.get("has_md_book_l2")
        and asset in set(full_l2.get("assets") or [])
        and set(VALID_DAYS).issubset(set(full_l2.get("days") or []))
    )
    btc_l2_ready = (
        asset == "BTC"
        and btc_l2.get("present")
        and btc_l2.get("has_md_book_l2")
        and "BTC" in set(btc_l2.get("assets") or [])
        and set(VALID_DAYS).issubset(set(btc_l2.get("days") or []))
    )
    if l1_top_overlay_required:
        if top_aligned_ready:
            return "READY_LOCAL_MULTIASSET_L2_TOP_ALIGNED", str(l2_top_aligned_mart["manifest"]), False
        if full_l2_ready:
            return "NEEDS_L2_TOP_ALIGNED_MART_BUILD", "", True
        if btc_l2_ready:
            return "NEEDS_BTC_L2_TOP_ALIGNED_MART_BUILD", "", True
    elif pure_l2_ready:
        if full_l2_ready:
            return "READY_LOCAL_MULTIASSET_L2", str(full_l2["manifest"]), False
        if btc_l2_ready:
            return "READY_LOCAL_BTC_REPLAY_STORE_V2_L2", str(btc_l2["manifest"]), False
    elif parity_status not in ("", "MISSING"):
        if full_l2_ready or btc_l2_ready:
            return "BLOCKED_L1_FROM_L2_PARITY_NOT_OK", "", True
    if (
        full_l2_ready
        and parity_status in ("", "MISSING")
    ):
        return "BLOCKED_L1_FROM_L2_PARITY_MISSING", "", True
    if (
        btc_l2_ready
        and parity_status in ("", "MISSING")
    ):
        return "BLOCKED_L1_FROM_L2_PARITY_MISSING", "", True
    if archives.get("missing_days"):
        return "BLOCKED_REPLAY_ARCHIVE_DAYS_MISSING", "", True
    return "NEEDS_POLYDATA_REPLAY_L2_BUILD", "", True


def shell_join(parts: list[str]) -> str:
    def quote(part: str) -> str:
        if not part:
            return "''"
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-=.,/:@%")
        if all(ch in allowed for ch in part):
            return part
        return "'" + part.replace("'", "'\"'\"'") + "'"

    return " ".join(quote(part) for part in parts)


def build_full_l2_command(args: argparse.Namespace) -> str:
    label = args.full_l2_label
    parts = [
        "uv",
        "run",
        "--with",
        "duckdb",
        "python",
        "scripts/build_replay_store_v2.py",
        "--archive-root",
        str(args.archive_root),
        "--store-root",
        str(args.data_root / "verification_store"),
        "--store-name",
        args.full_l2_store_name,
        "--label",
        label,
        "--days",
        "valid",
        "--assets",
        "all",
        "--tables",
        ",".join(DEFAULT_L2_TABLES),
        "--temp-root",
        str(args.data_root / "tmp"),
        "--parallel-days",
        str(args.parallel_days),
        "--duckdb-threads",
        str(args.duckdb_threads),
        "--min-store-free-gb",
        str(args.min_store_free_gb),
        "--min-temp-free-gb",
        str(args.min_temp_free_gb),
    ]
    return shell_join(parts)


def build_top_aligned_mart_command(args: argparse.Namespace, full_l2_manifest: Path) -> str:
    parts = [
        "uv",
        "run",
        "--with",
        "duckdb",
        "python",
        "scripts/build_l2_top_aligned_mart.py",
        "--l2-manifest",
        str(full_l2_manifest),
        "--output-dir",
        str(args.data_root / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2"),
        "--days",
        "all",
        "--assets",
        "all",
        "--duckdb-threads",
        str(args.duckdb_threads),
    ]
    if args.top_aligned_duckdb_temp_dir:
        parts.extend(["--duckdb-temp-dir", str(args.top_aligned_duckdb_temp_dir)])
    return shell_join(parts)


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "shortlist_rank",
        "candidate_key",
        "asset",
        "l2_status",
        "requires_polydata_replay_archive",
        "local_l2_manifest",
        "validation_job_id",
        "audit_status",
        "deployable_ready",
        "best_queue_pnl",
        "candidate_params_json",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    data_root = args.data_root
    output_dir = args.output_dir
    shortlist_path = args.shortlist_csv
    audit_path = args.audit_csv
    queue_path = args.validation_queue_jsonl

    shortlist = read_csv_rows(shortlist_path)
    audit = read_csv_rows(audit_path)
    queue = read_jsonl(queue_path)
    jobs_by_candidate = validation_jobs_by_candidate(queue)
    candidates = candidate_rows(shortlist, audit, args.top_n)

    archives = archive_inventory(args.archive_root)
    full_l2_manifest = (
        data_root
        / "verification_store"
        / args.full_l2_store_name
        / args.full_l2_label
        / "REPLAY_STORE_V2_MANIFEST.json"
    )
    full_l2 = manifest_l2_capability(full_l2_manifest)
    btc_l2 = manifest_l2_capability(args.btc_replay_store_manifest)
    l1_from_l2_parity = summarize_l1_from_l2_parity(args.l1_from_l2_parity_report)
    l2_top_aligned_mart = summarize_l2_top_aligned_mart(args.l2_top_aligned_mart_manifest)

    plan_rows: list[dict[str, Any]] = []
    queue_rows: list[dict[str, Any]] = []
    for candidate in candidates:
        status, local_manifest, requires_polydata = l2_status_for_candidate(
            candidate, full_l2, btc_l2, l1_from_l2_parity, l2_top_aligned_mart, archives
        )
        job = jobs_by_candidate.get(candidate["candidate_key"], {})
        plan_row = {
            **candidate,
            "l2_status": status,
            "requires_polydata_replay_archive": requires_polydata,
            "local_l2_manifest": local_manifest,
            "validation_job_id": job.get("job_id", ""),
            "candidate_params_json": json.dumps(candidate.get("candidate_params") or {}, sort_keys=True),
        }
        plan_rows.append(plan_row)
        queue_rows.append(
            {
                "schema_version": "backtest_l2_validation_job_v1",
                "candidate_key": candidate["candidate_key"],
                "asset": candidate["asset"],
                "shortlist_rank": candidate["shortlist_rank"],
                "status": status,
                "requires_polydata_replay_archive": requires_polydata,
                "requires_raw": False,
                "requires_remote": False,
                "valid_days": list(VALID_DAYS),
                "blocklisted_days_excluded": list(BLOCKLISTED_DAYS),
                "source_archive_root": str(args.archive_root) if requires_polydata else None,
                "local_l2_manifest": local_manifest or None,
                "l1_from_l2_parity_status": l1_from_l2_parity.get("status"),
                "l2_top_semantics": "l1_top_overlay"
                if l1_from_l2_parity.get("l1_top_overlay_required")
                else "pure_l2",
                "candidate_params": candidate.get("candidate_params") or {},
                "upstream_validation_job_id": job.get("job_id"),
                "allowed_layers": [
                    "replay_source_truth",
                    "search_safe_lineage",
                    "l2_targeted_replay_source_truth",
                    "l1_top_aligned_l2_depth_provenance",
                ],
                "operator_guardrails": {
                    "extract_replay_sqlite_one_day_at_a_time": True,
                    "build_top_aligned_l2_mart_when_parity_requires_overlay": True,
                    "delete_temp_sqlite_after_parquet_export": True,
                    "do_not_scan_raw_by_default": True,
                    "do_not_use_remote": True,
                    "do_not_include_blocklisted_days": True,
                },
            }
        )

    statuses = {row["l2_status"] for row in plan_rows}
    if not candidates:
        status = "NO_CANDIDATES"
    elif statuses <= {"READY_LOCAL_MULTIASSET_L2", "READY_LOCAL_BTC_REPLAY_STORE_V2_L2"}:
        status = "OK_LOCAL_L2_READY"
    elif statuses <= {"READY_LOCAL_MULTIASSET_L2_TOP_ALIGNED"}:
        status = "OK_LOCAL_L2_TOP_ALIGNED_READY"
    elif "BLOCKED_L1_FROM_L2_PARITY_NOT_OK" in statuses:
        status = "BLOCKED_L1_FROM_L2_PARITY_NOT_OK"
    elif "BLOCKED_L1_FROM_L2_PARITY_MISSING" in statuses:
        status = "BLOCKED_L1_FROM_L2_PARITY_MISSING"
    elif "NEEDS_L2_TOP_ALIGNED_MART_BUILD" in statuses or "NEEDS_BTC_L2_TOP_ALIGNED_MART_BUILD" in statuses:
        status = "NEEDS_L2_TOP_ALIGNED_MART_BUILD"
    elif "BLOCKED_REPLAY_ARCHIVE_DAYS_MISSING" in statuses:
        status = "BLOCKED_REPLAY_ARCHIVE_DAYS_MISSING"
    elif archives.get("available_day_count") == len(VALID_DAYS):
        status = "NEEDS_L2_BUILD_POLYDATA_ARCHIVES_AVAILABLE"
    else:
        status = "NEEDS_L2_BUILD_POLYDATA_ARCHIVES_INCOMPLETE"

    output_dir.mkdir(parents=True, exist_ok=True)
    plan_csv = output_dir / "backtest_l2_validation_plan.csv"
    queue_jsonl = output_dir / "backtest_l2_validation_queue.jsonl"
    write_csv(plan_csv, plan_rows)
    with queue_jsonl.open("w", encoding="utf-8") as f:
        for row in queue_rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    command = build_full_l2_command(args)
    top_aligned_command = build_top_aligned_mart_command(args, full_l2_manifest)
    manifest = {
        "schema_version": "backtest_l2_validation_plan_v1",
        "created_utc": utc_now(),
        "status": status,
        "data_root": str(data_root),
        "output_dir": str(output_dir),
        "valid_days": list(VALID_DAYS),
        "blocklisted_days_excluded": list(BLOCKLISTED_DAYS),
        "candidate_count": len(candidates),
        "candidate_assets": sorted({row["asset"] for row in plan_rows if row.get("asset")}),
        "l2_status_counts": {
            item: sum(1 for row in plan_rows if row["l2_status"] == item) for item in sorted(statuses)
        },
        "shortlist_csv": str(shortlist_path),
        "audit_csv": str(audit_path),
        "validation_queue_jsonl": str(queue_path),
        "outputs": {
            "plan_csv": str(plan_csv),
            "queue_jsonl": str(queue_jsonl),
        },
        "local_l2_full_store": full_l2,
        "local_btc_replay_store_v2": btc_l2,
        "l1_from_l2_parity": l1_from_l2_parity,
        "l2_top_aligned_mart": l2_top_aligned_mart,
        "archive_inventory": archives,
        "disk_free_bytes": {
            "data_root": free_bytes(data_root),
            "archive_root": free_bytes(args.archive_root),
        },
        "recommended_full_l2_build": {
            "purpose": "Build a MacBook-local multiasset L2 replay store so deep validation does not need PolyData for normal use after the build.",
            "command": command,
            "expected_output_manifest": str(full_l2_manifest),
            "notes": [
                "This is an offline local build from PolyData replay archives; it does not use remote hosts or raw archives.",
                "The builder extracts one day of replay SQLite at a time and removes temp SQLite unless --keep-temp is added.",
                "The L2 build table set intentionally excludes empty owner-private tables; future owner truth remains a separate pipeline.",
                "If L1-from-L2 parity reports OK_L1_TOP_OVERLAY_REQUIRED, do not validate candidates directly against pure md_book_l2 top-of-book.",
                "For legacy replay archives with side-update/top-refresh mismatch, build and use the L1-top-aligned L2 mart before candidate L2 validation.",
                "Use this only when local free space comfortably exceeds the guardrails.",
            ],
        },
        "recommended_top_aligned_mart_build": {
            "purpose": "Materialize the corrected legacy L2 validation surface: md_book_l1 canonical top plus latest md_book_l2 depth/provenance.",
            "command": top_aligned_command,
            "expected_output_manifest": str(
                data_root
                / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json"
            ),
            "required_when": "l1_from_l2_parity.status == OK_L1_TOP_OVERLAY_REQUIRED",
            "notes": [
                "This mart is the accuracy-safe L2 tier for current legacy replay archives.",
                "Pure md_book_l2 top reconstruction is not a valid accuracy proof until parity status is OK.",
                "Candidate-level L2 validation should join this mart, not raw md_book_l2 side snapshots, when overlay is required.",
            ],
        },
        "plan_rows": plan_rows,
    }
    manifest_path = output_dir / "BACKTEST_L2_VALIDATION_PLAN_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    parser.add_argument("--archive-root", type=Path, default=DEFAULT_ARCHIVE_ROOT)
    parser.add_argument("--shortlist-csv", type=Path)
    parser.add_argument("--audit-csv", type=Path)
    parser.add_argument("--validation-queue-jsonl", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--top-n", type=int, default=None)
    parser.add_argument("--full-l2-store-name", default=DEFAULT_L2_STORE_NAME)
    parser.add_argument("--full-l2-label", default=DEFAULT_L2_LABEL)
    parser.add_argument(
        "--btc-replay-store-manifest",
        type=Path,
        default=DEFAULT_DATA_ROOT
        / "verification_store/replay_store_v2/20260502_20260518/REPLAY_STORE_V2_MANIFEST.json",
    )
    parser.add_argument(
        "--l1-from-l2-parity-report",
        type=Path,
        default=DEFAULT_DATA_ROOT / "derived/contract_examples/l1_from_l2_parity_latest/L1_FROM_L2_PARITY_REPORT.json",
    )
    parser.add_argument(
        "--l2-top-aligned-mart-manifest",
        type=Path,
        default=DEFAULT_DATA_ROOT
        / "derived/contract_examples/l2_top_aligned_mart_20260502_20260518_l2/L2_TOP_ALIGNED_MART_MANIFEST.json",
    )
    parser.add_argument("--parallel-days", type=int, default=1)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument(
        "--top-aligned-duckdb-temp-dir",
        type=Path,
        default=DEFAULT_DATA_ROOT / "tmp/l2_top_aligned_20260502_20260518",
    )
    parser.add_argument("--min-store-free-gb", type=float, default=160.0)
    parser.add_argument("--min-temp-free-gb", type=float, default=160.0)
    args = parser.parse_args()

    args.data_root = args.data_root.expanduser()
    args.archive_root = args.archive_root.expanduser()
    if args.shortlist_csv is None:
        args.shortlist_csv = (
            args.data_root
            / "derived/contract_examples/backtest_candidate_shortlist_deep_v1/backtest_candidate_shortlist.csv"
        )
    if args.audit_csv is None:
        args.audit_csv = (
            args.data_root
            / "derived/contract_examples/backtest_candidate_audit_pack_latest/backtest_candidate_audit_pack.csv"
        )
    if args.validation_queue_jsonl is None:
        args.validation_queue_jsonl = (
            args.data_root
            / "derived/contract_examples/backtest_validation_queue_deep_v1/backtest_validation_queue.jsonl"
        )
    if args.output_dir is None:
        args.output_dir = (
            args.data_root
            / "derived/contract_examples/backtest_l2_validation_plan_latest"
        )
    args.shortlist_csv = args.shortlist_csv.expanduser()
    args.audit_csv = args.audit_csv.expanduser()
    args.validation_queue_jsonl = args.validation_queue_jsonl.expanduser()
    args.output_dir = args.output_dir.expanduser()
    args.btc_replay_store_manifest = args.btc_replay_store_manifest.expanduser()
    args.l1_from_l2_parity_report = args.l1_from_l2_parity_report.expanduser()
    args.l2_top_aligned_mart_manifest = args.l2_top_aligned_mart_manifest.expanduser()
    if args.top_aligned_duckdb_temp_dir:
        args.top_aligned_duckdb_temp_dir = args.top_aligned_duckdb_temp_dir.expanduser()

    manifest = build_report(args)
    print(
        json.dumps(
            {
                k: manifest[k]
                for k in (
                    "status",
                    "candidate_count",
                    "candidate_assets",
                    "l2_status_counts",
                    "l1_from_l2_parity",
                    "l2_top_aligned_mart",
                    "outputs",
                )
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not str(manifest["status"]).startswith("BLOCKED") else 1


if __name__ == "__main__":
    raise SystemExit(main())

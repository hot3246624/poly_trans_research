#!/usr/bin/env python3
"""Produce BTC core current/future projected market targets from reviewed metadata.

This materializer is read-only and deterministic. It does not fetch network
data by itself; a future resolver packet must provide a reviewed metadata JSON
with path/hash/provenance before execution.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
from typing import Any


STRATEGY_ID = "BTC_CORE_COMPLETION_V1"
OWNER_LINE = "xuan_research_local"
STATUS_OK = "KEEP_BTC_CORE_TARGET_PROJECTION_MATERIALIZED_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_TARGET_PROJECTION_MATERIALIZATION_FAIL_CLOSED_NOT_OOS_READY"
TARGET_FIELDS = [
    "strategy_owner_line",
    "strategy_id",
    "projection_id",
    "projection_created_ts_ms",
    "projection_created_ts_utc",
    "projection_round_index",
    "start_round_offset",
    "slug",
    "asset",
    "timeframe",
    "market_id",
    "condition_id",
    "token_id_yes",
    "token_id_no",
    "subscribed_asset_ids",
    "window_start_ts_ms",
    "window_end_ts_ms",
    "binding_status",
    "resolver_source",
    "resolver_audit_row_hash",
]
REJECT_FIELDS = [
    "strategy_owner_line",
    "strategy_id",
    "projection_id",
    "projection_created_ts_ms",
    "projection_round_index",
    "start_round_offset",
    "slug",
    "window_start_ts_ms",
    "window_end_ts_ms",
    "reject_category",
    "reject_reason",
    "resolver_source",
    "resolver_audit_row_hash",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def stable_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return value
    return value


def as_list(value: Any) -> list[Any]:
    parsed = parse_jsonish(value)
    if parsed is None:
        return []
    if isinstance(parsed, list):
        return parsed
    return [parsed]


def normalize_market(raw: dict[str, Any]) -> dict[str, Any]:
    slug = str(raw.get("slug") or raw.get("market_slug") or raw.get("eventSlug") or "")
    condition_id = str(raw.get("condition_id") or raw.get("conditionId") or raw.get("market_id") or raw.get("id") or "")
    market_id = str(raw.get("market_id") or raw.get("condition_id") or raw.get("conditionId") or raw.get("id") or "")
    token_id_yes = str(raw.get("token_id_yes") or raw.get("yes_token_id") or "")
    token_id_no = str(raw.get("token_id_no") or raw.get("no_token_id") or "")
    if not token_id_yes or not token_id_no:
        outcomes = [str(x).strip().upper() for x in as_list(raw.get("outcomes"))]
        tokens = [str(x).strip() for x in as_list(raw.get("clobTokenIds") or raw.get("tokenIds") or raw.get("tokens"))]
        for outcome, token in zip(outcomes, tokens):
            if outcome == "YES" and not token_id_yes:
                token_id_yes = token
            if outcome == "NO" and not token_id_no:
                token_id_no = token
    subscribed = raw.get("subscribed_asset_ids")
    if subscribed is None:
        subscribed = raw.get("asset_ids")
    subscribed_ids = [str(x).strip() for x in as_list(subscribed) if str(x).strip()]
    if not subscribed_ids and token_id_yes and token_id_no:
        subscribed_ids = [token_id_yes, token_id_no]
    return {
        "slug": slug,
        "condition_id": condition_id,
        "market_id": market_id,
        "token_id_yes": token_id_yes,
        "token_id_no": token_id_no,
        "subscribed_asset_ids": subscribed_ids,
        "raw_hash": stable_hash(raw),
    }


def load_metadata(path: Path) -> dict[str, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("markets") or payload.get("rows") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    by_slug: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        normalized = normalize_market(raw)
        if normalized["slug"]:
            by_slug.setdefault(normalized["slug"], []).append(normalized)
    return by_slug


def iso_from_ms(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def projection_id(created_ms: int, start_offset: int, count: int) -> str:
    return hashlib.sha256(f"{STRATEGY_ID}:{created_ms}:{start_offset}:{count}".encode("utf-8")).hexdigest()[:24]


def target_start_ms(created_ms: int, offset: int) -> int:
    base_sec = math.floor(created_ms / 1000 / 300) * 300
    return int((base_sec + 300 * offset) * 1000)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resolver-metadata-json", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--projection-created-ts-ms", type=int, required=True)
    parser.add_argument("--projection-created-ts-utc", required=True)
    parser.add_argument("--start-round-offset", type=int, default=73)
    parser.add_argument("--target-round-count", type=int, default=288)
    parser.add_argument("--min-target-start-delay-ms", type=int, default=21_600_000)
    parser.add_argument("--resolver-source", default="reviewed_public_metadata_cache")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise SystemExit(f"BLOCKED_OUTPUT_DIR_EXISTS:{output_dir}")
    output_dir.mkdir(parents=True)
    metadata_by_slug = load_metadata(args.resolver_metadata_json)
    proj_id = projection_id(args.projection_created_ts_ms, args.start_round_offset, args.target_round_count)
    target_rows: list[dict[str, Any]] = []
    reject_rows: list[dict[str, Any]] = []
    resolver_audit_rows: list[dict[str, Any]] = []

    for idx in range(args.target_round_count):
        offset = args.start_round_offset + idx
        start_ms = target_start_ms(args.projection_created_ts_ms, offset)
        end_ms = start_ms + 300_000
        slug = f"btc-updown-5m-{start_ms // 1000}"
        matches = metadata_by_slug.get(slug, [])
        audit_base = {
            "strategy_owner_line": OWNER_LINE,
            "strategy_id": STRATEGY_ID,
            "projection_id": proj_id,
            "projection_created_ts_ms": args.projection_created_ts_ms,
            "projection_round_index": idx,
            "start_round_offset": offset,
            "slug": slug,
            "window_start_ts_ms": start_ms,
            "window_end_ts_ms": end_ms,
            "resolver_source": args.resolver_source,
            "match_count": len(matches),
        }
        if start_ms < args.projection_created_ts_ms + args.min_target_start_delay_ms:
            reason = "projected target violates min_target_start_delay_ms"
            category = "STALE_OR_INSUFFICIENT_DELAY"
            row_hash = stable_hash({**audit_base, "category": category, "reason": reason})
            resolver_audit_rows.append({**audit_base, "binding_status": "REJECTED_FAIL_CLOSED", "reason": reason})
            reject_rows.append(
                {
                    "strategy_owner_line": OWNER_LINE,
                    "strategy_id": STRATEGY_ID,
                    "projection_id": proj_id,
                    "projection_created_ts_ms": args.projection_created_ts_ms,
                    "projection_round_index": idx,
                    "start_round_offset": offset,
                    "slug": slug,
                    "window_start_ts_ms": start_ms,
                    "window_end_ts_ms": end_ms,
                    "reject_category": category,
                    "reject_reason": reason,
                    "resolver_source": args.resolver_source,
                    "resolver_audit_row_hash": row_hash,
                }
            )
            continue
        if len(matches) != 1:
            reason = f"resolver match count is {len(matches)}"
            category = "MISSING_RESOLVER_OUTPUT" if not matches else "AMBIGUOUS_RESOLVER_OUTPUT"
            row_hash = stable_hash({**audit_base, "category": category, "reason": reason})
            resolver_audit_rows.append({**audit_base, "binding_status": "REJECTED_FAIL_CLOSED", "reason": reason})
            reject_rows.append(
                {
                    "strategy_owner_line": OWNER_LINE,
                    "strategy_id": STRATEGY_ID,
                    "projection_id": proj_id,
                    "projection_created_ts_ms": args.projection_created_ts_ms,
                    "projection_round_index": idx,
                    "start_round_offset": offset,
                    "slug": slug,
                    "window_start_ts_ms": start_ms,
                    "window_end_ts_ms": end_ms,
                    "reject_category": category,
                    "reject_reason": reason,
                    "resolver_source": args.resolver_source,
                    "resolver_audit_row_hash": row_hash,
                }
            )
            continue
        match = matches[0]
        subscribed = list(dict.fromkeys(match["subscribed_asset_ids"]))
        errors: list[str] = []
        if not match["market_id"] or not match["condition_id"]:
            errors.append("market_id_or_condition_id_missing")
        if not match["token_id_yes"] or not match["token_id_no"]:
            errors.append("token_side_missing")
        if match["token_id_yes"] and match["token_id_yes"] == match["token_id_no"]:
            errors.append("token_side_duplicate")
        if set(subscribed) != {match["token_id_yes"], match["token_id_no"]}:
            errors.append("subscribed_asset_ids_mismatch")
        if errors:
            category = "TOKEN_SIDE_MISSING" if "token_side_missing" in errors else "SUBSCRIBED_ASSET_MISMATCH"
            reason = ",".join(errors)
            row_hash = stable_hash({**audit_base, "category": category, "reason": reason, "match": match})
            resolver_audit_rows.append({**audit_base, "binding_status": "REJECTED_FAIL_CLOSED", "reason": reason})
            reject_rows.append(
                {
                    "strategy_owner_line": OWNER_LINE,
                    "strategy_id": STRATEGY_ID,
                    "projection_id": proj_id,
                    "projection_created_ts_ms": args.projection_created_ts_ms,
                    "projection_round_index": idx,
                    "start_round_offset": offset,
                    "slug": slug,
                    "window_start_ts_ms": start_ms,
                    "window_end_ts_ms": end_ms,
                    "reject_category": category,
                    "reject_reason": reason,
                    "resolver_source": args.resolver_source,
                    "resolver_audit_row_hash": row_hash,
                }
            )
            continue
        row_hash = stable_hash({**audit_base, "match": match})
        resolver_audit_rows.append({**audit_base, "binding_status": "BOUND", "reason": "resolved_exactly_one"})
        target_rows.append(
            {
                "strategy_owner_line": OWNER_LINE,
                "strategy_id": STRATEGY_ID,
                "projection_id": proj_id,
                "projection_created_ts_ms": args.projection_created_ts_ms,
                "projection_created_ts_utc": args.projection_created_ts_utc,
                "projection_round_index": idx,
                "start_round_offset": offset,
                "slug": slug,
                "asset": "BTC",
                "timeframe": "5m",
                "market_id": match["market_id"],
                "condition_id": match["condition_id"],
                "token_id_yes": match["token_id_yes"],
                "token_id_no": match["token_id_no"],
                "subscribed_asset_ids": json.dumps(subscribed, separators=(",", ":")),
                "window_start_ts_ms": start_ms,
                "window_end_ts_ms": end_ms,
                "binding_status": "BOUND",
                "resolver_source": args.resolver_source,
                "resolver_audit_row_hash": row_hash,
            }
        )

    slugs = [row["slug"] for row in target_rows]
    market_ids = [row["market_id"] for row in target_rows]
    token_pairs = [(row["token_id_yes"], row["token_id_no"]) for row in target_rows]
    windows = [(row["window_start_ts_ms"], row["window_end_ts_ms"]) for row in target_rows]
    audit = {
        "schema_version": 1,
        "status": STATUS_OK if target_rows and len(target_rows) + len(reject_rows) == args.target_round_count else STATUS_BLOCKED,
        "projection_id": proj_id,
        "projection_created_ts_ms": args.projection_created_ts_ms,
        "projection_created_ts_utc": args.projection_created_ts_utc,
        "target_round_count": args.target_round_count,
        "bound_count": len(target_rows),
        "reject_count": len(reject_rows),
        "bound_plus_reject_count": len(target_rows) + len(reject_rows),
        "duplicate_slug_count": len(slugs) - len(set(slugs)),
        "duplicate_market_id_count": len(market_ids) - len(set(market_ids)),
        "duplicate_token_id_pair_count": len(token_pairs) - len(set(token_pairs)),
        "window_collision_count": len(windows) - len(set(windows)),
        "stale_target_count": sum(
            1 for row in target_rows if int(row["window_start_ts_ms"]) < args.projection_created_ts_ms + args.min_target_start_delay_ms
        ),
        "resolver_metadata_json": str(args.resolver_metadata_json),
        "resolver_metadata_sha256": sha256_file(args.resolver_metadata_json),
        "non_claims": {
            "oos_ready": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    targets_path = output_dir / "BTC_CORE_PROJECTED_MARKET_TARGETS.csv"
    reject_path = output_dir / "BTC_CORE_PROJECTION_REJECT_MANIFEST.jsonl"
    resolver_audit_path = output_dir / "BTC_CORE_PROJECTION_RESOLVER_AUDIT.jsonl"
    audit_path = output_dir / "BTC_CORE_TARGET_PROJECTION_COVERAGE_COLLISION_AUDIT.json"
    validation_path = output_dir / "BTC_CORE_TARGET_PROJECTION_VALIDATION_RESULT.json"
    hash_manifest_path = output_dir / "BTC_CORE_TARGET_PROJECTION_HASH_MANIFEST.json"
    write_csv(targets_path, target_rows, TARGET_FIELDS)
    reject_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in reject_rows), encoding="utf-8")
    resolver_audit_path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in resolver_audit_rows),
        encoding="utf-8",
    )
    write_json(audit_path, audit)
    validation = {
        "schema_version": 1,
        "ok": audit["status"] == STATUS_OK
        and audit["bound_plus_reject_count"] == args.target_round_count
        and audit["duplicate_slug_count"] == 0
        and audit["duplicate_market_id_count"] == 0
        and audit["duplicate_token_id_pair_count"] == 0
        and audit["window_collision_count"] == 0
        and audit["stale_target_count"] == 0,
        "status": audit["status"],
        "audit": audit,
    }
    write_json(validation_path, validation)
    files = [targets_path, reject_path, resolver_audit_path, audit_path, validation_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": audit["status"],
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    print(f"status={audit['status']}")
    print(f"output_dir={output_dir}")
    print(f"bound_count={len(target_rows)}")
    print(f"reject_count={len(reject_rows)}")
    print(f"validation_ok={validation['ok']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Validate BTC core current/future projected market target artifacts."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


STATUS_OK = "KEEP_BTC_CORE_TARGET_PROJECTION_VALIDATED_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_TARGET_PROJECTION_VALIDATION_FAIL_CLOSED_NOT_OOS_READY"
REQUIRED_TARGET_FIELDS = {
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
}


def read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    with path.open("r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader), list(reader.fieldnames or [])


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.stat().st_size == 0:
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def parse_subscribed(value: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(x) for x in parsed]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--targets-csv", type=Path, required=True)
    parser.add_argument("--reject-manifest", type=Path, required=True)
    parser.add_argument("--coverage-audit-json", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--expected-target-round-count", type=int, default=288)
    parser.add_argument("--min-target-start-delay-ms", type=int, default=21_600_000)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows, fields = read_csv(args.targets_csv)
    rejects = read_jsonl(args.reject_manifest)
    audit = json.loads(args.coverage_audit_json.read_text(encoding="utf-8"))
    errors: list[str] = []
    missing_fields = sorted(REQUIRED_TARGET_FIELDS.difference(fields))
    if missing_fields:
        errors.append(f"missing_target_fields:{','.join(missing_fields)}")
    if len(rows) + len(rejects) != args.expected_target_round_count:
        errors.append("bound_plus_reject_count_mismatch")
    projection_created_values = {row.get("projection_created_ts_ms", "") for row in rows}
    if len(projection_created_values) > 1:
        errors.append("multiple_projection_created_ts_ms")
    projection_created_ts_ms = parse_int(next(iter(projection_created_values), "")) if projection_created_values else None
    slugs: list[str] = []
    market_ids: list[str] = []
    token_pairs: list[tuple[str, str]] = []
    windows: list[tuple[int, int]] = []
    stale_count = 0
    for idx, row in enumerate(rows):
        if row.get("strategy_owner_line") != "xuan_research_local":
            errors.append(f"row_{idx}_owner_line_mismatch")
        if row.get("strategy_id") != "BTC_CORE_COMPLETION_V1":
            errors.append(f"row_{idx}_strategy_id_mismatch")
        if row.get("asset") != "BTC" or row.get("timeframe") != "5m":
            errors.append(f"row_{idx}_asset_timeframe_mismatch")
        if row.get("binding_status") != "BOUND":
            errors.append(f"row_{idx}_binding_status_not_bound")
        start_ms = parse_int(row.get("window_start_ts_ms"))
        end_ms = parse_int(row.get("window_end_ts_ms"))
        if start_ms is None or end_ms is None or end_ms - start_ms != 300_000:
            errors.append(f"row_{idx}_window_invalid")
        else:
            expected_slug = f"btc-updown-5m-{start_ms // 1000}"
            if row.get("slug") != expected_slug:
                errors.append(f"row_{idx}_slug_window_mismatch")
            windows.append((start_ms, end_ms))
            if projection_created_ts_ms is not None and start_ms < projection_created_ts_ms + args.min_target_start_delay_ms:
                stale_count += 1
        yes = row.get("token_id_yes", "")
        no = row.get("token_id_no", "")
        subscribed = parse_subscribed(row.get("subscribed_asset_ids", ""))
        if not yes or not no or yes == no:
            errors.append(f"row_{idx}_token_side_invalid")
        if set(subscribed) != {yes, no}:
            errors.append(f"row_{idx}_subscribed_asset_ids_mismatch")
        slugs.append(row.get("slug", ""))
        market_ids.append(row.get("market_id", ""))
        token_pairs.append((yes, no))
    duplicate_slug_count = len(slugs) - len(set(slugs))
    duplicate_market_id_count = len(market_ids) - len(set(market_ids))
    duplicate_token_pair_count = len(token_pairs) - len(set(token_pairs))
    window_collision_count = len(windows) - len(set(windows))
    if duplicate_slug_count:
        errors.append(f"duplicate_slug_count:{duplicate_slug_count}")
    if duplicate_market_id_count:
        errors.append(f"duplicate_market_id_count:{duplicate_market_id_count}")
    if duplicate_token_pair_count:
        errors.append(f"duplicate_token_id_pair_count:{duplicate_token_pair_count}")
    if window_collision_count:
        errors.append(f"window_collision_count:{window_collision_count}")
    if stale_count:
        errors.append(f"stale_target_count:{stale_count}")
    audit_checks = {
        "audit_bound_count_matches": int(audit.get("bound_count", -1)) == len(rows),
        "audit_reject_count_matches": int(audit.get("reject_count", -1)) == len(rejects),
        "audit_target_round_count_matches": int(audit.get("target_round_count", -1)) == args.expected_target_round_count,
        "audit_stale_target_count_zero": int(audit.get("stale_target_count", -1)) == 0,
    }
    for key, ok in audit_checks.items():
        if not ok:
            errors.append(key)
    result = {
        "schema_version": 1,
        "ok": not errors,
        "status": STATUS_OK if not errors else STATUS_BLOCKED,
        "errors": errors,
        "summary": {
            "bound_count": len(rows),
            "reject_count": len(rejects),
            "expected_target_round_count": args.expected_target_round_count,
            "duplicate_slug_count": duplicate_slug_count,
            "duplicate_market_id_count": duplicate_market_id_count,
            "duplicate_token_id_pair_count": duplicate_token_pair_count,
            "window_collision_count": window_collision_count,
            "stale_target_count": stale_count,
            "audit_checks": audit_checks,
        },
        "non_claims": {
            "oos_ready": False,
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    write_json(args.output_json, result)
    print(f"status={result['status']}")
    print(f"ok={result['ok']}")
    print(f"errors={len(errors)}")
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

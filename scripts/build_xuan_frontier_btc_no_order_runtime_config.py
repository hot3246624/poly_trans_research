#!/usr/bin/env python3
"""Build a BTC-only no-order runtime config for the xuan-frontier backend.

This only prepares a local config. It may read public Gamma market metadata to
resolve the next BTC 5m markets, but it never starts a runner and never touches
order/cancel/redeem/import paths.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path("/Users/hot/web3Scientist/poly_backtest_data")
DEFAULT_CONTRACT = DEFAULT_DATA_ROOT / "derived/contract_examples"
DEFAULT_IMPORT_CONTRACT = (
    DEFAULT_CONTRACT
    / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/research_only_import_contract.csv"
)
DEFAULT_SOURCE_SEMANTICS = (
    DEFAULT_CONTRACT / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/source_semantics_contract.json"
)
DEFAULT_OWNER_TRUTH_SCHEMA = (
    DEFAULT_CONTRACT / "btc_same_window_residual_share_le_3pct_v1_canary_preflight_latest/owner_private_truth_schema.json"
)
DEFAULT_OUTPUT_DIR = DEFAULT_CONTRACT / "xuan_frontier_btc_no_order_runtime_config_latest"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_import_contract(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def parse_jsonish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return json.loads(value)
    return []


def fetch_gamma_market(slug: str) -> dict[str, Any]:
    url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({"slug": slug})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read())
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"gamma_event_missing:{slug}")
    event = data[0]
    markets = event.get("markets") or []
    if not markets:
        raise RuntimeError(f"gamma_market_missing:{slug}")
    market = markets[0]
    outcomes = parse_jsonish(market.get("outcomes"))
    token_ids = parse_jsonish(market.get("clobTokenIds"))
    if len(outcomes) < 2 or len(token_ids) < 2:
        raise RuntimeError(f"gamma_market_tokens_missing:{slug}")
    up_idx = outcomes.index("Up") if "Up" in outcomes else 0
    down_idx = outcomes.index("Down") if "Down" in outcomes else 1
    return {
        "slug": slug,
        "market_id": str(market.get("id") or ""),
        "condition_id": str(market.get("conditionId") or ""),
        "yes_asset_id": str(token_ids[up_idx]),
        "no_asset_id": str(token_ids[down_idx]),
        "outcome_yes": "Up",
        "outcome_no": "Down",
        "gamma_event_title": event.get("title") or "",
    }


def next_btc_slugs(round_offsets: list[int]) -> list[str]:
    now = int(time.time())
    base = now - (now % 300)
    return [f"btc-updown-5m-{base + offset * 300}" for offset in round_offsets]


def validate_import_rows(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    if len(rows) != 52:
        errors.append(f"candidate_count_expected_52_got_{len(rows)}")
    for key in ["asset", "filter_name", "filter_version", "runner_profile_id"]:
        values = {row.get(key, "") for row in rows}
        if len(values) != 1:
            errors.append(f"{key}_not_singleton")
    if rows and rows[0].get("asset") != "BTC":
        errors.append("asset_scope_not_btc")
    if rows and rows[0].get("filter_name") != "btc_same_window_residual_share_le_3pct_v1":
        errors.append("filter_name_mismatch")
    for flag in ["import_enabled", "candidate_import_allowed", "live_orders_allowed", "deployable"]:
        bad = [row for row in rows if str(row.get(flag, "")).lower() != "false"]
        if bad:
            errors.append(f"{flag}_must_be_false")
    bad_dry = [row for row in rows if str(row.get("dry_run_only", "")).lower() != "true"]
    if bad_dry:
        errors.append("dry_run_only_must_be_true")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--import-contract", type=Path, default=DEFAULT_IMPORT_CONTRACT)
    parser.add_argument("--source-semantics", type=Path, default=DEFAULT_SOURCE_SEMANTICS)
    parser.add_argument("--owner-truth-schema", type=Path, default=DEFAULT_OWNER_TRUTH_SCHEMA)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--round-offsets", default="1,2,3")
    args = parser.parse_args()

    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    import_contract = args.import_contract.expanduser()
    source_semantics_path = args.source_semantics.expanduser()
    owner_truth_schema = args.owner_truth_schema.expanduser()
    rows = read_import_contract(import_contract)
    source_semantics = read_json(source_semantics_path)
    row_errors = validate_import_rows(rows)
    round_offsets = [int(part.strip()) for part in args.round_offsets.split(",") if part.strip()]
    slugs = next_btc_slugs(round_offsets)
    resolved_markets: list[dict[str, Any]] = []
    resolve_errors: list[str] = []
    for slug in slugs:
        try:
            resolved_markets.append(fetch_gamma_market(slug))
        except Exception as exc:
            resolve_errors.append(f"{slug}:{exc}")

    first = rows[0] if rows else {}
    config = {
        "schema_version": "xuan_frontier_btc_same_window_no_order_runtime_config_v1",
        "created_utc": utc_now(),
        "mode": "read_only_ws_no_order_runtime_binding",
        "runner_profile_id": first.get("runner_profile_id") or "btc_same_window_tiny_canary_dryrun_v1",
        "dry_run_only": True,
        "no_order": True,
        "orders_allowed": False,
        "live_orders_allowed": False,
        "candidate_import_allowed": False,
        "live_import_enabled": False,
        "remote_runner_allowed": False,
        "deployable": False,
        "private_truth_ready": False,
        "owner_private_truth_ready": False,
        "candidate_source": {
            "csv": str(import_contract),
            "sha256": sha256_file(import_contract),
            "filter_name": first.get("filter_name"),
            "filter_version": first.get("filter_version"),
            "assets": ["BTC"],
            "candidate_count": len(rows),
            "deterministic_candidate_id_column": "deterministic_candidate_id",
            "candidate_import_allowed": False,
        },
        "live_market_selector": {
            "mode": "prefix_offsets",
            "prefix": "btc-updown-5m",
            "round_offsets": round_offsets,
            "resolved_at_utc": utc_now(),
            "resolved_markets": resolved_markets,
            "resolver": "public_gamma_events_by_slug",
        },
        "source_semantics": {
            "path": str(source_semantics_path),
            "sha256": sha256_file(source_semantics_path),
            "source_semantics_contract_id": source_semantics.get("source_semantics_contract_id"),
            "source_dataset_fingerprint": source_semantics.get("source_dataset_fingerprint"),
            "l2_top_overlay_contract_id": source_semantics.get("l2_top_overlay_contract_id"),
            "historical_shadow_or_v1_is_private_truth": False,
        },
        "owner_truth_collection": {
            "output_root": str(output_dir / "owner_truth"),
            "schema_path": str(owner_truth_schema),
            "schema_sha256": sha256_file(owner_truth_schema),
            "future_owner_execution_required": True,
            "owner_private_truth_data_ready": False,
        },
        "log_plan": {
            "root": str(output_dir / "runtime_logs"),
            "status_json": str(output_dir / "runtime_logs/no_order_backend_status.json"),
            "events_jsonl": str(output_dir / "runtime_logs/no_order_backend_events.jsonl"),
        },
        "policy": {
            "script_may_start_ws": False,
            "config_preparation_only": True,
            "public_gamma_metadata_only": True,
            "no_private_key": True,
            "no_import": True,
            "no_orders": True,
            "no_cancels": True,
            "no_redeems": True,
            "no_promotion_claim": True,
            "no_private_truth_claim": True,
        },
    }
    validation_errors = row_errors + resolve_errors
    if len(resolved_markets) != len(round_offsets):
        validation_errors.append("resolved_market_count_mismatch")
    config_path = output_dir / "xuan_frontier_btc_no_order_runtime_config.json"
    manifest_path = output_dir / "XUAN_FRONTIER_BTC_NO_ORDER_RUNTIME_CONFIG_MANIFEST.json"
    write_json(config_path, config)
    manifest = {
        "schema_version": "xuan_frontier_btc_no_order_runtime_config_manifest_v1",
        "created_utc": utc_now(),
        "status": (
            "KEEP_XUAN_FRONTIER_BTC_NO_ORDER_RUNTIME_CONFIG_READY_VALIDATE_ONLY"
            if not validation_errors
            else "BLOCKED_XUAN_FRONTIER_BTC_NO_ORDER_RUNTIME_CONFIG_INVALID"
        ),
        "validation_errors": validation_errors,
        "config": {
            "path": str(config_path),
            "sha256": sha256_file(config_path),
        },
        "candidate_count": len(rows),
        "resolved_market_count": len(resolved_markets),
        "resolved_markets": resolved_markets,
        "promotion_gate": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "live_orders_allowed": False,
            "deployable": False,
        },
    }
    write_json(manifest_path, manifest)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "validation_errors": validation_errors,
                "config": str(config_path),
                "manifest": str(manifest_path),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if not validation_errors else 1


if __name__ == "__main__":
    raise SystemExit(main())

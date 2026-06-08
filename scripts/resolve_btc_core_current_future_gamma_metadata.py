#!/usr/bin/env python3
"""Resolve future BTC 5m market metadata from public Gamma endpoints.

Read-only public metadata only. This script does not open WebSockets, does not
observe books, does not start OOS, and never touches private/order/live paths.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_OUTPUT_DIR = ROOT / "data/exports/btc_core_gamma_metadata_resolver_20260605"
STATUS_OK = "KEEP_BTC_CORE_GAMMA_METADATA_RESOLVED_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_PARTIAL = "KEEP_BTC_CORE_GAMMA_METADATA_RESOLVED_PARTIAL_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_GAMMA_METADATA_RESOLVER_NO_BOUND_MARKETS_NOT_OOS_READY"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def iso_from_ms(ts_ms: int) -> str:
    return dt.datetime.fromtimestamp(ts_ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stable_hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def target_start_ms(created_ms: int, offset: int) -> int:
    base_sec = math.floor(created_ms / 1000 / 300) * 300
    return int((base_sec + 300 * offset) * 1000)


def fetch_json(url: str, timeout_sec: float) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; btc-core-research/1.0)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_jsonish(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def first_market_from_events(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, list) or not payload:
        return None
    event = payload[0]
    if not isinstance(event, dict):
        return None
    markets = event.get("markets")
    if isinstance(markets, list) and markets:
        market = markets[0]
        if isinstance(market, dict):
            out = dict(market)
            out.setdefault("event_id", event.get("id"))
            out.setdefault("event_slug", event.get("slug"))
            out.setdefault("event_title", event.get("title"))
            return out
    return None


def normalize_market(raw: dict[str, Any], slug: str) -> dict[str, Any]:
    outcomes = parse_jsonish(raw.get("outcomes"))
    token_ids = parse_jsonish(raw.get("clobTokenIds") or raw.get("clob_token_ids"))
    yes_token = ""
    no_token = ""
    if isinstance(outcomes, list) and isinstance(token_ids, list):
        for outcome, token_id in zip(outcomes, token_ids):
            outcome_text = str(outcome).strip().lower()
            if outcome_text in {"yes", "up"} and not yes_token:
                yes_token = str(token_id)
            if outcome_text in {"no", "down"} and not no_token:
                no_token = str(token_id)
    if not yes_token and isinstance(token_ids, list) and len(token_ids) >= 2:
        yes_token = str(token_ids[0])
        no_token = str(token_ids[1])
    condition_id = str(raw.get("conditionId") or raw.get("condition_id") or "")
    market_id = str(raw.get("market_id") or raw.get("conditionId") or raw.get("condition_id") or raw.get("id") or "")
    return {
        "slug": slug,
        "market_id": market_id,
        "conditionId": condition_id,
        "condition_id": condition_id,
        "token_id_yes": yes_token,
        "token_id_no": no_token,
        "subscribed_asset_ids": [yes_token, no_token] if yes_token and no_token else [],
        "outcomes": json.dumps(outcomes, separators=(",", ":")) if isinstance(outcomes, list) else outcomes,
        "clobTokenIds": json.dumps(token_ids, separators=(",", ":")) if isinstance(token_ids, list) else token_ids,
        "gamma_id": str(raw.get("id") or ""),
        "gamma_question": raw.get("question") or raw.get("title") or raw.get("event_title") or "",
        "gamma_end_date": raw.get("endDate") or "",
        "raw_gamma_hash": stable_hash(raw),
    }


def resolve_slug(slug: str, timeout_sec: float) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    market_url = f"{GAMMA_MARKETS_URL}?{urllib.parse.urlencode({'slug': slug, 'limit': 1})}"
    event_url = f"{GAMMA_EVENTS_URL}?{urllib.parse.urlencode({'slug': slug, 'limit': 1})}"
    audit: dict[str, Any] = {
        "slug": slug,
        "market_url": market_url,
        "event_url": event_url,
        "markets_status": "not_attempted",
        "events_status": "not_attempted",
        "resolver_source": "public_gamma_markets_then_events_fallback",
    }
    try:
        payload = fetch_json(market_url, timeout_sec)
        audit["markets_status"] = "ok"
        audit["markets_payload_type"] = type(payload).__name__
        audit["markets_count"] = len(payload) if isinstance(payload, list) else None
        if isinstance(payload, list) and payload:
            raw = payload[0]
            if isinstance(raw, dict):
                return normalize_market(raw, slug), {**audit, "binding_status": "BOUND", "source_used": "markets"}
    except Exception as exc:  # noqa: BLE001
        audit["markets_status"] = "error"
        audit["markets_error"] = f"{type(exc).__name__}:{exc}"
    try:
        payload = fetch_json(event_url, timeout_sec)
        audit["events_status"] = "ok"
        audit["events_payload_type"] = type(payload).__name__
        audit["events_count"] = len(payload) if isinstance(payload, list) else None
        raw = first_market_from_events(payload)
        if raw is not None:
            return normalize_market(raw, slug), {**audit, "binding_status": "BOUND", "source_used": "events"}
    except Exception as exc:  # noqa: BLE001
        audit["events_status"] = "error"
        audit["events_error"] = f"{type(exc).__name__}:{exc}"
    return None, {**audit, "binding_status": "REJECTED_FAIL_CLOSED", "source_used": "", "reason": "gamma_market_missing"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--projection-created-ts-ms", type=int, default=int(time.time() * 1000))
    parser.add_argument("--start-round-offset", type=int, default=73)
    parser.add_argument("--target-round-count", type=int, default=288)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--sleep-sec", type=float, default=0.02)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise SystemExit(f"BLOCKED_OUTPUT_DIR_EXISTS:{output_dir}")
    output_dir.mkdir(parents=True)
    markets: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    started = utc_now()
    for idx in range(args.target_round_count):
        offset = args.start_round_offset + idx
        start_ms = target_start_ms(args.projection_created_ts_ms, offset)
        slug = f"btc-updown-5m-{start_ms // 1000}"
        market, audit = resolve_slug(slug, args.timeout_sec)
        audit.update(
            {
                "projection_round_index": idx,
                "start_round_offset": offset,
                "window_start_ts_ms": start_ms,
                "window_end_ts_ms": start_ms + 300_000,
            }
        )
        if market is not None:
            market.update(
                {
                    "projection_round_index": idx,
                    "start_round_offset": offset,
                    "window_start_ts_ms": start_ms,
                    "window_end_ts_ms": start_ms + 300_000,
                    "resolver_source": audit["resolver_source"],
                }
            )
            markets.append(market)
        audit_rows.append(audit)
        if args.sleep_sec > 0:
            time.sleep(args.sleep_sec)
    ended = utc_now()
    status = STATUS_OK if len(markets) == args.target_round_count else STATUS_PARTIAL if markets else STATUS_BLOCKED
    metadata = {
        "schema_version": 1,
        "status": status,
        "created_at": ended,
        "started_at": started,
        "projection_created_ts_ms": args.projection_created_ts_ms,
        "projection_created_ts_utc": iso_from_ms(args.projection_created_ts_ms),
        "start_round_offset": args.start_round_offset,
        "target_round_count": args.target_round_count,
        "bound_market_count": len(markets),
        "rejected_market_count": args.target_round_count - len(markets),
        "markets": markets,
        "provenance": {
            "source": "public_gamma_http_metadata",
            "network_used": True,
            "ws_used": False,
            "orders_authorized": False,
            "private_key_loaded": False,
            "oos_authorized": False,
            "live_ready": False,
            "deployable": False,
        },
    }
    metadata_path = output_dir / "BTC_CORE_REVIEWED_RESOLVER_METADATA.json"
    audit_path = output_dir / "BTC_CORE_GAMMA_RESOLVER_AUDIT.jsonl"
    summary_path = output_dir / "BTC_CORE_GAMMA_METADATA_RESOLVER_SUMMARY.json"
    hash_manifest_path = output_dir / "BTC_CORE_GAMMA_METADATA_RESOLVER_HASH_MANIFEST.json"
    write_json(metadata_path, metadata)
    audit_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in audit_rows), encoding="utf-8")
    summary = {
        "schema_version": 1,
        "status": status,
        "projection_created_ts_ms": args.projection_created_ts_ms,
        "projection_created_ts_utc": iso_from_ms(args.projection_created_ts_ms),
        "target_round_count": args.target_round_count,
        "bound_market_count": len(markets),
        "rejected_market_count": args.target_round_count - len(markets),
        "audit_bound_count": sum(1 for row in audit_rows if row.get("binding_status") == "BOUND"),
        "audit_reject_count": sum(1 for row in audit_rows if row.get("binding_status") != "BOUND"),
        "metadata_json": str(metadata_path),
        "metadata_sha256": sha256_file(metadata_path),
        "audit_jsonl": str(audit_path),
        "audit_sha256": sha256_file(audit_path),
        "non_claims": metadata["provenance"],
    }
    write_json(summary_path, summary)
    files = [metadata_path, audit_path, summary_path]
    hash_manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "files": {
            path.name: {"path": str(path), "sha256": sha256_file(path), "size": path.stat().st_size}
            for path in files
        },
    }
    write_json(hash_manifest_path, hash_manifest)
    print(f"status={status}")
    print(f"output_dir={output_dir}")
    print(f"bound_market_count={len(markets)}")
    print(f"rejected_market_count={args.target_round_count - len(markets)}")
    print(f"metadata_json={metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

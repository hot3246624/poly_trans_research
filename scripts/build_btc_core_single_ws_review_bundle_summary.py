#!/usr/bin/env python3
"""Build a local review summary for BTC_CORE single-WS live handoff bundles.

This script is deliberately local-only. It does not SSH, connect to WebSocket,
touch shared-ingress, or start any runner. It summarizes artifacts that were
already copied into a review bundle directory.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


REPORT = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REPORT.csv"
EVAL = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_EVAL.json"
SUMMARY_JSON = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REVIEW_SUMMARY.json"
SUMMARY_MD = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REVIEW_SUMMARY.md"
SHA256S = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REVIEW_BUNDLE_SHA256SUMS.txt"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def as_int(raw: str | int | None) -> int | None:
    if raw is None or raw == "":
        return None
    return int(raw)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_summary(bundle_dir: Path) -> dict[str, Any]:
    eval_payload = read_json(bundle_dir / EVAL)
    with (bundle_dir / REPORT).open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return {
        "status": eval_payload.get("status"),
        "ok": eval_payload.get("ok"),
        "highest_allowed_status": eval_payload.get("highest_allowed_status"),
        "scope": eval_payload.get("scope"),
        "scope_interpretation": eval_payload.get("scope_interpretation"),
        "evidence_target_market_count": eval_payload.get("evidence_target_market_count"),
        "handoff_target_market_count": eval_payload.get("handoff_target_market_count"),
        "subscribed_target_market_count": eval_payload.get("subscribed_target_market_count"),
        "active_condition_count_max": eval_payload.get("active_condition_count_max"),
        "ws_connection_count": eval_payload.get("ws_connection_count"),
        "ws_disconnect_count": eval_payload.get("ws_disconnect_count"),
        "ws_reconnect_count": eval_payload.get("ws_reconnect_count"),
        "lifecycle_terminal_close_count": eval_payload.get("lifecycle_terminal_close_count"),
        "observed_evidence_target_market_count": eval_payload.get("observed_evidence_target_market_count"),
        "top_depth_complete_evidence_market_count": eval_payload.get("top_depth_complete_evidence_market_count"),
        "live_fresh_top_depth_evidence_market_count": eval_payload.get("live_fresh_top_depth_evidence_market_count"),
        "book_age_p50_ms": eval_payload.get("book_age_p50_ms"),
        "book_age_p95_ms": eval_payload.get("book_age_p95_ms"),
        "book_age_max_ms": eval_payload.get("book_age_max_ms"),
        "threshold_failure_count": eval_payload.get("threshold_failure_count"),
        "threshold_failures": eval_payload.get("threshold_failures"),
        "readiness": eval_payload.get("readiness"),
        "safety_counters": eval_payload.get("safety_counters"),
        "transport": eval_payload.get("transport"),
        "rest_book_used": eval_payload.get("rest_book_used"),
        "shared_ingress_used": eval_payload.get("shared_ingress_used"),
        "markets": [
            {
                "slug": row.get("slug"),
                "role": row.get("target_role"),
                "book_snapshot_count": as_int(row.get("book_snapshot_count")),
                "top_depth_complete_count": as_int(row.get("top_depth_complete_count")),
                "fresh_top_depth_after_warmup_count": as_int(row.get("fresh_top_depth_after_warmup_count")),
                "book_age_p95_ms": as_int(row.get("book_age_p95_ms")),
                "book_age_max_ms": as_int(row.get("book_age_max_ms")),
                "pair_ask_cost_p50": row.get("pair_ask_cost_p50"),
                "pair_ask_cost_p95": row.get("pair_ask_cost_p95"),
                "pair_ask_cost_min": row.get("pair_ask_cost_min"),
                "pair_ask_cost_max": row.get("pair_ask_cost_max"),
                "top_pair_ask_size_p50": row.get("top_pair_ask_size_p50"),
            }
            for row in rows
        ],
    }


def write_markdown(path: Path, summary: dict[str, Any]) -> None:
    lines = [
        "# BTC_CORE single-WS live handoff review summary",
        "",
        f"status: `{summary['status']}`",
        f"ok: `{summary['ok']}`",
        f"threshold_failures: `{summary['threshold_failures']}`",
        "",
        "Key metrics:",
    ]
    for key in [
        "evidence_target_market_count",
        "handoff_target_market_count",
        "subscribed_target_market_count",
        "active_condition_count_max",
        "ws_connection_count",
        "ws_disconnect_count",
        "ws_reconnect_count",
        "lifecycle_terminal_close_count",
        "observed_evidence_target_market_count",
        "top_depth_complete_evidence_market_count",
        "live_fresh_top_depth_evidence_market_count",
        "book_age_p95_ms",
        "book_age_max_ms",
    ]:
        lines.append(f"- {key}: `{summary.get(key)}`")
    lines.extend(
        [
            "",
            "Interpretation: public/no-order current-live BTC 5m evidence only. "
            "Next-round targets are handoff attribution until live. This is not "
            "private truth, promotion, live readiness, deployability, or a full "
            "215/288-market OOS pass.",
            "",
            "Markets:",
        ]
    )
    for market in summary["markets"]:
        lines.append(
            f"- {market['slug']} ({market['role']}): "
            f"snapshots={market['book_snapshot_count']}, "
            f"top_depth={market['top_depth_complete_count']}, "
            f"fresh_top_depth={market['fresh_top_depth_after_warmup_count']}, "
            f"p95_age_ms={market['book_age_p95_ms']}, max_age_ms={market['book_age_max_ms']}, "
            f"pair_ask_p50={market.get('pair_ask_cost_p50')}, pair_ask_p95={market.get('pair_ask_cost_p95')}, "
            f"top_pair_ask_size_p50={market.get('top_pair_ask_size_p50')}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_hash_manifest(bundle_dir: Path) -> None:
    entries = []
    for path in sorted(bundle_dir.iterdir()):
        if not path.is_file() or path.name == SHA256S:
            continue
        entries.append(f"{sha256_file(path)}  {path.name}")
    (bundle_dir / SHA256S).write_text("\n".join(entries) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    summary = build_summary(args.bundle_dir)
    (args.bundle_dir / SUMMARY_JSON).write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(args.bundle_dir / SUMMARY_MD, summary)
    write_hash_manifest(args.bundle_dir)
    print(json.dumps({"status": summary["status"], "ok": summary["ok"], "bundle_dir": str(args.bundle_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

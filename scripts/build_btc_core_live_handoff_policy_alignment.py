#!/usr/bin/env python3
"""Build BTC_CORE live-handoff evidence-to-policy alignment artifacts.

This is a local review helper. It consumes an already-copied single-WS review
bundle plus the reviewed BTC_CORE target CSV and backtest/rescore manifest. It
does not connect to WebSocket, SSH, import candidates, load keys, or place
orders.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


REPORT_NAME = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REPORT.csv"
EVAL_NAME = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_EVAL.json"
SUMMARY_NAME = "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REVIEW_SUMMARY.json"
STATUS_KEEP = "KEEP_BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT_READY_RESEARCH_ONLY"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT_FAIL_CLOSED"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as f:
        return [dict(row) for row in csv.DictReader(f)]


def to_int(raw: str | int | None) -> int | None:
    if raw is None or raw == "":
        return None
    return int(raw)


def to_float(raw: Any, default: float = 0.0) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def role_is_evidence(role: str) -> bool:
    return role in {"evidence", "evidence_current", "current"} or role.startswith("evidence")


def build_alignment(
    *,
    bundle_dir: Path,
    target_csv: Path,
    rescore_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    eval_payload = read_json(bundle_dir / EVAL_NAME)
    summary = read_json(bundle_dir / SUMMARY_NAME)
    report_rows = read_csv(bundle_dir / REPORT_NAME)
    target_rows = [row for row in read_csv(target_csv) if row.get("binding_status") == "BOUND"]
    rescore = read_json(rescore_manifest)

    target_by_condition = {row["condition_id"]: row for row in target_rows}
    report_condition_ids = [row["condition_id"] for row in report_rows]
    missing_from_targets = [cid for cid in report_condition_ids if cid not in target_by_condition]
    target_hash = sha256_file(target_csv)
    bundle_files = {
        path.name: sha256_file(path)
        for path in sorted(bundle_dir.iterdir())
        if path.is_file()
    }

    evidence_rows = [row for row in report_rows if role_is_evidence(row.get("target_role", ""))]
    handoff_rows = [row for row in report_rows if not role_is_evidence(row.get("target_role", ""))]
    evidence_pair_ask_p50_values = [
        to_float(row.get("pair_ask_cost_p50"), default=float("nan"))
        for row in evidence_rows
        if row.get("pair_ask_cost_p50") not in (None, "")
    ]
    evidence_pair_ask_p95_values = [
        to_float(row.get("pair_ask_cost_p95"), default=float("nan"))
        for row in evidence_rows
        if row.get("pair_ask_cost_p95") not in (None, "")
    ]
    starts = [to_int(row.get("window_start_ts_ms")) for row in evidence_rows]
    starts = [value for value in starts if value is not None]
    starts_sorted = sorted(starts)
    deltas = [b - a for a, b in zip(starts_sorted, starts_sorted[1:])]
    window_delta_counts = Counter(deltas)

    threshold_failures: list[str] = []
    if eval_payload.get("ok") is not True:
        threshold_failures.append("single_ws_eval_not_ok")
    if eval_payload.get("threshold_failure_count") != 0:
        threshold_failures.append("single_ws_threshold_failures_present")
    if eval_payload.get("ws_connection_count") != 1:
        threshold_failures.append("ws_connection_count_not_one")
    if eval_payload.get("ws_disconnect_count") != 0 or eval_payload.get("ws_reconnect_count") != 0:
        threshold_failures.append("ws_disconnect_or_reconnect_nonzero")
    if eval_payload.get("rest_book_used") is not False:
        threshold_failures.append("rest_book_used_not_false")
    if eval_payload.get("shared_ingress_used") is not False:
        threshold_failures.append("shared_ingress_used_not_false")
    if missing_from_targets:
        threshold_failures.append("report_condition_not_in_reviewed_target_csv")
    if len(evidence_rows) != eval_payload.get("evidence_target_market_count"):
        threshold_failures.append("evidence_row_count_mismatch")
    if len(handoff_rows) != eval_payload.get("handoff_target_market_count"):
        threshold_failures.append("handoff_row_count_mismatch")
    if window_delta_counts and set(window_delta_counts) != {300_000}:
        threshold_failures.append("evidence_windows_not_contiguous_5m")
    for row in evidence_rows:
        if row.get("observed") != "True":
            threshold_failures.append("evidence_row_not_observed")
            break
        if row.get("token_side_top_depth_complete") != "True":
            threshold_failures.append("evidence_row_top_depth_incomplete")
            break
        if to_int(row.get("fresh_top_depth_after_warmup_count")) in (None, 0):
            threshold_failures.append("evidence_row_missing_fresh_top_depth_after_warmup")
            break
    readiness = eval_payload.get("readiness") or {}
    if any(readiness.get(key) is not False for key in ("private_truth_ready", "strategy_promotion_ready", "live_ready", "deployable")):
        threshold_failures.append("readiness_flag_true")
    safety = eval_payload.get("safety_counters") or {}
    if any(to_float(value) != 0.0 for value in safety.values()):
        threshold_failures.append("safety_counter_nonzero")

    rescore_summary = rescore.get("summary") or {}
    handoff_summary = rescore.get("same_window_handoff") or {}
    status = STATUS_KEEP if not threshold_failures else STATUS_BLOCKED
    artifact = {
        "status": status,
        "schema_version": 1,
        "source_kind": "public_no_order_single_ws_plus_local_backtest_research",
        "bundle_dir": str(bundle_dir),
        "target_csv": str(target_csv),
        "target_csv_sha256": target_hash,
        "bundle_file_sha256": bundle_files,
        "single_ws_eval": {
            "status": eval_payload.get("status"),
            "ok": eval_payload.get("ok"),
            "evidence_target_market_count": eval_payload.get("evidence_target_market_count"),
            "handoff_target_market_count": eval_payload.get("handoff_target_market_count"),
            "subscribed_target_market_count": eval_payload.get("subscribed_target_market_count"),
            "active_condition_count_max": eval_payload.get("active_condition_count_max"),
            "ws_connection_count": eval_payload.get("ws_connection_count"),
            "ws_disconnect_count": eval_payload.get("ws_disconnect_count"),
            "ws_reconnect_count": eval_payload.get("ws_reconnect_count"),
            "book_age_p95_ms": eval_payload.get("book_age_p95_ms"),
            "book_age_max_ms": eval_payload.get("book_age_max_ms"),
            "threshold_failures": eval_payload.get("threshold_failures"),
        },
        "policy_alignment": {
            "reviewed_target_bound_count": len(target_rows),
            "report_condition_count": len(report_rows),
            "missing_report_conditions_from_target_csv": missing_from_targets,
            "evidence_market_count": len(evidence_rows),
            "handoff_market_count": len(handoff_rows),
            "evidence_window_delta_ms_counts": dict(window_delta_counts),
            "current_live_only_evidence_denominator": True,
            "next_market_handoff_attribution_only": True,
            "far_future_markets_are_schedule_pool_not_denominator": True,
            "evidence_slugs": [row.get("slug") for row in evidence_rows],
            "handoff_slugs": [row.get("slug") for row in handoff_rows],
            "evidence_pair_ask_cost_p50_min": round(min(evidence_pair_ask_p50_values), 6)
            if evidence_pair_ask_p50_values
            else None,
            "evidence_pair_ask_cost_p50_max": round(max(evidence_pair_ask_p50_values), 6)
            if evidence_pair_ask_p50_values
            else None,
            "evidence_pair_ask_cost_p95_max": round(max(evidence_pair_ask_p95_values), 6)
            if evidence_pair_ask_p95_values
            else None,
        },
        "backtest_research_context": {
            "rescore_status": rescore.get("status"),
            "xuan_after_fee_pnl": rescore_summary.get("xuan_after_fee_pnl"),
            "official_taker_fee": rescore_summary.get("official_taker_fee"),
            "net_roi": rescore_summary.get("net_roi"),
            "same_window_handoff_market_count": handoff_summary.get("handoff_market_count"),
            "same_window_positive_handoff_market_count": handoff_summary.get("positive_handoff_market_count"),
            "official_fee_formula": "fee = shares * 0.07 * price * (1 - price)",
            "fee_source": "Polymarket crypto taker fee docs; maker fee is zero",
        },
        "non_claims": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
            "orders_authorized": False,
            "candidate_import_authorized": False,
            "latest_pointer_update_authorized": False,
        },
        "threshold_failure_count": len(threshold_failures),
        "threshold_failures": threshold_failures,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT.json").write_text(
        json.dumps(artifact, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_markdown(output_dir / "BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT.md", artifact)
    write_hashes(output_dir)
    return artifact


def write_markdown(path: Path, artifact: dict[str, Any]) -> None:
    single = artifact["single_ws_eval"]
    align = artifact["policy_alignment"]
    backtest = artifact["backtest_research_context"]
    lines = [
        "# BTC_CORE live handoff policy alignment",
        "",
        f"status: `{artifact['status']}`",
        f"threshold_failures: `{artifact['threshold_failures']}`",
        "",
        "Single-WS evidence:",
        f"- evidence markets: `{single['evidence_target_market_count']}`",
        f"- handoff markets: `{single['handoff_target_market_count']}`",
        f"- ws connections/disconnects/reconnects: `{single['ws_connection_count']}/{single['ws_disconnect_count']}/{single['ws_reconnect_count']}`",
        f"- book age p95/max ms: `{single['book_age_p95_ms']}/{single['book_age_max_ms']}`",
        "",
        "Policy alignment:",
        f"- reviewed target bound count: `{align['reviewed_target_bound_count']}`",
        f"- report condition count: `{align['report_condition_count']}`",
        f"- evidence market count: `{align['evidence_market_count']}`",
        f"- handoff market count: `{align['handoff_market_count']}`",
        f"- evidence window delta ms counts: `{align['evidence_window_delta_ms_counts']}`",
        f"- evidence pair ask cost p50 min/max: `{align.get('evidence_pair_ask_cost_p50_min')}/{align.get('evidence_pair_ask_cost_p50_max')}`",
        f"- evidence pair ask cost p95 max: `{align.get('evidence_pair_ask_cost_p95_max')}`",
        "- current/live markets are the only evidence denominator",
        "- next market is handoff attribution only until live",
        "- far-future markets are a schedule pool, not evidence",
        "",
        "Backtest context:",
        f"- rescore status: `{backtest['rescore_status']}`",
        f"- xuan_after_fee_pnl: `{backtest['xuan_after_fee_pnl']}`",
        f"- official_taker_fee: `{backtest['official_taker_fee']}`",
        f"- net_roi: `{backtest['net_roi']}`",
        f"- same-window positive handoff markets: `{backtest['same_window_positive_handoff_market_count']}/{backtest['same_window_handoff_market_count']}`",
        "",
        "Boundary:",
        "- research-only, public/no-order evidence",
        "- no private truth, promotion, live readiness, deployability, import, order, or latest pointer claim",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_hashes(output_dir: Path) -> None:
    lines = []
    for path in sorted(output_dir.iterdir()):
        if not path.is_file() or path.name == "BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT_SHA256SUMS.txt":
            continue
        lines.append(f"{sha256_file(path)}  {path.name}")
    (output_dir / "BTC_CORE_LIVE_HANDOFF_POLICY_ALIGNMENT_SHA256SUMS.txt").write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--target-csv", type=Path, required=True)
    parser.add_argument("--rescore-manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    artifact = build_alignment(
        bundle_dir=args.bundle_dir,
        target_csv=args.target_csv,
        rescore_manifest=args.rescore_manifest,
        output_dir=args.output_dir,
    )
    print(json.dumps({"status": artifact["status"], "output_dir": str(args.output_dir)}, indent=2))
    return 0 if artifact["status"] == STATUS_KEEP else 2


if __name__ == "__main__":
    raise SystemExit(main())

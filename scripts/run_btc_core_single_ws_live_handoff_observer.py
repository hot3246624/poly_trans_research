#!/usr/bin/env python3
"""Single-connection BTC_CORE public WS live handoff observer.

This is the CE25-like research mode: evidence is only the BTC 5m market while
it is live. The next market may be subscribed as a handoff target, but a target
only enters the evidence denominator after its own window becomes current.

Boundary:
- exactly one direct public CLOB market WebSocket connection;
- no shared-ingress/shared-WS;
- no REST book evidence;
- no private keys, imports, orders, cancels, redeems, live, deploy, funding, or
  latest pointer updates.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_btc_core_scoped_public_ws_no_order_observer as scoped  # noqa: E402


STATUS_KEEP = "KEEP_BTC_CORE_SINGLE_WS_LIVE_HANDOFF_RESEARCH_REVIEW_REQUIRED_PROMOTION_BLOCKED_OWNER_TRUTH"
STATUS_BLOCKED = "BLOCKED_BTC_CORE_SINGLE_WS_LIVE_HANDOFF_FAIL_CLOSED"
OUTPUT_FILES = {
    "report": "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_REPORT.csv",
    "audit": "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_AUDIT_MANIFEST.json",
    "gate": "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_GATE_SUMMARY.json",
    "eval": "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_EVAL.json",
    "events": "BTC_CORE_SINGLE_WS_LIVE_HANDOFF_EVENTS.jsonl",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {key: output_dir / name for key, name in OUTPUT_FILES.items()}


def sorted_bound_targets(targets: list[scoped.TargetMarket]) -> list[scoped.TargetMarket]:
    return sorted(targets, key=lambda item: (item.window_start_ts_ms, item.condition_id))


def select_active_targets(
    targets: list[scoped.TargetMarket],
    *,
    at_ms: int,
    lead_ms: int,
    min_remaining_ms: int,
    next_target_count: int,
) -> list[scoped.TargetMarket]:
    current = [
        target
        for target in targets
        if target.window_start_ts_ms <= at_ms < target.window_end_ts_ms
        and target.window_end_ts_ms > at_ms + min_remaining_ms
    ]
    if not current:
        return []
    future = [
        target
        for target in targets
        if at_ms < target.window_start_ts_ms <= at_ms + lead_ms
    ]
    selected = [replace(current[0], target_role="current_pending_depth")]
    selected.extend(replace(target, target_role="handoff_next") for target in future[: max(0, next_target_count)])
    return selected


def target_is_live(target: scoped.TargetMarket, at_ms: int) -> bool:
    return target.window_start_ts_ms <= at_ms < target.window_end_ts_ms


def target_is_decision_window(target: scoped.TargetMarket, at_ms: int, min_remaining_ms: int) -> bool:
    return target.window_start_ts_ms <= at_ms < target.window_end_ts_ms - min_remaining_ms


def target_is_evidence(stats: scoped.MarketStats) -> bool:
    return stats.target.target_role in {"evidence", "evidence_current"} or stats.target.target_role.startswith("evidence")


def ensure_stats(
    stats_by_condition: dict[str, scoped.MarketStats],
    target: scoped.TargetMarket,
) -> scoped.MarketStats:
    existing = stats_by_condition.get(target.condition_id)
    if existing is None:
        existing = scoped.MarketStats(target=target, ws_chunk_id=0)
        stats_by_condition[target.condition_id] = existing
        return existing
    if target.target_role.startswith("evidence") and not existing.target.target_role.startswith("evidence"):
        existing.target = replace(existing.target, target_role=target.target_role)
    return existing


def percentile(values: list[int], q: float) -> int | None:
    return scoped.pct(values, q)


def write_report(path: Path, stats_by_condition: dict[str, scoped.MarketStats]) -> None:
    rows = [
        scoped.market_row(stats)
        for stats in sorted(stats_by_condition.values(), key=lambda item: item.target.projection_round_index)
    ]
    scoped.write_report(path, rows)


def non_claims() -> dict[str, bool]:
    return scoped.non_claims()


async def run_single_ws(args: argparse.Namespace) -> int:
    import websockets

    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        print(f"BLOCKED_OUTPUT_DIR_EXISTS path={args.output_dir}", file=sys.stderr)
        return 2
    args.output_dir.mkdir(parents=True, exist_ok=True)
    paths = output_paths(args.output_dir)
    paths["events"].write_text("", encoding="utf-8")

    errors: list[str] = []
    if args.expected_target_csv_sha256 and scoped.sha256_file(args.target_csv) != args.expected_target_csv_sha256:
        errors.append("target_csv_hash_mismatch")
    try:
        targets = sorted_bound_targets(scoped.load_targets(args.target_csv))
    except Exception as exc:  # noqa: BLE001
        targets = []
        errors.append(f"target_load_error:{exc}")
    if args.max_ws_connections != 1:
        errors.append("max_ws_connections_out_of_bounds")
    if args.next_target_count < 0:
        errors.append("next_target_count_negative")
    if args.lead_ms < 0:
        errors.append("lead_ms_negative")
    if args.min_remaining_ms < 0:
        errors.append("min_remaining_ms_negative")
    if args.duration_sec <= 0:
        errors.append("duration_sec_nonpositive")
    if args.max_decision_depth_gap_ms < 0:
        errors.append("max_decision_depth_gap_ms_negative")
    if errors:
        write_fail_closed(paths, args, targets, errors, "preflight_failed")
        return 2

    try:
        ws_iter = scoped.fallback_iter_ws_objects
        normalizer = scoped.fallback_normalize_market_ws_message
        try:
            from completion_first_data.capture.websocket_sidecar import _iter_ws_objects, normalize_market_ws_message

            ws_iter = _iter_ws_objects
            normalizer = normalize_market_ws_message
        except Exception:  # noqa: BLE001
            pass

        asset_to_condition: dict[str, str] = {}
        asset_to_side: dict[str, str] = {}
        stats_by_condition: dict[str, scoped.MarketStats] = {}
        subscribed_assets: set[str] = set()
        active_condition_counts: list[int] = []
        subscribed_condition_ids: set[str] = set()
        event_counts: Counter[str] = Counter()
        assemblers: dict[str, Any] = {}
        allowed_events = {"book", "price_change", "best_bid_ask"}
        ws_open_count = 0
        ws_disconnect_count = 0
        ws_reconnect_count = 0
        lifecycle_terminal_close_count = 0
        raw_message_count = 0
        normalized_book_count = 0
        diagnostic_pending_depth_snapshot_count = 0
        diagnostic_pending_depth_incomplete_count = 0
        evidence_top_depth_incomplete_after_ready_count = 0
        decision_depth_gap_burst_count = 0
        decision_depth_gap_max_ms = 0
        open_depth_gap_start_ms_by_condition: dict[str, int] = {}
        open_depth_gap_last_ms_by_condition: dict[str, int] = {}
        terminal_exit_snapshot_count = 0
        terminal_exit_top_depth_incomplete_count = 0
        live_current_seen_condition_ids: set[str] = set()
        evidence_ready_condition_ids: set[str] = set()
        stop_reason = "not_started"
        error: str | None = None

        run_start_ms = now_ms()
        warmup_end_ms = run_start_ms + int(max(0.0, args.warmup_sec) * 1000)
        stop_at = time.monotonic() + args.duration_sec
        last_selection_ms = 0

        def note_decision_depth_gap(condition_id: str, recv_ms: int) -> None:
            nonlocal decision_depth_gap_burst_count
            if condition_id not in open_depth_gap_start_ms_by_condition:
                open_depth_gap_start_ms_by_condition[condition_id] = recv_ms
                decision_depth_gap_burst_count += 1
            open_depth_gap_last_ms_by_condition[condition_id] = recv_ms

        def close_decision_depth_gap(condition_id: str) -> None:
            nonlocal decision_depth_gap_max_ms
            start_ms = open_depth_gap_start_ms_by_condition.pop(condition_id, None)
            last_ms = open_depth_gap_last_ms_by_condition.pop(condition_id, None)
            if start_ms is not None and last_ms is not None:
                decision_depth_gap_max_ms = max(decision_depth_gap_max_ms, max(0, last_ms - start_ms))

        async with websockets.connect(args.ws_url, ping_interval=None, max_size=None) as ws:
            ws_open_count += 1
            stop_reason = "connected"
            with paths["events"].open("a", encoding="utf-8") as event_file:
                while True:
                    monotonic_remaining = stop_at - time.monotonic()
                    if monotonic_remaining <= 0:
                        stop_reason = "duration_elapsed"
                        break

                    current_ms = now_ms()
                    if current_ms - last_selection_ms >= args.selection_poll_ms:
                        last_selection_ms = current_ms
                        active_targets = select_active_targets(
                            targets,
                            at_ms=current_ms,
                            lead_ms=args.lead_ms,
                            min_remaining_ms=args.min_remaining_ms,
                            next_target_count=args.next_target_count,
                        )
                        active_condition_counts.append(len(active_targets))
                        if active_targets:
                            new_assets: list[str] = []
                            for target in active_targets:
                                ensure_stats(stats_by_condition, target)
                                subscribed_condition_ids.add(target.condition_id)
                                for asset_id in target.subscribed_asset_ids:
                                    asset_to_condition[asset_id] = target.condition_id
                                asset_to_side[target.token_id_yes] = "YES"
                                asset_to_side[target.token_id_no] = "NO"
                                for asset_id in target.subscribed_asset_ids:
                                    if asset_id not in subscribed_assets:
                                        subscribed_assets.add(asset_id)
                                        new_assets.append(asset_id)
                            if new_assets:
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "market",
                                            "operation": "subscribe",
                                            "markets": [],
                                            "assets_ids": sorted(new_assets),
                                            "asset_ids": sorted(new_assets),
                                            "initial_dump": True,
                                        }
                                    )
                                )
                                event_file.write(
                                    json.dumps(
                                        {
                                            "event": "subscribe_assets",
                                            "ts_ms": current_ms,
                                            "new_asset_count": len(new_assets),
                                            "active_target_count": len(active_targets),
                                            "condition_ids": [target.condition_id for target in active_targets],
                                        },
                                        sort_keys=True,
                                    )
                                    + "\n"
                                )

                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, monotonic_remaining))
                    except asyncio.TimeoutError:
                        continue
                    recv_ms = now_ms()
                    raw_message_count += 1
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError:
                        event_counts["json_decode_error"] += 1
                        continue
                    for msg in ws_iter(parsed):
                        event_type = str(msg.get("event_type") or msg.get("type") or msg.get("channel") or "").lower()
                        event_counts[event_type or "unknown"] += 1
                        for channel, payload, condition_id in normalizer(
                            msg,
                            allowed_events=allowed_events,
                            asset_to_condition_id=asset_to_condition,
                            asset_to_market_side=asset_to_side,
                            assemblers=assemblers,
                        ):
                            if channel != "book" or condition_id not in stats_by_condition:
                                continue
                            normalized_book_count += 1
                            stats = stats_by_condition[condition_id]
                            source_ts_ms = payload.get("source_ts_ms")
                            age: int | None = None
                            is_after_warmup = recv_ms >= warmup_end_ms
                            is_fresh_after_warmup = False
                            if isinstance(source_ts_ms, int) and source_ts_ms > 0:
                                age = max(0, recv_ms - source_ts_ms)
                                is_fresh_after_warmup = is_after_warmup and age <= args.book_max_age_ms
                            counts = scoped.top_depth_counts(payload)
                            complete = scoped.top_depth_complete(payload, args.min_top_levels)
                            is_live_current = target_is_live(stats.target, recv_ms)
                            is_decision_window = target_is_decision_window(
                                stats.target,
                                recv_ms,
                                args.min_remaining_ms,
                            )
                            if is_live_current:
                                live_current_seen_condition_ids.add(condition_id)
                            if is_decision_window and complete and is_fresh_after_warmup:
                                if not stats.target.target_role.startswith("evidence"):
                                    stats.target = replace(stats.target, target_role="evidence_current")
                                evidence_ready_condition_ids.add(condition_id)
                            if is_decision_window and complete:
                                close_decision_depth_gap(condition_id)

                            is_terminal_exit = target_is_evidence(stats) and is_live_current and not is_decision_window
                            is_evidence_depth_gap = (
                                target_is_evidence(stats)
                                and is_decision_window
                                and is_after_warmup
                                and not complete
                            )
                            should_count_stats = (target_is_evidence(stats) and not is_terminal_exit) or (
                                stats.target.target_role == "handoff_next" and not is_live_current
                            )
                            if is_evidence_depth_gap:
                                should_count_stats = False
                                evidence_top_depth_incomplete_after_ready_count += 1
                                note_decision_depth_gap(condition_id, recv_ms)
                            if should_count_stats:
                                stats.book_snapshot_count += 1
                                stats.message_count += 1
                                stats.first_recv_ts_ms = stats.first_recv_ts_ms or recv_ms
                                stats.last_recv_ts_ms = recv_ms
                                if isinstance(source_ts_ms, int) and source_ts_ms > 0 and age is not None:
                                    stats.first_source_ts_ms = stats.first_source_ts_ms or source_ts_ms
                                    stats.last_source_ts_ms = source_ts_ms
                                    stats.book_ages_ms.append(age)
                                    stats.latency_ms.append(age)
                                    stats.max_book_age_ms = (
                                        age if stats.max_book_age_ms is None else max(stats.max_book_age_ms, age)
                                    )
                                    if age > args.book_max_age_ms:
                                        stats.stale_snapshot_count += 1
                                        if is_after_warmup:
                                            stats.post_warmup_stale_snapshot_count += 1
                                        else:
                                            stats.warmup_stale_snapshot_count += 1
                                    elif is_after_warmup:
                                        stats.fresh_after_warmup_count += 1
                                        stats.first_fresh_after_warmup_recv_ts_ms = (
                                            stats.first_fresh_after_warmup_recv_ts_ms or recv_ms
                                        )
                                        stats.last_fresh_after_warmup_recv_ts_ms = recv_ms
                                stats.min_yes_bid_depth = scoped.min_seen(stats.min_yes_bid_depth, counts["yes_bid_depth"])
                                stats.min_yes_ask_depth = scoped.min_seen(stats.min_yes_ask_depth, counts["yes_ask_depth"])
                                stats.min_no_bid_depth = scoped.min_seen(stats.min_no_bid_depth, counts["no_bid_depth"])
                                stats.min_no_ask_depth = scoped.min_seen(stats.min_no_ask_depth, counts["no_ask_depth"])
                                if complete:
                                    stats.top_depth_complete_count += 1
                                    if is_fresh_after_warmup:
                                        stats.fresh_top_depth_after_warmup_count += 1
                                        scoped.collect_price_proxy(stats, payload)
                            elif is_terminal_exit:
                                terminal_exit_snapshot_count += 1
                                if not complete:
                                    terminal_exit_top_depth_incomplete_count += 1
                            elif is_live_current:
                                diagnostic_pending_depth_snapshot_count += 1
                                if not complete:
                                    diagnostic_pending_depth_incomplete_count += 1
                            event_file.write(
                                json.dumps(
                                    {
                                        "event": "book",
                                        "recv_ts_ms": recv_ms,
                                        "condition_id": condition_id,
                                        "slug": stats.target.slug,
                                        "target_role": stats.target.target_role,
                                        "source_ts_ms": source_ts_ms,
                                        "book_age_ms": age,
                                        "is_after_warmup": is_after_warmup,
                                        "fresh_after_warmup": bool(
                                            is_after_warmup and age is not None and age <= args.book_max_age_ms
                                        ),
                                        "top_depth_complete": complete,
                                        "is_decision_window": is_decision_window,
                                        "is_terminal_exit": is_terminal_exit,
                                        **counts,
                                    },
                                    sort_keys=True,
                                )
                                + "\n"
                            )
    except Exception as exc:  # noqa: BLE001
        error = repr(exc)
        seconds_to_stop = stop_at - time.monotonic()
        if seconds_to_stop <= args.terminal_close_grace_sec:
            lifecycle_terminal_close_count += 1
            stop_reason = "duration_elapsed_after_terminal_close"
        else:
            ws_disconnect_count += 1
            stop_reason = "exception"

    for condition_id in list(open_depth_gap_start_ms_by_condition):
        close_decision_depth_gap(condition_id)

    write_report(paths["report"], stats_by_condition)
    all_stats = list(stats_by_condition.values())
    evidence_stats = [stats for stats in all_stats if target_is_evidence(stats)]
    handoff_stats = [stats for stats in all_stats if not target_is_evidence(stats)]
    all_ages = [age for stats in all_stats for age in stats.book_ages_ms]
    all_latencies = [latency for stats in all_stats for latency in stats.latency_ms]

    observed_evidence = sum(1 for stats in evidence_stats if stats.book_snapshot_count > 0)
    top_depth_evidence = sum(1 for stats in evidence_stats if stats.top_depth_complete_count > 0)
    fresh_evidence = sum(1 for stats in evidence_stats if stats.fresh_after_warmup_count > 0)
    fresh_top_depth_evidence = sum(1 for stats in evidence_stats if stats.fresh_top_depth_after_warmup_count > 0)
    stale_only_round_count = sum(
        1 for stats in evidence_stats if stats.book_snapshot_count > 0 and stats.top_depth_complete_count == 0
    )
    zero_valid_snapshot_rounds = sum(1 for stats in evidence_stats if stats.book_snapshot_count == 0)
    active_condition_count_max = max(active_condition_counts) if active_condition_counts else 0
    pending_depth_never_ready_condition_count = len(live_current_seen_condition_ids - evidence_ready_condition_ids)

    threshold_failures: list[str] = []
    if not evidence_stats:
        threshold_failures.append("evidence_target_market_count_zero")
    if active_condition_count_max > args.max_active_targets:
        threshold_failures.append(f"active_condition_count_max:{active_condition_count_max}>{args.max_active_targets}")
    if ws_open_count != 1:
        threshold_failures.append(f"ws_connection_count:{ws_open_count}!=1")
    if ws_disconnect_count != 0:
        threshold_failures.append(f"ws_disconnect_count:{ws_disconnect_count}")
    if ws_reconnect_count != 0:
        threshold_failures.append(f"ws_reconnect_count:{ws_reconnect_count}")
    if observed_evidence != len(evidence_stats):
        threshold_failures.append(f"observed_evidence_target_market_count:{observed_evidence}!={len(evidence_stats)}")
    if top_depth_evidence != len(evidence_stats):
        threshold_failures.append(f"top_depth_complete_evidence_market_count:{top_depth_evidence}!={len(evidence_stats)}")
    if fresh_evidence != len(evidence_stats):
        threshold_failures.append(f"live_fresh_evidence_target_market_count:{fresh_evidence}!={len(evidence_stats)}")
    if fresh_top_depth_evidence != len(evidence_stats):
        threshold_failures.append(
            f"live_fresh_top_depth_evidence_market_count:{fresh_top_depth_evidence}!={len(evidence_stats)}"
        )
    if stale_only_round_count:
        threshold_failures.append(f"stale_only_round_count:{stale_only_round_count}")
    if zero_valid_snapshot_rounds:
        threshold_failures.append(f"zero_valid_snapshot_rounds:{zero_valid_snapshot_rounds}")
    if any(stats.post_warmup_stale_snapshot_count for stats in evidence_stats):
        threshold_failures.append("book_age_out_of_bounds")
    if decision_depth_gap_max_ms > args.max_decision_depth_gap_ms:
        threshold_failures.append(
            f"decision_depth_gap_max_ms:{decision_depth_gap_max_ms}>{args.max_decision_depth_gap_ms}"
        )
    if pending_depth_never_ready_condition_count:
        threshold_failures.append(
            f"pending_depth_never_ready_condition_count:{pending_depth_never_ready_condition_count}"
        )

    status = STATUS_KEEP if not threshold_failures else STATUS_BLOCKED
    summary = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_id": "BTC_CORE_COMPLETION_V1",
        "strategy_owner_line": "xuan_research_local",
        "scope": "single_ws_current_live_evidence_next_handoff_public_research",
        "scope_interpretation": (
            "Evidence denominator contains only markets observed while current/live. "
            "Next-round handoff targets are attribution only until their window becomes current."
        ),
        "target_csv": str(args.target_csv),
        "target_csv_sha256": scoped.sha256_file(args.target_csv),
        "loaded_target_count": len(targets),
        "subscribed_target_market_count": len(subscribed_condition_ids),
        "evidence_target_market_count": len(evidence_stats),
        "handoff_target_market_count": len(handoff_stats),
        "observed_evidence_target_market_count": observed_evidence,
        "top_depth_complete_evidence_market_count": top_depth_evidence,
        "live_fresh_evidence_target_market_count": fresh_evidence,
        "live_fresh_top_depth_evidence_market_count": fresh_top_depth_evidence,
        "active_condition_count_max": active_condition_count_max,
        "max_active_targets": args.max_active_targets,
        "lead_ms": args.lead_ms,
        "min_remaining_ms": args.min_remaining_ms,
        "next_target_count": args.next_target_count,
        "book_ws_used": True,
        "transport": "direct_public_clob_ws",
        "rest_book_used": False,
        "shared_ingress_used": False,
        "ws_connection_count": ws_open_count,
        "ws_disconnect_count": ws_disconnect_count,
        "ws_reconnect_count": ws_reconnect_count,
        "lifecycle_terminal_close_count": lifecycle_terminal_close_count,
        "raw_message_count": raw_message_count,
        "normalized_book_count": normalized_book_count,
        "diagnostic_pending_depth_snapshot_count": diagnostic_pending_depth_snapshot_count,
        "diagnostic_pending_depth_incomplete_count": diagnostic_pending_depth_incomplete_count,
        "evidence_top_depth_incomplete_after_ready_count": evidence_top_depth_incomplete_after_ready_count,
        "decision_depth_gap_snapshot_count": evidence_top_depth_incomplete_after_ready_count,
        "decision_depth_gap_burst_count": decision_depth_gap_burst_count,
        "decision_depth_gap_max_ms": decision_depth_gap_max_ms,
        "max_decision_depth_gap_ms": args.max_decision_depth_gap_ms,
        "terminal_exit_snapshot_count": terminal_exit_snapshot_count,
        "terminal_exit_top_depth_incomplete_count": terminal_exit_top_depth_incomplete_count,
        "live_current_seen_condition_count": len(live_current_seen_condition_ids),
        "evidence_ready_condition_count": len(evidence_ready_condition_ids),
        "pending_depth_never_ready_condition_count": pending_depth_never_ready_condition_count,
        "stop_reason": stop_reason,
        "error": error,
        "event_counts": dict(event_counts),
        "warmup_sec": args.warmup_sec,
        "book_max_age_ms": args.book_max_age_ms,
        "latency_p50_ms": percentile(all_latencies, 0.50),
        "latency_p95_ms": percentile(all_latencies, 0.95),
        "latency_max_ms": max(all_latencies) if all_latencies else None,
        "book_age_p50_ms": percentile(all_ages, 0.50),
        "book_age_p95_ms": percentile(all_ages, 0.95),
        "book_age_max_ms": max(all_ages) if all_ages else None,
        "recovered_round_count": 0,
        "stale_only_round_count": stale_only_round_count,
        "zero_valid_snapshot_rounds": zero_valid_snapshot_rounds,
        "observer_nonzero_rounds": 1 if threshold_failures else 0,
        "safety_counters": {
            "private_key_loaded": 0,
            "candidate_import_calls": 0,
            "orders_sent": 0,
            "cancels_sent": 0,
            "redeems_sent": 0,
            "live_orders_allowed": 0,
            "latest_pointer_updates": 0,
        },
        "readiness": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "non_claims": non_claims(),
        "threshold_failure_count": len(threshold_failures),
        "threshold_failures": threshold_failures,
    }
    write_json(paths["audit"], summary)
    write_json(paths["gate"], summary)
    write_json(
        paths["eval"],
        {
            **summary,
            "ok": not threshold_failures,
            "highest_allowed_status": STATUS_KEEP,
            "full_215_oos_pass": False,
            "full_288_market_oos_pass": False,
        },
    )
    print(json.dumps({"status": status, "output_dir": str(args.output_dir)}, indent=2))
    return 0 if not threshold_failures else 2


def write_fail_closed(
    paths: dict[str, Path],
    args: argparse.Namespace,
    targets: list[scoped.TargetMarket],
    errors: list[str],
    reason: str,
) -> None:
    stats = {
        target.condition_id: scoped.MarketStats(target=target, ws_chunk_id=0)
        for target in targets
    }
    write_report(paths["report"], stats)
    common = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": STATUS_BLOCKED,
        "status_reason": reason,
        "errors": errors,
        "target_csv": str(args.target_csv),
        "book_ws_used": False,
        "transport": "direct_public_clob_ws_not_started",
        "rest_book_used": False,
        "shared_ingress_used": False,
        "non_claims": non_claims(),
        "readiness": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "threshold_failure_count": len(errors),
        "threshold_failures": errors,
    }
    write_json(paths["audit"], common)
    write_json(paths["gate"], common)
    write_json(paths["eval"], {**common, "ok": False, "highest_allowed_status": STATUS_KEEP})
    paths["events"].write_text("", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-csv", type=Path, required=True)
    parser.add_argument("--expected-target-csv-sha256", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=float, default=1800.0)
    parser.add_argument("--warmup-sec", type=float, default=20.0)
    parser.add_argument("--selection-poll-ms", type=int, default=2000)
    parser.add_argument("--lead-ms", type=int, default=300_000)
    parser.add_argument("--min-remaining-ms", type=int, default=90_000)
    parser.add_argument("--next-target-count", type=int, default=1)
    parser.add_argument("--max-active-targets", type=int, default=2)
    parser.add_argument("--book-max-age-ms", type=int, default=60_000)
    parser.add_argument("--min-top-levels", type=int, default=1)
    parser.add_argument(
        "--max-decision-depth-gap-ms",
        type=int,
        default=2_000,
        help=(
            "Maximum tolerated continuous top-depth gap inside the decision window. "
            "Gap snapshots are excluded from evidence samples and audited separately."
        ),
    )
    parser.add_argument("--max-ws-connections", type=int, default=1)
    parser.add_argument(
        "--terminal-close-grace-sec",
        type=float,
        default=15.0,
        help="Classify a server close inside this final window as lifecycle terminal close, not mid-run disconnect.",
    )
    parser.add_argument("--ws-url", default=scoped.WS_URL)
    return parser.parse_args()


def main() -> int:
    return asyncio.run(run_single_ws(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

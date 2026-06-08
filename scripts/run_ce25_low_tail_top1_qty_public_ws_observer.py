#!/usr/bin/env python3
"""CE25 low-tail top1-qty public/no-order observer gate.

This runtime turns the reviewed historical CE25 low-tail top1-qty shape into a
live/public evidence gate. It is intentionally narrow:
- current/live BTC 5m market only;
- final-60s evidence only;
- exactly one direct public CLOB market WebSocket in live mode;
- no shared-ingress, no REST book evidence, no private keys, no imports, and no
  orders/cancels/redeems/live/deploy/latest-pointer paths.

The script also includes a deterministic offline self-test mode. Self-tests do
not open a WebSocket and exist so review packets can bind runtime behavior
before any separately approved public/no-order smoke run.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import math
import sys
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_btc_core_scoped_public_ws_no_order_observer as scoped  # noqa: E402


STATUS_KEEP = "KEEP_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_EVIDENCE_REVIEW_REQUIRED_NOT_OOS_READY"
STATUS_BLOCKED = "BLOCKED_CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_FAIL_CLOSED"
STRATEGY_OWNER_LINE = "CE25_LOW_TAIL_RESEARCH"
STRATEGY_ID = "CE25_BTC5M_LOW_PRICE_TAIL_SIDE_SPLIT_TOP1_QTY_V2"
OUTPUT_FILES = {
    "events": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_EVENTS.jsonl",
    "opportunities": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_OPPORTUNITIES.csv",
    "gate": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_GATE_SUMMARY.json",
    "audit": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_AUDIT_MANIFEST.json",
    "eval": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_EVAL.json",
    "hash_manifest": "CE25_LOW_TAIL_TOP1_QTY_PUBLIC_OBSERVER_HASH_MANIFEST.json",
}
OPPORTUNITY_FIELDS = [
    "strategy_owner_line",
    "strategy_id",
    "candidate_id",
    "observed_ts_ms",
    "market_id",
    "condition_id",
    "slug",
    "window_start_ts_ms",
    "window_end_ts_ms",
    "remaining_ms",
    "side_split",
    "target_qty",
    "strict_mode_family",
    "historical_strict_modes_covered",
    "yes_top1_price",
    "yes_top1_qty",
    "no_top1_price",
    "no_top1_qty",
    "executable_side",
    "executable_side_price",
    "executable_top1_qty",
    "opposite_side",
    "opposite_top1_qty",
    "pair_cost",
    "paircap",
    "paircap_pass",
    "top1_qty_gate_pass",
    "official_fee_rate",
    "official_fee_yes",
    "official_fee_no",
    "official_fee_total",
    "gross_pair_cost",
    "official_total_cost",
    "official_cash_pnl",
    "official_roi",
    "official_fee_profit_positive",
    "orders_sent",
    "private_key_loaded",
    "live_orders_allowed",
]


@dataclass(slots=True)
class ObserverStats:
    loaded_target_count: int = 0
    subscribed_target_market_count: int = 0
    current_live_seen_condition_count: int = 0
    final60_seen_condition_count: int = 0
    raw_message_count: int = 0
    normalized_book_count: int = 0
    book_snapshot_count: int = 0
    final60_book_snapshot_count: int = 0
    final60_valid_snapshot_count: int = 0
    final60_top_depth_incomplete_count: int = 0
    final60_stale_snapshot_count: int = 0
    final60_missing_source_ts_count: int = 0
    outside_final60_snapshot_count: int = 0
    opportunity_count: int = 0
    ws_open_count: int = 0
    ws_disconnect_count: int = 0
    ws_reconnect_count: int = 0
    lifecycle_terminal_close_count: int = 0
    decision_depth_gap_burst_count: int = 0
    decision_depth_gap_max_ms: int = 0
    stop_reason: str = "not_started"
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def now_ms() -> int:
    return int(time.time() * 1000)


def iso_ms(ts_ms: int | None) -> str | None:
    if ts_ms is None:
        return None
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def output_paths(output_dir: Path) -> dict[str, Path]:
    return {key: output_dir / filename for key, filename in OUTPUT_FILES.items()}


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "orders_authorized": False,
        "cancels_authorized": False,
        "redeems_authorized": False,
        "candidate_import_authorized": False,
        "private_key_loaded": False,
        "latest_pointer_update_authorized": False,
        "oos_pass_claimed": False,
    }


def safety_counters() -> dict[str, int]:
    return {
        "private_key_loaded": 0,
        "candidate_import_calls": 0,
        "orders_sent": 0,
        "cancels_sent": 0,
        "redeems_sent": 0,
        "live_orders_allowed": 0,
        "latest_pointer_updates": 0,
        "shared_ingress_reads": 0,
        "rest_book_reads": 0,
    }


def write_opportunities(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=OPPORTUNITY_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def write_hash_manifest(output_dir: Path, paths: dict[str, Path], summary: dict[str, Any]) -> None:
    artifacts = [
        paths["events"],
        paths["opportunities"],
        paths["gate"],
        paths["audit"],
        paths["eval"],
    ]
    manifest = {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": summary["status"],
        "strategy_owner_line": STRATEGY_OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "artifact_count": len(artifacts),
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "non_claims": non_claims(),
    }
    write_json(paths["hash_manifest"], manifest)


def pct_int(values: list[int], q: float) -> int | None:
    return scoped.pct(values, q)


def pct_float(values: list[float], q: float, *, digits: int = 6) -> float | None:
    return scoped.pct_float(values, q, digits=digits)


def parse_float(raw: Any) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return value


def top_ask(book: dict[str, Any], side: str) -> tuple[float | None, float | None]:
    key = "yes" if side == "YES" else "no"
    raw_l2 = book.get("raw_l2") or {}
    side_book = raw_l2.get(key) or {}
    asks = side_book.get("asks") or []
    if asks:
        level = asks[0]
        if isinstance(level, dict):
            return parse_float(level.get("price")), parse_float(level.get("size"))
        if isinstance(level, (list, tuple)):
            return parse_float(level[0] if len(level) > 0 else None), parse_float(level[1] if len(level) > 1 else None)
    px_key = "yes_ask_px" if side == "YES" else "no_ask_px"
    sz_key = "yes_ask_sz" if side == "YES" else "no_ask_sz"
    return parse_float(book.get(px_key)), parse_float(book.get(sz_key))


def official_buy_fee(qty: float, price: float, fee_rate: float) -> float:
    return qty * fee_rate * price * (1.0 - price)


def candidate_id_for(target: scoped.TargetMarket, recv_ts_ms: int, side: str, qty: float) -> str:
    raw = "|".join([STRATEGY_ID, target.condition_id, str(recv_ts_ms), side, f"{qty:g}"])
    return "ce25_live_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def target_is_btc_5m(target: scoped.TargetMarket) -> bool:
    slug = target.slug.lower()
    return "btc" in slug and "5m" in slug


def target_is_current_live(target: scoped.TargetMarket, at_ms: int) -> bool:
    return target.window_start_ts_ms <= at_ms < target.window_end_ts_ms


def remaining_ms(target: scoped.TargetMarket, at_ms: int) -> int:
    return target.window_end_ts_ms - at_ms


def target_is_subscribe_eligible(target: scoped.TargetMarket, at_ms: int, *, final_window_ms: int, subscribe_lead_ms: int) -> bool:
    if not target_is_btc_5m(target) or not target_is_current_live(target, at_ms):
        return False
    rem = remaining_ms(target, at_ms)
    return 0 < rem <= final_window_ms + subscribe_lead_ms


def target_is_final_window(target: scoped.TargetMarket, at_ms: int, *, final_window_ms: int, min_remaining_ms: int) -> bool:
    if not target_is_btc_5m(target) or not target_is_current_live(target, at_ms):
        return False
    rem = remaining_ms(target, at_ms)
    return min_remaining_ms <= rem <= final_window_ms


def sorted_targets(targets: Iterable[scoped.TargetMarket]) -> list[scoped.TargetMarket]:
    return sorted(targets, key=lambda item: (item.window_start_ts_ms, item.condition_id))


def select_subscribe_targets(
    targets: list[scoped.TargetMarket],
    *,
    at_ms: int,
    final_window_ms: int,
    subscribe_lead_ms: int,
) -> list[scoped.TargetMarket]:
    eligible = [
        target
        for target in targets
        if target_is_subscribe_eligible(
            target,
            at_ms,
            final_window_ms=final_window_ms,
            subscribe_lead_ms=subscribe_lead_ms,
        )
    ]
    return eligible[:1]


def evaluate_opportunities(
    *,
    target: scoped.TargetMarket,
    book: dict[str, Any],
    recv_ts_ms: int,
    final_window_ms: int,
    min_remaining_ms: int,
    min_top_levels: int,
    price_lo: float,
    price_hi: float,
    paircap: float,
    target_qty_values: list[float],
    fee_rate: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rem = remaining_ms(target, recv_ts_ms)
    counts = scoped.top_depth_counts(book)
    depth_complete = scoped.top_depth_complete(book, min_top_levels)
    final_window = target_is_final_window(
        target,
        recv_ts_ms,
        final_window_ms=final_window_ms,
        min_remaining_ms=min_remaining_ms,
    )
    yes_px, yes_qty = top_ask(book, "YES")
    no_px, no_qty = top_ask(book, "NO")
    rows: list[dict[str, Any]] = []
    audit = {
        "condition_id": target.condition_id,
        "slug": target.slug,
        "recv_ts_ms": recv_ts_ms,
        "remaining_ms": rem,
        "final_window": final_window,
        "top_depth_complete": depth_complete,
        "yes_top1_price": yes_px,
        "yes_top1_qty": yes_qty,
        "no_top1_price": no_px,
        "no_top1_qty": no_qty,
        **counts,
    }
    if not final_window or not depth_complete:
        return rows, audit
    if yes_px is None or no_px is None or yes_qty is None or no_qty is None:
        return rows, audit
    if min(yes_px, no_px, yes_qty, no_qty) <= 0.0:
        return rows, audit

    pair_cost = yes_px + no_px
    side_specs = [
        ("UP", "YES", yes_px, yes_qty, "NO", no_qty),
        ("DOWN", "NO", no_px, no_qty, "YES", yes_qty),
    ]
    for side_split, executable_side, executable_price, executable_qty, opposite_side, opposite_qty in side_specs:
        executable_price_pass = price_lo <= executable_price <= price_hi
        paircap_pass = pair_cost <= paircap
        for qty in target_qty_values:
            top1_qty_pass = executable_qty >= qty and opposite_qty >= qty
            fee_yes = official_buy_fee(qty, yes_px, fee_rate)
            fee_no = official_buy_fee(qty, no_px, fee_rate)
            fee_total = fee_yes + fee_no
            gross_cost = qty * pair_cost
            total_cost = gross_cost + fee_total
            cash_pnl = qty - total_cost
            roi = cash_pnl / total_cost if total_cost > 0 else None
            profit_positive = cash_pnl > 0
            if not (executable_price_pass and paircap_pass and top1_qty_pass and profit_positive):
                continue
            rows.append(
                {
                    "strategy_owner_line": STRATEGY_OWNER_LINE,
                    "strategy_id": STRATEGY_ID,
                    "candidate_id": candidate_id_for(target, recv_ts_ms, side_split, qty),
                    "observed_ts_ms": recv_ts_ms,
                    "market_id": target.market_id,
                    "condition_id": target.condition_id,
                    "slug": target.slug,
                    "window_start_ts_ms": target.window_start_ts_ms,
                    "window_end_ts_ms": target.window_end_ts_ms,
                    "remaining_ms": rem,
                    "side_split": side_split,
                    "target_qty": f"{qty:g}",
                    "strict_mode_family": "live_same_snapshot",
                    "historical_strict_modes_covered": "same_row,entry_paircap",
                    "yes_top1_price": round(yes_px, 6),
                    "yes_top1_qty": round(yes_qty, 6),
                    "no_top1_price": round(no_px, 6),
                    "no_top1_qty": round(no_qty, 6),
                    "executable_side": executable_side,
                    "executable_side_price": round(executable_price, 6),
                    "executable_top1_qty": round(executable_qty, 6),
                    "opposite_side": opposite_side,
                    "opposite_top1_qty": round(opposite_qty, 6),
                    "pair_cost": round(pair_cost, 6),
                    "paircap": round(paircap, 6),
                    "paircap_pass": str(paircap_pass).lower(),
                    "top1_qty_gate_pass": str(top1_qty_pass).lower(),
                    "official_fee_rate": round(fee_rate, 6),
                    "official_fee_yes": round(fee_yes, 6),
                    "official_fee_no": round(fee_no, 6),
                    "official_fee_total": round(fee_total, 6),
                    "gross_pair_cost": round(gross_cost, 6),
                    "official_total_cost": round(total_cost, 6),
                    "official_cash_pnl": round(cash_pnl, 6),
                    "official_roi": round(roi, 6) if roi is not None else "",
                    "official_fee_profit_positive": str(profit_positive).lower(),
                    "orders_sent": 0,
                    "private_key_loaded": "false",
                    "live_orders_allowed": "false",
                }
            )
    return rows, audit


def synthetic_target() -> scoped.TargetMarket:
    return scoped.TargetMarket(
        projection_round_index=0,
        slug="btc-updown-5m-selftest",
        market_id="0xselftest",
        condition_id="0xselftest",
        token_id_yes="yes-token",
        token_id_no="no-token",
        subscribed_asset_ids=("yes-token", "no-token"),
        window_start_ts_ms=0,
        window_end_ts_ms=300_000,
        target_role="evidence_current",
    )


def synthetic_books(mode: str) -> list[dict[str, Any]]:
    if mode == "valid":
        return [
            {
                "recv_ts_ms": 245_000,
                "source_ts_ms": 244_900,
                "book": {
                    "yes_bid_px": 0.30,
                    "yes_ask_px": 0.31,
                    "no_bid_px": 0.61,
                    "no_ask_px": 0.62,
                    "yes_bid_sz": 10.0,
                    "yes_ask_sz": 8.0,
                    "no_bid_sz": 10.0,
                    "no_ask_sz": 8.0,
                    "raw_l2": {
                        "yes": {"bids": [{"price": 0.30, "size": 10.0}], "asks": [{"price": 0.31, "size": 8.0}]},
                        "no": {"bids": [{"price": 0.61, "size": 10.0}], "asks": [{"price": 0.62, "size": 8.0}]},
                    },
                },
            },
            {
                "recv_ts_ms": 252_000,
                "source_ts_ms": 251_900,
                "book": {
                    "yes_bid_px": 0.61,
                    "yes_ask_px": 0.62,
                    "no_bid_px": 0.32,
                    "no_ask_px": 0.33,
                    "yes_bid_sz": 9.0,
                    "yes_ask_sz": 8.0,
                    "no_bid_sz": 9.0,
                    "no_ask_sz": 8.0,
                    "raw_l2": {
                        "yes": {"bids": [{"price": 0.61, "size": 9.0}], "asks": [{"price": 0.62, "size": 8.0}]},
                        "no": {"bids": [{"price": 0.32, "size": 9.0}], "asks": [{"price": 0.33, "size": 8.0}]},
                    },
                },
            },
        ]
    if mode == "invalid":
        return [
            {
                "recv_ts_ms": 245_000,
                "source_ts_ms": 100_000,
                "book": {
                    "yes_bid_px": 0.30,
                    "yes_ask_px": 0.31,
                    "no_bid_px": 0.61,
                    "no_ask_px": 0.62,
                    "yes_bid_sz": 10.0,
                    "yes_ask_sz": 8.0,
                    "no_bid_sz": 10.0,
                    "no_ask_sz": 0.0,
                    "raw_l2": {
                        "yes": {"bids": [{"price": 0.30, "size": 10.0}], "asks": [{"price": 0.31, "size": 8.0}]},
                        "no": {"bids": [{"price": 0.61, "size": 10.0}], "asks": []},
                    },
                },
            }
        ]
    raise ValueError(f"unknown self-test mode: {mode}")


def ensure_fresh_output_dir(output_dir: Path) -> list[str]:
    if output_dir.exists() and any(output_dir.iterdir()):
        return [f"output_dir_exists:{output_dir}"]
    output_dir.mkdir(parents=True, exist_ok=True)
    return []


def common_summary(
    *,
    args: argparse.Namespace,
    status: str,
    stats: ObserverStats,
    threshold_failures: list[str],
    opportunity_rows: list[dict[str, Any]],
    mode: str,
    target_csv_sha256: str | None,
    loaded_targets: list[scoped.TargetMarket],
    book_ages_ms: list[int],
    event_counts: Counter[str],
) -> dict[str, Any]:
    side_counts = Counter(row["side_split"] for row in opportunity_rows)
    qty_counts = Counter(row["target_qty"] for row in opportunity_rows)
    pnl_values = [float(row["official_cash_pnl"]) for row in opportunity_rows]
    roi_values = [float(row["official_roi"]) for row in opportunity_rows if row["official_roi"] != ""]
    return {
        "schema_version": 1,
        "created_at": utc_now(),
        "status": status,
        "strategy_owner_line": STRATEGY_OWNER_LINE,
        "strategy_id": STRATEGY_ID,
        "mode": mode,
        "scope": "current_live_btc_5m_final60_public_no_order_observer_gate",
        "scope_interpretation": (
            "This is public/no-order evidence only. It tests whether the reviewed CE25 low-tail "
            "side-split top1-qty gate appears in final-60s public CLOB data; it is not OOS-ready, "
            "not private truth, and not live/promotion/deploy evidence."
        ),
        "target_csv": str(args.target_csv) if args.target_csv else None,
        "target_csv_sha256": target_csv_sha256,
        "expected_target_csv_sha256": args.expected_target_csv_sha256,
        "loaded_target_count": len(loaded_targets),
        "loaded_btc5m_target_count": sum(1 for target in loaded_targets if target_is_btc_5m(target)),
        "final_window_ms": args.final_window_sec * 1000,
        "min_remaining_ms": args.min_final_remaining_ms,
        "subscribe_lead_ms": args.subscribe_lead_ms,
        "price_range": [args.price_lo, args.price_hi],
        "paircap": args.paircap,
        "target_qty_values": args.target_qty_values,
        "fee_rate": args.fee_rate,
        "official_fee_formula": "fee = qty * fee_rate * price * (1 - price)",
        "transport": "direct_public_clob_ws" if mode == "live" else "offline_self_test_no_ws",
        "book_ws_used": mode == "live",
        "rest_book_used": False,
        "shared_ingress_used": False,
        "ws_connection_count": stats.ws_open_count,
        "ws_disconnect_count": stats.ws_disconnect_count,
        "ws_reconnect_count": stats.ws_reconnect_count,
        "lifecycle_terminal_close_count": stats.lifecycle_terminal_close_count,
        "loaded_target_count_runtime": stats.loaded_target_count,
        "subscribed_target_market_count": stats.subscribed_target_market_count,
        "current_live_seen_condition_count": stats.current_live_seen_condition_count,
        "final60_seen_condition_count": stats.final60_seen_condition_count,
        "raw_message_count": stats.raw_message_count,
        "normalized_book_count": stats.normalized_book_count,
        "book_snapshot_count": stats.book_snapshot_count,
        "final60_book_snapshot_count": stats.final60_book_snapshot_count,
        "final60_valid_snapshot_count": stats.final60_valid_snapshot_count,
        "final60_top_depth_incomplete_count": stats.final60_top_depth_incomplete_count,
        "final60_stale_snapshot_count": stats.final60_stale_snapshot_count,
        "final60_missing_source_ts_count": stats.final60_missing_source_ts_count,
        "outside_final60_snapshot_count": stats.outside_final60_snapshot_count,
        "decision_depth_gap_burst_count": stats.decision_depth_gap_burst_count,
        "decision_depth_gap_max_ms": stats.decision_depth_gap_max_ms,
        "max_decision_depth_gap_ms": args.max_decision_depth_gap_ms,
        "opportunity_count": len(opportunity_rows),
        "side_opportunity_counts": dict(side_counts),
        "target_qty_opportunity_counts": dict(qty_counts),
        "official_cash_pnl_sum": round(sum(pnl_values), 6) if pnl_values else 0.0,
        "official_roi_p50": pct_float(roi_values, 0.50) if roi_values else None,
        "book_age_p50_ms": pct_int(book_ages_ms, 0.50),
        "book_age_p95_ms": pct_int(book_ages_ms, 0.95),
        "book_age_max_ms": max(book_ages_ms) if book_ages_ms else None,
        "book_max_age_ms": args.book_max_age_ms,
        "event_counts": dict(event_counts),
        "stop_reason": stats.stop_reason,
        "error": stats.error,
        "threshold_failure_count": len(threshold_failures),
        "threshold_failures": threshold_failures,
        "safety_counters": safety_counters(),
        "readiness": {
            "private_truth_ready": False,
            "strategy_promotion_ready": False,
            "live_ready": False,
            "deployable": False,
        },
        "non_claims": non_claims(),
        "highest_allowed_status": STATUS_KEEP,
    }


def finalize_outputs(
    *,
    args: argparse.Namespace,
    paths: dict[str, Path],
    stats: ObserverStats,
    threshold_failures: list[str],
    opportunity_rows: list[dict[str, Any]],
    mode: str,
    target_csv_sha256: str | None,
    loaded_targets: list[scoped.TargetMarket],
    book_ages_ms: list[int],
    event_counts: Counter[str],
) -> int:
    status = STATUS_KEEP if not threshold_failures else STATUS_BLOCKED
    write_opportunities(paths["opportunities"], opportunity_rows)
    summary = common_summary(
        args=args,
        status=status,
        stats=stats,
        threshold_failures=threshold_failures,
        opportunity_rows=opportunity_rows,
        mode=mode,
        target_csv_sha256=target_csv_sha256,
        loaded_targets=loaded_targets,
        book_ages_ms=book_ages_ms,
        event_counts=event_counts,
    )
    write_json(paths["gate"], summary)
    write_json(paths["audit"], {**summary, "artifact_role": "audit_manifest"})
    write_json(paths["eval"], {**summary, "ok": not threshold_failures, "oos_ready": False})
    write_hash_manifest(args.output_dir, paths, summary)
    print(json.dumps({"status": status, "ok": not threshold_failures, "output_dir": str(args.output_dir)}, indent=2))
    return 0 if not threshold_failures else 2


def preflight(args: argparse.Namespace) -> tuple[list[str], list[scoped.TargetMarket], str | None]:
    errors: list[str] = []
    targets: list[scoped.TargetMarket] = []
    target_csv_sha256: str | None = None
    if args.max_ws_connections != 1:
        errors.append(f"max_ws_connections:{args.max_ws_connections}!=1")
    if not args.no_rest_book:
        errors.append("no_rest_book_flag_required")
    if not args.no_shared_ingress:
        errors.append("no_shared_ingress_flag_required")
    if args.price_lo <= 0 or args.price_hi <= 0 or args.price_lo > args.price_hi:
        errors.append("invalid_price_range")
    if args.paircap <= 0 or args.paircap >= 1.0:
        errors.append("invalid_paircap")
    if args.fee_rate < 0 or args.fee_rate > 1:
        errors.append("invalid_fee_rate")
    if any(qty <= 0 for qty in args.target_qty_values):
        errors.append("invalid_target_qty_values")
    if args.final_window_sec <= 0:
        errors.append("final_window_sec_nonpositive")
    if args.min_final_remaining_ms < 0:
        errors.append("min_final_remaining_ms_negative")
    if args.book_max_age_ms <= 0:
        errors.append("book_max_age_ms_nonpositive")
    if args.self_test:
        return errors, [synthetic_target()], None
    if args.target_csv is None:
        errors.append("target_csv_required_for_live_mode")
        return errors, targets, target_csv_sha256
    try:
        target_csv_sha256 = sha256_file(args.target_csv)
        if args.expected_target_csv_sha256 and target_csv_sha256 != args.expected_target_csv_sha256:
            errors.append("target_csv_hash_mismatch")
        targets = sorted_targets(scoped.load_targets(args.target_csv))
        if args.expected_target_count is not None and len(targets) != args.expected_target_count:
            errors.append(f"target_count_mismatch:{len(targets)}!={args.expected_target_count}")
        if not any(target_is_btc_5m(target) for target in targets):
            errors.append("btc_5m_target_count_zero")
    except Exception as exc:  # noqa: BLE001
        errors.append(f"target_load_error:{exc}")
    return errors, targets, target_csv_sha256


def threshold_failures_for(
    *,
    args: argparse.Namespace,
    stats: ObserverStats,
    mode: str,
    opportunity_rows: list[dict[str, Any]],
    preflight_errors: list[str],
) -> list[str]:
    failures = list(preflight_errors)
    if mode == "live":
        if stats.ws_open_count != 1:
            failures.append(f"ws_connection_count:{stats.ws_open_count}!=1")
    if stats.ws_disconnect_count:
        failures.append(f"ws_disconnect_count:{stats.ws_disconnect_count}")
    if stats.ws_reconnect_count:
        failures.append(f"ws_reconnect_count:{stats.ws_reconnect_count}")
    if stats.final60_valid_snapshot_count < args.min_final60_valid_snapshots:
        failures.append(
            f"final60_valid_snapshot_count:{stats.final60_valid_snapshot_count}<{args.min_final60_valid_snapshots}"
        )
    if stats.final60_stale_snapshot_count:
        failures.append(f"final60_stale_snapshot_count:{stats.final60_stale_snapshot_count}")
    if stats.final60_missing_source_ts_count:
        failures.append(f"final60_missing_source_ts_count:{stats.final60_missing_source_ts_count}")
    if stats.decision_depth_gap_max_ms > args.max_decision_depth_gap_ms:
        failures.append(f"decision_depth_gap_max_ms:{stats.decision_depth_gap_max_ms}>{args.max_decision_depth_gap_ms}")
    if args.require_opportunity and not opportunity_rows:
        failures.append("opportunity_count_zero")
    nonzero_safety = [key for key, value in safety_counters().items() if value]
    if nonzero_safety:
        failures.append(f"safety_counters_nonzero:{','.join(nonzero_safety)}")
    if any(non_claims().values()):
        failures.append("readiness_or_authorization_flag_true")
    return failures


def handle_book_sample(
    *,
    args: argparse.Namespace,
    paths: dict[str, Path],
    stats: ObserverStats,
    target: scoped.TargetMarket,
    recv_ts_ms: int,
    source_ts_ms: int | None,
    book: dict[str, Any],
    event_file: Any,
    opportunity_rows: list[dict[str, Any]],
    book_ages_ms: list[int],
    open_depth_gap_start_ms_by_condition: dict[str, int],
    open_depth_gap_last_ms_by_condition: dict[str, int],
) -> None:
    stats.book_snapshot_count += 1
    if target_is_current_live(target, recv_ts_ms):
        stats.current_live_seen_condition_count = max(stats.current_live_seen_condition_count, 1)
    final_window = target_is_final_window(
        target,
        recv_ts_ms,
        final_window_ms=args.final_window_sec * 1000,
        min_remaining_ms=args.min_final_remaining_ms,
    )
    if not final_window:
        stats.outside_final60_snapshot_count += 1
        return
    stats.final60_seen_condition_count = max(stats.final60_seen_condition_count, 1)
    stats.final60_book_snapshot_count += 1
    age: int | None = None
    if source_ts_ms is None:
        stats.final60_missing_source_ts_count += 1
    else:
        age = max(0, recv_ts_ms - source_ts_ms)
        book_ages_ms.append(age)
        if age > args.book_max_age_ms:
            stats.final60_stale_snapshot_count += 1
    complete = scoped.top_depth_complete(book, args.min_top_levels)
    if not complete:
        stats.final60_top_depth_incomplete_count += 1
        if target.condition_id not in open_depth_gap_start_ms_by_condition:
            open_depth_gap_start_ms_by_condition[target.condition_id] = recv_ts_ms
            stats.decision_depth_gap_burst_count += 1
        open_depth_gap_last_ms_by_condition[target.condition_id] = recv_ts_ms
    else:
        start_ms = open_depth_gap_start_ms_by_condition.pop(target.condition_id, None)
        last_ms = open_depth_gap_last_ms_by_condition.pop(target.condition_id, None)
        if start_ms is not None and last_ms is not None:
            stats.decision_depth_gap_max_ms = max(stats.decision_depth_gap_max_ms, max(0, last_ms - start_ms))
    if complete and age is not None and age <= args.book_max_age_ms:
        stats.final60_valid_snapshot_count += 1
        rows, audit = evaluate_opportunities(
            target=target,
            book=book,
            recv_ts_ms=recv_ts_ms,
            final_window_ms=args.final_window_sec * 1000,
            min_remaining_ms=args.min_final_remaining_ms,
            min_top_levels=args.min_top_levels,
            price_lo=args.price_lo,
            price_hi=args.price_hi,
            paircap=args.paircap,
            target_qty_values=args.target_qty_values,
            fee_rate=args.fee_rate,
        )
        opportunity_rows.extend(rows)
        stats.opportunity_count += len(rows)
        event_file.write(
            json.dumps(
                {
                    "event": "final60_book",
                    "source_ts_ms": source_ts_ms,
                    "book_age_ms": age,
                    "opportunity_count": len(rows),
                    **audit,
                },
                sort_keys=True,
            )
            + "\n"
        )
    else:
        event_file.write(
            json.dumps(
                {
                    "event": "final60_book_rejected",
                    "recv_ts_ms": recv_ts_ms,
                    "source_ts_ms": source_ts_ms,
                    "book_age_ms": age,
                    "condition_id": target.condition_id,
                    "slug": target.slug,
                    "remaining_ms": remaining_ms(target, recv_ts_ms),
                    "top_depth_complete": complete,
                    "reject_reason": "STALE_OR_INCOMPLETE_OR_MISSING_SOURCE_TS",
                    **scoped.top_depth_counts(book),
                },
                sort_keys=True,
            )
            + "\n"
        )


def run_self_test(args: argparse.Namespace, paths: dict[str, Path], targets: list[scoped.TargetMarket]) -> int:
    stats = ObserverStats(loaded_target_count=len(targets), stop_reason=f"self_test_{args.self_test}_complete")
    opportunity_rows: list[dict[str, Any]] = []
    book_ages_ms: list[int] = []
    event_counts: Counter[str] = Counter()
    open_depth_gap_start_ms_by_condition: dict[str, int] = {}
    open_depth_gap_last_ms_by_condition: dict[str, int] = {}
    paths["events"].write_text("", encoding="utf-8")
    target = targets[0]
    with paths["events"].open("a", encoding="utf-8") as event_file:
        for sample in synthetic_books(args.self_test):
            event_counts["synthetic_book"] += 1
            stats.normalized_book_count += 1
            handle_book_sample(
                args=args,
                paths=paths,
                stats=stats,
                target=target,
                recv_ts_ms=int(sample["recv_ts_ms"]),
                source_ts_ms=int(sample["source_ts_ms"]) if sample.get("source_ts_ms") is not None else None,
                book=sample["book"],
                event_file=event_file,
                opportunity_rows=opportunity_rows,
                book_ages_ms=book_ages_ms,
                open_depth_gap_start_ms_by_condition=open_depth_gap_start_ms_by_condition,
                open_depth_gap_last_ms_by_condition=open_depth_gap_last_ms_by_condition,
            )
    for condition_id in list(open_depth_gap_start_ms_by_condition):
        start_ms = open_depth_gap_start_ms_by_condition[condition_id]
        last_ms = open_depth_gap_last_ms_by_condition.get(condition_id, start_ms)
        stats.decision_depth_gap_max_ms = max(stats.decision_depth_gap_max_ms, max(0, last_ms - start_ms))
    preflight_errors: list[str] = []
    threshold_failures = threshold_failures_for(
        args=args,
        stats=stats,
        mode="self_test",
        opportunity_rows=opportunity_rows,
        preflight_errors=preflight_errors,
    )
    return finalize_outputs(
        args=args,
        paths=paths,
        stats=stats,
        threshold_failures=threshold_failures,
        opportunity_rows=opportunity_rows,
        mode="self_test",
        target_csv_sha256=None,
        loaded_targets=targets,
        book_ages_ms=book_ages_ms,
        event_counts=event_counts,
    )


async def run_live(args: argparse.Namespace, paths: dict[str, Path], targets: list[scoped.TargetMarket], target_csv_sha256: str | None) -> int:
    import websockets

    ws_iter = scoped.fallback_iter_ws_objects
    normalizer = scoped.fallback_normalize_market_ws_message
    try:
        from completion_first_data.capture.websocket_sidecar import _iter_ws_objects, normalize_market_ws_message

        ws_iter = _iter_ws_objects
        normalizer = normalize_market_ws_message
    except Exception:  # noqa: BLE001
        pass

    stats = ObserverStats(loaded_target_count=len(targets))
    opportunity_rows: list[dict[str, Any]] = []
    book_ages_ms: list[int] = []
    event_counts: Counter[str] = Counter()
    subscribed_assets: set[str] = set()
    subscribed_condition_ids: set[str] = set()
    target_by_condition: dict[str, scoped.TargetMarket] = {}
    asset_to_condition: dict[str, str] = {}
    asset_to_side: dict[str, str] = {}
    assemblers: dict[str, Any] = {}
    allowed_events = {"book", "price_change", "best_bid_ask"}
    open_depth_gap_start_ms_by_condition: dict[str, int] = {}
    open_depth_gap_last_ms_by_condition: dict[str, int] = {}
    stop_at = time.monotonic() + args.duration_sec
    last_selection_ms = 0
    paths["events"].write_text("", encoding="utf-8")
    try:
        async with websockets.connect(args.ws_url, ping_interval=None, max_size=None) as ws:
            stats.ws_open_count += 1
            stats.stop_reason = "connected"
            with paths["events"].open("a", encoding="utf-8") as event_file:
                while True:
                    monotonic_remaining = stop_at - time.monotonic()
                    if monotonic_remaining <= 0:
                        stats.stop_reason = "duration_elapsed"
                        break
                    current_ms = now_ms()
                    if current_ms - last_selection_ms >= args.selection_poll_ms:
                        last_selection_ms = current_ms
                        active_targets = select_subscribe_targets(
                            targets,
                            at_ms=current_ms,
                            final_window_ms=args.final_window_sec * 1000,
                            subscribe_lead_ms=args.subscribe_lead_ms,
                        )
                        new_assets: list[str] = []
                        for target in active_targets:
                            target = replace(target, target_role="evidence_current")
                            target_by_condition[target.condition_id] = target
                            subscribed_condition_ids.add(target.condition_id)
                            asset_to_condition[target.token_id_yes] = target.condition_id
                            asset_to_condition[target.token_id_no] = target.condition_id
                            asset_to_side[target.token_id_yes] = "YES"
                            asset_to_side[target.token_id_no] = "NO"
                            for asset_id in target.subscribed_asset_ids:
                                if asset_id not in subscribed_assets:
                                    subscribed_assets.add(asset_id)
                                    new_assets.append(asset_id)
                        if new_assets:
                            stats.subscribed_target_market_count = len(subscribed_condition_ids)
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
                                        "subscribed_condition_count": len(subscribed_condition_ids),
                                        "condition_ids": sorted(subscribed_condition_ids),
                                    },
                                    sort_keys=True,
                                )
                                + "\n"
                            )
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=min(2.0, monotonic_remaining))
                    except asyncio.TimeoutError:
                        continue
                    recv_ts_ms = now_ms()
                    stats.raw_message_count += 1
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
                            if channel != "book" or condition_id not in target_by_condition:
                                continue
                            stats.normalized_book_count += 1
                            source_ts_ms = payload.get("source_ts_ms")
                            handle_book_sample(
                                args=args,
                                paths=paths,
                                stats=stats,
                                target=target_by_condition[condition_id],
                                recv_ts_ms=recv_ts_ms,
                                source_ts_ms=source_ts_ms if isinstance(source_ts_ms, int) else None,
                                book=payload,
                                event_file=event_file,
                                opportunity_rows=opportunity_rows,
                                book_ages_ms=book_ages_ms,
                                open_depth_gap_start_ms_by_condition=open_depth_gap_start_ms_by_condition,
                                open_depth_gap_last_ms_by_condition=open_depth_gap_last_ms_by_condition,
                            )
    except Exception as exc:  # noqa: BLE001
        seconds_to_stop = stop_at - time.monotonic()
        stats.error = repr(exc)
        if seconds_to_stop <= args.terminal_close_grace_sec:
            stats.lifecycle_terminal_close_count += 1
            stats.stop_reason = "duration_elapsed_after_terminal_close"
        else:
            stats.ws_disconnect_count += 1
            stats.stop_reason = "exception"
    for condition_id in list(open_depth_gap_start_ms_by_condition):
        start_ms = open_depth_gap_start_ms_by_condition[condition_id]
        last_ms = open_depth_gap_last_ms_by_condition.get(condition_id, start_ms)
        stats.decision_depth_gap_max_ms = max(stats.decision_depth_gap_max_ms, max(0, last_ms - start_ms))
    threshold_failures = threshold_failures_for(
        args=args,
        stats=stats,
        mode="live",
        opportunity_rows=opportunity_rows,
        preflight_errors=[],
    )
    return finalize_outputs(
        args=args,
        paths=paths,
        stats=stats,
        threshold_failures=threshold_failures,
        opportunity_rows=opportunity_rows,
        mode="live",
        target_csv_sha256=target_csv_sha256,
        loaded_targets=targets,
        book_ages_ms=book_ages_ms,
        event_counts=event_counts,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target-csv", type=Path)
    parser.add_argument("--expected-target-csv-sha256")
    parser.add_argument("--expected-target-count", type=int)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-sec", type=float, default=600.0)
    parser.add_argument("--selection-poll-ms", type=int, default=1000)
    parser.add_argument("--subscribe-lead-ms", type=int, default=20_000)
    parser.add_argument("--final-window-sec", type=int, default=60)
    parser.add_argument("--min-final-remaining-ms", type=int, default=1)
    parser.add_argument("--book-max-age-ms", type=int, default=2_000)
    parser.add_argument("--min-top-levels", type=int, default=1)
    parser.add_argument("--max-decision-depth-gap-ms", type=int, default=2_000)
    parser.add_argument("--price-lo", type=float, default=0.20)
    parser.add_argument("--price-hi", type=float, default=0.35)
    parser.add_argument("--paircap", type=float, default=0.965)
    parser.add_argument("--target-qty", dest="target_qty_values", action="append", type=float, default=[])
    parser.add_argument("--fee-rate", type=float, default=0.07)
    parser.add_argument("--min-final60-valid-snapshots", type=int, default=1)
    parser.add_argument("--require-opportunity", action="store_true")
    parser.add_argument("--max-ws-connections", type=int, default=1)
    parser.add_argument("--no-rest-book", action="store_true")
    parser.add_argument("--no-shared-ingress", action="store_true")
    parser.add_argument("--terminal-close-grace-sec", type=float, default=10.0)
    parser.add_argument("--ws-url", default=scoped.WS_URL)
    parser.add_argument("--self-test", choices=["valid", "invalid"])
    args = parser.parse_args()
    if not args.target_qty_values:
        args.target_qty_values = [5.0, 8.0]
    args.output_dir = args.output_dir.expanduser().resolve()
    if args.target_csv is not None:
        args.target_csv = args.target_csv.expanduser().resolve()
    return args


def main() -> int:
    args = parse_args()
    dir_errors = ensure_fresh_output_dir(args.output_dir)
    paths = output_paths(args.output_dir)
    paths["events"].write_text("", encoding="utf-8")
    preflight_errors, targets, target_csv_sha256 = preflight(args)
    preflight_errors = dir_errors + preflight_errors
    if preflight_errors:
        stats = ObserverStats(loaded_target_count=len(targets), stop_reason="preflight_failed")
        return finalize_outputs(
            args=args,
            paths=paths,
            stats=stats,
            threshold_failures=preflight_errors,
            opportunity_rows=[],
            mode="self_test" if args.self_test else "live",
            target_csv_sha256=target_csv_sha256,
            loaded_targets=targets,
            book_ages_ms=[],
            event_counts=Counter(),
        )
    if args.self_test:
        return run_self_test(args, paths, targets)
    return asyncio.run(run_live(args, paths, targets, target_csv_sha256))


if __name__ == "__main__":
    raise SystemExit(main())

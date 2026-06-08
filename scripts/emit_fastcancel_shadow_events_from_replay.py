#!/usr/bin/env python3
"""Emit replay-derived Fast-Cancel shadow events.

This script does not read raw capture files and does not touch replay SQLite DBs.
It converts the selected-row CSV produced by the replay backtest into the event
shape expected from a future live shadow sidecar.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONFIG = Path("configs/xuan/fastcancel_shadow_sidecar_v1.json")
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_dual_window_fastcancel_combo import (  # noqa: E402
    first_side_l2_bid_vwap_at,
    opposite_l2_ask_vwap_at_completion,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--input-csv",
        type=Path,
        help="Defaults to <leader output>/dual_window_fastcancel_combo_selected_rows.csv from config.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--replay-root", type=Path, help="Override config data_window.replay_root for L2 enrichment.")
    parser.add_argument("--no-l2-enrich", action="store_true", help="Do not attach per-episode L2 PnL/VWAP fields.")
    parser.add_argument("--limit", type=int, default=0, help="Optional row limit for smoke tests.")
    return parser.parse_args()


def as_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def as_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))


def as_bool(value: str | None) -> bool:
    return str(value).lower() == "true"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def event_base(row: dict[str, str], event_type: str, seq: int) -> dict[str, Any]:
    return {
        "event_type": event_type,
        "event_seq": seq,
        "source": "replay_selected_rows",
        "generated_at": iso_now(),
        "day": row.get("day"),
        "market_slug": row.get("slug"),
        "condition_id": row.get("condition_id"),
        "candidate_ts_ms": as_int(row.get("candidate_ts_ms")),
        "candidate_iso": row.get("candidate_iso"),
        "candidate_offset_s": as_float(row.get("offset_s")),
        "window_name": row.get("kind"),
        "first_side": row.get("first_side"),
    }


def common_fields(row: dict[str, str], config: dict[str, Any]) -> dict[str, Any]:
    base_clip = config["strategy"]["sizing"]["base_clip"]
    effective_clip = as_float(row.get("clip"))
    upclip_from = as_float(row.get("dynamic_upclip_from_clip"))
    upclip_reason = None
    if upclip_from is not None and effective_clip is not None and effective_clip > upclip_from:
        upclip_reason = row.get("dynamic_upclip_rule") or config["strategy"]["sizing"]["dynamic_upclip"]["condition"]

    return {
        "side_bid": as_float(row.get("side_bid")),
        "opp_ask": as_float(row.get("opp_ask")),
        "prev_bid_delta_1s": as_float(row.get("prev_bid_delta_1s")),
        "spread_ticks": as_float(row.get("spread_ticks")),
        "opp_spread_ticks": as_float(row.get("opp_spread_ticks")),
        "top_bid_sz": as_float(row.get("top_bid_sz")),
        "queue_same": as_float(row.get("queue_same")),
        "base_clip": base_clip,
        "effective_clip": effective_clip,
        "upclip_reason": upclip_reason,
        "order_price": as_float(row.get("order_price")),
        "required_size_proxy": as_float(row.get("required_size")),
        "actual_first_order_placed": False,
        "actual_first_fill_ts_ms": None,
        "actual_first_fill_qty": None,
        "actual_first_fill_vwap": None,
        "proxy_queue_full_fill_ts_ms": as_int(row.get("fill_ts_ms")),
        "extra_required_size_equivalent": 0.0,
        "completion_pair_cost_l1": as_float(row.get("pair_cost")),
        "completion_l2_vwap": None,
        "completion_vwap_drift": None,
        "min_pair_cost_seen_30s": as_float(row.get("min_pair_cost_seen_30s")),
        "slow_path_allowed": as_bool(row.get("slow_continue_eligible")),
        "repair_used": row.get("path") == "repair",
        "residual_exit_delay_s": residual_exit_delay(row),
        "residual_exit_vwap": None,
        "episode_status": row.get("path"),
        "shadow_pnl_l2": None,
        "shadow_pnl_with_2c_friction": None,
    }


def residual_exit_delay(row: dict[str, str]) -> int | None:
    if row.get("path") not in {"residual_settle", "repair"}:
        return None
    first_price = as_float(row.get("first_price"))
    if first_price is None:
        return None
    return 180 if first_price < 0.50 else 120


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


class L2Enricher:
    def __init__(self, replay_root: Path, slippage: float = 0.02) -> None:
        self.replay_root = replay_root
        self.slippage = slippage
        self.conns: dict[str, sqlite3.Connection] = {}

    def close(self) -> None:
        for conn in self.conns.values():
            conn.close()
        self.conns.clear()

    def conn(self, day: str) -> sqlite3.Connection:
        if day not in self.conns:
            self.conns[day] = ro_connect(self.replay_root / day / "crypto_5m.sqlite")
        return self.conns[day]

    def enrich(self, row: dict[str, str]) -> dict[str, Any]:
        path = row.get("path")
        first_fill = as_bool(row.get("first_fill"))
        if not first_fill:
            return {
                "completion_l2_vwap": None,
                "completion_vwap_drift": None,
                "residual_exit_vwap": None,
                "shadow_pnl_l2": 0.0,
                "shadow_pnl_with_2c_friction": 0.0,
                "l2_unfilled_qty": None,
            }

        day = str(row["day"])
        typed_row: dict[str, Any] = dict(row)
        for key in ("clip", "first_price", "second_price"):
            if typed_row.get(key) not in (None, ""):
                typed_row[key] = float(typed_row[key])
        for key in ("fill_ts_ms", "completion_ts_ms"):
            if typed_row.get(key) not in (None, ""):
                typed_row[key] = int(float(typed_row[key]))
        conn = self.conn(day)
        clip = float(row.get("clip") or 0.0)
        first_price = as_float(row.get("first_price"))
        if first_price is None:
            return {}

        completion_vwap = None
        completion_drift = None
        residual_vwap = None
        l2_unfilled_qty = None
        pnl_l2 = as_float(row.get("pnl")) or 0.0
        if path in {"completion", "slow_completion", "repair"}:
            result = opposite_l2_ask_vwap_at_completion(conn, typed_row)
            if result is not None and result.get("vwap") is not None:
                completion_vwap = float(result["vwap"])
                second_price = as_float(row.get("second_price"))
                completion_drift = None if second_price is None else completion_vwap - second_price
                pnl_l2 = (1.0 - first_price - completion_vwap) * clip
                l2_unfilled_qty = float(result.get("unfilled_qty") or 0.0)
        elif path == "residual_settle":
            fill_ts_ms = as_int(row.get("fill_ts_ms"))
            exit_delay_s = residual_exit_delay(row)
            if fill_ts_ms is not None and exit_delay_s is not None:
                result = first_side_l2_bid_vwap_at(conn, typed_row, fill_ts_ms + exit_delay_s * 1000)
                if result is not None and result.get("vwap") is not None:
                    residual_vwap = float(result["vwap"])
                    pnl_l2 = (residual_vwap - first_price) * clip
                    l2_unfilled_qty = float(result.get("unfilled_qty") or 0.0)

        return {
            "completion_l2_vwap": None if completion_vwap is None else round(completion_vwap, 6),
            "completion_vwap_drift": None if completion_drift is None else round(completion_drift, 6),
            "residual_exit_vwap": None if residual_vwap is None else round(residual_vwap, 6),
            "shadow_pnl_l2": round(pnl_l2, 6),
            "shadow_pnl_with_2c_friction": round(pnl_l2 - self.slippage * clip, 6),
            "l2_unfilled_qty": None if l2_unfilled_qty is None else round(l2_unfilled_qty, 6),
        }


def build_events(
    row: dict[str, str],
    config: dict[str, Any],
    start_seq: int,
    l2_enrichment: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    seq = start_seq
    common = common_fields(row, config)
    if l2_enrichment:
        common.update(l2_enrichment)
    path = row.get("path")
    first_fill = as_bool(row.get("first_fill"))

    open_event = event_base(row, "fastcancel_open_candidate", seq)
    open_event.update(common)
    open_event["candidate_allowed"] = True
    events.append(open_event)
    seq += 1

    would_place = event_base(row, "fastcancel_would_place_first_maker", seq)
    would_place.update(common)
    would_place["fill_model"] = row.get("fill_model")
    would_place["fill_timeout_s"] = config["strategy"]["first_leg"]["fill_timeout_s"]
    would_place["cancel_if_unfilled"] = config["strategy"]["first_leg"]["cancel_if_unfilled"]
    events.append(would_place)
    seq += 1

    fill_event = event_base(row, "fastcancel_first_fill_truth_or_proxy", seq)
    fill_event.update(common)
    fill_event["proxy_first_fill"] = first_fill
    fill_event["proxy_fill_delay_s"] = as_float(row.get("fill_delay_s"))
    fill_event["proxy_first_fill_price"] = as_float(row.get("first_price"))
    fill_event["proxy_first_fill_qty"] = as_float(row.get("clip")) if first_fill else None
    events.append(fill_event)
    seq += 1

    if path == "completion":
        event_type = "fastcancel_completion_window"
    elif path == "slow_completion":
        event_type = "fastcancel_slow_path_decision"
    elif path == "repair":
        event_type = "fastcancel_repair_decision"
    elif path == "residual_settle":
        event_type = "fastcancel_residual_exit_plan"
    else:
        event_type = None

    if event_type:
        lifecycle_event = event_base(row, event_type, seq)
        lifecycle_event.update(common)
        lifecycle_event["completion_ts_ms"] = as_int(row.get("completion_ts_ms"))
        lifecycle_event["completion_delay_s"] = as_float(row.get("completion_delay_s"))
        lifecycle_event["second_price"] = as_float(row.get("second_price"))
        lifecycle_event["first_price"] = as_float(row.get("first_price"))
        events.append(lifecycle_event)
        seq += 1

    summary = event_base(row, "fastcancel_episode_summary", seq)
    summary.update(common)
    summary["proxy_first_fill"] = first_fill
    summary["path"] = path
    summary["raw_replay_pnl"] = as_float(row.get("pnl"))
    summary["first_is_winner_research_only"] = as_bool(row.get("first_is_winner"))
    summary["winner_side_research_only"] = row.get("winner_side") or None
    events.append(summary)

    return events


def load_config(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def resolve_input_csv(config: dict[str, Any], input_csv: Path | None) -> Path:
    if input_csv is not None:
        return input_csv
    leader_dir = Path(config["source_outputs"]["leader"])
    return leader_dir / "dual_window_fastcancel_combo_selected_rows.csv"


def summarize(events: list[dict[str, Any]]) -> dict[str, Any]:
    episodes = [e for e in events if e["event_type"] == "fastcancel_episode_summary"]
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    for ep in episodes:
        day = ep.get("day") or "unknown"
        path = ep.get("path") or "unknown"
        daily[day]["attempts"] += 1
        if ep.get("proxy_first_fill"):
            daily[day]["fills"] += 1
        daily[day][f"path_{path}"] += 1

    return {
        "generated_at": iso_now(),
        "events": len(events),
        "episodes": len(episodes),
        "event_type_counts": Counter(e["event_type"] for e in events),
        "path_counts": Counter((e.get("path") or e.get("episode_status") or "unknown") for e in episodes),
        "daily": {day: dict(counts) for day, counts in sorted(daily.items())},
        "notes": [
            "Replay fixture uses public queue proxy; actual order/fill truth fields are null.",
            "winner_side and first_is_winner are research-only and must not be used in live decisions.",
            "L2 PnL is not available in selected-row CSV; use replay summary/report for aggregate L2 evidence.",
        ],
    }


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    input_csv = resolve_input_csv(config, args.input_csv)
    if not input_csv.exists():
        raise SystemExit(f"input CSV not found: {input_csv}")

    events: list[dict[str, Any]] = []
    seq = 1
    replay_root = args.replay_root or Path(config["data_window"]["replay_root"])
    enricher = None if args.no_l2_enrich else L2Enricher(replay_root)
    try:
        with input_csv.open(newline="") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                if args.limit and idx >= args.limit:
                    break
                l2_enrichment = None if enricher is None else enricher.enrich(row)
                row_events = build_events(row, config, seq, l2_enrichment)
                events.extend(row_events)
                seq += len(row_events)
    finally:
        if enricher is not None:
            enricher.close()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    events_path = args.output_dir / "fastcancel_shadow_events.jsonl"
    summary_path = args.output_dir / "fastcancel_shadow_events_summary.json"

    with events_path.open("w") as f:
        for event in events:
            f.write(json.dumps(event, sort_keys=True) + "\n")

    summary = summarize(events)
    with summary_path.open("w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
        f.write("\n")

    print(json.dumps({"events": len(events), "episodes": summary["episodes"], "output_dir": str(args.output_dir)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

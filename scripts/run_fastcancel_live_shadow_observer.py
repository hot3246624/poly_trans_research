#!/usr/bin/env python3
"""Rolling replay observer for Fast-Cancel live shadow.

This is a no-order dry-run tool. It only reads replay SQLite and writes JSONL
shadow events. It does not read raw data, does not send REST orders, and does
not modify replay DBs.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_dual_window_fastcancel_combo import apply_dynamic_upclip, select_state_machine  # noqa: E402
from backtest_btc5m_maker_fill_triggered import (  # noqa: E402
    OUTAGE_END_MS,
    OUTAGE_START_MS,
    TRUSTED_START_MS,
    build_rows_for_market,
    ro_connect,
)
from emit_fastcancel_shadow_events_from_replay import L2Enricher, build_events  # noqa: E402
from summarize_fastcancel_shadow_events import read_events, summarize, write_markdown  # noqa: E402


DEFAULT_CONFIG = Path("configs/xuan/fastcancel_shadow_sidecar_v1.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--replay-root", type=Path)
    parser.add_argument("--day", help="UTC day YYYY-MM-DD. Default: current UTC day.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--state-file", type=Path)
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--max-markets", type=int, default=0)
    parser.add_argument("--min-market-age-sec", type=int, default=220, help="Only evaluate markets with enough future data.")
    parser.add_argument("--no-l2-enrich", action="store_true")
    return parser.parse_args()


def utc_day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"emitted_keys": []}
    return json.loads(path.read_text())


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def max_available_ms(conn: sqlite3.Connection) -> int | None:
    book = conn.execute("SELECT MAX(recv_ms) FROM md_book_l1").fetchone()[0]
    trades = conn.execute("SELECT MAX(trade_ts_ms) FROM md_trades WHERE trade_ts_ms IS NOT NULL").fetchone()[0]
    values = [int(v) for v in (book, trades) if v is not None]
    return min(values) if values else None


def fetch_shadow_markets(conn: sqlite3.Connection, *, max_ms: int, min_age_sec: int, max_markets: int) -> list[sqlite3.Row]:
    min_end_ms = max_ms - max(0, min_age_sec) * 1000
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC'
          AND m.interval_sec = 300
          AND m.end_ms > ?
          AND m.start_ms <= ?
        ORDER BY m.start_ms
        """,
        (TRUSTED_START_MS, min_end_ms),
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        out.append(row)
        if max_markets > 0 and len(out) >= max_markets:
            break
    return out


def strategy_args(window: dict[str, Any], strategy: dict[str, Any], clip: int) -> SimpleNamespace:
    first_leg = strategy["first_leg"]
    completion = strategy["completion_controller"]
    primary = completion["primary"]
    slow = completion["slow_path"]
    repair = completion["repair"]
    return SimpleNamespace(
        sample_interval_s=1,
        clip=float(clip),
        min_offset_s=int(window["min_offset_s"]),
        max_offset_s=int(window["max_offset_s"]),
        tail_freeze_s=int(strategy["state_machine"]["tail_freeze_s"]),
        min_side_bid=float(window["min_side_bid"]),
        max_side_bid=float(window["max_side_bid"]),
        max_spread_ticks=float(window["max_spread_ticks"]),
        max_opp_spread_ticks=(
            float(window["max_opp_spread_ticks"]) if "max_opp_spread_ticks" in window else None
        ),
        max_immediate_pair_cost=(
            float(window["max_immediate_pair_cost"]) if "max_immediate_pair_cost" in window else None
        ),
        min_opp_ask_sz=None,
        min_prev_bid_delta_1s=float(window["min_prev_bid_delta_1s"]),
        max_queue_same=None,
        max_top_bid_sz=float(window["max_top_bid_sz"]),
        price_offsets="0",
        fill_models="queue_full",
        extra_required_size=0.0,
        first_fill_timeout_s=int(first_leg["fill_timeout_s"]),
        completion_pair_ceiling=float(primary["pair_cost_ceiling"]),
        completion_deadline_s=int(primary["deadline_s"]),
        slow_continue_evidence_ceiling=float(slow["allow_if_min_pair_cost_seen_30s_lte"]),
        slow_completion_pair_ceiling=float(slow["pair_cost_ceiling"]),
        slow_completion_deadline_s=int(slow["deadline_s"]),
        repair_pair_ceiling=float(repair["pair_cost_ceiling"]),
        repair_deadline_s=int(repair["deadline_s"]),
        disable_repair=False,
        cooldown_s=int(strategy["state_machine"]["cooldown_after_close_s"]),
        emit_all_candidates=False,
        max_markets=0,
    )


def row_key(row: dict[str, Any]) -> str:
    return ":".join(
        [
            str(row.get("condition_id")),
            str(row.get("candidate_ts_ms")),
            str(row.get("first_side")),
            str(row.get("kind")),
            str(row.get("clip")),
        ]
    )


def generate_selected_rows(conn: sqlite3.Connection, config: dict[str, Any], markets: list[sqlite3.Row]) -> list[dict[str, Any]]:
    strategy = config["strategy"]
    windows = strategy["open_windows"]
    base_clip = int(strategy["sizing"]["base_clip"])
    dynamic_upclip = strategy.get("sizing", {}).get("dynamic_upclip", {})
    upclip_enabled = bool(dynamic_upclip.get("enabled", False))
    upclip = int(dynamic_upclip.get("effective_clip", base_clip))
    all_base_rows: list[dict[str, Any]] = []
    all_upclip_rows: list[dict[str, Any]] = []

    for market in markets:
        for idx, window in enumerate(windows):
            kind = str(window.get("name") or f"window_{idx + 1}")
            clip_targets = [(base_clip, all_base_rows)]
            if upclip_enabled and upclip != base_clip:
                clip_targets.append((upclip, all_upclip_rows))
            for clip, target in clip_targets:
                args = strategy_args(window, strategy, clip)
                rows = build_rows_for_market(conn, market, args)
                for row in rows:
                    row["kind"] = kind
                target.extend(rows)

    dynamic_rule = str(dynamic_upclip.get("condition", "prev_bid_delta_1s >= 0.14")).replace(" >= ", ":ge:")
    if upclip_enabled and all_upclip_rows:
        combined = apply_dynamic_upclip(all_base_rows, all_upclip_rows, dynamic_rule)
    else:
        combined = all_base_rows
    return select_state_machine(combined, int(strategy["state_machine"]["cooldown_after_close_s"]) * 1000)


def append_events(
    *,
    output_dir: Path,
    config: dict[str, Any],
    replay_root: Path,
    selected: list[dict[str, Any]],
    emitted_keys: set[str],
    no_l2_enrich: bool,
) -> tuple[int, list[str]]:
    events_path = output_dir / "fastcancel_live_shadow_events.jsonl"
    output_dir.mkdir(parents=True, exist_ok=True)
    new_keys: list[str] = []
    event_seq = 1
    if events_path.exists():
        event_seq = sum(1 for _ in events_path.open()) + 1

    enricher = None if no_l2_enrich else L2Enricher(replay_root)
    emitted = 0
    try:
        with events_path.open("a") as f:
            for row in selected:
                key = row_key(row)
                if key in emitted_keys:
                    continue
                enrich = None if enricher is None else enricher.enrich(row)
                events = build_events(row, config, event_seq, enrich)
                for event in events:
                    event["source"] = "rolling_replay_live_shadow_observer"
                    event["event_id"] = f"{key}:{event['event_type']}"
                    f.write(json.dumps(event, sort_keys=True) + "\n")
                event_seq += len(events)
                emitted += len(events)
                new_keys.append(key)
    finally:
        if enricher is not None:
            enricher.close()
    return emitted, new_keys


def write_report(output_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    events_path = output_dir / "fastcancel_live_shadow_events.jsonl"
    report_json = output_dir / "fastcancel_live_shadow_report.json"
    report_md = output_dir / "fastcancel_live_shadow_report.md"
    events = read_events(events_path) if events_path.exists() else []
    summary = summarize(events, config, combo_summary=None)
    report_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write_markdown(summary, report_md)
    return summary


def run_once(args: argparse.Namespace, config: dict[str, Any], replay_root: Path, day: str, output_dir: Path, state_file: Path) -> dict[str, Any]:
    db = replay_root / day / "crypto_5m.sqlite"
    if not db.exists():
        raise SystemExit(f"replay db not found: {db}")
    state = load_state(state_file)
    emitted_keys = set(state.get("emitted_keys", []))
    with ro_connect(db) as conn:
        max_ms = max_available_ms(conn)
        if max_ms is None:
            return {"status": "no_data", "day": day, "emitted_events": 0, "new_episodes": 0}
        markets = fetch_shadow_markets(
            conn,
            max_ms=max_ms,
            min_age_sec=int(args.min_market_age_sec),
            max_markets=int(args.max_markets),
        )
        selected = generate_selected_rows(conn, config, markets)
    emitted, new_keys = append_events(
        output_dir=output_dir,
        config=config,
        replay_root=replay_root,
        selected=selected,
        emitted_keys=emitted_keys,
        no_l2_enrich=bool(args.no_l2_enrich),
    )
    if new_keys:
        state["emitted_keys"] = sorted(emitted_keys | set(new_keys))
        state["last_run_utc"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
        state["last_day"] = day
        save_state(state_file, state)
    report = write_report(output_dir, config)
    return {
        "status": "ok",
        "day": day,
        "markets": len(markets),
        "selected_episodes_seen": len(selected),
        "new_episodes": len(new_keys),
        "emitted_events": emitted,
        "report_verdict": report.get("verdict", {}),
    }


def main() -> int:
    args = parse_args()
    config = load_json(args.config)
    day = args.day or utc_day()
    replay_root = args.replay_root or Path(config["data_window"]["replay_root"])
    output_dir = args.output_dir or Path("data/exports") / "fastcancel_live_shadow" / day
    state_file = args.state_file or output_dir / ".fastcancel_live_shadow_state.json"

    started = time.monotonic()
    while True:
        result = run_once(args, config, replay_root, day, output_dir, state_file)
        print(json.dumps(result, indent=2, sort_keys=True))
        if not args.loop:
            break
        if args.duration_sec > 0 and time.monotonic() - started >= args.duration_sec:
            break
        time.sleep(max(1.0, float(args.poll_sec)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

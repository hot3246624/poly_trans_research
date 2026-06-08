#!/usr/bin/env python3
"""Summarize Fast-Cancel shadow event JSONL.

The input can be replay-derived fixtures or future live shadow events. The
report is deliberately conservative: missing user execution truth is surfaced as
a validation gap, not silently treated as pass.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--config", type=Path, help="Optional fastcancel shadow config for gate thresholds.")
    parser.add_argument("--combo-summary", type=Path, help="Optional dual_window_fastcancel_combo_summary.json.")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    parser.add_argument("--require-replay-ready", action="store_true", help="Exit 2 unless replay_ready_for_live_shadow is true.")
    parser.add_argument("--require-enforce-ready", action="store_true", help="Exit 2 unless promote_to_enforce_discussion is true.")
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with path.open() as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise SystemExit(f"invalid JSONL at {path}:{line_no}: {exc}") from exc
    return events


def fnum(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    with path.open() as f:
        return json.load(f)


def min_observation_days(config: dict[str, Any] | None) -> int:
    if not config:
        return 3
    raw = str(config.get("shadow_pass_fail", {}).get("minimum_observation_window", "3"))
    match = re.search(r"\d+", raw)
    return int(match.group(0)) if match else 3


def candidate_min_per_day(config: dict[str, Any] | None) -> int:
    if not config:
        return 20
    return int(config.get("shadow_pass_fail", {}).get("candidate_count_min_per_day", 20))


def parse_positive_days(value: str | None) -> tuple[int, int] | None:
    if not value or "/" not in value:
        return None
    left, right = value.split("/", 1)
    return int(left), int(right)


def combo_replay_gates(combo_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not combo_summary:
        return {
            "combo_summary_available": False,
            "l2_positive_all_days": "unknown",
            "l2_2c_positive_all_days": "unknown",
            "l2_2c_total_pnl": None,
            "max_all_positive_slippage": None,
        }
    l2 = combo_summary.get("l2_completion_reprice", {})
    base = l2.get("base", {})
    slip = l2.get("slippage", {})
    robust_slip = combo_summary.get("robustness", {}).get("l2_slippage", {})
    l2_pos = parse_positive_days(base.get("positive_days"))
    slip_2c = slip.get("0.02") or slip.get("0.020") or {}
    slip_2c_pos = parse_positive_days(slip_2c.get("positive_days"))
    return {
        "combo_summary_available": True,
        "l2_total_pnl": base.get("pnl"),
        "l2_positive_days": base.get("positive_days"),
        "l2_positive_all_days": bool(l2_pos and l2_pos[0] == l2_pos[1] and l2_pos[1] > 0),
        "l2_2c_total_pnl": slip_2c.get("pnl"),
        "l2_2c_positive_days": slip_2c.get("positive_days"),
        "l2_2c_positive_all_days": bool(slip_2c_pos and slip_2c_pos[0] == slip_2c_pos[1] and slip_2c_pos[1] > 0),
        "second_leg_vwap_delta": l2.get("second_leg_vwap_delta", {}),
        "unfilled_second_qty": l2.get("unfilled_second_qty"),
        "unfilled_exit_qty": l2.get("unfilled_exit_qty"),
        "max_all_positive_slippage": robust_slip.get("max_tested_slippage_all_days_positive"),
    }


def event_contract(events: list[dict[str, Any]], episodes: list[dict[str, Any]], config: dict[str, Any] | None) -> dict[str, Any]:
    if not config:
        return {
            "required_event_types": [],
            "event_types_seen": sorted({str(e.get("event_type", "unknown")) for e in events}),
            "missing_required_event_types": [],
            "required_episode_fields": [],
            "missing_required_episode_fields": [],
            "episode_schema_pass": True,
        }
    required_event_types = [str(v) for v in config.get("required_shadow_events", [])]
    event_types_seen = sorted({str(e.get("event_type", "unknown")) for e in events})
    required_episode_fields = [str(v) for v in config.get("required_shadow_fields", [])]
    missing_fields: list[str] = []
    for field in required_episode_fields:
        if any(field not in ep for ep in episodes):
            missing_fields.append(field)
    return {
        "required_event_types": required_event_types,
        "event_types_seen": event_types_seen,
        "missing_required_event_types": sorted(set(required_event_types) - set(event_types_seen)),
        "required_episode_fields": required_episode_fields,
        "missing_required_episode_fields": missing_fields,
        "episode_schema_pass": not missing_fields,
    }


def summarize(events: list[dict[str, Any]], config: dict[str, Any] | None, combo_summary: dict[str, Any] | None) -> dict[str, Any]:
    episodes = [e for e in events if e.get("event_type") == "fastcancel_episode_summary"]
    daily: dict[str, Counter[str]] = defaultdict(Counter)
    event_type_counts = Counter(e.get("event_type", "unknown") for e in events)
    path_counts = Counter(e.get("path") or e.get("episode_status") or "unknown" for e in episodes)
    clip_counts = Counter(str(e.get("effective_clip")) for e in episodes)
    upclip_count = sum(1 for e in episodes if e.get("upclip_reason"))
    raw_pnls = [fnum(e.get("raw_replay_pnl")) for e in episodes]
    raw_pnl = sum(v for v in raw_pnls if v is not None)
    path_pnl: dict[str, float] = defaultdict(float)
    kind_pnl: dict[str, float] = defaultdict(float)
    kind_pnl_l2: dict[str, float] = defaultdict(float)
    kind_pnl_2c: dict[str, float] = defaultdict(float)
    clip_pnl_l2: dict[str, float] = defaultdict(float)
    clip_pnl_2c: dict[str, float] = defaultdict(float)
    kind_counts: Counter[str] = Counter()

    actual_truth_events = [
        e
        for e in events
        if e.get("actual_first_order_placed")
        or e.get("actual_first_fill_ts_ms") is not None
        or e.get("actual_first_fill_qty") is not None
    ]

    proxy_filled = 0
    actual_filled = 0
    shadow_pnl_l2_values: list[float] = []
    shadow_pnl_2c_values: list[float] = []
    completion_vwap_drifts = []
    extra_required_size_equiv = []
    residual_exit_vwaps = []
    for ep in episodes:
        day = ep.get("day") or "unknown"
        daily[day]["attempts"] += 1
        if ep.get("proxy_first_fill"):
            proxy_filled += 1
            daily[day]["proxy_fills"] += 1
        if ep.get("actual_first_fill_ts_ms") is not None:
            actual_filled += 1
            daily[day]["actual_fills"] += 1
        path = ep.get("path") or ep.get("episode_status") or "unknown"
        window_name = str(ep.get("window_name") or "unknown")
        clip_key = str(ep.get("effective_clip"))
        daily[day][f"path_{path}"] += 1
        pnl = fnum(ep.get("raw_replay_pnl"))
        if pnl is not None:
            daily[day]["raw_replay_pnl"] += pnl
            path_pnl[path] += pnl
            kind_pnl[window_name] += pnl
        pnl_2c = fnum(ep.get("shadow_pnl_with_2c_friction"))
        if pnl_2c is not None:
            shadow_pnl_2c_values.append(pnl_2c)
            daily[day]["shadow_pnl_with_2c_friction"] += pnl_2c
            kind_pnl_2c[window_name] += pnl_2c
            clip_pnl_2c[clip_key] += pnl_2c
        pnl_l2 = fnum(ep.get("shadow_pnl_l2"))
        if pnl_l2 is not None:
            shadow_pnl_l2_values.append(pnl_l2)
            daily[day]["shadow_pnl_l2"] += pnl_l2
            kind_pnl_l2[window_name] += pnl_l2
            clip_pnl_l2[clip_key] += pnl_l2
        drift = fnum(ep.get("completion_vwap_drift"))
        if drift is not None:
            completion_vwap_drifts.append(drift)
        extra_size = fnum(ep.get("extra_required_size_equivalent"))
        if extra_size is not None:
            extra_required_size_equiv.append(extra_size)
        exit_vwap = fnum(ep.get("residual_exit_vwap"))
        if exit_vwap is not None:
            residual_exit_vwaps.append(exit_vwap)
        kind_counts[str(ep.get("window_name") or "unknown")] += 1

    attempts = len(episodes)
    days = sorted(daily)
    min_days = min_observation_days(config)
    min_candidates = candidate_min_per_day(config)
    daily_attempts = {day: int(row.get("attempts", 0)) for day, row in daily.items()}
    daily_attempts_min = min(daily_attempts.values()) if daily_attempts else 0
    combo_gates = combo_replay_gates(combo_summary)
    contract = event_contract(events, episodes, config)
    warnings: list[str] = []
    if not actual_truth_events:
        warnings.append("actual user execution truth missing; enforce cannot be evaluated")
    if not shadow_pnl_2c_values:
        warnings.append("shadow_pnl_with_2c_friction missing; using replay/raw fields only")

    drift_p50 = percentile(completion_vwap_drifts, 50)
    drift_p90 = percentile(completion_vwap_drifts, 90)
    own_truth_ready = bool(actual_truth_events)
    replay_shadow_ready = (
        attempts > 0
        and len(days) >= min_days
        and daily_attempts_min >= min_candidates
        and combo_gates.get("l2_2c_positive_all_days") is True
        and contract["episode_schema_pass"] is True
    )
    completion_drift_pass = None
    if drift_p50 is not None and drift_p90 is not None:
        completion_drift_pass = drift_p50 <= 0.01 and drift_p90 <= 0.03

    promote_to_enforce = bool(
        replay_shadow_ready
        and own_truth_ready
        and shadow_pnl_2c_values
        and completion_drift_pass is True
    )

    return {
        "generated_at": now_iso(),
        "events": len(events),
        "episodes": attempts,
        "event_type_counts": dict(event_type_counts),
        "path_counts": dict(path_counts),
        "clip_counts": dict(clip_counts),
        "upclip_count": upclip_count,
        "kind_counts": dict(kind_counts),
        "proxy_filled": proxy_filled,
        "proxy_fill_rate": proxy_filled / attempts if attempts else None,
        "actual_filled": actual_filled,
        "actual_fill_rate": actual_filled / attempts if attempts else None,
        "raw_replay_pnl": raw_pnl,
        "shadow_pnl_l2": sum(shadow_pnl_l2_values) if shadow_pnl_l2_values else None,
        "path_raw_replay_pnl": {key: round(value, 6) for key, value in sorted(path_pnl.items())},
        "kind_raw_replay_pnl": {key: round(value, 6) for key, value in sorted(kind_pnl.items())},
        "kind_shadow_pnl_l2": {key: round(value, 6) for key, value in sorted(kind_pnl_l2.items())},
        "kind_shadow_pnl_with_2c_friction": {key: round(value, 6) for key, value in sorted(kind_pnl_2c.items())},
        "clip_shadow_pnl_l2": {key: round(value, 6) for key, value in sorted(clip_pnl_l2.items())},
        "clip_shadow_pnl_with_2c_friction": {key: round(value, 6) for key, value in sorted(clip_pnl_2c.items())},
        "shadow_pnl_with_2c_friction": sum(shadow_pnl_2c_values) if shadow_pnl_2c_values else None,
        "completion_vwap_drift": {
            "count": len(completion_vwap_drifts),
            "p50": drift_p50,
            "p90": drift_p90,
        },
        "extra_required_size_equivalent": {
            "count": len(extra_required_size_equiv),
            "p50": percentile(extra_required_size_equiv, 50),
            "p90": percentile(extra_required_size_equiv, 90),
        },
        "residual_exit_vwap": {
            "count": len(residual_exit_vwaps),
            "p50": percentile(residual_exit_vwaps, 50),
            "p90": percentile(residual_exit_vwaps, 90),
        },
        "gate_thresholds": {
            "min_observation_days": min_days,
            "candidate_count_min_per_day": min_candidates,
            "completion_vwap_drift_p50_max": 0.01,
            "completion_vwap_drift_p90_max": 0.03,
        },
        "replay_l2_evidence": combo_gates,
        "event_contract": contract,
        "daily": {day: dict(counter) for day, counter in sorted(daily.items())},
        "validation_warnings": warnings,
        "gate_results": {
            "observation_window": len(days) >= min_days,
            "candidate_count_min_per_day": daily_attempts_min >= min_candidates,
            "event_schema_pass": contract["episode_schema_pass"],
            "l2_2c_positive_all_days": combo_gates.get("l2_2c_positive_all_days"),
            "own_execution_truth_ready": own_truth_ready,
            "completion_vwap_drift_pass": completion_drift_pass if completion_drift_pass is not None else "unknown",
            "shadow_pnl_2c_available": bool(shadow_pnl_2c_values),
        },
        "verdict": {
            "market_side_shadow_reportable": attempts > 0,
            "replay_ready_for_live_shadow": replay_shadow_ready,
            "own_execution_truth_ready": own_truth_ready,
            "enforce_evaluable": bool(actual_truth_events and shadow_pnl_2c_values),
            "promote_to_enforce_discussion": promote_to_enforce,
        },
    }


def fmt_money(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def write_markdown(summary: dict[str, Any], path: Path) -> None:
    lines = [
        "# Fast-Cancel Shadow Event Summary",
        "",
        f"- generated_at: `{summary['generated_at']}`",
        f"- events: `{summary['events']}`",
        f"- episodes: `{summary['episodes']}`",
        f"- proxy_filled: `{summary['proxy_filled']}`",
        f"- proxy_fill_rate: `{fmt_pct(summary['proxy_fill_rate'])}`",
        f"- actual_filled: `{summary['actual_filled']}`",
        f"- actual_fill_rate: `{fmt_pct(summary['actual_fill_rate'])}`",
        f"- raw_replay_pnl: `{fmt_money(summary['raw_replay_pnl'])}`",
        f"- replay_l2_pnl: `{fmt_money(summary['replay_l2_evidence'].get('l2_total_pnl'))}`",
        f"- shadow_pnl_l2: `{fmt_money(summary['shadow_pnl_l2'])}`",
        f"- replay_l2_2c_pnl: `{fmt_money(summary['replay_l2_evidence'].get('l2_2c_total_pnl'))}`",
        f"- shadow_pnl_with_2c_friction: `{fmt_money(summary['shadow_pnl_with_2c_friction'])}`",
        "",
        "## Verdict",
        "",
    ]
    for key, value in summary["verdict"].items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(["", "## Gate Results", ""])
    for key, value in summary["gate_results"].items():
        lines.append(f"- {key}: `{value}`")

    lines.extend(["", "## Event Contract", ""])
    lines.append(f"- episode_schema_pass: `{summary['event_contract']['episode_schema_pass']}`")
    lines.append(f"- missing_required_episode_fields: `{summary['event_contract']['missing_required_episode_fields']}`")
    lines.append(f"- missing_required_event_types: `{summary['event_contract']['missing_required_event_types']}`")

    if summary["validation_warnings"]:
        lines.extend(["", "## Validation Warnings", ""])
        for warning in summary["validation_warnings"]:
            lines.append(f"- {warning}")

    lines.extend(["", "## Path Counts", ""])
    lines.extend(f"- {key}: `{value}`" for key, value in sorted(summary["path_counts"].items()))

    lines.extend(["", "## Raw PnL Attribution", ""])
    lines.append("| bucket | raw replay pnl |")
    lines.append("|---|---:|")
    for key, value in summary["path_raw_replay_pnl"].items():
        lines.append(f"| path:{key} | {float(value):.2f} |")
    for key, value in summary["kind_raw_replay_pnl"].items():
        lines.append(f"| window:{key} | {float(value):.2f} |")

    lines.extend(["", "## L2 Attribution", ""])
    lines.append("| bucket | shadow L2 | shadow +2c |")
    lines.append("|---|---:|---:|")
    for key, value in summary["kind_shadow_pnl_l2"].items():
        plus_2c = summary["kind_shadow_pnl_with_2c_friction"].get(key, 0.0)
        lines.append(f"| window:{key} | {float(value):.2f} | {float(plus_2c):.2f} |")
    for key, value in summary["clip_shadow_pnl_l2"].items():
        plus_2c = summary["clip_shadow_pnl_with_2c_friction"].get(key, 0.0)
        lines.append(f"| clip:{key} | {float(value):.2f} | {float(plus_2c):.2f} |")

    lines.extend(["", "## Drift Metrics", ""])
    lines.append(f"- completion_vwap_drift: `{summary['completion_vwap_drift']}`")
    lines.append(f"- extra_required_size_equivalent: `{summary['extra_required_size_equivalent']}`")
    lines.append(f"- residual_exit_vwap: `{summary['residual_exit_vwap']}`")

    lines.extend(["", "## Daily", ""])
    lines.append("| day | attempts | proxy fills | raw replay pnl | shadow L2 | shadow +2c | paths |")
    lines.append("|---|---:|---:|---:|---:|---:|---|")
    for day, row in summary["daily"].items():
        paths = ", ".join(
            f"{key.removeprefix('path_')}={value}"
            for key, value in sorted(row.items())
            if key.startswith("path_")
        )
        lines.append(
            f"| {day} | {int(row.get('attempts', 0))} | {int(row.get('proxy_fills', 0))} "
            f"| {float(row.get('raw_replay_pnl', 0.0)):.2f} "
            f"| {float(row.get('shadow_pnl_l2', 0.0)):.2f} "
            f"| {float(row.get('shadow_pnl_with_2c_friction', 0.0)):.2f} | {paths} |"
        )

    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    events = read_events(args.events)
    config = load_json(args.config)
    combo_summary = load_json(args.combo_summary)
    summary = summarize(events, config, combo_summary)

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    write_markdown(summary, args.output_md)
    print(json.dumps({"episodes": summary["episodes"], "output_json": str(args.output_json), "output_md": str(args.output_md)}))
    if args.require_enforce_ready and not summary["verdict"]["promote_to_enforce_discussion"]:
        return 2
    if args.require_replay_ready and not summary["verdict"]["replay_ready_for_live_shadow"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

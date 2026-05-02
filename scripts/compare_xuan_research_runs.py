#!/usr/bin/env python3
"""Compare a fresh xuan research run with the current baseline artifacts."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


BASELINE_PATHS = {
    "tranche": "data/exports/xuan_tranche_ladder_latest_20260501/xuan_tranche_ladder_summary.json",
    "cycle_gate": "data/exports/xuan_cycle_feature_gate_20260501/xuan_cycle_gate_summary.json",
    "match": "data/exports/xuan_public_trade_match_20260501/xuan_public_trade_match_summary.json",
    "threshold_wait30": "data/exports/btc5m_bounded_taker_threshold_wait30_20260501/btc5m_bounded_taker_threshold_summary.json",
    "threshold_wait50": "data/exports/btc5m_bounded_taker_threshold_wait50_20260501/btc5m_bounded_taker_threshold_summary.json",
    "threshold_wait70": "data/exports/btc5m_bounded_taker_threshold_20260501/btc5m_bounded_taker_threshold_summary.json",
    "schedule": "data/exports/btc5m_bounded_taker_schedule_20260501/btc5m_bounded_taker_schedule_summary.json",
}


RUN_PATHS = {
    "tranche": "xuan_tranche_ladder/xuan_tranche_ladder_summary.json",
    "cycle_gate": "xuan_cycle_feature_gate/xuan_cycle_gate_summary.json",
    "match": "xuan_public_trade_match/xuan_public_trade_match_summary.json",
    "threshold_wait30": "bounded_taker_threshold_wait30/btc5m_bounded_taker_threshold_summary.json",
    "threshold_wait50": "bounded_taker_threshold_wait50/btc5m_bounded_taker_threshold_summary.json",
    "threshold_wait70": "bounded_taker_threshold_wait70/btc5m_bounded_taker_threshold_summary.json",
    "schedule": "bounded_taker_schedule/btc5m_bounded_taker_schedule_summary.json",
}


def load_json(path: str | Path) -> dict[str, Any] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def get(data: dict[str, Any] | None, path: str, default: Any = None) -> Any:
    if data is None:
        return default
    cur: Any = data
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def as_num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def delta(current: Any, baseline: Any) -> float | None:
    c = as_num(current)
    b = as_num(baseline)
    if c is None or b is None:
        return None
    return round(c - b, 6)


def pct(value: Any) -> str:
    n = as_num(value)
    if n is None:
        return "N/A"
    return f"{n * 100:.2f}%"


def val(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def metric_row(name: str, current: Any, baseline: Any, kind: str = "num") -> dict[str, Any]:
    return {
        "metric": name,
        "baseline": baseline,
        "current": current,
        "delta": delta(current, baseline),
        "kind": kind,
    }


def extract_metrics(bundle: dict[str, dict[str, Any] | None]) -> list[dict[str, Any]]:
    tranche = bundle["tranche"]
    cycle_gate = bundle["cycle_gate"]
    match = bundle["match"]
    threshold70 = bundle["threshold_wait70"]
    schedule = bundle["schedule"]

    metrics: list[dict[str, Any]] = []
    metrics.extend(
        [
            {"metric": "trade_range_max_iso", "value": get(tranche, "trade_time_range.max_iso"), "kind": "text"},
            {"metric": "btc_5m_buy_trade_count", "value": get(tranche, "btc_5m_buy_trade_count")},
            {"metric": "cycle_closed_rate", "value": get(tranche, "cycle_summary.closed_rate"), "kind": "pct"},
            {"metric": "cycle_pair_cost_p50", "value": get(tranche, "cycle_summary.pair_cost.p50")},
            {"metric": "cycle_pair_cost_lt_0_90_rate", "value": get(tranche, "cycle_summary.pair_cost_lt_0_90_rate"), "kind": "pct"},
            {"metric": "latest_pair_cost_p50", "value": get(tranche, "latest_window_summary.pair_cost.p50")},
            {"metric": "latest_pair_cost_lt_0_90_rate", "value": get(tranche, "latest_window_summary.pair_cost_lt_0_90_rate"), "kind": "pct"},
            {"metric": "cycle_clean_slow_improved_count", "value": get(tranche, "cycle_summary.class_counts.clean_slow_improved")},
            {"metric": "cycle_failed_residual_count", "value": get(tranche, "cycle_summary.class_counts.failed_residual")},
            {"metric": "cycle_gate_baseline_target_rate", "value": get(cycle_gate, "baseline.baseline_target_rate"), "kind": "pct"},
            {"metric": "cycle_gate_baseline_failed_rate", "value": get(cycle_gate, "baseline.baseline_failed_rate"), "kind": "pct"},
            {"metric": "match_5s_trade_count", "value": get(match, "by_window.5000.trade_count")},
            {"metric": "match_5s_exact_rate", "value": get(match, "by_window.5000.price_size_match_rate"), "kind": "pct"},
            {"metric": "match_5s_taker_exact_rate", "value": get(match, "by_window.5000.taker_like_buy_rate_exact"), "kind": "pct"},
            {"metric": "match_5s_maker_exact_rate", "value": get(match, "by_window.5000.maker_like_bid_rate_exact"), "kind": "pct"},
            {"metric": "open_phase_5s_exact_rate", "value": get(match, "by_window_phase.5000.open_residual.price_size_match_rate"), "kind": "pct"},
            {"metric": "completion_phase_5s_exact_rate", "value": get(match, "by_window_phase.5000.clean_completion.price_size_match_rate"), "kind": "pct"},
        ]
    )

    threshold_items = get(threshold70, "aggregate.by_mode_cap_ceiling", {}) or {}
    schedule_items = get(schedule, "aggregate.by_mode_cap_schedule", {}) or {}
    threshold_keys = {
        "threshold_balanced_090_closed": ("balanced_180_240_taker_high_ask|cap=220|ceiling=0.9", "closed_rate_among_candidates"),
        "threshold_balanced_095_closed": ("balanced_180_240_taker_high_ask|cap=220|ceiling=0.95", "closed_rate_among_candidates"),
        "threshold_tail_090_closed": ("tail_sniper_240_285_taker_small_clip|cap=140|ceiling=0.9", "closed_rate_among_candidates"),
        "threshold_tail_095_closed": ("tail_sniper_240_285_taker_small_clip|cap=140|ceiling=0.95", "closed_rate_among_candidates"),
    }
    for name, (item_key, leaf) in threshold_keys.items():
        metrics.append({"metric": name, "value": get(threshold_items.get(item_key), leaf), "kind": "pct"})
    schedule_keys = {
        "schedule_balanced_default_closed": ("balanced_180_240_taker_high_ask|cap=220|schedule=30s_0.9_50s_0.95_70s_1", "closed_rate_among_candidates", "pct"),
        "schedule_balanced_default_pair_p50": ("balanced_180_240_taker_high_ask|cap=220|schedule=30s_0.9_50s_0.95_70s_1", "pair_cost.p50", "num"),
        "schedule_tail_30_90_70_95_closed": ("tail_sniper_240_285_taker_small_clip|cap=140|schedule=30s_0.9_70s_0.95", "closed_rate_among_candidates", "pct"),
        "schedule_tail_30_90_70_95_pair_p50": ("tail_sniper_240_285_taker_small_clip|cap=140|schedule=30s_0.9_70s_0.95", "pair_cost.p50", "num"),
    }
    for name, (item_key, leaf, kind) in schedule_keys.items():
        metrics.append({"metric": name, "value": get(schedule_items.get(item_key), leaf), "kind": kind})
    return metrics


def metric_map(metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {row["metric"]: row for row in metrics}


def verdict(current: dict[str, dict[str, Any]]) -> list[str]:
    out = []
    exact_count = as_num(current.get("match_5s_trade_count", {}).get("value")) or 0
    taker_exact = as_num(current.get("match_5s_taker_exact_rate", {}).get("value"))
    maker_exact = as_num(current.get("match_5s_maker_exact_rate", {}).get("value"))
    balanced_closed = as_num(current.get("schedule_balanced_default_closed", {}).get("value"))
    tail_closed = as_num(current.get("schedule_tail_30_90_70_95_closed", {}).get("value"))
    latest_pair = as_num(current.get("latest_pair_cost_p50", {}).get("value"))

    if exact_count >= 300 and taker_exact is not None and taker_exact >= 0.95:
        out.append("execution_proxy: taker-dominant overlap remains supported")
    elif exact_count >= 300 and maker_exact is not None and maker_exact >= 0.25:
        out.append("execution_proxy: maker-like share increased materially; re-open maker-first hypothesis")
    else:
        out.append("execution_proxy: insufficient exact-match coverage or mixed signal")

    if balanced_closed is not None and tail_closed is not None and max(balanced_closed, tail_closed) >= 0.75:
        out.append("bounded_taker: causal staged controller remains viable but residual repair is still required")
    else:
        out.append("bounded_taker: causal staged controller weak; require stronger gate or L2/repair model")

    if latest_pair is not None and latest_pair < 0.95:
        out.append("xuan_regime: latest pair-cost distribution remains strong")
    elif latest_pair is not None:
        out.append("xuan_regime: latest pair-cost edge weakened versus target")
    else:
        out.append("xuan_regime: latest pair-cost unavailable")
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan Research Delta Report",
        "",
        f"- generated_at_utc: `{report['generated_at_utc']}`",
        f"- current_run_dir: `{report['current_run_dir']}`",
        "",
        "## Verdict",
        "",
    ]
    for item in report["verdict"]:
        lines.append(f"- {item}")
    lines.extend(["", "## Metrics", "", "| metric | baseline | current | delta |", "|---|---:|---:|---:|"])
    for row in report["comparison_rows"]:
        kind = row.get("kind")
        if kind == "pct":
            baseline = pct(row["baseline"])
            current = pct(row["current"])
            d = row["delta"]
            delta_text = "N/A" if d is None else f"{d * 100:+.2f}pct"
        elif kind == "text":
            baseline = val(row["baseline"])
            current = val(row["current"])
            delta_text = "N/A"
        else:
            baseline = val(row["baseline"])
            current = val(row["current"])
            d = row["delta"]
            delta_text = "N/A" if d is None else f"{d:+.6g}"
        lines.append(f"| {row['metric']} | {baseline} | {current} | {delta_text} |")
    lines.extend(
        [
            "",
            "## Interpretation Rules",
            "",
            "- `best-in-window` opportunity is not used here; causal threshold/schedule metrics are the implementable view.",
            "- `taker_like_buy_rate_exact >= 95%` with enough exact matches keeps maker-only downgraded.",
            "- staged close rate below 90% means the remaining gap is repair/L2/open-gate quality, not just first-leg timing.",
        ]
    )
    return "\n".join(lines) + "\n"


def load_bundle(paths: dict[str, str]) -> dict[str, dict[str, Any] | None]:
    return {name: load_json(path) for name, path in paths.items()}


def run_paths(run_dir: Path) -> dict[str, str]:
    return {name: str(run_dir / rel) for name, rel in RUN_PATHS.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current-run-dir", required=True)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--baseline-tranche", default=BASELINE_PATHS["tranche"])
    parser.add_argument("--baseline-cycle-gate", default=BASELINE_PATHS["cycle_gate"])
    parser.add_argument("--baseline-match", default=BASELINE_PATHS["match"])
    parser.add_argument("--baseline-threshold-wait30", default=BASELINE_PATHS["threshold_wait30"])
    parser.add_argument("--baseline-threshold-wait50", default=BASELINE_PATHS["threshold_wait50"])
    parser.add_argument("--baseline-threshold-wait70", default=BASELINE_PATHS["threshold_wait70"])
    parser.add_argument("--baseline-schedule", default=BASELINE_PATHS["schedule"])
    args = parser.parse_args()

    baseline_paths = {
        "tranche": args.baseline_tranche,
        "cycle_gate": args.baseline_cycle_gate,
        "match": args.baseline_match,
        "threshold_wait30": args.baseline_threshold_wait30,
        "threshold_wait50": args.baseline_threshold_wait50,
        "threshold_wait70": args.baseline_threshold_wait70,
        "schedule": args.baseline_schedule,
    }
    current_run_dir = Path(args.current_run_dir)
    baseline_metrics = metric_map(extract_metrics(load_bundle(baseline_paths)))
    current_metrics = metric_map(extract_metrics(load_bundle(run_paths(current_run_dir))))
    comparison_rows = []
    for name in sorted(set(baseline_metrics) | set(current_metrics)):
        b = baseline_metrics.get(name, {})
        c = current_metrics.get(name, {})
        comparison_rows.append(metric_row(name, c.get("value"), b.get("value"), c.get("kind") or b.get("kind") or "num"))

    output_dir = Path(args.output_dir) if args.output_dir else current_run_dir / "delta_vs_baseline"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "current_run_dir": str(current_run_dir.resolve()),
        "baseline_paths": baseline_paths,
        "current_paths": run_paths(current_run_dir),
        "verdict": verdict(current_metrics),
        "comparison_rows": comparison_rows,
    }
    (output_dir / "xuan_research_delta_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "xuan_research_delta_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "metrics": len(comparison_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

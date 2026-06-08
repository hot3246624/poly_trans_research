#!/usr/bin/env python3
"""Find pre-trade features that predict xuan's post-fill repricing label.

Input is `xuan_queue_timing_rows.csv`, typically filtered to the mid-price edge
cohort. The label is ex-post:

    shift_1000_bid_gte_price_in_top5 == True

This is not a live signal by itself. The goal is to rank open-time buckets and
simple rules that could become a live detector after fillability validation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


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
    w = pos - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
    }


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def bucket_num(value: float | None, cuts: list[tuple[float, str]], last: str) -> str:
    if value is None:
        return "missing"
    for threshold, label in cuts:
        if value < threshold:
            return label
    return last


def l2_edge(row: dict[str, Any]) -> float | None:
    first_l2 = as_float(row.get("first_l2_vwap"))
    first = as_float(row.get("first_price"))
    if first_l2 is None or first is None:
        return None
    return first_l2 - first


def label(row: dict[str, Any]) -> bool:
    return row.get("shift_1000_bid_gte_price_in_top5") == "True" or row.get("shift_1000_bid_gte_price_in_top5") is True


def first_winner(row: dict[str, Any]) -> bool:
    return row.get("first_is_winner") == "True" or row.get("first_is_winner") is True


def bool_field(row: dict[str, Any], field: str) -> bool:
    return row.get(field) == "True" or row.get(field) is True


def bucket(row: dict[str, Any], feature: str) -> str:
    if feature == "l2_edge":
        return bucket_num(l2_edge(row), [(-0.01, "<-1c"), (0.0, "-1c..0"), (0.005, "0..0.5c"), (0.03, "0.5c..3c")], ">=3c")
    if feature == "first_offset":
        return bucket_num(as_float(row.get("first_offset_s")), [(30, "0-30"), (60, "30-60"), (120, "60-120"), (180, "120-180"), (240, "180-240")], "240+")
    if feature == "first_price":
        return bucket_num(as_float(row.get("first_price")), [(0.45, "40-45"), (0.50, "45-50"), (0.55, "50-55")], ">=55")
    if feature == "first_spread":
        return bucket_num(as_float(row.get("first_side_l1_spread_ticks")), [(1.01, "<=1"), (2.01, "1-2"), (3.01, "2-3")], ">3")
    if feature == "recent_same_minus_opp_buy":
        return bucket_num(as_float(row.get("recent_same_minus_opp_buy_size_15s")), [(-100, "<-100"), (-20, "-100..-20"), (20, "-20..20"), (100, "20..100")], ">=100")
    if feature == "first_side":
        return str(row.get("first_side") or "missing")
    if feature == "first_ts_source":
        return str(row.get("first_ts_source") or "missing")
    return "missing"


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    pair_costs = [as_float(row.get("observed_pair_cost")) for row in rows]
    return {
        "n": n,
        "post_plus_1s_bid_gte_rate": rate(sum(1 for row in rows if label(row)), n),
        "first_winner_rate": rate(sum(1 for row in rows if first_winner(row)), n),
        "slow_profit_rate": rate(sum(1 for row in rows if row.get("path_label") == "slow_profit_lt95"), n),
        "slow_bad_rate": rate(sum(1 for row in rows if row.get("path_label") == "slow_bad_ge95"), n),
        "pair_cost": summarize(pair_costs),
        "first_size": summarize([as_float(row.get("size")) for row in rows]),
    }


def bucket_rows(rows: list[dict[str, Any]], min_n: int) -> list[dict[str, Any]]:
    features = [
        "l2_edge",
        "first_offset",
        "first_price",
        "first_spread",
        "recent_same_minus_opp_buy",
        "first_side",
        "first_ts_source",
    ]
    base = compact(rows)
    base_rate = float(base["post_plus_1s_bid_gte_rate"] or 0.0)
    out = []
    for feature in features:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[bucket(row, feature)].append(row)
        for bucket_name, xs in groups.items():
            if len(xs) < min_n:
                continue
            item = compact(xs)
            item.update(
                {
                    "feature": feature,
                    "bucket": bucket_name,
                    "selected_rate": rate(len(xs), len(rows)),
                    "label_lift": round(float(item["post_plus_1s_bid_gte_rate"] or 0.0) - base_rate, 6),
                }
            )
            out.append(item)
    out.sort(
        key=lambda item: (
            float(item["post_plus_1s_bid_gte_rate"] or 0.0),
            -float(item["pair_cost"]["p50"] or 9.0),
            int(item["n"]),
        ),
        reverse=True,
    )
    return out


def rule_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rules = rule_defs()
    out = []
    for name, pred in rules:
        xs = [row for row in rows if pred(row)]
        item = compact(xs)
        item["rule"] = name
        item["selected_rate"] = rate(len(xs), len(rows))
        out.append(item)
    out.sort(
        key=lambda item: (
            float(item["post_plus_1s_bid_gte_rate"] or 0.0),
            -float(item["pair_cost"]["p50"] or 9.0),
            int(item["n"]),
        ),
        reverse=True,
    )
    return out


def rule_defs() -> list[tuple[str, Any]]:
    return [
        ("all", lambda _row: True),
        ("l2_edge_ge_3c", lambda row: (l2_edge(row) or -9.0) >= 0.03),
        ("offset_30_60", lambda row: 30 <= (as_float(row.get("first_offset_s")) or -1.0) < 60),
        ("spread_le_1", lambda row: (as_float(row.get("first_side_l1_spread_ticks")) or 99.0) <= 1.0),
        (
            "l2_edge_ge_3c_and_spread_le_1",
            lambda row: (l2_edge(row) or -9.0) >= 0.03
            and (as_float(row.get("first_side_l1_spread_ticks")) or 99.0) <= 1.0,
        ),
        (
            "l2_edge_ge_3c_and_offset_30_60",
            lambda row: (l2_edge(row) or -9.0) >= 0.03
            and 30 <= (as_float(row.get("first_offset_s")) or -1.0) < 60,
        ),
        (
            "l2_edge_ge_3c_and_offset_lt60_and_spread_le_1",
            lambda row: (l2_edge(row) or -9.0) >= 0.03
            and (as_float(row.get("first_offset_s")) or 999.0) < 60
            and (as_float(row.get("first_side_l1_spread_ticks")) or 99.0) <= 1.0,
        ),
        (
            "avoid_weak_mid",
            lambda row: not (
                120 <= (as_float(row.get("first_offset_s")) or -1.0) < 180
                or (as_float(row.get("first_price")) or 0.0) < 0.45
                or ((l2_edge(row) or -9.0) < 0.03)
            ),
        ),
    ]


def rule_day_rows(rows: list[dict[str, Any]], min_day_n: int) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    rules = rule_defs()
    days = sorted({str(row.get("day") or "missing") for row in rows})
    for day in days:
        day_rows = [row for row in rows if str(row.get("day") or "missing") == day]
        day_base = compact(day_rows)
        day_base_rate = float(day_base["post_plus_1s_bid_gte_rate"] or 0.0)
        day_base_pair = day_base["pair_cost"]["p50"]
        for name, pred in rules:
            xs = [row for row in day_rows if pred(row)]
            if len(xs) < min_day_n:
                continue
            item = compact(xs)
            item.update(
                {
                    "day": day,
                    "rule": name,
                    "day_total_n": len(day_rows),
                    "selected_rate": rate(len(xs), len(day_rows)),
                    "label_lift_vs_day": round(float(item["post_plus_1s_bid_gte_rate"] or 0.0) - day_base_rate, 6),
                    "pair_p50_delta_vs_day": (
                        round(float(item["pair_cost"]["p50"]) - float(day_base_pair), 6)
                        if item["pair_cost"]["p50"] is not None and day_base_pair is not None
                        else None
                    ),
                }
            )
            out.append(item)
    out.sort(key=lambda item: (item["rule"], item["day"]))
    return out


def transition_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cohorts = [
        ("all", lambda _row: True),
        ("l2_edge_ge_3c", lambda row: (l2_edge(row) or -9.0) >= 0.03),
        (
            "l2_edge_ge_3c_and_offset_lt60_and_spread_le_1",
            lambda row: (l2_edge(row) or -9.0) >= 0.03
            and (as_float(row.get("first_offset_s")) or 999.0) < 60
            and (as_float(row.get("first_side_l1_spread_ticks")) or 99.0) <= 1.0,
        ),
    ]
    states = [
        (
            "upcross_pre_minus1_false_post_plus1_true",
            lambda row: not bool_field(row, "shift_-1000_bid_gte_price_in_top5")
            and bool_field(row, "shift_1000_bid_gte_price_in_top5"),
        ),
        (
            "already_gte_pre_minus1_and_post_plus1",
            lambda row: bool_field(row, "shift_-1000_bid_gte_price_in_top5")
            and bool_field(row, "shift_1000_bid_gte_price_in_top5"),
        ),
        (
            "lost_pre_minus1_true_post_plus1_false",
            lambda row: bool_field(row, "shift_-1000_bid_gte_price_in_top5")
            and not bool_field(row, "shift_1000_bid_gte_price_in_top5"),
        ),
        (
            "never_gte_pre_minus1_false_post_plus1_false",
            lambda row: not bool_field(row, "shift_-1000_bid_gte_price_in_top5")
            and not bool_field(row, "shift_1000_bid_gte_price_in_top5"),
        ),
    ]
    out = []
    for cohort_name, cohort_pred in cohorts:
        xs = [row for row in rows if cohort_pred(row)]
        for state_name, state_pred in states:
            state_rows = [row for row in xs if state_pred(row)]
            item = compact(state_rows)
            item.update(
                {
                    "cohort": cohort_name,
                    "transition": state_name,
                    "cohort_n": len(xs),
                    "transition_rate": rate(len(state_rows), len(xs)),
                    "day_counts": dict(sorted({day: sum(1 for row in state_rows if row.get("day") == day) for day in {row.get("day") for row in state_rows}}.items())),
                }
            )
            out.append(item)
    return out
    out = []
    for name, pred in rules:
        xs = [row for row in rows if pred(row)]
        item = compact(xs)
        item["rule"] = name
        item["selected_rate"] = rate(len(xs), len(rows))
        out.append(item)
    out.sort(
        key=lambda item: (
            float(item["post_plus_1s_bid_gte_rate"] or 0.0),
            -float(item["pair_cost"]["p50"] or 9.0),
            int(item["n"]),
        ),
        reverse=True,
    )
    return out


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Xuan Post-Move Signal Report",
        "",
        "## Scope",
        "",
        f"- rows_csv: `{report['rows_csv']}`",
        "- Label: `shift_1000_bid_gte_price_in_top5`.",
        "- This is an ex-post research label. Do not use it directly in live logic.",
        "",
        "## Baseline",
        "",
    ]
    base = report["baseline"]
    for key in ("n", "post_plus_1s_bid_gte_rate", "first_winner_rate", "slow_profit_rate", "slow_bad_rate"):
        lines.append(f"- {key}: `{base[key]}`")
    lines.append(f"- pair_cost_p50: `{base['pair_cost']['p50']}`")
    lines.extend(
        [
            "",
            "## Rule Probes",
            "",
            "| rule | n | selected | post+1s gte | winner | slow_profit | slow_bad | pair p50 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["rules"]:
        lines.append(
            f"| {row['rule']} | {row['n']} | {row['selected_rate']} | "
            f"{row['post_plus_1s_bid_gte_rate']} | {row['first_winner_rate']} | "
            f"{row['slow_profit_rate']} | {row['slow_bad_rate']} | {row['pair_cost']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Top Buckets",
            "",
            "`first_ts_source` is diagnostic only; it is not a live-usable signal.",
            "",
            "| feature | bucket | n | selected | post+1s gte | lift | winner | pair p50 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["buckets"][:20]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['selected_rate']} | "
            f"{row['post_plus_1s_bid_gte_rate']} | {row['label_lift']} | "
            f"{row['first_winner_rate']} | {row['pair_cost']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Rule Stability By Day",
            "",
            "| day | rule | n | selected | post+1s gte | lift vs day | winner | pair p50 | pair p50 delta |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    focus_rules = {
        "all",
        "l2_edge_ge_3c",
        "l2_edge_ge_3c_and_spread_le_1",
        "l2_edge_ge_3c_and_offset_lt60_and_spread_le_1",
        "avoid_weak_mid",
    }
    for row in report["rule_days"]:
        if row["rule"] not in focus_rules:
            continue
        lines.append(
            f"| {row['day']} | {row['rule']} | {row['n']} | {row['selected_rate']} | "
            f"{row['post_plus_1s_bid_gte_rate']} | {row['label_lift_vs_day']} | "
            f"{row['first_winner_rate']} | {row['pair_cost']['p50']} | {row['pair_p50_delta_vs_day']} |"
        )
    lines.extend(
        [
            "",
            "## Pre/Post Transition Decomposition",
            "",
            "`upcross` is the live-learnable subset. `already_gte` is more likely queue/timestamp/synchronization edge.",
            "",
            "| cohort | transition | n | rate | winner | pair p50 |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in report["transitions"]:
        lines.append(
            f"| {row['cohort']} | {row['transition']} | {row['n']} | {row['transition_rate']} | "
            f"{row['first_winner_rate']} | {row['pair_cost']['p50']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows-csv", default="data/exports/xuan_queue_timing_0427_0501_midprice_edge/xuan_queue_timing_rows.csv")
    parser.add_argument("--output-dir", default="data/exports/xuan_post_move_signal_0427_0501_midprice_edge")
    parser.add_argument("--min-bucket-n", type=int, default=30)
    parser.add_argument("--min-day-n", type=int, default=10)
    args = parser.parse_args()

    rows = read_csv(Path(args.rows_csv))
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "rows_csv": str(Path(args.rows_csv).resolve()),
        "baseline": compact(rows),
        "rules": rule_rows(rows),
        "buckets": bucket_rows(rows, args.min_bucket_n),
        "rule_days": rule_day_rows(rows, args.min_day_n),
        "transitions": transition_rows(rows),
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_post_move_signal_buckets.csv", report["buckets"])
    write_csv(output_dir / "xuan_post_move_signal_rules.csv", report["rules"])
    write_csv(output_dir / "xuan_post_move_signal_rule_days.csv", report["rule_days"])
    write_csv(output_dir / "xuan_post_move_signal_transitions.csv", report["transitions"])
    (output_dir / "xuan_post_move_signal_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_post_move_signal_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

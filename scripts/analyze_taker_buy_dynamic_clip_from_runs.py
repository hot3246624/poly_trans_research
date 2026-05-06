#!/usr/bin/env python3
"""Compose dynamic clip policies from precomputed taker-BUY clip runs.

This is an execution-cost sanity check, not a replacement for a native dynamic
backtest. It only uses matching trigger rows from existing clip60/120/160 runs,
and falls back to clip60 when a larger clip would not have passed the L2 gate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any


def parse_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def load_rows(path: Path) -> dict[tuple[str, int, str], dict[str, Any]]:
    rows: dict[tuple[str, int, str], dict[str, Any]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row: dict[str, Any] = dict(raw)
            row["trigger_ts_ms"] = int(float(row["trigger_ts_ms"]))
            for key in ["offset_s", "trigger_price", "public_trade_price", "trigger_size", "l1_immediate_pair", "pnl", "clip"]:
                row[key] = parse_float(row.get(key))
            row["completion_fill"] = parse_bool(row.get("completion_fill"))
            row["first_is_winner"] = parse_bool(row.get("first_is_winner"))
            key = (str(row["condition_id"]), int(row["trigger_ts_ms"]), str(row["first_side"]))
            rows[key] = row
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnl = sum(float(row["pnl"] or 0.0) for row in rows)
    cost = sum(float(row["trigger_price"] or 0.0) * float(row["clip"] or 0.0) for row in rows)
    by_day: dict[str, float] = defaultdict(float)
    by_day_rows: dict[str, int] = defaultdict(int)
    for row in rows:
        by_day[str(row["day"])] += float(row["pnl"] or 0.0)
        by_day_rows[str(row["day"])] += 1
    residual = [row for row in rows if not row["completion_fill"]]
    return {
        "rows": len(rows),
        "pnl": round(pnl, 6),
        "roi_on_first_cost": round(pnl / cost, 6) if cost else None,
        "closed_rate": round(sum(1 for row in rows if row["completion_fill"]) / len(rows), 6) if rows else None,
        "first_winner_rate": round(sum(1 for row in rows if row["first_is_winner"]) / len(rows), 6) if rows else None,
        "residual": len(residual),
        "residual_winner_rate": round(sum(1 for row in residual if row["first_is_winner"]) / len(residual), 6) if residual else None,
        "negative_days": [day for day, value in sorted(by_day.items()) if value < 0],
        "min_day_pnl": round(min(by_day.values()), 6) if by_day else None,
        "by_day_pnl": {day: round(by_day[day], 6) for day in sorted(by_day)},
        "by_day_rows": {day: by_day_rows[day] for day in sorted(by_day_rows)},
    }


def choose_clip(row: dict[str, Any], policy: str) -> int:
    l1_pair = float(row["l1_immediate_pair"] or 99.0)
    offset = float(row["offset_s"] or 0.0)
    if policy == "base60":
        return 60
    if policy == "l1_le_098_up120_else60":
        return 120 if l1_pair <= 0.98 else 60
    if policy == "l1_le_098_up160_else60":
        return 160 if l1_pair <= 0.98 else 60
    if policy == "offset_ge_60_up160_else60":
        return 160 if offset >= 60 else 60
    if policy == "offset_ge_60_and_l1_le_098_up160_else60":
        return 160 if offset >= 60 and l1_pair <= 0.98 else 60
    if policy == "offset_ge_60_or_l1_le_098_up160_else60":
        return 160 if offset >= 60 or l1_pair <= 0.98 else 60
    raise ValueError(f"unknown policy: {policy}")


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# Taker BUY Dynamic Clip Audit",
        "",
        "| policy | rows | fallback | pnl | ROI | min day pnl | closed | first winner | residual winner | negative days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in report["policies"]:
        lines.append(
            f"| `{row['policy']}` | {row['rows']} | {row['fallback_to_clip60']} | {row['pnl']} | "
            f"{row['roi_on_first_cost']} | {row['min_day_pnl']} | {row['closed_rate']} | "
            f"{row['first_winner_rate']} | {row['residual_winner_rate']} | {', '.join(row['negative_days']) or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `fallback_to_clip60` means the larger-clip run did not have the same trigger row, usually because larger L2 size broke the first-leg or immediate-pair gate.",
            "- Policies with no negative days and materially higher min-day PnL than base60 are sizing candidates for a native dynamic backtest.",
            "- This audit does not prove live fillability; it only checks whether up-clipping is directionally safe in replay rows already known to pass the core gate.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--clip60-rows", type=Path, required=True)
    parser.add_argument("--clip120-rows", type=Path, required=True)
    parser.add_argument("--clip160-rows", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, default=Path("data/exports/taker_buy_dynamic_clip_audit"))
    args = parser.parse_args()

    by_clip = {
        60: load_rows(args.clip60_rows),
        120: load_rows(args.clip120_rows),
        160: load_rows(args.clip160_rows),
    }
    base = list(by_clip[60].values())
    policies = [
        "base60",
        "l1_le_098_up120_else60",
        "l1_le_098_up160_else60",
        "offset_ge_60_up160_else60",
        "offset_ge_60_and_l1_le_098_up160_else60",
        "offset_ge_60_or_l1_le_098_up160_else60",
    ]
    out_rows = []
    for policy in policies:
        selected = []
        fallback = 0
        for base_row in base:
            key = (str(base_row["condition_id"]), int(base_row["trigger_ts_ms"]), str(base_row["first_side"]))
            clip = choose_clip(base_row, policy)
            row = by_clip[clip].get(key)
            if row is None:
                row = base_row
                fallback += 1
            selected.append(row)
        out_rows.append({"policy": policy, "fallback_to_clip60": fallback, **summarize(selected)})

    report = {
        "inputs": {
            "clip60_rows": str(args.clip60_rows),
            "clip120_rows": str(args.clip120_rows),
            "clip160_rows": str(args.clip160_rows),
        },
        "policies": out_rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "taker_buy_dynamic_clip_audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (args.output_dir / "taker_buy_dynamic_clip_audit.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(args.output_dir), "policies": len(out_rows)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

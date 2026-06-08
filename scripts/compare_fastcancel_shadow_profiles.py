#!/usr/bin/env python3
"""Compare Fast-Cancel shadow profiles against each other and xuan truth.

Inputs are replay-derived shadow reports. The script does not read raw data and
does not touch replay SQLite DBs; it only summarizes existing JSON artifacts.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        action="append",
        required=True,
        help="Repeatable NAME=fastcancel_shadow_report.json.",
    )
    parser.add_argument("--xuan-summary", type=Path, help="Optional xuan_market_pnl_truth_summary.json.")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return json.load(f)


def parse_profile_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise SystemExit("--profile must use NAME=PATH")
    name, path = value.split("=", 1)
    name = name.strip()
    path = path.strip()
    if not name or not path:
        raise SystemExit("--profile must use non-empty NAME=PATH")
    return name, Path(path)


def fnum(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def daily_positive_count(daily: dict[str, dict[str, Any]], key: str) -> str:
    values = [float(row.get(key, 0.0)) for _, row in sorted(daily.items())]
    return f"{sum(1 for value in values if value > 0)}/{len(values)}" if values else "0/0"


def min_daily_value(daily: dict[str, dict[str, Any]], key: str) -> float | None:
    values = [float(row.get(key, 0.0)) for row in daily.values()]
    return round(min(values), 6) if values else None


def summarize_profile(name: str, path: Path) -> dict[str, Any]:
    report = load_json(path)
    daily = report.get("daily", {})
    attempts_by_day = {day: int(row.get("attempts", 0)) for day, row in sorted(daily.items())}
    fills_by_day = {day: int(row.get("proxy_fills", 0)) for day, row in sorted(daily.items())}
    l2_2c = fnum(report.get("shadow_pnl_with_2c_friction"))
    l2 = fnum(report.get("shadow_pnl_l2"))
    raw = fnum(report.get("raw_replay_pnl"))
    return {
        "name": name,
        "path": str(path),
        "episodes": int(report.get("episodes", 0)),
        "proxy_filled": int(report.get("proxy_filled", 0)),
        "proxy_fill_rate": fnum(report.get("proxy_fill_rate")),
        "raw_replay_pnl": raw,
        "shadow_pnl_l2": l2,
        "shadow_pnl_with_2c_friction": l2_2c,
        "positive_days_l2_2c": daily_positive_count(daily, "shadow_pnl_with_2c_friction"),
        "min_day_l2_2c": min_daily_value(daily, "shadow_pnl_with_2c_friction"),
        "attempts_by_day": attempts_by_day,
        "fills_by_day": fills_by_day,
        "min_attempts_per_day": min(attempts_by_day.values()) if attempts_by_day else 0,
        "clip_counts": report.get("clip_counts", {}),
        "path_counts": report.get("path_counts", {}),
        "gate_results": report.get("gate_results", {}),
        "verdict": report.get("verdict", {}),
    }


def summarize_xuan(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    report = load_json(path)
    summary = report["summary"]
    by_day = report.get("bucket_tables", {}).get("day", {})
    return {
        "path": str(path),
        "market_count": int(summary["market_count"]),
        "trade_count": int(summary["trade_count"]),
        "trade_pnl": float(summary["trade_pnl"]),
        "roi_on_cost": float(summary["roi_on_cost"]),
        "weighted_pair_cost": float(summary["weighted_pair_cost"]),
        "profitable_pair_market_rate": float(summary["profitable_pair_market_rate"]),
        "daily_trade_pnl": {day: round(float(item["trade_pnl"]), 6) for day, item in sorted(by_day.items())},
    }


def fmt_money(value: Any) -> str:
    if value is None:
        return "N/A"
    return f"${float(value):,.2f}"


def fmt_pct(value: Any) -> str:
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return "N/A"
    return f"{float(value) * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Fast-Cancel Shadow Profile Comparison",
        "",
        "## Profiles",
        "",
        "| profile | episodes | proxy fills | fill rate | L2 PnL | L2 +2c PnL | +2c positive days | min +2c day | min attempts/day | replay-ready | enforce-ready |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for item in report["profiles"]:
        verdict = item["verdict"]
        lines.append(
            "| "
            + " | ".join(
                [
                    item["name"],
                    str(item["episodes"]),
                    str(item["proxy_filled"]),
                    fmt_pct(item["proxy_fill_rate"]),
                    fmt_money(item["shadow_pnl_l2"]),
                    fmt_money(item["shadow_pnl_with_2c_friction"]),
                    item["positive_days_l2_2c"],
                    fmt_money(item["min_day_l2_2c"]),
                    str(item["min_attempts_per_day"]),
                    str(verdict.get("replay_ready_for_live_shadow")),
                    str(verdict.get("promote_to_enforce_discussion")),
                ]
            )
            + " |"
        )

    xuan = report.get("xuan")
    if xuan:
        lines.extend(
            [
                "",
                "## Xuan Target",
                "",
                f"- markets: `{xuan['market_count']}`",
                f"- trades: `{xuan['trade_count']}`",
                f"- trade_pnl: `{fmt_money(xuan['trade_pnl'])}`",
                f"- roi_on_cost: `{fmt_pct(xuan['roi_on_cost'])}`",
                f"- weighted_pair_cost: `{xuan['weighted_pair_cost']:.6f}`",
                f"- profitable_pair_market_rate: `{fmt_pct(xuan['profitable_pair_market_rate'])}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `core` is the cleaner low-frequency safety kernel: robust across days and friction, but insufficient frequency to beat xuan alone.",
            "- `expansion` has better frequency and higher replay PnL, but depends more on public queue proxy quality and cannot be enforced without own maker fill truth.",
            "- Neither profile is comparable to xuan absolute PnL yet because our profiles are shadow proxy fills, not actual queue-position execution.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    profiles = [summarize_profile(name, path) for name, path in map(parse_profile_spec, args.profile)]
    report = {
        "profiles": profiles,
        "xuan": summarize_xuan(args.xuan_summary),
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    args.output_md.write_text(render_markdown(report))
    print(json.dumps({"profiles": len(profiles), "output_json": str(args.output_json), "output_md": str(args.output_md)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Build an explainable xuan winner-proxy gate from replay-derived truth.

This is a research script. It uses:

- xuan L2 completion curve rows for tranche timing and market-side features;
- xuan winner-path tranches for ex-post winner labels;
- replay SQLite in read-only mode for pre-open market-flow features.

It does not read raw data and does not remap Up/Down labels. Direction fields
must already be normalized to YES/NO by replay.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable


TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")


def as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    try:
        if value in (None, ""):
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if v is not None)
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
    return round(xs[lo] * (1.0 - w) + xs[hi] * w, 6)


def summarize(values: list[float | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
    }


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def join_key(row: dict[str, Any]) -> tuple[Any, ...]:
    first_tx = row.get("first_tx") or ""
    second_tx = row.get("second_tx") or ""
    if first_tx and second_tx:
        return ("tx", first_tx, second_tx)
    return (
        "fields",
        row.get("condition_id"),
        row.get("first_ts_ms"),
        row.get("first_side"),
        row.get("size"),
        row.get("observed_pair_delay_s") or row.get("pair_delay_s"),
    )


def load_joined_rows(completion_rows_path: Path, winner_tranches_path: Path) -> list[dict[str, Any]]:
    completion_rows = read_csv(completion_rows_path)
    winner_rows = read_csv(winner_tranches_path)
    winner_by_key = {join_key(row): row for row in winner_rows}
    out: list[dict[str, Any]] = []
    for row in completion_rows:
        winner = winner_by_key.get(join_key(row))
        if not winner:
            continue
        merged = dict(row)
        merged["winner_side"] = winner.get("winner_side")
        merged["first_is_winner"] = winner.get("first_is_winner")
        merged["second_is_winner"] = winner.get("second_is_winner")
        merged["path_label"] = winner.get("path_label")
        merged["surplus_usdc"] = winner.get("surplus_usdc")
        merged["pair_surplus"] = winner.get("pair_surplus")
        out.append(merged)
    return out


def day_from_ms(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).date().isoformat()


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def bucket_first_price(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_price"))
    if x is None:
        return "unknown"
    if x < 0.40:
        return "<0.40"
    if x < 0.50:
        return "0.40-0.50"
    if x < 0.55:
        return "0.50-0.55"
    if x < 0.70:
        return "0.55-0.70"
    return ">=0.70"


def bucket_high_side(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_price"))
    if x is None:
        return "unknown"
    if x < 0.50:
        return "low_side_<0.50"
    if x < 0.55:
        return "near_mid_0.50_0.55"
    if x < 0.70:
        return "high_side_0.55_0.70"
    return "very_high_side_>=0.70"


def bucket_offset(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_offset_s"))
    if x is None:
        return "unknown"
    if x < 30:
        return "000-030s"
    if x < 120:
        return "030-120s"
    if x < 240:
        return "120-240s"
    return "240-300s"


def bucket_size(row: dict[str, Any]) -> str:
    x = as_float(row.get("size"))
    if x is None:
        return "unknown"
    if x <= 80:
        return "<=80"
    if x <= 160:
        return "80-160"
    return ">160"


def bucket_l2_edge(row: dict[str, Any]) -> str:
    first = as_float(row.get("first_price"))
    vwap = as_float(row.get("first_l2_vwap"))
    if first is None or vwap is None:
        return "missing"
    diff = vwap - first
    if diff <= -0.01:
        return "<=-1c"
    if diff <= 0.0:
        return "-1c..0"
    if diff <= 0.01:
        return "0..+1c"
    if diff <= 0.03:
        return "+1c..+3c"
    return ">+3c"


def bucket_min_pair_30s(row: dict[str, Any]) -> str:
    x = as_float(row.get("min_pair_cost_30s"))
    if x is None:
        return "missing"
    if x <= 0.90:
        return "<=0.90"
    if x <= 0.95:
        return "0.90-0.95"
    if x <= 0.99:
        return "0.95-0.99"
    if x <= 1.01:
        return "0.99-1.01"
    return ">1.01"


def bucket_recent_net(row: dict[str, Any]) -> str:
    x = as_float(row.get("recent_same_minus_opp_buy_size_15s"))
    if x is None:
        return "missing"
    if x <= -100:
        return "opp_buy_lead_gt100"
    if x < -20:
        return "opp_buy_lead_20_100"
    if x <= 20:
        return "balanced"
    if x <= 100:
        return "same_buy_lead_20_100"
    return "same_buy_lead_gt100"


def bucket_recent_total(row: dict[str, Any]) -> str:
    x = as_float(row.get("recent_total_trade_count_15s"))
    if x is None:
        return "missing"
    if x == 0:
        return "0"
    if x <= 3:
        return "1-3"
    if x <= 8:
        return "4-8"
    return ">8"


def bucket_l1_spread(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_side_l1_spread_ticks"))
    if x is None:
        return "missing"
    if x <= 1:
        return "<=1"
    if x <= 3:
        return "2-3"
    return ">3"


def bucket_l1_ask_depth(row: dict[str, Any]) -> str:
    x = as_float(row.get("first_side_ask_sz"))
    if x is None:
        return "missing"
    if x <= 50:
        return "<=50"
    if x <= 150:
        return "50-150"
    if x <= 500:
        return "150-500"
    return ">500"


def first_l2_edge(row: dict[str, Any]) -> float | None:
    first = as_float(row.get("first_price"))
    vwap = as_float(row.get("first_l2_vwap"))
    if first is None or vwap is None:
        return None
    return vwap - first


def annotate_market_features(rows: list[dict[str, Any]], replay_root: Path) -> list[dict[str, Any]]:
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        ts = as_int(row.get("first_exec_ts_ms")) or as_int(row.get("first_ts_ms"))
        if ts is None:
            continue
        row["first_exec_ts_ms_resolved"] = ts
        by_day[day_from_ms(ts)].append(row)

    for day, xs in by_day.items():
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            for row in xs:
                row["feature_error"] = "missing_db"
            continue
        conn = connect_ro(db_path)
        try:
            for row in xs:
                condition_id = row["condition_id"]
                ts = int(row["first_exec_ts_ms_resolved"])
                first_side = row["first_side"]
                opp_side = opposite(first_side)

                l1 = conn.execute(
                    """
                    SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                           yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
                    FROM md_book_l1
                    WHERE condition_id = ? AND recv_ms <= ?
                    ORDER BY recv_ms DESC
                    LIMIT 1
                    """,
                    (condition_id, ts),
                ).fetchone()
                if l1:
                    row["l1_age_ms"] = ts - int(l1["recv_ms"])
                    for side in ("yes", "no"):
                        bid = as_float(l1[f"{side}_bid_px"])
                        ask = as_float(l1[f"{side}_ask_px"])
                        bid_sz = as_float(l1[f"{side}_bid_sz"])
                        ask_sz = as_float(l1[f"{side}_ask_sz"])
                        spread_ticks = round((ask - bid) * 100, 6) if bid is not None and ask is not None else None
                        prefix = "first_side" if side.upper() == first_side else "opposite_side"
                        row[f"{prefix}_bid_px"] = bid
                        row[f"{prefix}_ask_px"] = ask
                        row[f"{prefix}_bid_sz"] = bid_sz
                        row[f"{prefix}_ask_sz"] = ask_sz
                        row[f"{prefix}_l1_spread_ticks"] = spread_ticks

                trade_rows = conn.execute(
                    """
                    SELECT market_side, taker_side, price, size
                    FROM md_trades
                    WHERE condition_id = ?
                      AND trade_ts_ms IS NOT NULL
                      AND trade_ts_ms >= ?
                      AND trade_ts_ms < ?
                    """,
                    (condition_id, ts - 15_000, ts),
                ).fetchall()
                counts = Counter()
                sizes = Counter()
                for trade in trade_rows:
                    side = trade["market_side"]
                    taker = trade["taker_side"]
                    size = float(trade["size"] or 0.0)
                    key = "same" if side == first_side else "opp" if side == opp_side else "other"
                    counts[key] += 1
                    sizes[key] += size
                    if taker:
                        counts[f"{key}_{taker}"] += 1
                        sizes[f"{key}_{taker}"] += size
                row["recent_total_trade_count_15s"] = len(trade_rows)
                row["recent_same_trade_count_15s"] = counts["same"]
                row["recent_opp_trade_count_15s"] = counts["opp"]
                row["recent_same_buy_size_15s"] = round(sizes["same_BUY"], 6)
                row["recent_opp_buy_size_15s"] = round(sizes["opp_BUY"], 6)
                row["recent_same_minus_opp_buy_size_15s"] = round(sizes["same_BUY"] - sizes["opp_BUY"], 6)
        finally:
            conn.close()
    return rows


def candidate_policy_table(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def price(row: dict[str, Any]) -> float | None:
        return as_float(row.get("first_price"))

    def size(row: dict[str, Any]) -> float | None:
        return as_float(row.get("size"))

    def min_pair_30(row: dict[str, Any]) -> float | None:
        return as_float(row.get("min_pair_cost_30s"))

    policies: list[tuple[str, str, Callable[[dict[str, Any]], bool]]] = [
        (
            "baseline_all_xuan_tranches",
            "All reconstructed xuan tranches.",
            lambda row: True,
        ),
        (
            "open_block_negative_l2_edge",
            "Open-time block if first_l2_vwap - intended_first_price <= -1c.",
            lambda row: first_l2_edge(row) is None or first_l2_edge(row) > -0.01,
        ),
        (
            "open_positive_l2_edge_only",
            "Open only when first_l2_vwap - intended_first_price > 3c.",
            lambda row: (first_l2_edge(row) or -999.0) > 0.03,
        ),
        (
            "open_positive_l2_edge_or_mid_large",
            "Open when L2 edge > 3c, or 0.50 <= price < 0.55 with size > 160.",
            lambda row: (first_l2_edge(row) or -999.0) > 0.03
            or (
                price(row) is not None
                and size(row) is not None
                and 0.50 <= price(row) < 0.55
                and size(row) > 160
            ),
        ),
        (
            "open_block_low_price_without_edge",
            "Block first_price < 0.40 unless L2 edge > 3c.",
            lambda row: not (price(row) is not None and price(row) < 0.40 and (first_l2_edge(row) or -999.0) <= 0.03),
        ),
        (
            "continue_after30_cheap_window_strong",
            "Post-open slow continuation only if min_pair_cost_30s <= 0.90.",
            lambda row: min_pair_30(row) is not None and min_pair_30(row) <= 0.90,
        ),
        (
            "continue_after30_cheap_window_medium",
            "Post-open slow continuation only if min_pair_cost_30s <= 0.95.",
            lambda row: min_pair_30(row) is not None and min_pair_30(row) <= 0.95,
        ),
        (
            "force_repair_after30_no_cheap_window",
            "Rows that should not be slow-waited: min_pair_cost_30s > 0.99 or missing.",
            lambda row: min_pair_30(row) is None or min_pair_30(row) > 0.99,
        ),
    ]
    out = []
    total = len(rows)
    for name, description, pred in policies:
        selected = [row for row in rows if pred(row)]
        item = compact(selected)
        item["policy"] = name
        item["description"] = description
        item["selected_rate"] = rate(len(selected), total)
        out.append(item)
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    first_winner = sum(1 for row in rows if str(row.get("first_is_winner")) == "True")
    path_counts = Counter(str(row.get("path_label")) for row in rows)
    surplus = sum(as_float(row.get("surplus_usdc")) or 0.0 for row in rows)
    total_size = sum(as_float(row.get("size")) or 0.0 for row in rows)
    return {
        "n": n,
        "first_winner_rate": rate(first_winner, n),
        "fast_control_rate": rate(path_counts["fast_control"], n),
        "slow_profit_rate": rate(path_counts["slow_profit_lt95"], n),
        "slow_bad_rate": rate(path_counts["slow_bad_ge95"], n),
        "slow_profit_to_bad_ratio": rate(path_counts["slow_profit_lt95"], path_counts["slow_bad_ge95"]),
        "surplus_usdc": round(surplus, 6),
        "surplus_per_tranche": round(surplus / n, 6) if n else None,
        "surplus_per_size": round(surplus / total_size, 6) if total_size else None,
        "pair_cost": summarize([as_float(row.get("observed_pair_cost")) for row in rows]),
        "pair_surplus": summarize([1.0 - (as_float(row.get("observed_pair_cost")) or 0.0) for row in rows]),
        "pair_delay_s": summarize([as_float(row.get("observed_pair_delay_s")) for row in rows]),
        "min_pair_cost_30s": summarize([as_float(row.get("min_pair_cost_30s")) for row in rows]),
        "size": summarize([as_float(row.get("size")) for row in rows]),
        "path_counts": dict(sorted(path_counts.items())),
    }


def bucket_table(
    rows: list[dict[str, Any]],
    name: str,
    bucket_fn: Callable[[dict[str, Any]], str],
    min_n: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[bucket_fn(row)].append(row)
    out = []
    baseline = compact(rows)
    baseline_surplus = baseline["surplus_per_tranche"] or 0.0
    baseline_winner = baseline["first_winner_rate"] or 0.0
    for bucket, xs in sorted(grouped.items()):
        if len(xs) < min_n:
            continue
        item = compact(xs)
        item["feature"] = name
        item["bucket"] = bucket
        item["winner_lift"] = round((item["first_winner_rate"] or 0.0) - baseline_winner, 6)
        item["surplus_lift_per_tranche"] = round((item["surplus_per_tranche"] or 0.0) - baseline_surplus, 6)
        out.append(item)
    return out


def combined_rule_table(
    rows: list[dict[str, Any]],
    feature_fns: dict[str, Callable[[dict[str, Any]], str]],
    min_n: int,
    include_post30: bool,
) -> list[dict[str, Any]]:
    allowed = dict(feature_fns)
    if not include_post30:
        allowed.pop("min_pair_cost_30s", None)
    baseline = compact(rows)
    baseline_surplus = baseline["surplus_per_tranche"] or 0.0
    baseline_winner = baseline["first_winner_rate"] or 0.0
    out = []
    names = list(allowed)
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
            for row in rows:
                groups[(allowed[left](row), allowed[right](row))].append(row)
            for (left_bucket, right_bucket), xs in groups.items():
                if len(xs) < min_n:
                    continue
                item = compact(xs)
                item["feature"] = f"{left}+{right}"
                item["bucket"] = f"{left_bucket} & {right_bucket}"
                item["winner_lift"] = round((item["first_winner_rate"] or 0.0) - baseline_winner, 6)
                item["surplus_lift_per_tranche"] = round((item["surplus_per_tranche"] or 0.0) - baseline_surplus, 6)
                out.append(item)
    out.sort(
        key=lambda item: (
            item["surplus_per_tranche"] if item["surplus_per_tranche"] is not None else -999,
            item["first_winner_rate"] if item["first_winner_rate"] is not None else -999,
            item["n"],
        ),
        reverse=True,
    )
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_report(report: dict[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# Xuan Winner Proxy Gate Analysis",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- completion_rows: `{report['inputs']['completion_rows']}`",
        f"- winner_tranches: `{report['inputs']['winner_tranches']}`",
        "- SQLite is opened read-only; no raw data is used.",
        "- `winner_side` is ex-post truth and is used only to score live-visible proxies.",
        "",
        "## Baseline",
        "",
        f"- rows: `{summary['n']}`",
        f"- first_winner_rate: `{summary['first_winner_rate']}`",
        f"- fast_control_rate: `{summary['fast_control_rate']}`",
        f"- slow_profit_rate: `{summary['slow_profit_rate']}`",
        f"- slow_bad_rate: `{summary['slow_bad_rate']}`",
        f"- surplus_per_tranche: `{summary['surplus_per_tranche']}`",
        f"- surplus_per_size: `{summary['surplus_per_size']}`",
        "",
        "## Strong Single-Feature Buckets",
        "",
        "| feature | bucket | n | first_winner | slow_profit | slow_bad | surplus/tranche | surplus/size | winner_lift | surplus_lift |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in report["top_single_buckets"][:20]:
        lines.append(
                f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['first_winner_rate']} | "
                f"{row['slow_profit_rate']} | {row['slow_bad_rate']} | {row['surplus_per_tranche']} | "
                f"{row['surplus_per_size']} | {row['winner_lift']} | {row['surplus_lift_per_tranche']} |"
            )
    lines.extend(
        [
            "",
            "## Strong Open-Time Two-Feature Buckets",
            "",
            "| feature | bucket | n | first_winner | slow_profit | slow_bad | surplus/tranche | surplus/size | winner_lift | surplus_lift |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["top_open_time_rules"][:25]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['first_winner_rate']} | "
            f"{row['slow_profit_rate']} | {row['slow_bad_rate']} | {row['surplus_per_tranche']} | "
            f"{row['surplus_per_size']} | {row['winner_lift']} | {row['surplus_lift_per_tranche']} |"
        )
    lines.extend(
        [
            "",
            "## Strong Continuation Buckets",
            "",
            "| feature | bucket | n | first_winner | slow_profit | slow_bad | surplus/tranche | surplus/size | winner_lift | surplus_lift |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["top_continuation_rules"][:25]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['first_winner_rate']} | "
            f"{row['slow_profit_rate']} | {row['slow_bad_rate']} | {row['surplus_per_tranche']} | "
            f"{row['surplus_per_size']} | {row['winner_lift']} | {row['surplus_lift_per_tranche']} |"
        )
    lines.extend(
        [
            "",
            "## Candidate Policy Checks",
            "",
            "| policy | selected | selected_rate | first_winner | slow_profit | slow_bad | surplus/size | surplus/tranche |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["candidate_policies"]:
        lines.append(
            f"| {row['policy']} | {row['n']} | {row['selected_rate']} | {row['first_winner_rate']} | "
            f"{row['slow_profit_rate']} | {row['slow_bad_rate']} | {row['surplus_per_size']} | {row['surplus_per_tranche']} |"
        )
    lines.extend(
        [
            "",
            "## Implementation Implications",
            "",
            "- Do not use ex-post `winner_side` in live logic; translate only live-visible buckets into shadow gates.",
            "- Open gate should be judged by open-time features only: first price, offset, size, L1/L2 state, and recent public flow.",
            "- Slow continuation may use post-open evidence such as `min_pair_cost_30s`, because it is known after the first 30 seconds.",
            "- Buckets with high first-winner but negative surplus are not alpha gates; they are risk-control or clip-down candidates.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument(
        "--completion-rows",
        default="data/exports/xuan_research_runs/replay_20260502_full/xuan_l2_completion_curve_5d/xuan_l2_completion_curve_rows.csv",
    )
    parser.add_argument(
        "--winner-tranches",
        default="data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_path_5d/xuan_winner_path_tranches.csv",
    )
    parser.add_argument("--output-dir", default="data/exports/xuan_research_runs/replay_20260503_full/xuan_winner_proxy_gate_5d")
    parser.add_argument("--min-n", type=int, default=80)
    args = parser.parse_args()

    rows = load_joined_rows(Path(args.completion_rows), Path(args.winner_tranches))
    rows = [row for row in rows if (as_int(row.get("first_exec_ts_ms")) or as_int(row.get("first_ts_ms")) or 0) >= TRUSTED_START_MS]
    rows = annotate_market_features(rows, Path(args.replay_root))

    feature_fns: dict[str, Callable[[dict[str, Any]], str]] = {
        "first_price_bucket": bucket_first_price,
        "high_side_bucket": bucket_high_side,
        "offset_bucket": bucket_offset,
        "size_bucket": bucket_size,
        "first_l2_edge_bucket": bucket_l2_edge,
        "first_l1_spread_bucket": bucket_l1_spread,
        "first_l1_ask_depth_bucket": bucket_l1_ask_depth,
        "recent_net_flow_bucket": bucket_recent_net,
        "recent_total_trades_bucket": bucket_recent_total,
        "min_pair_cost_30s": bucket_min_pair_30s,
    }
    bucket_rows: list[dict[str, Any]] = []
    for name, fn in feature_fns.items():
        bucket_rows.extend(bucket_table(rows, name, fn, args.min_n))

    single_sorted = sorted(
        bucket_rows,
        key=lambda row: (
            row["surplus_per_tranche"] if row["surplus_per_tranche"] is not None else -999,
            row["first_winner_rate"] if row["first_winner_rate"] is not None else -999,
            row["n"],
        ),
        reverse=True,
    )
    open_rules = combined_rule_table(rows, feature_fns, args.min_n, include_post30=False)
    continuation_rules = combined_rule_table(rows, feature_fns, args.min_n, include_post30=True)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_winner_proxy_gate_rows.csv", rows)
    write_csv(output_dir / "xuan_winner_proxy_gate_buckets.csv", bucket_rows)
    write_csv(output_dir / "xuan_winner_proxy_gate_open_rules.csv", open_rules)
    write_csv(output_dir / "xuan_winner_proxy_gate_continuation_rules.csv", continuation_rules)

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "inputs": {
            "completion_rows": str(Path(args.completion_rows).resolve()),
            "winner_tranches": str(Path(args.winner_tranches).resolve()),
        },
        "summary": compact(rows),
        "candidate_policies": candidate_policy_table(rows),
        "top_single_buckets": single_sorted[:50],
        "top_open_time_rules": open_rules[:50],
        "top_continuation_rules": continuation_rules[:50],
        "outputs": {
            "rows_csv": str((output_dir / "xuan_winner_proxy_gate_rows.csv").resolve()),
            "buckets_csv": str((output_dir / "xuan_winner_proxy_gate_buckets.csv").resolve()),
            "open_rules_csv": str((output_dir / "xuan_winner_proxy_gate_open_rules.csv").resolve()),
            "continuation_rules_csv": str((output_dir / "xuan_winner_proxy_gate_continuation_rules.csv").resolve()),
            "summary_json": str((output_dir / "xuan_winner_proxy_gate_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_winner_proxy_gate_report.md").resolve()),
        },
    }
    (output_dir / "xuan_winner_proxy_gate_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "xuan_winner_proxy_gate_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

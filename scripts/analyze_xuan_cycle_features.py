#!/usr/bin/env python3
"""Join xuan inventory cycles with market replay features and derive a slow-improvement gate.

Inputs are public xuan cycle reconstructions plus read-only replay SQLite. This
script does not use raw capture files and does not modify replay DBs.
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
from typing import Any


DEFAULT_REPLAY_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1777274700000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_bool(value: str | bool | None) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def as_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    vals = [float(v) for v in values if v is not None]
    return {
        "count": len(vals),
        "avg": round(sum(vals) / len(vals), 6) if vals else None,
        "p10": percentile(vals, 10),
        "p25": percentile(vals, 25),
        "p50": percentile(vals, 50),
        "p75": percentile(vals, 75),
        "p90": percentile(vals, 90),
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def replay_db_map(root: Path, days: list[str]) -> dict[str, Path]:
    return {day: root / day / "crypto_5m.sqlite" for day in days if (root / day / "crypto_5m.sqlite").exists()}


def find_l1_before(conn: sqlite3.Connection, condition_id: str, ts_ms: int, max_age_ms: int) -> sqlite3.Row | None:
    row = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=? AND recv_ms <= ?
        ORDER BY recv_ms DESC
        LIMIT 1
        """,
        (condition_id, ts_ms),
    ).fetchone()
    if row is None or ts_ms - int(row["recv_ms"]) > max_age_ms:
        return None
    return row


def trades_before(
    conn: sqlite3.Connection,
    condition_id: str,
    start_ms: int,
    end_ms: int,
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT trade_ts_ms, market_side, taker_side, price, size
            FROM md_trades
            WHERE condition_id=?
              AND trade_ts_ms IS NOT NULL
              AND trade_ts_ms >= ?
              AND trade_ts_ms < ?
            ORDER BY trade_ts_ms ASC
            """,
            (condition_id, start_ms, end_ms),
        )
    )


def side_key(side: str, field: str) -> str:
    return f"{'yes' if side == 'YES' else 'no'}_{field}"


def opposite(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def bucket_offset(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 30:
        return "000_030s"
    if value < 60:
        return "030_060s"
    if value < 120:
        return "060_120s"
    if value < 180:
        return "120_180s"
    if value < 240:
        return "180_240s"
    return "240_300s"


def bucket_price(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.40:
        return "lt_0_40"
    if value < 0.55:
        return "0_40_0_55"
    if value < 0.70:
        return "0_55_0_70"
    if value < 0.85:
        return "0_70_0_85"
    return "gte_0_85"


def bucket_size(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 80:
        return "lt_80"
    if value < 140:
        return "080_140"
    if value < 220:
        return "140_220"
    return "gte_220"


def bucket_spread_ticks(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value <= 1:
        return "le_1"
    if value <= 2:
        return "le_2"
    if value <= 4:
        return "le_4"
    return "gt_4"


def bucket_pair_sum(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 0.98:
        return "lt_0_98"
    if value <= 1.02:
        return "0_98_1_02"
    if value <= 1.06:
        return "1_02_1_06"
    return "gt_1_06"


def bucket_book_size(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 50:
        return "lt_50"
    if value < 150:
        return "050_150"
    if value < 400:
        return "150_400"
    return "gte_400"


def bucket_trade_count(value: int | None) -> str:
    if value is None:
        return "unknown"
    if value == 0:
        return "0"
    if value <= 2:
        return "1_2"
    if value <= 5:
        return "3_5"
    return "gt_5"


def bucket_mid_skew(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < -0.10:
        return "lt_-0_10"
    if value < -0.03:
        return "-0_10_-0_03"
    if value <= 0.03:
        return "near_flat"
    if value <= 0.10:
        return "0_03_0_10"
    return "gt_0_10"


def load_cycles(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def add_l1_features(row: dict[str, Any], l1: sqlite3.Row | None, prev_l1: sqlite3.Row | None, ts_ms: int) -> None:
    first_side = row["first_side"]
    opp_side = opposite(first_side)
    row["l1_matched"] = bool(l1)
    row["book_age_ms"] = None
    row["first_bid_px"] = None
    row["first_ask_px"] = None
    row["opp_bid_px"] = None
    row["opp_ask_px"] = None
    row["first_spread_ticks"] = None
    row["opp_spread_ticks"] = None
    row["pair_ask_sum"] = None
    row["pair_bid_sum"] = None
    row["first_mid"] = None
    row["opp_mid"] = None
    row["first_mid_skew"] = None
    row["first_is_l1_high_side"] = "unknown"
    row["first_bid_sz"] = None
    row["first_ask_sz"] = None
    row["opp_bid_sz"] = None
    row["opp_ask_sz"] = None
    row["first_mid_change_5s"] = None
    if l1 is None:
        return
    row["book_age_ms"] = ts_ms - int(l1["recv_ms"])
    first_bid = as_float(l1[side_key(first_side, "bid_px")])
    first_ask = as_float(l1[side_key(first_side, "ask_px")])
    opp_bid = as_float(l1[side_key(opp_side, "bid_px")])
    opp_ask = as_float(l1[side_key(opp_side, "ask_px")])
    yes_bid = as_float(l1["yes_bid_px"])
    yes_ask = as_float(l1["yes_ask_px"])
    no_bid = as_float(l1["no_bid_px"])
    no_ask = as_float(l1["no_ask_px"])
    row["first_bid_px"] = first_bid
    row["first_ask_px"] = first_ask
    row["opp_bid_px"] = opp_bid
    row["opp_ask_px"] = opp_ask
    row["first_bid_sz"] = as_float(l1[side_key(first_side, "bid_sz")])
    row["first_ask_sz"] = as_float(l1[side_key(first_side, "ask_sz")])
    row["opp_bid_sz"] = as_float(l1[side_key(opp_side, "bid_sz")])
    row["opp_ask_sz"] = as_float(l1[side_key(opp_side, "ask_sz")])
    if first_bid is not None and first_ask is not None:
        row["first_spread_ticks"] = round((first_ask - first_bid) / 0.01, 6)
        row["first_mid"] = round((first_bid + first_ask) / 2.0, 6)
    if opp_bid is not None and opp_ask is not None:
        row["opp_spread_ticks"] = round((opp_ask - opp_bid) / 0.01, 6)
        row["opp_mid"] = round((opp_bid + opp_ask) / 2.0, 6)
    if yes_ask is not None and no_ask is not None:
        row["pair_ask_sum"] = round(yes_ask + no_ask, 6)
    if yes_bid is not None and no_bid is not None:
        row["pair_bid_sum"] = round(yes_bid + no_bid, 6)
    if row["first_mid"] is not None and row["opp_mid"] is not None:
        row["first_mid_skew"] = round(row["first_mid"] - row["opp_mid"], 6)
        row["first_is_l1_high_side"] = str(row["first_mid"] >= row["opp_mid"])
    if prev_l1 is not None and row["first_mid"] is not None:
        prev_first_bid = as_float(prev_l1[side_key(first_side, "bid_px")])
        prev_first_ask = as_float(prev_l1[side_key(first_side, "ask_px")])
        if prev_first_bid is not None and prev_first_ask is not None:
            prev_mid = (prev_first_bid + prev_first_ask) / 2.0
            row["first_mid_change_5s"] = round(row["first_mid"] - prev_mid, 6)


def add_trade_features(row: dict[str, Any], trades_5s: list[sqlite3.Row], trades_15s: list[sqlite3.Row]) -> None:
    first_side = row["first_side"]
    opp_side = opposite(first_side)
    for suffix, trades in (("5s", trades_5s), ("15s", trades_15s)):
        row[f"recent_total_trade_count_{suffix}"] = len(trades)
        row[f"recent_total_trade_size_{suffix}"] = round(sum(float(t["size"]) for t in trades), 6)
        row[f"recent_first_side_trade_count_{suffix}"] = sum(1 for t in trades if t["market_side"] == first_side)
        row[f"recent_opp_side_trade_count_{suffix}"] = sum(1 for t in trades if t["market_side"] == opp_side)
        row[f"recent_sell_on_first_count_{suffix}"] = sum(
            1 for t in trades if t["market_side"] == first_side and str(t["taker_side"]).upper() == "SELL"
        )
        row[f"recent_sell_on_opp_count_{suffix}"] = sum(
            1 for t in trades if t["market_side"] == opp_side and str(t["taker_side"]).upper() == "SELL"
        )


def feature_buckets(row: dict[str, Any]) -> dict[str, str]:
    start_ts = int(float(row["start_ts_s"]))
    hour = dt.datetime.fromtimestamp(start_ts, tz=dt.timezone.utc).strftime("%H")
    return {
        "utc_hour": hour,
        "start_offset_bucket": bucket_offset(as_float(row.get("start_offset_s"))),
        "first_side": row.get("first_side") or "unknown",
        "first_price_bucket": bucket_price(as_float(row.get("first_price"))),
        "first_size_bucket": bucket_size(as_float(row.get("first_size"))),
        "first_price_high": str((as_float(row.get("first_price")) or 0.0) >= 0.5),
        "l1_matched": str(bool(row.get("l1_matched"))),
        "first_is_l1_high_side": str(row.get("first_is_l1_high_side") or "unknown"),
        "first_spread_bucket": bucket_spread_ticks(as_float(row.get("first_spread_ticks"))),
        "opp_spread_bucket": bucket_spread_ticks(as_float(row.get("opp_spread_ticks"))),
        "pair_ask_sum_bucket": bucket_pair_sum(as_float(row.get("pair_ask_sum"))),
        "pair_bid_sum_bucket": bucket_pair_sum(as_float(row.get("pair_bid_sum"))),
        "first_bid_size_bucket": bucket_book_size(as_float(row.get("first_bid_sz"))),
        "opp_ask_size_bucket": bucket_book_size(as_float(row.get("opp_ask_sz"))),
        "first_mid_skew_bucket": bucket_mid_skew(as_float(row.get("first_mid_skew"))),
        "recent_total_trade_count_5s_bucket": bucket_trade_count(int(row.get("recent_total_trade_count_5s") or 0)),
        "recent_total_trade_count_15s_bucket": bucket_trade_count(int(row.get("recent_total_trade_count_15s") or 0)),
        "recent_opp_side_trade_count_15s_bucket": bucket_trade_count(
            int(row.get("recent_opp_side_trade_count_15s") or 0)
        ),
        "recent_sell_on_opp_count_15s_bucket": bucket_trade_count(int(row.get("recent_sell_on_opp_count_15s") or 0)),
    }


def enrich_cycles(cycles: list[dict[str, Any]], replay_root: Path, replay_days: list[str], max_l1_age_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    dbs = replay_db_map(replay_root, replay_days)
    conns: dict[str, sqlite3.Connection] = {}
    stats = {
        "replay_days_available": sorted(dbs),
        "cycle_count": len(cycles),
        "replay_day_available_count": 0,
        "l1_matched_count": 0,
        "trade_context_matched_count": 0,
    }
    out: list[dict[str, Any]] = []
    try:
        for raw in cycles:
            row = dict(raw)
            ts_s = int(float(row["start_ts_s"]))
            ts_ms = ts_s * 1000
            day = dt.datetime.fromtimestamp(ts_s, tz=dt.timezone.utc).strftime("%Y-%m-%d")
            db_path = dbs.get(day)
            row["trusted_replay_window"] = ts_ms >= TRUSTED_START_MS and not (
                PLANNED_OUTAGE_START_MS <= ts_ms < PLANNED_OUTAGE_END_MS
            )
            row["replay_day_available"] = bool(db_path)
            if not db_path:
                add_l1_features(row, None, None, ts_ms)
                add_trade_features(row, [], [])
                row.update(feature_buckets(row))
                out.append(row)
                continue
            stats["replay_day_available_count"] += 1
            if day not in conns:
                conns[day] = connect_ro(db_path)
            conn = conns[day]
            l1 = find_l1_before(conn, row["condition_id"], ts_ms, max_l1_age_ms)
            prev_l1 = find_l1_before(conn, row["condition_id"], ts_ms - 5000, max_l1_age_ms)
            add_l1_features(row, l1, prev_l1, ts_ms)
            if l1 is not None:
                stats["l1_matched_count"] += 1
            trades_5s = trades_before(conn, row["condition_id"], ts_ms - 5000, ts_ms)
            trades_15s = trades_before(conn, row["condition_id"], ts_ms - 15000, ts_ms)
            if trades_5s or trades_15s:
                stats["trade_context_matched_count"] += 1
            add_trade_features(row, trades_5s, trades_15s)
            row.update(feature_buckets(row))
            out.append(row)
    finally:
        for conn in conns.values():
            conn.close()
    return out, stats


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


def label_flags(row: dict[str, Any]) -> dict[str, bool]:
    cls = row.get("class")
    return {
        "target_slow_improved": cls == "clean_slow_improved",
        "closed": parse_bool(row.get("closed")),
        "failed": cls == "failed_residual",
        "bad_slow_or_failed": cls in {"clean_slow", "failed_residual"},
    }


def bucket_stats(rows: list[dict[str, Any]], feature_names: list[str], min_bucket_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    baseline_target = rate(sum(1 for row in rows if label_flags(row)["target_slow_improved"]), len(rows)) or 0.0
    baseline_failed = rate(sum(1 for row in rows if label_flags(row)["failed"]), len(rows)) or 0.0
    baseline_bad = rate(sum(1 for row in rows if label_flags(row)["bad_slow_or_failed"]), len(rows)) or 0.0
    out: list[dict[str, Any]] = []
    for feature in feature_names:
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            groups[str(row.get(feature, "unknown"))].append(row)
        for bucket, xs in groups.items():
            n = len(xs)
            if n < min_bucket_n:
                continue
            target_rate = rate(sum(1 for row in xs if label_flags(row)["target_slow_improved"]), n) or 0.0
            failed_rate = rate(sum(1 for row in xs if label_flags(row)["failed"]), n) or 0.0
            bad_rate = rate(sum(1 for row in xs if label_flags(row)["bad_slow_or_failed"]), n) or 0.0
            pair_costs = [as_float(row.get("pair_cost")) for row in xs if as_float(row.get("pair_cost")) is not None]
            score = 0
            lift = target_rate - baseline_target
            if lift >= 0.10 and failed_rate <= baseline_failed:
                score += 3
            elif lift >= 0.05:
                score += 2
            elif lift >= 0.02:
                score += 1
            elif lift <= -0.03:
                score -= 1
            if failed_rate >= baseline_failed + 0.03:
                score -= 2
            if bad_rate >= baseline_bad + 0.08:
                score -= 1
            out.append(
                {
                    "feature": feature,
                    "bucket": bucket,
                    "n": n,
                    "target_rate": target_rate,
                    "target_lift": round(lift, 6),
                    "failed_rate": failed_rate,
                    "bad_slow_or_failed_rate": bad_rate,
                    "pair_cost_avg": round(sum(pair_costs) / len(pair_costs), 6) if pair_costs else None,
                    "pair_cost_p50": percentile(pair_costs, 50) if pair_costs else None,
                    "score": score,
                }
            )
    out.sort(key=lambda row: (int(row["score"]), float(row["target_lift"]), int(row["n"])), reverse=True)
    return out, {
        "baseline_target_rate": baseline_target,
        "baseline_failed_rate": baseline_failed,
        "baseline_bad_slow_or_failed_rate": baseline_bad,
    }


def score_rows(rows: list[dict[str, Any]], bucket_rows: list[dict[str, Any]], feature_names: list[str]) -> list[dict[str, Any]]:
    score_map = {(row["feature"], row["bucket"]): int(row["score"]) for row in bucket_rows}
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        score = 0
        reasons: list[str] = []
        for feature in feature_names:
            bucket = str(row.get(feature, "unknown"))
            item_score = score_map.get((feature, bucket), 0)
            score += item_score
            if item_score:
                reasons.append(f"{feature}={bucket}:{item_score:+d}")
        row["slow_improvement_score"] = score
        row["slow_improvement_score_reasons"] = ";".join(reasons)
        out.append(row)
    return out


def score_bucket(score: int) -> str:
    if score <= 0:
        return "block_le_0"
    if score <= 2:
        return "watch_1_2"
    if score <= 4:
        return "allow_3_4"
    return "strong_allow_ge_5"


def score_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[score_bucket(int(row["slow_improvement_score"]))].append(row)
    out: dict[str, Any] = {}
    for bucket in ("block_le_0", "watch_1_2", "allow_3_4", "strong_allow_ge_5"):
        xs = groups.get(bucket, [])
        pair_costs = [as_float(row.get("pair_cost")) for row in xs if as_float(row.get("pair_cost")) is not None]
        out[bucket] = {
            "n": len(xs),
            "target_rate": rate(sum(1 for row in xs if label_flags(row)["target_slow_improved"]), len(xs)),
            "failed_rate": rate(sum(1 for row in xs if label_flags(row)["failed"]), len(xs)),
            "bad_slow_or_failed_rate": rate(sum(1 for row in xs if label_flags(row)["bad_slow_or_failed"]), len(xs)),
            "pair_cost_avg": round(sum(pair_costs) / len(pair_costs), 6) if pair_costs else None,
            "pair_cost_p50": percentile(pair_costs, 50) if pair_costs else None,
        }
    return out


def build_profile(rows: list[dict[str, Any]], feature_names: list[str], min_bucket_n: int) -> dict[str, Any]:
    bucket_rows, baseline = bucket_stats(rows, feature_names, min_bucket_n)
    scored = score_rows(rows, bucket_rows, feature_names)
    return {
        "feature_names": feature_names,
        "baseline": baseline,
        "bucket_rows": bucket_rows,
        "score_summary": score_summary(scored),
        "top_positive_buckets": [row for row in bucket_rows if int(row["score"]) > 0][:20],
        "top_negative_buckets": sorted(
            [row for row in bucket_rows if int(row["score"]) < 0],
            key=lambda row: (int(row["score"]), float(row["target_lift"]), -int(row["n"])),
        )[:20],
        "scored_rows": scored,
    }


def build_holdout_profile(rows: list[dict[str, Any]], feature_names: list[str], min_bucket_n: int, train_fraction: float) -> dict[str, Any]:
    ordered = sorted(rows, key=lambda row: int(float(row["start_ts_s"])))
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * train_fraction)))
    train = ordered[:cut]
    holdout = ordered[cut:]
    bucket_rows, train_baseline = bucket_stats(train, feature_names, min_bucket_n)
    scored_holdout = score_rows(holdout, bucket_rows, feature_names)
    holdout_baseline = {
        "baseline_target_rate": rate(sum(1 for row in holdout if label_flags(row)["target_slow_improved"]), len(holdout)),
        "baseline_failed_rate": rate(sum(1 for row in holdout if label_flags(row)["failed"]), len(holdout)),
        "baseline_bad_slow_or_failed_rate": rate(sum(1 for row in holdout if label_flags(row)["bad_slow_or_failed"]), len(holdout)),
    }
    return {
        "train_count": len(train),
        "holdout_count": len(holdout),
        "train_min_iso": iso_ms(int(float(train[0]["start_ts_s"])) * 1000) if train else None,
        "train_max_iso": iso_ms(int(float(train[-1]["start_ts_s"])) * 1000) if train else None,
        "holdout_min_iso": iso_ms(int(float(holdout[0]["start_ts_s"])) * 1000) if holdout else None,
        "holdout_max_iso": iso_ms(int(float(holdout[-1]["start_ts_s"])) * 1000) if holdout else None,
        "train_baseline": train_baseline,
        "holdout_baseline": holdout_baseline,
        "holdout_score_summary": score_summary(scored_holdout),
    }


def render_report(summary: dict[str, Any], top_positive: list[dict[str, Any]], top_negative: list[dict[str, Any]]) -> str:
    lines = [
        "# Xuan Cycle Feature Gate Report",
        "",
        "## Scope",
        "",
        f"- cycle_count: `{summary['cycle_count']}`",
        f"- target: `clean_slow_improved`",
        f"- baseline_target_rate: `{summary['baseline']['baseline_target_rate']}`",
        f"- baseline_failed_rate: `{summary['baseline']['baseline_failed_rate']}`",
        f"- replay_coverage: `{summary['replay_stats']}`",
        f"- provisional: `{summary['provisional']}`",
        "",
        "## Score Buckets",
        "",
        "| score bucket | n | target_rate | failed_rate | bad_slow_or_failed | pair_avg | pair_p50 |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for bucket, item in summary["score_summary"].items():
        lines.append(
            f"| {bucket} | {item['n']} | {item['target_rate']} | {item['failed_rate']} | "
            f"{item['bad_slow_or_failed_rate']} | {item['pair_cost_avg']} | {item['pair_cost_p50']} |"
        )
    lines.extend(["", "## Profile Comparison", "", "| profile | bucket | n | target_rate | failed_rate | pair_p50 |", "|---|---|---:|---:|---:|---:|"])
    for profile_name, profile in summary["profile_comparison"].items():
        for bucket, item in profile["score_summary"].items():
            lines.append(
                f"| {profile_name} | {bucket} | {item['n']} | {item['target_rate']} | "
                f"{item['failed_rate']} | {item['pair_cost_p50']} |"
            )
    lines.extend(["", "## Time Holdout", "", "| profile | holdout bucket | n | target_rate | failed_rate | pair_p50 |", "|---|---|---:|---:|---:|---:|"])
    for profile_name, profile in summary["holdout_profiles"].items():
        for bucket, item in profile["holdout_score_summary"].items():
            lines.append(
                f"| {profile_name} | {bucket} | {item['n']} | {item['target_rate']} | "
                f"{item['failed_rate']} | {item['pair_cost_p50']} |"
            )
    lines.extend(["", "## Top Positive Buckets", "", "| feature | bucket | n | target_rate | lift | failed_rate | score |", "|---|---|---:|---:|---:|---:|---:|"])
    for row in top_positive:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['target_rate']} | "
            f"{row['target_lift']} | {row['failed_rate']} | {row['score']} |"
        )
    lines.extend(["", "## Top Negative Buckets", "", "| feature | bucket | n | target_rate | lift | failed_rate | score |", "|---|---|---:|---:|---:|---:|---:|"])
    for row in top_negative:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['target_rate']} | "
            f"{row['target_lift']} | {row['failed_rate']} | {row['score']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- This is a public-data, cycle-level gate for researching when slow improvement is plausible.",
            "- It is not maker/taker truth and should not be used as live enforcement.",
            "- Buckets with high target lift but elevated failed-rate should be treated as risky, not automatic allow.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cycles-csv", default="data/exports/xuan_tranche_ladder_latest_20260501/xuan_inventory_cycles.csv")
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--replay-days", default=",".join(DEFAULT_REPLAY_DAYS))
    parser.add_argument("--output-dir", default="data/exports/xuan_cycle_feature_gate_20260501")
    parser.add_argument("--max-l1-age-ms", type=int, default=1000)
    parser.add_argument("--min-bucket-n", type=int, default=30)
    args = parser.parse_args()

    cycles = load_cycles(Path(args.cycles_csv))
    replay_days = [day.strip() for day in args.replay_days.split(",") if day.strip()]
    enriched, replay_stats = enrich_cycles(cycles, Path(args.replay_root), replay_days, args.max_l1_age_ms)

    feature_names = [
        "utc_hour",
        "start_offset_bucket",
        "first_side",
        "first_price_bucket",
        "first_size_bucket",
        "first_price_high",
        "l1_matched",
        "first_is_l1_high_side",
        "first_spread_bucket",
        "opp_spread_bucket",
        "pair_ask_sum_bucket",
        "pair_bid_sum_bucket",
        "first_bid_size_bucket",
        "opp_ask_size_bucket",
        "first_mid_skew_bucket",
        "recent_total_trade_count_5s_bucket",
        "recent_total_trade_count_15s_bucket",
        "recent_opp_side_trade_count_15s_bucket",
        "recent_sell_on_opp_count_15s_bucket",
    ]
    structural_feature_names = [name for name in feature_names if name != "utc_hour"]
    public_structural_feature_names = [
        "start_offset_bucket",
        "first_side",
        "first_price_bucket",
        "first_size_bucket",
        "first_price_high",
    ]
    full_profile = build_profile(enriched, feature_names, args.min_bucket_n)
    structural_profile = build_profile(enriched, structural_feature_names, args.min_bucket_n)
    public_structural_profile = build_profile(enriched, public_structural_feature_names, args.min_bucket_n)
    bucket_rows = full_profile["bucket_rows"]
    baseline = full_profile["baseline"]
    scored = full_profile["scored_rows"]
    score_stats = full_profile["score_summary"]
    positive = full_profile["top_positive_buckets"]
    negative = full_profile["top_negative_buckets"]
    holdout_profiles = {
        "full": build_holdout_profile(enriched, feature_names, args.min_bucket_n, 0.70),
        "structural_no_hour": build_holdout_profile(enriched, structural_feature_names, args.min_bucket_n, 0.70),
        "public_structural": build_holdout_profile(enriched, public_structural_feature_names, args.min_bucket_n, 0.70),
    }
    profile_comparison = {
        "full": {"score_summary": full_profile["score_summary"], "baseline": full_profile["baseline"]},
        "structural_no_hour": {
            "score_summary": structural_profile["score_summary"],
            "baseline": structural_profile["baseline"],
        },
        "public_structural": {
            "score_summary": public_structural_profile["score_summary"],
            "baseline": public_structural_profile["baseline"],
        },
    }
    def holdout_lift_ok(profile: dict[str, Any]) -> bool:
        baseline_target = profile["holdout_baseline"]["baseline_target_rate"] or 0.0
        candidates = [
            item for bucket, item in profile["holdout_score_summary"].items()
            if bucket in {"allow_3_4", "strong_allow_ge_5"} and item["n"] >= 30 and item["target_rate"] is not None
        ]
        return any(float(item["target_rate"]) >= baseline_target + 0.10 for item in candidates)
    provisional = replay_stats["l1_matched_count"] < 300 or not holdout_lift_ok(holdout_profiles["structural_no_hour"])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "xuan_cycle_features.csv", scored)
    write_csv(output_dir / "xuan_cycle_bucket_lifts.csv", bucket_rows)

    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "cycles_csv": str(Path(args.cycles_csv).resolve()),
        "replay_root": str(Path(args.replay_root).resolve()),
        "replay_days": replay_days,
        "trusted_start_ms": TRUSTED_START_MS,
        "trusted_start_iso": iso_ms(TRUSTED_START_MS),
        "planned_outage": {"start_iso": iso_ms(PLANNED_OUTAGE_START_MS), "end_iso": iso_ms(PLANNED_OUTAGE_END_MS)},
        "cycle_count": len(enriched),
        "class_counts": dict(Counter(row["class"] for row in enriched)),
        "baseline": baseline,
        "replay_stats": replay_stats,
        "score_summary": score_stats,
        "profile_comparison": profile_comparison,
        "holdout_profiles": holdout_profiles,
        "top_positive_buckets": positive,
        "top_negative_buckets": negative,
        "feature_names": feature_names,
        "structural_feature_names": structural_feature_names,
        "public_structural_feature_names": public_structural_feature_names,
        "provisional": provisional,
        "outputs": {
            "features_csv": str((output_dir / "xuan_cycle_features.csv").resolve()),
            "bucket_lifts_csv": str((output_dir / "xuan_cycle_bucket_lifts.csv").resolve()),
            "summary_json": str((output_dir / "xuan_cycle_gate_summary.json").resolve()),
            "report_md": str((output_dir / "xuan_cycle_gate_report.md").resolve()),
            "defaults_json": str((output_dir / "xuan_slow_improvement_gate_defaults.json").resolve()),
        },
    }
    defaults = {
        "schema": "xuan_slow_improvement_gate_v0_public_research",
        "target": "clean_slow_improved",
        "score_buckets": {
            "block": {"max_score": 0},
            "watch": {"min_score": 1, "max_score": 2},
            "allow_shadow": {"min_score": 3, "max_score": 4},
            "strong_allow_shadow": {"min_score": 5},
        },
        "feature_names": feature_names,
        "bucket_scores": [
            {"feature": row["feature"], "bucket": row["bucket"], "score": int(row["score"])}
            for row in bucket_rows
            if int(row["score"]) != 0
        ],
        "hard_blocks": {
            "failed_residual_not_enforce_grade": True,
            "maker_taker_truth_missing": True,
            "l2_queue_truth_missing": True,
        },
        "coverage_stats": replay_stats,
        "baseline": baseline,
        "profile_comparison": profile_comparison,
        "holdout_profiles": holdout_profiles,
        "provisional": summary["provisional"],
    }
    (output_dir / "xuan_cycle_gate_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "xuan_slow_improvement_gate_defaults.json").write_text(
        json.dumps(defaults, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "xuan_cycle_gate_report.md").write_text(
        render_report(summary, positive, negative),
        encoding="utf-8",
    )
    print(json.dumps({"output_dir": str(output_dir), "cycles": len(enriched), "provisional": summary["provisional"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

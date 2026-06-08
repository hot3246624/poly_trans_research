#!/usr/bin/env python3
"""All-market BTC 5m upcross predictor research.

This intentionally does not use xuan fills as the sampling anchor. It samples
BTC 5m market state once per second from replay L1/trades and asks whether a
side's bid jumps within the next second.

The output is a research-only detector audit. It is not an execution backtest:
maker fillability and pair completion still require separate validation.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


TRUSTED_START_MS = 1777274700000
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def ro_connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


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


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 6) if den else None


def summarize_num(values: list[float | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
    }


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


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "n": n,
        "bid_jump_1s_ge_3c_rate": rate(sum(1 for r in rows if r["bid_jump_1s_ge_3c"]), n),
        "bid_jump_1s_ge_2c_rate": rate(sum(1 for r in rows if r["bid_jump_1s_ge_2c"]), n),
        "future_bid_ge_ask_rate": rate(sum(1 for r in rows if r["future_bid_ge_ask"]), n),
        "future_bid_ge_bid_rate": rate(sum(1 for r in rows if r["future_bid_ge_bid"]), n),
        "future_bid_delta": summarize_num([r.get("future_bid_delta") for r in rows]),
        "spread_ticks": summarize_num([r.get("spread_ticks") for r in rows]),
        "same_minus_opp_buy_15s": summarize_num([r.get("same_minus_opp_buy_15s") for r in rows]),
    }


def bucket_num(value: float | None, cuts: list[tuple[float, str]], last: str) -> str:
    if value is None:
        return "missing"
    for threshold, label in cuts:
        if value < threshold:
            return label
    return last


def bucket(row: dict[str, Any], feature: str) -> str:
    if feature == "side_bid":
        return bucket_num(row.get("side_bid"), [(0.40, "<40"), (0.45, "40-45"), (0.50, "45-50"), (0.55, "50-55"), (0.60, "55-60")], "60+")
    if feature == "side_ask":
        return bucket_num(row.get("side_ask"), [(0.40, "<40"), (0.45, "40-45"), (0.50, "45-50"), (0.55, "50-55"), (0.60, "55-60")], "60+")
    if feature == "offset":
        return bucket_num(row.get("offset_s"), [(30, "0-30"), (60, "30-60"), (120, "60-120"), (180, "120-180"), (240, "180-240")], "240+")
    if feature == "spread":
        return bucket_num(row.get("spread_ticks"), [(1.01, "<=1"), (2.01, "1-2"), (3.01, "2-3")], ">3")
    if feature == "prev_bid_delta":
        return bucket_num(row.get("prev_bid_delta_1s"), [(-0.02, "<-2c"), (-0.005, "-2c..-0.5c"), (0.005, "-0.5c..0.5c"), (0.02, "0.5c..2c")], ">=2c")
    if feature == "same_minus_opp_buy":
        return bucket_num(row.get("same_minus_opp_buy_15s"), [(-300, "<-300"), (-50, "-300..-50"), (50, "-50..50"), (300, "50..300")], ">=300")
    if feature == "side":
        return str(row.get("side") or "missing")
    return "missing"


def rule_defs() -> list[tuple[str, Any]]:
    return [
        ("all", lambda _r: True),
        ("bid_40_55_spread_le1", lambda r: 0.40 <= r["side_bid"] < 0.55 and r["spread_ticks"] <= 1.0),
        ("ask_40_55_spread_le1", lambda r: 0.40 <= r["side_ask"] < 0.55 and r["spread_ticks"] <= 1.0),
        ("offset_lt60_bid_40_55_spread_le1", lambda r: r["offset_s"] < 60 and 0.40 <= r["side_bid"] < 0.55 and r["spread_ticks"] <= 1.0),
        ("offset_30_60_bid_40_55_spread_le1", lambda r: 30 <= r["offset_s"] < 60 and 0.40 <= r["side_bid"] < 0.55 and r["spread_ticks"] <= 1.0),
        (
            "premom_ge2c_bid_40_55_spread_le1",
            lambda r: r["prev_bid_delta_1s"] >= 0.02 and 0.40 <= r["side_bid"] < 0.55 and r["spread_ticks"] <= 1.0,
        ),
        (
            "flow_ge300_bid_40_55_spread_le1",
            lambda r: r["same_minus_opp_buy_15s"] >= 300 and 0.40 <= r["side_bid"] < 0.55 and r["spread_ticks"] <= 1.0,
        ),
        (
            "premom_ge2c_or_flow_ge300_bid_40_55_spread_le1",
            lambda r: (r["prev_bid_delta_1s"] >= 0.02 or r["same_minus_opp_buy_15s"] >= 300)
            and 0.40 <= r["side_bid"] < 0.55
            and r["spread_ticks"] <= 1.0,
        ),
    ]


def fetch_markets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        WHERE symbol = 'BTC' AND interval_sec = 300 AND end_ms > ?
        ORDER BY start_ms
        """,
        (TRUSTED_START_MS,),
    )
    markets = []
    for condition_id, slug, start_ms, end_ms in cur.fetchall():
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        markets.append({"condition_id": condition_id, "slug": slug, "start_ms": int(start_ms), "end_ms": int(end_ms)})
    return markets


def latest_l1_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, Any]]:
    cur = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms >= ? AND recv_ms <= ?
        ORDER BY recv_ms
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in cur:
        recv_ms = int(row[0])
        sec = recv_ms // 1000
        out[sec] = {
            "recv_ms": recv_ms,
            "YES": {"bid": as_float(row[1]), "ask": as_float(row[2]), "bid_sz": as_float(row[5]), "ask_sz": as_float(row[6])},
            "NO": {"bid": as_float(row[3]), "ask": as_float(row[4]), "bid_sz": as_float(row[7]), "ask_sz": as_float(row[8])},
        }
    return out


def trades_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, float]]:
    cur = conn.execute(
        """
        SELECT COALESCE(trade_ts_ms, recv_ms), market_side, taker_side, size
        FROM md_trades
        WHERE condition_id = ? AND COALESCE(trade_ts_ms, recv_ms) >= ? AND COALESCE(trade_ts_ms, recv_ms) <= ?
        ORDER BY COALESCE(trade_ts_ms, recv_ms)
        """,
        (condition_id, start_ms - 20_000, end_ms),
    )
    out: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for ts_ms, market_side, taker_side, size in cur:
        if taker_side != "BUY" or market_side not in ("YES", "NO"):
            continue
        out[int(ts_ms) // 1000][f"{market_side}_buy_size"] += float(size or 0.0)
    return out


def rolling_buy_sums(trades_sec: dict[int, dict[str, float]], sec: int, window_s: int = 15) -> dict[str, float]:
    yes = 0.0
    no = 0.0
    for s in range(sec - window_s + 1, sec + 1):
        item = trades_sec.get(s)
        if not item:
            continue
        yes += item.get("YES_buy_size", 0.0)
        no += item.get("NO_buy_size", 0.0)
    return {"YES": yes, "NO": no}


def build_rows_for_market(conn: sqlite3.Connection, market: dict[str, Any]) -> list[dict[str, Any]]:
    start_ms = max(market["start_ms"], TRUSTED_START_MS)
    end_ms = market["end_ms"]
    l1 = latest_l1_by_second(conn, market["condition_id"], start_ms, end_ms)
    trades = trades_by_second(conn, market["condition_id"], start_ms, end_ms)
    rows: list[dict[str, Any]] = []
    start_sec = start_ms // 1000
    end_sec = end_ms // 1000
    for sec in range(start_sec, end_sec - 1):
        cur = l1.get(sec)
        fut = l1.get(sec + 1)
        prev = l1.get(sec - 1)
        if not cur or not fut or not prev:
            continue
        buy_15 = rolling_buy_sums(trades, sec, 15)
        for side in ("YES", "NO"):
            opp = "NO" if side == "YES" else "YES"
            c = cur[side]
            f = fut[side]
            p = prev[side]
            side_bid = c["bid"]
            side_ask = c["ask"]
            future_bid = f["bid"]
            prev_bid = p["bid"]
            if side_bid is None or side_ask is None or future_bid is None or prev_bid is None:
                continue
            if side_bid <= 0 or side_ask <= 0:
                continue
            spread_ticks = round((side_ask - side_bid) * 100.0, 6)
            future_delta = round(future_bid - side_bid, 6)
            prev_delta = round(side_bid - prev_bid, 6)
            same_buy = buy_15[side]
            opp_buy = buy_15[opp]
            rows.append(
                {
                    "day": dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc).date().isoformat(),
                    "slug": market["slug"],
                    "condition_id": market["condition_id"],
                    "sec": sec,
                    "iso": dt.datetime.fromtimestamp(sec, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                    "offset_s": sec - start_sec,
                    "side": side,
                    "side_bid": side_bid,
                    "side_ask": side_ask,
                    "side_bid_sz": c["bid_sz"],
                    "side_ask_sz": c["ask_sz"],
                    "opp_bid": cur[opp]["bid"],
                    "opp_ask": cur[opp]["ask"],
                    "spread_ticks": spread_ticks,
                    "future_bid_1s": future_bid,
                    "future_bid_delta": future_delta,
                    "prev_bid_delta_1s": prev_delta,
                    "same_buy_size_15s": round(same_buy, 6),
                    "opp_buy_size_15s": round(opp_buy, 6),
                    "same_minus_opp_buy_15s": round(same_buy - opp_buy, 6),
                    "bid_jump_1s_ge_3c": future_delta >= 0.03 - 1e-9,
                    "bid_jump_1s_ge_2c": future_delta >= 0.02 - 1e-9,
                    "future_bid_ge_ask": future_bid >= side_ask - 1e-9,
                    "future_bid_ge_bid": future_bid >= side_bid - 1e-9,
                }
            )
    return rows


def bucket_rows(rows: list[dict[str, Any]], min_n: int) -> list[dict[str, Any]]:
    features = ["side_bid", "side_ask", "offset", "spread", "prev_bid_delta", "same_minus_opp_buy", "side"]
    base = compact(rows)
    base_jump = float(base["bid_jump_1s_ge_3c_rate"] or 0.0)
    base_taker = float(base["future_bid_ge_ask_rate"] or 0.0)
    out: list[dict[str, Any]] = []
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
                    "bid_jump_lift": round(float(item["bid_jump_1s_ge_3c_rate"] or 0.0) - base_jump, 6),
                    "future_bid_ge_ask_lift": round(float(item["future_bid_ge_ask_rate"] or 0.0) - base_taker, 6),
                }
            )
            out.append(item)
    out.sort(
        key=lambda item: (
            float(item["bid_jump_1s_ge_3c_rate"] or 0.0),
            float(item["future_bid_ge_ask_rate"] or 0.0),
            int(item["n"]),
        ),
        reverse=True,
    )
    return out


def rule_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for name, pred in rule_defs():
        xs = [r for r in rows if pred(r)]
        item = compact(xs)
        item.update({"rule": name, "selected_rate": rate(len(xs), len(rows))})
        out.append(item)
    out.sort(
        key=lambda item: (
            float(item["bid_jump_1s_ge_3c_rate"] or 0.0),
            float(item["future_bid_ge_ask_rate"] or 0.0),
            int(item["n"]),
        ),
        reverse=True,
    )
    return out


def rule_day_rows(rows: list[dict[str, Any]], min_day_n: int) -> list[dict[str, Any]]:
    out = []
    for day in sorted({r["day"] for r in rows}):
        day_rows = [r for r in rows if r["day"] == day]
        base = compact(day_rows)
        base_jump = float(base["bid_jump_1s_ge_3c_rate"] or 0.0)
        for name, pred in rule_defs():
            xs = [r for r in day_rows if pred(r)]
            if len(xs) < min_day_n:
                continue
            item = compact(xs)
            item.update(
                {
                    "day": day,
                    "rule": name,
                    "day_total_n": len(day_rows),
                    "selected_rate": rate(len(xs), len(day_rows)),
                    "bid_jump_lift_vs_day": round(float(item["bid_jump_1s_ge_3c_rate"] or 0.0) - base_jump, 6),
                }
            )
            out.append(item)
    return sorted(out, key=lambda x: (x["rule"], x["day"]))


def render_report(report: dict[str, Any]) -> str:
    lines = [
        "# BTC 5m Upcross Predictor Report",
        "",
        "## Scope",
        "",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{','.join(report['days'])}`",
        "- Sampling: latest L1 per second, both YES/NO sides.",
        "- Label: `bid_jump_1s_ge_3c` means side best bid rises by >= 3c in the next second.",
        "- This is a market-state predictor audit, not an execution backtest.",
        "",
        "## Baseline",
        "",
    ]
    base = report["baseline"]
    for key in ("n", "bid_jump_1s_ge_3c_rate", "bid_jump_1s_ge_2c_rate", "future_bid_ge_ask_rate", "future_bid_ge_bid_rate"):
        lines.append(f"- {key}: `{base[key]}`")
    lines.extend(
        [
            "",
            "## Rule Probes",
            "",
            "| rule | n | selected | jump>=3c | jump>=2c | future bid>=ask | future bid>=bid | delta p50 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["rules"]:
        lines.append(
            f"| {row['rule']} | {row['n']} | {row['selected_rate']} | "
            f"{row['bid_jump_1s_ge_3c_rate']} | {row['bid_jump_1s_ge_2c_rate']} | "
            f"{row['future_bid_ge_ask_rate']} | {row['future_bid_ge_bid_rate']} | {row['future_bid_delta']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Top Buckets",
            "",
            "| feature | bucket | n | selected | jump>=3c | lift | future bid>=ask | lift | delta p50 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in report["buckets"][:30]:
        lines.append(
            f"| {row['feature']} | {row['bucket']} | {row['n']} | {row['selected_rate']} | "
            f"{row['bid_jump_1s_ge_3c_rate']} | {row['bid_jump_lift']} | "
            f"{row['future_bid_ge_ask_rate']} | {row['future_bid_ge_ask_lift']} | {row['future_bid_delta']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Rule Stability By Day",
            "",
            "| day | rule | n | selected | jump>=3c | lift vs day | future bid>=ask | delta p50 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    focus = {"all", "bid_40_55_spread_le1", "offset_lt60_bid_40_55_spread_le1", "premom_ge2c_bid_40_55_spread_le1", "premom_ge2c_or_flow_ge300_bid_40_55_spread_le1"}
    for row in report["rule_days"]:
        if row["rule"] not in focus:
            continue
        lines.append(
            f"| {row['day']} | {row['rule']} | {row['n']} | {row['selected_rate']} | "
            f"{row['bid_jump_1s_ge_3c_rate']} | {row['bid_jump_lift_vs_day']} | "
            f"{row['future_bid_ge_ask_rate']} | {row['future_bid_delta']['p50']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--output-dir", default="data/exports/btc5m_upcross_predictor_0427_0501")
    parser.add_argument("--min-bucket-n", type=int, default=500)
    parser.add_argument("--min-day-n", type=int, default=100)
    args = parser.parse_args()

    replay_root = Path(args.replay_root)
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    all_rows: list[dict[str, Any]] = []
    coverage = []
    for day in days:
        db_path = replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            coverage.append({"day": day, "db_path": str(db_path), "status": "missing"})
            continue
        with ro_connect(db_path) as conn:
            markets = fetch_markets(conn)
            day_rows = []
            for market in markets:
                day_rows.extend(build_rows_for_market(conn, market))
            all_rows.extend(day_rows)
            coverage.append({"day": day, "db_path": str(db_path), "status": "ok", "markets": len(markets), "rows": len(day_rows)})

    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(replay_root.resolve()),
        "days": days,
        "coverage": coverage,
        "baseline": compact(all_rows),
        "rules": rule_rows(all_rows),
        "buckets": bucket_rows(all_rows, args.min_bucket_n),
        "rule_days": rule_day_rows(all_rows, args.min_day_n),
    }

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_upcross_predictor_rows.csv", all_rows)
    write_csv(output_dir / "btc5m_upcross_predictor_rules.csv", report["rules"])
    write_csv(output_dir / "btc5m_upcross_predictor_buckets.csv", report["buckets"])
    write_csv(output_dir / "btc5m_upcross_predictor_rule_days.csv", report["rule_days"])
    (output_dir / "btc5m_upcross_predictor_summary.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_upcross_predictor_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(all_rows), "coverage": coverage}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

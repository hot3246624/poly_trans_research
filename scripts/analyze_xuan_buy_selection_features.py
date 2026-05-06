#!/usr/bin/env python3
"""Compare xuan-selected first BUY events against all public BTC 5m BUY flow.

The goal is to identify the observable state that makes xuan choose a tiny
subset of public taker-side BUY events. This is a selection-lift analysis, not a
live strategy backtest.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import sqlite3
from collections import Counter, deque
from pathlib import Path
from typing import Any, Callable


TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)

DEFAULT_XUAN_ROWS = Path(
    "data/exports/xuan_research_runs/replay_20260503_full/"
    "xuan_winner_proxy_gate_5d/xuan_winner_proxy_gate_rows.csv"
)
DEFAULT_MATCH_CSV = Path(
    "data/exports/xuan_research_runs/replay_20260502_full/"
    "xuan_public_trade_match/xuan_public_trade_match_5000ms.csv"
)
DEFAULT_OUTPUT_DIR = Path("data/exports/xuan_buy_selection_features_20260505")


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def rate(num: int | float, den: int | float) -> float | None:
    return round(float(num) / float(den), 8) if den else None


def key_for(condition_id: str, ts_ms: int, side: str, price: float, size: float) -> tuple[str, int, str, float, float]:
    return (condition_id, int(ts_ms), side, round(float(price), 6), round(float(size), 6))


def load_selected_keys(xuan_rows: Path, match_csv: Path) -> Counter[tuple[str, int, str, float, float]]:
    first_txs: set[str] = set()
    with xuan_rows.open() as f:
        for row in csv.DictReader(f):
            tx = row.get("first_tx")
            if tx:
                first_txs.add(str(tx))
    out: Counter[tuple[str, int, str, float, float]] = Counter()
    with match_csv.open() as f:
        for row in csv.DictReader(f):
            if row.get("tx") not in first_txs:
                continue
            if row.get("match_kind") != "price_size_match":
                continue
            if row.get("execution_proxy") != "taker_like_buy":
                continue
            out[
                key_for(
                    str(row["condition_id"]),
                    int(float(row["match_trade_ts_ms"])),
                    str(row["market_side"]),
                    float(row["match_price"]),
                    float(row["match_size"]),
                )
            ] += 1
    return out


def load_markets(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms, s.winner_side
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.symbol = 'BTC' AND m.interval_sec = 300
        ORDER BY m.start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if start_ms < OUTAGE_END_MS and end_ms > OUTAGE_START_MS:
            continue
        if row["winner_side"] not in ("YES", "NO"):
            continue
        out.append(row)
    return out


def load_l1_by_second(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id = ? AND recv_ms >= ? AND recv_ms <= ?
        ORDER BY recv_ms, capture_seq
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        out[int(row["recv_ms"]) // 1000] = {
            "recv_ms": int(row["recv_ms"]),
            "YES": {
                "bid": row["yes_bid_px"],
                "ask": row["yes_ask_px"],
                "bid_sz": row["yes_bid_sz"],
                "ask_sz": row["yes_ask_sz"],
            },
            "NO": {
                "bid": row["no_bid_px"],
                "ask": row["no_ask_px"],
                "bid_sz": row["no_bid_sz"],
                "ask_sz": row["no_ask_sz"],
            },
        }
    return out


def book_at(l1_by_sec: dict[int, dict[str, Any]], ts_ms: int) -> dict[str, Any] | None:
    sec = ts_ms // 1000
    for candidate in (sec, sec - 1, sec - 2):
        book = l1_by_sec.get(candidate)
        if book is not None:
            return book
    return None


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def mid(book: dict[str, Any], side: str) -> float | None:
    bid = book[side]["bid"]
    ask = book[side]["ask"]
    if bid is None or ask is None:
        return None
    return (float(bid) + float(ask)) / 2.0


def spread_ticks(book: dict[str, Any], side: str) -> float | None:
    bid = book[side]["bid"]
    ask = book[side]["ask"]
    if bid is None or ask is None:
        return None
    return round((float(ask) - float(bid)) * 100.0, 6)


def price_bucket(x: float) -> str:
    if x < 0.40:
        return "<0.40"
    if x < 0.50:
        return "0.40-0.50"
    if x < 0.55:
        return "0.50-0.55"
    if x < 0.70:
        return "0.55-0.70"
    return ">=0.70"


def size_bucket(x: float) -> str:
    if x < 20:
        return "<20"
    if x < 50:
        return "20-50"
    if x < 100:
        return "50-100"
    if x < 200:
        return "100-200"
    return ">=200"


def offset_bucket(x: float) -> str:
    if x < 30:
        return "000-030"
    if x < 60:
        return "030-060"
    if x < 120:
        return "060-120"
    if x < 240:
        return "120-240"
    return "240+"


def spread_bucket(x: float | None) -> str:
    if x is None:
        return "missing"
    if x <= 1:
        return "<=1"
    if x <= 3:
        return "2-3"
    return ">3"


def depth_bucket(x: float | None) -> str:
    if x is None:
        return "missing"
    if x < 50:
        return "<50"
    if x < 100:
        return "50-100"
    if x < 250:
        return "100-250"
    if x < 500:
        return "250-500"
    return ">=500"


def pair_bucket(x: float | None) -> str:
    if x is None:
        return "missing"
    if x <= 1.00:
        return "<=1.00"
    if x <= 1.03:
        return "1.00-1.03"
    if x <= 1.06:
        return "1.03-1.06"
    return ">1.06"


def flow_bucket(x: float) -> str:
    if x <= -200:
        return "opp_lead_gt200"
    if x <= -50:
        return "opp_lead_50_200"
    if x < 50:
        return "balanced"
    if x < 200:
        return "same_lead_50_200"
    return "same_lead_gt200"


def count_bucket(x: int) -> str:
    if x == 0:
        return "0"
    if x <= 2:
        return "1-2"
    if x <= 5:
        return "3-5"
    return ">5"


class FlowWindow:
    def __init__(self, window_ms: int) -> None:
        self.window_ms = window_ms
        self.q: deque[sqlite3.Row] = deque()
        self.buy_count = 0
        self.sell_count = 0
        self.side_buy_count = {"YES": 0, "NO": 0}
        self.side_buy_size = {"YES": 0.0, "NO": 0.0}
        self.side_sell_size = {"YES": 0.0, "NO": 0.0}

    def expire(self, ts_ms: int) -> None:
        cutoff = ts_ms - self.window_ms
        while self.q and int(self.q[0]["trade_ts_ms"]) < cutoff:
            row = self.q.popleft()
            self._remove(row)

    def append(self, row: sqlite3.Row) -> None:
        self.q.append(row)
        self._add(row)

    def _add(self, row: sqlite3.Row) -> None:
        side = str(row["market_side"])
        taker = str(row["taker_side"])
        size = float(row["size"])
        if taker == "BUY":
            self.buy_count += 1
            self.side_buy_count[side] += 1
            self.side_buy_size[side] += size
        elif taker == "SELL":
            self.sell_count += 1
            self.side_sell_size[side] += size

    def _remove(self, row: sqlite3.Row) -> None:
        side = str(row["market_side"])
        taker = str(row["taker_side"])
        size = float(row["size"])
        if taker == "BUY":
            self.buy_count -= 1
            self.side_buy_count[side] -= 1
            self.side_buy_size[side] -= size
        elif taker == "SELL":
            self.sell_count -= 1
            self.side_sell_size[side] -= size


def add_event_features(
    row: sqlite3.Row,
    market: sqlite3.Row,
    selected_counter: Counter[tuple[str, int, str, float, float]],
    l1_by_sec: dict[int, dict[str, Any]],
    recent5: FlowWindow,
    recent15: FlowWindow,
) -> dict[str, Any] | None:
    ts_ms = int(row["trade_ts_ms"])
    side = str(row["market_side"])
    price = float(row["price"])
    size = float(row["size"])
    key = key_for(str(market["condition_id"]), ts_ms, side, price, size)
    selected = selected_counter[key] > 0
    if selected:
        selected_counter[key] -= 1

    book = book_at(l1_by_sec, ts_ms)
    if book is None:
        return None
    opp = other(side)
    side_mid = mid(book, side)
    opp_mid = mid(book, opp)
    if side_mid is None or opp_mid is None:
        return None
    side_is_high = side_mid >= opp_mid
    side_spread = spread_ticks(book, side)
    opp_spread = spread_ticks(book, opp)
    opp_ask = book[opp]["ask"]
    immediate_pair = price + float(opp_ask) if opp_ask is not None else None

    same_buy_15 = recent15.side_buy_size[side]
    opp_buy_15 = recent15.side_buy_size[opp]
    same_sell_15 = recent15.side_sell_size[side]
    opp_sell_15 = recent15.side_sell_size[opp]
    buy_count_5 = recent5.buy_count
    same_buy_count_5 = recent5.side_buy_count[side]
    opp_buy_count_5 = recent5.side_buy_count[opp]
    offset_s = (ts_ms - int(market["start_ms"])) / 1000.0
    return {
        "selected": selected,
        "winner": side == market["winner_side"],
        "price_bucket": price_bucket(price),
        "size_bucket": size_bucket(size),
        "offset_bucket": offset_bucket(offset_s),
        "side_is_high": "high" if side_is_high else "low",
        "side_spread_bucket": spread_bucket(side_spread),
        "opp_spread_bucket": spread_bucket(opp_spread),
        "side_ask_depth_bucket": depth_bucket(book[side]["ask_sz"]),
        "opp_ask_depth_bucket": depth_bucket(book[opp]["ask_sz"]),
        "immediate_pair_bucket": pair_bucket(immediate_pair),
        "recent_buy_count_5s_bucket": count_bucket(buy_count_5),
        "recent_same_buy_count_5s_bucket": count_bucket(same_buy_count_5),
        "recent_opp_buy_count_5s_bucket": count_bucket(opp_buy_count_5),
        "recent_same_minus_opp_buy_size_15s_bucket": flow_bucket(same_buy_15 - opp_buy_15),
        "recent_same_minus_opp_sell_size_15s_bucket": flow_bucket(same_sell_15 - opp_sell_15),
    }


def load_trades(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT trade_ts_ms, market_side, taker_side, price, size
        FROM md_trades
        WHERE condition_id = ?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms <= ?
          AND market_side IN ('YES', 'NO')
          AND taker_side IN ('BUY', 'SELL')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()


def analyze_day(db_path: Path, selected_counter: Counter[tuple[str, int, str, float, float]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with ro_connect(db_path) as conn:
        for market in load_markets(conn):
            start_ms = max(int(market["start_ms"]), TRUSTED_START_MS)
            end_ms = int(market["end_ms"])
            l1_by_sec = load_l1_by_second(conn, str(market["condition_id"]), start_ms - 2_000, end_ms)
            if not l1_by_sec:
                continue
            trades = load_trades(conn, str(market["condition_id"]), start_ms, end_ms)
            recent5 = FlowWindow(5_000)
            recent15 = FlowWindow(15_000)
            for trade in trades:
                ts_ms = int(trade["trade_ts_ms"])
                recent5.expire(ts_ms)
                recent15.expire(ts_ms)
                if trade["taker_side"] == "BUY":
                    item = add_event_features(trade, market, selected_counter, l1_by_sec, recent5, recent15)
                    if item is not None:
                        out.append(item)
                recent5.append(trade)
                recent15.append(trade)
    return out


def summarize_group(
    rows: list[dict[str, Any]],
    name: str,
    key_fn: Callable[[dict[str, Any]], str],
    min_all: int,
) -> list[dict[str, Any]]:
    base_select_rate = sum(1 for row in rows if row["selected"]) / len(rows) if rows else 0.0
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(key_fn(row), []).append(row)
    out = []
    for bucket, xs in buckets.items():
        selected = [row for row in xs if row["selected"]]
        if len(xs) < min_all and not selected:
            continue
        select_rate = len(selected) / len(xs)
        out.append(
            {
                "group": name,
                "bucket": bucket,
                "all_count": len(xs),
                "selected_count": len(selected),
                "select_rate": round(select_rate, 8),
                "lift": round(select_rate / base_select_rate, 6) if base_select_rate else None,
                "all_winner_rate": round(sum(1 for row in xs if row["winner"]) / len(xs), 6),
                "selected_winner_rate": round(sum(1 for row in selected if row["winner"]) / len(selected), 6)
                if selected
                else None,
            }
        )
    return sorted(out, key=lambda x: (-x["selected_count"], -x["lift"], x["bucket"]))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def render_markdown(report: dict[str, Any], group_rows: list[dict[str, Any]]) -> str:
    def table(group: str, limit: int = 15) -> str:
        items = [row for row in group_rows if row["group"] == group][:limit]
        lines = [
            "| bucket | all | selected | select rate | lift | all winner | selected winner |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
        for row in items:
            selected_winner = (
                "" if row["selected_winner_rate"] is None else f"{row['selected_winner_rate']:.3f}"
            )
            lines.append(
                f"| {row['bucket']} | {row['all_count']} | {row['selected_count']} | "
                f"{row['select_rate']:.6f} | {row['lift']:.2f} | "
                f"{row['all_winner_rate']:.3f} | {selected_winner} |"
            )
        return "\n".join(lines)

    lines = [
        "# Xuan BUY Selection Features",
        "",
        "## Summary",
        "",
        f"- all_buy_events_with_l1: `{report['summary']['all_buy_events_with_l1']}`",
        f"- selected_matched_events: `{report['summary']['selected_matched_events']}`",
        f"- selected_unmatched_after_scan: `{report['summary']['selected_unmatched_after_scan']}`",
        f"- base_select_rate: `{report['summary']['base_select_rate']}`",
        "",
    ]
    for group in report["groups"]:
        lines.extend([f"## {group}", "", table(group), ""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--days", default="2026-04-27,2026-04-28,2026-04-29,2026-04-30,2026-05-01")
    parser.add_argument("--xuan-rows", type=Path, default=DEFAULT_XUAN_ROWS)
    parser.add_argument("--match-csv", type=Path, default=DEFAULT_MATCH_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--min-all", type=int, default=1000)
    args = parser.parse_args()

    selected_counter = load_selected_keys(args.xuan_rows, args.match_csv)
    selected_total = sum(selected_counter.values())
    rows: list[dict[str, Any]] = []
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        rows.extend(analyze_day(db_path, selected_counter))

    groups: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        ("price", lambda r: r["price_bucket"]),
        ("size", lambda r: r["size_bucket"]),
        ("offset", lambda r: r["offset_bucket"]),
        ("side_is_high", lambda r: r["side_is_high"]),
        ("side_spread", lambda r: r["side_spread_bucket"]),
        ("opp_spread", lambda r: r["opp_spread_bucket"]),
        ("side_ask_depth", lambda r: r["side_ask_depth_bucket"]),
        ("opp_ask_depth", lambda r: r["opp_ask_depth_bucket"]),
        ("immediate_pair", lambda r: r["immediate_pair_bucket"]),
        ("recent_buy_count_5s", lambda r: r["recent_buy_count_5s_bucket"]),
        ("recent_same_buy_count_5s", lambda r: r["recent_same_buy_count_5s_bucket"]),
        ("recent_opp_buy_count_5s", lambda r: r["recent_opp_buy_count_5s_bucket"]),
        ("recent_same_minus_opp_buy_size_15s", lambda r: r["recent_same_minus_opp_buy_size_15s_bucket"]),
        ("price_x_size", lambda r: f"{r['price_bucket']}|{r['size_bucket']}"),
        ("price_x_offset", lambda r: f"{r['price_bucket']}|{r['offset_bucket']}"),
        ("price_x_high", lambda r: f"{r['price_bucket']}|{r['side_is_high']}"),
        ("price_x_immediate_pair", lambda r: f"{r['price_bucket']}|{r['immediate_pair_bucket']}"),
        ("size_x_immediate_pair", lambda r: f"{r['size_bucket']}|{r['immediate_pair_bucket']}"),
        ("price_size_x_immediate_pair", lambda r: f"{r['price_bucket']}|{r['size_bucket']}|{r['immediate_pair_bucket']}"),
        ("price_size_x_recent_buy5", lambda r: f"{r['price_bucket']}|{r['size_bucket']}|{r['recent_buy_count_5s_bucket']}"),
    ]
    group_rows: list[dict[str, Any]] = []
    for name, fn in groups:
        group_rows.extend(summarize_group(rows, name, fn, args.min_all))

    selected_matched = sum(1 for row in rows if row["selected"])
    report = {
        "summary": {
            "selected_keys_total": selected_total,
            "all_buy_events_with_l1": len(rows),
            "selected_matched_events": selected_matched,
            "selected_unmatched_after_scan": sum(selected_counter.values()),
            "base_select_rate": rate(selected_matched, len(rows)),
        },
        "groups": [name for name, _ in groups],
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "xuan_buy_selection_features_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2)
    )
    write_csv(args.output_dir / "xuan_buy_selection_feature_groups.csv", group_rows)
    (args.output_dir / "xuan_buy_selection_features_report.md").write_text(
        render_markdown(report, group_rows)
    )
    print(json.dumps({"output_dir": str(args.output_dir), **report["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

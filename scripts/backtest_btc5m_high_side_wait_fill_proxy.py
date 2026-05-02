#!/usr/bin/env python3
"""Fill-proxy backtest for high-side wait modes.

This script reads replay SQLite in read-only mode. It upgrades the market-side
opportunity scan by requiring:

- bid-like first legs to be filled by cumulative public taker SELL flow;
- ask-like first legs to have enough ask depth at entry;
- opposite completion to have enough ask depth inside the wait window.

It is still not execution truth: queue priority and private fills are unknown.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import datetime as dt
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_275_000_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


@dataclass(frozen=True)
class Book:
    recv_ms: int
    yes_bid_px: float | None
    yes_ask_px: float | None
    no_bid_px: float | None
    no_ask_px: float | None
    yes_bid_sz: float | None
    yes_ask_sz: float | None
    no_bid_sz: float | None
    no_ask_sz: float | None


@dataclass(frozen=True)
class Trade:
    ts_ms: int
    side: str
    price: float
    size: float


@dataclass(frozen=True)
class Fill:
    ts_ms: int
    vwap: float
    size: float
    event_count: int


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


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
    w = pos - lo
    return round(xs[lo] * (1 - w) + xs[hi] * w, 6)


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
        "min": round(min(vals), 6) if vals else None,
        "max": round(max(vals), 6) if vals else None,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def side_value(book: Book, side: str, field: str) -> float | None:
    if side == "YES":
        return getattr(book, f"yes_{field}")
    return getattr(book, f"no_{field}")


def mid(book: Book, side: str) -> float | None:
    bid = side_value(book, side, "bid_px")
    ask = side_value(book, side, "ask_px")
    if bid is None or ask is None:
        return None
    return (bid + ask) / 2.0


def high_side(book: Book) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def first_price(book: Book, side: str, kind: str) -> float | None:
    if kind == "bid":
        return side_value(book, side, "bid_px")
    if kind == "ask":
        return side_value(book, side, "ask_px")
    raise ValueError(kind)


def first_depth(book: Book, side: str, kind: str) -> float | None:
    return side_value(book, side, "ask_sz" if kind == "ask" else "bid_sz")


def day_max_ms(conn: sqlite3.Connection) -> int | None:
    row = conn.execute(
        """
        SELECT MAX(x) FROM (
          SELECT MAX(recv_ms) AS x FROM md_book_l1
          UNION ALL
          SELECT MAX(trade_ts_ms) AS x FROM md_trades WHERE trade_ts_ms IS NOT NULL
        )
        """
    ).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def load_markets(conn: sqlite3.Connection, max_ms: int | None) -> list[sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT condition_id, slug, start_ms, end_ms
        FROM market_meta
        WHERE symbol='BTC' AND interval_sec=300
        ORDER BY start_ms
        """
    ).fetchall()
    out = []
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= TRUSTED_START_MS:
            continue
        if PLANNED_OUTAGE_START_MS <= start_ms < PLANNED_OUTAGE_END_MS:
            continue
        if max_ms is not None and start_ms >= max_ms:
            continue
        out.append(row)
    return out


def load_books(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> list[Book]:
    rows = conn.execute(
        """
        SELECT recv_ms, yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
               yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz
        FROM md_book_l1
        WHERE condition_id=?
          AND recv_ms >= ?
          AND recv_ms < ?
        ORDER BY recv_ms
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    return [
        Book(
            recv_ms=int(row["recv_ms"]),
            yes_bid_px=row["yes_bid_px"],
            yes_ask_px=row["yes_ask_px"],
            no_bid_px=row["no_bid_px"],
            no_ask_px=row["no_ask_px"],
            yes_bid_sz=row["yes_bid_sz"],
            yes_ask_sz=row["yes_ask_sz"],
            no_bid_sz=row["no_bid_sz"],
            no_ask_sz=row["no_ask_sz"],
        )
        for row in rows
    ]


def load_sell_trades(conn: sqlite3.Connection, condition_id: str, start_ms: int, end_ms: int) -> dict[str, list[Trade]]:
    rows = conn.execute(
        """
        SELECT trade_ts_ms, market_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms >= ?
          AND trade_ts_ms < ?
          AND taker_side='SELL'
          AND market_side IN ('YES', 'NO')
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, start_ms, end_ms),
    ).fetchall()
    out = {"YES": [], "NO": []}
    for row in rows:
        out[str(row["market_side"])].append(
            Trade(
                ts_ms=int(row["trade_ts_ms"]),
                side=str(row["market_side"]),
                price=float(row["price"]),
                size=float(row["size"]),
            )
        )
    return out


def sample_indices(books: list[Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            out.append(idx)
            next_sample = book.recv_ms + sample_interval_ms
    return out


def sell_fill(
    trades: list[Trade],
    times: list[int],
    start_ms: int,
    end_ms: int,
    max_price: float,
    target_size: float,
) -> Fill | None:
    idx = bisect.bisect_left(times, start_ms)
    filled = 0.0
    notional = 0.0
    event_count = 0
    while idx < len(trades):
        trade = trades[idx]
        if trade.ts_ms > end_ms:
            return None
        if trade.price <= max_price:
            use = min(trade.size, target_size - filled)
            filled += use
            notional += use * trade.price
            event_count += 1
            if filled + 1e-9 >= target_size:
                return Fill(ts_ms=trade.ts_ms, vwap=notional / filled, size=filled, event_count=event_count)
        idx += 1
    return None


def best_depth_completion(
    books: list[Book],
    times: list[int],
    start_ms: int,
    end_ms: int,
    side: str,
    target_size: float,
) -> tuple[Book | None, float | None]:
    start_idx = bisect.bisect_left(times, start_ms)
    end_idx = bisect.bisect_right(times, end_ms)
    best_book = None
    best_px = None
    for book in books[start_idx:end_idx]:
        px = side_value(book, side, "ask_px")
        sz = side_value(book, side, "ask_sz")
        if px is None or sz is None or sz < target_size:
            continue
        if best_px is None or px < best_px:
            best_px = px
            best_book = book
    return best_book, best_px


def candidate_matches(book: Book, market: sqlite3.Row, mode: dict[str, Any]) -> dict[str, Any] | None:
    offset_s = (book.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < mode["offset_start_s"] or offset_s >= mode["offset_end_s"]:
        return None
    side = high_side(book)
    if side is None:
        return None
    px = first_price(book, side, mode["first_price_kind"])
    depth = first_depth(book, side, mode["first_price_kind"])
    if px is None or px < mode["first_price_min"] or px >= mode["first_price_max"]:
        return None
    if mode.get("first_depth_required", True) and (depth is None or depth < mode["clip_size"]):
        return None
    opp = other(side)
    opp_ask = side_value(book, opp, "ask_px")
    if opp_ask is None:
        return None
    return {
        "candidate_ts_ms": book.recv_ms,
        "candidate_iso": iso_ms(book.recv_ms),
        "candidate_offset_s": round(offset_s, 3),
        "first_side": side,
        "opposite_side": opp,
        "first_price": round(px, 6),
        "first_depth": None if depth is None else round(depth, 6),
        "opposite_ask_now": round(opp_ask, 6),
        "opposite_ask_sz_now": side_value(book, opp, "ask_sz"),
        "immediate_pair_cost": round(px + opp_ask, 6),
        "mid_skew": round(abs((mid(book, "YES") or 0.0) - (mid(book, "NO") or 0.0)), 6),
    }


def simulate_candidate(
    market: sqlite3.Row,
    books: list[Book],
    times: list[int],
    sells: dict[str, list[Trade]],
    sell_times: dict[str, list[int]],
    mode: dict[str, Any],
    cap: float,
    sample_idx: int,
    first_fill_timeout_s: int,
    queue_ahead_fraction: float,
) -> dict[str, Any] | None:
    book = books[sample_idx]
    candidate = candidate_matches(book, market, mode)
    if candidate is None:
        return None
    clip = float(mode["clip_size"])
    row: dict[str, Any] = {
        "mode": mode["name"],
        "cap": cap,
        "residual_cap_ok": clip <= cap,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "round_end_iso": iso_ms(int(market["end_ms"])),
        **candidate,
        "clip_size": clip,
        "first_price_kind": mode["first_price_kind"],
        "wait_budget_s": mode["wait_budget_s"],
        "queue_ahead_fraction": queue_ahead_fraction,
        "first_queue_ahead_size": 0.0,
        "first_flow_required_size": clip,
        "first_fill": False,
        "first_fill_delay_s": None,
        "first_fill_px": None,
        "completion_fill": False,
        "completion_delay_s": None,
        "completion_px": None,
        "pair_cost": None,
        "pair_surplus": None,
        "status": "blocked_by_residual_cap" if clip > cap else None,
    }
    if clip > cap:
        return row

    if mode["first_price_kind"] == "ask":
        # Conservative taker-like bootstrap: require visible ask depth at candidate.
        row.update(
            {
                "first_fill": True,
                "first_fill_ts_ms": candidate["candidate_ts_ms"],
                "first_fill_iso": candidate["candidate_iso"],
                "first_fill_delay_s": 0.0,
                "first_fill_px": candidate["first_price"],
                "first_fill_event_count": 1,
            }
        )
    else:
        queue_ahead = max(float(candidate["first_depth"] or 0.0) * queue_ahead_fraction, 0.0)
        flow_required = clip + queue_ahead
        row["first_queue_ahead_size"] = round(queue_ahead, 6)
        row["first_flow_required_size"] = round(flow_required, 6)
        fill = sell_fill(
            sells[candidate["first_side"]],
            sell_times[candidate["first_side"]],
            candidate["candidate_ts_ms"],
            min(candidate["candidate_ts_ms"] + first_fill_timeout_s * 1000, int(market["end_ms"])),
            candidate["first_price"],
            flow_required,
        )
        if fill is None:
            row["status"] = "no_first_fill"
            return row
        row.update(
            {
                "first_fill": True,
                "first_fill_ts_ms": fill.ts_ms,
                "first_fill_iso": iso_ms(fill.ts_ms),
                "first_fill_delay_s": round((fill.ts_ms - candidate["candidate_ts_ms"]) / 1000.0, 3),
                # The public trade flow is only a fillability proxy. If our maker bid
                # were resting at first_price, our cost is our order price, not the
                # lower prices that may appear in the public sweep.
                "first_fill_px": candidate["first_price"],
                "first_fill_proxy_trade_vwap": round(fill.vwap, 6),
                "first_fill_event_count": fill.event_count,
            }
        )

    first_fill_ts = int(row["first_fill_ts_ms"])
    end_wait_ms = min(first_fill_ts + int(mode["wait_budget_s"]) * 1000, int(market["end_ms"]))
    completion_book, completion_px = best_depth_completion(
        books,
        times,
        first_fill_ts,
        end_wait_ms,
        candidate["opposite_side"],
        clip,
    )
    if completion_book is None or completion_px is None:
        row["status"] = "no_opposite_depth_completion"
        return row
    pair_cost = float(row["first_fill_px"]) + completion_px
    row.update(
        {
            "completion_fill": True,
            "completion_ts_ms": completion_book.recv_ms,
            "completion_iso": iso_ms(completion_book.recv_ms),
            "completion_delay_s": round((completion_book.recv_ms - first_fill_ts) / 1000.0, 3),
            "completion_px": round(completion_px, 6),
            "completion_depth": side_value(completion_book, candidate["opposite_side"], "ask_sz"),
            "pair_cost": round(pair_cost, 6),
            "pair_surplus": round(1.0 - pair_cost, 6),
            "status": "closed",
        }
    )
    return row


def load_modes(path: Path, override_wait_budget_s: int | None = None) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    modes = []
    for mode in data["modes"]:
        modes.append(
            {
                "name": mode["name"],
                "first_price_kind": mode["first_price_kind"],
                "offset_start_s": int(mode["offset_start_s"]),
                "offset_end_s": int(mode["offset_end_s"]),
                "first_price_min": float(mode["first_price_min"]),
                "first_price_max": float(mode["first_price_max"]),
                "clip_size": float(mode["clip_size"]),
                "wait_budget_s": int(override_wait_budget_s if override_wait_budget_s is not None else mode["wait_budget_s"]),
                "first_depth_required": bool(mode.get("first_depth_required", True)),
                "residual_cap_qty_candidates": [float(x) for x in mode.get("residual_cap_qty_candidates", [])],
            }
        )
    return modes


def scan_market(
    market: sqlite3.Row,
    books: list[Book],
    sells: dict[str, list[Trade]],
    modes: list[dict[str, Any]],
    sample_interval_ms: int,
    first_fill_timeout_s: int,
    queue_ahead_fraction: float,
) -> list[dict[str, Any]]:
    times = [book.recv_ms for book in books]
    sell_times = {side: [trade.ts_ms for trade in xs] for side, xs in sells.items()}
    sample_idxs = []
    next_sample = int(market["start_ms"])
    for idx, book in enumerate(books):
        if book.recv_ms >= next_sample:
            sample_idxs.append(idx)
            next_sample = book.recv_ms + sample_interval_ms

    out = []
    seen: set[tuple[str, float]] = set()
    for mode in modes:
        caps = mode["residual_cap_qty_candidates"] or [mode["clip_size"]]
        for cap in caps:
            key = (mode["name"], cap)
            if key in seen:
                continue
            for idx in sample_idxs:
                row = simulate_candidate(
                    market,
                    books,
                    times,
                    sells,
                    sell_times,
                    mode,
                    cap,
                    idx,
                    first_fill_timeout_s,
                    queue_ahead_fraction,
                )
                if row is None:
                    continue
                out.append(row)
                seen.add(key)
                break
    return out


def compact(rows: list[dict[str, Any]]) -> dict[str, Any]:
    first = [row for row in rows if row.get("first_fill") is True]
    closed = [row for row in rows if row.get("completion_fill") is True]
    return {
        "candidate_count": len(rows),
        "first_fill_count": len(first),
        "first_fill_rate": rate(len(first), len(rows)),
        "closed_count": len(closed),
        "closed_rate_among_candidates": rate(len(closed), len(rows)),
        "closed_rate_among_first_fills": rate(len(closed), len(first)),
        "first_fill_delay_s": summarize([row.get("first_fill_delay_s") for row in first]),
        "completion_delay_s": summarize([row.get("completion_delay_s") for row in closed]),
        "pair_cost": summarize([row.get("pair_cost") for row in closed]),
        "pair_cost_lt_0_90_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.90), len(closed)),
        "pair_cost_lt_0_95_rate": rate(sum(1 for row in closed if float(row["pair_cost"]) < 0.95), len(closed)),
        "status_counts": dict(sorted({status: sum(1 for row in rows if row.get("status") == status) for status in {row.get("status") for row in rows}}.items())),
    }


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {"all": compact(rows), "by_mode_cap": {}}
    for key in sorted({(row["mode"], float(row["cap"])) for row in rows}):
        mode, cap = key
        xs = [row for row in rows if row["mode"] == mode and float(row["cap"]) == cap]
        out["by_mode_cap"][f"{mode}|cap={cap:g}"] = compact(xs)
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
    lines = [
        "# BTC 5m High-Side Wait Fill Proxy",
        "",
        "## Scope",
        "",
        f"- modes_file: `{report['modes_file']}`",
        f"- days: `{report['days']}`",
        f"- first_fill_timeout_s: `{report['parameters']['first_fill_timeout_s']}`",
        f"- override_wait_budget_s: `{report['parameters']['override_wait_budget_s']}`",
        f"- queue_ahead_fraction: `{report['parameters']['queue_ahead_fraction']}`",
        f"- sample_interval_s: `{report['parameters']['sample_interval_s']}`",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "- This is still proxy, not queue/execution truth.",
        "- Bid-like first-leg fills use public SELL flow only as a fillability proxy; cost is modeled at our posted bid.",
        "",
        "## Mode Results",
        "",
        "| mode/cap | candidates | first fill | closed/cand | closed/fill | pair p50 | pair <0.90 | first delay p50 | completion delay p50 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, item in report["aggregate"]["by_mode_cap"].items():
        lines.append(
            f"| {key} | {item['candidate_count']} | {item['first_fill_rate']} | "
            f"{item['closed_rate_among_candidates']} | {item['closed_rate_among_first_fills']} | "
            f"{item['pair_cost']['p50']} | {item['pair_cost_lt_0_90_rate']} | "
            f"{item['first_fill_delay_s']['p50']} | {item['completion_delay_s']['p50']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- If `first_fill_rate` is low, the market-side opportunity depends on queue priority or taker execution.",
            "- If `closed_rate_among_first_fills` is low, the cheap opposite ask was usually not deep enough for full clip.",
            "- `first_fill_proxy_trade_vwap` is diagnostic only; `pair_cost` uses modeled order cost.",
            "- A mode needs both fillability and pair-cost advantage before it can become a live strategy candidate.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument(
        "--modes-file",
        default="data/exports/xuan_cycle_feature_gate_20260501/high_side_wait_shadow_candidates.json",
    )
    parser.add_argument("--output-dir", default="data/exports/btc5m_high_side_wait_fill_proxy_20260501")
    parser.add_argument("--sample-interval-s", type=float, default=5.0)
    parser.add_argument("--first-fill-timeout-s", type=int, default=30)
    parser.add_argument("--override-wait-budget-s", type=int)
    parser.add_argument(
        "--queue-ahead-fraction",
        type=float,
        default=0.0,
        help="Fraction of displayed same-price bid size assumed to be ahead of us for bid-like first legs.",
    )
    args = parser.parse_args()

    modes = load_modes(Path(args.modes_file), args.override_wait_budget_s)
    days = [day.strip() for day in args.days.split(",") if day.strip()]
    rows: list[dict[str, Any]] = []
    db_summaries: list[dict[str, Any]] = []
    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            db_summaries.append({"day": day, "db_path": str(db_path), "markets": len(markets)})
            for market in markets:
                books = load_books(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                if not books:
                    continue
                sells = load_sell_trades(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                market_rows = scan_market(
                    market,
                    books,
                    sells,
                    modes,
                    int(args.sample_interval_s * 1000),
                    args.first_fill_timeout_s,
                    args.queue_ahead_fraction,
                )
                for row in market_rows:
                    row["day"] = day
                rows.extend(market_rows)
        finally:
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_high_side_wait_fill_proxy_rows.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "modes_file": str(Path(args.modes_file).resolve()),
        "days": days,
        "parameters": {
            "sample_interval_s": args.sample_interval_s,
            "first_fill_timeout_s": args.first_fill_timeout_s,
            "override_wait_budget_s": args.override_wait_budget_s,
            "queue_ahead_fraction": args.queue_ahead_fraction,
        },
        "db_summaries": db_summaries,
        "aggregate": aggregate(rows),
        "outputs": {
            "rows_csv": str((output_dir / "btc5m_high_side_wait_fill_proxy_rows.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_high_side_wait_fill_proxy_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_high_side_wait_fill_proxy_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_high_side_wait_fill_proxy_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "btc5m_high_side_wait_fill_proxy_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "rows": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

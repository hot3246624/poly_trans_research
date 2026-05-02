#!/usr/bin/env python3
"""Fast grid search for BTC 5m high-side wait pockets.

This scanner reads replay SQLite in read-only mode. It loads each BTC market's
book timeline once, samples candidate timestamps in memory, then evaluates a
parameter grid. It does not use raw capture files and does not modify DBs.
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


def other(side: str) -> str:
    return "NO" if side == "YES" else "YES"


def first_price(book: Book, side: str, kind: str) -> float | None:
    if kind == "ask":
        return side_value(book, side, "ask_px")
    if kind == "bid":
        return side_value(book, side, "bid_px")
    if kind == "mid":
        return mid(book, side)
    raise ValueError(kind)


def first_depth(book: Book, side: str, kind: str) -> float | None:
    if kind == "ask":
        return side_value(book, side, "ask_sz")
    return side_value(book, side, "bid_sz")


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


def sample_indices(books: list[Book], start_ms: int, sample_interval_ms: int) -> list[int]:
    out: list[int] = []
    next_sample = start_ms
    for idx, book in enumerate(books):
        if book.recv_ms < next_sample:
            continue
        out.append(idx)
        next_sample = book.recv_ms + sample_interval_ms
    return out


def high_side(book: Book) -> str | None:
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    if yes_mid is None or no_mid is None:
        return None
    return "YES" if yes_mid >= no_mid else "NO"


def best_wait(
    books: list[Book],
    times: list[int],
    start_idx: int,
    side: str,
    budget_s: int,
    end_ms: int,
) -> tuple[float | None, float | None, int | None]:
    start_ms = books[start_idx].recv_ms
    stop_ms = min(start_ms + budget_s * 1000, end_ms)
    end_idx = bisect.bisect_right(times, stop_ms)
    best_px = None
    best_sz = None
    best_ms = None
    for book in books[start_idx:end_idx]:
        px = side_value(book, side, "ask_px")
        if px is None:
            continue
        if best_px is None or px < best_px:
            best_px = px
            best_sz = side_value(book, side, "ask_sz")
            best_ms = book.recv_ms
    return best_px, best_sz, best_ms


def candidate_from_sample(
    market: sqlite3.Row,
    books: list[Book],
    times: list[int],
    idx: int,
    config: dict[str, Any],
    wait_budgets: list[int],
) -> dict[str, Any] | None:
    book = books[idx]
    offset_s = (book.recv_ms - int(market["start_ms"])) / 1000.0
    if offset_s < config["offset_start_s"] or offset_s >= config["offset_end_s"]:
        return None
    side = high_side(book)
    if side is None:
        return None
    opp = other(side)
    px = first_price(book, side, config["first_price_kind"])
    depth = first_depth(book, side, config["first_price_kind"])
    opp_ask_now = side_value(book, opp, "ask_px")
    if px is None or opp_ask_now is None:
        return None
    if px < config["price_min"] or px >= config["price_max"]:
        return None
    if config["require_first_depth"] and (depth is None or depth < config["clip_size"]):
        return None
    yes_mid = mid(book, "YES")
    no_mid = mid(book, "NO")
    row: dict[str, Any] = {
        **config,
        "slug": market["slug"],
        "condition_id": market["condition_id"],
        "round_start_ms": int(market["start_ms"]),
        "round_start_iso": iso_ms(int(market["start_ms"])),
        "recv_ms": book.recv_ms,
        "recv_iso": iso_ms(book.recv_ms),
        "offset_s": round(offset_s, 3),
        "first_side": side,
        "opp_side": opp,
        "first_price": round(px, 6),
        "first_depth": None if depth is None else round(depth, 6),
        "opp_ask_now": round(opp_ask_now, 6),
        "opp_ask_sz_now": side_value(book, opp, "ask_sz"),
        "immediate_pair_cost": round(px + opp_ask_now, 6),
        "yes_mid": None if yes_mid is None else round(yes_mid, 6),
        "no_mid": None if no_mid is None else round(no_mid, 6),
        "mid_skew": None if yes_mid is None or no_mid is None else round(abs(yes_mid - no_mid), 6),
    }
    for budget_s in wait_budgets:
        best_px, best_sz, best_ms = best_wait(books, times, idx, opp, budget_s, int(market["end_ms"]))
        prefix = f"wait_{budget_s}s"
        if best_px is None:
            row[f"{prefix}_pair_cost"] = None
            row[f"{prefix}_improvement"] = None
            row[f"{prefix}_delay_s"] = None
            row[f"{prefix}_depth_ge_clip"] = None
            continue
        pair_cost = px + best_px
        row[f"{prefix}_pair_cost"] = round(pair_cost, 6)
        row[f"{prefix}_improvement"] = round(float(row["immediate_pair_cost"]) - pair_cost, 6)
        row[f"{prefix}_delay_s"] = None if best_ms is None else round((best_ms - book.recv_ms) / 1000.0, 3)
        row[f"{prefix}_opp_ask_sz"] = None if best_sz is None else round(best_sz, 6)
        row[f"{prefix}_depth_ge_clip"] = bool(best_sz is not None and best_sz >= config["clip_size"])
    return row


def summarize_rows(config: dict[str, Any], rows: list[dict[str, Any]], wait_budgets: list[int], source_markets: int) -> dict[str, Any]:
    out = {**config}
    out["source_markets"] = source_markets
    out["candidate_count"] = len(rows)
    out["coverage_rate"] = rate(len(rows), source_markets)
    out["immediate_pair_p50"] = summarize([row.get("immediate_pair_cost") for row in rows])["p50"]
    out["first_price_p50"] = summarize([row.get("first_price") for row in rows])["p50"]
    out["first_depth_ge_clip_rate"] = rate(
        sum(1 for row in rows if row.get("first_depth") is not None and float(row["first_depth"]) >= config["clip_size"]),
        len(rows),
    )
    for budget_s in wait_budgets:
        prefix = f"wait_{budget_s}s"
        costs = [row.get(f"{prefix}_pair_cost") for row in rows]
        improvements = [row.get(f"{prefix}_improvement") for row in rows]
        cost_summary = summarize(costs)
        imp_summary = summarize(improvements)
        out[f"{prefix}_pair_p50"] = cost_summary["p50"]
        out[f"{prefix}_pair_p90"] = cost_summary["p90"]
        out[f"{prefix}_pair_avg"] = cost_summary["avg"]
        out[f"{prefix}_improvement_p50"] = imp_summary["p50"]
        out[f"{prefix}_lt_0_90_rate"] = rate(
            sum(1 for row in rows if row.get(f"{prefix}_pair_cost") is not None and float(row[f"{prefix}_pair_cost"]) < 0.90),
            len(rows),
        )
        out[f"{prefix}_lt_0_95_rate"] = rate(
            sum(1 for row in rows if row.get(f"{prefix}_pair_cost") is not None and float(row[f"{prefix}_pair_cost"]) < 0.95),
            len(rows),
        )
        out[f"{prefix}_improvement_ge_0_03_rate"] = rate(
            sum(1 for row in rows if row.get(f"{prefix}_improvement") is not None and float(row[f"{prefix}_improvement"]) >= 0.03),
            len(rows),
        )
        out[f"{prefix}_depth_ge_clip_rate"] = rate(
            sum(1 for row in rows if row.get(f"{prefix}_depth_ge_clip") is True),
            len(rows),
        )
    return out


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


def parse_offsets(value: str) -> list[tuple[int, int]]:
    return [tuple(map(int, item.split("-"))) for item in value.split(",") if item.strip()]


def parse_price_ranges(value: str) -> list[tuple[float, float]]:
    return [tuple(map(float, item.split("-"))) for item in value.split(",") if item.strip()]


def build_configs(args: argparse.Namespace) -> list[dict[str, Any]]:
    first_price_kinds = [x.strip() for x in args.first_price_kinds.split(",") if x.strip()]
    offsets = parse_offsets(args.offsets)
    price_ranges = parse_price_ranges(args.price_ranges)
    clip_sizes = [float(x.strip()) for x in args.clip_sizes.split(",") if x.strip()]
    depth_modes = [True, False] if args.include_no_depth else [True]
    configs = []
    for kind in first_price_kinds:
        for offset_start, offset_end in offsets:
            for price_min, price_max in price_ranges:
                for clip_size in clip_sizes:
                    for require_depth in depth_modes:
                        configs.append(
                            {
                                "first_price_kind": kind,
                                "offset_start_s": offset_start,
                                "offset_end_s": offset_end,
                                "price_min": price_min,
                                "price_max": price_max,
                                "clip_size": clip_size,
                                "require_first_depth": require_depth,
                            }
                        )
    return configs


def render_report(summary: dict[str, Any], top_rows: list[dict[str, Any]]) -> str:
    primary = f"wait_{summary['primary_wait_s']}s"
    lines = [
        "# BTC 5m High-Side Wait Grid Search",
        "",
        "## Scope",
        "",
        f"- days: `{summary['days']}`",
        f"- configs: `{summary['config_count']}`",
        f"- primary_wait_s: `{summary['primary_wait_s']}`",
        f"- source_markets: `{summary['source_markets']}`",
        "- Read-only replay SQLite. No raw data, no DB writes, no execution truth.",
        "",
        "## Top Configs",
        "",
        f"| rank | kind | first_depth | offset | price | clip | n | coverage | {primary} p50 | {primary} <0.90 | {primary} depth | improvement p50 |",
        "|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, row in enumerate(top_rows[:30], start=1):
        lines.append(
            f"| {idx} | {row['first_price_kind']} | {row['require_first_depth']} | "
            f"{row['offset_start_s']}-{row['offset_end_s']} | {row['price_min']}-{row['price_max']} | "
            f"{row['clip_size']} | {row['candidate_count']} | {row['coverage_rate']} | "
            f"{row[f'{primary}_pair_p50']} | {row[f'{primary}_lt_0_90_rate']} | "
            f"{row[f'{primary}_depth_ge_clip_rate']} | {row[f'{primary}_improvement_p50']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- Pair-cost opportunity is market-side only. Candidate coverage and depth rates are more important than raw pair p50.",
            "- Configs with high pair improvement but low opposite depth need queue/fill truth before implementation.",
            "- Prefer configs with enough candidates, non-trivial depth, and improvement across multiple wait budgets.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--output-dir", default="data/exports/btc5m_high_side_wait_grid_20260501")
    parser.add_argument("--first-price-kinds", default="ask,bid")
    parser.add_argument("--offsets", default="0-60,60-120,120-180,180-240,240-285")
    parser.add_argument("--price-ranges", default="0.50-0.55,0.55-0.70,0.70-0.85,0.50-0.85,0.85-0.98")
    parser.add_argument("--clip-sizes", default="140,180,220")
    parser.add_argument("--wait-budgets-s", default="30,50,70,98")
    parser.add_argument("--primary-wait-s", type=int, default=70)
    parser.add_argument("--sample-interval-sec", type=int, default=5)
    parser.add_argument("--include-no-depth", action="store_true")
    parser.add_argument("--min-candidates", type=int, default=40)
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    wait_budgets = [int(x.strip()) for x in args.wait_budgets_s.split(",") if x.strip()]
    configs = build_configs(args)
    by_config: list[list[dict[str, Any]]] = [[] for _ in configs]
    source_markets = 0

    for day in days:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            continue
        conn = connect_ro(db_path)
        try:
            markets = load_markets(conn, day_max_ms(conn))
            source_markets += len(markets)
            for market in markets:
                books = load_books(conn, market["condition_id"], int(market["start_ms"]), int(market["end_ms"]))
                if not books:
                    continue
                times = [book.recv_ms for book in books]
                samples = sample_indices(books, int(market["start_ms"]), args.sample_interval_sec * 1000)
                # For each config keep the first matching sample in this market.
                matched = [False] * len(configs)
                for idx in samples:
                    if all(matched):
                        break
                    for config_idx, config in enumerate(configs):
                        if matched[config_idx]:
                            continue
                        row = candidate_from_sample(market, books, times, idx, config, wait_budgets)
                        if row is None:
                            continue
                        row["config_idx"] = config_idx + 1
                        by_config[config_idx].append(row)
                        matched[config_idx] = True
        finally:
            conn.close()

    summary_rows = [
        {**summarize_rows(config, rows, wait_budgets, source_markets), "config_idx": idx + 1}
        for idx, (config, rows) in enumerate(zip(configs, by_config))
    ]
    primary = f"wait_{args.primary_wait_s}s"
    eligible = [
        row for row in summary_rows
        if int(row["candidate_count"]) >= args.min_candidates and row.get(f"{primary}_pair_p50") is not None
    ]
    eligible.sort(
        key=lambda row: (
            float(row.get(f"{primary}_lt_0_90_rate") or 0.0),
            float(row.get(f"{primary}_depth_ge_clip_rate") or 0.0),
            -float(row.get(f"{primary}_pair_p50") or 9.0),
            int(row["candidate_count"]),
        ),
        reverse=True,
    )
    sample_rows: list[dict[str, Any]] = []
    eligible_ids = {int(row["config_idx"]) for row in eligible[:20]}
    for config_idx, rows in enumerate(by_config, start=1):
        if config_idx in eligible_ids:
            sample_rows.extend(rows[:10])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "btc5m_high_side_wait_grid_summary.csv", summary_rows)
    write_csv(output_dir / "btc5m_high_side_wait_grid_top_samples.csv", sample_rows)
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "days": days,
        "source_markets": source_markets,
        "config_count": len(summary_rows),
        "primary_wait_s": args.primary_wait_s,
        "wait_budgets_s": wait_budgets,
        "min_candidates": args.min_candidates,
        "top_configs": eligible[:50],
        "outputs": {
            "summary_csv": str((output_dir / "btc5m_high_side_wait_grid_summary.csv").resolve()),
            "top_samples_csv": str((output_dir / "btc5m_high_side_wait_grid_top_samples.csv").resolve()),
            "summary_json": str((output_dir / "btc5m_high_side_wait_grid_summary.json").resolve()),
            "report_md": str((output_dir / "btc5m_high_side_wait_grid_report.md").resolve()),
        },
    }
    (output_dir / "btc5m_high_side_wait_grid_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (output_dir / "btc5m_high_side_wait_grid_report.md").write_text(render_report(summary, eligible), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "configs": len(summary_rows), "source_markets": source_markets}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

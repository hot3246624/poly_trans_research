#!/usr/bin/env python3
"""Infer xuan maker/taker proxy by matching public xuan trades to replay trades.

This is still not address-level execution truth. It only asks:

- Is there a public market trade near xuan's public BUY record with the same
  condition, side, price, and size?
- If yes, what was the public trade's taker_side?

For a xuan BUY:

- matched public taker_side=BUY is taker-like for xuan.
- matched public taker_side=SELL is maker-like bid fill for xuan.

The xuan Data API timestamp is second-level and may lag exchange trade_ts_ms, so
the script reports multiple matching windows.
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


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30")
TRUSTED_START_MS = 1_777_274_700_000
PLANNED_OUTAGE_START_MS = int(dt.datetime(2026, 4, 28, 11, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)
PLANNED_OUTAGE_END_MS = int(dt.datetime(2026, 4, 28, 12, 0, tzinfo=dt.timezone.utc).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def outcome_to_market_side(row: dict[str, Any]) -> str | None:
    direct = row.get("outcomeSide") or row.get("outcome_side") or row.get("market_side")
    if direct in {"YES", "NO"}:
        return str(direct)
    if "outcomeIndex" in row:
        try:
            idx = int(row["outcomeIndex"])
            if idx == 0:
                return "YES"
            if idx == 1:
                return "NO"
        except (TypeError, ValueError):
            pass
    outcome = str(row.get("outcome", "")).strip().lower()
    if outcome in {"up", "yes"}:
        return "YES"
    if outcome in {"down", "no"}:
        return "NO"
    return None


def load_xuan_trades(path: Path, days: set[str]) -> list[dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for row in rows:
        if str(row.get("side", "")).upper() != "BUY":
            continue
        ts_s = int(row["timestamp"])
        ts_ms = ts_s * 1000
        if ts_ms < TRUSTED_START_MS:
            continue
        if PLANNED_OUTAGE_START_MS <= ts_ms < PLANNED_OUTAGE_END_MS:
            continue
        day = dt.datetime.fromtimestamp(ts_s, tz=dt.timezone.utc).strftime("%Y-%m-%d")
        if day not in days:
            continue
        side = outcome_to_market_side(row)
        if side is None:
            continue
        out.append(
            {
                "xuan_ts_ms": ts_ms,
                "xuan_iso": iso_ms(ts_ms),
                "day": day,
                "condition_id": row["conditionId"],
                "slug": row.get("slug"),
                "market_side": side,
                "price": float(row["price"]),
                "size": float(row["size"]),
                "tx": row.get("transactionHash"),
            }
        )
    return sorted(out, key=lambda r: (r["xuan_ts_ms"], r["condition_id"], r["market_side"], r["price"], r["size"]))


def load_event_index(path: Path) -> dict[tuple[str, int, str, float, float], list[dict[str, Any]]]:
    if not path.exists():
        return {}
    out: dict[tuple[str, int, str, float, float], list[dict[str, Any]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            key = (
                row["condition_id"],
                int(row["ts_s"]),
                row["market_side"],
                round(float(row["price"]), 6),
                round(float(row["size"]), 3),
            )
            out[key].append(row)
    return out


def attach_event_fields(
    rows: list[dict[str, Any]],
    event_index: dict[tuple[str, int, str, float, float], list[dict[str, Any]]],
) -> None:
    for row in rows:
        key = (
            row["condition_id"],
            int(row["xuan_ts_ms"] / 1000),
            row["market_side"],
            round(float(row["price"]), 6),
            round(float(row["size"]), 3),
        )
        event = event_index.get(key, [{}])[0]
        row["event_idx"] = event.get("event_idx")
        row["event_clean_now"] = event.get("clean_now")
        row["event_round_offset_s"] = event.get("round_offset_s")
        if event.get("clean_now") == "False":
            row["event_phase"] = "open_residual"
        elif event.get("clean_now") == "True":
            row["event_phase"] = "clean_completion"
        else:
            row["event_phase"] = "unknown"


def load_day_max_ms(conn: sqlite3.Connection) -> int | None:
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


def fetch_trade_candidates(
    conn: sqlite3.Connection,
    condition_id: str,
    market_side: str,
    start_ms: int,
    end_ms: int,
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT id, trade_ts_ms, recv_ms, market_side, taker_side, price, size
        FROM md_trades
        WHERE condition_id=?
          AND market_side=?
          AND trade_ts_ms IS NOT NULL
          AND trade_ts_ms BETWEEN ? AND ?
        ORDER BY trade_ts_ms, id
        """,
        (condition_id, market_side, start_ms, end_ms),
    ).fetchall()


def match_one(
    xuan: dict[str, Any],
    candidates: list[sqlite3.Row],
    price_tol: float,
    size_tol_abs: float,
    size_tol_rel: float,
) -> dict[str, Any]:
    price = float(xuan["price"])
    size = float(xuan["size"])
    size_tol = max(size_tol_abs, abs(size) * size_tol_rel)
    best = None
    best_score = None
    best_kind = "no_match"
    for trade in candidates:
        trade_price = float(trade["price"])
        trade_size = float(trade["size"])
        price_diff = abs(trade_price - price)
        size_diff = abs(trade_size - size)
        if price_diff > price_tol:
            continue
        exact_size = size_diff <= size_tol
        # Prefer exact-size matches, then closer time, then closer size/price.
        time_diff = abs(int(trade["trade_ts_ms"]) - int(xuan["xuan_ts_ms"]))
        score = (
            0 if exact_size else 1,
            time_diff,
            round(size_diff, 9),
            round(price_diff, 9),
        )
        if best_score is None or score < best_score:
            best = trade
            best_score = score
            best_kind = "price_size_match" if exact_size else "price_only_match"
    if best is None:
        return {
            "match_kind": "no_match",
            "match_taker_side": None,
            "match_trade_ts_ms": None,
            "match_trade_iso": None,
            "match_time_diff_ms": None,
            "match_price": None,
            "match_size": None,
            "match_price_diff": None,
            "match_size_diff": None,
            "execution_proxy": "unknown",
        }
    taker_side = str(best["taker_side"]).upper()
    if taker_side == "BUY":
        proxy = "taker_like_buy"
    elif taker_side == "SELL":
        proxy = "maker_like_bid"
    else:
        proxy = "unknown"
    return {
        "match_kind": best_kind,
        "match_taker_side": taker_side,
        "match_trade_ts_ms": int(best["trade_ts_ms"]),
        "match_trade_iso": iso_ms(int(best["trade_ts_ms"])),
        "match_time_diff_ms": int(best["trade_ts_ms"]) - int(xuan["xuan_ts_ms"]),
        "match_price": float(best["price"]),
        "match_size": float(best["size"]),
        "match_price_diff": round(abs(float(best["price"]) - price), 9),
        "match_size_diff": round(abs(float(best["size"]) - size), 9),
        "execution_proxy": proxy,
    }


def rate(num: int, den: int) -> float | None:
    return round(num / den, 6) if den else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(row["execution_proxy"] for row in rows)
    kinds = Counter(row["match_kind"] for row in rows)
    exact = [row for row in rows if row["match_kind"] == "price_size_match"]
    exact_counts = Counter(row["execution_proxy"] for row in exact)
    return {
        "trade_count": len(rows),
        "match_kind_counts": dict(sorted(kinds.items())),
        "execution_proxy_counts": dict(sorted(counts.items())),
        "matched_rate": rate(len(rows) - counts.get("unknown", 0), len(rows)),
        "price_size_match_count": len(exact),
        "price_size_match_rate": rate(len(exact), len(rows)),
        "price_size_execution_proxy_counts": dict(sorted(exact_counts.items())),
        "taker_like_buy_rate_all": rate(counts.get("taker_like_buy", 0), len(rows)),
        "maker_like_bid_rate_all": rate(counts.get("maker_like_bid", 0), len(rows)),
        "taker_like_buy_rate_exact": rate(exact_counts.get("taker_like_buy", 0), len(exact)),
        "maker_like_bid_rate_exact": rate(exact_counts.get("maker_like_bid", 0), len(exact)),
    }


def phase_summaries(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for phase in ("open_residual", "clean_completion", "unknown"):
        xs = [row for row in rows if row.get("event_phase") == phase]
        if xs:
            out[phase] = summarize(xs)
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
        "# Xuan Public Trade Match Execution Proxy",
        "",
        "## Scope",
        "",
        f"- xuan_trades: `{report['xuan_trades_path']}`",
        f"- replay_root: `{report['replay_root']}`",
        f"- days: `{report['days']}`",
        f"- windows_ms: `{report['windows_ms']}`",
        "- Read-only replay SQLite. No raw data, no DB writes.",
        "- This is not address-level truth; it matches public xuan trades to public market trades.",
        "",
        "## Summary By Window",
        "",
        "| window | trades | exact price+size | matched | taker-like all | maker-like all | taker-like exact | maker-like exact |",
        "|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for window, item in report["by_window"].items():
        lines.append(
            f"| {window} | {item['trade_count']} | {item['price_size_match_rate']} | {item['matched_rate']} | "
            f"{item['taker_like_buy_rate_all']} | {item['maker_like_bid_rate_all']} | "
            f"{item['taker_like_buy_rate_exact']} | {item['maker_like_bid_rate_exact']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `taker_like_buy` means the matched public trade has `taker_side=BUY` on xuan's bought outcome.",
            "- `maker_like_bid` means the matched public trade has `taker_side=SELL`, consistent with xuan resting a bid.",
            "- Exact price+size matches are the highest-confidence subset.",
            "- If exact matches skew taker-like, xuan's recent edge cannot be modeled as pure maker-first.",
            "",
            "## Phase Summary",
            "",
            "| window | phase | trades | exact price+size | taker-like exact | maker-like exact |",
            "|---:|---|---:|---:|---:|---:|",
        ]
    )
    for window, phases in report["by_window_phase"].items():
        for phase, item in phases.items():
            lines.append(
                f"| {window} | {phase} | {item['trade_count']} | {item['price_size_match_rate']} | "
                f"{item['taker_like_buy_rate_exact']} | {item['maker_like_bid_rate_exact']} |"
            )
    lines.extend(
        [
            "",
            "Highest-confidence interpretation:",
            "",
            "- If both `open_residual` and `clean_completion` exact matches are taker-like, the observed xuan cycle is not maker-only.",
            "- This does not prove every unmatched trade is taker; it proves the matched recent overlap is dominated by taker-side BUY prints.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--xuan-trades", default="data/exports/xuan_tranche_ladder_latest_20260501/xuan_trades_raw.json")
    parser.add_argument("--events-csv", default="data/exports/xuan_tranche_ladder_latest_20260501/xuan_inventory_events.csv")
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--windows-ms", default="1000,3000,5000")
    parser.add_argument("--price-tol", type=float, default=0.005)
    parser.add_argument("--size-tol-abs", type=float, default=0.02)
    parser.add_argument("--size-tol-rel", type=float, default=0.002)
    parser.add_argument("--output-dir", default="data/exports/xuan_public_trade_match_20260501")
    args = parser.parse_args()

    days = [day.strip() for day in args.days.split(",") if day.strip()]
    day_set = set(days)
    windows = [int(x.strip()) for x in args.windows_ms.split(",") if x.strip()]
    xuan_rows = load_xuan_trades(Path(args.xuan_trades), day_set)
    attach_event_fields(xuan_rows, load_event_index(Path(args.events_csv)))

    conns: dict[str, sqlite3.Connection] = {}
    day_max: dict[str, int | None] = {}
    try:
        for day in days:
            path = Path(args.replay_root) / day / "crypto_5m.sqlite"
            if not path.exists():
                continue
            conn = connect_ro(path)
            conns[day] = conn
            day_max[day] = load_day_max_ms(conn)

        by_window_rows: dict[int, list[dict[str, Any]]] = {window: [] for window in windows}
        for xuan in xuan_rows:
            conn = conns.get(xuan["day"])
            max_ms = day_max.get(xuan["day"])
            if conn is None or max_ms is None or xuan["xuan_ts_ms"] > max_ms:
                continue
            for window in windows:
                candidates = fetch_trade_candidates(
                    conn,
                    xuan["condition_id"],
                    xuan["market_side"],
                    int(xuan["xuan_ts_ms"]) - window,
                    int(xuan["xuan_ts_ms"]) + window,
                )
                row = {
                    **xuan,
                    "window_ms": window,
                    "candidate_public_trade_count": len(candidates),
                    **match_one(xuan, candidates, args.price_tol, args.size_tol_abs, args.size_tol_rel),
                }
                by_window_rows[window].append(row)
    finally:
        for conn in conns.values():
            conn.close()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for window, rows in by_window_rows.items():
        write_csv(output_dir / f"xuan_public_trade_match_{window}ms.csv", rows)
    report = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "xuan_trades_path": str(Path(args.xuan_trades).resolve()),
        "events_csv": str(Path(args.events_csv).resolve()),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": days,
        "windows_ms": windows,
        "parameters": {
            "price_tol": args.price_tol,
            "size_tol_abs": args.size_tol_abs,
            "size_tol_rel": args.size_tol_rel,
        },
        "by_window": {str(window): summarize(rows) for window, rows in by_window_rows.items()},
        "by_window_phase": {str(window): phase_summaries(rows) for window, rows in by_window_rows.items()},
        "outputs": {
            "output_dir": str(output_dir.resolve()),
            "report_md": str((output_dir / "xuan_public_trade_match_report.md").resolve()),
            "summary_json": str((output_dir / "xuan_public_trade_match_summary.json").resolve()),
        },
    }
    (output_dir / "xuan_public_trade_match_summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "xuan_public_trade_match_report.md").write_text(render_report(report), encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "windows": windows}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

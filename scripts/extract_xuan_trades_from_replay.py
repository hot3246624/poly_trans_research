#!/usr/bin/env python3
"""Extract deduplicated xuan public trades from replay SQLite.

Input is read-only replay SQLite. Output is a Data-API-shaped JSON file so the
existing xuan analysis scripts can consume replay-native public truth without
fetching from the network or reading old exports as the source of truth.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DAYS = ("2026-04-27", "2026-04-28", "2026-04-29", "2026-04-30", "2026-05-01")
TRUSTED_START_MS = int(dt.datetime(2026, 4, 27, 7, 25, tzinfo=dt.timezone.utc).timestamp() * 1000)


def parse_iso_ms(value: str | None) -> int | None:
    if not value:
        return None
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def iso_ms(ms: int | None) -> str | None:
    if ms is None:
        return None
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def fallback_raw(row: sqlite3.Row) -> dict[str, Any]:
    outcome = row["outcome"]
    outcome_side = row["outcome_side"] if "outcome_side" in row.keys() else None
    if outcome_side == "YES":
        outcome_index = 0
    elif outcome_side == "NO":
        outcome_index = 1
    elif outcome in {"Up", "YES", "Yes"}:
        outcome_index = 0
    elif outcome in {"Down", "NO", "No"}:
        outcome_index = 1
    else:
        outcome_index = 0 if str(outcome).lower() == "up" else 1
    return {
        "proxyWallet": row["proxy_wallet"],
        "side": row["side"],
        "asset": row["asset"],
        "conditionId": row["condition_id"],
        "size": row["size"],
        "price": row["price"],
        "timestamp": int(row["trade_ts_ms"] / 1000) if row["trade_ts_ms"] is not None else None,
        "title": row["title"],
        "slug": row["slug"],
        "eventSlug": row["event_slug"],
        "outcome": outcome,
        "outcomeSide": outcome_side,
        "outcomeIndex": outcome_index,
        "transactionHash": row["tx_hash"],
    }


def dedupe_key(raw: dict[str, Any], row: sqlite3.Row) -> tuple[Any, ...]:
    tx = raw.get("transactionHash") or row["tx_hash"]
    if tx:
        return ("tx", tx)
    trade_id = raw.get("tradeId") or row["trade_id"]
    if trade_id:
        return ("trade_id", trade_id)
    return (
        "fields",
        raw.get("conditionId") or row["condition_id"],
        raw.get("timestamp") or (int(row["trade_ts_ms"] / 1000) if row["trade_ts_ms"] else None),
        raw.get("outcome") or row["outcome"],
        raw.get("side") or row["side"],
        round(float(raw.get("price") or row["price"] or 0.0), 8),
        round(float(raw.get("size") or row["size"] or 0.0), 8),
    )


def extract_day(db_path: Path, start_ms: int, end_ms: int | None) -> list[dict[str, Any]]:
    conn = connect_ro(db_path)
    try:
        query = """
            SELECT *
            FROM xuan_trades
            WHERE side='BUY'
              AND slug LIKE 'btc-updown-5m-%'
              AND trade_ts_ms IS NOT NULL
              AND trade_ts_ms >= ?
        """
        params: list[Any] = [start_ms]
        if end_ms is not None:
            query += " AND trade_ts_ms < ?"
            params.append(end_ms)
        query += " ORDER BY trade_ts_ms DESC, id DESC"
        out = []
        for row in conn.execute(query, params):
            try:
                raw = json.loads(row["raw_json"])
            except Exception:
                raw = fallback_raw(row)
            out.append({"raw": raw, "row": row})
        return out
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", default="data/replay")
    parser.add_argument("--days", default=",".join(DEFAULT_DAYS))
    parser.add_argument("--start-iso")
    parser.add_argument("--end-iso")
    parser.add_argument("--output-json", required=True)
    parser.add_argument("--summary-json")
    args = parser.parse_args()

    start_ms = parse_iso_ms(args.start_iso) or TRUSTED_START_MS
    end_ms = parse_iso_ms(args.end_iso)
    seen = set()
    rows: list[dict[str, Any]] = []
    day_summaries = []
    for day in [x.strip() for x in args.days.split(",") if x.strip()]:
        db_path = Path(args.replay_root) / day / "crypto_5m.sqlite"
        if not db_path.exists():
            day_summaries.append({"day": day, "db_path": str(db_path), "exists": False, "raw_rows": 0, "new_rows": 0})
            continue
        extracted = extract_day(db_path, start_ms, end_ms)
        new_count = 0
        for item in extracted:
            key = dedupe_key(item["raw"], item["row"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(item["raw"])
            new_count += 1
        day_summaries.append(
            {"day": day, "db_path": str(db_path), "exists": True, "raw_rows": len(extracted), "new_rows": new_count}
        )
    rows.sort(key=lambda row: int(row.get("timestamp") or 0), reverse=True)
    output_json = Path(args.output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    ts_values = [int(row.get("timestamp") or 0) for row in rows if row.get("timestamp")]
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "replay_root": str(Path(args.replay_root).resolve()),
        "days": [x.strip() for x in args.days.split(",") if x.strip()],
        "trusted_start_ms": start_ms,
        "trusted_start_iso": iso_ms(start_ms),
        "end_ms": end_ms,
        "end_iso": iso_ms(end_ms),
        "day_summaries": day_summaries,
        "deduped_trade_count": len(rows),
        "trade_time_range": {
            "min_s": min(ts_values) if ts_values else None,
            "max_s": max(ts_values) if ts_values else None,
            "min_iso": iso_ms(min(ts_values) * 1000) if ts_values else None,
            "max_iso": iso_ms(max(ts_values) * 1000) if ts_values else None,
        },
        "output_json": str(output_json.resolve()),
    }
    summary_json = Path(args.summary_json) if args.summary_json else output_json.with_name("xuan_trades_from_replay_summary.json")
    summary_json.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output_json": str(output_json), "deduped_trade_count": len(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

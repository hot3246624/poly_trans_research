#!/usr/bin/env python3
"""Build public execution truth/proxy stores for public Polymarket accounts.

This is the multi-account generalization of the xuan public execution store.
It intentionally does not mutate raw captures or replay SQLite. Public account
activity is fetched from the Data API, then joined to existing replay-published
market truth for strict L1/L2 context, public trade matching, and settlement.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import fcntl
import json
import math
import os
import re
import shutil
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

try:
    import duckdb
except ModuleNotFoundError as exc:  # pragma: no cover - operational guard
    raise SystemExit("duckdb is required. Run with `uv run --with duckdb python ...`.") from exc

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import build_xuan_public_execution_truth_v1 as base  # noqa: E402


ACTIVITY_URL = "https://data-api.polymarket.com/activity"
DEFAULT_STORE_NAME = "public_account_execution_truth_v1"
DEFAULT_ACCOUNTS = {
    "b27bc": {
        "wallet": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
        "regex": r"^btc-updown-5m-",
    },
    "rwo": {
        "wallet": "0xd189664c5308903476f9f079820431e4fd7d06f4",
        "regex": r"-updown-5m-",
    },
}
PAGE_LIMIT = 500
MAX_OFFSET = 3_000
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
ACTIVITY_TYPES = ("TRADE", "MERGE", "REDEEM")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_days(value: str) -> list[str]:
    days = [part.strip() for part in value.split(",") if part.strip()]
    if not days:
        raise ValueError("at least one day is required")
    return days


def day_bounds_s(day: str) -> tuple[int, int]:
    start = dt.datetime.fromisoformat(day).replace(tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp()) - 1


def normalize_outcome(row: dict[str, Any]) -> str | None:
    raw = str(row.get("outcome") or "").strip().lower()
    if raw in {"up", "yes"}:
        return "YES"
    if raw in {"down", "no"}:
        return "NO"
    idx = row.get("outcomeIndex")
    if idx is None:
        return None
    try:
        return "YES" if int(idx) == 0 else "NO"
    except (TypeError, ValueError):
        return None


def slug_of(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("eventSlug") or "")


def activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash") or row.get("txHash") or "",
        row.get("type") or "",
        row.get("conditionId") or "",
        row.get("asset") or "",
        row.get("side") or "",
        row.get("outcome") or "",
        int(row.get("timestamp") or 0),
        round(float(row.get("size") or 0.0), 8),
        round(float(row.get("price") or 0.0), 10),
        round(float(row.get("usdcSize") or 0.0), 10),
    )


def fetch_json(url: str, params: dict[str, Any], *, retries: int, timeout: int, pause_s: float) -> Any:
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{url}?{qs}", headers=HEADERS)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (
            TimeoutError,
            socket.timeout,
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(pause_s * (attempt + 1))
    raise RuntimeError(f"fetch failed url={url} params={params} exc={last_exc}")


def fetch_activity_window(
    user: str,
    typ: str,
    start_s: int,
    end_s: int,
    *,
    retries: int,
    timeout: int,
    pause_s: float,
    max_offset: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, max_offset + PAGE_LIMIT, PAGE_LIMIT):
        page = fetch_json(
            ACTIVITY_URL,
            {"user": user, "type": typ, "start": start_s, "end": end_s, "limit": PAGE_LIMIT, "offset": offset},
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([row for row in page if isinstance(row, dict)])
        if len(page) < PAGE_LIMIT:
            break
        if offset >= max_offset:
            if end_s <= start_s:
                return rows
            mid = start_s + (end_s - start_s) // 2
            return fetch_activity_window(
                user,
                typ,
                start_s,
                mid,
                retries=retries,
                timeout=timeout,
                pause_s=pause_s,
                max_offset=max_offset,
            ) + fetch_activity_window(
                user,
                typ,
                mid + 1,
                end_s,
                retries=retries,
                timeout=timeout,
                pause_s=pause_s,
                max_offset=max_offset,
            )
        time.sleep(pause_s)
    return rows


def fetch_account_day_activity(
    *,
    account_label: str,
    wallet: str,
    slug_regex: re.Pattern[str],
    day: str,
    retries: int,
    timeout: int,
    pause_s: float,
    max_offset: int,
) -> list[dict[str, Any]]:
    start_s, end_s = day_bounds_s(day)
    rows: list[dict[str, Any]] = []
    for typ in ACTIVITY_TYPES:
        rows.extend(
            fetch_activity_window(
                wallet,
                typ,
                start_s,
                end_s,
                retries=retries,
                timeout=timeout,
                pause_s=pause_s,
                max_offset=max_offset,
            )
        )
    deduped = list({activity_key(row): row for row in rows}.values())
    out: list[dict[str, Any]] = []
    for row in deduped:
        slug = slug_of(row)
        if slug and not slug_regex.search(slug):
            continue
        if not row.get("conditionId"):
            continue
        out.append(row)
    return sorted(out, key=lambda row: (int(row.get("timestamp") or 0), row.get("transactionHash") or ""))


def fetch_markets(conn: Any) -> dict[str, base.Market]:
    rows = conn.execute(
        """
        SELECT m.condition_id, m.slug, m.start_ms, m.end_ms,
               s.winner_side, s.resolution_source
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id = m.condition_id
        WHERE m.interval_sec=300
          AND m.slug LIKE '%-updown-5m-%'
        ORDER BY m.start_ms, m.condition_id
        """
    ).fetchall()
    markets: dict[str, base.Market] = {}
    for row in rows:
        start_ms = int(row["start_ms"])
        end_ms = int(row["end_ms"])
        if end_ms <= base.TRUSTED_START_MS:
            continue
        if start_ms < base.OUTAGE_END_MS and end_ms > base.OUTAGE_START_MS:
            continue
        markets[str(row["condition_id"])] = base.Market(
            condition_id=str(row["condition_id"]),
            slug=str(row["slug"]),
            start_ms=start_ms,
            end_ms=end_ms,
            winner_side=base.norm_side(row["winner_side"]),
            resolution_source=None if row["resolution_source"] is None else str(row["resolution_source"]),
        )
    return markets


def activity_to_row(idx: int, account_label: str, wallet: str, row: dict[str, Any]) -> dict[str, Any]:
    ts_s = int(row.get("timestamp") or 0)
    size = base.safe_float(row.get("size"))
    price = base.safe_float(row.get("price"))
    usdc_size = base.safe_float(row.get("usdcSize"))
    if usdc_size is None and size is not None and price is not None:
        usdc_size = size * price
    typ = str(row.get("type") or "").upper()
    side = str(row.get("side") or "").upper()
    return {
        "id": idx,
        "activity_ts_ms": ts_s * 1000,
        "recv_ms": ts_s * 1000,
        "poll_ts_ms": ts_s * 1000,
        "condition_id": str(row.get("conditionId") or ""),
        "slug": slug_of(row),
        "activity_type": typ,
        "outcome_side": normalize_outcome(row),
        "side": side if side in {"BUY", "SELL"} else typ,
        "price": price,
        "size": size,
        "usdc_size": usdc_size,
        "asset": row.get("asset"),
        "proxy_wallet": str(row.get("proxyWallet") or row.get("proxy_wallet") or wallet),
        "tx_hash": str(row.get("transactionHash") or row.get("txHash") or ""),
        "raw_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
        "_account_label": account_label,
        "_account_wallet": wallet,
    }


def public_fieldnames() -> list[str]:
    fields = ["account_label", "account_wallet", *base.fieldnames()]
    return list(dict.fromkeys(fields))


def quote_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def build_duckdb(tmp_dir: Path, csv_paths: list[Path], threads: int) -> dict[str, Any]:
    db_path = tmp_dir / "event_store.duckdb"
    conn = duckdb.connect(str(db_path))
    conn.execute(f"PRAGMA threads={max(1, int(threads))}")
    list_literal = "[" + ", ".join(quote_literal(path) for path in csv_paths) + "]"
    string_columns = {
        "account_label",
        "account_wallet",
        "day",
        "market_id",
        "condition_id",
        "slug",
        "round_start_iso",
        "event_iso",
        "event_kind",
        "source_table",
        "xuan_account",
        "wallet_or_label",
        "cycle_id",
        "order_id",
        "side",
        "action",
        "order_type",
        "status",
        "reason",
        "winner_side",
        "truth_level",
        "classification_method",
        "match_confidence",
        "public_trade_taker_side",
        "public_trade_trade_id",
        "public_trade_maker_address",
        "public_trade_taker_address",
        "xuan_trade_trade_id",
        "xuan_trade_tx_hash",
        "xuan_activity_tx_hash",
        "side_alignment",
        "high_side",
        "execution_level_kind",
        "queue_context_policy",
    }
    type_literal = "{" + ", ".join(f"{quote_literal(column)}: 'VARCHAR'" for column in sorted(string_columns)) + "}"
    conn.execute(
        f"""
        CREATE TABLE public_account_execution_events AS
        SELECT *
        FROM read_csv({list_literal}, header=true, union_by_name=true, auto_detect=true, types={type_literal})
        """
    )
    total_rows = int(conn.execute("SELECT COUNT(*) FROM public_account_execution_events").fetchone()[0])
    event_kind_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT event_kind, COUNT(*) FROM public_account_execution_events GROUP BY event_kind ORDER BY event_kind"
        ).fetchall()
    }
    truth_level_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT truth_level, COUNT(*) FROM public_account_execution_events GROUP BY truth_level ORDER BY truth_level"
        ).fetchall()
    }
    order_type_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT order_type, COUNT(*) FROM public_account_execution_events GROUP BY order_type ORDER BY order_type"
        ).fetchall()
    }
    account_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT account_label, COUNT(*) FROM public_account_execution_events GROUP BY account_label ORDER BY account_label"
        ).fetchall()
    }
    day_counts = {
        str(key): int(value)
        for key, value in conn.execute(
            "SELECT day, COUNT(*) FROM public_account_execution_events GROUP BY day ORDER BY day"
        ).fetchall()
    }
    exact_maker_fill_rows = int(
        conn.execute(
            "SELECT COUNT(*) FROM public_account_execution_events WHERE event_kind='fill' AND order_type='maker' AND is_exact_maker_fill"
        ).fetchone()[0]
    )
    dataset_dir = tmp_dir / "dataset"
    dataset_dir.mkdir(exist_ok=True)
    conn.execute(
        f"""
        COPY (SELECT * FROM public_account_execution_events)
        TO {quote_literal(dataset_dir)}
        (FORMAT PARQUET, COMPRESSION ZSTD, PARTITION_BY (account_label, day), OVERWRITE_OR_IGNORE TRUE)
        """
    )
    conn.execute("CHECKPOINT")
    conn.close()
    parquet_files = sorted(path.relative_to(tmp_dir).as_posix() for path in dataset_dir.rglob("*.parquet"))
    return {
        "duckdb": "event_store.duckdb",
        "duckdb_table": "public_account_execution_events",
        "parquet_glob": "dataset/**/*.parquet",
        "parquet_files": parquet_files,
        "row_count": total_rows,
        "event_kind_counts": event_kind_counts,
        "truth_level_counts": truth_level_counts,
        "order_type_counts": order_type_counts,
        "account_counts": account_counts,
        "day_counts": day_counts,
        "exact_maker_fill_rows": exact_maker_fill_rows,
    }


def build_market_events_cached(
    *,
    conn: Any,
    day: str,
    market: base.Market,
    activity_rows: list[dict[str, Any]],
    public_trades: list[base.PublicTrade],
    query_start_ms: int,
    query_end_ms: int,
    account_wallet: str,
    public_match_window_ms: int,
    next_book_window_ms: int,
    price_tol: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Build account events with per-market L1/L2 caches.

    The xuan builder was designed for sparse account events and can query
    SQLite per event. B27/RWO can have tens of thousands of public activity rows
    per day, so we preload only the relevant market window and let the existing
    builder call in-memory replacements for strict/next book lookup.
    """
    l1_books = base.load_l1(conn, market, query_start_ms, query_end_ms)
    l1_times = [book.recv_ms for book in l1_books]
    l2_by_side = base.load_l2(conn, market, query_start_ms, query_end_ms + next_book_window_ms)
    l2_times = {side: [book.recv_ms for book in books] for side, books in l2_by_side.items()}

    old_l1 = base.query_l1_at
    old_l2 = base.query_l2_at
    old_l2_after = base.query_l2_after

    def query_l1_cached(_conn: Any, _condition_id: str, ts_ms: int) -> tuple[base.L1Book | None, int | None]:
        return base.prev_by_time(l1_books, l1_times, ts_ms)

    def query_l2_cached(_conn: Any, _condition_id: str, side: str, ts_ms: int) -> tuple[base.L2Book | None, int | None]:
        books = l2_by_side.get(side, [])
        times = l2_times.get(side, [])
        return base.prev_by_time(books, times, ts_ms)

    def query_l2_after_cached(
        _conn: Any,
        _condition_id: str,
        side: str,
        ts_ms: int,
        max_wait_ms: int,
    ) -> tuple[base.L2Book | None, int | None]:
        books = l2_by_side.get(side, [])
        times = l2_times.get(side, [])
        return base.next_by_time(books, times, ts_ms, max_wait_ms)

    base.query_l1_at = query_l1_cached
    base.query_l2_at = query_l2_cached
    base.query_l2_after = query_l2_after_cached
    try:
        return base.build_market_events(
            conn=conn,
            day=day,
            market=market,
            activity_rows=activity_rows,
            xuan_trades=[],
            public_trades=public_trades,
            l1_books=l1_books,
            l2_by_side=l2_by_side,
            xuan_user=account_wallet,
            public_match_window_ms=public_match_window_ms,
            next_book_window_ms=next_book_window_ms,
            price_tol=price_tol,
        )
    finally:
        base.query_l1_at = old_l1
        base.query_l2_at = old_l2
        base.query_l2_after = old_l2_after


def close_writer(writer: csv.DictWriter[str]) -> None:
    handle = getattr(writer, "_xuan_handle", None)
    if handle is not None:
        handle.close()


def parse_accounts(value: str) -> dict[str, dict[str, str]]:
    selected = [part.strip() for part in value.split(",") if part.strip()]
    if not selected:
        raise ValueError("at least one account is required")
    out: dict[str, dict[str, str]] = {}
    for label in selected:
        if label not in DEFAULT_ACCOUNTS:
            raise ValueError(f"unknown account {label!r}; known={sorted(DEFAULT_ACCOUNTS)}")
        out[label] = DEFAULT_ACCOUNTS[label]
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replay-root", type=Path, required=True)
    parser.add_argument("--store-root", type=Path, required=True)
    parser.add_argument("--store-name", default=DEFAULT_STORE_NAME)
    parser.add_argument("--days", required=True)
    parser.add_argument("--label")
    parser.add_argument("--accounts", default="b27bc,rwo")
    parser.add_argument("--public-match-window-ms", type=int, default=base.DEFAULT_PUBLIC_MATCH_WINDOW_MS)
    parser.add_argument("--next-book-window-ms", type=int, default=base.DEFAULT_NEXT_BOOK_WINDOW_MS)
    parser.add_argument("--price-tol", type=float, default=1e-9)
    parser.add_argument("--min-free-gb", type=float, default=120.0)
    parser.add_argument("--duckdb-threads", type=int, default=2)
    parser.add_argument("--progress-every-markets", type=int, default=25)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--pause-s", type=float, default=0.05)
    parser.add_argument("--max-offset", type=int, default=MAX_OFFSET)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    days = parse_days(args.days)
    label = args.label or f"{days[0].replace('-', '')}_{days[-1].replace('-', '')}"
    accounts = parse_accounts(args.accounts)

    publish_root = args.store_root / args.store_name
    final_dir = publish_root / label
    tmp_dir = publish_root / f".{label}.tmp.{os.getpid()}"
    lock_path = publish_root / f".{label}.lock"
    publish_root.mkdir(parents=True, exist_ok=True)

    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        base.require_free_gb(args.store_root, args.min_free_gb)
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir)
        tmp_dir.mkdir(parents=True)

        started_at = utc_now()
        csv_dir = tmp_dir / "csv"
        csv_dir.mkdir()
        fields = public_fieldnames()
        csv_paths: list[Path] = []
        source_replay: list[dict[str, Any]] = []
        source_activity: dict[str, Any] = {}
        build_counts: dict[str, Any] = {}
        total_counts: Counter[str] = Counter()
        try:
            for day in days:
                db_path = args.replay_root / day / "crypto_5m.sqlite"
                if not db_path.is_file():
                    raise FileNotFoundError(f"missing replay SQLite for {day}: {db_path}")
                stat = db_path.stat()
                source_replay.append(
                    {
                        "day": day,
                        "path": str(db_path),
                        "size_bytes": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "sqlite_sequence": base.load_sqlite_sequence(db_path),
                    }
                )
                for account_label, cfg in accounts.items():
                    wallet = cfg["wallet"]
                    slug_regex = re.compile(cfg["regex"])
                    print(
                        json.dumps(
                            {"stage": "fetch_activity", "account_label": account_label, "day": day},
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    activity = fetch_account_day_activity(
                        account_label=account_label,
                        wallet=wallet,
                        slug_regex=slug_regex,
                        day=day,
                        retries=args.retries,
                        timeout=args.timeout,
                        pause_s=args.pause_s,
                        max_offset=args.max_offset,
                    )
                    print(
                        json.dumps(
                            {
                                "stage": "fetched_activity",
                                "account_label": account_label,
                                "day": day,
                                "activity_rows": len(activity),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )
                    source_activity[f"{account_label}:{day}"] = {
                        "account_label": account_label,
                        "wallet": wallet,
                        "regex": cfg["regex"],
                        "activity_rows": len(activity),
                    }
                    day_csv = csv_dir / f"{account_label}_{day}.csv"
                    csv_paths.append(day_csv)
                    writer = base.write_csv_header(day_csv, fields)
                    day_counts: Counter[str] = Counter()
                    try:
                        with base.connect_ro(db_path) as conn:
                            markets = fetch_markets(conn)
                            activity_rows_by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
                            for idx, raw in enumerate(activity, start=1):
                                row = activity_to_row(idx, account_label, wallet, raw)
                                cid = row["condition_id"]
                                if cid in markets:
                                    activity_rows_by_condition[cid].append(row)
                            day_counts["markets"] = len(markets)
                            day_counts["activity_rows"] = len(activity)
                            day_counts["matched_activity_rows"] = sum(len(v) for v in activity_rows_by_condition.values())
                            day_counts["markets_with_activity"] = len(activity_rows_by_condition)
                            for idx, (condition_id, activity_rows) in enumerate(
                                sorted(activity_rows_by_condition.items()), start=1
                            ):
                                market = markets[condition_id]
                                event_times = [int(row["activity_ts_ms"]) for row in activity_rows]
                                query_start_ms = max(
                                    market.start_ms,
                                    min(event_times) - args.public_match_window_ms - 5_000,
                                )
                                query_end_ms = min(
                                    market.end_ms,
                                    max(event_times) + args.next_book_window_ms + 5_000,
                                )
                                public_trades = base.load_public_trades(
                                    conn,
                                    condition_id,
                                    query_start_ms - args.public_match_window_ms,
                                    query_end_ms + args.public_match_window_ms,
                                )
                                rows, counts = build_market_events_cached(
                                    conn=conn,
                                    day=day,
                                    market=market,
                                    activity_rows=activity_rows,
                                    public_trades=public_trades,
                                    query_start_ms=query_start_ms,
                                    query_end_ms=query_end_ms,
                                    account_wallet=wallet,
                                    public_match_window_ms=args.public_match_window_ms,
                                    next_book_window_ms=args.next_book_window_ms,
                                    price_tol=args.price_tol,
                                )
                                for row in rows:
                                    row["account_label"] = account_label
                                    row["account_wallet"] = wallet
                                    row["source_table"] = "public_account_activity" if row["source_table"] == "xuan_activity" else row["source_table"]
                                    writer.writerow(row)
                                day_counts.update(counts)
                                total_counts.update(counts)
                                if args.progress_every_markets > 0 and idx % args.progress_every_markets == 0:
                                    print(
                                        json.dumps(
                                            {
                                                "stage": "build_account_day",
                                                "account_label": account_label,
                                                "day": day,
                                                "markets_done": idx,
                                                "markets_with_activity": len(activity_rows_by_condition),
                                                "day_counts": dict(day_counts),
                                            },
                                            ensure_ascii=False,
                                            sort_keys=True,
                                        ),
                                        flush=True,
                                    )
                    finally:
                        close_writer(writer)
                    build_counts[f"{account_label}:{day}"] = dict(day_counts)
                    print(
                        json.dumps(
                            {
                                "stage": "finished_account_day",
                                "account_label": account_label,
                                "day": day,
                                "day_counts": dict(day_counts),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        ),
                        flush=True,
                    )

            outputs = build_duckdb(tmp_dir, csv_paths, args.duckdb_threads)
            manifest = {
                "schema_version": "public_account_execution_truth_v1",
                "store_name": args.store_name,
                "label": label,
                "days": days,
                "accounts": accounts,
                "generated_at_utc": utc_now(),
                "started_at_utc": started_at,
                "source": "data_api_activity_plus_replay_published_sqlite",
                "source_replay": source_replay,
                "source_activity": source_activity,
                "build_counts": build_counts,
                "total_build_counts": dict(total_counts),
                "outputs": outputs,
                "truth_policy": {
                    "is_private_truth": False,
                    "public_execution_truth": "Data API public account activity rows are public execution observations.",
                    "strict_market_context": "L1/L2 context uses latest replay rows with recv_ms <= event_ts_ms.",
                    "maker_role_policy": (
                        "Maker/taker is exact only for address matches in public md_trades. "
                        "time/price/size matches are public-role inference and are labeled with lower confidence."
                    ),
                    "not_reconstructed": [
                        "private order_place",
                        "private cancel",
                        "true queue_ahead",
                        "private resting lifetime",
                    ],
                },
            }
            (tmp_dir / "EVENT_STORE_MANIFEST.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            (tmp_dir / "README.md").write_text(
                "\n".join(
                    [
                        f"# public_account_execution_truth_v1 {label}",
                        "",
                        "DuckDB table: `public_account_execution_events`.",
                        "",
                        "This store joins public Data API account activity to replay-published",
                        "strict L1/L2, md_trades, and settlement context. It is public truth,",
                        "not private order/cancel/queue truth.",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            base.publish_tmp(tmp_dir, final_dir, args.force)
            print(json.dumps({"published": str(final_dir), "outputs": outputs}, ensure_ascii=False, sort_keys=True))
            return 0
        except Exception:
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir)
            raise


if __name__ == "__main__":
    raise SystemExit(main())

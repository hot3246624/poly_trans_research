#!/usr/bin/env python3
"""Build taker-BUY candidate cache with bounded-memory dense L2 streaming.

This is equivalent to build_taker_buy_signal_candidate_cache.py for the
candidate row logic, but avoids both high-memory day preloading and slow
per-market L2 queries. It performs one ordered read of the relevant L2 rows per
day and processes one condition_id at a time.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

import build_taker_buy_signal_candidate_cache as base


def load_condition_l2(
    conn: Any,
    args: argparse.Namespace,
    market: Any,
) -> dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]]:
    """Load one condition_id through the covering cond/side/recv index.

    The dense databases are large enough that a day-level IN query may allocate
    a temp ORDER BY b-tree before returning the first row. One indexed condition
    read at a time is slower than a perfect streaming scan but is memory-stable
    and starts yielding immediately on NFS.
    """
    condition_id = str(market["condition_id"])
    start_ms = max(int(market["start_ms"]), base.TRUSTED_START_MS)
    end_ms = min(
        int(market["end_ms"]),
        int(market["start_ms"]) + (args.max_offset_s + args.completion_s) * 1000,
    )
    rows = conn.execute(
        """
        SELECT market_side, recv_ms,
               ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz,
               ask4_px, ask4_sz, ask5_px, ask5_sz
        FROM md_book_l2 l
        WHERE condition_id = ?
          AND market_side IN ('YES', 'NO')
          AND recv_ms >= ?
          AND recv_ms <= ?
        ORDER BY market_side, recv_ms, id
        """,
        (condition_id, start_ms, end_ms),
    )
    out: dict[tuple[str, str], tuple[list[int], list[list[tuple[float, float]]]]] = {}
    for row in rows:
        levels = base.ask_levels(row)
        if not levels:
            continue
        key = (condition_id, str(row["market_side"]))
        if key not in out:
            out[key] = ([], [])
        out[key][0].append(int(row["recv_ms"]))
        out[key][1].append(levels)
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-root", type=Path, default=Path("data/replay"))
    parser.add_argument("--days", default="2026-05-02,2026-05-06")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--min-trade-price", type=float, default=0.50)
    parser.add_argument("--max-trade-price", type=float, default=0.75)
    parser.add_argument("--min-trade-size", type=float, default=50.0)
    parser.add_argument("--max-trade-size", type=float, default=250.0)
    parser.add_argument("--min-offset-s", type=int, default=0)
    parser.add_argument("--max-offset-s", type=int, default=240)
    parser.add_argument("--completion-s", type=int, default=30)
    parser.add_argument("--max-l2-age-ms", type=int, default=750)
    parser.add_argument("--clip", type=float, default=60.0)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    rows: list[dict[str, Any]] = []
    market_count = 0
    empty_l2_trigger_markets = 0
    for day in [part.strip() for part in args.days.split(",") if part.strip()]:
        db_path = args.replay_root / day / "crypto_5m.sqlite"
        if not db_path.exists():
            print(f"{day}: missing {db_path}", file=sys.stderr, flush=True)
            continue
        with base.ro_connect(db_path) as conn:
            markets = base.load_markets(conn)
            market_count += len(markets)
            market_by_condition = {str(market["condition_id"]): market for market in markets}
            trades_by_condition = base.load_day_trigger_trades(conn, args)
            trigger_condition_ids = sorted(cid for cid in trades_by_condition if cid in market_by_condition)
            print(
                f"{day}: markets={len(markets)} trigger_markets={len(trigger_condition_ids)} stream_l2=true",
                file=sys.stderr,
                flush=True,
            )

            processed = 0
            with_l2 = 0
            for condition_id in trigger_condition_ids:
                market = market_by_condition.get(condition_id)
                if market is None:
                    continue
                day_l2 = load_condition_l2(conn, args, market)
                if day_l2:
                    with_l2 += 1
                rows.extend(
                    base.market_candidates(
                        conn,
                        market,
                        trades_by_condition.get(condition_id, []),
                        day_l2,
                        args,
                    )
                )
                processed += 1
                if args.progress_every > 0 and processed % args.progress_every == 0:
                    print(
                        f"{day}: processed_trigger_markets={processed} with_l2={with_l2} rows={len(rows)}",
                        file=sys.stderr,
                        flush=True,
                    )
            missing_l2 = len(trigger_condition_ids) - with_l2
            empty_l2_trigger_markets += missing_l2
            print(
                f"{day}: done processed_trigger_markets={processed} missing_l2_trigger_markets={missing_l2} rows={len(rows)}",
                file=sys.stderr,
                flush=True,
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "taker_buy_signal_candidate_cache.csv"
    base.write_csv(csv_path, rows)
    summary = {
        "generated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "parameters": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "market_count": market_count,
        "candidate_rows": len(rows),
        "empty_l2_trigger_markets": empty_l2_trigger_markets,
        "csv_path": str(csv_path),
        "builder": "dense_l2_index_loop_by_condition",
        "l1_policy": "strict_l1_at_or_before_trigger_ts_ms",
        "strict_l1_max_age_ms": base.MAX_L1_AGE_MS,
    }
    (args.output_dir / "taker_buy_signal_candidate_cache_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Collect a bounded public /trades history for one Polymarket account.

The endpoint returns public trade rows without ``activity.usdcSize``.  The
output is therefore a gross trade/settlement proxy and must not be presented
as fee-inclusive cash PnL.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import random
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


URL = "https://data-api.polymarket.com/trades"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def parse_iso(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fetch(params: dict[str, object], *, retries: int, timeout: int, pause: float):
    query = urllib.parse.urlencode(params)
    req = urllib.request.Request(f"{URL}?{query}", headers=HEADERS)
    last: Exception | None = None
    for attempt in range(max(retries, 10)):
        try:
            with urllib.request.urlopen(req, timeout=max(timeout, 45)) as response:
                data = json.loads(response.read().decode())
            time.sleep(pause)
            return data if isinstance(data, list) else data.get("data", [])
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, socket.timeout, json.JSONDecodeError) as exc:
            last = exc
            if isinstance(exc, urllib.error.HTTPError) and exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                break
            time.sleep(min(90.0, 1.5 * (2**attempt)) + random.uniform(0.0, 0.75))
    raise RuntimeError(f"fetch failed params={params} error={last}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--pause", type=float, default=1.2)
    parser.add_argument("--timeout", type=int, default=45)
    args = parser.parse_args()
    start_s = parse_iso(args.start_iso)
    end_s = parse_iso(args.end_iso)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    out = args.output_dir / "trades.jsonl"
    rows: list[dict] = []
    offset = 0
    pages = 0
    while True:
        page = fetch(
            {"user": args.user, "limit": args.limit, "offset": offset, "takerOnly": "false"},
            retries=12,
            timeout=args.timeout,
            pause=args.pause,
        )
        pages += 1
        if not page:
            break
        in_window = [
            row for row in page
            if start_s <= int(row.get("timestamp") or 0) <= end_s
        ]
        rows.extend(in_window)
        timestamps = [int(row.get("timestamp") or 0) for row in page]
        oldest = min(timestamps) if timestamps else None
        print(json.dumps({"page": pages, "offset": offset, "page_rows": len(page), "kept_rows": len(in_window), "oldest_ts": oldest}), flush=True)
        if oldest is not None and oldest < start_s:
            break
        if len(page) < args.limit:
            break
        offset += args.limit

    rows.sort(key=lambda row: (int(row.get("timestamp") or 0), str(row.get("transactionHash") or "")))
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    summary = {
        "user": args.user,
        "source": URL,
        "start_iso": args.start_iso,
        "end_iso": args.end_iso,
        "pages": pages,
        "rows": len(rows),
        "gross_only": True,
        "fee_inclusive": False,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    (args.output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

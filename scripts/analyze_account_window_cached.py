#!/usr/bin/env python3
"""Cached fee-inclusive public activity analysis for one Polymarket account/window."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import http.client
import json
import math
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ACTIVITY_URL = "https://data-api.polymarket.com/activity"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
PAGE_LIMIT = 500
MAX_OFFSET = 3000
EPS = 1e-9


def parse_iso(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def bjt_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone(dt.timedelta(hours=8))).isoformat()


def round6(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def pct(num: float, den: float) -> float | None:
    return round(num / den, 6) if abs(den) > EPS else None


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
            ConnectionResetError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            urllib.error.URLError,
            urllib.error.HTTPError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(pause_s * (attempt + 1))
    raise RuntimeError(f"fetch failed url={url} params={params} exc={last_exc}")


def activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash") or "",
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


def slug_of(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("eventSlug") or "")


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


def classify_asset_tf(slug: str, title: str | None = None) -> tuple[str, str]:
    text = f"{slug} {title or ''}".lower()
    asset = "OTHER"
    for token, label in (
        ("bitcoin", "BTC"),
        ("btc", "BTC"),
        ("ethereum", "ETH"),
        ("eth", "ETH"),
        ("solana", "SOL"),
        ("sol", "SOL"),
        ("xrp", "XRP"),
    ):
        if re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", text):
            asset = label
            break
    if "updown-15m-" in slug:
        tf = "15m"
    elif "updown-5m-" in slug:
        tf = "5m"
    elif "updown-4h-" in slug:
        tf = "4h"
    elif "up-or-down" in slug:
        tf = "1h_or_named"
    else:
        tf = "other"
    return asset, tf


def is_updown(row: dict[str, Any]) -> bool:
    slug = slug_of(row).lower()
    return bool(re.search(r"(^|-)updown-(5m|15m|4h)-\d{10}$", slug) or "up-or-down" in slug)


def fetch_activity_window(
    user: str,
    typ: str,
    start_s: int,
    end_s: int,
    *,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, MAX_OFFSET + 1, PAGE_LIMIT):
        page = fetch_json(
            ACTIVITY_URL,
            {"user": user, "type": typ, "start": start_s, "end": end_s, "limit": PAGE_LIMIT, "offset": offset},
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([r for r in page if isinstance(r, dict)])
        if len(page) < PAGE_LIMIT:
            break
        if offset >= MAX_OFFSET:
            if end_s <= start_s:
                return rows
            mid = start_s + (end_s - start_s) // 2
            return fetch_activity_window(user, typ, start_s, mid, retries=retries, timeout=timeout, pause_s=pause_s) + fetch_activity_window(
                user, typ, mid + 1, end_s, retries=retries, timeout=timeout, pause_s=pause_s
            )
        time.sleep(pause_s)
    return rows


def cached_activity(
    user: str,
    typ: str,
    start_s: int,
    end_s: int,
    cache_dir: Path,
    *,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    out: list[dict[str, Any]] = []
    cursor = start_s
    while cursor <= end_s:
        win_end = min(end_s, cursor + 3600 - 1)
        path = cache_dir / f"{typ}_{cursor}_{win_end}.json"
        if path.exists():
            rows = json.loads(path.read_text())
        else:
            print(f"[fetch] {typ} {iso_s(cursor)} {iso_s(win_end)}", flush=True)
            rows = fetch_activity_window(user, typ, cursor, win_end, retries=retries, timeout=timeout, pause_s=pause_s)
            path.write_text(json.dumps(rows, ensure_ascii=False) + "\n")
        out.extend(rows)
        cursor = win_end + 1
    return out


def fetch_positions(user: str, *, retries: int, timeout: int, pause_s: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, 50_000, PAGE_LIMIT):
        page = fetch_json(POSITIONS_URL, {"user": user, "limit": PAGE_LIMIT, "offset": offset}, retries=retries, timeout=timeout, pause_s=pause_s)
        if not isinstance(page, list) or not page:
            break
        rows.extend([r for r in page if isinstance(r, dict)])
        if len(page) < PAGE_LIMIT:
            break
        time.sleep(pause_s)
    return rows


def gamma_outcome(slug: str, *, retries: int, timeout: int, pause_s: float) -> dict[str, Any]:
    for attempt in range(retries):
        try:
            data = fetch_json(GAMMA_EVENTS_URL, {"slug": slug}, retries=1, timeout=timeout, pause_s=pause_s)
            if not data:
                return {"slug": slug, "winner": None, "error": "not_found"}
            market = (data[0].get("markets") or [{}])[0]
            prices = json.loads(market.get("outcomePrices") or "[]")
            outcomes = json.loads(market.get("outcomes") or "[]")
            winner = None
            for outcome, price in zip(outcomes, prices):
                try:
                    if float(price) >= 0.999:
                        norm = str(outcome).strip().upper()
                        winner = "UP" if norm in {"UP", "YES"} else "DOWN" if norm in {"DOWN", "NO"} else norm
                except (TypeError, ValueError):
                    pass
            return {"slug": slug, "winner": winner, "closed": market.get("closed"), "closedTime": market.get("closedTime"), "outcomePrices": prices}
        except Exception as exc:  # noqa: BLE001
            if attempt + 1 == retries:
                return {"slug": slug, "winner": None, "error": str(exc)}
            time.sleep(pause_s * (attempt + 1))
    return {"slug": slug, "winner": None, "error": "unreachable"}


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", required=True)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--pause-ms", type=int, default=120)
    parser.add_argument("--resolve-outcomes", action="store_true")
    args = parser.parse_args()

    start_s = parse_iso(args.start_iso)
    end_s = parse_iso(args.end_iso)
    pause_s = args.pause_ms / 1000.0
    out_dir = Path(args.output_dir)
    cache_dir = out_dir / "cache"
    out_dir.mkdir(parents=True, exist_ok=True)

    types = ["TRADE", "MERGE", "REDEEM", "MAKER_REBATE", "REWARD", "REFERRAL_REWARD", "SPLIT"]
    raw_rows: list[dict[str, Any]] = []
    for typ in types:
        raw_rows.extend(cached_activity(args.user, typ, start_s, end_s, cache_dir, retries=args.retries, timeout=args.timeout, pause_s=pause_s))
    raw_rows = sorted({activity_key(r): r for r in raw_rows}.values(), key=lambda r: (int(r.get("timestamp") or 0), str(r.get("transactionHash") or "")))
    rows = [r for r in raw_rows if is_updown(r)]
    positions = fetch_positions(args.user, retries=args.retries, timeout=args.timeout, pause_s=pause_s)

    pos: dict[str, dict[str, float]] = defaultdict(lambda: {"YES": 0.0, "NO": 0.0, "value": 0.0})
    for row in positions:
        cid = str(row.get("conditionId") or "")
        outcome = normalize_outcome(row)
        if cid and outcome in {"YES", "NO"}:
            pos[cid][outcome] += float(row.get("size") or 0.0)
            pos[cid]["value"] += float(row.get("currentValue") or 0.0)

    by: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "condition_id": "",
            "slug": "",
            "title": "",
            "asset": "OTHER",
            "tf": "other",
            "trade_count": 0,
            "buy_actual": 0.0,
            "buy_gross": 0.0,
            "fee": 0.0,
            "cash_in": 0.0,
            "split": 0.0,
            "up_qty": 0.0,
            "down_qty": 0.0,
            "up_actual": 0.0,
            "down_actual": 0.0,
            "up_gross": 0.0,
            "down_gross": 0.0,
            "first_trade_s": None,
            "last_trade_s": None,
        }
    )
    all_counts: Counter[str] = Counter()
    counts: Counter[str] = Counter()
    no_condition_rebate = 0.0
    for row in raw_rows:
        typ = str(row.get("type") or "").upper()
        all_counts[typ] += 1
        if not row.get("conditionId") and typ in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            no_condition_rebate += float(row.get("usdcSize") or 0.0)
    for row in rows:
        typ = str(row.get("type") or "").upper()
        counts[typ] += 1
        qty = float(row.get("size") or 0.0)
        price = float(row.get("price") or 0.0)
        gross = qty * price
        usdc = float(row.get("usdcSize") or gross)
        cid = str(row.get("conditionId") or "")
        if not cid:
            continue
        item = by[cid]
        item["condition_id"] = cid
        if not item["slug"]:
            item["slug"] = slug_of(row)
            item["title"] = row.get("title") or ""
            item["asset"], item["tf"] = classify_asset_tf(item["slug"], item["title"])
        if typ == "TRADE":
            side = str(row.get("side") or "").upper()
            if side == "BUY":
                item["trade_count"] += 1
                item["buy_actual"] += usdc
                item["buy_gross"] += gross
                item["fee"] += usdc - gross
                ts = int(row.get("timestamp") or 0)
                item["first_trade_s"] = ts if item["first_trade_s"] is None else min(item["first_trade_s"], ts)
                item["last_trade_s"] = ts if item["last_trade_s"] is None else max(item["last_trade_s"], ts)
                outcome = normalize_outcome(row)
                if outcome == "YES":
                    item["up_qty"] += qty
                    item["up_actual"] += usdc
                    item["up_gross"] += gross
                elif outcome == "NO":
                    item["down_qty"] += qty
                    item["down_actual"] += usdc
                    item["down_gross"] += gross
            elif side == "SELL":
                item["cash_in"] += usdc
        elif typ in {"MERGE", "REDEEM", "MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            item["cash_in"] += usdc
        elif typ == "SPLIT":
            item["split"] += usdc

    outcomes: dict[str, dict[str, Any]] = {}
    if args.resolve_outcomes:
        for slug in sorted({v["slug"] for v in by.values() if v["slug"]}):
            outcomes[slug] = gamma_outcome(slug, retries=args.retries, timeout=args.timeout, pause_s=pause_s)
            time.sleep(pause_s)

    market_rows: list[dict[str, Any]] = []
    summary = defaultdict(float)
    summary["markets"] = 0
    summary["resolved_markets"] = 0
    summary["unresolved_markets"] = 0
    for item in by.values():
        buy_qty = item["up_qty"] + item["down_qty"]
        paired = min(item["up_qty"], item["down_qty"])
        resid = abs(item["up_qty"] - item["down_qty"])
        up_avg = item["up_actual"] / item["up_qty"] if item["up_qty"] > EPS else None
        down_avg = item["down_actual"] / item["down_qty"] if item["down_qty"] > EPS else None
        up_gross_avg = item["up_gross"] / item["up_qty"] if item["up_qty"] > EPS else None
        down_gross_avg = item["down_gross"] / item["down_qty"] if item["down_qty"] > EPS else None
        pair_cost = up_avg + down_avg if paired > EPS and up_avg is not None and down_avg is not None else None
        gross_pair_cost = up_gross_avg + down_gross_avg if paired > EPS and up_gross_avg is not None and down_gross_avg is not None else None
        pair_pnl = paired * (1.0 - pair_cost) if pair_cost is not None else 0.0
        pitem = pos.get(item["condition_id"], {"YES": 0.0, "NO": 0.0, "value": 0.0})
        cash_pnl = item["cash_in"] - item["buy_actual"] - item["split"]
        outcome = outcomes.get(item["slug"], {})
        winner = outcome.get("winner")
        final_value = None
        if winner == "UP":
            final_value = item["up_qty"]
        elif winner == "DOWN":
            final_value = item["down_qty"]
        final_pnl = final_value - item["buy_actual"] if final_value is not None else None
        row = {
            "condition_id": item["condition_id"],
            "slug": item["slug"],
            "asset": item["asset"],
            "tf": item["tf"],
            "trade_count": item["trade_count"],
            "first_trade_bjt": bjt_s(item["first_trade_s"]),
            "last_trade_bjt": bjt_s(item["last_trade_s"]),
            "buy_actual": round6(item["buy_actual"]),
            "buy_gross": round6(item["buy_gross"]),
            "fee": round6(item["fee"]),
            "cash_in": round6(item["cash_in"]),
            "cash_pnl": round6(cash_pnl),
            "current_value": round6(pitem["value"]),
            "paired_qty": round6(paired),
            "resid_qty": round6(resid),
            "resid_rate": pct(resid, buy_qty),
            "resid_side": "UP" if item["up_qty"] > item["down_qty"] else "DOWN" if item["down_qty"] > item["up_qty"] else "FLAT",
            "actual_pair_cost": round6(pair_cost),
            "gross_pair_cost": round6(gross_pair_cost),
            "pair_pnl": round6(pair_pnl),
            "winner": winner,
            "final_value": round6(final_value),
            "final_pnl": round6(final_pnl),
            "outcome_error": outcome.get("error"),
        }
        market_rows.append(row)
        summary["markets"] += 1
        summary["buy_actual"] += item["buy_actual"]
        summary["buy_gross"] += item["buy_gross"]
        summary["fee"] += item["fee"]
        summary["cash_in"] += item["cash_in"]
        summary["cash_pnl"] += cash_pnl
        summary["current_value"] += pitem["value"]
        summary["paired_qty"] += paired
        summary["resid_qty"] += resid
        summary["buy_qty"] += buy_qty
        summary["pair_pnl"] += pair_pnl
        if final_pnl is None:
            summary["unresolved_markets"] += 1
            summary["unresolved_buy_actual"] += item["buy_actual"]
            summary["unresolved_current_value"] += pitem["value"]
            summary["unresolved_cash_pnl"] += cash_pnl
        else:
            summary["resolved_markets"] += 1
            summary["resolved_buy_actual"] += item["buy_actual"]
            summary["resolved_final_value"] += final_value or 0.0
            summary["resolved_final_pnl"] += final_pnl
            summary["resolved_pair_pnl"] += pair_pnl

    pair_cost = (summary["paired_qty"] - summary["pair_pnl"]) / summary["paired_qty"] if summary["paired_qty"] > EPS else None
    result = {
        "user": args.user,
        "window": {"start_bjt": bjt_s(start_s), "end_bjt": bjt_s(end_s)},
        "all_activity_counts": dict(sorted(all_counts.items())),
        "activity_counts": dict(sorted(counts.items())),
        "no_condition_rebate": round6(no_condition_rebate),
        **{k: round6(v) for k, v in summary.items()},
        "fee_rate_on_gross": pct(summary["fee"], summary["buy_gross"]),
        "resid_rate_on_buy_qty": pct(summary["resid_qty"], summary["buy_qty"]),
        "actual_pair_cost": round6(pair_cost),
        "cash_plus_current_plus_rebate": round6(summary["cash_pnl"] + summary["current_value"] + no_condition_rebate),
        "resolved_roi": pct(summary["resolved_final_pnl"], summary["resolved_buy_actual"]),
        "resolved_plus_unresolved_current_plus_rebate": round6(
            summary["resolved_final_pnl"] + summary["unresolved_cash_pnl"] + summary["unresolved_current_value"] + no_condition_rebate
        ),
    }
    market_rows.sort(key=lambda r: float(r.get("buy_actual") or 0.0), reverse=True)
    (out_dir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(out_dir / "market_rows.csv", market_rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

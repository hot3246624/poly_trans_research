#!/usr/bin/env python3
"""Screen current Polymarket crypto leaderboard accounts for up/down strategy quality.

This is a fast first pass. It uses the public leaderboard page to get current
proxy wallets, then samples each account's recent public activity and computes
fee-inclusive pair-arb quality metrics on crypto up/down markets.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import http.client
import json
import math
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LEADERBOARD_URL = "https://polymarket.com/zh/leaderboard/crypto/monthly/profit"
LEADERBOARD_API_URL = "https://data-api.polymarket.com/v1/leaderboard"
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
PAGE_LIMIT = 500
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
EPS = 1e-9


def round6(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def pct(num: float, den: float) -> float | None:
    return round(num / den, 6) if abs(den) > EPS else None


def iso_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def bjt_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone(dt.timedelta(hours=8))).isoformat()


def parse_iso_to_s(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def fetch_text(url: str, *, retries: int, timeout: int, pause_s: float) -> str:
    req = urllib.request.Request(url, headers=HEADERS)
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8", "ignore")
        except (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            urllib.error.URLError,
            urllib.error.HTTPError,
        ) as exc:
            last_exc = exc
            if attempt + 1 < retries:
                time.sleep(pause_s * (attempt + 1))
    raise RuntimeError(f"fetch text failed url={url} exc={last_exc}")


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
    raise RuntimeError(f"fetch json failed url={url} params={params} exc={last_exc}")


def extract_leaderboard_from_page(top: int, *, retries: int, timeout: int, pause_s: float) -> list[dict[str, Any]]:
    html = fetch_text(LEADERBOARD_URL, retries=retries, timeout=timeout, pause_s=pause_s)
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', html)
    if not match:
        raise RuntimeError("cannot find __NEXT_DATA__ in leaderboard page")
    data = json.loads(match.group(1))
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    profit_rows: list[dict[str, Any]] | None = None
    dehydrated_at: int | None = None
    for query in queries:
        key = query.get("queryKey")
        if key == ["/leaderboard", "profit", "30d", 1, "crypto", None]:
            profit_rows = query.get("state", {}).get("data")
            dehydrated_at = query.get("dehydratedAt")
            break
    if not isinstance(profit_rows, list):
        raise RuntimeError("cannot find crypto monthly profit leaderboard rows")
    out: list[dict[str, Any]] = []
    for row in profit_rows[:top]:
        if not isinstance(row, dict):
            continue
        wallet = str(row.get("proxyWallet") or "").lower()
        if not re.fullmatch(r"0x[a-f0-9]{40}", wallet):
            continue
        out.append(
            {
                "rank": row.get("rank"),
                "name": row.get("name") or row.get("pseudonym") or wallet,
                "proxy_wallet": wallet,
                "leaderboard_pnl": round6(float(row.get("pnl") or 0.0)),
                "leaderboard_volume": round6(float(row.get("volume") or row.get("amount") or 0.0)),
                "leaderboard_dehydrated_at_ms": dehydrated_at,
            }
        )
    return out


def extract_leaderboard_from_api(
    top: int,
    *,
    category: str,
    period: str,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    offset = 0
    while len(out) < top:
        # The endpoint currently caps responses at 50 rows even when a larger
        # limit is requested.
        limit = min(50, top - len(out))
        page = fetch_json(
            LEADERBOARD_API_URL,
            {
                "timePeriod": period,
                "orderBy": "PNL",
                "limit": limit,
                "offset": offset,
                "category": category,
            },
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        for row in page:
            if not isinstance(row, dict):
                continue
            wallet = str(row.get("proxyWallet") or "").lower()
            if not re.fullmatch(r"0x[a-f0-9]{40}", wallet):
                continue
            out.append(
                {
                    "rank": int(row.get("rank") or len(out) + 1),
                    "name": row.get("userName") or row.get("name") or wallet,
                    "proxy_wallet": wallet,
                    "leaderboard_pnl": round6(float(row.get("pnl") or 0.0)),
                    "leaderboard_volume": round6(float(row.get("vol") or row.get("volume") or 0.0)),
                    "leaderboard_dehydrated_at_ms": None,
                }
            )
            if len(out) >= top:
                break
        if len(page) < limit:
            break
        offset += len(page)
        time.sleep(pause_s)
    return out


def extract_leaderboard(
    top: int,
    *,
    category: str,
    period: str,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    try:
        rows = extract_leaderboard_from_api(top, category=category, period=period, retries=retries, timeout=timeout, pause_s=pause_s)
        if rows:
            return rows
    except Exception as exc:  # noqa: BLE001 - fall back to SSR page if the API changes.
        print(f"[warn] leaderboard API failed, falling back to page: {exc}", file=sys.stderr, flush=True)
    if category != "crypto" or period != "month":
        raise RuntimeError("page fallback only supports crypto monthly profit leaderboard")
    return extract_leaderboard_from_page(top, retries=retries, timeout=timeout, pause_s=pause_s)


def activity_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("transactionHash") or ""),
        str(row.get("type") or ""),
        str(row.get("conditionId") or ""),
        str(row.get("asset") or ""),
        str(row.get("side") or ""),
        str(row.get("outcome") or ""),
        int(row.get("timestamp") or 0),
        round(float(row.get("size") or 0.0), 8),
        round(float(row.get("price") or 0.0), 10),
        round(float(row.get("usdcSize") or 0.0), 10),
    )


def fetch_latest_activity(
    user: str,
    *,
    max_offset: int,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, max_offset + PAGE_LIMIT, PAGE_LIMIT):
        page = fetch_json(
            ACTIVITY_URL,
            {"user": user, "limit": PAGE_LIMIT, "offset": offset},
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([row for row in page if isinstance(row, dict)])
        if len(page) < PAGE_LIMIT:
            break
        time.sleep(pause_s)
    return sorted({activity_key(row): row for row in rows}.values(), key=lambda r: int(r.get("timestamp") or 0))


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
        ("doge", "DOGE"),
        ("hype", "HYPE"),
        ("bnb", "BNB"),
    ):
        if re.search(rf"(^|[^a-z0-9]){re.escape(token)}([^a-z0-9]|$)", text):
            asset = label
            break
    if "updown-15m-" in slug or "15 minute" in text or "15-minute" in text:
        tf = "15m"
    elif "updown-4h-" in slug or "4 hour" in text or "4-hour" in text:
        tf = "4h"
    elif "updown-5m-" in slug or "5 minute" in text or "5-minute" in text:
        tf = "5m"
    elif "up-or-down" in slug:
        tf = "1h_or_named"
    else:
        tf = "other"
    return asset, tf


def is_updown(row: dict[str, Any]) -> bool:
    slug = slug_of(row).lower()
    if re.search(r"(^|-)updown-(5m|15m|4h)-\d{10}$", slug):
        return True
    if "up or down" in str(row.get("title") or "").lower():
        return True
    return False


def quality_bucket(actual_pair_cost: float | None, resid_rate: float | None, buy_actual: float) -> str:
    if buy_actual < 1_000:
        return "too_small"
    if actual_pair_cost is None:
        return "unpaired_or_directional"
    if resid_rate is not None and resid_rate > 0.25:
        return "high_residual"
    if actual_pair_cost < 0.95 and (resid_rate is None or resid_rate < 0.12):
        return "elite_pair_quality"
    if actual_pair_cost < 0.98 and (resid_rate is None or resid_rate < 0.18):
        return "good_pair_quality"
    if actual_pair_cost < 1.0:
        return "thin_edge"
    return "negative_pair_edge"


def summarize_account(account: dict[str, Any], rows: list[dict[str, Any]], start_s: int) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    sampled_count = len(rows)
    sample_min_s = min((int(r.get("timestamp") or 0) for r in rows), default=None)
    sample_max_s = max((int(r.get("timestamp") or 0) for r in rows), default=None)
    rows = [r for r in rows if int(r.get("timestamp") or 0) >= start_s]
    updown_rows = [r for r in rows if is_updown(r)]

    by_cond: dict[str, dict[str, Any]] = defaultdict(
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
            "sell": 0.0,
            "merge": 0.0,
            "redeem": 0.0,
            "rebate": 0.0,
            "split": 0.0,
            "yes_qty": 0.0,
            "no_qty": 0.0,
            "yes_actual": 0.0,
            "no_actual": 0.0,
            "yes_gross": 0.0,
            "no_gross": 0.0,
        }
    )
    type_counts: Counter[str] = Counter()
    slug_counts: Counter[str] = Counter()
    for row in updown_rows:
        typ = str(row.get("type") or "").upper()
        type_counts[typ] += 1
        slug_counts[slug_of(row)] += 1
        cid = str(row.get("conditionId") or "")
        if not cid:
            continue
        item = by_cond[cid]
        item["condition_id"] = cid
        if not item["slug"]:
            item["slug"] = slug_of(row)
            item["title"] = row.get("title") or ""
            item["asset"], item["tf"] = classify_asset_tf(item["slug"], item["title"])
        qty = float(row.get("size") or 0.0)
        price = float(row.get("price") or 0.0)
        gross = qty * price
        usdc = float(row.get("usdcSize") or gross)
        if typ == "TRADE":
            side = str(row.get("side") or "").upper()
            if side == "BUY":
                item["trade_count"] += 1
                item["buy_actual"] += usdc
                item["buy_gross"] += gross
                item["fee"] += usdc - gross
                outcome = normalize_outcome(row)
                if outcome == "YES":
                    item["yes_qty"] += qty
                    item["yes_actual"] += usdc
                    item["yes_gross"] += gross
                elif outcome == "NO":
                    item["no_qty"] += qty
                    item["no_actual"] += usdc
                    item["no_gross"] += gross
            elif side == "SELL":
                item["sell"] += usdc
        elif typ == "MERGE":
            item["merge"] += usdc
        elif typ == "REDEEM":
            item["redeem"] += usdc
        elif typ in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            item["rebate"] += usdc
        elif typ == "SPLIT":
            item["split"] += usdc

    market_rows: list[dict[str, Any]] = []
    for item in by_cond.values():
        paired_qty = min(item["yes_qty"], item["no_qty"])
        resid_qty = abs(item["yes_qty"] - item["no_qty"])
        buy_qty = item["yes_qty"] + item["no_qty"]
        yes_avg = item["yes_actual"] / item["yes_qty"] if item["yes_qty"] > EPS else None
        no_avg = item["no_actual"] / item["no_qty"] if item["no_qty"] > EPS else None
        yes_gross_avg = item["yes_gross"] / item["yes_qty"] if item["yes_qty"] > EPS else None
        no_gross_avg = item["no_gross"] / item["no_qty"] if item["no_qty"] > EPS else None
        actual_pair_cost = yes_avg + no_avg if paired_qty > EPS and yes_avg is not None and no_avg is not None else None
        gross_pair_cost = (
            yes_gross_avg + no_gross_avg
            if paired_qty > EPS and yes_gross_avg is not None and no_gross_avg is not None
            else None
        )
        cash_in = item["sell"] + item["merge"] + item["redeem"] + item["rebate"]
        cash_pnl = cash_in - item["buy_actual"] - item["split"]
        market_rows.append(
            {
                "rank": account["rank"],
                "name": account["name"],
                "proxy_wallet": account["proxy_wallet"],
                "condition_id": item["condition_id"],
                "slug": item["slug"],
                "asset": item["asset"],
                "tf": item["tf"],
                "trade_count": item["trade_count"],
                "buy_actual": round6(item["buy_actual"]),
                "fee": round6(item["fee"]),
                "cash_in": round6(cash_in),
                "cash_pnl": round6(cash_pnl),
                "paired_qty": round6(paired_qty),
                "resid_qty": round6(resid_qty),
                "resid_rate": pct(resid_qty, buy_qty),
                "actual_pair_cost": round6(actual_pair_cost),
                "gross_pair_cost": round6(gross_pair_cost),
                "pair_pnl": round6(paired_qty * (1.0 - actual_pair_cost)) if actual_pair_cost is not None else 0.0,
            }
        )

    paired_qty_total = sum(float(r["paired_qty"] or 0.0) for r in market_rows)
    buy_actual = sum(float(r["buy_actual"] or 0.0) for r in market_rows)
    fee = sum(float(r["fee"] or 0.0) for r in market_rows)
    buy_gross = buy_actual - fee
    buy_qty_total = sum(float(r["paired_qty"] or 0.0) * 2 + float(r["resid_qty"] or 0.0) for r in market_rows)
    resid_qty_total = sum(float(r["resid_qty"] or 0.0) for r in market_rows)
    pair_actual_notional = sum(
        float(r["paired_qty"] or 0.0) * float(r["actual_pair_cost"] or 0.0)
        for r in market_rows
        if r["actual_pair_cost"] is not None
    )
    pair_gross_notional = sum(
        float(r["paired_qty"] or 0.0) * float(r["gross_pair_cost"] or 0.0)
        for r in market_rows
        if r["gross_pair_cost"] is not None
    )
    pair_pnl = sum(float(r["pair_pnl"] or 0.0) for r in market_rows)
    cash_in = sum(float(r["cash_in"] or 0.0) for r in market_rows)
    cash_pnl = sum(float(r["cash_pnl"] or 0.0) for r in market_rows)
    actual_pair_cost = pair_actual_notional / paired_qty_total if paired_qty_total > EPS else None
    resid_rate = resid_qty_total / buy_qty_total if buy_qty_total > EPS else None

    group_rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"markets": 0, "buy_actual": 0.0, "paired_qty": 0.0, "resid_qty": 0.0, "pair_pnl": 0.0}
    )
    for row in market_rows:
        key = (str(row["asset"]), str(row["tf"]))
        groups[key]["markets"] += 1
        groups[key]["buy_actual"] += float(row["buy_actual"] or 0.0)
        groups[key]["paired_qty"] += float(row["paired_qty"] or 0.0)
        groups[key]["resid_qty"] += float(row["resid_qty"] or 0.0)
        groups[key]["pair_pnl"] += float(row["pair_pnl"] or 0.0)
    for (asset, tf), g in groups.items():
        group_rows.append(
            {
                "rank": account["rank"],
                "name": account["name"],
                "proxy_wallet": account["proxy_wallet"],
                "asset": asset,
                "tf": tf,
                "markets": g["markets"],
                "buy_actual": round6(g["buy_actual"]),
                "paired_qty": round6(g["paired_qty"]),
                "resid_qty": round6(g["resid_qty"]),
                "pair_pnl": round6(g["pair_pnl"]),
            }
        )

    summary = {
        **account,
        "sample_rows_total": sampled_count,
        "sample_start_bjt": bjt_s(sample_min_s),
        "sample_end_bjt": bjt_s(sample_max_s),
        "window_start_bjt": bjt_s(start_s),
        "rows_since": len(rows),
        "updown_rows_since": len(updown_rows),
        "activity_counts": dict(sorted(type_counts.items())),
        "top_updown_slugs": slug_counts.most_common(5),
        "updown_markets": len(market_rows),
        "buy_actual": round6(buy_actual),
        "cash_in": round6(cash_in),
        "cash_pnl_observed": round6(cash_pnl),
        "fee": round6(fee),
        "fee_rate_on_gross": pct(fee, buy_gross),
        "paired_qty": round6(paired_qty_total),
        "resid_qty": round6(resid_qty_total),
        "resid_rate_on_buy_qty": round6(resid_rate),
        "actual_pair_cost": round6(actual_pair_cost),
        "gross_pair_cost": round6(pair_gross_notional / paired_qty_total) if paired_qty_total > EPS else None,
        "paired_actual_profit": round6(pair_pnl),
        "quality_bucket": quality_bucket(actual_pair_cost, resid_rate, buy_actual),
    }
    return summary, market_rows, group_rows


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
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--category", default="crypto")
    parser.add_argument("--period", default="month", choices=["day", "week", "month", "all"])
    parser.add_argument("--since-iso", default=None)
    parser.add_argument("--days", type=float, default=3.0)
    parser.add_argument("--max-offset", type=int, default=3000)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=120)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    pause_s = args.pause_ms / 1000.0
    now_s = int(dt.datetime.now(dt.timezone.utc).timestamp())
    start_s = parse_iso_to_s(args.since_iso) if args.since_iso else now_s - int(args.days * 86400)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else ROOT / "data" / "exports" / f"leaderboard_crypto_updown_screen_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    leaderboard = extract_leaderboard(
        args.top,
        category=args.category,
        period=args.period,
        retries=args.retries,
        timeout=args.timeout,
        pause_s=pause_s,
    )
    summaries: list[dict[str, Any]] = []
    market_rows: list[dict[str, Any]] = []
    group_rows: list[dict[str, Any]] = []
    for account in leaderboard:
        print(
            f"[screen] rank={account['rank']} name={account['name']} wallet={account['proxy_wallet']}",
            file=sys.stderr,
            flush=True,
        )
        try:
            rows = fetch_latest_activity(
                account["proxy_wallet"],
                max_offset=args.max_offset,
                retries=args.retries,
                timeout=args.timeout,
                pause_s=pause_s,
            )
            summary, markets, groups = summarize_account(account, rows, start_s)
        except Exception as exc:  # noqa: BLE001 - screening should continue across flaky accounts.
            summary = {**account, "error": str(exc)}
            markets = []
            groups = []
        summaries.append(summary)
        market_rows.extend(markets)
        group_rows.extend(groups)

    summaries.sort(
        key=lambda row: (
            float(row.get("buy_actual") or 0.0),
            -(float(row.get("actual_pair_cost") or 9.0)),
        ),
        reverse=True,
    )
    (output_dir / "leaderboard.json").write_text(json.dumps(leaderboard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output_dir / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(output_dir / "summary.csv", summaries)
    write_csv(output_dir / "market_rows.csv", market_rows)
    write_csv(output_dir / "asset_tf_groups.csv", group_rows)
    print(json.dumps({"output_dir": str(output_dir), "accounts": len(summaries), "window_start_bjt": bjt_s(start_s), "now_bjt": bjt_s(now_s)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

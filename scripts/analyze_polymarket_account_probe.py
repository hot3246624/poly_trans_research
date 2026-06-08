#!/usr/bin/env python3
"""Quick public-account probes for Polymarket up/down market strategies.

The probe is intentionally bounded:
- seed with the latest public activity rows for each account;
- filter by a slug regex;
- re-fetch the exact observed time window by activity type;
- compute fee-inclusive cashflow and buy-side pair metrics;
- sample recent markets through /trades?takerOnly=true/false to classify maker/taker evidence.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
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


ACTIVITY_URL = "https://data-api.polymarket.com/activity"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
TRADES_URL = "https://data-api.polymarket.com/trades"
PAGE_LIMIT = 500
MAX_OFFSET = 3000
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
EPS = 1e-9


ACCOUNTS = {
    "b27bc": {
        "wallet": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
        "regex": r"^btc-updown-5m-",
    },
    "m4444": {
        "wallet": "0xc5d521074e88279556836998fb2a5d2e2c1c6caa",
        "regex": r"^btc-updown-5m-",
    },
    "yyyhaaa": {
        "wallet": "0x50255851148f5564aec93066edd52f7de518ce88",
        "regex": r"-updown-5m-",
    },
    "denise97234544": {
        "wallet": "0x1faa66202ed5b3da4e807be1956c7e46c9caad8a",
        "regex": r"-updown-5m-",
    },
    "realpowell": {
        "wallet": "0x6d87dbd0fd21c25c9f0252b38b027a7d6cdd8b48",
        "regex": r"-updown-5m-",
    },
    "rwo": {
        "wallet": "0xd189664c5308903476f9f079820431e4fd7d06f4",
        "regex": r"-updown-5m-",
    },
    "ascetic0x": {
        "wallet": "0xfcbecc7e5186e88e03445b81f593685d62828f44",
        "regex": r"^(bitcoin|ethereum|solana|xrp|doge|hype)-up-or-down-",
    },
    "wanglin9x9": {
        "wallet": "0xe0bebb65005e1f42ca5cc1a664b64a36e68617fa",
        "regex": r"-updown-5m-",
    },
    "f247_1hr": {
        "wallet": "0xf247584e41117bbbe4cc06e4d2c95741792a5216",
        "regex": r"^(bitcoin|ethereum|solana|xrp|doge|hype)-up-or-down-",
    },
}


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


def pct(num: float, den: float) -> float | None:
    return round(num / den, 6) if den else None


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


def trade_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row.get("transactionHash") or "",
        row.get("conditionId") or "",
        row.get("asset") or "",
        row.get("side") or "",
        int(row.get("timestamp") or 0),
        round(float(row.get("size") or 0.0), 8),
        round(float(row.get("price") or 0.0), 10),
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


def fetch_latest_activity(user: str, *, retries: int, timeout: int, pause_s: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, MAX_OFFSET + PAGE_LIMIT, PAGE_LIMIT):
        page = fetch_json(
            ACTIVITY_URL,
            {"user": user, "limit": PAGE_LIMIT, "offset": offset},
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([r for r in page if isinstance(r, dict)])
        if len(page) < PAGE_LIMIT:
            break
        time.sleep(pause_s)
    return list({activity_key(r): r for r in rows}.values())


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
    for offset in range(0, MAX_OFFSET + PAGE_LIMIT, PAGE_LIMIT):
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
            return fetch_activity_window(
                user, typ, start_s, mid, retries=retries, timeout=timeout, pause_s=pause_s
            ) + fetch_activity_window(user, typ, mid + 1, end_s, retries=retries, timeout=timeout, pause_s=pause_s)
    return rows


def fetch_activity_exact(
    user: str,
    start_s: int,
    end_s: int,
    *,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for typ in ["TRADE", "MERGE", "REDEEM", "MAKER_REBATE", "REWARD", "REFERRAL_REWARD"]:
        out.extend(fetch_activity_window(user, typ, start_s, end_s, retries=retries, timeout=timeout, pause_s=pause_s))
    return sorted({activity_key(r): r for r in out}.values(), key=lambda r: (int(r.get("timestamp") or 0), r.get("transactionHash") or ""))


def fetch_positions(user: str, *, retries: int, timeout: int, pause_s: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, 50_000, PAGE_LIMIT):
        page = fetch_json(
            POSITIONS_URL,
            {"user": user, "limit": PAGE_LIMIT, "offset": offset},
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([r for r in page if isinstance(r, dict)])
        if len(page) < PAGE_LIMIT:
            break
        time.sleep(pause_s)
    return rows


def build_pair_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("type") or "").upper() == "TRADE" and str(row.get("side") or "").upper() == "BUY":
            cid = str(row.get("conditionId") or "")
            if cid:
                grouped[cid].append(row)

    market_rows: list[dict[str, Any]] = []
    for cid, xs in grouped.items():
        qty = {"YES": 0.0, "NO": 0.0}
        gross = {"YES": 0.0, "NO": 0.0}
        actual = {"YES": 0.0, "NO": 0.0}
        for row in xs:
            side = normalize_outcome(row)
            if side not in qty:
                continue
            q = float(row.get("size") or 0.0)
            p = float(row.get("price") or 0.0)
            u = float(row.get("usdcSize") or q * p)
            qty[side] += q
            gross[side] += q * p
            actual[side] += u
        if qty["YES"] <= EPS and qty["NO"] <= EPS:
            continue
        paired = min(qty["YES"], qty["NO"])
        residual = abs(qty["YES"] - qty["NO"])
        yes_gross_avg = gross["YES"] / qty["YES"] if qty["YES"] > EPS else None
        no_gross_avg = gross["NO"] / qty["NO"] if qty["NO"] > EPS else None
        yes_actual_avg = actual["YES"] / qty["YES"] if qty["YES"] > EPS else None
        no_actual_avg = actual["NO"] / qty["NO"] if qty["NO"] > EPS else None
        gross_pair_cost = None
        actual_pair_cost = None
        if paired > EPS and yes_gross_avg is not None and no_gross_avg is not None:
            gross_pair_cost = yes_gross_avg + no_gross_avg
        if paired > EPS and yes_actual_avg is not None and no_actual_avg is not None:
            actual_pair_cost = yes_actual_avg + no_actual_avg
        market_rows.append(
            {
                "condition_id": cid,
                "slug": slug_of(xs[0]),
                "title": xs[0].get("title"),
                "first_trade_s": min(int(r.get("timestamp") or 0) for r in xs),
                "last_trade_s": max(int(r.get("timestamp") or 0) for r in xs),
                "trade_count": len(xs),
                "yes_qty": qty["YES"],
                "no_qty": qty["NO"],
                "paired_qty": paired,
                "residual_qty": residual,
                "gross_pair_cost": gross_pair_cost,
                "actual_pair_cost": actual_pair_cost,
                "paired_actual_profit": paired * (1.0 - actual_pair_cost) if actual_pair_cost is not None else None,
            }
        )
    total_paired = sum(r["paired_qty"] for r in market_rows)
    total_residual = sum(r["residual_qty"] for r in market_rows)
    paired_actual_notional = sum(r["paired_qty"] * r["actual_pair_cost"] for r in market_rows if r["actual_pair_cost"] is not None)
    paired_gross_notional = sum(r["paired_qty"] * r["gross_pair_cost"] for r in market_rows if r["gross_pair_cost"] is not None)
    paired_profit = sum(r["paired_actual_profit"] or 0.0 for r in market_rows)
    return {
        "market_rows": market_rows,
        "paired_market_count": sum(1 for r in market_rows if r["paired_qty"] > EPS),
        "market_count": len(market_rows),
        "residual_market_count": sum(1 for r in market_rows if r["residual_qty"] > EPS),
        "total_paired_qty": total_paired,
        "total_residual_qty": total_residual,
        "residual_rate_on_buy_qty": None,
        "gross_pair_cost": paired_gross_notional / total_paired if total_paired > EPS else None,
        "actual_pair_cost": paired_actual_notional / total_paired if total_paired > EPS else None,
        "paired_actual_profit": paired_profit,
    }


def summarize_activity(rows: list[dict[str, Any]], pos_rows: list[dict[str, Any]], regex: re.Pattern[str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    rows = [r for r in rows if regex.search(slug_of(r))]
    cids = {str(r.get("conditionId") or "") for r in rows if r.get("conditionId")}
    by_type: Counter[str] = Counter()
    trade_sides: Counter[str] = Counter()
    buy_qty = buy_gross = buy_actual = fee_like = split_cost = 0.0
    sell_proceeds = merge_proceeds = redeem_proceeds = rebate_proceeds = 0.0
    by_slug: Counter[str] = Counter()
    timestamps = [int(r.get("timestamp") or 0) for r in rows if r.get("timestamp")]
    for row in rows:
        typ = str(row.get("type") or "").upper()
        by_type[typ] += 1
        by_slug[slug_of(row)] += 1
        q = float(row.get("size") or 0.0)
        p = float(row.get("price") or 0.0)
        gross = q * p
        usdc = float(row.get("usdcSize") or gross)
        if typ == "TRADE":
            side = str(row.get("side") or "").upper()
            trade_sides[side] += 1
            if side == "BUY":
                buy_qty += q
                buy_gross += gross
                buy_actual += usdc
                fee_like += usdc - gross
            elif side == "SELL":
                sell_proceeds += usdc
        elif typ == "MERGE":
            merge_proceeds += usdc
        elif typ == "REDEEM":
            redeem_proceeds += usdc
        elif typ == "SPLIT":
            split_cost += usdc
        elif typ in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            rebate_proceeds += usdc

    pair = build_pair_metrics(rows)
    if buy_qty > EPS:
        pair["residual_rate_on_buy_qty"] = pair["total_residual_qty"] / buy_qty

    current_rows = []
    current_value = current_qty = current_pair_qty = current_residual_qty = 0.0
    grouped_pos: dict[str, dict[str, float]] = defaultdict(lambda: {"YES": 0.0, "NO": 0.0, "value": 0.0})
    for row in pos_rows:
        cid = str(row.get("conditionId") or "")
        if cid not in cids and not regex.search(slug_of(row)):
            continue
        outcome = normalize_outcome(row)
        if outcome not in {"YES", "NO"}:
            continue
        q = float(row.get("size") or 0.0)
        v = float(row.get("currentValue") or 0.0)
        grouped_pos[cid][outcome] += q
        grouped_pos[cid]["value"] += v
    for cid, item in grouped_pos.items():
        yes = item["YES"]
        no = item["NO"]
        value = item["value"]
        total = yes + no
        pair_qty = min(yes, no)
        residual = abs(yes - no)
        current_qty += total
        current_pair_qty += pair_qty
        current_residual_qty += residual
        current_value += value
        current_rows.append({"condition_id": cid, "yes_size": yes, "no_size": no, "current_value": value})

    cash_in = sell_proceeds + merge_proceeds + redeem_proceeds + rebate_proceeds
    cash_pnl = cash_in - buy_actual - split_cost
    summary = {
        "row_count": len(rows),
        "market_count": len(cids),
        "start_s": min(timestamps) if timestamps else None,
        "end_s": max(timestamps) if timestamps else None,
        "start_bjt": bjt_s(min(timestamps)) if timestamps else None,
        "end_bjt": bjt_s(max(timestamps)) if timestamps else None,
        "duration_hours": round((max(timestamps) - min(timestamps)) / 3600.0, 4) if len(timestamps) >= 2 else None,
        "activity_counts": dict(sorted(by_type.items())),
        "trade_side_counts": dict(sorted(trade_sides.items())),
        "top_slugs": by_slug.most_common(10),
        "buy_qty": round(buy_qty, 6),
        "buy_gross": round(buy_gross, 6),
        "buy_actual": round(buy_actual, 6),
        "fee_like": round(fee_like, 6),
        "fee_like_rate_on_gross": pct(fee_like, buy_gross),
        "split_cost": round(split_cost, 6),
        "sell_proceeds": round(sell_proceeds, 6),
        "merge_proceeds": round(merge_proceeds, 6),
        "redeem_proceeds": round(redeem_proceeds, 6),
        "rebate_proceeds": round(rebate_proceeds, 6),
        "cash_pnl": round(cash_pnl, 6),
        "gross_before_fee_pnl": round(cash_in - buy_gross - split_cost, 6),
        "current_value": round(current_value, 6),
        "mtm_pnl": round(cash_pnl + current_value, 6),
        "current_qty": round(current_qty, 6),
        "current_residual_qty": round(current_residual_qty, 6),
        "current_residual_rate": pct(current_residual_qty, current_qty),
        "paired_market_count": pair["paired_market_count"],
        "buy_side_market_count": pair["market_count"],
        "pair_total_paired_qty": round(pair["total_paired_qty"], 6),
        "pair_total_residual_qty": round(pair["total_residual_qty"], 6),
        "pair_residual_rate_on_buy_qty": round(pair["residual_rate_on_buy_qty"], 6)
        if pair["residual_rate_on_buy_qty"] is not None
        else None,
        "gross_pair_cost": round(pair["gross_pair_cost"], 6) if pair["gross_pair_cost"] is not None else None,
        "actual_pair_cost": round(pair["actual_pair_cost"], 6) if pair["actual_pair_cost"] is not None else None,
        "paired_actual_profit": round(pair["paired_actual_profit"], 6),
    }
    return summary, pair["market_rows"]


def fetch_trades(
    user: str,
    markets: list[str],
    taker_only: bool,
    *,
    limit: int,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    if not markets:
        return []
    out: list[dict[str, Any]] = []
    for i in range(0, len(markets), 10):
        chunk = markets[i : i + 10]
        page = fetch_json(
            TRADES_URL,
            {
                "user": user,
                "market": ",".join(chunk),
                "limit": limit,
                "offset": 0,
                "takerOnly": str(taker_only).lower(),
            },
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if isinstance(page, list):
            out.extend([r for r in page if isinstance(r, dict)])
        time.sleep(pause_s)
    return out


def maker_taker_probe(
    user: str,
    rows: list[dict[str, Any]],
    *,
    probe_markets: int,
    trade_probe_limit: int,
    retries: int,
    timeout: int,
    pause_s: float,
) -> dict[str, Any]:
    trades = [r for r in rows if str(r.get("type") or "").upper() == "TRADE"]
    latest_markets: list[str] = []
    for row in sorted(trades, key=lambda r: int(r.get("timestamp") or 0), reverse=True):
        cid = str(row.get("conditionId") or "")
        if cid and cid not in latest_markets:
            latest_markets.append(cid)
        if len(latest_markets) >= probe_markets:
            break
    sample = [r for r in trades if str(r.get("conditionId") or "") in set(latest_markets)]
    all_rows = fetch_trades(user, latest_markets, False, limit=trade_probe_limit, retries=retries, timeout=timeout, pause_s=pause_s)
    taker_rows = fetch_trades(user, latest_markets, True, limit=trade_probe_limit, retries=retries, timeout=timeout, pause_s=pause_s)
    sample_keys = {trade_key(r) for r in sample}
    all_keys = {trade_key(r) for r in all_rows}
    taker_keys = {trade_key(r) for r in taker_rows}
    return {
        "probe_market_count": len(latest_markets),
        "sample_trade_rows": len(sample_keys),
        "all_trades_rows": len(all_rows),
        "taker_only_rows": len(taker_rows),
        "sample_in_all": len(sample_keys & all_keys),
        "sample_in_taker_only": len(sample_keys & taker_keys),
        "sample_taker_only_rate": pct(len(sample_keys & taker_keys), len(sample_keys)),
        "market_ids": latest_markets,
    }


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
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account", action="append", choices=sorted(ACCOUNTS), default=[])
    parser.add_argument("--since-iso", default="2026-05-06T00:00:00+08:00")
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=120)
    parser.add_argument("--probe-markets", type=int, default=6)
    parser.add_argument("--trade-probe-limit", type=int, default=1000)
    parser.add_argument("--skip-taker-probe", action="store_true")
    parser.add_argument("--skip-positions", action="store_true")
    parser.add_argument(
        "--seed-only",
        action="store_true",
        help="Use the latest activity pages directly instead of re-fetching the exact observed time window.",
    )
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    accounts = args.account or list(ACCOUNTS)
    since_s = parse_iso_to_s(args.since_iso)
    pause_s = args.pause_ms / 1000.0
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("data/exports") / f"account_probe_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries = []
    all_market_rows: list[dict[str, Any]] = []
    for label in accounts:
        cfg = ACCOUNTS[label]
        user = cfg["wallet"]
        regex = re.compile(cfg["regex"])
        print(f"[probe] {label} {user} regex={cfg['regex']}", file=sys.stderr, flush=True)
        seed = fetch_latest_activity(user, retries=args.retries, timeout=args.timeout, pause_s=pause_s)
        seed_filtered = [r for r in seed if regex.search(slug_of(r)) and int(r.get("timestamp") or 0) >= since_s]
        if not seed_filtered:
            summary = {"label": label, "user": user, "regex": cfg["regex"], "row_count": 0, "error": "no recent matching activity"}
            summaries.append(summary)
            continue
        start_s = min(int(r.get("timestamp") or 0) for r in seed_filtered)
        end_s = max(int(r.get("timestamp") or 0) for r in seed_filtered)
        if args.seed_only:
            exact = seed_filtered
        else:
            exact = fetch_activity_exact(user, start_s, end_s, retries=args.retries, timeout=args.timeout, pause_s=pause_s)
        positions = [] if args.skip_positions else fetch_positions(user, retries=args.retries, timeout=args.timeout, pause_s=pause_s)
        summary, market_rows = summarize_activity(exact, positions, regex)
        probe = (
            {"skipped": True}
            if args.skip_taker_probe
            else maker_taker_probe(
                user,
                [r for r in exact if regex.search(slug_of(r))],
                probe_markets=args.probe_markets,
                trade_probe_limit=args.trade_probe_limit,
                retries=args.retries,
                timeout=args.timeout,
                pause_s=pause_s,
            )
        )
        summary = {
            "label": label,
            "user": user,
            "regex": cfg["regex"],
            "seed_rows_total": len(seed),
            "seed_rows_matching_since": len(seed_filtered),
            **summary,
            "maker_taker_probe": probe,
        }
        summaries.append(summary)
        for row in market_rows:
            all_market_rows.append({"label": label, **row})

    (output_dir / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_csv(output_dir / "summary.csv", summaries)
    write_csv(output_dir / "market_rows.csv", all_market_rows)
    print(json.dumps({"output_dir": str(output_dir), "accounts": len(summaries)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

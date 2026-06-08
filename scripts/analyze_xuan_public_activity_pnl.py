#!/usr/bin/env python3
"""Strict fee-inclusive PnL truth for xuanxuan008 from public Polymarket APIs.

Data model:
- cash outflows for buys come from activity.usdcSize
- fee is activity.usdcSize - size * price on TRADE BUY rows
- cash inflows come from SELL / MERGE / REDEEM / rebate-like activity
- mark-to-market add-on comes from current positions on markets touched in-window

This script intentionally separates:
- lifetime buy-side pairing metrics inferred from TRADE rows
- current residual inventory metrics inferred from positions on touched markets
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import http.client
import json
import math
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any


XUAN = "0xcfb103c37c0234f524c632d964ed31f117b5f694"
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
POSITIONS_URL = "https://data-api.polymarket.com/positions"
PAGE_LIMIT = 500
MAX_ACTIVITY_OFFSET = 3000
HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
EPS = 1e-9


def iso_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso_to_s(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def percentile(values: list[float], q: float) -> float | None:
    xs = sorted(v for v in values if math.isfinite(v))
    if not xs:
        return None
    if len(xs) == 1:
        return round(xs[0], 6)
    pos = (len(xs) - 1) * q / 100.0
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return round(xs[lo], 6)
    weight = pos - lo
    return round(xs[lo] * (1.0 - weight) + xs[hi] * weight, 6)


def summarize(values: list[float | int | None]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "count": len(xs),
        "avg": round(sum(xs) / len(xs), 6) if xs else None,
        "p10": percentile(xs, 10),
        "p25": percentile(xs, 25),
        "p50": percentile(xs, 50),
        "p75": percentile(xs, 75),
        "p90": percentile(xs, 90),
        "min": round(min(xs), 6) if xs else None,
        "max": round(max(xs), 6) if xs else None,
    }


def rate(num: float, den: float) -> float | None:
    return round(num / den, 6) if den else None


def fetch_json(url: str, params: dict[str, Any], retries: int, timeout: int, pause_s: float) -> Any:
    last_exc: Exception | None = None
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(f"{url}?{qs}", headers=HEADERS)
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
                continue
            break
    raise RuntimeError(f"failed fetch url={url} params={params} exc={last_exc}")


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


def position_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        str(row.get("conditionId") or ""),
        str(row.get("asset") or ""),
        str(row.get("outcome") or ""),
        round(float(row.get("size") or 0.0), 8),
        round(float(row.get("avgPrice") or 0.0), 10),
    )


def normalize_outcome(row: dict[str, Any]) -> str | None:
    direct = str(row.get("outcome") or "").strip().lower()
    if direct in {"up", "yes"}:
        return "YES"
    if direct in {"down", "no"}:
        return "NO"
    idx = row.get("outcomeIndex")
    if idx is None:
        return None
    try:
        return "YES" if int(idx) == 0 else "NO"
    except (TypeError, ValueError):
        return None


def bjt_day(ts_s: int) -> str:
    return dt.datetime.fromtimestamp(ts_s, tz=dt.timezone(dt.timedelta(hours=8))).date().isoformat()


def fetch_activity_window(
    user: str,
    activity_type: str,
    start_s: int,
    end_s: int,
    *,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset in range(0, MAX_ACTIVITY_OFFSET + PAGE_LIMIT, PAGE_LIMIT):
        page = fetch_json(
            ACTIVITY_URL,
            {
                "user": user,
                "type": activity_type,
                "start": start_s,
                "end": end_s,
                "limit": PAGE_LIMIT,
                "offset": offset,
            },
            retries=retries,
            timeout=timeout,
            pause_s=pause_s,
        )
        if not isinstance(page, list) or not page:
            break
        rows.extend([row for row in page if isinstance(row, dict)])
        if len(page) < PAGE_LIMIT:
            break
        if offset >= MAX_ACTIVITY_OFFSET:
            span = end_s - start_s
            if span <= 1:
                raise RuntimeError(
                    f"activity window overflow at smallest split type={activity_type} start={start_s} end={end_s}"
                )
            mid = start_s + span // 2
            left = fetch_activity_window(
                user,
                activity_type,
                start_s,
                mid,
                retries=retries,
                timeout=timeout,
                pause_s=pause_s,
            )
            right = fetch_activity_window(
                user,
                activity_type,
                mid + 1,
                end_s,
                retries=retries,
                timeout=timeout,
                pause_s=pause_s,
            )
            return left + right
    return rows


def fetch_activity_rows(
    user: str,
    start_s: int,
    end_s: int,
    *,
    activity_types: list[str],
    window_hours: int,
    retries: int,
    timeout: int,
    pause_s: float,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    window_s = max(1, int(window_hours * 3600))
    for activity_type in activity_types:
        type_rows_before = len(out)
        cursor = start_s
        while cursor <= end_s:
            win_end = min(end_s, cursor + window_s - 1)
            print(
                f"[activity] type={activity_type} start={iso_s(cursor)} end={iso_s(win_end)}",
                file=sys.stderr,
                flush=True,
            )
            out.extend(
                fetch_activity_window(
                    user,
                    activity_type,
                    cursor,
                    win_end,
                    retries=retries,
                    timeout=timeout,
                    pause_s=pause_s,
                )
            )
            cursor = win_end + 1
            time.sleep(pause_s)
        print(
            f"[activity] type={activity_type} fetched_rows={len(out) - type_rows_before}",
            file=sys.stderr,
            flush=True,
        )
    deduped = list({activity_key(row): row for row in out}.values())
    return sorted(deduped, key=lambda row: (int(row.get("timestamp") or 0), str(row.get("transactionHash") or "")))


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
        rows.extend([row for row in page if isinstance(row, dict)])
        if len(page) < PAGE_LIMIT:
            break
        time.sleep(pause_s)
    return list({position_key(row): row for row in rows}.values())


def build_market_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row.get("type") or "").upper() != "TRADE":
            continue
        if str(row.get("side") or "").upper() != "BUY":
            continue
        condition_id = str(row.get("conditionId") or "")
        if not condition_id:
            continue
        grouped[condition_id].append(row)

    out: list[dict[str, Any]] = []
    for condition_id, xs in grouped.items():
        yes_qty = no_qty = 0.0
        yes_gross = no_gross = 0.0
        yes_actual = no_actual = 0.0
        for row in xs:
            side = normalize_outcome(row)
            if side not in {"YES", "NO"}:
                continue
            qty = float(row.get("size") or 0.0)
            price = float(row.get("price") or 0.0)
            gross = qty * price
            actual = float(row.get("usdcSize") or gross)
            if side == "YES":
                yes_qty += qty
                yes_gross += gross
                yes_actual += actual
            else:
                no_qty += qty
                no_gross += gross
                no_actual += actual

        if yes_qty <= EPS and no_qty <= EPS:
            continue

        yes_gross_avg = yes_gross / yes_qty if yes_qty > EPS else None
        no_gross_avg = no_gross / no_qty if no_qty > EPS else None
        yes_actual_avg = yes_actual / yes_qty if yes_qty > EPS else None
        no_actual_avg = no_actual / no_qty if no_qty > EPS else None
        paired_qty = min(yes_qty, no_qty)
        gross_pair_cost = (
            float(yes_gross_avg) + float(no_gross_avg)
            if yes_gross_avg is not None and no_gross_avg is not None and paired_qty > EPS
            else None
        )
        actual_pair_cost = (
            float(yes_actual_avg) + float(no_actual_avg)
            if yes_actual_avg is not None and no_actual_avg is not None and paired_qty > EPS
            else None
        )
        out.append(
            {
                "condition_id": condition_id,
                "slug": xs[0].get("slug"),
                "title": xs[0].get("title"),
                "first_trade_s": min(int(row.get("timestamp") or 0) for row in xs),
                "last_trade_s": max(int(row.get("timestamp") or 0) for row in xs),
                "trade_count": len(xs),
                "yes_qty": round(yes_qty, 6),
                "no_qty": round(no_qty, 6),
                "paired_qty": round(paired_qty, 6),
                "lifetime_residual_qty": round(abs(yes_qty - no_qty), 6),
                "yes_gross_avg": round(yes_gross_avg, 6) if yes_gross_avg is not None else None,
                "no_gross_avg": round(no_gross_avg, 6) if no_gross_avg is not None else None,
                "yes_actual_avg": round(yes_actual_avg, 6) if yes_actual_avg is not None else None,
                "no_actual_avg": round(no_actual_avg, 6) if no_actual_avg is not None else None,
                "gross_pair_cost": round(gross_pair_cost, 6) if gross_pair_cost is not None else None,
                "actual_pair_cost": round(actual_pair_cost, 6) if actual_pair_cost is not None else None,
                "pair_fee_cost": round(actual_pair_cost - gross_pair_cost, 6)
                if actual_pair_cost is not None and gross_pair_cost is not None
                else None,
                "paired_gross_profit": round(paired_qty * (1.0 - gross_pair_cost), 6)
                if gross_pair_cost is not None
                else None,
                "paired_actual_profit": round(paired_qty * (1.0 - actual_pair_cost), 6)
                if actual_pair_cost is not None
                else None,
            }
        )
    return sorted(out, key=lambda row: (int(row["first_trade_s"]), str(row["condition_id"])))


def filter_by_slug_prefix(rows: list[dict[str, Any]], prefixes: list[str]) -> list[dict[str, Any]]:
    if not prefixes:
        return rows
    out = []
    for row in rows:
        slug = str(row.get("slug") or row.get("eventSlug") or "")
        if any(slug.startswith(prefix) for prefix in prefixes):
            out.append(row)
    return out


def build_position_rows(position_rows: list[dict[str, Any]], tracked_conditions: set[str]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in position_rows:
        condition_id = str(row.get("conditionId") or "")
        if not condition_id or condition_id not in tracked_conditions:
            continue
        outcome = normalize_outcome(row)
        if outcome not in {"YES", "NO"}:
            continue
        item = grouped.setdefault(
            condition_id,
            {
                "condition_id": condition_id,
                "slug": row.get("slug"),
                "title": row.get("title"),
                "yes_size": 0.0,
                "no_size": 0.0,
                "yes_value": 0.0,
                "no_value": 0.0,
                "yes_redeemable": 0,
                "no_redeemable": 0,
                "yes_mergeable": 0,
                "no_mergeable": 0,
            },
        )
        qty = float(row.get("size") or 0.0)
        value = float(row.get("currentValue") or 0.0)
        redeemable = 1 if bool(row.get("redeemable")) else 0
        mergeable = 1 if bool(row.get("mergeable")) else 0
        if outcome == "YES":
            item["yes_size"] += qty
            item["yes_value"] += value
            item["yes_redeemable"] = max(item["yes_redeemable"], redeemable)
            item["yes_mergeable"] = max(item["yes_mergeable"], mergeable)
        else:
            item["no_size"] += qty
            item["no_value"] += value
            item["no_redeemable"] = max(item["no_redeemable"], redeemable)
            item["no_mergeable"] = max(item["no_mergeable"], mergeable)

    out: list[dict[str, Any]] = []
    for item in grouped.values():
        yes_size = float(item["yes_size"])
        no_size = float(item["no_size"])
        total_qty = yes_size + no_size
        residual_qty = abs(yes_size - no_size)
        pair_qty = min(yes_size, no_size)
        current_value = float(item["yes_value"]) + float(item["no_value"])
        out.append(
            {
                **item,
                "yes_size": round(yes_size, 6),
                "no_size": round(no_size, 6),
                "current_total_qty": round(total_qty, 6),
                "current_pair_qty": round(pair_qty, 6),
                "current_residual_qty": round(residual_qty, 6),
                "current_value": round(current_value, 6),
            }
        )
    return sorted(out, key=lambda row: (str(row.get("slug") or ""), str(row["condition_id"])))


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=XUAN)
    parser.add_argument("--start-iso", default="2026-05-01T00:00:00+08:00")
    parser.add_argument("--end-iso", default=None)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--retries", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=120)
    parser.add_argument(
        "--slug-prefix",
        action="append",
        default=[],
        help="Only include rows whose slug/eventSlug starts with this prefix. Repeatable.",
    )
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    start_s = parse_iso_to_s(args.start_iso)
    end_s = parse_iso_to_s(args.end_iso) if args.end_iso else int(time.time())
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else Path("data/exports")
        / f"xuan_public_activity_pnl_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )

    activity_types = ["TRADE", "MERGE", "REDEEM", "MAKER_REBATE", "REWARD", "REFERRAL_REWARD"]
    activity_rows = fetch_activity_rows(
        args.user,
        start_s,
        end_s,
        activity_types=activity_types,
        window_hours=args.window_hours,
        retries=args.retries,
        timeout=args.timeout,
        pause_s=args.pause_ms / 1000.0,
    )
    position_rows = fetch_positions(
        args.user,
        retries=args.retries,
        timeout=args.timeout,
        pause_s=args.pause_ms / 1000.0,
    )
    activity_rows = filter_by_slug_prefix(activity_rows, args.slug_prefix)
    position_rows = filter_by_slug_prefix(position_rows, args.slug_prefix)

    by_type: dict[str, int] = defaultdict(int)
    by_type_usdc: dict[str, float] = defaultdict(float)
    by_day: dict[str, dict[str, float]] = {}
    tracked_conditions: set[str] = set()
    buy_qty_total = 0.0
    buy_gross_cost = 0.0
    buy_actual_cost = 0.0
    fee_total = 0.0
    cash_in = 0.0
    sell_proceeds = 0.0
    merge_proceeds = 0.0
    redeem_proceeds = 0.0
    rebate_proceeds = 0.0

    for row in activity_rows:
        activity_type = str(row.get("type") or "").upper()
        ts_s = int(row.get("timestamp") or 0)
        day = bjt_day(ts_s)
        qty = float(row.get("size") or 0.0)
        price = float(row.get("price") or 0.0)
        usdc = float(row.get("usdcSize") or qty * price)
        condition_id = str(row.get("conditionId") or "")
        if condition_id:
            tracked_conditions.add(condition_id)
        by_type[activity_type] += 1
        by_type_usdc[activity_type] += usdc
        bucket = by_day.setdefault(
            day,
            {
                "row_count": 0.0,
                "buy_qty": 0.0,
                "gross_buy_cost": 0.0,
                "actual_buy_cost": 0.0,
                "fee": 0.0,
                "sell_proceeds": 0.0,
                "merge_proceeds": 0.0,
                "redeem_proceeds": 0.0,
                "rebate_proceeds": 0.0,
                "cash_pnl": 0.0,
            },
        )
        bucket["row_count"] += 1

        if activity_type == "TRADE":
            side = str(row.get("side") or "").upper()
            if side == "BUY":
                gross = qty * price
                fee = usdc - gross
                buy_qty_total += qty
                buy_gross_cost += gross
                buy_actual_cost += usdc
                fee_total += fee
                bucket["buy_qty"] += qty
                bucket["gross_buy_cost"] += gross
                bucket["actual_buy_cost"] += usdc
                bucket["fee"] += fee
                bucket["cash_pnl"] -= usdc
            elif side == "SELL":
                sell_proceeds += usdc
                cash_in += usdc
                bucket["sell_proceeds"] += usdc
                bucket["cash_pnl"] += usdc
        elif activity_type == "MERGE":
            merge_proceeds += usdc
            cash_in += usdc
            bucket["merge_proceeds"] += usdc
            bucket["cash_pnl"] += usdc
        elif activity_type == "REDEEM":
            redeem_proceeds += usdc
            cash_in += usdc
            bucket["redeem_proceeds"] += usdc
            bucket["cash_pnl"] += usdc
        elif activity_type in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            rebate_proceeds += usdc
            cash_in += usdc
            bucket["rebate_proceeds"] += usdc
            bucket["cash_pnl"] += usdc

    market_trade_rows = build_market_trade_rows(activity_rows)
    current_position_rows = build_position_rows(position_rows, tracked_conditions)

    total_paired_qty = sum(float(row["paired_qty"]) for row in market_trade_rows)
    total_lifetime_residual_qty = sum(float(row["lifetime_residual_qty"]) for row in market_trade_rows)
    paired_cost_gross_notional = sum(
        float(row["paired_qty"]) * float(row["gross_pair_cost"] or 0.0) for row in market_trade_rows if row["gross_pair_cost"] is not None
    )
    paired_cost_actual_notional = sum(
        float(row["paired_qty"]) * float(row["actual_pair_cost"] or 0.0) for row in market_trade_rows if row["actual_pair_cost"] is not None
    )
    gross_pair_cost = paired_cost_gross_notional / total_paired_qty if total_paired_qty > EPS else None
    actual_pair_cost = paired_cost_actual_notional / total_paired_qty if total_paired_qty > EPS else None
    paired_gross_profit = sum(float(row["paired_gross_profit"] or 0.0) for row in market_trade_rows)
    paired_actual_profit = sum(float(row["paired_actual_profit"] or 0.0) for row in market_trade_rows)
    residual_markets_lifetime = [row for row in market_trade_rows if float(row["lifetime_residual_qty"]) > EPS]

    current_total_qty = sum(float(row["current_total_qty"]) for row in current_position_rows)
    current_pair_qty = sum(float(row["current_pair_qty"]) for row in current_position_rows)
    current_residual_qty = sum(float(row["current_residual_qty"]) for row in current_position_rows)
    current_value = sum(float(row["current_value"]) for row in current_position_rows)
    current_residual_markets = [row for row in current_position_rows if float(row["current_residual_qty"]) > EPS]
    current_nonzero_value_markets = [row for row in current_position_rows if float(row["current_value"]) > EPS]

    daily_rows = []
    for day, item in sorted(by_day.items()):
        daily_rows.append(
            {
                "day_bjt": day,
                "row_count": int(item["row_count"]),
                "buy_qty": round(item["buy_qty"], 6),
                "gross_buy_cost": round(item["gross_buy_cost"], 6),
                "actual_buy_cost": round(item["actual_buy_cost"], 6),
                "fee": round(item["fee"], 6),
                "sell_proceeds": round(item["sell_proceeds"], 6),
                "merge_proceeds": round(item["merge_proceeds"], 6),
                "redeem_proceeds": round(item["redeem_proceeds"], 6),
                "rebate_proceeds": round(item["rebate_proceeds"], 6),
                "cash_pnl": round(item["cash_pnl"], 6),
            }
        )

    summary = {
        "generated_at_utc": iso_s(int(time.time())),
        "user": args.user,
        "window": {
            "start_s": start_s,
            "start_iso": iso_s(start_s),
            "end_s": end_s,
            "end_iso": iso_s(end_s),
        },
        "row_counts": {
            "activity_unique_rows": len(activity_rows),
            "tracked_markets": len(tracked_conditions),
            "market_trade_rows": len(market_trade_rows),
            "current_position_rows": len(current_position_rows),
        },
        "filters": {
            "slug_prefixes": args.slug_prefix,
        },
        "activity_types": {
            "counts": dict(sorted(by_type.items())),
            "usdc": {k: round(v, 6) for k, v in sorted(by_type_usdc.items())},
        },
        "cashflow": {
            "buy_qty_total": round(buy_qty_total, 6),
            "buy_gross_cost": round(buy_gross_cost, 6),
            "buy_actual_cost": round(buy_actual_cost, 6),
            "fee_total": round(fee_total, 6),
            "fee_rate_on_gross": rate(fee_total, buy_gross_cost),
            "fee_per_share": round(fee_total / buy_qty_total, 6) if buy_qty_total > EPS else None,
            "sell_proceeds": round(sell_proceeds, 6),
            "merge_proceeds": round(merge_proceeds, 6),
            "redeem_proceeds": round(redeem_proceeds, 6),
            "rebate_proceeds": round(rebate_proceeds, 6),
            "cash_in": round(cash_in, 6),
            "cash_pnl": round(cash_in - buy_actual_cost, 6),
            "gross_pnl_before_fee": round(cash_in - buy_gross_cost, 6),
        },
        "pair_metrics_lifetime": {
            "paired_market_count": sum(1 for row in market_trade_rows if float(row["paired_qty"]) > EPS),
            "lifetime_residual_market_count": len(residual_markets_lifetime),
            "lifetime_residual_market_rate": rate(len(residual_markets_lifetime), len(market_trade_rows)),
            "total_paired_qty": round(total_paired_qty, 6),
            "total_lifetime_residual_qty": round(total_lifetime_residual_qty, 6),
            "lifetime_residual_rate_on_bought_qty": rate(total_lifetime_residual_qty, buy_qty_total),
            "gross_pair_cost": round(gross_pair_cost, 6) if gross_pair_cost is not None else None,
            "actual_pair_cost": round(actual_pair_cost, 6) if actual_pair_cost is not None else None,
            "pair_fee_cost": round(actual_pair_cost - gross_pair_cost, 6)
            if actual_pair_cost is not None and gross_pair_cost is not None
            else None,
            "paired_gross_profit": round(paired_gross_profit, 6),
            "paired_actual_profit": round(paired_actual_profit, 6),
            "per_market_actual_pair_cost": summarize([row["actual_pair_cost"] for row in market_trade_rows]),
            "per_market_lifetime_residual_qty": summarize([row["lifetime_residual_qty"] for row in market_trade_rows]),
        },
        "current_positions": {
            "tracked_position_market_count": len(current_position_rows),
            "current_nonzero_value_market_count": len(current_nonzero_value_markets),
            "current_residual_market_count": len(current_residual_markets),
            "current_residual_market_rate": rate(len(current_residual_markets), len(current_position_rows)),
            "current_total_qty": round(current_total_qty, 6),
            "current_pair_qty": round(current_pair_qty, 6),
            "current_residual_qty": round(current_residual_qty, 6),
            "current_residual_rate_on_position_qty": rate(current_residual_qty, current_total_qty),
            "current_value": round(current_value, 6),
        },
        "mark_to_market": {
            "cash_pnl": round(cash_in - buy_actual_cost, 6),
            "current_value": round(current_value, 6),
            "pnl_with_current_value": round(cash_in - buy_actual_cost + current_value, 6),
        },
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    write_csv(output_dir / "daily_cashflow.csv", daily_rows)
    write_csv(output_dir / "market_trade_metrics.csv", market_trade_rows)
    write_csv(output_dir / "current_positions.csv", current_position_rows)

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

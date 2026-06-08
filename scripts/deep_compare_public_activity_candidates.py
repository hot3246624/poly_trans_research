#!/usr/bin/env python3
"""Deep public-activity comparison for Polymarket up/down strategy candidates.

This script is intentionally strict about window-boundary effects:
- total cash PnL is reported;
- redeems/merges in markets with no in-window BUY are separated as old-position inflows;
- in-window BUY markets are evaluated separately;
- maker rebates and SPLIT rows are counted separately;
- pair metrics use actual BUY cost, i.e. `usdcSize`, not `size * price`.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BASE_SCRIPT = ROOT / "scripts" / "analyze_xuan_public_activity_pnl.py"
EPS = 1e-9


ACCOUNTS = {
    "b55": "0xb55fa1296e6ec55d0ce53d93b9237389f11764d4",
    "ce25": "0xce25e214d5cfe4f459cf67f08df581885aae7fdc",
    "ohanism": "0x89b5cdaaa4866c1e738406712012a630b4078beb",
    "xuan": "0xcfb103c37c0234f524c632d964ed31f117b5f694",
    "04b6": "0x04b6d7e930cf9e493c5e6ef24b496294f95594c8",
    "b27bc": "0xb27bc932bf8110d8f78e55da7d5f0497a18b5b82",
}


def load_base_module() -> Any:
    spec = importlib.util.spec_from_file_location("activity_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base_module()


def fnum(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


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


def slug_of(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("eventSlug") or "")


def normalize_outcome(row: dict[str, Any]) -> str | None:
    return base.normalize_outcome(row)


def pct(num: float, den: float) -> float | None:
    return round(num / den, 6) if abs(den) > EPS else None


def round6(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


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
    w = pos - lo
    return round(xs[lo] * (1.0 - w) + xs[hi] * w, 6)


def summarize(values: list[float]) -> dict[str, Any]:
    xs = [v for v in values if math.isfinite(v)]
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
    if "updown-15m-" in slug or re.search(r"(^|[^0-9a-z])15m([^0-9a-z]|$)", text) or "15 minute" in text or "15-minute" in text:
        tf = "15m"
    elif "updown-4h-" in slug or "4h" in text or "4 hour" in text or "4-hour" in text:
        tf = "4h"
    elif "updown-5m-" in slug or re.search(r"(^|[^0-9a-z])5m([^0-9a-z]|$)", text) or "5 minute" in text or "5-minute" in text:
        tf = "5m"
    elif "up-or-down" in slug:
        tf = "1h_or_named"
    else:
        tf = "other"
    return asset, tf


def infer_market_end_s(slug: str) -> int | None:
    match = re.search(r"updown-(5m|15m|4h)-(\d{10})$", slug)
    if match:
        duration_s = {"5m": 5 * 60, "15m": 15 * 60, "4h": 4 * 60 * 60}[match.group(1)]
        return int(match.group(2)) + duration_s
    return None


def price_bucket(price: float) -> str:
    cents = price * 100.0
    bands = [
        (0, 5, "00-05"),
        (5, 10, "05-10"),
        (10, 20, "10-20"),
        (20, 35, "20-35"),
        (35, 50, "35-50"),
        (50, 65, "50-65"),
        (65, 80, "65-80"),
        (80, 90, "80-90"),
        (90, 97, "90-97"),
        (97, 101, "97-100"),
    ]
    for lo, hi, label in bands:
        if lo <= cents < hi:
            return label
    return "other"


def time_bucket(row: dict[str, Any]) -> str:
    slug = slug_of(row)
    end_s = infer_market_end_s(slug)
    if end_s is None:
        return "unknown"
    ts_s = int(row.get("timestamp") or 0)
    delta = end_s - ts_s
    if delta < 0:
        return "after_end"
    if delta <= 60:
        return "-60~0s"
    if delta <= 5 * 60:
        return "-5~-1m"
    if delta <= 15 * 60:
        return "-15~-5m"
    if delta <= 30 * 60:
        return "-30~-15m"
    if delta <= 60 * 60:
        return "-60~-30m"
    return "<-60m"


def add_bucket(bucket: dict[str, Any], qty: float, actual: float, gross: float) -> None:
    bucket["count"] += 1
    bucket["qty"] += qty
    bucket["actual"] += actual
    bucket["gross"] += gross


def build_position_index(position_rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = defaultdict(lambda: {"YES": 0.0, "NO": 0.0, "value": 0.0})
    for row in position_rows:
        cid = str(row.get("conditionId") or "")
        if not cid:
            continue
        outcome = normalize_outcome(row)
        if outcome not in {"YES", "NO"}:
            continue
        out[cid][outcome] += fnum(row.get("size"))
        out[cid]["value"] += fnum(row.get("currentValue"))
    return out


def analyze_account(
    label: str,
    user: str,
    start_s: int,
    end_s: int,
    *,
    window_hours: int,
    retries: int,
    timeout: int,
    pause_s: float,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    activity_types = ["TRADE", "MERGE", "REDEEM", "MAKER_REBATE", "REWARD", "REFERRAL_REWARD", "SPLIT"]
    print(f"[fetch] {label} {user}", file=sys.stderr, flush=True)
    rows = base.fetch_activity_rows(
        user,
        start_s,
        end_s,
        activity_types=activity_types,
        window_hours=window_hours,
        retries=retries,
        timeout=timeout,
        pause_s=pause_s,
    )
    positions = base.fetch_positions(user, retries=retries, timeout=timeout, pause_s=pause_s)
    pos = build_position_index(positions)

    by_cond: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "condition_id": "",
            "slug": "",
            "title": "",
            "asset": "OTHER",
            "tf": "other",
            "trade_count": 0,
            "buy_qty": 0.0,
            "buy_actual": 0.0,
            "buy_gross": 0.0,
            "fee": 0.0,
            "sell": 0.0,
            "merge": 0.0,
            "redeem": 0.0,
            "rebate": 0.0,
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
    counts: dict[str, int] = defaultdict(int)
    usdc_by_type: dict[str, float] = defaultdict(float)
    no_condition_rebate = 0.0
    price_bands: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "qty": 0.0, "actual": 0.0, "gross": 0.0})
    time_bands: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "qty": 0.0, "actual": 0.0, "gross": 0.0})
    top_time_price: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"count": 0, "qty": 0.0, "actual": 0.0, "gross": 0.0})

    for row in rows:
        typ = str(row.get("type") or "").upper()
        counts[typ] += 1
        qty = fnum(row.get("size"))
        price = fnum(row.get("price"))
        gross = qty * price
        usdc = fnum(row.get("usdcSize")) if row.get("usdcSize") is not None else gross
        usdc_by_type[typ] += usdc
        cid = str(row.get("conditionId") or "")
        if not cid:
            if typ in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
                no_condition_rebate += usdc
            continue
        item = by_cond[cid]
        item["condition_id"] = cid
        if not item["slug"]:
            item["slug"] = slug_of(row)
            item["title"] = row.get("title") or ""
            item["asset"], item["tf"] = classify_asset_tf(item["slug"], item["title"])
        if typ == "TRADE":
            side = str(row.get("side") or "").upper()
            if side == "BUY":
                item["trade_count"] += 1
                item["buy_qty"] += qty
                item["buy_gross"] += gross
                item["buy_actual"] += usdc
                item["fee"] += usdc - gross
                ts_s = int(row.get("timestamp") or 0)
                item["first_trade_s"] = ts_s if item["first_trade_s"] is None else min(item["first_trade_s"], ts_s)
                item["last_trade_s"] = ts_s if item["last_trade_s"] is None else max(item["last_trade_s"], ts_s)
                outcome = normalize_outcome(row)
                if outcome == "YES":
                    item["up_qty"] += qty
                    item["up_actual"] += usdc
                    item["up_gross"] += gross
                elif outcome == "NO":
                    item["down_qty"] += qty
                    item["down_actual"] += usdc
                    item["down_gross"] += gross
                pb = price_bucket(price)
                tb = time_bucket(row)
                add_bucket(price_bands[pb], qty, usdc, gross)
                add_bucket(time_bands[tb], qty, usdc, gross)
                add_bucket(top_time_price[(tb, pb)], qty, usdc, gross)
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
    for cid, item in by_cond.items():
        cash_in = item["sell"] + item["merge"] + item["redeem"] + item["rebate"]
        cash_pnl = cash_in - item["buy_actual"] - item["split"]
        paired_qty = min(item["up_qty"], item["down_qty"])
        resid_qty = abs(item["up_qty"] - item["down_qty"])
        total_buy_qty = item["up_qty"] + item["down_qty"]
        up_actual_avg = item["up_actual"] / item["up_qty"] if item["up_qty"] > EPS else None
        down_actual_avg = item["down_actual"] / item["down_qty"] if item["down_qty"] > EPS else None
        up_gross_avg = item["up_gross"] / item["up_qty"] if item["up_qty"] > EPS else None
        down_gross_avg = item["down_gross"] / item["down_qty"] if item["down_qty"] > EPS else None
        actual_pair_cost = (
            up_actual_avg + down_actual_avg
            if paired_qty > EPS and up_actual_avg is not None and down_actual_avg is not None
            else None
        )
        gross_pair_cost = (
            up_gross_avg + down_gross_avg
            if paired_qty > EPS and up_gross_avg is not None and down_gross_avg is not None
            else None
        )
        pair_pnl = paired_qty * (1.0 - actual_pair_cost) if actual_pair_cost is not None else 0.0
        residual_pnl_est = cash_pnl - pair_pnl
        pitem = pos.get(cid, {"YES": 0.0, "NO": 0.0, "value": 0.0})
        row = {
            "condition_id": cid,
            "slug": item["slug"],
            "title": item["title"],
            "asset": item["asset"],
            "tf": item["tf"],
            "trade_count": item["trade_count"],
            "first_trade_s": item["first_trade_s"],
            "first_trade_bjt": bjt_s(item["first_trade_s"]) if item["first_trade_s"] is not None else None,
            "last_trade_s": item["last_trade_s"],
            "last_trade_bjt": bjt_s(item["last_trade_s"]) if item["last_trade_s"] is not None else None,
            "buy_qty": round6(item["buy_qty"]),
            "buy_actual": round6(item["buy_actual"]),
            "buy_gross": round6(item["buy_gross"]),
            "fee": round6(item["fee"]),
            "sell": round6(item["sell"]),
            "merge": round6(item["merge"]),
            "redeem": round6(item["redeem"]),
            "rebate": round6(item["rebate"]),
            "split": round6(item["split"]),
            "cash_in": round6(cash_in),
            "cash_pnl": round6(cash_pnl),
            "paired_qty": round6(paired_qty),
            "resid_qty": round6(resid_qty),
            "resid_rate": pct(resid_qty, total_buy_qty),
            "resid_side": "UP" if item["up_qty"] > item["down_qty"] else "DOWN" if item["down_qty"] > item["up_qty"] else "FLAT",
            "actual_pair_cost": round6(actual_pair_cost),
            "gross_pair_cost": round6(gross_pair_cost),
            "pair_fee_cost": round6(actual_pair_cost - gross_pair_cost)
            if actual_pair_cost is not None and gross_pair_cost is not None
            else None,
            "pair_pnl": round6(pair_pnl),
            "residual_pnl_est": round6(residual_pnl_est),
            "up_qty": round6(item["up_qty"]),
            "down_qty": round6(item["down_qty"]),
            "current_yes": round6(pitem["YES"]),
            "current_no": round6(pitem["NO"]),
            "current_value": round6(pitem["value"]),
        }
        market_rows.append(row)

    paired_qty_total = sum(fnum(r["paired_qty"]) for r in market_rows)
    pair_actual_notional = sum(fnum(r["paired_qty"]) * fnum(r["actual_pair_cost"]) for r in market_rows if r["actual_pair_cost"] is not None)
    pair_gross_notional = sum(fnum(r["paired_qty"]) * fnum(r["gross_pair_cost"]) for r in market_rows if r["gross_pair_cost"] is not None)
    buy_actual = sum(fnum(r["buy_actual"]) for r in market_rows)
    buy_gross = sum(fnum(r["buy_gross"]) for r in market_rows)
    fee = sum(fnum(r["fee"]) for r in market_rows)
    cash_in = sum(fnum(r["cash_in"]) for r in market_rows) + no_condition_rebate
    old_rows = [r for r in market_rows if fnum(r["buy_actual"]) <= EPS and fnum(r["cash_in"]) > EPS]
    with_buy_rows = [r for r in market_rows if fnum(r["buy_actual"]) > EPS]
    open_no_in_rows = [r for r in with_buy_rows if fnum(r["cash_in"]) <= EPS]
    current_value = sum(fnum(r["current_value"]) for r in market_rows)

    group_rows: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "markets": 0,
            "trade_count": 0,
            "buy_actual": 0.0,
            "cash_in": 0.0,
            "cash_pnl": 0.0,
            "pair_pnl": 0.0,
            "residual_pnl_est": 0.0,
            "paired_qty": 0.0,
            "resid_qty": 0.0,
            "buy_qty": 0.0,
            "win_markets": 0,
            "loss_markets": 0,
            "current_value": 0.0,
        }
    )
    for r in with_buy_rows:
        g = groups[(str(r["asset"]), str(r["tf"]))]
        g["markets"] += 1
        g["trade_count"] += int(r["trade_count"] or 0)
        g["buy_actual"] += fnum(r["buy_actual"])
        g["cash_in"] += fnum(r["cash_in"])
        g["cash_pnl"] += fnum(r["cash_pnl"])
        g["pair_pnl"] += fnum(r["pair_pnl"])
        g["residual_pnl_est"] += fnum(r["residual_pnl_est"])
        g["paired_qty"] += fnum(r["paired_qty"])
        g["resid_qty"] += fnum(r["resid_qty"])
        g["buy_qty"] += fnum(r["buy_qty"])
        g["current_value"] += fnum(r["current_value"])
        if fnum(r["cash_pnl"]) > 0:
            g["win_markets"] += 1
        elif fnum(r["cash_pnl"]) < 0:
            g["loss_markets"] += 1
    for (asset, tf), g in groups.items():
        group_rows.append(
            {
                "asset": asset,
                "tf": tf,
                "markets": g["markets"],
                "trade_count": g["trade_count"],
                "buy_actual": round6(g["buy_actual"]),
                "cash_in": round6(g["cash_in"]),
                "cash_pnl": round6(g["cash_pnl"]),
                "current_value": round6(g["current_value"]),
                "cash_plus_current": round6(g["cash_pnl"] + g["current_value"]),
                "pair_pnl": round6(g["pair_pnl"]),
                "residual_pnl_est": round6(g["residual_pnl_est"]),
                "resid_rate": pct(g["resid_qty"], g["buy_qty"]),
                "roi_cash": pct(g["cash_pnl"], g["buy_actual"]),
                "win_markets": g["win_markets"],
                "loss_markets": g["loss_markets"],
            }
        )
    group_rows.sort(key=lambda r: fnum(r["cash_plus_current"]), reverse=True)
    market_rows.sort(key=lambda r: fnum(r["cash_pnl"]), reverse=True)

    summary = {
        "label": label,
        "user": user,
        "window": {"start_s": start_s, "start_iso": iso_s(start_s), "start_bjt": bjt_s(start_s), "end_s": end_s, "end_iso": iso_s(end_s), "end_bjt": bjt_s(end_s)},
        "row_count": len(rows),
        "activity_counts": dict(sorted(counts.items())),
        "activity_usdc": {k: round6(v) for k, v in sorted(usdc_by_type.items())},
        "buy_actual": round6(buy_actual),
        "buy_gross": round6(buy_gross),
        "fee": round6(fee),
        "fee_rate_on_gross": pct(fee, buy_gross),
        "cash_in": round6(cash_in),
        "cash_pnl_total": round6(cash_in - buy_actual),
        "old_no_buy_cash": round6(sum(fnum(r["cash_in"]) for r in old_rows)),
        "old_no_buy_markets": len(old_rows),
        "with_buy_cash_ex_no_condition_rebate": round6(sum(fnum(r["cash_pnl"]) for r in with_buy_rows)),
        "with_buy_markets": len(with_buy_rows),
        "open_no_in_cash": round6(sum(fnum(r["cash_pnl"]) for r in open_no_in_rows)),
        "open_no_in_markets": len(open_no_in_rows),
        "no_condition_rebate": round6(no_condition_rebate),
        "current_value": round6(current_value),
        "with_buy_plus_current_ex_no_condition_rebate": round6(sum(fnum(r["cash_pnl"]) for r in with_buy_rows) + current_value),
        "with_buy_plus_current_plus_no_condition_rebate": round6(
            sum(fnum(r["cash_pnl"]) for r in with_buy_rows) + current_value + no_condition_rebate
        ),
        "paired_market_count": sum(1 for r in market_rows if fnum(r["paired_qty"]) > EPS),
        "paired_qty": round6(paired_qty_total),
        "resid_qty": round6(sum(fnum(r["resid_qty"]) for r in with_buy_rows)),
        "resid_rate_on_buy_qty": pct(sum(fnum(r["resid_qty"]) for r in with_buy_rows), sum(fnum(r["buy_qty"]) for r in with_buy_rows)),
        "actual_pair_cost": round6(pair_actual_notional / paired_qty_total) if paired_qty_total > EPS else None,
        "gross_pair_cost": round6(pair_gross_notional / paired_qty_total) if paired_qty_total > EPS else None,
        "paired_actual_profit": round6(sum(fnum(r["pair_pnl"]) for r in market_rows)),
        "per_market_actual_pair_cost": summarize([fnum(r["actual_pair_cost"]) for r in market_rows if r["actual_pair_cost"] is not None]),
    }
    deep = {
        "price_bands": {k: {kk: round6(vv) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in sorted(price_bands.items())},
        "time_bands": {k: {kk: round6(vv) if isinstance(vv, float) else vv for kk, vv in v.items()} for k, v in sorted(time_bands.items())},
        "top_time_price": [
            [tb, pb, round6(v["actual"]), round6(v["qty"]), int(v["count"])]
            for (tb, pb), v in sorted(top_time_price.items(), key=lambda kv: kv[1]["actual"], reverse=True)[:20]
        ],
        "top_cash_markets": market_rows[:20],
        "worst_cash_markets": sorted(market_rows, key=lambda r: fnum(r["cash_pnl"]))[:20],
        "top_pair_markets": sorted(market_rows, key=lambda r: fnum(r["pair_pnl"]), reverse=True)[:20],
        "worst_pair_markets": sorted(market_rows, key=lambda r: fnum(r["pair_pnl"]))[:20],
    }
    return summary, market_rows, group_rows, deep


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
    parser.add_argument("--account", action="append", choices=sorted(ACCOUNTS), default=[])
    parser.add_argument(
        "--user",
        action="append",
        default=[],
        metavar="LABEL=0xWALLET",
        help="Add an arbitrary account by label and proxy wallet.",
    )
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=100)
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args()

    start_s = parse_iso_to_s(args.start_iso)
    end_s = parse_iso_to_s(args.end_iso)
    account_map = dict(ACCOUNTS)
    dynamic_labels: list[str] = []
    for spec in args.user:
        if "=" in spec:
            label, wallet = spec.split("=", 1)
        else:
            wallet = spec
            label = wallet[:10]
        label = label.strip()
        wallet = wallet.strip().lower()
        if not label or not wallet:
            raise SystemExit(f"invalid --user value: {spec!r}")
        account_map[label] = wallet
        dynamic_labels.append(label)
    labels = args.account + dynamic_labels
    if not labels:
        labels = ["b55", "ce25"]
    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "data" / "exports" / f"candidate_deep_compare_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    output_dir.mkdir(parents=True, exist_ok=True)

    summaries: list[dict[str, Any]] = []
    all_groups: list[dict[str, Any]] = []
    for label in labels:
        summary, markets, groups, deep = analyze_account(
            label,
            account_map[label],
            start_s,
            end_s,
            window_hours=args.window_hours,
            retries=args.retries,
            timeout=args.timeout,
            pause_s=args.pause_ms / 1000.0,
        )
        summaries.append(summary)
        for row in groups:
            all_groups.append({"label": label, **row})
        account_dir = output_dir / label
        account_dir.mkdir(parents=True, exist_ok=True)
        (account_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (account_dir / "deep.json").write_text(json.dumps(deep, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_csv(account_dir / "market_cash_pnl.csv", markets)
        write_csv(account_dir / "asset_tf_groups.csv", groups)

    write_csv(output_dir / "summary.csv", summaries)
    write_csv(output_dir / "asset_tf_groups.csv", all_groups)
    (output_dir / "summary.json").write_text(json.dumps(summaries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "accounts": labels}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

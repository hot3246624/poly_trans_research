#!/usr/bin/env python3
"""Profile ce25 execution pattern from public Polymarket activity.

The goal is not just PnL. It extracts market-level sequence features:
- first/last buy timing relative to market end;
- first-side vs opposite-side construction;
- delay to first opposite leg;
- pair cost / residual / cohort PnL buckets;
- residual side bias by asset and timeframe.
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
CE25 = "0xce25e214d5cfe4f459cf67f08df581885aae7fdc"
EPS = 1e-9


def load_base() -> Any:
    spec = importlib.util.spec_from_file_location("activity_base", BASE_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BASE_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = load_base()


def fnum(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def parse_iso_to_s(value: str) -> int:
    return int(dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


def iso_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone.utc).isoformat().replace("+00:00", "Z")


def bjt_s(ts_s: int | float | None) -> str | None:
    if ts_s is None:
        return None
    return dt.datetime.fromtimestamp(float(ts_s), tz=dt.timezone(dt.timedelta(hours=8))).isoformat()


def pct(num: float, den: float) -> float | None:
    return round(num / den, 6) if abs(den) > EPS else None


def round6(value: float | None) -> float | None:
    return round(value, 6) if value is not None and math.isfinite(value) else None


def slug_of(row: dict[str, Any]) -> str:
    return str(row.get("slug") or row.get("eventSlug") or "")


def normalize_outcome(row: dict[str, Any]) -> str | None:
    return base.normalize_outcome(row)


def classify_asset_tf(slug: str, title: str | None = None) -> tuple[str, str]:
    text = f"{slug} {title or ''}".lower()
    if "bitcoin" in text or re.search(r"(^|[^a-z0-9])btc([^a-z0-9]|$)", text):
        asset = "BTC"
    elif "ethereum" in text or re.search(r"(^|[^a-z0-9])eth([^a-z0-9]|$)", text):
        asset = "ETH"
    elif "solana" in text or re.search(r"(^|[^a-z0-9])sol([^a-z0-9]|$)", text):
        asset = "SOL"
    elif "xrp" in text:
        asset = "XRP"
    else:
        asset = "OTHER"
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


def market_end_s(slug: str) -> int | None:
    match = re.search(r"updown-(5m|15m|4h)-(\d{10})$", slug)
    if not match:
        return None
    duration_s = {"5m": 300, "15m": 900, "4h": 14400}[match.group(1)]
    return int(match.group(2)) + duration_s


def delta_bucket(delta_s: float | None) -> str:
    if delta_s is None:
        return "unknown"
    if delta_s < 0:
        return "after_end"
    if delta_s <= 60:
        return "last_60s"
    if delta_s <= 300:
        return "1-5m"
    if delta_s <= 900:
        return "5-15m"
    if delta_s <= 1800:
        return "15-30m"
    if delta_s <= 3600:
        return "30-60m"
    return ">60m"


def delay_bucket(delay_s: float | None) -> str:
    if delay_s is None:
        return "one_sided"
    if delay_s <= 5:
        return "<=5s"
    if delay_s <= 15:
        return "5-15s"
    if delay_s <= 30:
        return "15-30s"
    if delay_s <= 60:
        return "30-60s"
    if delay_s <= 180:
        return "1-3m"
    if delay_s <= 600:
        return "3-10m"
    return ">10m"


def price_bucket(price: float | None) -> str:
    if price is None:
        return "none"
    cents = price * 100.0
    if cents < 5:
        return "00-05"
    if cents < 10:
        return "05-10"
    if cents < 20:
        return "10-20"
    if cents < 35:
        return "20-35"
    if cents < 50:
        return "35-50"
    if cents < 65:
        return "50-65"
    if cents < 80:
        return "65-80"
    if cents < 90:
        return "80-90"
    if cents < 97:
        return "90-97"
    if cents <= 100:
        return "97-100"
    return "other"


def pair_bucket(pair_cost: float | None) -> str:
    if pair_cost is None:
        return "unpaired"
    if pair_cost < 0.85:
        return "<0.85"
    if pair_cost < 0.90:
        return "0.85-0.90"
    if pair_cost < 0.95:
        return "0.90-0.95"
    if pair_cost < 0.98:
        return "0.95-0.98"
    if pair_cost < 1.00:
        return "0.98-1.00"
    if pair_cost < 1.05:
        return "1.00-1.05"
    if pair_cost < 1.10:
        return "1.05-1.10"
    return ">=1.10"


def residual_bucket(resid_rate: float | None) -> str:
    if resid_rate is None:
        return "none"
    if resid_rate < 0.02:
        return "<2%"
    if resid_rate < 0.05:
        return "2-5%"
    if resid_rate < 0.10:
        return "5-10%"
    if resid_rate < 0.20:
        return "10-20%"
    if resid_rate < 0.35:
        return "20-35%"
    return ">=35%"


def read_post_followup(path: Path) -> dict[str, float]:
    if not path.exists():
        return {}
    out: dict[str, float] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("label") == "ce25":
                out[str(row.get("condition_id") or "")] = fnum(row.get("cohort_cash"))
    return out


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


def summarize_group(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "markets": 0,
            "buy_actual": 0.0,
            "cash_pnl": 0.0,
            "cohort_pnl": 0.0,
            "pair_pnl": 0.0,
            "residual_pnl": 0.0,
            "paired_qty": 0.0,
            "resid_qty": 0.0,
            "buy_qty": 0.0,
            "wins": 0,
            "losses": 0,
        }
    )
    for row in rows:
        group_key = str(row.get(key) or "")
        item = grouped[group_key]
        item["markets"] += 1
        item["buy_actual"] += fnum(row.get("buy_actual"))
        item["cash_pnl"] += fnum(row.get("cash_pnl"))
        item["cohort_pnl"] += fnum(row.get("cohort_pnl"))
        item["pair_pnl"] += fnum(row.get("pair_pnl"))
        item["residual_pnl"] += fnum(row.get("residual_pnl_est"))
        item["paired_qty"] += fnum(row.get("paired_qty"))
        item["resid_qty"] += fnum(row.get("resid_qty"))
        item["buy_qty"] += fnum(row.get("buy_qty"))
        if fnum(row.get("cohort_pnl")) > 0:
            item["wins"] += 1
        elif fnum(row.get("cohort_pnl")) < 0:
            item["losses"] += 1
    out: list[dict[str, Any]] = []
    for group_key, item in grouped.items():
        out.append(
            {
                "bucket": group_key,
                "markets": item["markets"],
                "buy_actual": round6(item["buy_actual"]),
                "cash_pnl": round6(item["cash_pnl"]),
                "cohort_pnl": round6(item["cohort_pnl"]),
                "roi_cohort": pct(item["cohort_pnl"], item["buy_actual"]),
                "pair_pnl": round6(item["pair_pnl"]),
                "residual_pnl": round6(item["residual_pnl"]),
                "resid_rate": pct(item["resid_qty"], item["buy_qty"]),
                "win_loss": f"{item['wins']}/{item['losses']}",
            }
        )
    return sorted(out, key=lambda r: fnum(r["cohort_pnl"]), reverse=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user", default=CE25)
    parser.add_argument("--start-iso", required=True)
    parser.add_argument("--end-iso", required=True)
    parser.add_argument("--window-hours", type=int, default=24)
    parser.add_argument("--retries", type=int, default=5)
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--pause-ms", type=int, default=80)
    parser.add_argument("--cohort-followup-csv", default=None)
    parser.add_argument(
        "--activity-types",
        default="TRADE,MERGE,REDEEM,MAKER_REBATE,REWARD,REFERRAL_REWARD,SPLIT",
        help="Comma-separated public activity types to fetch.",
    )
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    start_s = parse_iso_to_s(args.start_iso)
    end_s = parse_iso_to_s(args.end_iso)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    pause_s = args.pause_ms / 1000.0
    activity_types = [item.strip().upper() for item in args.activity_types.split(",") if item.strip()]
    if not activity_types:
        raise SystemExit("--activity-types produced an empty list")
    print(f"[fetch] ce25 {args.user}", file=sys.stderr, flush=True)
    activity = base.fetch_activity_rows(
        args.user,
        start_s,
        end_s,
        activity_types=activity_types,
        window_hours=args.window_hours,
        retries=args.retries,
        timeout=args.timeout,
        pause_s=pause_s,
    )
    (output_dir / "raw_activity.json").write_text(json.dumps(activity, ensure_ascii=False) + "\n", encoding="utf-8")
    followup = read_post_followup(Path(args.cohort_followup_csv)) if args.cohort_followup_csv else {}

    by_condition: dict[str, list[dict[str, Any]]] = defaultdict(list)
    cash_by_condition: dict[str, dict[str, float]] = defaultdict(lambda: {"buy": 0.0, "gross": 0.0, "fee": 0.0, "cash": 0.0, "rebate": 0.0})
    for row in activity:
        cid = str(row.get("conditionId") or "")
        if not cid:
            continue
        typ = str(row.get("type") or "").upper()
        qty = fnum(row.get("size"))
        price = fnum(row.get("price"))
        gross = qty * price
        usdc = fnum(row.get("usdcSize")) if row.get("usdcSize") is not None else gross
        if typ == "TRADE" and str(row.get("side") or "").upper() == "BUY":
            by_condition[cid].append(row)
            cash_by_condition[cid]["buy"] += usdc
            cash_by_condition[cid]["gross"] += gross
            cash_by_condition[cid]["fee"] += usdc - gross
        elif typ == "TRADE" and str(row.get("side") or "").upper() == "SELL":
            cash_by_condition[cid]["cash"] += usdc
        elif typ in {"MERGE", "REDEEM"}:
            cash_by_condition[cid]["cash"] += usdc
        elif typ in {"MAKER_REBATE", "REWARD", "REFERRAL_REWARD"}:
            cash_by_condition[cid]["cash"] += usdc
            cash_by_condition[cid]["rebate"] += usdc

    market_rows: list[dict[str, Any]] = []
    for cid, rows in by_condition.items():
        rows.sort(key=lambda r: (int(r.get("timestamp") or 0), str(r.get("transactionHash") or "")))
        slug = slug_of(rows[0])
        title = rows[0].get("title") or ""
        asset, tf = classify_asset_tf(slug, title)
        end_market_s = market_end_s(slug)
        qty = {"YES": 0.0, "NO": 0.0}
        actual = {"YES": 0.0, "NO": 0.0}
        gross = {"YES": 0.0, "NO": 0.0}
        first_ts_by_side: dict[str, int] = {}
        first_price_by_side: dict[str, float] = {}
        first_size_by_side: dict[str, float] = {}
        buy_events: list[dict[str, Any]] = []
        for row in rows:
            side = normalize_outcome(row)
            if side not in {"YES", "NO"}:
                continue
            ts_s = int(row.get("timestamp") or 0)
            q = fnum(row.get("size"))
            price = fnum(row.get("price"))
            g = q * price
            a = fnum(row.get("usdcSize")) if row.get("usdcSize") is not None else g
            qty[side] += q
            actual[side] += a
            gross[side] += g
            first_ts_by_side.setdefault(side, ts_s)
            first_price_by_side.setdefault(side, price)
            first_size_by_side.setdefault(side, q)
            buy_events.append({"ts_s": ts_s, "side": side, "qty": q, "actual": a, "gross": g, "price": price})
        if not buy_events:
            continue
        first = buy_events[0]
        first_side = first["side"]
        opp_side = "NO" if first_side == "YES" else "YES"
        first_ts = first["ts_s"]
        last_ts = buy_events[-1]["ts_s"]
        opp_ts = first_ts_by_side.get(opp_side)
        pair_delay_s = opp_ts - first_ts if opp_ts is not None else None
        first_side_qty = qty[first_side]
        opp_qty = qty[opp_side]
        paired_qty = min(qty["YES"], qty["NO"])
        resid_qty = abs(qty["YES"] - qty["NO"])
        buy_qty = qty["YES"] + qty["NO"]
        yes_avg = actual["YES"] / qty["YES"] if qty["YES"] > EPS else None
        no_avg = actual["NO"] / qty["NO"] if qty["NO"] > EPS else None
        first_side_avg = actual[first_side] / qty[first_side] if qty[first_side] > EPS else None
        opp_avg = actual[opp_side] / qty[opp_side] if qty[opp_side] > EPS else None
        pair_cost = yes_avg + no_avg if yes_avg is not None and no_avg is not None and paired_qty > EPS else None
        pair_pnl = paired_qty * (1.0 - pair_cost) if pair_cost is not None else 0.0
        cash = cash_by_condition[cid]
        cash_pnl = cash["cash"] - cash["buy"]
        cohort_pnl = followup.get(cid, cash_pnl)
        first_delta = end_market_s - first_ts if end_market_s is not None else None
        last_delta = end_market_s - last_ts if end_market_s is not None else None
        market_rows.append(
            {
                "condition_id": cid,
                "slug": slug,
                "title": title,
                "asset": asset,
                "tf": tf,
                "trade_count": len(buy_events),
                "first_side": "UP" if first_side == "YES" else "DOWN",
                "resid_side": "UP" if qty["YES"] > qty["NO"] else "DOWN" if qty["NO"] > qty["YES"] else "FLAT",
                "first_trade_s": first_ts,
                "first_trade_bjt": bjt_s(first_ts),
                "last_trade_s": last_ts,
                "last_trade_bjt": bjt_s(last_ts),
                "first_delta_s": round6(first_delta),
                "last_delta_s": round6(last_delta),
                "first_delta_bucket": delta_bucket(first_delta),
                "last_delta_bucket": delta_bucket(last_delta),
                "pair_delay_s": round6(pair_delay_s),
                "pair_delay_bucket": delay_bucket(pair_delay_s),
                "first_price": round6(first["price"]),
                "first_price_bucket": price_bucket(first["price"]),
                "first_side_qty": round6(first_side_qty),
                "opp_qty": round6(opp_qty),
                "first_side_avg": round6(first_side_avg),
                "opp_avg": round6(opp_avg),
                "pair_cost": round6(pair_cost),
                "pair_bucket": pair_bucket(pair_cost),
                "buy_qty": round6(buy_qty),
                "buy_actual": round6(cash["buy"]),
                "fee": round6(cash["fee"]),
                "fee_rate": pct(cash["fee"], cash["gross"]),
                "cash_in": round6(cash["cash"]),
                "cash_pnl": round6(cash_pnl),
                "cohort_pnl": round6(cohort_pnl),
                "paired_qty": round6(paired_qty),
                "resid_qty": round6(resid_qty),
                "resid_rate": pct(resid_qty, buy_qty),
                "residual_bucket": residual_bucket(pct(resid_qty, buy_qty)),
                "pair_pnl": round6(pair_pnl),
                "residual_pnl_est": round6(cash_pnl - pair_pnl),
                "yes_qty": round6(qty["YES"]),
                "no_qty": round6(qty["NO"]),
                "yes_avg": round6(yes_avg),
                "no_avg": round6(no_avg),
            }
        )

    market_rows.sort(key=lambda r: fnum(r["cohort_pnl"]), reverse=True)
    write_csv(output_dir / "ce25_market_sequence.csv", market_rows)
    group_names = [
        "asset",
        "tf",
        "first_side",
        "resid_side",
        "first_delta_bucket",
        "last_delta_bucket",
        "pair_delay_bucket",
        "first_price_bucket",
        "pair_bucket",
        "residual_bucket",
    ]
    group_summary = {name: summarize_group(market_rows, name) for name in group_names}
    (output_dir / "group_summary.json").write_text(json.dumps(group_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {
        "user": args.user,
        "window": {"start_s": start_s, "start_iso": iso_s(start_s), "start_bjt": bjt_s(start_s), "end_s": end_s, "end_iso": iso_s(end_s), "end_bjt": bjt_s(end_s)},
        "activity_rows": len(activity),
        "market_count": len(market_rows),
        "buy_actual": round6(sum(fnum(r["buy_actual"]) for r in market_rows)),
        "cash_pnl": round6(sum(fnum(r["cash_pnl"]) for r in market_rows)),
        "cohort_pnl": round6(sum(fnum(r["cohort_pnl"]) for r in market_rows)),
        "fee": round6(sum(fnum(r["fee"]) for r in market_rows)),
        "avg_pair_cost_weighted": round6(
            sum(fnum(r["paired_qty"]) * fnum(r["pair_cost"]) for r in market_rows if r["pair_cost"] is not None)
            / sum(fnum(r["paired_qty"]) for r in market_rows if r["pair_cost"] is not None)
        ),
        "resid_rate": pct(sum(fnum(r["resid_qty"]) for r in market_rows), sum(fnum(r["buy_qty"]) for r in market_rows)),
        "top_cohort": market_rows[:20],
        "worst_cohort": sorted(market_rows, key=lambda r: fnum(r["cohort_pnl"]))[:20],
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output_dir), "markets": len(market_rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

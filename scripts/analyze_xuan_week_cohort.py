#!/usr/bin/env python3
"""Resolve xuan weekly buy cohort by final Gamma outcomes."""

from __future__ import annotations

import csv
import json
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch_json(url: str) -> Any:
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def outcome_for_slug(slug: str) -> dict[str, Any]:
    url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({"slug": slug})
    for attempt in range(4):
        try:
            data = fetch_json(url)
            if not data:
                return {"slug": slug, "winner": None, "error": "not_found"}
            market = (data[0].get("markets") or [{}])[0]
            prices = json.loads(market.get("outcomePrices") or "[]")
            outcomes = json.loads(market.get("outcomes") or "[]")
            winner = None
            for outcome, price in zip(outcomes, prices):
                try:
                    if float(price) >= 0.999:
                        winner = str(outcome).upper()
                except (TypeError, ValueError):
                    pass
            return {
                "slug": slug,
                "condition_id": market.get("conditionId"),
                "winner": winner,
                "outcome_prices": prices,
                "closed_time": market.get("closedTime"),
            }
        except Exception as exc:  # noqa: BLE001
            if attempt == 3:
                return {"slug": slug, "winner": None, "error": str(exc)}
            time.sleep(0.25 * (attempt + 1))
    return {"slug": slug, "winner": None, "error": "unreachable"}


def main() -> int:
    in_path = Path("data/exports/xuan_public_activity_pnl_20260407_20260413_bjt/market_trade_metrics.csv")
    out_dir = Path("data/exports/xuan_week_cohort_20260407_20260413_bjt")
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = list(csv.DictReader(in_path.open()))
    slugs = sorted({row["slug"] for row in rows if row.get("slug")})
    outcomes: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=24) as pool:
        futures = {pool.submit(outcome_for_slug, slug): slug for slug in slugs}
        for i, fut in enumerate(as_completed(futures), start=1):
            result = fut.result()
            outcomes[result["slug"]] = result
            if i % 200 == 0:
                print(f"[gamma] {i}/{len(slugs)}", flush=True)

    market_out = []
    total_cost = total_value = total_paired_profit = total_residual_qty = 0.0
    missing = 0
    for row in rows:
        slug = row["slug"]
        outcome = outcomes.get(slug, {})
        winner = outcome.get("winner")
        yes_qty = float(row["yes_qty"] or 0)
        no_qty = float(row["no_qty"] or 0)
        yes_actual_avg = float(row["yes_actual_avg"] or 0)
        no_actual_avg = float(row["no_actual_avg"] or 0)
        yes_cost = yes_qty * yes_actual_avg
        no_cost = no_qty * no_actual_avg
        cost = yes_cost + no_cost
        if winner == "UP":
            value = yes_qty
        elif winner == "DOWN":
            value = no_qty
        else:
            value = 0.0
            missing += 1
        pnl = value - cost
        paired_profit = float(row["paired_actual_profit"] or 0)
        residual_qty = float(row["lifetime_residual_qty"] or 0)
        total_cost += cost
        total_value += value
        total_paired_profit += paired_profit
        total_residual_qty += residual_qty
        market_out.append(
            {
                **row,
                "winner": winner,
                "final_value": round(value, 8),
                "buy_cost": round(cost, 8),
                "final_pnl": round(pnl, 8),
                "closed_time": outcome.get("closed_time"),
                "outcome_error": outcome.get("error"),
            }
        )

    summary = {
        "input": str(in_path),
        "markets": len(rows),
        "slugs": len(slugs),
        "missing_outcomes": missing,
        "buy_cost": round(total_cost, 8),
        "final_value": round(total_value, 8),
        "final_cohort_pnl": round(total_value - total_cost, 8),
        "paired_actual_profit": round(total_paired_profit, 8),
        "total_residual_qty": round(total_residual_qty, 8),
        "residual_contribution_vs_pair_profit": round((total_value - total_cost) - total_paired_profit, 8),
    }

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    with (out_dir / "market_cohort_pnl.csv").open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(market_out[0]))
        writer.writeheader()
        writer.writerows(market_out)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

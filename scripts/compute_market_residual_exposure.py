#!/usr/bin/env python3
"""Compute an auditable residual-exposure ratio from market trade metrics."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def number(row: dict[str, str], key: str) -> float:
    value = row.get(key, "")
    return float(value) if value else 0.0


def quantile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    index = (len(values) - 1) * probability
    lower = int(index)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (index - lower)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--market-trade-metrics", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    rows = list(csv.DictReader(args.market_trade_metrics.open(encoding="utf-8")))
    traded_notional = 0.0
    residual_notional = 0.0
    residual_shares = 0.0
    paired_qty = 0.0
    residual_market_count = 0
    market_rer: list[float] = []
    for row in rows:
        yes_qty = number(row, "yes_qty")
        no_qty = number(row, "no_qty")
        yes_avg = number(row, "yes_actual_avg")
        no_avg = number(row, "no_actual_avg")
        traded = yes_qty * yes_avg + no_qty * no_avg
        traded_notional += traded
        paired = min(yes_qty, no_qty)
        paired_qty += paired
        if yes_qty >= no_qty:
            residual_qty = yes_qty - no_qty
            residual_value = residual_qty * yes_avg
        else:
            residual_qty = no_qty - yes_qty
            residual_value = residual_qty * no_avg
        residual_shares += residual_qty
        residual_notional += residual_value
        if residual_qty > 0:
            residual_market_count += 1
        if traded > 0:
            market_rer.append(residual_value / traded)

    payload = {
        "source": str(args.market_trade_metrics),
        "market_count": len(rows),
        "traded_notional_usdc": traded_notional,
        "residual_notional_usdc": residual_notional,
        "residual_shares": residual_shares,
        "paired_qty": paired_qty,
        "residual_market_count": residual_market_count,
        "aggregate_rer": residual_notional / traded_notional if traded_notional else None,
        "market_rer_quantiles": {
            "p50": quantile(market_rer, 0.50),
            "p90": quantile(market_rer, 0.90),
            "p95": quantile(market_rer, 0.95),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

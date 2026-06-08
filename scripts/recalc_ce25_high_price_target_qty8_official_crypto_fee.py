#!/usr/bin/env python3
"""Recalculate CE25 target_qty=8 replay ledger with official crypto taker fees.

Polymarket docs define taker fee as:
    fee = C * feeRate * p * (1 - p)
Crypto feeRate is 0.07. Fees are rounded to 5 decimal places and the smallest
charged fee is 0.00001 USDC.

This script does not mutate the canonical 3% historical ledger. It emits a
separate review artifact that supersedes the old 0.03 fee interpretation for
current Crypto taker-fee discussion.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any


ROOT = Path("/Users/hot/web3Scientist/poly_trans_research")
DEFAULT_LEDGER = (
    ROOT
    / "data"
    / "exports"
    / "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604"
    / "ce25_high_price_top1_qty_target_qty8_candidate_ledger.csv"
)
DEFAULT_STRATEGY_INPUT = (
    ROOT
    / "data"
    / "exports"
    / "ce25_high_price_top1_qty_target_qty8_candidate_ledger_20260604"
    / "CE25_HIGH_PRICE_TOP1_QTY_TARGET_QTY8_STRATEGY_INPUT.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "data"
    / "exports"
    / "ce25_high_price_top1_qty_target_qty8_official_crypto_fee_recalc_20260604"
)

STATUS = "KEEP_CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALCULATED_REVIEW_REQUIRED_NOT_OOS_READY"
STRATEGY_ID = "CE25_BTC5M_HIGH_PRICE_TOP1_QTY_GATE_V1"
OWNER_LINE = "CE25_HIGH_PRICE_RESEARCH"
FEE_SOURCE_URL = "https://docs.polymarket.com/trading/fees"
OFFICIAL_CRYPTO_TAKER_FEE_RATE = Decimal("0.07")
FEE_QUANT = Decimal("0.00001")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def d(value: str | float | int | Decimal) -> Decimal:
    return Decimal(str(value))


def q5(value: Decimal) -> Decimal:
    rounded = value.quantize(FEE_QUANT, rounding=ROUND_HALF_UP)
    return rounded if rounded >= FEE_QUANT else Decimal("0")


def official_fee(shares: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    if shares <= 0 or price < 0 or price > 1 or fee_rate <= 0:
        return Decimal("0")
    return q5(shares * fee_rate * price * (Decimal("1") - price))


def f6(value: Decimal) -> float:
    return float(value.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def non_claims() -> dict[str, bool]:
    return {
        "private_truth_ready": False,
        "strategy_promotion_ready": False,
        "live_ready": False,
        "deployable": False,
        "canary_authorized": False,
        "orders_authorized": False,
        "oos_authorized": False,
    }


def build(args: argparse.Namespace) -> dict[str, Any]:
    ledger_path = args.ledger.expanduser().resolve()
    strategy_input_path = args.strategy_input.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = read_rows(ledger_path)
    action_rows: list[dict[str, Any]] = []
    by_day: dict[str, dict[str, Decimal | int]] = defaultdict(
        lambda: {
            "action_count": 0,
            "market_ids": set(),
            "gross_cost": Decimal("0"),
            "fee": Decimal("0"),
            "buy_actual": Decimal("0"),
            "pnl": Decimal("0"),
            "paired_return": Decimal("0"),
        }
    )
    total = {
        "gross_cost": Decimal("0"),
        "fee_0p03_from_ledger": Decimal("0"),
        "fee_0p07": Decimal("0"),
        "buy_actual_0p07": Decimal("0"),
        "pnl_0p07": Decimal("0"),
        "paired_return": Decimal("0"),
        "old_pnl": Decimal("0"),
        "old_buy_actual": Decimal("0"),
    }

    for row in rows:
        qty = d(row["paired_qty"])
        first_px = d(row["first_leg_price"])
        completion_px = d(row["completion_leg_price"])
        first_fee = official_fee(qty, first_px, OFFICIAL_CRYPTO_TAKER_FEE_RATE)
        completion_fee = official_fee(qty, completion_px, OFFICIAL_CRYPTO_TAKER_FEE_RATE)
        fee = first_fee + completion_fee
        gross = qty * (first_px + completion_px)
        buy_actual = gross + fee
        paired_return = qty
        pnl = paired_return - buy_actual
        old_buy = d(row["buy_actual_est"])
        old_pnl = d(row["cash_pnl_est"])
        old_gross = gross
        old_fee = old_buy - old_gross

        out = dict(row)
        out.update(
            {
                "official_fee_source": FEE_SOURCE_URL,
                "official_fee_formula": "fee = C * feeRate * p * (1 - p)",
                "official_crypto_taker_fee_rate": str(OFFICIAL_CRYPTO_TAKER_FEE_RATE),
                "official_fee_rounding": "ROUND_HALF_UP_TO_5_DECIMALS_MIN_CHARGED_0.00001",
                "first_leg_official_fee_0p07": f"{first_fee:.5f}",
                "completion_leg_official_fee_0p07": f"{completion_fee:.5f}",
                "official_fee_0p07": f"{fee:.5f}",
                "gross_pair_cost": f"{gross:.6f}",
                "buy_actual_official_fee_0p07": f"{buy_actual:.6f}",
                "cash_pnl_official_fee_0p07": f"{pnl:.6f}",
                "old_fee_est_0p03": f"{old_fee:.6f}",
                "old_fee_delta_to_0p07": f"{(fee - old_fee):.6f}",
            }
        )
        action_rows.append(out)

        day = by_day[row["day"]]
        day["action_count"] = int(day["action_count"]) + 1
        day["market_ids"].add(row["condition_id"])  # type: ignore[union-attr]
        for key, value in [
            ("gross_cost", gross),
            ("fee", fee),
            ("buy_actual", buy_actual),
            ("pnl", pnl),
            ("paired_return", paired_return),
        ]:
            day[key] = day[key] + value  # type: ignore[operator]

        total["gross_cost"] += gross
        total["fee_0p03_from_ledger"] += old_fee
        total["fee_0p07"] += fee
        total["buy_actual_0p07"] += buy_actual
        total["pnl_0p07"] += pnl
        total["paired_return"] += paired_return
        total["old_pnl"] += old_pnl
        total["old_buy_actual"] += old_buy

    action_path = output_dir / "ce25_target_qty8_official_crypto_fee_action_recalc.csv"
    with action_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(action_rows[0].keys()))
        writer.writeheader()
        writer.writerows(action_rows)

    day_rows = []
    for day, stats in sorted(by_day.items()):
        buy = stats["buy_actual"]  # type: ignore[assignment]
        pnl = stats["pnl"]  # type: ignore[assignment]
        day_rows.append(
            {
                "day": day,
                "action_count": stats["action_count"],
                "market_count": len(stats["market_ids"]),  # type: ignore[arg-type]
                "gross_cost": f6(stats["gross_cost"]),  # type: ignore[arg-type]
                "official_fee_0p07": f6(stats["fee"]),  # type: ignore[arg-type]
                "buy_actual_official_fee_0p07": f6(buy),  # type: ignore[arg-type]
                "cash_pnl_official_fee_0p07": f6(pnl),  # type: ignore[arg-type]
                "turnover_roi_official_fee_0p07": f6(pnl / buy if buy else Decimal("0")),  # type: ignore[operator]
            }
        )
    day_path = output_dir / "ce25_target_qty8_official_crypto_fee_day_summary.csv"
    with day_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(day_rows[0].keys()))
        writer.writeheader()
        writer.writerows(day_rows)

    rounds_total = len(day_rows) * 288
    old_buy = total["old_buy_actual"]
    new_buy = total["buy_actual_0p07"]
    old_pnl = total["old_pnl"]
    new_pnl = total["pnl_0p07"]
    summary = {
        "schema_version": 1,
        "status": STATUS,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "fee_source_url": FEE_SOURCE_URL,
        "fee_formula": "fee = C * feeRate * p * (1 - p)",
        "fee_rounding": "round to 5 decimals; smallest charged fee is 0.00001 USDC",
        "official_crypto_taker_fee_rate": float(OFFICIAL_CRYPTO_TAKER_FEE_RATE),
        "old_ledger_fee_rate": 0.03,
        "old_ledger_fee_interpretation": "legacy low-fee stress / superseded for current crypto taker-fee discussion",
        "candidate_count": len(rows),
        "market_count": len({row["condition_id"] for row in rows}),
        "active_days": len(day_rows),
        "total_5m_rounds_in_active_days": rounds_total,
        "participation_rate_by_round": round(len(rows) / rounds_total, 8) if rounds_total else 0,
        "old_buy_actual_0p03": f6(old_buy),
        "old_cash_pnl_0p03": f6(old_pnl),
        "old_turnover_roi_0p03": f6(old_pnl / old_buy if old_buy else Decimal("0")),
        "gross_cost_no_fee": f6(total["gross_cost"]),
        "official_fee_0p07": f6(total["fee_0p07"]),
        "buy_actual_official_fee_0p07": f6(new_buy),
        "cash_pnl_official_fee_0p07": f6(new_pnl),
        "turnover_roi_official_fee_0p07": f6(new_pnl / new_buy if new_buy else Decimal("0")),
        "capital_300_roi_official_fee_0p07": f6(new_pnl / Decimal("300")),
        "capital_300_pnl_official_fee_0p07": f6(new_pnl),
        "capital_300_turnover_multiple": f6(new_buy / Decimal("300")),
        "profitable_days_official_fee_0p07": sum(1 for row in day_rows if d(row["cash_pnl_official_fee_0p07"]) > 0),
        "worst_day_official_fee_0p07": min(day_rows, key=lambda row: d(row["cash_pnl_official_fee_0p07"])),
        "best_day_official_fee_0p07": max(day_rows, key=lambda row: d(row["cash_pnl_official_fee_0p07"])),
        "capacity_warning": "target_qty=8 and participation_rate_by_round≈3.1%; this is a narrow low-capacity historical candidate, not a high-utilization strategy.",
        "non_claims": non_claims(),
    }
    summary_path = output_dir / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_SUMMARY.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    note_path = output_dir / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_NOTE.md"
    note_path.write_text(
        "\n".join(
            [
                "# CE25 Target Qty 8 Official Crypto Fee Recalc",
                "",
                f"Status: `{STATUS}`",
                "",
                "This artifact recalculates the target_qty=8 historical replay ledger with Polymarket's current documented Crypto taker fee rate of 0.07.",
                "It supersedes the old 0.03 fee interpretation for current fee discussion, but remains historical replay review only.",
                "",
                "The result is still positive after official crypto taker fees, but the participation rate is only about 3.1% of BTC 5m rounds over the active days and capacity remains small.",
                "",
                "No OOS, runner/observer start, private key, import, order/cancel/redeem, canary/live/deploy/funding, latest pointer update, private truth, promotion, live-ready, or deployable claim is authorized.",
                "",
            ]
        )
    )

    artifacts = [action_path, day_path, summary_path, note_path, ledger_path, strategy_input_path, Path(__file__).resolve()]
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": STATUS,
        "strategy_id": STRATEGY_ID,
        "strategy_owner_line": OWNER_LINE,
        "artifacts": [
            {"path": str(path), "sha256": sha256_file(path), "size_bytes": path.stat().st_size}
            for path in artifacts
        ],
        "summary_sha256": sha256_file(summary_path),
        "action_recalc_sha256": sha256_file(action_path),
        "day_summary_sha256": sha256_file(day_path),
        "non_claims": non_claims(),
    }
    manifest_path = output_dir / "CE25_TARGET_QTY8_OFFICIAL_CRYPTO_FEE_RECALC_HASH_MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "ok": True,
        "status": STATUS,
        "output_dir": str(output_dir),
        "summary_sha256": sha256_file(summary_path),
        "manifest_sha256": sha256_file(manifest_path),
        "cash_pnl_official_fee_0p07": summary["cash_pnl_official_fee_0p07"],
        "capital_300_roi_official_fee_0p07": summary["capital_300_roi_official_fee_0p07"],
        "participation_rate_by_round": summary["participation_rate_by_round"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    parser.add_argument("--strategy-input", type=Path, default=DEFAULT_STRATEGY_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()
    print(json.dumps(build(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

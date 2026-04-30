import sys
import unittest
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "legacy" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from trade_analysis import (
    calculate_position_series,
    calculate_resolution_pnl,
    calculate_summary,
    calculate_table_metrics,
    calculate_trade_summary,
    infer_resolved_side_from_trades,
    parse_trades,
)


class LegacyTradeAnalysisTests(unittest.TestCase):
    def test_parse_trades_normalizes_yes_no_outcomes(self) -> None:
        parsed = parse_trades(
            [
                {"side": "BUY", "outcome": "Yes", "size": 1, "price": 0.42, "timestamp": 1_777_400_000_000},
                {"side": "SELL", "outcome": "No", "size": 2, "price": 0.58, "timestamp": 1_777_400_001},
            ]
        )

        self.assertEqual(parsed[0]["side"], "Up")
        self.assertEqual(parsed[0]["type"], "Buy")
        self.assertEqual(parsed[0]["timestamp"], 1_777_400_000)
        self.assertRegex(parsed[0]["time_label"], r" E[DS]T$")
        self.assertRegex(parsed[0]["time_axis_label"], r" E[DS]T$")
        self.assertEqual(parsed[1]["side"], "Down")
        self.assertEqual(parsed[1]["type"], "Sell")

    def test_buy_sell_accounting_uses_net_spent_and_avg_cost_basis(self) -> None:
        parsed = parse_trades(
            [
                {"side": "BUY", "outcome": "Yes", "size": 10, "price": 0.40, "timestamp": 1000},
                {"side": "SELL", "outcome": "Yes", "size": 4, "price": 0.60, "timestamp": 1001},
                {"side": "BUY", "outcome": "No", "size": 6, "price": 0.30, "timestamp": 1002},
                {"side": "SELL", "outcome": "No", "size": 1, "price": 0.10, "timestamp": 1003},
            ]
        )

        summary = calculate_trade_summary(parsed)
        self.assertAlmostEqual(summary["yes_shares"], 6.0)
        self.assertAlmostEqual(summary["yes_net_spent"], 1.6)
        self.assertAlmostEqual(summary["yes_avg_cost"], 0.40)
        self.assertAlmostEqual(summary["no_shares"], 5.0)
        self.assertAlmostEqual(summary["no_net_spent"], 1.7)
        self.assertAlmostEqual(summary["no_avg_cost"], 0.30)
        self.assertAlmostEqual(summary["total_net_spent"], 3.3)
        self.assertAlmostEqual(summary["realized_pnl"], 0.6)
        self.assertAlmostEqual(summary["locked_profit"], 1.7)

        resolution = calculate_resolution_pnl(parsed, "YES")
        self.assertAlmostEqual(resolution["pnl"], 2.7)
        self.assertAlmostEqual(resolution["if_no_wins_pnl"], 1.7)

        table_summary = calculate_summary(calculate_table_metrics(parsed))
        self.assertAlmostEqual(table_summary["total_spent"], 3.3)
        self.assertAlmostEqual(table_summary["locked_profit"], 1.7)
        self.assertAlmostEqual(table_summary["realized_pnl"], 0.6)

    def test_position_series_includes_cumulative_sells(self) -> None:
        parsed = parse_trades(
            [
                {"side": "BUY", "outcome": "Up", "size": 3, "price": 0.2, "timestamp": 1},
                {"side": "SELL", "outcome": "Up", "size": 1, "price": 0.5, "timestamp": 2},
            ]
        )

        series = calculate_position_series(parsed)
        self.assertEqual(series["yes_buy_shares"], [3.0, 3.0])
        self.assertEqual(series["yes_sell_shares"], [0.0, 1.0])
        self.assertEqual(series["yes_shares"], [3.0, 2.0])
        self.assertAlmostEqual(series["yes_net_spent"][-1], 0.1)

    def test_resolution_inference_requires_explicit_outcome(self) -> None:
        inferred, latest = infer_resolved_side_from_trades([{"side": "BUY", "size": 1, "price": 0.8, "timestamp": 1}])

        self.assertIsNone(inferred)
        self.assertIsNotNone(latest)


if __name__ == "__main__":
    unittest.main()

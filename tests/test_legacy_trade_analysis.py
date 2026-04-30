import sys
import unittest
from pathlib import Path
from unittest.mock import patch


TOOLS_DIR = Path(__file__).resolve().parents[1] / "legacy" / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from trade_analysis import (
    calculate_position_series,
    calculate_resolution_pnl,
    calculate_summary,
    calculate_table_metrics,
    calculate_trade_summary,
    extract_market_identifier,
    fetch_trades_detailed,
    infer_resolved_side_from_trades,
    normalize_clob_user_trades,
    parse_trades,
    resolve_market_identifier,
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

    def test_table_metrics_list_only_reported_trade_side(self) -> None:
        parsed = parse_trades(
            [
                {"side": "BUY", "outcome": "Up", "size": 5.52, "price": 0.99, "timestamp": 1000},
                {"side": "SELL", "outcome": "No", "size": 2, "price": 0.25, "timestamp": 1001},
            ]
        )

        rows = calculate_table_metrics(parsed)
        self.assertEqual(rows[0]["yes_trade"], "BUY 5.52 @ 99.00c")
        self.assertEqual(rows[0]["no_trade"], "")
        self.assertEqual(rows[1]["no_trade"], "SELL 2.00 @ 25.00c")
        self.assertEqual(rows[1]["yes_trade"], "")

    def test_clob_user_trades_use_maker_order_side_for_maker_fill(self) -> None:
        user = "0x45bc74efa620b45c02308acaecdff1f7c06f978b"
        normalized = normalize_clob_user_trades(
            [
                {
                    "id": "trade-1",
                    "market": "0x" + "a" * 64,
                    "asset_id": "yes-token",
                    "side": "BUY",
                    "outcome": "YES",
                    "size": "10",
                    "price": "0.84",
                    "match_time": "1777480378",
                    "transaction_hash": "0xabc",
                    "trader_side": "MAKER",
                    "maker_orders": [
                        {
                            "order_id": "order-1",
                            "maker_address": user,
                            "asset_id": "no-token",
                            "side": "SELL",
                            "outcome": "NO",
                            "matched_amount": "10",
                            "price": "0.16",
                        }
                    ],
                }
            ],
            user_address=user,
        )

        self.assertEqual(len(normalized), 1)
        self.assertEqual(normalized[0]["side"], "SELL")
        self.assertEqual(normalized[0]["outcome"], "NO")
        parsed = parse_trades(normalized)
        self.assertEqual(parsed[0]["type"], "Sell")
        self.assertEqual(parsed[0]["side"], "Down")
        self.assertAlmostEqual(parsed[0]["price"], 16.0)

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

    def test_extract_market_identifier_accepts_polymarket_urls(self) -> None:
        self.assertEqual(
            extract_market_identifier("https://polymarket.com/event/btc-updown-5m-1777439400?tid=123"),
            "btc-updown-5m-1777439400",
        )
        self.assertEqual(
            extract_market_identifier("https://polymarket.com/event/x?conditionId=0x" + "a" * 64),
            "0x" + "a" * 64,
        )

    def test_resolve_market_identifier_accepts_condition_id_without_network(self) -> None:
        condition_id = "0x" + "b" * 64
        with patch("trade_analysis.requests.get") as get:
            event, market, identifier = resolve_market_identifier(condition_id)

        get.assert_not_called()
        self.assertIsNone(event)
        self.assertEqual(identifier, condition_id)
        self.assertEqual(market["conditionId"], condition_id)

    def test_resolve_market_identifier_fetches_exact_event_slug(self) -> None:
        class FakeResponse:
            def raise_for_status(self) -> None:
                return None

            def json(self):
                return [
                    {
                        "slug": "btc-updown-5m-1777439400",
                        "title": "Bitcoin Up or Down",
                        "markets": [
                            {
                                "slug": "btc-updown-5m-1777439400",
                                "question": "Bitcoin Up or Down - April 29, 1:10AM-1:15AM ET",
                                "conditionId": "0x" + "c" * 64,
                            }
                        ],
                    }
                ]

        with patch("trade_analysis.requests.get", return_value=FakeResponse()) as get:
            event, market, identifier = resolve_market_identifier("btc-updown-5m-1777439400")

        get.assert_called_once()
        self.assertEqual(identifier, "btc-updown-5m-1777439400")
        self.assertEqual(event["slug"], "btc-updown-5m-1777439400")
        self.assertEqual(market["conditionId"], "0x" + "c" * 64)

    def test_fetch_trades_detailed_public_source_labels_public_view(self) -> None:
        public_rows = [
            {
                "side": "SELL",
                "outcome": "No",
                "size": 2,
                "price": 0.25,
                "timestamp": 1001,
                "source": "data_api_public_trades",
            }
        ]
        with patch("trade_analysis.fetch_public_trades", return_value=public_rows) as public_fetch:
            result = fetch_trades_detailed("0x" + "d" * 64, "0xuser", source="public")

        public_fetch.assert_called_once()
        self.assertEqual(result.meta["data_source"], "public_data_api")
        self.assertEqual(result.meta["view_mode"], "public_canonical_view")
        self.assertEqual(result.meta["trade_count"], 1)
        self.assertEqual(result.trades[0]["source"], "data_api_public_trades")
        self.assertIn("Public Data API", result.meta["warnings"][0])

    def test_fetch_trades_detailed_authenticated_source_does_not_fallback_to_public(self) -> None:
        with patch("trade_analysis._load_env", return_value={}), patch(
            "trade_analysis.fetch_public_trades"
        ) as public_fetch:
            result = fetch_trades_detailed("0x" + "e" * 64, "0xuser", source="authenticated")

        public_fetch.assert_not_called()
        self.assertEqual(result.trades, [])
        self.assertEqual(result.meta["requested_source"], "authenticated")
        self.assertEqual(result.meta["fallback_reason"], "CLOB auth not configured")


if __name__ == "__main__":
    unittest.main()

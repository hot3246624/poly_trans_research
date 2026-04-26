import unittest

from completion_first_data.capture.meta import MarketMetaRecord
from completion_first_data.capture.websocket_sidecar import (
    MarketSelectionState,
    build_asset_maps,
    build_market_subscription_message,
    normalize_market_ws_message,
    select_markets_by_prefix,
)


def _rec(condition_id: str, slug: str, start_ms: int, end_ms: int, yes_token: str, no_token: str) -> MarketMetaRecord:
    return MarketMetaRecord(
        condition_id=condition_id,
        slug=slug,
        symbol="BTC",
        interval_sec=300,
        start_ms=start_ms,
        end_ms=end_ms,
        yes_token_id=yes_token,
        no_token_id=no_token,
        tick_size=0.01,
    )


class SidecarSelectionTests(unittest.TestCase):
    def test_select_markets_max1_prefers_active_round(self) -> None:
        now_ms = 1_000_000
        markets = [
            _rec("cond_past", "btc-updown-5m-10", now_ms - 20_000, now_ms - 10_000, "11", "12"),
            _rec("cond_active", "btc-updown-5m-20", now_ms - 1_000, now_ms + 10_000, "21", "22"),
            _rec("cond_future", "btc-updown-5m-30", now_ms + 20_000, now_ms + 30_000, "31", "32"),
        ]

        selected = select_markets_by_prefix(
            markets,
            ["btc-updown-5m"],
            max_markets_per_prefix=1,
            now_ms=now_ms,
        )
        self.assertEqual([m.condition_id for m in selected], ["cond_active"])

    def test_build_market_subscription_message_official_shape(self) -> None:
        markets = [_rec("cond1", "btc-updown-5m-1", 1000, 1300, "100", "200")]
        msg = build_market_subscription_message(markets)
        self.assertIsNotNone(msg)
        assert msg is not None

        self.assertEqual(msg["type"], "market")
        self.assertEqual(msg["operation"], "subscribe")
        self.assertEqual(msg["markets"], [])
        self.assertEqual(msg["assets_ids"], ["100", "200"])
        self.assertEqual(msg["asset_ids"], ["100", "200"])
        self.assertEqual(msg["initial_dump"], True)

    def test_selection_state_revision_changes_on_token_rollover(self) -> None:
        state = MarketSelectionState()
        first = [_rec("cond1", "btc-updown-5m-1", 1000, 1300, "100", "200")]
        second = [_rec("cond1", "btc-updown-5m-2", 1300, 1600, "101", "201")]

        self.assertTrue(state.update_from_markets(first))
        snap1 = state.snapshot()
        self.assertEqual(snap1.revision, 1)

        self.assertFalse(state.update_from_markets(first))
        self.assertEqual(state.snapshot().revision, 1)

        self.assertTrue(state.update_from_markets(second))
        self.assertEqual(state.snapshot().revision, 2)


class SidecarNormalizationTests(unittest.TestCase):
    def test_book_snapshot_and_trade_normalization(self) -> None:
        markets = [_rec("cond1", "btc-updown-5m-1", 1000, 1300, "yes_tok", "no_tok")]
        asset_to_condition, asset_to_side = build_asset_maps(markets)
        assemblers = {}
        allowed_events = {"book", "price_change", "best_bid_ask", "last_trade_price"}

        yes_book = {
            "event_type": "book",
            "asset_id": "yes_tok",
            "timestamp": "1700000001000",
            "bids": [{"price": "0.41", "size": "10"}],
            "asks": [{"price": "0.42", "size": "12"}],
        }
        no_book = {
            "event_type": "book",
            "asset_id": "no_tok",
            "timestamp": "1700000001001",
            "bids": [{"price": "0.58", "size": "9"}],
            "asks": [{"price": "0.59", "size": "11"}],
        }

        # first side only -> not enough for full 4-price book
        rows1 = normalize_market_ws_message(
            yes_book,
            allowed_events=allowed_events,
            asset_to_condition_id=asset_to_condition,
            asset_to_market_side=asset_to_side,
            assemblers=assemblers,
        )
        self.assertEqual(rows1, [])

        # second side arrives -> full L1 emitted
        rows2 = normalize_market_ws_message(
            no_book,
            allowed_events=allowed_events,
            asset_to_condition_id=asset_to_condition,
            asset_to_market_side=asset_to_side,
            assemblers=assemblers,
        )
        self.assertEqual(len(rows2), 1)
        channel, payload, condition_id = rows2[0]
        self.assertEqual(channel, "book")
        self.assertEqual(condition_id, "cond1")
        self.assertEqual(payload["yes_bid_px"], 0.41)
        self.assertEqual(payload["no_bid_px"], 0.58)

        trade = {
            "event_type": "last_trade_price",
            "asset_id": "yes_tok",
            "timestamp": "1700000002000",
            "price": "0.41",
            "size": "3.5",
            "side": "BUY",
            "transaction_hash": "0xabc",
        }
        trows = normalize_market_ws_message(
            trade,
            allowed_events=allowed_events,
            asset_to_condition_id=asset_to_condition,
            asset_to_market_side=asset_to_side,
            assemblers=assemblers,
        )
        self.assertEqual(len(trows), 1)
        tchannel, tpayload, tcond = trows[0]
        self.assertEqual(tchannel, "last_trade_price")
        self.assertEqual(tcond, "cond1")
        self.assertEqual(tpayload["market_side"], "YES")
        self.assertEqual(tpayload["taker_side"], "BUY")
        self.assertEqual(tpayload["trade_id"], "0xabc")
        self.assertEqual(tpayload["price"], 0.41)
        self.assertEqual(tpayload["size"], 3.5)


if __name__ == "__main__":
    unittest.main()

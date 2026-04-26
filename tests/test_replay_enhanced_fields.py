import unittest

from completion_first_data.capture.envelope import RawEnvelope
from completion_first_data.replay.normalize import normalize_book_row, normalize_md_trade


class ReplayEnhancedFieldTests(unittest.TestCase):
    def test_normalize_md_trade_parses_taker_side_and_raw_json(self) -> None:
        env = RawEnvelope(
            recv_unix_ms=1777174100000,
            recv_monotonic_ns=1,
            capture_seq=1,
            source="market_ws",
            channel="last_trade_price",
            condition_id="0xcond",
            payload_json={
                "condition_id": "0xcond",
                "trade_ts_ms": 1777174099000,
                "price": 0.45,
                "size": 5.0,
                "side": "SELL",
                "maker_address": "0xmaker",
                "taker_address": "0xtaker",
            },
        )
        rec = normalize_md_trade(env)
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec["taker_side"], "SELL")
        self.assertEqual(rec["maker_address"], "0xmaker")
        self.assertEqual(rec["taker_address"], "0xtaker")
        self.assertTrue(isinstance(rec["raw_json"], str) and rec["raw_json"])

    def test_normalize_book_row_carries_raw_json(self) -> None:
        env = RawEnvelope(
            recv_unix_ms=1777174100000,
            recv_monotonic_ns=2,
            capture_seq=2,
            source="market_ws",
            channel="book",
            condition_id="0xcond",
            payload_json={
                "condition_id": "0xcond",
                "yes_bid_px": 0.51,
                "yes_ask_px": 0.52,
                "no_bid_px": 0.48,
                "no_ask_px": 0.49,
                "yes_bid_sz": 10.0,
                "yes_ask_sz": 11.0,
                "no_bid_sz": 12.0,
                "no_ask_sz": 13.0,
                "raw_json": {"event_type": "book"},
            },
        )
        rec = normalize_book_row(env)
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertTrue(isinstance(rec["raw_json"], str) and rec["raw_json"])


if __name__ == "__main__":
    unittest.main()

import unittest

from completion_first_data.replay.normalize import as_int, normalize_market_meta_payload


class ReplayNormalizeTimeUnitTests(unittest.TestCase):
    def test_as_int_timestamp_units(self) -> None:
        self.assertEqual(as_int(1_777_171_800), 1_777_171_800_000)  # seconds
        self.assertEqual(as_int(1_777_171_800_000), 1_777_171_800_000)  # milliseconds
        self.assertEqual(as_int(1_777_171_800_000_000), 1_777_171_800_000)  # microseconds
        self.assertEqual(as_int(1_777_171_800_000_000_000), 1_777_171_800_000)  # nanoseconds

    def test_normalize_market_meta_payload_keeps_ms_window(self) -> None:
        rec = normalize_market_meta_payload(
            {
                "condition_id": "0xcond",
                "slug": "btc-updown-5m-1777171800000",
                "symbol": "BTC",
                "interval_sec": 300,
                "start_ms": 1_777_171_800_000,
                "end_ms": 1_777_172_100_000,
            }
        )
        self.assertIsNotNone(rec)
        assert rec is not None
        self.assertEqual(rec["start_ms"], 1_777_171_800_000)
        self.assertEqual(rec["end_ms"], 1_777_172_100_000)
        self.assertEqual(rec["interval_sec"], 300)


if __name__ == "__main__":
    unittest.main()

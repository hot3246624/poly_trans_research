import unittest

from completion_first_data.capture.meta import normalize_market_meta


class MetaNormalizeTests(unittest.TestCase):
    def test_normalize_market_meta_uses_slug_window_for_5m(self) -> None:
        market = {
            "conditionId": "0xcond",
            "slug": "btc-updown-5m-1766162100",
            # Gamma may return event-level windows here (~24h)
            "startDate": "2025-12-18T16:43:11Z",
            "endDate": "2025-12-19T16:40:00Z",
            "clobTokenIds": '["100", "200"]',
            "question": "Bitcoin Up or Down - December 19, 11:35AM-11:40AM ET",
            "orderPriceMinTickSize": "0.01",
        }

        rec = normalize_market_meta(market)
        self.assertIsNotNone(rec)
        assert rec is not None

        self.assertEqual(rec.condition_id, "0xcond")
        self.assertEqual(rec.yes_token_id, "100")
        self.assertEqual(rec.no_token_id, "200")
        self.assertEqual(rec.start_ms, 1766162100 * 1000)
        self.assertEqual(rec.end_ms, rec.start_ms + 300_000)
        self.assertEqual(rec.interval_sec, 300)
        self.assertEqual(rec.symbol, "BTC")

    def test_normalize_market_meta_accepts_ms_slug_timestamp(self) -> None:
        market = {
            "conditionId": "0xcond_ms",
            "slug": "btc-updown-5m-1777171500000",
            # Intentionally invalid 5m window from Gamma fields to force slug fallback.
            "startDate": "2026-04-26T00:00:00Z",
            "endDate": "2026-04-27T00:00:00Z",
            "clobTokenIds": '["101", "201"]',
            "question": "Bitcoin Up or Down - April 26, 08:45AM-08:50AM ET",
        }

        rec = normalize_market_meta(market)
        self.assertIsNotNone(rec)
        assert rec is not None

        self.assertEqual(rec.start_ms, 1777171500000)
        self.assertEqual(rec.end_ms, 1777171800000)
        self.assertEqual(rec.interval_sec, 300)


if __name__ == "__main__":
    unittest.main()

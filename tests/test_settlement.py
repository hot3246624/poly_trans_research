import unittest
from unittest.mock import Mock

from completion_first_data.capture.settlement import fetch_condition_settlement


class SettlementFetchTests(unittest.TestCase):
    def test_fetch_condition_settlement_uses_slug_and_closed_market_payload(self) -> None:
        session = Mock()
        response = Mock()
        response.json.return_value = [
            {
                "conditionId": "0xcond",
                "slug": "btc-updown-5m-123",
                "closed": True,
                "endDate": "2026-04-27T00:05:00Z",
                "outcomes": '["Up","Down"]',
                "outcomePrices": '["1","0"]',
                "clobTokenIds": '["yes_token","no_token"]',
            }
        ]
        response.raise_for_status.return_value = None
        session.get.return_value = response

        record = fetch_condition_settlement("0xcond", market_slug="btc-updown-5m-123", session=session)
        self.assertIsNotNone(record)
        assert record is not None

        self.assertEqual(record["condition_id"], "0xcond")
        self.assertEqual(record["market_slug"], "btc-updown-5m-123")
        self.assertEqual(record["official_outcome"], "YES")
        self.assertEqual(record["winner_token_id"], "yes_token")
        self.assertEqual(record["resolution_source"], "gamma_api")

        _, kwargs = session.get.call_args
        self.assertEqual(kwargs["params"], {"slug": "btc-updown-5m-123", "closed": "true"})

    def test_fetch_condition_settlement_rejects_condition_mismatch(self) -> None:
        session = Mock()
        response = Mock()
        response.json.return_value = [
            {
                "conditionId": "0xother",
                "slug": "btc-updown-5m-123",
                "closed": True,
                "outcomes": '["Up","Down"]',
                "outcomePrices": '["1","0"]',
                "clobTokenIds": '["yes_token","no_token"]',
            }
        ]
        response.raise_for_status.return_value = None
        session.get.return_value = response

        record = fetch_condition_settlement("0xcond", market_slug="btc-updown-5m-123", session=session)
        self.assertIsNone(record)


if __name__ == "__main__":
    unittest.main()

import unittest

from completion_first_data.capture.settlement import _winner_from_tokens


class SettlementTests(unittest.TestCase):
    def test_winner_from_tokens_yes(self) -> None:
        outcome, token_id = _winner_from_tokens(
            [
                {"token_id": "1", "outcome": "Up", "winner": True},
                {"token_id": "2", "outcome": "Down", "winner": False},
            ]
        )
        self.assertEqual(outcome, "YES")
        self.assertEqual(token_id, "1")

    def test_winner_from_tokens_no(self) -> None:
        outcome, token_id = _winner_from_tokens(
            [
                {"token_id": "1", "outcome": "Up", "winner": False},
                {"token_id": "2", "outcome": "Down", "winner": True},
            ]
        )
        self.assertEqual(outcome, "NO")
        self.assertEqual(token_id, "2")


if __name__ == "__main__":
    unittest.main()

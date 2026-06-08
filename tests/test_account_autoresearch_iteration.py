import csv
import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_account_autoresearch_iteration.py"
spec = importlib.util.spec_from_file_location("account_autoresearch", SCRIPT)
assert spec is not None and spec.loader is not None
account_autoresearch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(account_autoresearch)


class AccountAutoresearchIterationTests(unittest.TestCase):
    def test_unpaired_markets_count_in_rollup_and_drag_proxy_bucket(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            profile = Path(tmp) / "ce25_market_sequence.csv"
            rows = [
                {
                    "asset": "BTC",
                    "tf": "5m",
                    "first_delta_bucket": "1-5m",
                    "last_delta_bucket": "last_60s",
                    "pair_delay_bucket": "<=5s",
                    "first_price_bucket": "20-35",
                    "first_side": "UP",
                    "buy_actual": "1000",
                    "cash_pnl": "100",
                    "pair_pnl": "100",
                    "residual_pnl_est": "0",
                    "paired_qty": "1000",
                    "buy_qty": "1000",
                    "resid_qty": "0",
                    "pair_cost": "0.9",
                    "fee": "0",
                },
                {
                    "asset": "BTC",
                    "tf": "5m",
                    "first_delta_bucket": "1-5m",
                    "last_delta_bucket": "last_60s",
                    "pair_delay_bucket": "<=5s",
                    "first_price_bucket": "20-35",
                    "first_side": "UP",
                    "buy_actual": "50",
                    "cash_pnl": "-25",
                    "pair_pnl": "0",
                    "residual_pnl_est": "-25",
                    "paired_qty": "0",
                    "buy_qty": "50",
                    "resid_qty": "50",
                    "pair_cost": "",
                    "fee": "0",
                },
                {
                    "asset": "ETH",
                    "tf": "5m",
                    "first_delta_bucket": "1-5m",
                    "last_delta_bucket": "last_60s",
                    "pair_delay_bucket": "one_sided",
                    "first_price_bucket": "50-65",
                    "first_side": "DOWN",
                    "buy_actual": "1000",
                    "cash_pnl": "10",
                    "pair_pnl": "0",
                    "residual_pnl_est": "10",
                    "paired_qty": "0",
                    "buy_qty": "1000",
                    "resid_qty": "1000",
                    "pair_cost": "",
                    "fee": "0",
                },
            ]
            with profile.open("w", newline="", encoding="utf-8") as fp:
                writer = csv.DictWriter(fp, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)

            loaded, _summary = account_autoresearch.read_profile(profile, "acct")
            rollup = account_autoresearch.account_rollups(loaded)[0]
            self.assertEqual(rollup["markets"], 3)
            self.assertAlmostEqual(rollup["buy_actual"], 2050.0)
            self.assertAlmostEqual(rollup["cash_pnl"], 85.0)

            scored = account_autoresearch.proxy_score(loaded, min_buy=1, min_markets=1)
            btc_bucket = next(row for row in scored if row["features"] == "asset" and row["value"] == "BTC")
            self.assertEqual(btc_bucket["markets"], 2)
            self.assertAlmostEqual(btc_bucket["buy_actual"], 1050.0)
            self.assertAlmostEqual(btc_bucket["cash_pnl"], 75.0)
            self.assertAlmostEqual(btc_bucket["resid_rate"], 50 / 1050)

            safe = account_autoresearch.safe_candidates(scored, min_buy=1, max_resid_rate=1.0, max_bad_pc_share=1.0)
            self.assertFalse(any(row["features"] == "asset" and row["value"] == "ETH" for row in safe))


if __name__ == "__main__":
    unittest.main()

import argparse
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from completion_first_data import cli
from completion_first_data.replay.schema import init_schema


class CliMarketOutcomeBackfillTests(unittest.TestCase):
    def test_backfill_market_outcomes_writes_winner_side_and_xuan_outcome_side(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            day = "2026-04-27"
            db = root / "replay" / day / "crypto_5m.sqlite"
            db.parent.mkdir(parents=True)
            conn = sqlite3.connect(db)
            try:
                init_schema(conn)
                conn.execute(
                    """
                    INSERT INTO market_meta (
                        condition_id, slug, symbol, interval_sec, start_ms, end_ms,
                        yes_token_id, no_token_id, tick_size, first_seen_ms, last_seen_ms
                    ) VALUES ('0xcond', 'btc-updown-5m-1777248000', 'BTC', 300, 1777248000000, 1777248300000,
                              'yes-token', 'no-token', 0.01, 1777248000000, 1777248300000)
                    """
                )
                conn.execute(
                    """
                    INSERT INTO xuan_trades (
                        user, poll_ts_ms, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                        condition_id, slug, outcome, side, price, size, source_quality, raw_json
                    ) VALUES ('0xuser', 1, 1, 1, 1, 1, '0xcond', 'btc-updown-5m-1777248000',
                              'Up', 'BUY', 0.51, 2.0, 'fixture', '{}')
                    """
                )
                conn.execute(
                    """
                    INSERT INTO xuan_activity (
                        user, poll_ts_ms, activity_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                        condition_id, slug, activity_type, outcome, side, source_quality, raw_json
                    ) VALUES ('0xuser', 1, 1, 1, 1, 1, '0xcond', 'btc-updown-5m-1777248000',
                              'TRADE', 'Down', 'SELL', 'fixture', '{}')
                    """
                )
                conn.commit()
            finally:
                conn.close()

            args = argparse.Namespace(
                days=day,
                symbols="BTC,ETH,SOL,XRP",
                trusted_start="2026-04-27T00:00:00Z",
                end="2026-04-28T00:00:00Z",
                dry_run=False,
                replay_root=str(root / "replay"),
                market_limit=0,
                timeout_sec=15.0,
                fetch_retries=3,
                sleep_sec=0.0,
                log_every=1,
                output=str(root / "report.json"),
            )

            def fake_fetch(condition_id, *, market_slug, session, timeout_sec):
                self.assertEqual(condition_id, "0xcond")
                self.assertEqual(market_slug, "btc-updown-5m-1777248000")
                return {
                    "condition_id": condition_id,
                    "official_outcome": "YES",
                    "winner_side": "YES",
                    "winner_token_id": "yes-token",
                    "settle_ms": 1777248300000,
                    "resolution_source": "gamma_api",
                    "raw_json": "{}",
                }

            with patch("completion_first_data.cli.fetch_condition_settlement", side_effect=fake_fetch):
                rc = cli.cmd_backfill_market_outcomes(args)

            self.assertEqual(rc, 0)
            conn = sqlite3.connect(db)
            try:
                settlement = conn.execute(
                    "SELECT official_outcome, winner_side, winner_token_id, resolution_source FROM settlement_records"
                ).fetchone()
                xuan_trade_side = conn.execute("SELECT outcome_side FROM xuan_trades").fetchone()[0]
                xuan_activity_side = conn.execute("SELECT outcome_side FROM xuan_activity").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(settlement, ("YES", "YES", "yes-token", "gamma_api"))
            self.assertEqual(xuan_trade_side, "YES")
            self.assertEqual(xuan_activity_side, "NO")


if __name__ == "__main__":
    unittest.main()

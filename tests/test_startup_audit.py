import sqlite3
import tempfile
import unittest
from pathlib import Path

from completion_first_data.quality.startup_audit import run_startup_audit
from completion_first_data.replay.schema import init_schema


class StartupAuditTests(unittest.TestCase):
    def test_startup_audit_passes_on_valid_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "replay.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                init_schema(conn)
                cur = conn.cursor()

                for i in range(12):
                    cid = f"0x{i:064x}"
                    start_ms = 1_777_171_200_000 + i * 300_000
                    end_ms = start_ms + 300_000
                    cur.execute(
                        """
                        INSERT INTO market_meta (
                            condition_id, slug, symbol, interval_sec, start_ms, end_ms,
                            yes_token_id, no_token_id, tick_size, first_seen_ms, last_seen_ms
                        ) VALUES (?, ?, 'BTC', 300, ?, ?, 'y', 'n', 0.01, ?, ?)
                        """,
                        (cid, f"btc-updown-5m-{start_ms//1000}", start_ms, end_ms, start_ms, end_ms),
                    )
                    if i == 0:
                        cur.execute(
                            """
                            INSERT INTO settlement_records
                            (condition_id, official_outcome, settle_ms, resolution_source, capture_seq)
                            VALUES (?, 'YES', ?, 'clob_market', 1)
                            """,
                            (cid, end_ms),
                        )

                for i in range(100):
                    cur.execute(
                        """
                        INSERT INTO md_trades (
                            condition_id, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            source_ts_ms, trade_id, market_side, taker_side, maker_address, taker_address,
                            price, size, source_quality, raw_json
                        ) VALUES ('0x1', 1000, 1010, 1, ?, 1000, ?, 'YES', ?, NULL, NULL, 0.5, 1.0, 'ws', '{}')
                        """,
                        (i + 1, f"t{i}", "BUY" if i < 97 else None),
                    )

                cur.execute(
                    """
                    INSERT INTO md_book_l1 (
                        condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                        yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                        yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz,
                        source_kind, raw_json
                    ) VALUES ('0x1', 1000, 1, 1, 1000, 0.5, 0.6, 0.4, 0.5, 10, 11, 12, 13, 'market_ws', '{}')
                    """
                )

                for i in range(12):
                    poll_ts = 1_777_171_200_000 + i * 300_000
                    cur.execute(
                        """
                        INSERT INTO xuan_poll_log (
                            user, endpoint, poll_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            rows, max_ts_ms, ok, error
                        ) VALUES ('0xabc', 'trades', ?, ?, 1, ?, 1, ?, 1, NULL)
                        """,
                        (poll_ts, poll_ts, i + 1, poll_ts),
                    )
                    cur.execute(
                        """
                        INSERT INTO xuan_poll_log (
                            user, endpoint, poll_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                            rows, max_ts_ms, ok, error
                        ) VALUES ('0xabc', 'activity', ?, ?, 1, ?, 1, ?, 1, NULL)
                        """,
                        (poll_ts, poll_ts, i + 100, poll_ts),
                    )

                conn.commit()
            finally:
                conn.close()

            report = run_startup_audit(db_path)
            self.assertTrue(report.all_passed)
            self.assertLessEqual(report.taker_side_null_ratio, 0.05)


if __name__ == "__main__":
    unittest.main()

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

    def test_startup_audit_can_require_user_truth(self) -> None:
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
                cur.execute(
                    """
                    INSERT INTO md_trades (
                        condition_id, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                        source_ts_ms, trade_id, market_side, taker_side, maker_address, taker_address,
                        price, size, source_quality, raw_json
                    ) VALUES ('0x1', 1000, 1010, 1, 1, 1000, 't1', 'YES', 'BUY', NULL, NULL, 0.5, 1.0, 'ws', '{}')
                    """
                )

                for i in range(12):
                    poll_ts = 1_777_171_200_000 + i * 300_000
                    for endpoint in ("trades", "activity"):
                        cur.execute(
                            """
                            INSERT INTO xuan_poll_log (
                                user, endpoint, poll_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                                rows, max_ts_ms, ok, error
                            ) VALUES ('0xabc', ?, ?, ?, 1, ?, 1, ?, 1, NULL)
                            """,
                            (endpoint, poll_ts, poll_ts, i + 1, poll_ts),
                        )

                cur.execute(
                    """
                    INSERT INTO own_order_events (
                        condition_id, recv_ms, recv_monotonic_ns, capture_seq, client_order_id, order_id,
                        event_type, side, direction, price, size, remaining, status, reason,
                        reject_kind, tx_hash, strategy_tag, round_id
                    ) VALUES ('0x1', 1000, 1, 1, NULL, 'order-1', 'placement', 'YES', 'BUY', 0.5, 2.0, 2.0, 'LIVE', NULL, NULL, NULL, NULL, NULL)
                    """
                )
                cur.execute(
                    """
                    INSERT INTO own_fill_events (
                        condition_id, asset_id, order_id, taker_order_id, trade_id, market_side,
                        direction, trader_side, price, size, fee_rate_bps, match_ts_ms,
                        recv_ms, recv_monotonic_ns, capture_seq, maker_address, tx_hash, raw_json
                    ) VALUES ('0x1', 'asset-1', 'order-1', 'taker-1', 'fill-1', 'YES', 'BUY', 'TAKER', 0.5, 2.0, 0.0, 1000, 1010, 1, 2, '0xmaker', '0xtx', '{}')
                    """
                )
                cur.execute(
                    """
                    INSERT INTO own_inventory_events (
                        condition_id, asset_id, outcome, size, avg_price, redeemable, mergeable,
                        source_kind, recv_ms, recv_monotonic_ns, capture_seq
                    ) VALUES
                    ('0x1', 'asset-1', 'YES', 2.0, 0.5, 0, 1, 'bootstrap', 1000, 1, 1),
                    ('0x1', 'asset-1', 'YES', 2.0, 0.5, 0, 1, 'reconcile', 2000, 2, 2)
                    """
                )
                cur.execute(
                    """
                    INSERT INTO user_ws_log (
                        recv_ms, recv_monotonic_ns, capture_seq, event_name, event_value, detail
                    ) VALUES (1000, 1, 1, 'auth_success', 'api_creds', NULL)
                    """
                )

                conn.commit()
            finally:
                conn.close()

            report = run_startup_audit(db_path, require_user_truth=True)
            self.assertTrue(report.all_passed)
            self.assertTrue(report.user_ws_auth_success)
            self.assertGreater(report.own_fill_rows, 0)


if __name__ == "__main__":
    unittest.main()

import sqlite3
import tempfile
import unittest
from pathlib import Path

from completion_first_data.capture.raw_store import RawCaptureStore
from completion_first_data.replay.builder import build_replay_for_day
from completion_first_data.utils.time import day_from_ms


class ReplayBuilderContractTests(unittest.TestCase):
    def test_builder_uses_standardized_channels_only(self) -> None:
        recv_ms = 1_760_000_000_000
        day = day_from_ms(recv_ms)
        condition_id = "0xcond"

        with tempfile.TemporaryDirectory() as tmp_raw, tempfile.TemporaryDirectory() as tmp_replay:
            store = RawCaptureStore(tmp_raw)

            store.write(
                source="meta",
                channel="market_meta",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={
                    "condition_id": condition_id,
                    "slug": "btc-updown-5m-1760000000",
                    "symbol": "BTC",
                    "interval_sec": 300,
                    "start_ms": recv_ms - 300_000,
                    "end_ms": recv_ms,
                    "yes_token_id": "yes",
                    "no_token_id": "no",
                    "tick_size": 0.01,
                },
            )

            # Optional debug raw text should not be parsed as md_book_l1.
            store.write(
                source="market_ws",
                channel="market_raw_text",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={"raw_text": "{\"event_type\":\"book\"}"},
            )

            # Legacy non-standard market trade channel should not be consumed.
            store.write(
                source="market_ws",
                channel="trade",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={
                    "condition_id": condition_id,
                    "market_side": "YES",
                    "price": 0.51,
                    "size": 2.0,
                    "trade_ts_ms": recv_ms,
                },
            )

            store.write(
                source="market_ws",
                channel="book",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={
                    "condition_id": condition_id,
                    "yes_bid_px": 0.51,
                    "yes_ask_px": 0.52,
                    "no_bid_px": 0.48,
                    "no_ask_px": 0.49,
                    "yes_bid_sz": 10.0,
                    "yes_ask_sz": 11.0,
                    "no_bid_sz": 9.0,
                    "no_ask_sz": 12.0,
                    "source_ts_ms": recv_ms,
                    "raw_market_side": "YES",
                    "raw_l2": {
                        "yes": {
                            "bids": [
                                {"price": "0.51", "size": "10"},
                                {"price": "0.50", "size": "20"},
                                {"price": "0.49", "size": "30"},
                            ],
                            "asks": [
                                {"price": "0.52", "size": "11"},
                                {"price": "0.53", "size": "21"},
                                {"price": "0.54", "size": "31"},
                            ],
                        },
                        "no": {
                            "bids": [{"price": "0.48", "size": "9"}],
                            "asks": [{"price": "0.49", "size": "12"}],
                        },
                    },
                    "raw_json": {
                        "event_type": "book",
                        "asset_id": "yes",
                        "bids": [
                            {"price": "0.51", "size": "10"},
                            {"price": "0.50", "size": "20"},
                            {"price": "0.49", "size": "30"},
                        ],
                        "asks": [
                            {"price": "0.52", "size": "11"},
                            {"price": "0.53", "size": "21"},
                            {"price": "0.54", "size": "31"},
                        ],
                    },
                },
            )

            store.write(
                source="market_ws",
                channel="book",
                condition_id=condition_id,
                recv_unix_ms=recv_ms + 1,
                payload_json={
                    "condition_id": condition_id,
                    "yes_bid_px": 0.51,
                    "yes_ask_px": 0.52,
                    "no_bid_px": 0.48,
                    "no_ask_px": 0.49,
                    "yes_bid_sz": 15.0,
                    "yes_ask_sz": 11.0,
                    "no_bid_sz": 9.0,
                    "no_ask_sz": 12.0,
                    "source_ts_ms": recv_ms + 1,
                    "raw_market_side": "YES",
                    "raw_l2": {
                        "yes": {
                            "bids": [
                                {"price": "0.51", "size": "10"},
                                {"price": "0.50", "size": "20"},
                                {"price": "0.49", "size": "30"},
                            ],
                            "asks": [
                                {"price": "0.52", "size": "11"},
                                {"price": "0.53", "size": "21"},
                                {"price": "0.54", "size": "31"},
                            ],
                        },
                        "no": {
                            "bids": [{"price": "0.48", "size": "9"}],
                            "asks": [{"price": "0.49", "size": "12"}],
                        },
                    },
                    "raw_json": {
                        "asset_id": "yes",
                        "price": "0.51",
                        "size": "15",
                        "side": "BUY",
                        "best_bid": "0.51",
                        "best_ask": "0.52",
                    },
                },
            )

            store.write(
                source="market_ws",
                channel="last_trade_price",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={
                    "condition_id": condition_id,
                    "market_side": "YES",
                    "price": 0.51,
                    "size": 3.0,
                    "trade_ts_ms": recv_ms,
                    "trade_id": "tx1",
                    "source_quality": "ws",
                },
            )

            build_replay_for_day(Path(tmp_raw), Path(tmp_replay), day)
            db_path = Path(tmp_replay) / day / "crypto_5m.sqlite"

            conn = sqlite3.connect(db_path)
            try:
                market_meta_rows = conn.execute("SELECT COUNT(*) FROM market_meta").fetchone()[0]
                md_book_rows = conn.execute("SELECT COUNT(*) FROM md_book_l1").fetchone()[0]
                md_book_l2_rows = conn.execute("SELECT COUNT(*) FROM md_book_l2").fetchone()[0]
                md_trades_rows = conn.execute("SELECT COUNT(*) FROM md_trades").fetchone()[0]
                l2 = conn.execute(
                    """
                    SELECT market_side, bid1_px, bid1_sz, bid2_px, bid2_sz, ask1_px, ask1_sz
                    FROM md_book_l2
                    ORDER BY id DESC
                    LIMIT 1
                    """
                ).fetchone()
                no_l2 = conn.execute(
                    """
                    SELECT market_side, bid1_px, bid1_sz, ask1_px, ask1_sz
                    FROM md_book_l2
                    WHERE market_side = 'NO'
                    ORDER BY id
                    LIMIT 1
                    """
                ).fetchone()
            finally:
                conn.close()

            self.assertEqual(market_meta_rows, 1)
            self.assertEqual(md_book_rows, 2)
            self.assertEqual(md_book_l2_rows, 3)
            self.assertEqual(md_trades_rows, 1)
            self.assertEqual(l2[0], "YES")
            self.assertEqual(l2[1], 0.51)
            self.assertEqual(l2[2], 15.0)
            self.assertEqual(l2[3], 0.5)
            self.assertEqual(l2[4], 20.0)
            self.assertEqual(l2[5], 0.52)
            self.assertEqual(l2[6], 11.0)
            self.assertEqual(no_l2[0], "NO")
            self.assertEqual(no_l2[1], 0.48)
            self.assertEqual(no_l2[2], 9.0)
            self.assertEqual(no_l2[3], 0.49)
            self.assertEqual(no_l2[4], 12.0)


if __name__ == "__main__":
    unittest.main()

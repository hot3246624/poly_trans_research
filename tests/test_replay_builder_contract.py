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
                md_trades_rows = conn.execute("SELECT COUNT(*) FROM md_trades").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(market_meta_rows, 1)
            self.assertEqual(md_book_rows, 1)
            self.assertEqual(md_trades_rows, 1)


if __name__ == "__main__":
    unittest.main()

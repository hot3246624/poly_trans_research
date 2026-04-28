import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from completion_first_data.capture.raw_store import RawCaptureStore
from completion_first_data.replay.builder import ReplayBuilder, build_replay_for_day
from completion_first_data.utils.time import day_from_ms


class ReplayBuildIdempotentTests(unittest.TestCase):
    def test_streaming_merge_preserves_capture_sequence_across_files(self) -> None:
        recv_ms = 1_760_000_000_000
        day = day_from_ms(recv_ms)
        condition_id = "0xcond"

        with tempfile.TemporaryDirectory() as tmp_raw, tempfile.TemporaryDirectory() as tmp_replay:
            store = RawCaptureStore(tmp_raw)
            store.write(
                source="market_ws",
                channel="book",
                condition_id=condition_id,
                recv_unix_ms=recv_ms,
                payload_json={
                    "condition_id": condition_id,
                    "yes_bid_px": 0.50,
                    "yes_ask_px": 0.51,
                    "no_bid_px": 0.49,
                    "no_ask_px": 0.50,
                    "yes_bid_sz": 10.0,
                    "yes_ask_sz": 11.0,
                    "no_bid_sz": 9.0,
                    "no_ask_sz": 12.0,
                    "source_ts_ms": recv_ms,
                },
            )
            store.write(
                source="meta",
                channel="market_meta",
                condition_id=condition_id,
                recv_unix_ms=recv_ms + 1,
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
            store.write(
                source="market_ws",
                channel="last_trade_price",
                condition_id=condition_id,
                recv_unix_ms=recv_ms + 2,
                payload_json={
                    "condition_id": condition_id,
                    "market_side": "YES",
                    "price": 0.51,
                    "size": 3.0,
                    "trade_ts_ms": recv_ms + 2,
                    "trade_id": "tx1",
                    "source_quality": "ws",
                },
            )
            store.write(
                source="market_ws",
                channel="book",
                condition_id=condition_id,
                recv_unix_ms=recv_ms + 3,
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
                    "source_ts_ms": recv_ms + 3,
                },
            )
            store.close()

            builder = ReplayBuilder(
                raw_day_root=Path(tmp_raw) / day,
                replay_db_path=Path(tmp_replay) / day / "crypto_5m.sqlite",
            )
            files = builder._source_files()
            seqs = [env.capture_seq for env in builder._iter_envelopes(files)]

            self.assertEqual(seqs, [1, 2, 3, 4])

    def test_rebuild_same_day_keeps_counts_stable(self) -> None:
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
            build_replay_for_day(Path(tmp_raw), Path(tmp_replay), day)

            db_path = Path(tmp_replay) / day / "crypto_5m.sqlite"
            conn = sqlite3.connect(db_path)
            try:
                meta_rows = conn.execute("SELECT COUNT(*) FROM market_meta").fetchone()[0]
                book_rows = conn.execute("SELECT COUNT(*) FROM md_book_l1").fetchone()[0]
                trade_rows = conn.execute("SELECT COUNT(*) FROM md_trades").fetchone()[0]
            finally:
                conn.close()

            self.assertEqual(meta_rows, 1)
            self.assertEqual(book_rows, 1)
            self.assertEqual(trade_rows, 1)

    def test_builder_emits_low_frequency_progress_logs(self) -> None:
        recv_ms = 1_760_000_000_000
        day = day_from_ms(recv_ms)
        condition_id = "0xcond"

        with tempfile.TemporaryDirectory() as tmp_raw, tempfile.TemporaryDirectory() as tmp_replay:
            store = RawCaptureStore(tmp_raw)
            for i in range(3):
                store.write(
                    source="market_ws",
                    channel="book",
                    condition_id=condition_id,
                    recv_unix_ms=recv_ms + i,
                    payload_json={
                        "condition_id": condition_id,
                        "yes_bid_px": 0.50,
                        "yes_ask_px": 0.51,
                        "no_bid_px": 0.49,
                        "no_ask_px": 0.50,
                        "yes_bid_sz": 10.0 + i,
                        "yes_ask_sz": 11.0,
                        "no_bid_sz": 9.0,
                        "no_ask_sz": 12.0,
                        "source_ts_ms": recv_ms + i,
                    },
                )
            store.close()

            builder = ReplayBuilder(
                raw_day_root=Path(tmp_raw) / day,
                replay_db_path=Path(tmp_replay) / day / "crypto_5m.sqlite",
            )
            with (
                patch("completion_first_data.replay.builder.PROGRESS_LOG_EVERY_RECORDS", 1),
                patch("completion_first_data.replay.builder.PROGRESS_LOG_MIN_INTERVAL_SEC", 0.0),
                self.assertLogs("completion_first_data.replay.builder", level="INFO") as captured,
            ):
                builder.build()

            joined = "\n".join(captured.output)
            self.assertIn("replay build started", joined)
            self.assertIn("replay build progress", joined)
            self.assertIn("replay build finished", joined)


if __name__ == "__main__":
    unittest.main()

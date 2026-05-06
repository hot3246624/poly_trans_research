import datetime as dt
import sqlite3
import tempfile
import unittest
from pathlib import Path

from completion_first_data.quality.replay_market_audit import AuditConfig, run_market_replay_audit
from completion_first_data.replay.schema import init_schema


DAY = "2026-04-27"


def _ms(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> int:
    return int(dt.datetime(year, month, day, hour, minute, tzinfo=dt.timezone.utc).timestamp() * 1000)


def _create_db(replay_root: Path, day: str, starts_ms: list[int]) -> Path:
    db_path = replay_root / day / "crypto_5m.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        init_schema(conn)
        cur = conn.cursor()
        capture_seq = 1
        for idx, start_ms in enumerate(starts_ms):
            end_ms = start_ms + 300_000
            cid = f"0x{idx + 1:064x}"
            cur.execute(
                """
                INSERT INTO market_meta (
                    condition_id, slug, symbol, interval_sec, start_ms, end_ms,
                    yes_token_id, no_token_id, tick_size, first_seen_ms, last_seen_ms
                ) VALUES (?, ?, 'BTC', 300, ?, ?, ?, ?, 0.01, ?, ?)
                """,
                (cid, f"btc-updown-5m-{start_ms // 1000}", start_ms, end_ms, f"yes-{idx}", f"no-{idx}", start_ms, end_ms),
            )
            cur.execute(
                """
                INSERT INTO md_book_l1 (
                    condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                    yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                    yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz,
                    source_kind, raw_json
                ) VALUES (?, ?, 1, ?, ?, 0.49, 0.51, 0.48, 0.50, 10, 11, 12, 13, 'market_ws', '{}')
                """,
                (cid, start_ms + 1_000, capture_seq, start_ms + 1_000),
            )
            cur.execute(
                """
                INSERT INTO md_book_l2 (
                    condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                    market_side, depth,
                    bid1_px, bid1_sz, bid2_px, bid2_sz, bid3_px, bid3_sz, bid4_px, bid4_sz, bid5_px, bid5_sz,
                    ask1_px, ask1_sz, ask2_px, ask2_sz, ask3_px, ask3_sz, ask4_px, ask4_sz, ask5_px, ask5_sz,
                    source_kind, raw_json
                ) VALUES (?, ?, 1, ?, ?, 'YES', 5,
                    0.49, 10, 0.48, 8, NULL, NULL, NULL, NULL, NULL, NULL,
                    0.51, 11, 0.52, 9, NULL, NULL, NULL, NULL, NULL, NULL,
                    'market_ws', '{}')
                """,
                (cid, start_ms + 1_000, capture_seq, start_ms + 1_000),
            )
            capture_seq += 1
            cur.execute(
                """
                INSERT INTO md_book_l1 (
                    condition_id, recv_ms, recv_monotonic_ns, capture_seq, source_ts_ms,
                    yes_bid_px, yes_ask_px, no_bid_px, no_ask_px,
                    yes_bid_sz, yes_ask_sz, no_bid_sz, no_ask_sz,
                    source_kind, raw_json
                ) VALUES (?, ?, 1, ?, ?, 0.50, 0.52, 0.47, 0.49, 10, 11, 12, 13, 'market_ws', '{}')
                """,
                (cid, end_ms - 1_000, capture_seq, end_ms - 1_000),
            )
            capture_seq += 1
            cur.execute(
                """
                INSERT INTO md_trades (
                    condition_id, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                    source_ts_ms, trade_id, market_side, taker_side, maker_address, taker_address,
                    price, size, source_quality, raw_json
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'BUY', NULL, NULL, 0.51, 2.0, 'ws', '{}')
                """,
                (cid, start_ms + 10_000, start_ms + 10_100, capture_seq, start_ms + 10_000, f"t-{idx}-yes", "YES"),
            )
            capture_seq += 1
            cur.execute(
                """
                INSERT INTO md_trades (
                    condition_id, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                    source_ts_ms, trade_id, market_side, taker_side, maker_address, taker_address,
                    price, size, source_quality, raw_json
                ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, 'SELL', NULL, NULL, 0.49, 1.0, 'ws', '{}')
                """,
                (cid, start_ms + 20_000, start_ms + 20_100, capture_seq, start_ms + 20_000, f"t-{idx}-no", "NO"),
            )
            capture_seq += 1
            cur.execute(
                """
                INSERT INTO settlement_records
                (condition_id, official_outcome, settle_ms, resolution_source, capture_seq)
                VALUES (?, 'YES', ?, 'fixture', ?)
                """,
                (cid, end_ms, capture_seq),
            )
            capture_seq += 1
        conn.commit()
    finally:
        conn.close()
    return db_path


class ReplayMarketAuditTests(unittest.TestCase):
    def test_market_audit_passes_public_only_fixture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            starts = [_ms(2026, 4, 27) + i * 300_000 for i in range(3)]
            _create_db(root / "replay", DAY, starts)

            report = run_market_replay_audit(
                AuditConfig(raw_root=root / "raw", replay_root=root / "replay", days=[DAY], min_db_bytes=0)
            )

            self.assertTrue(report["final_verdict"]["market_replay_trusted"])
            self.assertFalse(report["final_verdict"]["xuan_episode_ready"])
            self.assertFalse(report["final_verdict"]["own_execution_truth_ready"])
            self.assertEqual(report["xuan_truth_audit"], "N/A")
            self.assertEqual(report["own_truth_audit"], "N/A")
            self.assertEqual(report["days"][0]["market_side_audit"], "pass")
            self.assertEqual(report["days"][0]["md_book_l1"]["size_null_rates"]["yes_bid_sz"], 0.0)
            self.assertEqual(report["days"][0]["settlement_records"]["btc_settlement_coverage"], 1.0)
            self.assertEqual(report["days"][0]["settlement_records"]["settlement_coverage_ratio"], 1.0)

    def test_market_audit_fails_small_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db_path = root / "replay" / DAY / "crypto_5m.sqlite"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(b"stub")

            report = run_market_replay_audit(
                AuditConfig(raw_root=root / "raw", replay_root=root / "replay", days=[DAY], min_db_bytes=100)
            )

            self.assertFalse(report["final_verdict"]["market_replay_trusted"])
            self.assertEqual(report["days"][0]["market_side_audit"], "fail")

    def test_taker_side_null_rate_fails_above_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            starts = [_ms(2026, 4, 27) + i * 300_000 for i in range(3)]
            db_path = _create_db(root / "replay", DAY, starts)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute("UPDATE md_trades SET taker_side=NULL")
                conn.commit()
            finally:
                conn.close()

            report = run_market_replay_audit(
                AuditConfig(raw_root=root / "raw", replay_root=root / "replay", days=[DAY], min_db_bytes=0)
            )

            self.assertFalse(report["final_verdict"]["market_replay_trusted"])
            self.assertIn("taker_side_null_rate_too_high", report["days"][0]["failures"])

    def test_planned_outage_gap_is_not_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            starts = [_ms(2026, 4, 28, 10, 55), _ms(2026, 4, 28, 12, 0)]
            _create_db(root / "replay", "2026-04-28", starts)

            report = run_market_replay_audit(
                AuditConfig(raw_root=root / "raw", replay_root=root / "replay", days=["2026-04-28"], min_db_bytes=0)
            )

            self.assertTrue(report["final_verdict"]["market_replay_trusted"])
            self.assertEqual(report["days"][0]["btc_continuity"]["planned_gap_count"], 1)
            self.assertEqual(report["days"][0]["btc_continuity"]["nonplanned_gap_count"], 0)


if __name__ == "__main__":
    unittest.main()

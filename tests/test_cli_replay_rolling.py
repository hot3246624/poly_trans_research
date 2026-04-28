import argparse
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from completion_first_data import cli


class CliReplayRollingTests(unittest.TestCase):
    def test_rolling_days_uses_single_utc_snapshot(self) -> None:
        now = dt.datetime(2026, 4, 28, 23, 59, tzinfo=dt.timezone.utc)
        self.assertEqual(cli._rolling_days(24, now), ["2026-04-27", "2026-04-28"])

    def test_validate_replay_returns_missing_db_code(self) -> None:
        with tempfile.TemporaryDirectory() as replay_root:
            args = argparse.Namespace(
                replay_root=replay_root,
                day="2026-04-28",
                db_path=None,
                gap_threshold_ms=0,
                output=None,
            )
            self.assertEqual(cli.cmd_validate_replay(args), 3)

    @patch("completion_first_data.cli.validate_replay_db")
    @patch("completion_first_data.cli.build_replay_for_day")
    def test_build_replay_rolling_validates_latest_built_day(
        self,
        mock_build_replay_for_day,
        mock_validate_replay_db,
    ) -> None:
        with tempfile.TemporaryDirectory() as raw_root, tempfile.TemporaryDirectory() as replay_root:
            def _fake_build(_raw_root: Path, replay_path_root: Path, day: str) -> SimpleNamespace:
                db = replay_path_root / day / "crypto_5m.sqlite"
                db.parent.mkdir(parents=True, exist_ok=True)
                db.write_text("stub", encoding="utf-8")
                return SimpleNamespace(as_dict=lambda: {"day": day})

            mock_build_replay_for_day.side_effect = _fake_build
            mock_validate_replay_db.return_value = SimpleNamespace(
                as_dict=lambda: {"all_passed": True},
                all_passed=True,
            )

            args = argparse.Namespace(
                raw_root=raw_root,
                replay_root=replay_root,
                hours=24,
                validate_latest=True,
                gap_threshold_ms=12345,
            )

            with patch("completion_first_data.cli._rolling_days", return_value=["2026-04-27", "2026-04-28"]):
                rc = cli.cmd_build_replay_rolling(args)

            self.assertEqual(rc, 0)
            self.assertEqual(mock_build_replay_for_day.call_count, 2)
            mock_validate_replay_db.assert_called_once_with(
                Path(replay_root) / "2026-04-28" / "crypto_5m.sqlite",
                gap_threshold_ms=12345,
            )


if __name__ == "__main__":
    unittest.main()

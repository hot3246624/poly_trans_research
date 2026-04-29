import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from completion_first_data import cli


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params, timeout))
        return _FakeResponse(
            [
                {
                    "timestamp": 1777248010,
                    "conditionId": "0x1",
                    "id": "t1",
                    "transactionHash": "0xtx1",
                    "outcome": "YES",
                    "side": "BUY",
                    "price": 0.51,
                    "size": 2.0,
                },
                {
                    "timestamp": 1777248200,
                    "conditionId": "0x2",
                    "id": "t2",
                    "transactionHash": "0xtx2",
                    "outcome": "NO",
                    "side": "SELL",
                    "price": 0.49,
                    "size": 1.0,
                },
            ]
        )


class CliXuanBackfillTests(unittest.TestCase):
    def test_backfill_xuan_public_dry_run_does_not_write_raw(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = argparse.Namespace(
                user="0xcfb103c37c0234f524c632d964ed31f117b5f694",
                start="2026-04-27T00:00:00Z",
                end="2026-04-29T00:00:00Z",
                dry_run=True,
                raw_root=str(root / "raw"),
                page_limit=500,
                max_pages=1,
                timeout_sec=20,
                output=str(root / "xuan_dry_run.json"),
            )
            fake = _FakeSession()
            with patch("completion_first_data.cli.requests.Session", return_value=fake):
                rc = cli.cmd_backfill_xuan_public(args)

            self.assertEqual(rc, 0)
            self.assertFalse((root / "raw").exists())
            report = json.loads((root / "xuan_dry_run.json").read_text(encoding="utf-8"))
            self.assertTrue(report["dry_run"])
            self.assertTrue(report["can_cover_target_window"])
            self.assertEqual(report["trades"]["rows_in_window"], 2)
            self.assertEqual(report["activity"]["rows_in_window"], 2)


if __name__ == "__main__":
    unittest.main()

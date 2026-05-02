import unittest

from completion_first_data.capture.xuan_poller import _iter_data_api_rows


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        if not self.pages:
            return _Resp([])
        return _Resp(self.pages.pop(0))


class XuanPollerTests(unittest.TestCase):
    def test_incremental_poll_starts_from_latest_page_not_last_seen_before(self) -> None:
        session = _Session(
            [
                [
                    {"timestamp": 1777608200, "id": "new"},
                    {"timestamp": 1777608100, "id": "last_seen"},
                ]
            ]
        )

        rows = _iter_data_api_rows(
            session=session,
            url="https://example.invalid/trades",
            user="0xuser",
            last_seen_ts_ms=1777608100 * 1000,
            page_limit=500,
            max_pages=3,
            include_taker_only_false=True,
        )

        self.assertEqual([r["id"] for r in rows], ["new"])
        self.assertNotIn("before", session.calls[0])
        self.assertNotIn("offset", session.calls[0])

    def test_back_pages_use_before_cursor(self) -> None:
        session = _Session(
            [
                [{"timestamp": 1777608200, "id": "a"}],
                [{"timestamp": 1777608000, "id": "b"}],
            ]
        )

        rows = _iter_data_api_rows(
            session=session,
            url="https://example.invalid/trades",
            user="0xuser",
            last_seen_ts_ms=None,
            page_limit=1,
            max_pages=2,
        )

        self.assertEqual([r["id"] for r in rows], ["a", "b"])
        self.assertNotIn("before", session.calls[0])
        self.assertEqual(session.calls[1]["before"], 1777608199)
        self.assertNotIn("offset", session.calls[1])


if __name__ == "__main__":
    unittest.main()

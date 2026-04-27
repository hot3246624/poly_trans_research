import tempfile
import unittest
from pathlib import Path

from completion_first_data.capture.raw_store import RawCaptureStore
from completion_first_data.utils.io import iter_jsonl_gz


class RawStoreTests(unittest.TestCase):
    def test_raw_store_writes_readable_concatenated_gzip_members(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = RawCaptureStore(tmp)
            store.write(
                source="market_ws",
                channel="book",
                payload_json={"condition_id": "0x1", "price": 0.41},
            )
            store.write(
                source="market_ws",
                channel="book",
                payload_json={"condition_id": "0x1", "price": 0.42},
            )
            store.close()

            day_dirs = list(Path(tmp).glob("*/market_ws/book.jsonl.gz"))
            self.assertEqual(len(day_dirs), 1)
            rows = list(iter_jsonl_gz(day_dirs[0]))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["payload_json"]["price"], 0.41)
            self.assertEqual(rows[1]["payload_json"]["price"], 0.42)


if __name__ == "__main__":
    unittest.main()

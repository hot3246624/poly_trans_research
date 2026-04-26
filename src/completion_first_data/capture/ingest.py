"""Ingest ndjson files/stdin into Raw envelope files."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, IO, Optional

from .raw_store import RawCaptureStore


def _open_input(path: Optional[str]) -> IO[str]:
    if not path or path == "-":
        return sys.stdin
    return Path(path).open("r", encoding="utf-8")


def ingest_ndjson(
    raw_store: RawCaptureStore,
    *,
    input_path: Optional[str],
    source: Optional[str] = None,
    channel: Optional[str] = None,
    condition_id: Optional[str] = None,
) -> int:
    """Ingest NDJSON records.

    Accepted line formats:
    1) envelope-like:
       {"source":"market_ws","channel":"book","condition_id":"...","payload_json":{...}}
    2) payload-only (requires --source and --channel):
       { ...raw payload... }
    """
    count = 0
    fp = _open_input(input_path)
    close_needed = fp is not sys.stdin
    try:
        for line_no, line in enumerate(fp, start=1):
            txt = line.strip()
            if not txt:
                continue
            try:
                record = json.loads(txt)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at line {line_no}: {exc}") from exc

            if isinstance(record, dict) and "payload_json" in record:
                src = str(record.get("source") or source or "ingest")
                ch = str(record.get("channel") or channel or "unknown")
                cid = str(record.get("condition_id") or condition_id or "")
                payload = record.get("payload_json")
                if not isinstance(payload, dict):
                    payload = {"value": payload}
                raw_store.write(source=src, channel=ch, payload_json=payload, condition_id=cid)
            else:
                if not source or not channel:
                    raise ValueError(
                        "Payload-only NDJSON requires --source and --channel. "
                        f"Line {line_no} has no payload_json envelope."
                    )
                payload = record if isinstance(record, dict) else {"value": record}
                raw_store.write(source=source, channel=channel, payload_json=payload, condition_id=condition_id)
            count += 1
    finally:
        if close_needed:
            fp.close()
    return count

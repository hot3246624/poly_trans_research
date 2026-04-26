"""Raw capture store with monotonic capture sequence."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from .envelope import RawEnvelope, pick_condition_id
from ..utils.io import append_jsonl_gz, ensure_parent
from ..utils.time import day_from_ms, now_monotonic_ns, now_unix_ms


class CaptureSeqStore:
    """Persistent sequence allocator keyed by UTC day."""

    def __init__(self, raw_root: Path):
        self.raw_root = Path(raw_root)
        self._lock = threading.Lock()
        self._day_cache: Dict[str, int] = {}

    def _state_path(self, day: str) -> Path:
        return self.raw_root / day / ".capture_seq"

    def _load_day_seq(self, day: str) -> int:
        if day in self._day_cache:
            return self._day_cache[day]
        p = self._state_path(day)
        if p.exists():
            try:
                seq = int(p.read_text(encoding="utf-8").strip())
            except ValueError:
                seq = 0
        else:
            seq = 0
        self._day_cache[day] = seq
        return seq

    def next(self, day: str) -> int:
        with self._lock:
            seq = self._load_day_seq(day) + 1
            self._day_cache[day] = seq
            p = self._state_path(day)
            ensure_parent(p)
            p.write_text(str(seq), encoding="utf-8")
            return seq


class RawCaptureStore:
    """Write capture envelopes into day-partitioned gzip jsonl files."""

    def __init__(self, raw_root: str | Path):
        self.raw_root = Path(raw_root)
        self.seq_store = CaptureSeqStore(self.raw_root)

    def _target_path(self, day: str, source: str, channel: str) -> Path:
        return self.raw_root / day / source / f"{channel}.jsonl.gz"

    def write(
        self,
        *,
        source: str,
        channel: str,
        payload_json: Dict[str, Any],
        condition_id: Optional[str] = None,
        recv_unix_ms: Optional[int] = None,
    ) -> RawEnvelope:
        recv_ms = recv_unix_ms if recv_unix_ms is not None else now_unix_ms()
        day = day_from_ms(recv_ms)
        capture_seq = self.seq_store.next(day)
        condition = pick_condition_id(payload_json, fallback=condition_id)
        env = RawEnvelope(
            recv_unix_ms=recv_ms,
            recv_monotonic_ns=now_monotonic_ns(),
            capture_seq=capture_seq,
            source=source,
            channel=channel,
            condition_id=condition,
            payload_json=payload_json,
        )
        append_jsonl_gz(self._target_path(day, source, channel), env.as_dict())
        return env

    def write_raw_line(
        self,
        *,
        source: str,
        channel: str,
        line: str,
        condition_id: Optional[str] = None,
        recv_unix_ms: Optional[int] = None,
    ) -> RawEnvelope:
        payload = json.loads(line)
        return self.write(
            source=source,
            channel=channel,
            payload_json=payload,
            condition_id=condition_id,
            recv_unix_ms=recv_unix_ms,
        )

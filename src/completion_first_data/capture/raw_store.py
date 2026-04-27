"""Raw capture store with monotonic capture sequence."""

from __future__ import annotations

import gzip
import json
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional

from .envelope import RawEnvelope, pick_condition_id
from ..utils.io import ensure_parent
from ..utils.time import day_from_ms, now_monotonic_ns, now_unix_ms


class CaptureSeqStore:
    """Persistent sequence allocator keyed by UTC day."""

    def __init__(self, raw_root: Path):
        self.raw_root = Path(raw_root)
        self._lock = threading.Lock()
        self._day_cache: Dict[str, int] = {}
        self._state_fps: Dict[str, BinaryIO] = {}

    def _state_path(self, day: str) -> Path:
        return self.raw_root / day / ".capture_seq"

    def _state_fp(self, day: str) -> BinaryIO:
        fp = self._state_fps.get(day)
        if fp is not None and not fp.closed:
            return fp
        path = self._state_path(day)
        ensure_parent(path)
        fp = path.open("r+b") if path.exists() else path.open("w+b")
        self._state_fps[day] = fp
        return fp

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
            fp = self._state_fp(day)
            fp.seek(0)
            fp.write(str(seq).encode("utf-8"))
            fp.truncate()
            # Keep state durable enough for restart while avoiding open/close per record.
            if seq <= 4 or seq % 128 == 0:
                fp.flush()
            return seq

    def flush(self) -> None:
        with self._lock:
            for day, seq in self._day_cache.items():
                fp = self._state_fp(day)
                fp.seek(0)
                fp.write(str(seq).encode("utf-8"))
                fp.truncate()
                fp.flush()

    def close(self) -> None:
        self.flush()
        with self._lock:
            for fp in self._state_fps.values():
                try:
                    fp.close()
                except OSError:
                    continue
            self._state_fps.clear()


class _JsonlGzMemberAppender:
    """Append complete gzip members to one file without reopening per record."""

    def __init__(self, path: Path, *, flush_every: int = 1, flush_interval_sec: float = 1.0):
        self.path = path
        ensure_parent(path)
        self.fp = path.open("ab")
        self.flush_every = max(1, int(flush_every))
        self.flush_interval_sec = max(0.1, float(flush_interval_sec))
        self.pending_writes = 0
        self.last_flush_monotonic = time.monotonic()

    def append_line(self, line: str) -> None:
        self.fp.write(gzip.compress(line.encode("utf-8"), compresslevel=6))
        self.pending_writes += 1
        now = time.monotonic()
        if self.pending_writes >= self.flush_every or (now - self.last_flush_monotonic) >= self.flush_interval_sec:
            self.fp.flush()
            self.pending_writes = 0
            self.last_flush_monotonic = now

    def close(self) -> None:
        try:
            self.fp.flush()
        finally:
            self.fp.close()


class RawCaptureStore:
    """Write capture envelopes into day-partitioned gzip jsonl files."""

    def __init__(self, raw_root: str | Path):
        self.raw_root = Path(raw_root)
        self.seq_store = CaptureSeqStore(self.raw_root)
        self._appenders: Dict[Path, _JsonlGzMemberAppender] = {}
        self._lock = threading.Lock()

    def _target_path(self, day: str, source: str, channel: str) -> Path:
        return self.raw_root / day / source / f"{channel}.jsonl.gz"

    def _appender(self, path: Path) -> _JsonlGzMemberAppender:
        appender = self._appenders.get(path)
        if appender is not None:
            return appender
        appender = _JsonlGzMemberAppender(path)
        self._appenders[path] = appender
        return appender

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
        path = self._target_path(day, source, channel)
        line = json.dumps(env.as_dict(), ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._appender(path).append_line(line + "\n")
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

    def close(self) -> None:
        with self._lock:
            for appender in self._appenders.values():
                try:
                    appender.close()
                except OSError:
                    continue
            self._appenders.clear()
        self.seq_store.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

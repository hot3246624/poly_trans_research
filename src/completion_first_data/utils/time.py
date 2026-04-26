"""Time helpers used across capture and replay modules."""

from __future__ import annotations

import datetime as dt
import time
from typing import Optional


def now_unix_ms() -> int:
    return int(time.time() * 1000)


def now_monotonic_ns() -> int:
    return time.monotonic_ns()


def parse_datetime_to_unix_ms(value: Optional[str]) -> Optional[int]:
    """Parse ISO-like datetime string into unix milliseconds.

    Returns None if input is empty or parsing fails.
    """
    if not value:
        return None
    txt = value.strip()
    if not txt:
        return None
    txt = txt.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(txt)
    except ValueError:
        fmts = (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S.%f",
        )
        parsed = None
        for fmt in fmts:
            try:
                parsed = dt.datetime.strptime(txt, fmt)
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
                break
            except ValueError:
                continue
        if parsed is None:
            return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


def day_from_ms(unix_ms: int) -> str:
    return dt.datetime.fromtimestamp(unix_ms / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")

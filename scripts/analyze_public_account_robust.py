#!/usr/bin/env python3
"""Run the canonical public-account ledger with resilient API backoff.

This wrapper keeps the canonical accounting and output schema in
``analyze_xuan_public_activity_pnl.py``.  It only replaces the HTTP retry
policy so long historical windows can survive public API throttling and
transient connection resets.
"""

from __future__ import annotations

import http.client
import json
import random
import socket
from pathlib import Path
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import analyze_xuan_public_activity_pnl as canonical


def resilient_fetch_json(
    url: str,
    params: dict,
    retries: int,
    timeout: int,
    pause_s: float,
):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(
        f"{url}?{query}",
        headers=canonical.HEADERS,
    )
    last_exc: Exception | None = None
    attempts = max(12, retries)
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=max(timeout, 45)) as response:
                payload = response.read().decode()
            data = json.loads(payload)
            if pause_s:
                time.sleep(pause_s)
            return data
        except urllib.error.HTTPError as exc:
            last_exc = exc
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            if exc.code not in {408, 425, 429, 500, 502, 503, 504}:
                break
            try:
                server_delay = float(retry_after) if retry_after else 0.0
            except ValueError:
                server_delay = 0.0
            delay = max(server_delay, min(90.0, 1.5 * (2**attempt)))
        except (
            TimeoutError,
            socket.timeout,
            ConnectionResetError,
            http.client.RemoteDisconnected,
            http.client.IncompleteRead,
            urllib.error.URLError,
            json.JSONDecodeError,
        ) as exc:
            last_exc = exc
            delay = min(90.0, 1.5 * (2**attempt))
        if attempt + 1 < attempts:
            time.sleep(delay + random.uniform(0.0, 0.75))
    raise RuntimeError(f"resilient fetch failed url={url} params={params} exc={last_exc}")


canonical.fetch_json = resilient_fetch_json
raise SystemExit(canonical.main())

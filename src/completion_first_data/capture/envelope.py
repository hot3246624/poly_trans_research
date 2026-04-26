"""Raw envelope model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(slots=True)
class RawEnvelope:
    recv_unix_ms: int
    recv_monotonic_ns: int
    capture_seq: int
    source: str
    channel: str
    condition_id: str
    payload_json: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "recv_unix_ms": self.recv_unix_ms,
            "recv_monotonic_ns": self.recv_monotonic_ns,
            "capture_seq": self.capture_seq,
            "source": self.source,
            "channel": self.channel,
            "condition_id": self.condition_id,
            "payload_json": self.payload_json,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RawEnvelope":
        return cls(
            recv_unix_ms=int(data.get("recv_unix_ms", 0)),
            recv_monotonic_ns=int(data.get("recv_monotonic_ns", 0)),
            capture_seq=int(data.get("capture_seq", 0)),
            source=str(data.get("source", "")),
            channel=str(data.get("channel", "")),
            condition_id=str(data.get("condition_id") or ""),
            payload_json=data.get("payload_json") or {},
        )


def pick_condition_id(payload: Dict[str, Any], fallback: Optional[str] = None) -> str:
    """Best-effort extraction for condition_id from different payload shapes."""
    for key in (
        "condition_id",
        "conditionId",
        "condition",
        "market",
        "market_id",
        "marketId",
    ):
        val = payload.get(key)
        if val:
            return str(val)
    if fallback:
        return fallback
    return ""

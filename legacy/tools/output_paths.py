from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


OUTPUT_ROOT = Path(__file__).resolve().parents[2] / "outputs" / "trade_analysis"
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class OutputBundle:
    root_dir: Path
    trades_json: Path
    fetch_meta_json: Path
    chart_html: Path
    analysis_html: Path
    chart_png: Path
    report_txt: Path


def _slugify(text: str, *, max_len: int = 72) -> str:
    lowered = (text or "").strip().lower().replace("–", "-").replace("—", "-")
    compact = _NON_ALNUM_RE.sub("-", lowered).strip("-")
    if not compact:
        compact = "market-unknown"
    return compact[:max_len].rstrip("-")


def _iter_candidate_values(raw_trades: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> Iterable[str]:
    for trade in raw_trades:
        for key in keys:
            value = trade.get(key)
            if isinstance(value, str):
                stripped = value.strip()
                if stripped:
                    yield stripped


def detect_user_address(raw_trades: list[dict[str, Any]], explicit: Optional[str] = None) -> Optional[str]:
    if explicit and explicit.strip():
        return explicit.strip()
    keys = ("proxyWallet", "user", "wallet", "maker", "taker", "address")
    for value in _iter_candidate_values(raw_trades[:25], keys):
        if value.lower().startswith("0x"):
            return value
    return None


def detect_market_label(raw_trades: list[dict[str, Any]], explicit_title: Optional[str] = None) -> str:
    if raw_trades:
        first = raw_trades[0]
        for key in ("slug", "eventSlug", "title"):
            value = first.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if explicit_title and explicit_title.strip():
        return explicit_title.strip()
    return "market-unknown"


def detect_condition_id(raw_trades: list[dict[str, Any]]) -> Optional[str]:
    if not raw_trades:
        return None
    first = raw_trades[0]
    for key in ("conditionId", "condition_id"):
        value = first.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _short_address(address: Optional[str]) -> str:
    if not address:
        return "user-unknown"
    txt = address.strip()
    if txt.lower().startswith("0x") and len(txt) >= 10:
        return txt[:12].lower()
    return _slugify(txt, max_len=18)


def prepare_output_bundle(
    raw_trades: list[dict[str, Any]],
    *,
    market_title: Optional[str] = None,
    user_address: Optional[str] = None,
) -> OutputBundle:
    market_part = _slugify(detect_market_label(raw_trades, market_title))
    user_part = _short_address(detect_user_address(raw_trades, user_address))
    run_ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    folder_name = f"{market_part}__{user_part}__{run_ts}"

    root_dir = OUTPUT_ROOT / folder_name
    root_dir.mkdir(parents=True, exist_ok=True)

    return OutputBundle(
        root_dir=root_dir,
        trades_json=root_dir / "trades.json",
        fetch_meta_json=root_dir / "fetch_meta.json",
        chart_html=root_dir / "chart.html",
        analysis_html=root_dir / "analysis_table.html",
        chart_png=root_dir / "chart.png",
        report_txt=root_dir / "report.txt",
    )

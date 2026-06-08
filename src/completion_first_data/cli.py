"""CLI for completion-first data acquisition and replay pipeline."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

from .capture.ingest import ingest_ndjson
from .capture.meta import MarketMetaFetchState, backfill_trades_once, capture_market_meta_once
from .capture.raw_store import RawCaptureStore
from .capture.settlement import fetch_condition_settlement
from .capture.websocket_sidecar import (
    MetaPollConfig,
    SettlementPollConfig,
    SidecarConfig,
    UserWsConfig,
    WsSourceConfig,
    load_sidecar_config,
    run_sidecar,
)
from .capture.xuan_poller import XuanPollConfig, _parse_ts_ms
from .quality.replay_market_audit import (
    AuditConfig,
    run_market_replay_audit,
    safety_gate,
    save_audit_report,
)
from .quality.startup_audit import save_startup_audit_report, run_startup_audit
from .quality.validator import save_report, validate_replay_db
from .replay.builder import build_replay_for_day
from .replay.normalize import normalize_side
from .replay.schema import init_schema
from .user_truth import resolve_user_auth_config
from .constants import (
    CHANNEL_XUAN_ACTIVITY,
    CHANNEL_XUAN_POLL_LOG,
    CHANNEL_XUAN_TRADES,
    POLYMARKET_DATA_ACTIVITY_URL,
    POLYMARKET_DATA_TRADES_URL,
    SOURCE_KIND_XUAN_POLL,
)

LOG = logging.getLogger("completion_first_data")


def _default_day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


def _rolling_days(hours: int, now: Optional[dt.datetime] = None) -> List[str]:
    snap = now or dt.datetime.now(dt.timezone.utc)
    start = snap - dt.timedelta(hours=max(1, hours))
    days: List[str] = []
    cursor = start.date()
    while cursor <= snap.date():
        days.append(cursor.strftime("%Y-%m-%d"))
        cursor += dt.timedelta(days=1)
    return days


def _replay_db_path(replay_root: str | Path, day: str) -> Path:
    return Path(replay_root) / day / "crypto_5m.sqlite"


def _require_existing_replay_db(db: Path, action: str) -> bool:
    if db.exists():
        return True
    LOG.error("%s skipped: replay db missing: %s", action, db)
    return False


def _parse_condition_ids(values: Optional[List[str]], file_path: Optional[str]) -> List[str]:
    out: List[str] = []
    if values:
        out.extend(values)
    if file_path:
        for line in Path(file_path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    # preserve order, remove duplicates
    seen = set()
    uniq = []
    for cid in out:
        if cid not in seen:
            seen.add(cid)
            uniq.append(cid)
    return uniq


def _load_env_file(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        txt = line.strip()
        if not txt or txt.startswith("#") or "=" not in txt:
            continue
        key, value = txt.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value.startswith(("'", '"')) and value.endswith(("'", '"')) and len(value) >= 2:
            value = value[1:-1]
        env[key] = value
    return env


def _load_env_stack(primary: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    candidates = [Path(".env"), primary.parent / ".env", primary]
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.exists():
            continue
        seen.add(resolved)
        env.update(_load_env_file(path))
    return env


def _ws_url(base: str, kind: str) -> str:
    b = (base or "").rstrip("/")
    if b.endswith(f"/{kind}"):
        return b
    return f"{b}/{kind}"


def _parse_bool_env(env: dict[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    txt = raw.strip().lower()
    if txt in {"1", "true", "yes", "on"}:
        return True
    if txt in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_int_env(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except (TypeError, ValueError):
        return default


def _parse_csv_env(env: dict[str, str], key: str, default_csv: str) -> List[str]:
    raw = env.get(key, default_csv)
    return [v.strip() for v in raw.split(",") if v and v.strip()]


def cmd_init_layout(args: argparse.Namespace) -> int:
    root = Path(args.root)
    for rel in [
        "data/raw",
        "data/replay",
        "config",
        "legacy/reports",
        "legacy/tools",
        "src/completion_first_data",
        "tests",
    ]:
        (root / rel).mkdir(parents=True, exist_ok=True)

    cfg_path = root / "config" / "capture.sources.example.json"
    if not cfg_path.exists() or args.force:
        cfg = {
            "raw_root": "data/raw",
            "market_prefixes": ["btc-updown-5m"],
            "market_channels": ["book", "last_trade_price"],
            "max_markets_per_prefix": 1,
            "debug_raw_market_ws": False,
            "market_ws": {
                "enabled": True,
                "url": "wss://ws-subscriptions-clob.polymarket.com/ws/market",
                "default_channel": "book",
                "reconnect_sec": 3,
                "subscribe": []
            },
            "user_ws": {
                "enabled": False,
                "url": "wss://ws-subscriptions-clob.polymarket.com/ws/user",
                "default_channel": "order",
                "reconnect_sec": 3,
                "subscribe": []
            },
            "meta_poll": {
                "enabled": True,
                "interval_sec": 20,
                "active_only": True,
                "conditional_get": True,
                "round_switch_delay_sec": 8
            },
            "settlement_poll": {
                "enabled": True,
                "interval_sec": 20,
                "per_condition_cooldown_sec": 30,
                "retention_hours": 12
            },
            "xuan_poll": {
                "enabled": False,
                "user": "",
                "interval_sec": 300,
                "aggressive_interval_sec": 60,
                "aggressive_trade_threshold": 500,
                "page_limit": 500,
                "max_pages": 30,
                "cursor_path": ""
            },
        }
        cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        LOG.info("wrote %s", cfg_path)
    else:
        LOG.info("skip existing %s", cfg_path)

    research_env = root / "config" / "research.env.example"
    if not research_env.exists() or args.force:
        research_env.write_text(
            "\n".join(
                [
                    "CF_MARKET_PREFIXES=btc-updown-5m",
                    "CF_MARKET_CHANNELS=book,last_trade_price",
                    "CF_DISABLE_USER_WS=true",
                    "CF_META_ACTIVE_ONLY=true",
                    "CF_MAX_MARKETS_PER_PREFIX=1",
                    "CF_META_INTERVAL_SEC=20",
                    "CF_META_SWITCH_DELAY_SEC=8",
                    "CF_SETTLEMENT_POLL_ENABLED=true",
                    "CF_SETTLEMENT_POLL_SEC=20",
                    "CF_SETTLEMENT_POLL_COOLDOWN_SEC=30",
                    "CF_RAW_ROOT=data/raw",
                    "CF_REPLAY_ROOT=data/replay",
                    "CF_USER_WS_ENABLED=false",
                    "CF_DISABLE_USER_WS=true",
                    "CF_XUAN_POLL_ENABLED=false",
                    "# CF_XUAN_USER=0x...",
                    "CF_XUAN_POLL_SEC=300",
                    "CF_XUAN_POLL_AGGRESSIVE_SEC=60",
                    "CF_XUAN_POLL_AGGRESSIVE_THRESHOLD=500",
                    "CF_XUAN_POLL_PAGE_LIMIT=500",
                    "CF_XUAN_POLL_MAX_PAGES=30",
                    "# CF_XUAN_CURSOR_PATH=data/raw/.xuan_cursor.json",
                    "POLYMARKET_WS_BASE_URL=wss://ws-subscriptions-clob.polymarket.com/ws",
                    "# Secrets should live in config/.env or .env, never in tracked files.",
                    "# POLYMARKET_FUNDER_ADDRESS=",
                    "# CF_L1_PRIVATE_KEY=",
                    "# CF_API_KEY=",
                    "# CF_API_SECRET=",
                    "# CF_API_PASSPHRASE=",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        LOG.info("wrote %s", research_env)
    else:
        LOG.info("skip existing %s", research_env)

    return 0


def cmd_capture_meta(args: argparse.Namespace) -> int:
    store = RawCaptureStore(args.raw_root)
    fetch_state = None if args.no_conditional_get else MarketMetaFetchState()

    if args.loop:
        while True:
            count = capture_market_meta_once(store, active_only=args.active_only, fetch_state=fetch_state)
            if fetch_state is not None and fetch_state.last_poll_not_modified:
                LOG.info("market_meta not modified (HTTP 304)")
            else:
                LOG.info("captured %d market_meta records", count)
            time.sleep(max(1, args.interval_sec))
    else:
        count = capture_market_meta_once(store, active_only=args.active_only, fetch_state=fetch_state)
        if fetch_state is not None and fetch_state.last_poll_not_modified:
            LOG.info("market_meta not modified (HTTP 304)")
        else:
            LOG.info("captured %d market_meta records", count)
    return 0


def cmd_backfill_trades(args: argparse.Namespace) -> int:
    cids = _parse_condition_ids(args.condition_id, args.condition_file)
    if not cids:
        raise SystemExit("No condition_ids provided. Use --condition-id or --condition-file")

    store = RawCaptureStore(args.raw_root)
    written = backfill_trades_once(
        store,
        condition_ids=cids,
        min_ts_ms=args.min_ts_ms,
        max_ts_ms=args.max_ts_ms,
    )
    LOG.info("backfilled %d trades for %d condition_ids", written, len(cids))
    return 0


def cmd_capture_ingest(args: argparse.Namespace) -> int:
    store = RawCaptureStore(args.raw_root)
    n = ingest_ndjson(
        store,
        input_path=args.input,
        source=args.source,
        channel=args.channel,
        condition_id=args.condition_id,
    )
    LOG.info("ingested %d records", n)
    return 0


def cmd_capture_sidecar(args: argparse.Namespace) -> int:
    cfg = load_sidecar_config(args.config, raw_root_override=args.raw_root)
    asyncio.run(run_sidecar(cfg, duration_sec=args.duration_sec))
    return 0


def cmd_capture_sidecar_env(args: argparse.Namespace) -> int:
    env_path = Path(args.env_file)
    if not env_path.exists():
        raise SystemExit(f"env file not found: {env_path}")

    env = _load_env_stack(env_path)
    ws_base = env.get("POLYMARKET_WS_BASE_URL", "wss://ws-subscriptions-clob.polymarket.com/ws")
    clob_rest_url = env.get("CF_CLOB_REST_URL", env.get("POLYMARKET_CLOB_BASE_URL", "https://clob.polymarket.com"))
    prefixes = _parse_csv_env(env, "CF_MARKET_PREFIXES", "btc-updown-5m")
    channels = _parse_csv_env(env, "CF_MARKET_CHANNELS", "book,last_trade_price")
    env_max_markets = max(0, _parse_int_env(env, "CF_MAX_MARKETS_PER_PREFIX", 1))
    env_disable_user_ws = _parse_bool_env(env, "CF_DISABLE_USER_WS", True)
    env_user_ws_enabled = _parse_bool_env(env, "CF_USER_WS_ENABLED", not env_disable_user_ws)
    env_user_reconcile_sec = max(1, _parse_int_env(env, "CF_USER_RECONCILE_SEC", 60))
    env_user_recovery_lookback_sec = max(30, _parse_int_env(env, "CF_USER_RECOVERY_LOOKBACK_SEC", 300))
    env_meta_active_only = _parse_bool_env(env, "CF_META_ACTIVE_ONLY", True)
    env_meta_interval_sec = max(1, _parse_int_env(env, "CF_META_INTERVAL_SEC", 20))
    env_meta_switch_delay_sec = max(0, _parse_int_env(env, "CF_META_SWITCH_DELAY_SEC", 8))
    env_settlement_poll_enabled = _parse_bool_env(env, "CF_SETTLEMENT_POLL_ENABLED", True)
    env_settlement_poll_sec = max(1, _parse_int_env(env, "CF_SETTLEMENT_POLL_SEC", 20))
    env_settlement_poll_cooldown_sec = max(1, _parse_int_env(env, "CF_SETTLEMENT_POLL_COOLDOWN_SEC", 30))
    env_xuan_poll_enabled = _parse_bool_env(env, "CF_XUAN_POLL_ENABLED", False)
    env_xuan_user = str(env.get("CF_XUAN_USER", "") or "").strip()
    env_xuan_poll_sec = max(1, _parse_int_env(env, "CF_XUAN_POLL_SEC", 300))
    env_xuan_aggressive_sec = max(1, _parse_int_env(env, "CF_XUAN_POLL_AGGRESSIVE_SEC", 60))
    env_xuan_aggressive_threshold = max(1, _parse_int_env(env, "CF_XUAN_POLL_AGGRESSIVE_THRESHOLD", 500))
    env_xuan_page_limit = max(1, _parse_int_env(env, "CF_XUAN_POLL_PAGE_LIMIT", 500))
    env_xuan_max_pages = max(1, _parse_int_env(env, "CF_XUAN_POLL_MAX_PAGES", 30))
    env_xuan_cursor_path = str(env.get("CF_XUAN_CURSOR_PATH", "") or "").strip() or None
    env_raw_root = env.get("CF_RAW_ROOT", "data/raw").strip() or "data/raw"

    if args.market_prefix:
        prefixes = [p.strip() for p in args.market_prefix if p and p.strip()]
    if args.market_channel:
        channels = [c.strip() for c in args.market_channel if c and c.strip()]
    if not prefixes:
        raise SystemExit("No market prefixes found. Set CF_MARKET_PREFIXES or pass --market-prefix.")
    max_markets_per_prefix = args.max_markets_per_prefix if args.max_markets_per_prefix is not None else env_max_markets
    xuan_user = args.xuan_user.strip() if args.xuan_user else env_xuan_user
    xuan_enabled = env_xuan_poll_enabled if args.xuan_poll_enabled is None else args.xuan_poll_enabled
    if args.disable_xuan_poll:
        xuan_enabled = False

    market_ws = WsSourceConfig(
        name="market_ws",
        url=_ws_url(ws_base, "market"),
        enabled=not args.disable_market_ws,
        subscribe=[],
        default_channel="book",
        reconnect_sec=3.0,
    )

    user_ws: Optional[UserWsConfig] = None
    user_auth = None
    user_requested = (not args.disable_user_ws) and env_user_ws_enabled
    if user_requested:
        user_auth = resolve_user_auth_config(env, clob_rest_url=clob_rest_url)
        if user_auth and user_auth.funder_address:
            user_ws = UserWsConfig(
                name="user_ws",
                url=_ws_url(ws_base, "user"),
                auth=user_auth,
                enabled=True,
                reconnect_sec=3.0,
                heartbeat_sec=10,
                reconcile_sec=env_user_reconcile_sec,
                recovery_lookback_sec=env_user_recovery_lookback_sec,
                rest_url=clob_rest_url,
            )
        elif user_requested:
            LOG.warning(
                "user truth requested but auth is incomplete or user address missing; continuing in public-only mode"
            )

    cfg = SidecarConfig(
        raw_root=args.raw_root or env_raw_root,
        market_ws=market_ws,
        user_ws=user_ws,
        meta_poll=MetaPollConfig(
            enabled=True,
            interval_sec=args.meta_interval_sec if args.meta_interval_sec is not None else env_meta_interval_sec,
            active_only=args.meta_active_only if args.meta_active_only is not None else env_meta_active_only,
            conditional_get=not args.disable_meta_conditional_get,
            round_switch_delay_sec=(
                args.meta_switch_delay_sec
                if args.meta_switch_delay_sec is not None
                else env_meta_switch_delay_sec
            ),
        ),
        market_prefixes=prefixes,
        market_channels=channels,
        max_markets_per_prefix=max(0, max_markets_per_prefix),
        debug_raw_market_ws=args.debug_raw_market_ws,
        settlement_poll=SettlementPollConfig(
            enabled=(
                env_settlement_poll_enabled
                if args.settlement_poll_enabled is None
                else args.settlement_poll_enabled
            )
            and (not args.disable_settlement_poll),
            interval_sec=args.settlement_poll_sec if args.settlement_poll_sec is not None else env_settlement_poll_sec,
            per_condition_cooldown_sec=(
                args.settlement_poll_cooldown_sec
                if args.settlement_poll_cooldown_sec is not None
                else env_settlement_poll_cooldown_sec
            ),
            retention_hours=12,
        ),
        xuan_poll=XuanPollConfig(
            enabled=xuan_enabled and bool(xuan_user),
            user=xuan_user,
            interval_sec=args.xuan_poll_sec if args.xuan_poll_sec is not None else env_xuan_poll_sec,
            aggressive_interval_sec=(
                args.xuan_poll_aggressive_sec
                if args.xuan_poll_aggressive_sec is not None
                else env_xuan_aggressive_sec
            ),
            aggressive_trade_threshold=(
                args.xuan_poll_aggressive_threshold
                if args.xuan_poll_aggressive_threshold is not None
                else env_xuan_aggressive_threshold
            ),
            page_limit=args.xuan_poll_page_limit if args.xuan_poll_page_limit is not None else env_xuan_page_limit,
            max_pages=args.xuan_poll_max_pages if args.xuan_poll_max_pages is not None else env_xuan_max_pages,
            cursor_path=args.xuan_cursor_path if args.xuan_cursor_path is not None else env_xuan_cursor_path,
        ),
    )

    LOG.info(
        "running sidecar from env, prefixes=%s, channels=%s, per_prefix_limit=%d, user_ws=%s, meta_active_only=%s, meta_conditional=%s, settlement_poll=%s, xuan_poll=%s",
        ",".join(prefixes),
        ",".join(channels),
        max(0, max_markets_per_prefix),
        "enabled" if user_ws and user_ws.enabled else "disabled",
        str(cfg.meta_poll.active_only).lower(),
        "on" if not args.disable_meta_conditional_get else "off",
        "enabled" if cfg.settlement_poll and cfg.settlement_poll.enabled else "disabled",
        "enabled" if cfg.xuan_poll and cfg.xuan_poll.enabled else "disabled",
    )
    asyncio.run(run_sidecar(cfg, duration_sec=args.duration_sec))
    return 0


def cmd_build_replay(args: argparse.Namespace) -> int:
    day = args.day or _default_day()
    raw_root = args.raw_root or os.getenv("CF_RAW_ROOT", "data/raw")
    replay_root = args.replay_root or os.getenv("CF_REPLAY_ROOT", "data/replay")
    stats = build_replay_for_day(Path(raw_root), Path(replay_root), day)
    LOG.info("build done: %s", json.dumps(stats.as_dict(), ensure_ascii=False))
    return 0


def cmd_build_replay_rolling(args: argparse.Namespace) -> int:
    raw_root = args.raw_root or os.getenv("CF_RAW_ROOT", "data/raw")
    replay_root = args.replay_root or os.getenv("CF_REPLAY_ROOT", "data/replay")
    hours = max(1, int(args.hours))
    days = _rolling_days(hours)

    for day in days:
        stats = build_replay_for_day(Path(raw_root), Path(replay_root), day)
        LOG.info("rolling build done [%s]: %s", day, json.dumps(stats.as_dict(), ensure_ascii=False))

    if args.validate_latest:
        latest_day = days[-1]
        db = _replay_db_path(replay_root, latest_day)
        if not _require_existing_replay_db(db, f"rolling validation latest_day={latest_day}"):
            return 3
        report = validate_replay_db(db, gap_threshold_ms=args.gap_threshold_ms)
        LOG.info("rolling validation [%s]: %s", latest_day, json.dumps(report.as_dict(), ensure_ascii=False))
        return 0 if report.all_passed else 2
    return 0


def cmd_validate_replay(args: argparse.Namespace) -> int:
    replay_root = args.replay_root or os.getenv("CF_REPLAY_ROOT", "data/replay")
    if args.db_path:
        db = Path(args.db_path)
    else:
        day = args.day or _default_day()
        db = _replay_db_path(replay_root, day)

    if not _require_existing_replay_db(db, "validation"):
        return 3

    try:
        report = validate_replay_db(db, gap_threshold_ms=args.gap_threshold_ms)
    except sqlite3.OperationalError as exc:
        LOG.error("validation failed to open replay db %s: %s", db, exc)
        return 3
    if args.output:
        save_report(report, Path(args.output))
        LOG.info("saved validation report -> %s", args.output)

    LOG.info("validation: %s", json.dumps(report.as_dict(), ensure_ascii=False))
    return 0 if report.all_passed else 2


def cmd_audit_startup(args: argparse.Namespace) -> int:
    replay_root = args.replay_root or os.getenv("CF_REPLAY_ROOT", "data/replay")
    if args.db_path:
        db = Path(args.db_path)
    else:
        day = args.day or _default_day()
        db = _replay_db_path(replay_root, day)

    if not _require_existing_replay_db(db, "startup audit"):
        return 3

    try:
        report = run_startup_audit(
            db,
            require_user_truth=args.require_user_truth,
            taker_side_null_max_ratio=args.taker_side_null_max_ratio,
            min_market_meta_rounds=args.min_market_meta_rounds,
            min_settlement_rows=args.min_settlement_rows,
            min_xuan_poll_points=args.min_xuan_poll_points,
            max_abs_avg_trade_latency_ms=args.max_abs_avg_trade_latency_ms,
        )
    except sqlite3.OperationalError as exc:
        LOG.error("startup audit failed to open replay db %s: %s", db, exc)
        return 3
    if args.output:
        save_startup_audit_report(report, Path(args.output))
        LOG.info("saved startup audit report -> %s", args.output)

    LOG.info("startup_audit: %s", json.dumps(report.as_dict(), ensure_ascii=False))
    return 0 if report.all_passed else 2


def _parse_utc_iso(value: str) -> dt.datetime:
    txt = value.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(txt)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _row_ts_ms(row: Dict[str, Any]) -> Optional[int]:
    return _parse_ts_ms(row.get("timestamp") or row.get("time") or row.get("createdAt"))


def _data_api_rows(payload: Any) -> List[Dict[str, Any]]:
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("trades") or payload.get("history") or payload.get("activity") or payload.get("data") or []
    else:
        rows = []
    return [r for r in rows if isinstance(r, dict)]


def _fetch_xuan_window(
    *,
    session: requests.Session,
    url: str,
    user: str,
    start_ms: int,
    end_ms: int,
    page_limit: int,
    max_pages: int,
    include_taker_only_false: bool,
    timeout_sec: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows_in_window: List[Dict[str, Any]] = []
    scanned = 0
    before_cursor_s: Optional[int] = int(end_ms / 1000)
    oldest_seen: Optional[int] = None
    newest_seen: Optional[int] = None
    page_cap_hit = False

    for page in range(max(1, max_pages)):
        params: Dict[str, Any] = {
            "limit": max(1, page_limit),
            "user": user,
        }
        if before_cursor_s is not None:
            params["before"] = before_cursor_s
        if include_taker_only_false:
            params["takerOnly"] = "false"

        resp = session.get(url, params=params, timeout=max(1, timeout_sec))
        resp.raise_for_status()
        page_rows = _data_api_rows(resp.json())
        if not page_rows:
            break

        page_oldest: Optional[int] = None
        for row in page_rows:
            scanned += 1
            ts_ms = _row_ts_ms(row)
            if ts_ms is None:
                continue
            oldest_seen = ts_ms if oldest_seen is None else min(oldest_seen, ts_ms)
            newest_seen = ts_ms if newest_seen is None else max(newest_seen, ts_ms)
            page_oldest = ts_ms if page_oldest is None else min(page_oldest, ts_ms)
            if start_ms <= ts_ms < end_ms:
                rows_in_window.append(row)

        if page_oldest is None:
            break
        if page_oldest < start_ms:
            break
        if len(page_rows) < max(1, page_limit):
            break
        before_cursor_s = max(0, int(page_oldest / 1000) - 1)
        if page == max(1, max_pages) - 1:
            page_cap_hit = True

    complete = not (page_cap_hit and (oldest_seen is None or oldest_seen >= start_ms))
    summary = {
        "rows_scanned": scanned,
        "rows_in_window": len(rows_in_window),
        "oldest_seen_ms": oldest_seen,
        "newest_seen_ms": newest_seen,
        "page_cap_hit": page_cap_hit,
        "target_window_complete": complete,
    }
    return rows_in_window, summary


def _write_xuan_backfill_rows(
    *,
    raw_store: RawCaptureStore,
    user: str,
    endpoint: str,
    rows: List[Dict[str, Any]],
    poll_ts_ms: int,
    log_recv_ms: Optional[int] = None,
) -> Optional[int]:
    max_ts: Optional[int] = None
    for row in sorted(rows, key=lambda r: _row_ts_ms(r) or 0):
        ts_ms = _row_ts_ms(row)
        if ts_ms is not None:
            max_ts = ts_ms if max_ts is None else max(max_ts, ts_ms)
        if endpoint == "trades":
            payload = {
                "user": user,
                "poll_ts_ms": poll_ts_ms,
                "condition_id": row.get("conditionId"),
                "trade_ts_ms": ts_ms,
                "trade_id": row.get("id"),
                "tx_hash": row.get("transactionHash"),
                "asset": row.get("asset"),
                "outcome": row.get("outcome"),
                "side": row.get("side"),
                "price": row.get("price"),
                "size": row.get("size"),
                "slug": row.get("slug"),
                "event_slug": row.get("eventSlug"),
                "title": row.get("title"),
                "proxy_wallet": row.get("proxyWallet"),
                "source_quality": "data_api_backfill",
                "raw_json": row,
            }
            raw_store.write(
                source=SOURCE_KIND_XUAN_POLL,
                channel=CHANNEL_XUAN_TRADES,
                payload_json=payload,
                condition_id=str(row.get("conditionId") or ""),
                recv_unix_ms=ts_ms or poll_ts_ms,
            )
        elif endpoint == "activity":
            payload = {
                "user": user,
                "poll_ts_ms": poll_ts_ms,
                "condition_id": row.get("conditionId"),
                "activity_ts_ms": ts_ms,
                "activity_type": row.get("type"),
                "tx_hash": row.get("transactionHash"),
                "asset": row.get("asset"),
                "outcome": row.get("outcome"),
                "side": row.get("side"),
                "price": row.get("price"),
                "size": row.get("size"),
                "usdc_size": row.get("usdcSize"),
                "slug": row.get("slug"),
                "event_slug": row.get("eventSlug"),
                "title": row.get("title"),
                "proxy_wallet": row.get("proxyWallet"),
                "source_quality": "data_api_backfill",
                "raw_json": row,
            }
            raw_store.write(
                source=SOURCE_KIND_XUAN_POLL,
                channel=CHANNEL_XUAN_ACTIVITY,
                payload_json=payload,
                condition_id=str(row.get("conditionId") or ""),
                recv_unix_ms=ts_ms or poll_ts_ms,
            )
    raw_store.write(
        source=SOURCE_KIND_XUAN_POLL,
        channel=CHANNEL_XUAN_POLL_LOG,
        payload_json={
            "user": user,
            "endpoint": endpoint,
            "poll_ts_ms": poll_ts_ms,
            "rows": len(rows),
            "max_ts_ms": max_ts,
            "ok": True,
            "error": None,
            "source_quality": "data_api_backfill",
        },
        recv_unix_ms=log_recv_ms or poll_ts_ms,
    )
    return max_ts


def _fetch_xuan_market_rows(
    *,
    session: requests.Session,
    url: str,
    user: str,
    condition_id: str,
    page_limit: int,
    max_pages: int,
    include_taker_only_false: bool,
    timeout_sec: int,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    scanned = 0
    oldest_seen: Optional[int] = None
    newest_seen: Optional[int] = None
    page_cap_hit = False

    for page in range(max(1, max_pages)):
        params: Dict[str, Any] = {
            "limit": max(1, page_limit),
            "offset": page * max(1, page_limit),
            "user": user,
            "market": condition_id,
        }
        if include_taker_only_false:
            params["takerOnly"] = "false"

        resp = session.get(url, params=params, timeout=max(1, timeout_sec))
        resp.raise_for_status()
        page_rows = _data_api_rows(resp.json())
        if not page_rows:
            break

        for row in page_rows:
            scanned += 1
            ts_ms = _row_ts_ms(row)
            if ts_ms is not None:
                oldest_seen = ts_ms if oldest_seen is None else min(oldest_seen, ts_ms)
                newest_seen = ts_ms if newest_seen is None else max(newest_seen, ts_ms)
            rows.append(row)

        if len(page_rows) < max(1, page_limit):
            break
        if page == max(1, max_pages) - 1:
            page_cap_hit = True

    return rows, {
        "rows_scanned": scanned,
        "rows": len(rows),
        "oldest_seen_ms": oldest_seen,
        "newest_seen_ms": newest_seen,
        "page_cap_hit": page_cap_hit,
    }


def _load_xuan_backfill_markets(
    *,
    replay_root: str | Path,
    days: Iterable[str],
    symbols: Iterable[str],
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    allowed_symbols = {s.strip().upper() for s in symbols if s.strip()}
    markets: Dict[str, Dict[str, Any]] = {}
    for day in days:
        db = _replay_db_path(replay_root, day)
        if not db.exists():
            LOG.warning("xuan market backfill skipping missing replay db: %s", db)
            continue
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            clauses = ["condition_id IS NOT NULL", "condition_id != ''"]
            params: List[Any] = []
            if allowed_symbols:
                placeholders = ",".join("?" for _ in allowed_symbols)
                clauses.append(f"upper(symbol) IN ({placeholders})")
                params.extend(sorted(allowed_symbols))
            if start_ms is not None:
                clauses.append("end_ms > ?")
                params.append(start_ms)
            if end_ms is not None:
                clauses.append("start_ms < ?")
                params.append(end_ms)
            sql = (
                "SELECT condition_id, slug, symbol, start_ms, end_ms "
                "FROM market_meta WHERE "
                + " AND ".join(clauses)
                + " ORDER BY start_ms, symbol, slug"
            )
            for condition_id, slug, symbol, m_start_ms, m_end_ms in conn.execute(sql, params):
                key = str(condition_id)
                if key not in markets:
                    markets[key] = {
                        "condition_id": key,
                        "slug": slug,
                        "symbol": symbol,
                        "start_ms": int(m_start_ms),
                        "end_ms": int(m_end_ms),
                    }
        finally:
            conn.close()
    return sorted(markets.values(), key=lambda m: (m["start_ms"], m["symbol"], m["slug"]))


def _load_outcome_backfill_markets(
    *,
    replay_root: str | Path,
    days: Iterable[str],
    symbols: Iterable[str],
    start_ms: Optional[int] = None,
    end_ms: Optional[int] = None,
) -> List[Dict[str, Any]]:
    allowed_symbols = {s.strip().upper() for s in symbols if s.strip()}
    markets: List[Dict[str, Any]] = []
    for day in days:
        db = _replay_db_path(replay_root, day)
        if not db.exists():
            LOG.warning("outcome backfill skipping missing replay db: %s", db)
            continue
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            clauses = ["condition_id IS NOT NULL", "condition_id != ''", "slug IS NOT NULL", "slug != ''"]
            params: List[Any] = []
            if allowed_symbols:
                placeholders = ",".join("?" for _ in allowed_symbols)
                clauses.append(f"upper(symbol) IN ({placeholders})")
                params.extend(sorted(allowed_symbols))
            if start_ms is not None:
                clauses.append("end_ms > ?")
                params.append(start_ms)
            if end_ms is not None:
                clauses.append("start_ms < ?")
                params.append(end_ms)
            sql = (
                "SELECT condition_id, slug, symbol, start_ms, end_ms "
                "FROM market_meta WHERE "
                + " AND ".join(clauses)
                + " ORDER BY start_ms, symbol, slug"
            )
            for condition_id, slug, symbol, m_start_ms, m_end_ms in conn.execute(sql, params):
                markets.append(
                    {
                        "day": day,
                        "db_path": str(db),
                        "condition_id": str(condition_id),
                        "slug": str(slug),
                        "symbol": str(symbol),
                        "start_ms": int(m_start_ms),
                        "end_ms": int(m_end_ms),
                    }
                )
        finally:
            conn.close()
    return markets


def _settlement_coverage_summary(
    conn: sqlite3.Connection,
    *,
    symbols: Iterable[str],
    start_ms: Optional[int],
    end_ms: Optional[int],
) -> Dict[str, Any]:
    allowed_symbols = {s.strip().upper() for s in symbols if s.strip()}
    settlement_cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(settlement_records)").fetchall()}
    winner_expr = "s.winner_side" if "winner_side" in settlement_cols else "s.official_outcome"
    clauses = ["1=1"]
    params: List[Any] = []
    if allowed_symbols:
        placeholders = ",".join("?" for _ in allowed_symbols)
        clauses.append(f"upper(m.symbol) IN ({placeholders})")
        params.extend(sorted(allowed_symbols))
    if start_ms is not None:
        clauses.append("m.end_ms > ?")
        params.append(start_ms)
    if end_ms is not None:
        clauses.append("m.start_ms < ?")
        params.append(end_ms)
    where = " AND ".join(clauses)
    official_expr = f"""
        CASE
          WHEN s.condition_id IS NOT NULL
           AND COALESCE({winner_expr}, s.official_outcome) IN ('YES', 'NO')
           AND lower(COALESCE(s.resolution_source, '')) NOT LIKE '%inferred%'
          THEN 1 ELSE 0
        END
    """
    inferred_expr = """
        CASE
          WHEN s.condition_id IS NOT NULL
           AND lower(COALESCE(s.resolution_source, '')) LIKE '%inferred%'
          THEN 1 ELSE 0
        END
    """
    row = conn.execute(
        f"""
        SELECT COUNT(*) AS markets_total,
               SUM({official_expr}) AS settled_markets,
               SUM({inferred_expr}) AS inferred_markets
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
        WHERE {where}
        """,
        params,
    ).fetchone()
    markets_total = int(row[0] or 0)
    settled_markets = int(row[1] or 0)
    inferred_markets = int(row[2] or 0)

    by_symbol: Dict[str, Dict[str, Any]] = {}
    for symbol, total, settled, inferred in conn.execute(
        f"""
        SELECT m.symbol,
               COUNT(*) AS markets_total,
               SUM({official_expr}) AS settled_markets,
               SUM({inferred_expr}) AS inferred_markets
        FROM market_meta m
        LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
        WHERE {where}
        GROUP BY m.symbol
        ORDER BY m.symbol
        """,
        params,
    ):
        total_i = int(total or 0)
        settled_i = int(settled or 0)
        by_symbol[str(symbol)] = {
            "markets_total": total_i,
            "settled_markets": settled_i,
            "inferred_markets": int(inferred or 0),
            "settlement_coverage_ratio": round(settled_i / total_i, 6) if total_i else 0.0,
        }

    by_source = {
        str(source or "unknown"): int(n or 0)
        for source, n in conn.execute(
            f"""
            SELECT COALESCE(s.resolution_source, 'missing') AS resolution_source, COUNT(*) AS n
            FROM market_meta m
            LEFT JOIN settlement_records s ON s.condition_id=m.condition_id
            WHERE {where}
            GROUP BY COALESCE(s.resolution_source, 'missing')
            ORDER BY n DESC
            """,
            params,
        )
    }
    return {
        "markets_total": markets_total,
        "settled_markets": settled_markets,
        "inferred_markets": inferred_markets,
        "settlement_coverage_ratio": round(settled_markets / markets_total, 6) if markets_total else 0.0,
        "by_symbol": by_symbol,
        "by_resolution_source": by_source,
    }


def _backfill_xuan_outcome_side(conn: sqlite3.Connection) -> Dict[str, int]:
    def _update(table: str) -> int:
        cur = conn.execute(
            f"""
            UPDATE {table}
            SET outcome_side = CASE lower(trim(COALESCE(outcome, '')))
                WHEN 'yes' THEN 'YES'
                WHEN 'up' THEN 'YES'
                WHEN 'y' THEN 'YES'
                WHEN 'true' THEN 'YES'
                WHEN '1' THEN 'YES'
                WHEN 'no' THEN 'NO'
                WHEN 'down' THEN 'NO'
                WHEN 'n' THEN 'NO'
                WHEN 'false' THEN 'NO'
                WHEN '0' THEN 'NO'
                ELSE outcome_side
            END
            WHERE outcome_side IS NULL OR trim(outcome_side) = ''
            """
        )
        return max(0, int(cur.rowcount or 0))

    return {
        "xuan_trades_rows_updated": _update("xuan_trades"),
        "xuan_activity_rows_updated": _update("xuan_activity"),
    }


def _write_settlement_record(conn: sqlite3.Connection, record: Dict[str, Any]) -> None:
    official_outcome = normalize_side(record.get("official_outcome"))
    winner_side = normalize_side(record.get("winner_side")) or official_outcome
    if official_outcome is None:
        raise ValueError("official_outcome must normalize to YES/NO")
    conn.execute(
        """
        INSERT INTO settlement_records (
            condition_id, official_outcome, winner_side, winner_token_id,
            settle_ms, resolution_source, raw_json, capture_seq
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(condition_id) DO UPDATE SET
            official_outcome=excluded.official_outcome,
            winner_side=excluded.winner_side,
            winner_token_id=excluded.winner_token_id,
            settle_ms=excluded.settle_ms,
            resolution_source=excluded.resolution_source,
            raw_json=excluded.raw_json,
            capture_seq=excluded.capture_seq
        """,
        (
            str(record["condition_id"]),
            official_outcome,
            winner_side,
            str(record.get("winner_token_id") or "") or None,
            record.get("settle_ms"),
            str(record.get("resolution_source") or "gamma_api"),
            record.get("raw_json"),
            int(record.get("capture_seq") or 0),
        ),
    )


def cmd_backfill_market_outcomes(args: argparse.Namespace) -> int:
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    if not days:
        raise SystemExit("--days is required")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start_ms = int(_parse_utc_iso(args.trusted_start).timestamp() * 1000) if args.trusted_start else None
    end_ms = int(_parse_utc_iso(args.end).timestamp() * 1000) if args.end else None
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--end must be later than --trusted-start")

    markets = _load_outcome_backfill_markets(
        replay_root=args.replay_root,
        days=days,
        symbols=symbols,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if args.market_limit and args.market_limit > 0:
        markets = markets[: args.market_limit]

    session = requests.Session()
    conns: Dict[str, sqlite3.Connection] = {}
    report: Dict[str, Any] = {
        "days": days,
        "symbols": symbols,
        "trusted_start": args.trusted_start,
        "end": args.end,
        "dry_run": bool(args.dry_run),
        "replay_root": args.replay_root,
        "markets_total": len(markets),
        "fetched_outcomes": 0,
        "missing_outcomes": 0,
        "fetch_errors": 0,
        "written_outcomes": 0,
        "source_counts": {},
        "winner_side_counts": {},
        "samples_missing": [],
        "samples_error": [],
        "days_result": {},
    }

    def _day_result(day: str, db_path: str) -> Dict[str, Any]:
        if day not in report["days_result"]:
            report["days_result"][day] = {"db_path": db_path}
        report["days_result"][day].setdefault("db_path", db_path)
        for key in ("markets_total", "fetched_outcomes", "missing_outcomes", "fetch_errors", "written_outcomes"):
            report["days_result"][day].setdefault(key, 0)
        return report["days_result"][day]

    try:
        if not args.dry_run:
            for day in days:
                db = _replay_db_path(args.replay_root, day)
                if not db.exists():
                    continue
                conn = sqlite3.connect(str(db))
                conn.execute("PRAGMA busy_timeout=60000")
                init_schema(conn)
                conns[day] = conn
                report["days_result"].setdefault(day, {"db_path": str(db)})
                report["days_result"][day]["xuan_outcome_side"] = _backfill_xuan_outcome_side(conn)

        for idx, market in enumerate(markets, start=1):
            day = str(market["day"])
            day_result = _day_result(day, str(market["db_path"]))
            day_result["markets_total"] += 1
            last_error: Optional[Exception] = None
            record = None
            for attempt in range(max(1, int(args.fetch_retries))):
                try:
                    record = fetch_condition_settlement(
                        str(market["condition_id"]),
                        market_slug=str(market["slug"]),
                        session=session,
                        timeout_sec=float(args.timeout_sec),
                    )
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < max(1, int(args.fetch_retries)) - 1:
                        time.sleep(min(2.0, 0.25 * (attempt + 1)))

            if last_error is not None:
                report["fetch_errors"] += 1
                day_result["fetch_errors"] += 1
                if len(report["samples_error"]) < 20:
                    report["samples_error"].append({**market, "error": str(last_error)})

            if record is None:
                report["missing_outcomes"] += 1
                day_result["missing_outcomes"] += 1
                if len(report["samples_missing"]) < 50:
                    report["samples_missing"].append(market)
            else:
                record["winner_side"] = normalize_side(record.get("winner_side")) or normalize_side(record.get("official_outcome"))
                record["capture_seq"] = 0
                source = str(record.get("resolution_source") or "unknown")
                winner_side = str(record.get("winner_side") or "unknown")
                report["source_counts"][source] = int(report["source_counts"].get(source, 0)) + 1
                report["winner_side_counts"][winner_side] = int(report["winner_side_counts"].get(winner_side, 0)) + 1
                report["fetched_outcomes"] += 1
                day_result["fetched_outcomes"] += 1
                if not args.dry_run:
                    _write_settlement_record(conns[day], record)
                    report["written_outcomes"] += 1
                    day_result["written_outcomes"] += 1

            if args.sleep_sec > 0:
                time.sleep(float(args.sleep_sec))
            if idx % max(1, args.log_every) == 0:
                LOG.info(
                    "market outcome backfill progress: %d/%d fetched=%d missing=%d errors=%d",
                    idx,
                    len(markets),
                    report["fetched_outcomes"],
                    report["missing_outcomes"],
                    report["fetch_errors"],
                )

        if not args.dry_run:
            for day, conn in conns.items():
                conn.commit()
                coverage = _settlement_coverage_summary(conn, symbols=symbols, start_ms=start_ms, end_ms=end_ms)
                report["days_result"].setdefault(day, {"db_path": str(_replay_db_path(args.replay_root, day))})
                report["days_result"][day]["post_write_coverage"] = coverage
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        else:
            for day in days:
                db = _replay_db_path(args.replay_root, day)
                if not db.exists():
                    continue
                conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                try:
                    report["days_result"].setdefault(day, {"db_path": str(db)})
                    report["days_result"][day]["pre_write_coverage"] = _settlement_coverage_summary(
                        conn, symbols=symbols, start_ms=start_ms, end_ms=end_ms
                    )
                finally:
                    conn.close()
    finally:
        for conn in conns.values():
            conn.close()

    report["fetch_success_ratio"] = round(report["fetched_outcomes"] / report["markets_total"], 6) if report["markets_total"] else 0.0
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("market_outcome_backfill: %s", json.dumps(report, ensure_ascii=False))
    return 0 if report["fetch_errors"] == 0 and report["fetched_outcomes"] > 0 else 2


def cmd_backfill_xuan_market_public(args: argparse.Namespace) -> int:
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    if not days:
        raise SystemExit("--days is required")
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    start_ms = int(_parse_utc_iso(args.start).timestamp() * 1000) if args.start else None
    end_ms = int(_parse_utc_iso(args.end).timestamp() * 1000) if args.end else None
    if start_ms is not None and end_ms is not None and end_ms <= start_ms:
        raise SystemExit("--end must be later than --start")

    markets = _load_xuan_backfill_markets(
        replay_root=args.replay_root,
        days=days,
        symbols=symbols,
        start_ms=start_ms,
        end_ms=end_ms,
    )
    if args.market_limit and args.market_limit > 0:
        markets = markets[: args.market_limit]

    session = requests.Session()
    raw_store: Optional[RawCaptureStore] = None if args.dry_run else RawCaptureStore(args.raw_root)
    totals = {
        "markets": len(markets),
        "trade_rows": 0,
        "activity_rows": 0,
        "trade_page_cap_hits": 0,
        "activity_page_cap_hits": 0,
    }
    samples: List[Dict[str, Any]] = []
    poll_ts_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)

    try:
        for idx, market in enumerate(markets, start=1):
            condition_id = market["condition_id"]
            trade_rows, trade_summary = _fetch_xuan_market_rows(
                session=session,
                url=POLYMARKET_DATA_TRADES_URL,
                user=args.user,
                condition_id=condition_id,
                page_limit=args.page_limit,
                max_pages=args.max_pages,
                include_taker_only_false=True,
                timeout_sec=args.timeout_sec,
            )
            activity_rows, activity_summary = _fetch_xuan_market_rows(
                session=session,
                url=POLYMARKET_DATA_ACTIVITY_URL,
                user=args.user,
                condition_id=condition_id,
                page_limit=args.page_limit,
                max_pages=args.max_pages,
                include_taker_only_false=False,
                timeout_sec=args.timeout_sec,
            )

            totals["trade_rows"] += int(trade_summary["rows"])
            totals["activity_rows"] += int(activity_summary["rows"])
            totals["trade_page_cap_hits"] += 1 if trade_summary["page_cap_hit"] else 0
            totals["activity_page_cap_hits"] += 1 if activity_summary["page_cap_hit"] else 0
            if trade_rows or activity_rows or len(samples) < 20:
                samples.append(
                    {
                        **market,
                        "trade_rows": trade_summary["rows"],
                        "activity_rows": activity_summary["rows"],
                        "trade_page_cap_hit": trade_summary["page_cap_hit"],
                        "activity_page_cap_hit": activity_summary["page_cap_hit"],
                    }
                )

            if raw_store is not None:
                log_recv_ms = int(market["end_ms"])
                _write_xuan_backfill_rows(
                    raw_store=raw_store,
                    user=args.user,
                    endpoint="trades",
                    rows=trade_rows,
                    poll_ts_ms=log_recv_ms,
                    log_recv_ms=log_recv_ms,
                )
                _write_xuan_backfill_rows(
                    raw_store=raw_store,
                    user=args.user,
                    endpoint="activity",
                    rows=activity_rows,
                    poll_ts_ms=log_recv_ms,
                    log_recv_ms=log_recv_ms,
                )

            if args.sleep_sec > 0:
                time.sleep(float(args.sleep_sec))
            if idx % max(1, args.log_every) == 0:
                LOG.info(
                    "xuan market backfill progress: %d/%d trade_rows=%d activity_rows=%d",
                    idx,
                    len(markets),
                    totals["trade_rows"],
                    totals["activity_rows"],
                )
    finally:
        if raw_store is not None:
            raw_store.close()

    report = {
        "user": args.user,
        "days": days,
        "symbols": symbols,
        "start": args.start,
        "end": args.end,
        "dry_run": bool(args.dry_run),
        "replay_root": args.replay_root,
        "raw_root": None if args.dry_run else args.raw_root,
        "page_limit": args.page_limit,
        "max_pages": args.max_pages,
        "totals": totals,
        "samples": samples[:100],
    }
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("xuan_market_backfill_public: %s", json.dumps(report, ensure_ascii=False))
    return 2 if totals["trade_page_cap_hits"] or totals["activity_page_cap_hits"] else 0


def cmd_merge_xuan_raw_into_replay(args: argparse.Namespace) -> int:
    from .capture.envelope import RawEnvelope
    from .replay.normalize import normalize_xuan_activity, normalize_xuan_poll_log, normalize_xuan_trade
    from .utils.io import iter_jsonl_gz

    days = [d.strip() for d in args.days.split(",") if d.strip()]
    if not days:
        raise SystemExit("--days is required")

    report: Dict[str, Any] = {
        "days": days,
        "raw_root": args.raw_root,
        "replay_root": args.replay_root,
        "dry_run": bool(args.dry_run),
        "day_results": {},
    }
    totals = {
        "xuan_trades_inserted": 0,
        "xuan_activity_inserted": 0,
        "xuan_poll_log_inserted": 0,
        "dedup_skips": 0,
        "raw_records": 0,
    }

    for day in days:
        db = _replay_db_path(args.replay_root, day)
        raw_dir = Path(args.raw_root) / day / SOURCE_KIND_XUAN_POLL
        day_result = {
            "db": str(db),
            "raw_dir": str(raw_dir),
            "raw_records": 0,
            "xuan_trades_inserted": 0,
            "xuan_activity_inserted": 0,
            "xuan_poll_log_inserted": 0,
            "dedup_skips": 0,
            "missing_db": not db.exists(),
            "missing_raw_dir": not raw_dir.exists(),
        }
        report["day_results"][day] = day_result
        if not db.exists() or not raw_dir.exists():
            continue

        conn = sqlite3.connect(str(db))
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA busy_timeout=60000")
            if not args.dry_run:
                init_schema(conn)
            trade_seen = {
                (
                    row[0],
                    row[1] or "",
                    row[2] or "",
                    row[3],
                    row[4] or "",
                    row[5],
                    row[6],
                )
                for row in cur.execute(
                    """
                    SELECT user, tx_hash, trade_id, trade_ts_ms, condition_id, price, size
                    FROM xuan_trades
                    """
                )
            }
            activity_seen = {
                (
                    row[0],
                    row[1] or "",
                    row[2],
                    row[3] or "",
                    row[4] or "",
                )
                for row in cur.execute(
                    """
                    SELECT user, tx_hash, activity_ts_ms, activity_type, condition_id
                    FROM xuan_activity
                    """
                )
            }
            poll_seen = {
                (row[0], row[1], row[2])
                for row in cur.execute("SELECT user, endpoint, poll_ts_ms FROM xuan_poll_log")
            }

            for path in sorted(raw_dir.glob("*.jsonl.gz")):
                for obj in iter_jsonl_gz(path):
                    env = RawEnvelope.from_dict(obj)
                    day_result["raw_records"] += 1
                    totals["raw_records"] += 1
                    if env.channel == CHANNEL_XUAN_TRADES:
                        rec = normalize_xuan_trade(env)
                        if not rec:
                            continue
                        key = (
                            rec["user"],
                            rec["tx_hash"] or "",
                            rec["trade_id"] or "",
                            rec["trade_ts_ms"],
                            rec["condition_id"] or "",
                            rec["price"],
                            rec["size"],
                        )
                        if key in trade_seen:
                            day_result["dedup_skips"] += 1
                            totals["dedup_skips"] += 1
                            continue
                        trade_seen.add(key)
                        if not args.dry_run:
                            cur.execute(
                                """
                                INSERT INTO xuan_trades (
                                    user, poll_ts_ms, trade_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                                    condition_id, slug, event_slug, title, outcome, outcome_side, side,
                                    price, size, asset, proxy_wallet, tx_hash, trade_id,
                                    source_quality, raw_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    rec["user"],
                                    rec["poll_ts_ms"],
                                    rec["trade_ts_ms"],
                                    rec["recv_ms"],
                                    rec["recv_monotonic_ns"],
                                    rec["capture_seq"],
                                    rec["condition_id"],
                                    rec["slug"],
                                    rec["event_slug"],
                                    rec["title"],
                                    rec["outcome"],
                                    rec["outcome_side"],
                                    rec["side"],
                                    rec["price"],
                                    rec["size"],
                                    rec["asset"],
                                    rec["proxy_wallet"],
                                    rec["tx_hash"],
                                    rec["trade_id"],
                                    rec["source_quality"],
                                    rec["raw_json"],
                                ),
                            )
                        day_result["xuan_trades_inserted"] += 1
                        totals["xuan_trades_inserted"] += 1
                    elif env.channel == CHANNEL_XUAN_ACTIVITY:
                        rec = normalize_xuan_activity(env)
                        if not rec:
                            continue
                        key = (
                            rec["user"],
                            rec["tx_hash"] or "",
                            rec["activity_ts_ms"],
                            rec["activity_type"] or "",
                            rec["condition_id"] or "",
                        )
                        if key in activity_seen:
                            day_result["dedup_skips"] += 1
                            totals["dedup_skips"] += 1
                            continue
                        activity_seen.add(key)
                        if not args.dry_run:
                            cur.execute(
                                """
                                INSERT INTO xuan_activity (
                                    user, poll_ts_ms, activity_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                                    condition_id, slug, event_slug, title, activity_type, outcome, outcome_side, side,
                                    price, size, usdc_size, asset, proxy_wallet, tx_hash,
                                    source_quality, raw_json
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    rec["user"],
                                    rec["poll_ts_ms"],
                                    rec["activity_ts_ms"],
                                    rec["recv_ms"],
                                    rec["recv_monotonic_ns"],
                                    rec["capture_seq"],
                                    rec["condition_id"],
                                    rec["slug"],
                                    rec["event_slug"],
                                    rec["title"],
                                    rec["activity_type"],
                                    rec["outcome"],
                                    rec["outcome_side"],
                                    rec["side"],
                                    rec["price"],
                                    rec["size"],
                                    rec["usdc_size"],
                                    rec["asset"],
                                    rec["proxy_wallet"],
                                    rec["tx_hash"],
                                    rec["source_quality"],
                                    rec["raw_json"],
                                ),
                            )
                        day_result["xuan_activity_inserted"] += 1
                        totals["xuan_activity_inserted"] += 1
                    elif env.channel == CHANNEL_XUAN_POLL_LOG:
                        rec = normalize_xuan_poll_log(env)
                        if not rec:
                            continue
                        key = (rec["user"], rec["endpoint"], rec["poll_ts_ms"])
                        if key in poll_seen:
                            day_result["dedup_skips"] += 1
                            totals["dedup_skips"] += 1
                            continue
                        poll_seen.add(key)
                        if not args.dry_run:
                            cur.execute(
                                """
                                INSERT INTO xuan_poll_log (
                                    user, endpoint, poll_ts_ms, recv_ms, recv_monotonic_ns, capture_seq,
                                    rows, max_ts_ms, ok, error
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    rec["user"],
                                    rec["endpoint"],
                                    rec["poll_ts_ms"],
                                    rec["recv_ms"],
                                    rec["recv_monotonic_ns"],
                                    rec["capture_seq"],
                                    rec["rows"],
                                    rec["max_ts_ms"],
                                    rec["ok"],
                                    rec["error"],
                                ),
                            )
                        day_result["xuan_poll_log_inserted"] += 1
                        totals["xuan_poll_log_inserted"] += 1
            if not args.dry_run:
                conn.commit()
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()

    report["totals"] = totals
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("merge_xuan_raw_into_replay: %s", json.dumps(report, ensure_ascii=False))
    return 0


def cmd_backfill_xuan_public(args: argparse.Namespace) -> int:
    start_dt = _parse_utc_iso(args.start)
    end_dt = _parse_utc_iso(args.end)
    if end_dt <= start_dt:
        raise SystemExit("--end must be later than --start")

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    session = requests.Session()
    trade_rows, trade_summary = _fetch_xuan_window(
        session=session,
        url=POLYMARKET_DATA_TRADES_URL,
        user=args.user,
        start_ms=start_ms,
        end_ms=end_ms,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        include_taker_only_false=True,
        timeout_sec=args.timeout_sec,
    )
    activity_rows, activity_summary = _fetch_xuan_window(
        session=session,
        url=POLYMARKET_DATA_ACTIVITY_URL,
        user=args.user,
        start_ms=start_ms,
        end_ms=end_ms,
        page_limit=args.page_limit,
        max_pages=args.max_pages,
        include_taker_only_false=False,
        timeout_sec=args.timeout_sec,
    )
    can_cover = bool(trade_summary["target_window_complete"] and activity_summary["target_window_complete"])
    report = {
        "user": args.user,
        "start": start_dt.isoformat(),
        "end": end_dt.isoformat(),
        "dry_run": bool(args.dry_run),
        "can_cover_target_window": can_cover,
        "trades": trade_summary,
        "activity": activity_summary,
    }

    if not args.dry_run:
        raw_store = RawCaptureStore(args.raw_root)
        poll_ts_ms = int(dt.datetime.now(dt.timezone.utc).timestamp() * 1000)
        try:
            _write_xuan_backfill_rows(
                raw_store=raw_store,
                user=args.user,
                endpoint="trades",
                rows=trade_rows,
                poll_ts_ms=poll_ts_ms,
            )
            _write_xuan_backfill_rows(
                raw_store=raw_store,
                user=args.user,
                endpoint="activity",
                rows=activity_rows,
                poll_ts_ms=poll_ts_ms,
            )
        finally:
            raw_store.close()
        report["raw_root"] = args.raw_root

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    LOG.info("xuan_backfill_public: %s", json.dumps(report, ensure_ascii=False))
    return 0 if can_cover else 2


def cmd_audit_replay_market(args: argparse.Namespace) -> int:
    days = [d.strip() for d in args.days.split(",") if d.strip()]
    if not days:
        raise SystemExit("--days is required, e.g. --days 2026-04-27,2026-04-28")
    min_db_bytes = max(0, int(args.min_db_bytes))
    replay_root = Path(args.replay_root)

    if not args.skip_safety_gate:
        ok, failures = safety_gate(
            replay_root=replay_root,
            days=days,
            min_db_bytes=min_db_bytes,
            min_mem_available_kib=max(0, int(args.min_mem_available_mib)) * 1024,
            min_disk_free_bytes=max(0, int(args.min_disk_free_gb)) * 1024**3,
            max_load_1m=float(args.max_load_1m),
        )
        if not ok:
            LOG.error("audit safety gate failed: %s", ", ".join(failures))
            return 3

    config = AuditConfig(
        raw_root=Path(args.raw_root),
        replay_root=replay_root,
        days=days,
        min_db_bytes=min_db_bytes,
        raw_trade_max_records=max(0, int(args.raw_trade_max_records)),
        raw_book_max_records=max(0, int(args.raw_book_max_records)),
        taker_side_null_max_ratio=float(args.taker_side_null_max_ratio),
        trusted_start_ms=int(_parse_utc_iso(args.trusted_start).timestamp() * 1000) if args.trusted_start else None,
        outcome_symbols=[s.strip().upper() for s in args.outcome_symbols.split(",") if s.strip()],
        min_official_outcome_coverage=float(args.min_official_outcome_coverage),
    )
    report = run_market_replay_audit(config)
    save_audit_report(report, Path(args.output), Path(args.markdown_output) if args.markdown_output else None)
    LOG.info("saved replay market audit report -> %s", args.output)
    LOG.info("replay market audit verdict: %s", json.dumps(report["final_verdict"], ensure_ascii=False))
    return 0 if report["final_verdict"].get("market_replay_trusted") else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cfdata",
        description="Completion-first raw/replay data pipeline tools",
    )
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])

    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init-layout", help="Create canonical project directories and config template")
    p.add_argument("--root", default=".")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init_layout)

    p = sub.add_parser("capture-meta", help="Capture crypto 5m market_meta from Gamma API")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--active-only", dest="active_only", action="store_true", default=True)
    p.add_argument("--include-inactive", dest="active_only", action="store_false")
    p.add_argument("--loop", action="store_true")
    p.add_argument("--interval-sec", type=int, default=20)
    p.add_argument("--no-conditional-get", action="store_true", help="Disable If-Modified-Since/304 polling")
    p.set_defaults(func=cmd_capture_meta)

    p = sub.add_parser("backfill-trades", help="Round-close backfill trades from Data API")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--condition-id", action="append")
    p.add_argument("--condition-file")
    p.add_argument("--min-ts-ms", type=int)
    p.add_argument("--max-ts-ms", type=int)
    p.set_defaults(func=cmd_backfill_trades)

    p = sub.add_parser("capture-ingest", help="Ingest NDJSON into raw envelope files")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--input", default="-", help="NDJSON input file path or '-' for stdin")
    p.add_argument("--source")
    p.add_argument("--channel")
    p.add_argument("--condition-id")
    p.set_defaults(func=cmd_capture_ingest)

    p = sub.add_parser("capture-sidecar", help="Run websocket + meta poll sidecar")
    p.add_argument("--config", default="config/capture.sources.example.json")
    p.add_argument("--raw-root", help="Override raw_root in config")
    p.add_argument("--duration-sec", type=int, help="Optional run duration")
    p.set_defaults(func=cmd_capture_sidecar)

    p = sub.add_parser(
        "capture-sidecar-env",
        help="Run sidecar using runtime env file (no config file generation)",
    )
    p.add_argument("--env-file", default="config/research.env")
    p.add_argument("--raw-root")
    p.add_argument("--duration-sec", type=int)
    p.add_argument("--market-prefix", action="append", help="Override CF_MARKET_PREFIXES")
    p.add_argument(
        "--market-channel",
        action="append",
        help="Override CF_MARKET_CHANNELS (repeatable). Supported: book,last_trade_price,best_bid_ask",
    )
    p.add_argument("--max-markets-per-prefix", type=int, help="Cap subscriptions per prefix (default from CF_MAX_MARKETS_PER_PREFIX)")
    p.add_argument("--disable-market-ws", action="store_true")
    p.add_argument("--disable-user-ws", action="store_true")
    p.add_argument("--meta-interval-sec", type=int, help="Override CF_META_INTERVAL_SEC")
    p.add_argument("--meta-switch-delay-sec", type=int, help="Delay subscription switch after round change")
    p.add_argument("--meta-active-only", dest="meta_active_only", action="store_true", default=None)
    p.add_argument("--meta-include-inactive", dest="meta_active_only", action="store_false")
    p.add_argument("--disable-meta-conditional-get", action="store_true")
    p.add_argument("--settlement-poll-enabled", dest="settlement_poll_enabled", action="store_true", default=None)
    p.add_argument("--disable-settlement-poll", action="store_true")
    p.add_argument("--settlement-poll-sec", type=int)
    p.add_argument("--settlement-poll-cooldown-sec", type=int)
    p.add_argument("--xuan-poll-enabled", dest="xuan_poll_enabled", action="store_true", default=None)
    p.add_argument("--disable-xuan-poll", action="store_true")
    p.add_argument("--xuan-user")
    p.add_argument("--xuan-poll-sec", type=int)
    p.add_argument("--xuan-poll-aggressive-sec", type=int)
    p.add_argument("--xuan-poll-aggressive-threshold", type=int)
    p.add_argument("--xuan-poll-page-limit", type=int)
    p.add_argument("--xuan-poll-max-pages", type=int)
    p.add_argument("--xuan-cursor-path")
    p.add_argument("--debug-raw-market-ws", action="store_true")
    p.set_defaults(func=cmd_capture_sidecar_env)

    p = sub.add_parser("build-replay", help="Build replay sqlite for one UTC day")
    p.add_argument("--raw-root")
    p.add_argument("--replay-root")
    p.add_argument("--day", help="UTC day YYYY-MM-DD")
    p.set_defaults(func=cmd_build_replay)

    p = sub.add_parser("build-replay-rolling", help="Build replay sqlite for days in a rolling UTC window")
    p.add_argument("--raw-root")
    p.add_argument("--replay-root")
    p.add_argument("--hours", type=int, default=24, help="Rolling window size in hours")
    p.add_argument(
        "--validate-latest",
        action="store_true",
        help="Validate the latest built UTC day from the same rolling window snapshot",
    )
    p.add_argument(
        "--gap-threshold-ms",
        type=int,
        default=0,
        help="Optional max allowed intra-round gap for --validate-latest (<=0 disables)",
    )
    p.set_defaults(func=cmd_build_replay_rolling)

    p = sub.add_parser("validate-replay", help="Validate replay DB against BTC 5m public-capture gates")
    p.add_argument("--replay-root")
    p.add_argument("--day", help="UTC day YYYY-MM-DD")
    p.add_argument("--db-path", help="Override explicit sqlite path")
    p.add_argument("--gap-threshold-ms", type=int, default=0, help="Optional max allowed intra-round gap (<=0 disables)")
    p.add_argument("--output", help="Save report json path")
    p.set_defaults(func=cmd_validate_replay)

    p = sub.add_parser("audit-startup", help="Run startup readiness audit checks for 1h pre-launch gate")
    p.add_argument("--replay-root")
    p.add_argument("--day", help="UTC day YYYY-MM-DD")
    p.add_argument("--db-path", help="Override explicit sqlite path")
    p.add_argument("--output", help="Save report json path")
    p.add_argument("--require-user-truth", action="store_true")
    p.add_argument("--taker-side-null-max-ratio", type=float, default=0.05)
    p.add_argument("--min-market-meta-rounds", type=int, default=12)
    p.add_argument("--min-settlement-rows", type=int, default=1)
    p.add_argument("--min-xuan-poll-points", type=int, default=12)
    p.add_argument("--max-abs-avg-trade-latency-ms", type=int, default=60000)
    p.set_defaults(func=cmd_audit_startup)

    p = sub.add_parser("audit-replay-market", help="Run read-only market-side replay credibility audit")
    p.add_argument("--days", required=True, help="Comma-separated UTC days, e.g. 2026-04-27,2026-04-28")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--replay-root", default="data/replay")
    p.add_argument("--output", default="data/replay/audits/replay_audit_report.json")
    p.add_argument("--markdown-output", default="data/replay/audits/replay_audit_report.md")
    p.add_argument("--min-db-bytes", type=int, default=100 * 1024 * 1024)
    p.add_argument("--raw-trade-max-records", type=int, default=1_000_000)
    p.add_argument("--raw-book-max-records", type=int, default=250_000)
    p.add_argument("--taker-side-null-max-ratio", type=float, default=0.05)
    p.add_argument("--trusted-start", help="Optional UTC ISO/ms trusted capture start; earlier BTC gaps are ignored")
    p.add_argument("--outcome-symbols", default="BTC,ETH,SOL,XRP", help="Comma-separated symbols for outcome coverage audit")
    p.add_argument("--min-official-outcome-coverage", type=float, default=0.99)
    p.add_argument("--min-mem-available-mib", type=int, default=1536)
    p.add_argument("--min-disk-free-gb", type=int, default=100)
    p.add_argument("--max-load-1m", type=float, default=1.5)
    p.add_argument(
        "--skip-safety-gate",
        action="store_true",
        help="Skip process/resource gate; intended only for local fixtures/tests",
    )
    p.set_defaults(func=cmd_audit_replay_market)

    p = sub.add_parser("backfill-xuan-public", help="Dry-run or write xuan public Data API history into raw")
    p.add_argument("--user", required=True)
    p.add_argument("--start", required=True, help="UTC ISO timestamp, e.g. 2026-04-27T00:00:00Z")
    p.add_argument("--end", required=True, help="UTC ISO timestamp, e.g. 2026-04-29T00:00:00Z")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--page-limit", type=int, default=500)
    p.add_argument("--max-pages", type=int, default=30)
    p.add_argument("--timeout-sec", type=int, default=20)
    p.add_argument("--output", help="Optional JSON report path")
    p.set_defaults(func=cmd_backfill_xuan_public)

    p = sub.add_parser(
        "backfill-market-outcomes",
        help="Backfill official YES/NO outcomes from public market metadata into replay settlement_records",
    )
    p.add_argument("--days", required=True, help="Comma-separated UTC days with replay market_meta")
    p.add_argument("--symbols", default="BTC,ETH,SOL,XRP", help="Comma-separated symbols, default: BTC,ETH,SOL,XRP")
    p.add_argument("--trusted-start", help="Optional UTC ISO lower bound for market windows")
    p.add_argument("--end", help="Optional UTC ISO upper bound for market windows")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--replay-root", default="data/replay")
    p.add_argument("--market-limit", type=int, default=0, help="Limit markets for validation; 0 means all")
    p.add_argument("--timeout-sec", type=float, default=15.0)
    p.add_argument("--fetch-retries", type=int, default=3, help="Retry transient outcome API failures per market")
    p.add_argument("--sleep-sec", type=float, default=0.02, help="Polite pause between Gamma requests")
    p.add_argument("--log-every", type=int, default=100)
    p.add_argument("--output", help="Optional JSON report path")
    p.set_defaults(func=cmd_backfill_market_outcomes)

    p = sub.add_parser(
        "backfill-xuan-market-public",
        help="Backfill xuan public Data API history market-by-market from replay market_meta",
    )
    p.add_argument("--user", required=True)
    p.add_argument("--days", required=True, help="Comma-separated UTC days with replay market_meta")
    p.add_argument("--symbols", default="BTC", help="Comma-separated symbols to backfill, default: BTC")
    p.add_argument("--start", help="Optional UTC ISO lower bound for market windows")
    p.add_argument("--end", help="Optional UTC ISO upper bound for market windows")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--replay-root", default="data/replay")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--page-limit", type=int, default=500)
    p.add_argument("--max-pages", type=int, default=20)
    p.add_argument("--timeout-sec", type=int, default=20)
    p.add_argument("--market-limit", type=int, default=0, help="Limit markets for validation; 0 means all")
    p.add_argument("--sleep-sec", type=float, default=0.02, help="Polite pause between market queries")
    p.add_argument("--log-every", type=int, default=100)
    p.add_argument("--output", help="Optional JSON report path")
    p.set_defaults(func=cmd_backfill_xuan_market_public)

    p = sub.add_parser(
        "merge-xuan-raw-into-replay",
        help="Merge existing raw xuan_poll files into replay xuan_* tables without rebuilding market data",
    )
    p.add_argument("--days", required=True, help="Comma-separated UTC days")
    p.add_argument("--raw-root", default="data/raw")
    p.add_argument("--replay-root", default="data/replay")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--output", help="Optional JSON report path")
    p.set_defaults(func=cmd_merge_xuan_raw_into_replay)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="[%(asctime)s] %(levelname)s %(message)s")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

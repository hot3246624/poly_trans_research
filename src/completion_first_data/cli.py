"""CLI for completion-first data acquisition and replay pipeline."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import logging
import os
import time
from pathlib import Path
from typing import Iterable, List, Optional

from .capture.ingest import ingest_ndjson
from .capture.meta import MarketMetaFetchState, backfill_trades_once, capture_market_meta_once
from .capture.raw_store import RawCaptureStore
from .capture.websocket_sidecar import (
    MetaPollConfig,
    SettlementPollConfig,
    SidecarConfig,
    UserWsConfig,
    WsSourceConfig,
    load_sidecar_config,
    run_sidecar,
)
from .capture.xuan_poller import XuanPollConfig
from .quality.startup_audit import save_startup_audit_report, run_startup_audit
from .quality.validator import save_report, validate_replay_db
from .replay.builder import build_replay_for_day
from .user_truth import resolve_user_auth_config

LOG = logging.getLogger("completion_first_data")


def _default_day() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")


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

    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(hours=hours)
    days: List[str] = []
    cursor = start.date()
    while cursor <= now.date():
        days.append(cursor.strftime("%Y-%m-%d"))
        cursor += dt.timedelta(days=1)

    for day in days:
        stats = build_replay_for_day(Path(raw_root), Path(replay_root), day)
        LOG.info("rolling build done [%s]: %s", day, json.dumps(stats.as_dict(), ensure_ascii=False))
    return 0


def cmd_validate_replay(args: argparse.Namespace) -> int:
    replay_root = args.replay_root or os.getenv("CF_REPLAY_ROOT", "data/replay")
    if args.db_path:
        db = Path(args.db_path)
    else:
        day = args.day or _default_day()
        db = Path(replay_root) / day / "crypto_5m.sqlite"

    report = validate_replay_db(db, gap_threshold_ms=args.gap_threshold_ms)
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
        db = Path(replay_root) / day / "crypto_5m.sqlite"

    report = run_startup_audit(
        db,
        require_user_truth=args.require_user_truth,
        taker_side_null_max_ratio=args.taker_side_null_max_ratio,
        min_market_meta_rounds=args.min_market_meta_rounds,
        min_settlement_rows=args.min_settlement_rows,
        min_xuan_poll_points=args.min_xuan_poll_points,
        max_abs_avg_trade_latency_ms=args.max_abs_avg_trade_latency_ms,
    )
    if args.output:
        save_startup_audit_report(report, Path(args.output))
        LOG.info("saved startup audit report -> %s", args.output)

    LOG.info("startup_audit: %s", json.dumps(report.as_dict(), ensure_ascii=False))
    return 0 if report.all_passed else 2


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

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="[%(asctime)s] %(levelname)s %(message)s")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

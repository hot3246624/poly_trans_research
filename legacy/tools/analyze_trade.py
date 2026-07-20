#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from interactive_chart import generate_chart, generate_html_table
from output_paths import prepare_output_bundle
from trade_analysis import (
    CACHE_ROOT,
    FUNDER_ADDRESS_ALIASES,
    calculate_summary,
    calculate_table_metrics,
    calculate_trade_summary,
    describe_trade_source,
    fetch_trades_detailed,
    format_ts,
    normalize_market_text,
    parse_trades,
    resolve_market_identifier,
    trade_source_warning,
    _first_env,
    _load_env,
)


RECENT_USER_PATH = CACHE_ROOT / "recent_user.txt"


def _write_chart_placeholder(path: Path, message: str) -> None:
    escaped = (
        message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    path.write_text(
        f"""<!doctype html>
<html>
<head><meta charset="utf-8"><title>Chart unavailable</title></head>
<body style="font-family: monospace; background: #111; color: #eee; padding: 24px;">
<h2>Chart unavailable</h2>
<p>{escaped}</p>
<p>The analysis table was generated independently.</p>
</body>
</html>
""",
    )


def _recent_user() -> str:
    try:
        return RECENT_USER_PATH.read_text().strip()
    except OSError:
        return ""


def _remember_user(address: str) -> None:
    if not address:
        return
    RECENT_USER_PATH.parent.mkdir(parents=True, exist_ok=True)
    RECENT_USER_PATH.write_text(address.strip())


def _default_user() -> str:
    env_user = _first_env(_load_env(), FUNDER_ADDRESS_ALIASES)
    return env_user or _recent_user()


def _warning_from_meta(meta: dict[str, Any], trades: list[dict[str, Any]]) -> str:
    warnings = [str(w) for w in meta.get("warnings", []) if w]
    return "; ".join(warnings) or trade_source_warning(trades)


def _analysis_metadata(
    *,
    market_title: str,
    condition_id: str,
    user_address: str,
    parsed: list[dict[str, Any]],
    fetch_meta: dict[str, Any],
    raw_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "market_title": market_title,
        "condition_id": condition_id or "N/A",
        "user_address": user_address or "N/A",
        "trade_time_range": (
            f"{parsed[0]['dt_et'].strftime('%Y-%m-%d %I:%M:%S %p %Z')} - "
            f"{parsed[-1]['dt_et'].strftime('%Y-%m-%d %I:%M:%S %p %Z')}"
        ),
        "trade_count": len(parsed),
        "data_source": fetch_meta.get("data_source") or describe_trade_source(raw_trades),
        "data_warning": _warning_from_meta(fetch_meta, raw_trades),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Analyze a Polymarket market/user trade history and generate HTML outputs.",
    )
    parser.add_argument("market", help="Polymarket URL, slug, conditionId, or market name")
    parser.add_argument("-u", "--user", help="User/proxy wallet address. Defaults to env funder or last used user.")
    parser.add_argument(
        "--source",
        choices=("auto", "public", "authenticated"),
        default="auto",
        help="Data source preference. auto uses authenticated only when the configured funder matches the requested user.",
    )
    parser.add_argument("--refresh", action="store_true", help="Bypass fetch cache and refresh the API result.")
    parser.add_argument("--no-cache", action="store_true", help="Disable local fetch cache for this run.")
    parser.add_argument(
        "--plotly-js",
        choices=("cdn", "inline"),
        default="cdn",
        help="Use CDN Plotly JS for smaller chart.html, or inline for offline HTML.",
    )
    parser.add_argument("--skip-chart", action="store_true", help="Generate analysis_table.html without chart.html dependencies.")
    parser.add_argument("--open", dest="open_result", action="store_true", help="Open analysis_table.html after generation.")
    parser.add_argument("--no-open", dest="open_result", action="store_false", help="Do not open the generated HTML.")
    parser.set_defaults(open_result=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    user_address = (args.user or _default_user()).strip()
    if not user_address:
        if sys.stdin.isatty():
            user_address = input("Enter user address: ").strip()
        if not user_address:
            print("User address is required. Pass --user once; it will be remembered.", file=sys.stderr)
            return 2

    print(f"Resolving market: {args.market}")
    event, market, identifier = resolve_market_identifier(args.market)
    if not market:
        print(f"Market not found: {identifier or args.market}", file=sys.stderr)
        return 1

    condition_id = market.get("conditionId") or ""
    market_title = market.get("question") or market.get("title") or (event or {}).get("title") or condition_id
    print(f"Matched market: {market_title}")
    print(f"Condition ID: {condition_id}")
    print(f"User: {user_address}")

    fetch_result = fetch_trades_detailed(
        condition_id,
        user_address,
        source=args.source,
        page_limit=1000,
        verbose=True,
        use_cache=not args.no_cache,
        refresh_cache=args.refresh,
    )
    raw_trades = fetch_result.trades
    fetch_meta = dict(fetch_result.meta)
    fetch_meta.update(
        {
            "market_query": args.market,
            "market_identifier": identifier,
            "market_title": market_title,
            "market_slug": market.get("slug") or normalize_market_text(market_title),
        }
    )
    if not raw_trades:
        print("No trades returned for that user/market.")
        if fetch_meta.get("fallback_reason"):
            print(f"Reason: {fetch_meta['fallback_reason']}")
        return 1

    _remember_user(user_address)

    output_bundle = prepare_output_bundle(raw_trades, market_title=market_title, user_address=user_address)
    output_bundle.trades_json.write_text(json.dumps(raw_trades, indent=2, ensure_ascii=False))

    parsed = parse_trades(raw_trades)
    if args.skip_chart:
        _write_chart_placeholder(output_bundle.chart_html, "Chart generation was skipped by --skip-chart.")
        fetch_meta["chart_status"] = "skipped"
    else:
        try:
            generate_chart(
                parsed,
                market_title,
                output_bundle.chart_html,
                include_plotlyjs=("cdn" if args.plotly_js == "cdn" else True),
            )
            fetch_meta["chart_status"] = "generated"
        except Exception as exc:
            chart_error = f"{type(exc).__name__}: {exc}"
            _write_chart_placeholder(output_bundle.chart_html, chart_error)
            fetch_meta["chart_status"] = "failed"
            fetch_meta["chart_error"] = chart_error
            print(f"Chart generation skipped: {chart_error}", file=sys.stderr)

    output_bundle.fetch_meta_json.write_text(json.dumps(fetch_meta, indent=2, ensure_ascii=False))

    table_rows = calculate_table_metrics(parsed)
    summary = calculate_summary(table_rows)
    totals = calculate_trade_summary(parsed)
    metadata = _analysis_metadata(
        market_title=market_title,
        condition_id=condition_id,
        user_address=user_address,
        parsed=parsed,
        fetch_meta=fetch_meta,
        raw_trades=raw_trades,
    )
    generate_html_table(table_rows, output_bundle.analysis_html, summary=summary, metadata=metadata, totals=totals)

    print(f"Saved {len(raw_trades)} trades to {output_bundle.trades_json}")
    print(f"Fetch metadata: {output_bundle.fetch_meta_json}")
    print(f"Chart: {output_bundle.chart_html}")
    print(f"Analysis table: {output_bundle.analysis_html}")
    print(f"Data source: {metadata['data_source']}")
    warning = metadata.get("data_warning")
    if warning:
        print(f"Warning: {warning}")
    print(f"Time range ET: {format_ts(parsed[0]['timestamp'])} to {format_ts(parsed[-1]['timestamp'])}")

    if args.open_result:
        webbrowser.open(output_bundle.analysis_html.as_uri())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

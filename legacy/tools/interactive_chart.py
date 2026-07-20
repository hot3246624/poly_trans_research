#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import math
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from output_paths import prepare_output_bundle
from trade_analysis import (
    calculate_position_series,
    calculate_summary,
    calculate_table_metrics,
    calculate_trade_summary,
    describe_trade_source,
    fetch_trades_detailed,
    parse_trades,
    resolve_market_identifier,
    trade_source_warning,
)


STYLES = {
    ("Buy", "Up"): dict(color="#008f00", symbol="x", name="Buy YES"),
    ("Sell", "Up"): dict(color="#00c800", symbol="circle-open", name="Sell YES"),
    ("Buy", "Down"): dict(color="#d000d0", symbol="x", name="Buy NO"),
    ("Sell", "Down"): dict(color="#d40000", symbol="circle-open", name="Sell NO"),
}


def _weighted_avg_price(group: list[dict]) -> float:
    total_shares = sum(t["shares"] for t in group)
    if total_shares <= 0:
        return sum(t["price"] for t in group) / max(len(group), 1)
    return sum(t["price"] * t["shares"] for t in group) / total_shares


def _money_class(value: float) -> str:
    if value > 1e-9:
        return "pos"
    if value < -1e-9:
        return "neg"
    return "neu"


def _build_trade_nodes(parsed: list[dict]) -> list[dict]:
    grouped: dict[int, list[dict]] = {}
    for trade in parsed:
        grouped.setdefault(trade["timestamp"], []).append(trade)

    nodes: list[dict] = []
    for ts in sorted(grouped):
        group = grouped[ts]
        count = len(group)
        dt_value = group[0]["time_axis_label"]
        hover_time = group[0]["time_label"]
        avg_price = _weighted_avg_price(group)
        total_shares = sum(t["shares"] for t in group)

        yes_buy = sum(t["shares"] for t in group if t["side"] == "Up" and t["type"] == "Buy")
        yes_sell = sum(t["shares"] for t in group if t["side"] == "Up" and t["type"] == "Sell")
        no_buy = sum(t["shares"] for t in group if t["side"] == "Down" and t["type"] == "Buy")
        no_sell = sum(t["shares"] for t in group if t["side"] == "Down" and t["type"] == "Sell")

        header = [
            f"YES buy/sell: {yes_buy:.2f}/{yes_sell:.2f}sh",
            f"NO buy/sell: {no_buy:.2f}/{no_sell:.2f}sh",
        ]
        details = []
        for trade in group:
            if trade["side"] == "Up":
                side_display = "YES"
                text_color = "#55ff55"
            elif trade["side"] == "Down":
                side_display = "NO"
                text_color = "#ff5555"
            else:
                side_display = "SETTLEMENT"
                text_color = "#ffd166"
            net_delta = trade["cost"] if trade["type"] == "Buy" else -trade["cost"]
            line = (
                f"{trade['type'].upper()} {side_display} "
                f"{trade['price']:.2f}c {trade['shares']:.2f}sh "
                f"gross ${trade['cost']:.2f} net ${net_delta:.2f}"
            )
            details.append(f"<span style='color: {text_color}'>{html.escape(line)}</span>")

        tooltip = (
            f"<b>{html.escape(hover_time)}</b> ({count} trades)<br>"
            f"{html.escape(' | '.join(header))}<br>"
            f"--------------------------------<br>"
            f"{'<br>'.join(details[:20])}"
            f"{'<br>... (+' + str(len(details) - 20) + ' more)' if len(details) > 20 else ''}"
        )

        if count == 1:
            trade = group[0]
            style = STYLES.get((trade["type"], trade["side"]), dict(color="gray", symbol="circle"))
            nodes.append(
                {
                    "x": dt_value,
                    "y": avg_price,
                    "size": 12,
                    "color": style["color"],
                    "symbol": style["symbol"],
                    "text": "",
                    "hover": tooltip,
                    "line_color": style["color"],
                    "line_width": 2 if "circle-open" in str(style["symbol"]) else 0,
                }
            )
            continue

        first = group[0]
        homogeneous = all((t["type"], t["side"]) == (first["type"], first["side"]) for t in group)
        base_color = STYLES.get((first["type"], first["side"]), dict(color="#1f77b4"))["color"] if homogeneous else "#1f77b4"
        nodes.append(
            {
                "x": dt_value,
                "y": avg_price,
                "size": min(55, max(22, math.sqrt(max(total_shares, count)) * 3)),
                "color": base_color,
                "symbol": "circle",
                "text": str(count),
                "hover": tooltip,
                "line_color": "white",
                "line_width": 1,
            }
        )

    return nodes


def generate_chart(
    parsed: list[dict],
    market_title: str,
    output_html: Path,
    *,
    include_plotlyjs: bool | str = True,
) -> None:
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except Exception as exc:
        raise RuntimeError("Plotly chart dependencies are unavailable") from exc

    min_ts = min(t["timestamp"] for t in parsed)
    max_ts = max(t["timestamp"] for t in parsed)
    start_et = parsed[0]["dt_et"]
    end_et = parsed[-1]["dt_et"]
    date_str = start_et.strftime("%B %d")
    time_range = f"{start_et.strftime('%I:%M%p %Z')} - {end_et.strftime('%I:%M%p %Z')}"
    if min_ts != max_ts and start_et.date() != end_et.date():
        date_str = f"{start_et.strftime('%B %d')} - {end_et.strftime('%B %d')}"
    full_title = f"Trades: {market_title} - {date_str}, {time_range}"

    fig = make_subplots(
        rows=4,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.44, 0.17, 0.20, 0.19],
        subplot_titles=("", "Cumulative Gross Activity", "Net Cash Spent", "Open Shares"),
    )

    nodes = _build_trade_nodes(parsed)
    fig.add_trace(
        go.Scatter(
            x=[n["x"] for n in nodes],
            y=[n["y"] for n in nodes],
            mode="markers+text",
            text=[n["text"] for n in nodes],
            textfont=dict(color="white", size=10),
            marker=dict(
                size=[n["size"] for n in nodes],
                color=[n["color"] for n in nodes],
                symbol=[n["symbol"] for n in nodes],
                line=dict(color=[n["line_color"] for n in nodes], width=[n["line_width"] for n in nodes]),
                opacity=0.9,
            ),
            hovertext=[n["hover"] for n in nodes],
            hoverinfo="text",
            name="Trades",
        ),
        row=1,
        col=1,
    )

    series = calculate_position_series(parsed)
    times = series["time"]

    fig.add_trace(go.Scatter(x=times, y=series["yes_buy_shares"], name="YES buys (sh)", line=dict(color="green", width=1.4)), row=2, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["yes_sell_shares"], name="YES sells (sh)", line=dict(color="limegreen", width=1.4, dash="dash")), row=2, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["no_buy_shares"], name="NO buys (sh)", line=dict(color="magenta", width=1.4)), row=2, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["no_sell_shares"], name="NO sells (sh)", line=dict(color="red", width=1.4, dash="dash")), row=2, col=1)

    fig.add_trace(go.Scatter(x=times, y=series["yes_net_spent"], name="YES net outflow ($)", line=dict(color="green")), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["no_net_spent"], name="NO net outflow ($)", line=dict(color="red")), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["total_net_spent"], name="TOTAL net outflow ($)", line=dict(color="blue")), row=3, col=1)

    fig.add_trace(go.Scatter(x=times, y=series["yes_shares"], name="YES open (sh)", line=dict(color="green", dash="dot")), row=4, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["no_shares"], name="NO open (sh)", line=dict(color="red", dash="dot")), row=4, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["total_shares"], name="Total open (sh)", line=dict(color="blue")), row=4, col=1)
    fig.add_trace(go.Scatter(x=times, y=series["imbalance_shares"], name="YES-NO imbalance", line=dict(color="gray", dash="dash")), row=4, col=1)

    fig.update_layout(
        title=full_title,
        height=1300,
        template="plotly_dark",
        hovermode="closest",
        showlegend=True,
        xaxis=dict(type="category"),
        xaxis2=dict(type="category"),
        xaxis3=dict(type="category"),
        xaxis4=dict(type="category"),
    )
    fig.update_yaxes(title_text="Price (c)", row=1, col=1)
    fig.update_yaxes(title_text="Gross sh", row=2, col=1)
    fig.update_yaxes(title_text="Net spent ($)", row=3, col=1, zeroline=True, zerolinecolor="#888")
    fig.update_yaxes(title_text="Shares", row=4, col=1, zeroline=True, zerolinecolor="#888")
    fig.write_html(output_html, include_plotlyjs=include_plotlyjs)


def generate_html_table(rows: list[dict], filename: Path, *, summary: dict, metadata: dict, totals: dict) -> None:
    market_title = html.escape(str(metadata.get("market_title") or "N/A"))
    condition_text = html.escape(str(metadata.get("condition_id") or "N/A"))
    user_text = html.escape(str(metadata.get("user_address") or "N/A"))
    range_text = html.escape(str(metadata.get("trade_time_range") or "N/A"))
    count_text = html.escape(str(metadata.get("trade_count") if metadata.get("trade_count") is not None else "N/A"))
    source_text = html.escape(str(metadata.get("data_source") or "N/A"))
    warning_text = str(metadata.get("data_warning") or "")
    warning_block = f'<div class="warning">{html.escape(warning_text)}</div>' if warning_text else ""

    locked_cls = _money_class(float(summary["locked_profit"]))
    yes_cls = _money_class(float(summary["if_yes_wins_pnl"]))
    no_cls = _money_class(float(summary["if_no_wins_pnl"]))
    total_invested = float(totals.get("total_buy_cost", summary.get("total_invested", 0.0)))
    total_fee = float(totals.get("total_fee", summary.get("total_fee", 0.0)))
    verdict_map = {
        "POSITIVE": ("pos", "配对部分为正收益 / Positive locked PnL"),
        "NEGATIVE": ("neg", "配对部分为负收益 / Negative locked PnL"),
        "NEUTRAL": ("neu", "配对部分接近持平 / Near break-even"),
    }
    verdict_cls, verdict_text = verdict_map.get(summary.get("final_verdict"), ("neu", "Unknown"))
    has_open_position = abs(float(summary["cum_yes"])) > 1e-9 or abs(float(summary["cum_no"])) > 1e-9

    body_rows = []
    for row in rows:
        diff_cls = _money_class(float(row["net_diff"]))
        spent_cls = _money_class(-float(row["net_spent"]))
        prof_cls = _money_class(float(row["profit"]))
        yes_win_cls = _money_class(float(row["if_yes_wins_pnl"]))
        no_win_cls = _money_class(float(row["if_no_wins_pnl"]))
        scenario_cells = (
            f"""
                <td class="{yes_win_cls}">${row['if_yes_wins_pnl']:.2f}</td>
                <td class="{no_win_cls}">${row['if_no_wins_pnl']:.2f}</td>
            """
            if has_open_position
            else ""
        )
        pair_cls = "cost-col"
        if row["pair_cost"] > 1.001:
            pair_cls = "neg"
        elif row["pair_cost"] < 0.999 and row["cum_yes"] > 0 and row["cum_no"] > 0:
            pair_cls = "pos"

        body_rows.append(
            f"""
            <tr>
                <td>{html.escape(row['time'])}</td>
                <td class="yes-col">{html.escape(row['yes_trade'])}</td>
                <td class="no-col">{html.escape(row['no_trade'])}</td>
                <td>{row['cum_yes']:.2f}</td>
                <td>${row['avg_yes']:.4f}</td>
                <td>{row['cum_no']:.2f}</td>
                <td>${row['avg_no']:.4f}</td>
                <td class="{pair_cls}">${row['pair_cost']:.4f}</td>
                <td class="{diff_cls}">{row['net_diff']:.2f}</td>
                <td class="{spent_cls}">${row['net_spent']:.2f}</td>
                <td class="cost-col">${row['total_fee']:.2f}</td>
                <td class="{prof_cls}">${row['profit']:.2f}</td>
                {scenario_cells}
            </tr>
            """
        )

    if has_open_position:
        outcome_block = f"""
            <p>若 YES 胜出 If YES wins: <strong class="{yes_cls}">${summary['if_yes_wins_pnl']:.2f}</strong></p>
            <p>若 NO 胜出 If NO wins: <strong class="{no_cls}">${summary['if_no_wins_pnl']:.2f}</strong></p>
            <p>最终判断 Final Verdict: <strong class="{verdict_cls}">{verdict_text}</strong></p>
        """
        locked_label = "锁定盈亏 Locked PnL"
    else:
        outcome_block = f"""
            <p>状态 Status: <strong class="neu">已平仓/已结算，无剩余 YES/NO 仓位</strong></p>
        """
        locked_label = "最终/锁定盈亏 Final / Locked PnL"

    scenario_headers = """
                    <th>If YES</th>
                    <th>If NO</th>
    """ if has_open_position else ""

    summary_block = f"""
        <div class="summary-box">
            <h3>总收益 / Total Return</h3>
            <p>剩余 YES / NO Remaining: <strong>{summary['cum_yes']:.2f}</strong> / <strong>{summary['cum_no']:.2f}</strong> shares</p>
            <p>累计投入 Total Invested: <strong>${total_invested:.2f}</strong></p>
            <p>总手续费 Total Fee: <strong>${total_fee:.2f}</strong></p>
            <p>{locked_label}: <strong class="{locked_cls}">${summary['locked_profit']:.2f}</strong></p>
            {outcome_block}
            <p style="margin-top: 10px; font-size: 11px; color: #888;">
                口径: Total Invested 为累计 BUY 的实际 usdcSize 支出；Total Fee 为公开成交中 usdcSize 与 price×shares 的差额推断。表格里的 Net Outflow 是扣除卖出/结算后的累计现金流，不等于投入本金。
            </p>
        </div>
    """

    html_doc = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <style>
            body {{ font-family: monospace; background: #111; color: #eee; padding: 20px; }}
            table {{ border-collapse: collapse; width: 100%; font-size: 12px; }}
            th, td {{ border: 1px solid #333; padding: 7px; text-align: right; white-space: nowrap; }}
            th {{ background: #222; position: sticky; top: 0; z-index: 1; }}
            tr:hover {{ background: #222; }}
            .pos {{ color: #4caf50; }}
            .neg {{ color: #f44336; }}
            .neu {{ color: #888; }}
            .yes-col {{ color: #4caf50; }}
            .no-col {{ color: #e91e63; }}
            .cost-col {{ color: #ffeb3b; }}
            .meta-line {{ margin-bottom: 14px; display: flex; flex-wrap: wrap; gap: 8px; }}
            .meta-item {{ font-size: 12px; color: #bbb; border: 1px solid #333; background: #1b1b1b; padding: 4px 8px; border-radius: 6px; }}
            .meta-label {{ color: #8a8a8a; }}
            .warning {{ margin: 0 0 14px 0; color: #ffd166; border: 1px solid #5c4b1c; background: #241f10; padding: 8px 10px; border-radius: 6px; }}
            .summary-box {{ margin-top: 20px; padding: 14px; border: 1px solid #444; border-radius: 8px; max-width: 680px; }}
            .summary-box p {{ margin: 6px 0; }}
        </style>
    </head>
    <body>
        <h2>Trade Analysis Table - {market_title}</h2>
        <div class="meta-line">
            <span class="meta-item"><span class="meta-label">Condition ID:</span> {condition_text}</span>
            <span class="meta-item"><span class="meta-label">User:</span> {user_text}</span>
            <span class="meta-item"><span class="meta-label">Data Source:</span> {source_text}</span>
            <span class="meta-item"><span class="meta-label">Trade Time Range:</span> {range_text}</span>
            <span class="meta-item"><span class="meta-label">Trade Count:</span> {count_text}</span>
            <span class="meta-item"><span class="meta-label">YES buy/sell:</span> {totals['yes_buy_shares']:.2f}/{totals['yes_sell_shares']:.2f} sh</span>
            <span class="meta-item"><span class="meta-label">NO buy/sell:</span> {totals['no_buy_shares']:.2f}/{totals['no_sell_shares']:.2f} sh</span>
        </div>
        {warning_block}
        <table>
            <thead>
                <tr>
                    <th>Time (ET)</th>
                    <th>YES Trade</th>
                    <th>NO Trade</th>
                    <th class="yes-col">Cum YES</th>
                    <th class="yes-col">Avg YES ($)</th>
                    <th class="no-col">Cum NO</th>
                    <th class="no-col">Avg NO ($)</th>
                    <th class="cost-col">Pair Cost</th>
                    <th>Net Diff</th>
                    <th>Net Outflow</th>
                    <th>Total Fee</th>
                    <th>Locked PnL</th>
                    {scenario_headers}
                </tr>
            </thead>
            <tbody>
                {''.join(body_rows)}
            </tbody>
        </table>
        {summary_block}
    </body>
    </html>
    """
    filename.write_text(html_doc)


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 interactive_chart.py <JsonFile OR MarketURL/Slug/ConditionID/Name> [UserAddress]")
        return

    arg1 = sys.argv[1]
    condition_id = None
    address = None

    if arg1.endswith(".json"):
        print(f"Loading trades from local file: {arg1}")
        try:
            raw_trades = json.loads(Path(arg1).read_text())
        except FileNotFoundError:
            print(f"Error: File '{arg1}' not found.")
            return
        except json.JSONDecodeError:
            print(f"Error: File '{arg1}' is not valid JSON.")
            return
        if len(sys.argv) >= 3:
            address = sys.argv[2]
        market_title = raw_trades[0].get("title", "Unknown Market") if raw_trades else "Unknown Market"
        if raw_trades:
            condition_id = raw_trades[0].get("conditionId") or raw_trades[0].get("condition_id")
        warning = trade_source_warning(raw_trades)
        fetch_meta = {
            "fetched_at": None,
            "condition_id": condition_id or "N/A",
            "user_address": address or "N/A",
            "requested_source": "local_json",
            "data_source": "local_json",
            "view_mode": "local_json_view",
            "trade_count": len(raw_trades),
            "fallback_reason": None,
            "warnings": [warning] if warning else [],
            "endpoints": [],
            "cache": {"enabled": False, "hit": False},
        }
    else:
        if len(sys.argv) < 3:
            print("User Address required for API fetch.")
            return
        address = sys.argv[2]
        print(f"Resolving market: {arg1}...")
        event, market, identifier = resolve_market_identifier(arg1)
        if not market:
            print(f"ERROR: Market not found via Gamma API: {identifier or arg1}")
            return
        condition_id = market.get("conditionId")
        market_title = market.get("question") or market.get("title") or (event or {}).get("title") or condition_id
        print(f"Matched market: {market_title}")
        print(f"Target Condition ID: {condition_id}")
        fetch_result = fetch_trades_detailed(condition_id, address, page_limit=1000, verbose=True)
        raw_trades = fetch_result.trades
        fetch_meta = fetch_result.meta

    if not raw_trades:
        print("No trades found.")
        return

    output_bundle = prepare_output_bundle(raw_trades, market_title=market_title, user_address=address)
    output_bundle.trades_json.write_text(json.dumps(raw_trades, indent=2))
    print(f"Saved {len(raw_trades)} trades to {output_bundle.trades_json}")
    output_bundle.fetch_meta_json.write_text(json.dumps(fetch_meta, indent=2, ensure_ascii=False))
    print(f"Fetch metadata saved to {output_bundle.fetch_meta_json}")

    parsed = parse_trades(raw_trades)
    generate_chart(parsed, market_title, output_bundle.chart_html)
    print(f"Chart saved to {output_bundle.chart_html}")

    print("Generating Analysis Table...")
    table_rows = calculate_table_metrics(parsed)
    summary = calculate_summary(table_rows)
    totals = calculate_trade_summary(parsed)
    analysis_meta = {
        "market_title": market_title,
        "condition_id": condition_id or "N/A",
        "user_address": address or "N/A",
        "trade_time_range": (
            f"{parsed[0]['dt_et'].strftime('%Y-%m-%d %I:%M:%S %p %Z')} - "
            f"{parsed[-1]['dt_et'].strftime('%Y-%m-%d %I:%M:%S %p %Z')}"
        ),
        "trade_count": len(parsed),
        "data_source": fetch_meta.get("data_source") or describe_trade_source(raw_trades),
        "data_warning": "; ".join(str(w) for w in fetch_meta.get("warnings", []) if w)
        or trade_source_warning(raw_trades),
    }
    generate_html_table(table_rows, output_bundle.analysis_html, summary=summary, metadata=analysis_meta, totals=totals)
    print(f"Analysis table saved to {output_bundle.analysis_html}")
    print(f"Output directory: {output_bundle.root_dir}")


if __name__ == "__main__":
    main()

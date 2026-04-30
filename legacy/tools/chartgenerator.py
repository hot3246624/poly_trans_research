#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import numpy as np

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

from output_paths import prepare_output_bundle
from trade_analysis import (
    calculate_position_series,
    calculate_resolution_pnl,
    calculate_trade_summary,
    describe_trade_source,
    fetch_trades,
    format_ts,
    infer_resolved_side_from_trades,
    normalize_resolved_side,
    parse_trades,
    resolve_market_identifier,
    trade_source_warning,
)


STYLES = {
    ("Buy", "Up"): ("#008f00", "x", "Buy YES"),
    ("Sell", "Up"): ("#00c800", "o", "Sell YES"),
    ("Buy", "Down"): ("#d000d0", "x", "Buy NO"),
    ("Sell", "Down"): ("#d40000", "o", "Sell NO"),
}


def prompt_resolved_side(current: str | None = None) -> str | None:
    if current in {"YES", "NO"}:
        return current
    while True:
        side = input("Enter resolved side (YES/NO, blank = skip): ").strip().upper()
        if not side:
            return None
        if side in {"YES", "NO"}:
            return side
        print("Please enter YES or NO.")


def _raw_timestamp(item: dict) -> int:
    try:
        ts = int(float(item.get("timestamp") or 0))
    except (TypeError, ValueError):
        return 0
    return ts // 1000 if ts > 10_000_000_000 else ts


def _weighted_avg_price(group: list[dict]) -> float:
    total_shares = sum(t["shares"] for t in group)
    if total_shares <= 0:
        return sum(t["price"] for t in group) / max(len(group), 1)
    return sum(t["price"] * t["shares"] for t in group) / total_shares


def _annotate_last(
    ax,
    x_vals: list[int],
    y_vals: list[float],
    color: str,
    *,
    prefix: str = "",
    suffix: str = "",
) -> None:
    if not x_vals or not y_vals:
        return
    ax.annotate(
        f"{prefix}{y_vals[-1]:.2f}{suffix}",
        (x_vals[-1], y_vals[-1]),
        xytext=(12, 0),
        textcoords="offset points",
        color=color,
        fontsize=8,
        va="center",
    )


def _time_formatter(unique_timestamps: list[int]):
    def formatter(x, pos):
        idx = int(x)
        if 0 <= idx < len(unique_timestamps):
            _, time_part, zone_part = format_ts(unique_timestamps[idx]).split()
            return f"{time_part}\n{zone_part}"
        return ""

    return formatter


def write_stats_report(
    report_path: Path,
    *,
    target_market: str,
    resolved_side: str,
    data_source: str,
    data_warning: str,
    parsed: list[dict],
    summary: dict,
    resolution: dict,
    prices: list[float],
) -> None:
    if parsed:
        start_time = format_ts(parsed[0]["timestamp"])
        end_time = format_ts(parsed[-1]["timestamp"])
    else:
        start_time = end_time = "N/A"

    min_price = min(prices) if prices else 0.0
    max_price = max(prices) if prices else 0.0

    side_counts = {"Buy": 0, "Sell": 0}
    outcome_counts = {"Up": 0, "Down": 0}
    for trade in parsed:
        side_counts[trade["type"]] = side_counts.get(trade["type"], 0) + 1
        outcome_counts[trade["side"]] = outcome_counts.get(trade["side"], 0) + 1

    lines = [
        f"MARKET: {target_market}",
        f"DATA SOURCE: {data_source}",
        f"DATA WARNING: {data_warning or 'N/A'}",
        f"RESOLUTION: {resolved_side}",
        f"TRADES: {len(parsed)}",
        f"TIME RANGE: {start_time} to {end_time}",
        f"PRICE RANGE: {min_price:.2f} - {max_price:.2f} cents",
        f"DIRECTION COUNTS: Buy={side_counts.get('Buy', 0)} Sell={side_counts.get('Sell', 0)}",
        f"OUTCOME COUNTS: YES/Up={outcome_counts.get('Up', 0)} NO/Down={outcome_counts.get('Down', 0)}",
        "",
        "--- Position and cash accounting ---",
        f"Remaining YES shares: {resolution['remaining_yes']:.6f}",
        f"Remaining NO shares:  {resolution['remaining_no']:.6f}",
        f"Net cash spent (buys - sells): $ {resolution['total_spent']:.6f}",
        f"Final value at resolution: $ {resolution['final_value']:.6f}",
        f"FINAL PNL: $ {resolution['pnl']:.6f}",
        f"If YES wins PnL: $ {resolution['if_yes_wins_pnl']:.6f}",
        f"If NO wins PnL:  $ {resolution['if_no_wins_pnl']:.6f}",
        f"Locked floor PnL: $ {resolution['locked_profit']:.6f}",
        f"Remaining cost basis: $ {summary['total_cost_basis']:.6f}",
        f"Realized PnL from explicit sells: $ {summary['realized_pnl']:.6f}",
        "",
        "--- Gross buy/sell totals ---",
        f"YES buys:  {summary['yes_buy_shares']:.6f} sh / $ {summary['yes_buy_cost']:.6f}",
        f"YES sells: {summary['yes_sell_shares']:.6f} sh / $ {summary['yes_sell_proceeds']:.6f}",
        f"NO buys:   {summary['no_buy_shares']:.6f} sh / $ {summary['no_buy_cost']:.6f}",
        f"NO sells:  {summary['no_sell_shares']:.6f} sh / $ {summary['no_sell_proceeds']:.6f}",
        "",
        "--- Final net position ---",
        f"YES net spent: $ {summary['yes_net_spent']:.6f} | avg remaining cost $ {summary['yes_avg_cost']:.6f}",
        f"NO net spent:  $ {summary['no_net_spent']:.6f} | avg remaining cost $ {summary['no_avg_cost']:.6f}",
        f"Total net spent: $ {summary['total_net_spent']:.6f}",
        f"Share imbalance YES-NO: {summary['imbalance_shares']:.6f} sh",
        "",
        "--- Trades (sorted by timestamp) ---",
        "Idx | Time ET                 | Type | Side | Price(c) |  Shares   | Gross($) | NetSpentDelta($)",
        "----+-------------------------+------+------+----------+-----------+----------+-----------------",
    ]

    for i, trade in enumerate(parsed, start=1):
        gross = trade["cost"]
        delta = gross if trade["type"] == "Buy" else -gross
        lines.append(
            f"{i:3d} | {format_ts(trade['timestamp']):<23} | {trade['type']:<4} | {trade['side']:<4} | "
            f"{trade['price']:8.2f} | {trade['shares']:9.2f} | $ {gross:8.2f} | $ {delta:14.2f}"
        )

    report_path.write_text("\n".join(lines))


def main() -> None:
    resolved_arg = None
    json_file = None
    market_query = None
    user_address = None

    args = sys.argv[1:]
    if args and args[0].lower().endswith(".json"):
        json_file = args[0]
        resolved_arg = normalize_resolved_side(args[1]) if len(args) > 1 else None
    elif args and normalize_resolved_side(args[0]):
        resolved_arg = normalize_resolved_side(args[0])
        if len(args) > 1 and args[1].lower().endswith(".json"):
            json_file = args[1]
        elif len(args) > 1:
            market_query = args[1]
            user_address = args[2] if len(args) > 2 else None
    else:
        market_query = args[0] if args else None
        user_address = args[1] if len(args) > 1 else None
        resolved_arg = normalize_resolved_side(args[2]) if len(args) > 2 else None

    if json_file:
        try:
            raw_data = json.loads(Path(json_file).read_text())
        except FileNotFoundError:
            print(f"Error: File '{json_file}' not found.")
            return
        except json.JSONDecodeError:
            print(f"Error: File '{json_file}' is not valid JSON.")
            return
        if not raw_data:
            print("No trades found.")
            return
        market_title = raw_data[0].get("title", "Unknown Market")
        user_address = None
    else:
        market_query = market_query or input("Enter market URL, slug, conditionId, or name: ").strip()
        if not market_query:
            print("Market name is required.")
            return

        event, market, identifier = resolve_market_identifier(market_query)
        if not market:
            print(f"No market found for that query: {identifier or market_query}")
            return

        market_title = market.get("question") or market.get("title") or (event or {}).get("title") or "Unknown Market"
        condition_id = market.get("conditionId") or ""
        print(f"Found market: {market_title}")
        print(f"Condition ID: {condition_id}")

        user_address = user_address or input("Enter user address to fetch trades: ").strip()
        if not user_address:
            print("User address is required.")
            return

        raw_data = fetch_trades(condition_id, user_address, page_limit=1000, verbose=True)
        if not raw_data:
            print("No trades returned for that user/market.")
            return

    raw_data = sorted(raw_data, key=_raw_timestamp)
    output_bundle = prepare_output_bundle(raw_data, market_title=market_title, user_address=user_address)
    output_bundle.trades_json.write_text(json.dumps(raw_data, indent=2))
    print(f"Saved {len(raw_data)} trades to {output_bundle.trades_json}")

    resolved_side = None
    if resolved_arg in {"YES", "NO"}:
        resolved_side = resolved_arg
    else:
        inferred, latest = infer_resolved_side_from_trades(raw_data)
        if inferred:
            resolved_side = inferred
            print(
                "Inferred resolved side: "
                f"{resolved_side} (latest trade outcome {latest.get('outcome', '')} "
                f"at price {float(latest.get('price', 0)):.4f}, ts {latest.get('timestamp', 0)})"
            )
        elif resolved_arg == "AUTO":
            print("Could not infer resolved side automatically.")
            return
        else:
            resolved_side = prompt_resolved_side(None)
            if not resolved_side:
                print("Resolved side is required.")
                return

    parsed = parse_trades(raw_data)
    if not parsed:
        print("No entries found.")
        return

    target_market = market_title or parsed[0].get("market") or "Unknown Market"
    prices = [trade["price"] for trade in parsed]
    series = calculate_position_series(parsed)
    summary = calculate_trade_summary(parsed)
    resolution = calculate_resolution_pnl(parsed, resolved_side)

    unique_timestamps = sorted({trade["timestamp"] for trade in parsed})
    ts_map = {ts: i for i, ts in enumerate(unique_timestamps)}
    x_indices = [ts_map[trade["timestamp"]] for trade in parsed]

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(
        4,
        1,
        figsize=(16, 14.5),
        gridspec_kw={"height_ratios": [3, 1.25, 1.15, 1.15]},
    )
    fig.subplots_adjust(hspace=0.45, bottom=0.2)

    grouped_trades: dict[int, list[dict]] = {}
    for trade in parsed:
        grouped_trades.setdefault(ts_map[trade["timestamp"]], []).append(trade)

    next_up = True
    for x_idx in sorted(grouped_trades):
        group = grouped_trades[x_idx]
        avg_price = _weighted_avg_price(group)
        count = len(group)
        if count == 1:
            trade = group[0]
            color, marker, _ = STYLES.get((trade["type"], trade["side"]), ("gray", "o", "Unknown"))
            ax1.scatter(
                x_idx,
                trade["price"],
                color=color,
                marker=marker,
                s=65,
                linewidths=2.5 if marker == "x" else 1.2,
                alpha=0.9,
                zorder=5,
            )
            direction = 1 if next_up else -1
            next_up = not next_up
            end_y = trade["price"] + direction * 10
            ax1.vlines(x_idx, trade["price"], end_y, colors=color, linewidth=1.5, alpha=0.6)
            label_text = f"{trade['type'][0]} {trade['shares']:.2f}sh\n${trade['cost']:.2f}"
            ax1.annotate(
                label_text,
                xy=(x_idx, end_y),
                xytext=(0, direction * 2),
                textcoords="offset points",
                ha="center",
                va="bottom" if direction > 0 else "top",
                fontsize=7,
                bbox={"boxstyle": "round,pad=0.2", "fc": "white", "alpha": 0.7, "ec": "none"},
            )
            continue

        first = group[0]
        same_style = all((t["type"], t["side"]) == (first["type"], first["side"]) for t in group)
        color = STYLES.get((first["type"], first["side"]), ("#1f77b4", "o", ""))[0] if same_style else "#1f77b4"
        ax1.scatter(x_idx, avg_price, color="white", marker="o", s=320, edgecolors=color, linewidth=2, zorder=5)
        ax1.text(x_idx, avg_price, str(count), ha="center", va="center", fontsize=9, fontweight="bold", color=color, zorder=6)

        direction = 1 if next_up else -1
        next_up = not next_up
        info_lines = []
        for idx, trade in enumerate(group):
            if idx >= 5:
                info_lines.append(f"...+ {len(group) - 5} more")
                break
            side_label = "YES" if trade["side"] == "Up" else "NO"
            info_lines.append(f"{trade['type'][0]} {side_label} {trade['shares']:.2f}sh ${trade['cost']:.2f}")
        end_y = avg_price + direction * (20 + len(info_lines) * 4)
        ax1.vlines(x_idx, avg_price, end_y, colors=color, linewidth=2, alpha=0.6, linestyles="dotted")
        ax1.annotate(
            "\n".join(info_lines),
            xy=(x_idx, end_y),
            xytext=(0, direction * 2),
            textcoords="offset points",
            ha="center",
            va="bottom" if direction > 0 else "top",
            fontsize=6,
            bbox={"boxstyle": "round,pad=0.3", "fc": "white", "alpha": 0.85, "ec": color},
        )

    ax1.set_title(f"Trades for {target_market}")
    ax1.set_ylabel("Price (cents)")
    ax1.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
    ax1.xaxis.set_major_formatter(ticker.FuncFormatter(_time_formatter(unique_timestamps)))
    ax1.set_yticks(range(0, 101, 10))
    ax1.grid(axis="y", linestyle="--", alpha=0.3)

    yes_buy_per_ts = [0.0] * len(unique_timestamps)
    yes_sell_per_ts = [0.0] * len(unique_timestamps)
    no_buy_per_ts = [0.0] * len(unique_timestamps)
    no_sell_per_ts = [0.0] * len(unique_timestamps)
    for x_idx, group in grouped_trades.items():
        for trade in group:
            if trade["side"] == "Up" and trade["type"] == "Buy":
                yes_buy_per_ts[x_idx] += trade["shares"]
            elif trade["side"] == "Up":
                yes_sell_per_ts[x_idx] += trade["shares"]
            elif trade["type"] == "Buy":
                no_buy_per_ts[x_idx] += trade["shares"]
            else:
                no_sell_per_ts[x_idx] += trade["shares"]

    x_range = np.arange(len(unique_timestamps))
    vol_ax = ax1.inset_axes([0, 0.0, 1.0, 0.23], sharex=ax1)
    vol_ax.patch.set_alpha(0)
    width = 0.18
    vol_ax.bar(x_range - width * 1.5, yes_buy_per_ts, width=width, color="green", alpha=0.20, label="Buy YES")
    vol_ax.bar(x_range - width * 0.5, [-v for v in yes_sell_per_ts], width=width, color="lime", alpha=0.24, label="Sell YES")
    vol_ax.bar(x_range + width * 0.5, no_buy_per_ts, width=width, color="magenta", alpha=0.18, label="Buy NO")
    vol_ax.bar(x_range + width * 1.5, [-v for v in no_sell_per_ts], width=width, color="red", alpha=0.20, label="Sell NO")
    vol_ax.axhline(0, color="gray", linewidth=0.5, alpha=0.5)
    vol_ax.set_yticks([])
    vol_ax.set_xticks([])
    vol_ax.set_xlim(-0.5, len(unique_timestamps) - 0.5)

    ax2.plot(x_indices, series["yes_buy_shares"], color="green", linewidth=1.6, label="YES buys (sh)")
    ax2.plot(x_indices, series["yes_sell_shares"], color="limegreen", linewidth=1.6, linestyle="--", label="YES sells (sh)")
    ax2.plot(x_indices, series["no_buy_shares"], color="magenta", linewidth=1.6, label="NO buys (sh)")
    ax2.plot(x_indices, series["no_sell_shares"], color="red", linewidth=1.6, linestyle="--", label="NO sells (sh)")
    ax2.set_ylabel("Gross volume (sh)")
    ax2.grid(axis="y", alpha=0.2)
    ax2.set_xticks([])
    ax2.set_title("Cumulative Gross Activity (buys and sells)")
    ax2.legend(loc="upper left", ncol=2, fontsize=8)
    activity_text = (
        f"YES buy/sell: {summary['yes_buy_shares']:.2f} / {summary['yes_sell_shares']:.2f} sh\n"
        f"NO buy/sell:  {summary['no_buy_shares']:.2f} / {summary['no_sell_shares']:.2f} sh"
    )
    ax2.text(
        0.01,
        0.02,
        activity_text,
        transform=ax2.transAxes,
        ha="left",
        va="bottom",
        fontsize=9,
        bbox={"boxstyle": "round,pad=0.3", "fc": "white", "alpha": 0.8, "ec": "gray"},
    )

    ax3.grid(alpha=0.3)
    ax3.plot(x_indices, series["yes_net_spent"], color="green", linewidth=2, label="YES net spent ($)")
    ax3.plot(x_indices, series["no_net_spent"], color="red", linewidth=2, label="NO net spent ($)")
    ax3.plot(x_indices, series["total_net_spent"], color="blue", linewidth=2, label="TOTAL net spent ($)")
    ax3.axhline(0, color="gray", linewidth=0.8, alpha=0.5)
    _annotate_last(ax3, x_indices, series["yes_net_spent"], "green", prefix="$")
    _annotate_last(ax3, x_indices, series["no_net_spent"], "red", prefix="$")
    _annotate_last(ax3, x_indices, series["total_net_spent"], "blue", prefix="$")
    ax3.set_title("Net Cash Spent (buys - sells)")
    ax3.set_ylabel("Net spent ($)")
    ax3.set_xticks([])
    ax3.legend(loc="upper left")

    pnl_text = (
        f"MARKET RESOLVED: {resolved_side}\n"
        f"Remaining YES shares: {resolution['remaining_yes']:.2f}\n"
        f"Remaining NO shares:  {resolution['remaining_no']:.2f}\n"
        f"Net Spent: $ {resolution['total_spent']:.2f}\n"
        f"Final Value: $ {resolution['final_value']:.2f}\n"
        f"FINAL PNL: $ {resolution['pnl']:.2f}"
    )
    summary_text = (
        f"YES Buy: {summary['yes_buy_shares']:.2f} sh ($ {summary['yes_buy_cost']:.2f}) | "
        f"Sell: {summary['yes_sell_shares']:.2f} sh ($ {summary['yes_sell_proceeds']:.2f})\n"
        f"NO Buy:  {summary['no_buy_shares']:.2f} sh ($ {summary['no_buy_cost']:.2f}) | "
        f"Sell: {summary['no_sell_shares']:.2f} sh ($ {summary['no_sell_proceeds']:.2f})"
    )
    fig.text(0.01, 0.01, summary_text, ha="left", va="bottom", fontsize=11, bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "black"})
    fig.text(0.99, 0.06, pnl_text, ha="right", va="top", fontsize=12, bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "black"})

    ax4.grid(alpha=0.3)
    ax4.plot(x_indices, series["yes_shares"], color="green", linewidth=2, label="YES open shares")
    ax4.plot(x_indices, series["no_shares"], color="red", linewidth=2, label="NO open shares")
    ax4.plot(x_indices, series["total_shares"], color="blue", linewidth=2, label="Total open shares")
    ax4.plot(x_indices, series["imbalance_shares"], color="gray", linewidth=1.5, linestyle="--", label="YES-NO imbalance")
    ax4.axhline(0, color="gray", linewidth=0.8, alpha=0.5)
    _annotate_last(ax4, x_indices, series["yes_shares"], "green", suffix=" sh")
    _annotate_last(ax4, x_indices, series["no_shares"], "red", suffix=" sh")
    _annotate_last(ax4, x_indices, series["total_shares"], "blue", suffix=" sh")
    ax4.set_title("Open Shares (buys - sells)")
    ax4.set_ylabel("Shares")
    ax4.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
    ax4.xaxis.set_major_formatter(ticker.FuncFormatter(_time_formatter(unique_timestamps)))
    plt.setp(ax4.get_xticklabels(), rotation=30, ha="right")
    ax4.legend(loc="upper left")

    xlim_range = (-0.5, len(unique_timestamps) - 0.5)
    for axis in (ax1, ax2, ax3, ax4):
        axis.set_xlim(*xlim_range)

    plt.tight_layout()
    plt.savefig(output_bundle.chart_png, dpi=200, bbox_inches="tight")
    plt.close("all")

    write_stats_report(
        output_bundle.report_txt,
        target_market=target_market,
        resolved_side=resolved_side,
        data_source=describe_trade_source(raw_data),
        data_warning=trade_source_warning(raw_data),
        parsed=parsed,
        summary=summary,
        resolution=resolution,
        prices=prices,
    )

    print(f"Chart saved to {output_bundle.chart_png}")
    print(f"Stats report saved to {output_bundle.report_txt}")
    print(f"Output directory: {output_bundle.root_dir}")


if __name__ == "__main__":
    main()

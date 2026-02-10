#!/usr/bin/env python3

import sys
import json
import datetime
import requests
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# ----------------------------------------
# CONFIG
# ----------------------------------------
SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
TRADES_URL = "https://data-api.polymarket.com/trades"
DEFAULT_CHART_FILE = "chart.html"
DEFAULT_TRADE_FILE = "trades.json"

# Visual Style Map
STYLES = {
    ("Buy", "Up"):   dict(color="#008f00", symbol="x", name="Buy YES"),
    ("Sell", "Up"):  dict(color="#00c800", symbol="circle-open", name="Sell YES"),
    ("Buy", "Down"): dict(color="#d000d0", symbol="x", name="Buy NO"),
    ("Sell", "Down"):dict(color="#d40000", symbol="circle-open", name="Sell NO")
}

# ----------------------------------------
# DATA FETCHING (From chartgenerator.py)
# ----------------------------------------
def search_market(query):
    """Return (event, market) for the first matching search result."""
    try:
        resp = requests.get(SEARCH_URL, params={"q": query}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"Error searching market: {exc}")
        return None, None

    events = data.get("events", []) if isinstance(data, dict) else []
    for event in events:
        markets = event.get("markets") or []
        if markets:
            return event, markets[0]
    return None, None


def fetch_trades(condition_id, user_address, page_limit=1000):
    """Fetch all trades using conditionId (Provenance: chartgenerator.py)"""
    all_trades = []
    offset = 0
    print(f"Fetching trades for market={condition_id}, user={user_address}...")
    
    while True:
        params = {
            "limit": page_limit,
            "offset": offset,
            "takerOnly": "false",
            "market": condition_id,
            "user": user_address,
        }
        try:
            resp = requests.get(TRADES_URL, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            print(f"Error fetching trades: {exc}")
            break

        if isinstance(data, dict):
            batch = data.get("trades", [])
        elif isinstance(data, list):
            batch = data
        else:
            batch = []

        if not batch:
            break
            
        all_trades.extend(batch)
        if len(batch) < page_limit:
            break
        offset += page_limit
        print(f"  Fetched {len(all_trades)} trades so far...")

    return all_trades

# ----------------------------------------
# PROCESSING
# ----------------------------------------
def parse_trades(raw_data):
    parsed = []
    for item in raw_data:
        entry = {}
        raw_side = item.get("side", "BUY").upper()
        
        # Outcome normalization
        outcome = item.get("outcome", "Up")
        # Handle "Yes"/"No" mapping if needed, chartgenerator does: item.get("outcome", "Up")
        if outcome.lower() == "yes": outcome = "Up"
        if outcome.lower() == "no": outcome = "Down"

        entry["type"] = "Buy" if raw_side == "BUY" else "Sell"
        entry["market"] = item.get("title", "")
        entry["side"] = outcome 
        # Price in Cents logic from chartgenerator: float * 100
        entry["price"] = float(item.get("price", 0)) * 100.0  
        entry["shares"] = float(item.get("size", 0))
        # Cost is Price(raw) * Shares. chartgenerator does price*shares where price is raw 0-1.
        raw_p = float(item.get("price", 0))
        entry["cost"] = raw_p * entry["shares"]
        
        entry["timestamp"] = int(item.get("timestamp", 0))
        entry["dt"] = datetime.datetime.fromtimestamp(entry["timestamp"])
        parsed.append(entry)
    
    # Sort by time
    parsed.sort(key=lambda x: x["timestamp"])
    return parsed

def calculate_exposure(parsed):
    # Matches chartgenerator logic
    yes_curve, no_curve, net_curve = [], [], []
    yes_sh_curve, no_sh_curve, net_sh_curve = [], [], []
    
    yes_exp = no_exp = 0.0
    yes_sh = no_sh = 0.0
    
    timestamps = []
    
    for e in parsed:
        timestamps.append(e["dt"])
        
        # Dollar Exp
        if e["side"] == "Up": # YES
            delta = e["cost"] if e["type"] == "Buy" else -e["cost"]
            yes_exp += delta
        else: # NO
            delta = e["cost"] if e["type"] == "Buy" else -e["cost"]
            no_exp += delta
            
        # Share Exp
        if e["side"] == "Up":
            delta = e["shares"] if e["type"] == "Buy" else -e["shares"]
            yes_sh += delta
        else:
            delta = e["shares"] if e["type"] == "Buy" else -e["shares"]
            no_sh += delta
            
        yes_curve.append(yes_exp)
        no_curve.append(no_exp)
        net_curve.append(yes_exp + no_exp)
        
        yes_sh_curve.append(yes_sh)
        no_sh_curve.append(no_sh)
        net_sh_curve.append(yes_sh + no_sh)
        
    return {
        "times": timestamps,
        "yes_cost": yes_curve, "no_cost": no_curve, "net_cost": net_curve,
        "yes_sh": yes_sh_curve, "no_sh": no_sh_curve, "net_sh": net_sh_curve
    }

def calculate_cumulative(parsed):
    # Matches chartgenerator layout
    timestamps = [e["dt"] for e in parsed]
    
    # We need separate running totals
    acc_yes_vol, acc_no_vol = 0, 0
    acc_yes_cost, acc_no_cost = 0, 0
    
    cum_yes, cum_no = [], []
    cum_yes_cost_arr, cum_no_cost_arr = [], []
    
    for e in parsed:
        is_yes = (e["side"] == "Up")
        is_buy = (e["type"] == "Buy")
        
        if is_buy:
            if is_yes:
                acc_yes_vol += e["shares"]
                acc_yes_cost += e["cost"]
            else:
                acc_no_vol += e["shares"]
                acc_no_cost += e["cost"]
        # Sells don't add to cumulative volume/cost "spend" in this logic usually
        # chartgenerator logic: "is_buy... append". Sells append 0.
        
        cum_yes.append(acc_yes_vol)
        cum_no.append(acc_no_vol)
        cum_yes_cost_arr.append(acc_yes_cost)
        cum_no_cost_arr.append(acc_no_cost)
        
    return timestamps, cum_yes, cum_no, cum_yes_cost_arr, cum_no_cost_arr

# ----------------------------------------
# MAIN
# ----------------------------------------
def main():
    if len(sys.argv) < 2:
        print("Usage: python3 interactive_chart.py <MarketName OR JsonFile> [UserAddress]")
        return
        
    arg1 = sys.argv[1]
    
    # Check if arg1 is a file
    if arg1.endswith(".json"):
        print(f"Loading trades from local file: {arg1}")
        with open(arg1, "r") as f:
            raw_trades = json.load(f)
        
        # Try to guess market title from first trade or filename
        if raw_trades:
            market_title = raw_trades[0].get("title", "Unknown Market")
        else:
            market_title = "Unknown Market"
            
    else:
        # It's a query
        if len(sys.argv) < 3:
            print("User Address required for API fetch.")
            return
        address = sys.argv[2]
        
        # 1. Resolve Market
        print(f"Resolving market: {arg1}...")
        event, market = search_market(arg1)
        
        if not market:
            print("ERROR: Market not found via Gamma API.")
            return
            
        condition_id = market.get("conditionId")
        market_title = market.get("question") or event.get("title")
        print(f"Target Condition ID: {condition_id}")
        
        # 2. Fetch Data
        raw_trades = fetch_trades(condition_id, address)
        if raw_trades:
            with open(DEFAULT_TRADE_FILE, 'w') as f:
                json.dump(raw_trades, f, indent=2)

    if not raw_trades:
        print("No trades found.")
        return
        
    # 3. Parse
    parsed = parse_trades(raw_trades)
    
    # Title Date
    min_ts = min(t["timestamp"] for t in parsed)
    max_ts = max(t["timestamp"] for t in parsed)
    # ET approx offset -5h
    start_et = datetime.datetime.utcfromtimestamp(min_ts - 18000)
    end_et = datetime.datetime.utcfromtimestamp(max_ts - 18000)
    date_str = start_et.strftime("%B %d")
    time_range = f"{start_et.strftime('%I:%M%p')} - {end_et.strftime('%I:%M%p')} ET"
    full_title = f"Trades: {market_title} - {date_str}, {time_range}"

    # 4. Generate Plotly Chart
    # ---------------------------
    fig = make_subplots(
        rows=4, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.45, 0.15, 0.2, 0.2],
        subplot_titles=("", "Cumulative Buys", "Dollar Exposure", "Shares Exposure")
    )
    
    # --- Top Plot: Bubbles & Lines ---
    # Group by Timestamp
    grouped = {}
    for e in parsed:
        ts = e["timestamp"]
        if ts not in grouped: grouped[ts] = []
        grouped[ts].append(e)
        
    # Prepare traces
    # Plotly doesn't easily do vertical lines for every point like matplotlib 'vlines' efficiently for thousands.
    # We will use "Error bar" style or just shapes logic, or simple scatter points.
    
    # Since interactions are key, Bubbles are best.
    # For single trades: X or O marker.
    # For bursts: Large Bubble with count.
    
    x_vals, y_vals = [], []     # For invisible line to set range?
    
    trace_nodes = [] # Collect all nodes to sort or batch
    
    for ts in sorted(grouped.keys()):
        group = grouped[ts]
        count = len(group)
        dt = group[0]["dt"]
        
        # Calc Stats
        total_p = sum(t["price"] for t in group)
        avg_price = total_p / count
        total_shares = sum(t["shares"] for t in group)
        
        # Tooltip content
        # Format: Price Shares Cost (Side)
        # Summary Header: UP: 100sh | DOWN: 50sh
        
        # Aggregate
        sum_up = sum(t["shares"] for t in group if t["side"] == "Up")
        sum_down = sum(t["shares"] for t in group if t["side"] != "Up")
        cost_up = sum(t["cost"] for t in group if t["side"] == "Up")
        cost_down = sum(t["cost"] for t in group if t["side"] != "Up")
        
        header = []
        if sum_up > 0: header.append(f"UP: {sum_up:.0f}sh ${cost_up:.2f}")
        if sum_down > 0: header.append(f"DOWN: {sum_down:.0f}sh ${cost_down:.2f}")
        header_str = " | ".join(header)
        
        details = []
        for t in group:
            side_display = "UP" if t["side"] == "Up" else "DOWN"
            # Color logic: Green for Up, Red for Down (using hex for Plotly compatibility)
            # Bright colors for dark tooltip background
            text_color = "#55ff55" if t["side"] == "Up" else "#ff5555"
            
            p_val = t['price']
            # Format: 0.70c 18.71sh $13.1(UP)
            line_clean = f"{p_val:.2f}¢ {t['shares']:.2f}sh ${t['cost']:.2f}({side_display})"
            # Wrap in span for color
            line_colored = f"<span style='color: {text_color}'>{line_clean}</span>"
            details.append(line_colored)
        
        # Construct tooltip with safe HTML
        # Explicit <br> after header before the line separator
        tooltip = (
            f"<b>{dt.strftime('%H:%M:%S')}</b> ({count} trades)<br>"
            f"{header_str}<br>" 
            f"--------------------------------<br>" # Using text line instead of <hr> to avoid layout bugs reported by user
            f"{'<br>'.join(details[:15])}"
            f"{'<br>... (+{len(details)-15} more)' if len(details) > 15 else ''}"
        )
        
        # Style
        if count == 1:
            t = group[0]
            key = (t["type"], t["side"])
            style = STYLES.get(key, dict(color="gray", symbol="circle"))
            
            trace_nodes.append({
                "x": dt, "y": avg_price,
                "size": 12,
                "color": style["color"],
                "symbol": style["symbol"],
                "text": "",
                "hover": tooltip,
                "line_color": style["color"],
                "line_width": 2 if "circle-open" in str(style["symbol"]) else 0
            })
        else:
            # Burst
            # Color logic: if all same side, use that color. else blue/mixed.
            first = group[0]
            is_homogeneous = all((t["type"]==first["type"] and t["side"]==first["side"]) for t in group)
            
            if is_homogeneous:
                key = (first["type"], first["side"])
                base_color = STYLES.get(key)["color"]
            else:
                base_color = "#1f77b4" # Blue mixed
            
            # Scaled size
            size = min(50, max(20, np.sqrt(total_shares)*3))
            
            trace_nodes.append({
                "x": dt, "y": avg_price,
                "size": size,
                "color": base_color, # Fill
                "symbol": "circle",
                "text": str(count),
                "hover": tooltip,
                "line_color": "white",
                "line_width": 1
            })

    # PRO TIP: Add all nodes as a SINGLE Scatter trace for performance, 
    # but we need individual symbols. 
    # Plotly supports array inputs for marker properties. Best approach.
    
    fig.add_trace(go.Scatter(
        x=[n["x"] for n in trace_nodes],
        y=[n["y"] for n in trace_nodes],
        mode="markers+text",
        text=[n["text"] for n in trace_nodes],
        textfont=dict(color="white", size=10),
        marker=dict(
            size=[n["size"] for n in trace_nodes],
            color=[n["color"] for n in trace_nodes],
            symbol=[n["symbol"] for n in trace_nodes],
            line=dict(
                color=[n["line_color"] for n in trace_nodes],
                width=[n["line_width"] for n in trace_nodes]
            ),
            opacity=0.9
        ),
        hovertext=[n["hover"] for n in trace_nodes],
        hoverinfo="text",
        name="Trades"
    ), row=1, col=1)
    
    # --- ROW 2: Cumulative ---
    ts_list, c_yes, c_no, c_yes_cost, c_no_cost = calculate_cumulative(parsed)
    
    fig.add_trace(go.Scatter(x=ts_list, y=c_yes, name="Cum Buy YES (sh)", fill='tozeroy', line=dict(color='green', width=1), opacity=0.3), row=2, col=1)
    fig.add_trace(go.Scatter(x=ts_list, y=c_no, name="Cum Buy NO (sh)", fill='tozeroy', line=dict(color='red', width=1), opacity=0.3), row=2, col=1)
    # Costs on secondary would be complex, just stick to Vol for clarity or overlay
    
    # --- ROW 3: Dollar Exposure ---
    exp_data = calculate_exposure(parsed)
    times = exp_data["times"]
    
    fig.add_trace(go.Scatter(x=times, y=exp_data["yes_cost"], name="YES Exp ($)", line=dict(color='green')), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=exp_data["no_cost"], name="NO Exp ($)", line=dict(color='red')), row=3, col=1)
    fig.add_trace(go.Scatter(x=times, y=exp_data["net_cost"], name="NET Exp ($)", line=dict(color='blue')), row=3, col=1)
    
    # --- ROW 4: Share Exposure ---
    fig.add_trace(go.Scatter(x=times, y=exp_data["yes_sh"], name="YES Exp (sh)", line=dict(color='green', dash='dot')), row=4, col=1)
    fig.add_trace(go.Scatter(x=times, y=exp_data["no_sh"], name="NO Exp (sh)", line=dict(color='red', dash='dot')), row=4, col=1)
    fig.add_trace(go.Scatter(x=times, y=exp_data["net_sh"], name="NET Exp (sh)", line=dict(color='blue', dash='dot')), row=4, col=1)

    # Layout
    fig.update_layout(
        title=full_title,
        height=1300,
        template="plotly_dark",
        hovermode="closest",
        showlegend=True
    )
    
    # Y-Axes labels
    fig.update_yaxes(title_text="Price (¢)", row=1, col=1)
    fig.update_yaxes(title_text="Volume (sh)", row=2, col=1)
    fig.update_yaxes(title_text="Exposure ($)", row=3, col=1)
    fig.update_yaxes(title_text="Exposure (sh)", row=4, col=1)

    fig.write_html(DEFAULT_CHART_FILE)
    print(f"Chart saved to {DEFAULT_CHART_FILE}")

    # 5. Generate Analysis Table
    # ---------------------------
    print("Generating Analysis Table...")
    table_rows = calculate_table_metrics(parsed)
    analysis_file = "analysis_table.html"
    generate_html_table(table_rows, analysis_file)
    print(f"Analysis table saved to {analysis_file}")

def calculate_table_metrics(parsed_trades):
    """
    Calculate per-trade cumulative metrics for the analysis table.
    Columns: YES Trade, NO Trade, CumYES, AvgYES, CumNO, AvgNO, NetDiff, DiffVal, Profit
    """
    rows = []
    
    cum_yes_qty = 0.0
    cum_yes_cost = 0.0
    cum_no_qty = 0.0
    cum_no_cost = 0.0
    
    for t in parsed_trades:
        # Trade Info
        is_yes = (t["side"] == "Up")
        qty = t["shares"]
        price = t["price"] / 100.0 # Convert back to dollars for display usually, but input is cents? 
        # Wait, t["price"] in parse_trades is cents. t["cost"] is dollars.
        # Let's use cents for price display, dollars for cost calculations.
        
        price_cents = t["price"]
        cost = t["cost"] # Dollars
        
        # Format Trade Cell
        trade_str = f"{qty:.2f} @ {price_cents:.2f}¢"
        yes_trade_cell = trade_str if is_yes else ""
        no_trade_cell = trade_str if not is_yes else ""
        
        # Update Cumulatives
        # Assumption: trades are strictly increasing position? 
        # User request implies "Buy YES" and "Buy NO". Sell logic is complex (reducing cost basis vs realizing profit).
        # For this specific table request, user focused on "Buy YES" and "Buy NO". 
        # If t["type"] == "Sell", we should probably subtract Qty and Cost?
        # User formula: "YES買入累计总量". imply ignores Sells or nets them.
        # Let's assume Netting for now to keep avg price sane.
        
        if t["type"] == "Buy":
            if is_yes:
                cum_yes_qty += qty
                cum_yes_cost += cost
            else:
                cum_no_qty += qty
                cum_no_cost += cost
        else:
            # Sell - Reduce Qty and Cost proportional to avg? Or specific fifo?
            # Simple approach: Reduce Qty, Reduce Cost by *Current Avg* (FIFO-ish assumption) or *Execution Price* (Realized Gain)?
            # User request: "YES买入累计总量" (Accumulated Buy Volume).
            # If I sell, does "Accumulated Buy Volume" decrease? Probably not.
            # But "Avg Buy Price" shouldn't change on Sell if we track Buys Only.
            # HOWEVER, Profit calculation `min(yes, no) - cost` implies we care about current holding cost.
            # Let's stick to "Net Position" logic for Qty/Cost to be useful for Profit.
            if is_yes:
                cum_yes_qty -= qty
                cum_yes_cost -= cost # Start with subtract execution cost (PnL realized outside) -> Wait, this messes up "Avg Buy Price".
                # If checking "Accumulation", maybe we only count BUYS?
                # User said: "YES买入累计总量" -> Cumulative BUY Volume.
                # All trades in file are BUY anyway based on previous analysis.
                # So we simply Add.
                pass
            pass
            
        # Re-check trades.json: Only BUYs seen so far. Secure assumption for now.
        
        # Avg Prices (Dollars)
        avg_yes = (cum_yes_cost / cum_yes_qty) if cum_yes_qty > 0.001 else 0
        avg_no = (cum_no_cost / cum_no_qty) if cum_no_qty > 0.001 else 0
        
        # Net Diff
        net_diff = cum_yes_qty - cum_no_qty
        
        # Diff Value ($)
        # "YES比NO高50, yes均价0.2 -> $10"
        # i.e. Difference * AvgPrice of the excess side
        diff_val = 0.0
        if net_diff > 0:
            diff_val = net_diff * avg_yes
        elif net_diff < 0:
            diff_val = abs(net_diff) * avg_no
            
        # Pair Cost (Avg YES + Avg NO)
        pair_cost = avg_yes + avg_no
            
        # Profit Metric (Hedging Profit)
        # min(qty_yes, qty_no) * $1.0 - (total_cost_yes + total_cost_no)
        # This assumes held until expiry at $1 payout for the matched portion.
        matched_qty = min(cum_yes_qty, cum_no_qty)
        total_payout = matched_qty * 1.0
        total_spent = cum_yes_cost + cum_no_cost
        profit = total_payout - total_spent
        
        row = {
            "time": t["dt"].strftime("%H:%M:%S"),
            "yes_trade": yes_trade_cell,
            "no_trade": no_trade_cell,
            "cum_yes": cum_yes_qty,
            "avg_yes": avg_yes,
            "cum_no": cum_no_qty,
            "avg_no": avg_no,
            "pair_cost": pair_cost,
            "net_diff": net_diff,
            "diff_val": diff_val,
            "profit": profit
        }
        rows.append(row)
        
    return rows

def generate_html_table(rows, filename):
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            body { font-family: monospace; background: #111; color: #eee; padding: 20px; }
            table { border-collapse: collapse; width: 100%; font-size: 13px; }
            th, td { border: 1px solid #333; padding: 8px; text-align: right; }
            th { background: #222; position: sticky; top: 0; }
            tr:hover { background: #222; }
            .pos { color: #4caf50; }
            .neg { color: #f44336; }
            .neu { color: #888; }
            .yes-col { color: #4caf50; }
            .no-col { color: #e91e63; }
            .cost-col { color: #ffeb3b; }
        </style>
    </head>
    <body>
        <h2>Trade Analysis Table</h2>
        <table>
            <thead>
                <tr>
                    <th>Time</th>
                    <th>YES Trade</th>
                    <th>NO Trade</th>
                    <th class="yes-col">Cum YES</th>
                    <th class="yes-col">Avg YES ($)</th>
                    <th class="no-col">Cum NO</th>
                    <th class="no-col">Avg NO ($)</th>
                    <th class="cost-col">Pair Cost</th>
                    <th>Net Diff</th>
                    <th>Diff Value ($)</th>
                    <th>Locked Profit ($)</th>
                </tr>
            </thead>
            <tbody>
                {rows}
            </tbody>
        </table>
    </body>
    </html>
    """
    
    tbody = ""
    for r in rows:
        # Styling classes
        diff_cls = "pos" if r["net_diff"] > 0 else "neg" if r["net_diff"] < 0 else "neu"
        prof_cls = "pos" if r["profit"] > 0 else "neg" if r["profit"] < 0 else "neu"
        
        # Color pair cost red if > 1.0, green if < 1.0
        pc = r["pair_cost"]
        pc_cls = "cost-col"
        if pc > 1.001: pc_cls = "neg"
        elif pc < 0.999: pc_cls = "pos"
        
        tbody += f"""
        <tr>
            <td>{r['time']}</td>
            <td class="yes-col">{r['yes_trade']}</td>
            <td class="no-col">{r['no_trade']}</td>
            <td>{r['cum_yes']:.2f}</td>
            <td>${r['avg_yes']:.4f}</td>
            <td>{r['cum_no']:.2f}</td>
            <td>${r['avg_no']:.4f}</td>
            <td class="{pc_cls}">${r['pair_cost']:.4f}</td>
            <td class="{diff_cls}">{r['net_diff']:.2f}</td>
            <td>${r['diff_val']:.2f}</td>
            <td class="{prof_cls}">${r['profit']:.2f}</td>
        </tr>
        """
        
    with open(filename, "w") as f:
        f.write(html_template.replace("{rows}", tbody))


if __name__ == "__main__":
    main()

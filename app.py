import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import io
import time
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

# ==============================================================================
# ⚙️ CONFIGURATION
# ==============================================================================
st.set_page_config(page_title="Auto-Paper Bot", layout="wide", page_icon="🦅")

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Buy Price", "Qty", "Stop Loss", "Target", "Entry Time"])
if 'trade_log' not in st.session_state:
    st.session_state.trade_log = pd.DataFrame(columns=["Ticker", "Action", "Price", "Time", "PnL", "Result"])
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = pd.DataFrame()

RISK_PER_TRADE = 1000

# ==============================================================================
# 🧠 HELPER FUNCTIONS (Data & Logic)
# ==============================================================================

@st.cache_data(ttl=600)
def get_nifty500():
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s = requests.get(url, headers=headers).content
        df = pd.read_csv(io.StringIO(s.decode('utf-8')))
        return [x + ".NS" for x in df['Symbol'].tolist()]
    except:
        return ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "INFY.NS", "BHEL.NS", "ITC.NS", "MRF.NS"]

def get_live_prices_bulk(tickers):
    """
    Fetches real-time prices for MULTIPLE tickers in ONE request.
    This fixes the 'Blindness' / 'Loading...' issue.
    """
    if not tickers: return {}
    
    try:
        # Download 1-minute data for all tickers at once
        data = yf.download(tickers, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        
        live_prices = {}
        
        # CASE 1: Single Ticker in list
        if len(tickers) == 1:
            t = tickers[0]
            try:
                price = data['Close'].iloc[-1]
                if isinstance(price, pd.Series): price = price.iloc[0]
                live_prices[t] = float(price)
            except: live_prices[t] = 0.0
            
        # CASE 2: Multiple Tickers in list
        else:
            for t in tickers:
                try:
                    if t in data.columns:
                        val = data[t]['Close'].iloc[-1]
                        if isinstance(val, pd.Series): val = val.values[0]
                        live_prices[t] = float(val)
                    else:
                        live_prices[t] = 0.0
                except: live_prices[t] = 0.0
                
        return live_prices
    except:
        return {}

def run_scanner_snapshot(scan_limit):
    """
    Scans daily data to find setups. 
    Filters strictly for the Top 20 closest to breakout.
    """
    tickers = get_nifty500()
    results = []
    subset = tickers[:scan_limit]
    
    progress = st.progress(0)
    status = st.empty()
    
    # Bulk download daily data (efficient)
    data = yf.download(subset, period="5d", interval="1d", group_by='ticker', progress=False, threads=True)
    
    for i, ticker in enumerate(subset):
        progress.progress((i+1)/len(subset))
        status.write(f"Analyzing {ticker}...")
        
        try:
            # Handle Data Structure
            if len(subset) == 1: df = data
            else:
                if ticker not in data.columns: continue
                df = data[ticker]
            
            if df.empty: continue
            
            # Clean & Check
            df = df.dropna()
            if len(df) < 2: continue
            
            prev_high = float(df['High'].iloc[-2])
            current_close = float(df['Close'].iloc[-1])
            atr = float((df['High'] - df['Low']).mean())
            
            trigger = prev_high * 1.001
            dist_pct = ((current_close - trigger) / trigger) * 100
            
            # THE FUNNEL: Only keep stocks within -2% to +1% of trigger
            if dist_pct > -2.0 and dist_pct < 1.0:
                results.append({
                    "Ticker": ticker,
                    "Trigger": trigger,
                    "Stop Loss": float(trigger - (atr * 1.5)),
                    "Target": float(trigger + (atr * 3)),
                    "Dist": dist_pct
                })
        except: continue
        
    progress.empty()
    status.empty()
    
    # Return Top 20 Closest
    final_df = pd.DataFrame(results)
    if not final_df.empty:
        final_df = final_df.sort_values(by="Dist", ascending=False).head(20)
        
    return final_df

def execute_trade(ticker, price, stop, target):
    if price <= 0: return
    if not st.session_state.portfolio.empty:
        if ticker in st.session_state.portfolio['Ticker'].values: return
            
    risk = price - stop
    qty = max(1, int(RISK_PER_TRADE / risk)) if risk > 0 else 1
    
    new_trade = {
        "Ticker": ticker, "Buy Price": price, "Qty": qty, 
        "Stop Loss": stop, "Target": target, "Entry Time": datetime.now().strftime("%H:%M:%S")
    }
    st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_trade])], ignore_index=True)
    
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([{
        "Ticker": ticker, "Action": "BUY", "Price": price, "Time": datetime.now().strftime("%H:%M"), "PnL": 0, "Result": "OPEN"
    }])], ignore_index=True)
    st.toast(f"⚔️ BOUGHT {ticker} @ {price}")

def close_trade(index, price, reason):
    trade = st.session_state.portfolio.iloc[index]
    pnl = (price - trade['Buy Price']) * trade['Qty']
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([{
        "Ticker": trade['Ticker'], "Action": "SELL", "Price": price, "Time": datetime.now().strftime("%H:%M"), "PnL": round(pnl, 2), "Result": reason
    }])], ignore_index=True)
    st.session_state.portfolio = st.session_state.portfolio.drop(index).reset_index(drop=True)
    st.toast(f"❌ CLOSED {trade['Ticker']} ({reason})")

# ==============================================================================
# 🖥️ DASHBOARD UI
# ==============================================================================
st.title("🦅 Precision Paper Bot (Final Version)")
st.markdown("**Status:** Monitoring Live Targets | **History:** Enabled")

with st.sidebar:
    scan_size = st.slider("Stocks to Analyze", 50, 500, 200)
    enable_auto = st.toggle("✅ Enable Auto-Trading", value=True)
    if st.button("🔴 Reset Portfolio"):
        st.cache_data.clear()
        st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Buy Price", "Qty", "Stop Loss", "Target", "Entry Time"])
        st.session_state.watchlist = pd.DataFrame()
        st.rerun()

# 1. PREPARE TICKER LIST FOR BULK FETCH
all_tickers = []
if not st.session_state.portfolio.empty:
    all_tickers.extend(st.session_state.portfolio['Ticker'].tolist())
if not st.session_state.watchlist.empty:
    all_tickers.extend(st.session_state.watchlist['Ticker'].tolist())

# 2. BULK FETCH (This is the Magic Fix)
live_map = get_live_prices_bulk(list(set(all_tickers)))

col1, col2 = st.columns([1, 4])
if col1.button("🚀 RUN MARKET SCAN", type="primary"):
    with st.spinner(f"Filtering Candidates..."):
        st.session_state.watchlist = run_scanner_snapshot(scan_size)

st.divider()

# 3. ACTIVE TRADES MONITOR
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    disp = []
    for i, row in st.session_state.portfolio.iterrows():
        ticker = row['Ticker']
        buy = row['Buy Price']
        
        # Get Price from Bulk Map
        curr = float(live_map.get(ticker, 0.0))
        
        if curr <= 0: 
            curr = buy # Fallback if data lag
            status = "⚠️ Checking..."
        else:
            status = "ACTIVE"
            if enable_auto:
                if curr <= row['Stop Loss']: close_trade(i, curr, "STOP LOSS"); st.rerun()
                elif curr >= row['Target']: close_trade(i, curr, "TARGET HIT"); st.rerun()
            
        pnl = (curr - buy) * row['Qty']
        disp.append({"Ticker": ticker, "Entry": buy, "Price": f"{curr:.2f}", "P&L": f"{pnl:.2f}", "Status": status})
        
    st.dataframe(pd.DataFrame(disp))
else:
    st.info("No active trades.")

st.divider()

# 4. WATCHLIST MONITOR (Top 20)
st.subheader(f"📡 High-Probability Watchlist ({len(st.session_state.watchlist)})")
if not st.session_state.watchlist.empty:
    w_data = []
    
    for idx, row in st.session_state.watchlist.iterrows():
        ticker = row['Ticker']
        trig = row['Trigger']
        
        # Get Price from Bulk Map
        curr = float(live_map.get(ticker, 0.0))
        
        if curr <= 0:
            w_data.append([ticker, "---", f"{trig:.2f}", "---", "⏳ Loading..."])
            continue
            
        dist = ((curr - trig) / trig) * 100
        
        status = "Waiting"
        if curr > trig:
            status = "🔥 BREAKOUT"
            if enable_auto: execute_trade(ticker, curr, row['Stop Loss'], row['Target']); st.rerun()
        
        w_data.append([ticker, f"{curr:.2f}", f"{trig:.2f}", f"{dist:.2f}%", status])
        
    st.dataframe(pd.DataFrame(w_data, columns=["Ticker", "Live Price", "Trigger", "Dist %", "Status"]))

else:
    st.info("Scanner is empty. Click 'RUN MARKET SCAN'.")

st.divider()

# 5. TRADE HISTORY & DOWNLOAD
st.subheader("📜 Trade History")

if not st.session_state.trade_log.empty:
    # Show the Table (Sorted Newest First)
    st.dataframe(st.session_state.trade_log.sort_index(ascending=False), use_container_width=True)
    
    # Download Button
    csv = st.session_state.trade_log.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="⬇️ Download Trade Log (CSV)",
        data=csv,
        file_name="paper_trade_history.csv",
        mime="text/csv",
        type="primary"
    )
else:
    st.info("No trades executed yet.")

# 6. AUTO REFRESH (30s)
if enable_auto:
    time.sleep(30)
    st.rerun()
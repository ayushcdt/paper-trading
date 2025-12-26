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
# 🧠 ENGINE: THE "FUNNEL" (Scan Many -> Monitor Few)
# ==============================================================================

def get_live_price(ticker):
    """
    Fetches price for a SINGLE ticker.
    Used only for the small list of active targets.
    """
    try:
        # Use history() which is often more reliable than download() for single files
        stock = yf.Ticker(ticker)
        data = stock.history(period="1d", interval="1m")
        if not data.empty:
            return float(data['Close'].iloc[-1])
        return 0.0
    except:
        return 0.0

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

def run_scanner_snapshot(scan_limit):
    """
    This runs ONCE. It downloads daily data to find setups.
    It does NOT need to be live.
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
            # Handle Single vs Multi-Index
            if len(subset) == 1:
                df = data
            else:
                if ticker not in data.columns: continue
                df = data[ticker]
            
            # Logic: Yesterday's High Breakout
            if df.empty: continue
            
            # Clean NaN rows
            df = df.dropna()
            if len(df) < 2: continue
            
            prev_high = float(df['High'].iloc[-2]) # Yesterday's High
            current_close = float(df['Close'].iloc[-1])
            atr = float((df['High'] - df['Low']).mean())
            
            trigger = prev_high * 1.001
            
            # Filter: Only keep stocks that are CLOSE to trigger (within 2%)
            # This is the FUNNEL. We don't care about stocks 10% away.
            dist_pct = ((current_close - trigger) / trigger) * 100
            
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
    
    # Sort by closest to breakout
    final_df = pd.DataFrame(results)
    if not final_df.empty:
        final_df = final_df.sort_values(by="Dist", ascending=False).head(20) # KEEP ONLY TOP 20
        
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
st.title("🦅 Precision Paper Bot")
st.markdown("**Strategy:** Scan 200 -> Filter Top 20 -> Monitor Live")

with st.sidebar:
    scan_size = st.slider("Stocks to Analyze", 50, 500, 100)
    enable_auto = st.toggle("✅ Enable Auto-Trading", value=True)
    if st.button("🔴 Stop / Reset"):
        st.cache_data.clear()
        st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Buy Price", "Qty", "Stop Loss", "Target", "Entry Time"])
        st.session_state.watchlist = pd.DataFrame()
        st.rerun()

# 1. SCANNER (Manual Trigger)
if st.button("🚀 RUN MARKET SCAN", type="primary"):
    with st.spinner(f"Filtering Top 20 Candidates from {scan_size} stocks..."):
        st.session_state.watchlist = run_scanner_snapshot(scan_size)

st.divider()

# 2. ACTIVE TRADES MONITOR (Live)
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    disp = []
    for i, row in st.session_state.portfolio.iterrows():
        ticker = row['Ticker']
        # Fetch Live Price Single
        curr = get_live_price(ticker)
        
        if curr <= 0: curr = row['Buy Price'] # Fallback
        
        pnl = (curr - row['Buy Price']) * row['Qty']
        
        if enable_auto and curr > 0:
            if curr <= row['Stop Loss']: close_trade(i, curr, "STOP LOSS"); st.rerun()
            elif curr >= row['Target']: close_trade(i, curr, "TARGET HIT"); st.rerun()
            
        disp.append({"Ticker": ticker, "Entry": row['Buy Price'], "Current": f"{curr:.2f}", "P&L": f"{pnl:.2f}"})
    st.dataframe(pd.DataFrame(disp))
else:
    st.info("No active trades.")

st.divider()

# 3. WATCHLIST MONITOR (Live - Only Top 20)
st.subheader(f"📡 High-Probability Watchlist ({len(st.session_state.watchlist)})")
if not st.session_state.watchlist.empty:
    
    # We loop through the SHORTLIST only
    live_data = []
    for idx, row in st.session_state.watchlist.iterrows():
        ticker = row['Ticker']
        trig = row['Trigger']
        
        # Fetch Live Price
        curr = get_live_price(ticker)
        
        if curr <= 0:
            live_data.append([ticker, "---", f"{trig:.2f}", "---", "⏳"])
            continue
            
        dist = ((curr - trig) / trig) * 100
        
        status = "Waiting"
        if curr > trig:
            status = "🔥 BREAKOUT"
            if enable_auto: execute_trade(ticker, curr, row['Stop Loss'], row['Target']); st.rerun()
        
        live_data.append([ticker, f"{curr:.2f}", f"{trig:.2f}", f"{dist:.2f}%", status])
        time.sleep(0.1) # Small delay to prevent block
        
    st.dataframe(pd.DataFrame(live_data, columns=["Ticker", "Live Price", "Trigger", "Dist %", "Status"]))

else:
    st.info("Scanner is empty. Click 'RUN MARKET SCAN' to find top targets.")

# 4. SLOW AUTO REFRESH (60s)
if enable_auto:
    time.sleep(60)
    st.rerun()
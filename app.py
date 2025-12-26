import streamlit as st
import pandas as pd
import yfinance as yf
import requests
import io
import time
from datetime import datetime
import warnings
import numpy as np

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
# 🧠 SMART BATCH ENGINE (THE FIX)
# ==============================================================================

def get_live_prices_batched(ticker_list):
    """
    Splits the list into batches of 50 to prevent Yahoo blocking/NaN errors.
    """
    if not ticker_list:
        return {}
    
    live_prices = {}
    
    # REMOVE DUPLICATES & CLEAN
    ticker_list = list(set(ticker_list))
    
    # BATCH SIZE = 50 (Safe Limit)
    BATCH_SIZE = 50
    total_batches = (len(ticker_list) // BATCH_SIZE) + 1
    
    # PROGRESS BAR FOR DATA FETCH
    fetch_bar = st.progress(0)
    
    for i in range(0, len(ticker_list), BATCH_SIZE):
        batch = ticker_list[i : i + BATCH_SIZE]
        if not batch: continue
        
        try:
            # Update Progress
            fetch_bar.progress((i / len(ticker_list)))
            
            # Download Batch
            data = yf.download(batch, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
            
            # CASE 1: Single Ticker in Batch
            if len(batch) == 1:
                t = batch[0]
                try:
                    price = data['Close'].iloc[-1]
                    if isinstance(price, pd.Series): price = price.iloc[0]
                    live_prices[t] = float(price)
                except: live_prices[t] = 0.0
            
            # CASE 2: Multiple Tickers
            else:
                for t in batch:
                    try:
                        if t in data.columns:
                            val = data[t]['Close'].iloc[-1]
                            if isinstance(val, pd.Series): val = val.values[0]
                            live_prices[t] = float(val)
                        else:
                            live_prices[t] = 0.0
                    except: live_prices[t] = 0.0
            
            # PAUSE to be polite to the server
            time.sleep(0.5)
            
        except Exception as e:
            continue
            
    fetch_bar.empty()
    return live_prices

@st.cache_data(ttl=600)
def get_nifty500():
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s = requests.get(url, headers=headers).content
        df = pd.read_csv(io.StringIO(s.decode('utf-8')))
        return [x + ".NS" for x in df['Symbol'].tolist()]
    except:
        return ["RELIANCE.NS", "TCS.NS", "ADANIENSOL.NS", "APOLLOHOSP.NS", "GMDCLTD.NS", "CONCOR.NS", "ASTERDM.NS"]

def run_scanner(scan_limit):
    tickers = get_nifty500()
    results = []
    
    # LIMIT SCAN
    subset = tickers[:scan_limit] 
    
    # USE BATCH FETCH FOR SCANNING TOO (Much Faster/Safer)
    # We fetch current price first to filter, then get history only for candidates
    # This is a complex optimization, keeping simple loop for now but adding error handling
    
    progress = st.progress(0)
    status = st.empty()
    
    for i, ticker in enumerate(subset):
        if i % 5 == 0: 
            progress.progress((i+1)/len(subset))
            status.text(f"Scanning {ticker} ({i}/{len(subset)})...")
            
        try:
            # Get 5 days history
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if df.empty: continue
            
            prev_high = df['High'].iloc[:-1].max()
            atr = (df['High'] - df['Low']).mean()
            current_close = df['Close'].iloc[-1]
            
            trigger = float(prev_high * 1.001)
            
            # Store Result
            results.append({
                "Ticker": ticker,
                "Trigger": trigger,
                "Stop Loss": float(trigger - (atr * 1.5)),
                "Target": float(trigger + (atr * 3))
            })
        except: continue
    
    progress.empty()
    status.empty()
    return pd.DataFrame(results)

def execute_trade(ticker, price, stop, target):
    # DONT BUY IF PRICE IS FAKE (0.0)
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
st.title("🦅 Full-Market Paper Bot (Safe Mode)")

# SIDEBAR
with st.sidebar:
    st.header("⚙️ Controls")
    scan_size = st.slider("Stocks to Scan", 10, 500, 100)
    enable_auto = st.toggle("✅ Enable Auto-Trading", value=True)
    if st.button("🧹 Reset System"):
        st.cache_data.clear()
        st.rerun()

# 1. FETCH DATA (BATCHED)
all_tickers = []
if not st.session_state.portfolio.empty:
    all_tickers.extend(st.session_state.portfolio['Ticker'].tolist())
if not st.session_state.watchlist.empty:
    all_tickers.extend(st.session_state.watchlist['Ticker'].tolist())

# Fetch prices safely
live_map = get_live_prices_batched(list(set(all_tickers)))

col1, col2 = st.columns([1, 4])
if col1.button("🚀 START SCAN", type="primary"):
    with st.spinner(f"Scanning Top {scan_size}..."):
        st.session_state.watchlist = run_scanner(scan_size)

st.divider()

# 2. ACTIVE POSITIONS (WITH 0.00 PROTECTION)
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    disp = []
    for i, row in st.session_state.portfolio.iterrows():
        ticker = row['Ticker']
        buy_price = row['Buy Price']
        
        # Get Current Price
        curr = float(live_map.get(ticker, 0.0))
        
        # PROTECTION: If Price is 0 or NaN, use Buy Price (Show 0 P&L)
        if curr <= 1.0 or np.isnan(curr):
            curr = buy_price
            status = "⚠️ DATA LAG"
            pnl = 0.0
        else:
            status = "ACTIVE"
            pnl = (curr - buy_price) * row['Qty']
            
            # AUTO EXIT LOGIC
            if enable_auto:
                if curr <= row['Stop Loss']: 
                    close_trade(i, curr, "STOP LOSS"); st.rerun()
                elif curr >= row['Target']: 
                    close_trade(i, curr, "TARGET HIT"); st.rerun()

        disp.append({
            "Ticker": ticker, 
            "Buy": buy_price, 
            "Current": f"{curr:.2f}",
            "Qty": row['Qty'], 
            "P&L": f"{pnl:.2f}", 
            "Status": status
        })
    st.dataframe(pd.DataFrame(disp))
else:
    st.info("No active trades.")

st.divider()

# 3. WATCHLIST
st.subheader(f"📡 Scanner Watchlist ({len(st.session_state.watchlist)} Targets)")
if not st.session_state.watchlist.empty:
    # Convert to list for display
    w_data = []
    for idx, row in st.session_state.watchlist.iterrows():
        ticker = row['Ticker']
        trig = float(row['Trigger'])
        curr = float(live_map.get(ticker, 0.0))
        
        if curr <= 0:
            w_data.append([ticker, "Waiting Data...", f"{trig:.2f}", "---", "⏳"])
            continue
            
        dist = ((curr - trig) / trig) * 100
        
        emoji = "⏳"
        if curr > trig:
            emoji = "🔥"
            if enable_auto: execute_trade(ticker, curr, row['Stop Loss'], row['Target']); st.rerun()
        elif dist > -0.5:
            emoji = "👀"
            
        w_data.append([ticker, f"{curr:.2f}", f"{trig:.2f}", f"{dist:.2f}%", emoji])
        
    st.dataframe(pd.DataFrame(w_data, columns=["Ticker", "Price", "Trigger", "Dist", "Status"]), height=400)

else:
    st.info("Scanner is empty. Click START SCAN.")

st.divider()
st.subheader("📜 Trade History")
st.dataframe(st.session_state.trade_log)

if enable_auto:
    time.sleep(30)
    st.rerun()
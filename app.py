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
st.set_page_config(page_title="Auto-Paper Bot", layout="wide", page_icon="🤖")

if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Buy Price", "Qty", "Stop Loss", "Target", "Entry Time"])
if 'trade_log' not in st.session_state:
    st.session_state.trade_log = pd.DataFrame(columns=["Ticker", "Action", "Price", "Time", "PnL", "Result"])
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = pd.DataFrame()

RISK_PER_TRADE = 1000

# ==============================================================================
# 🧠 CORE ENGINE
# ==============================================================================

def get_live_prices_bulk(ticker_list):
    """
    Downloads data for ALL tickers in one single request.
    """
    if not ticker_list:
        return {}
    
    try:
        data = yf.download(ticker_list, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        live_prices = {}
        
        # CASE 1: Single Ticker
        if len(ticker_list) == 1:
            ticker = ticker_list[0]
            if not data.empty:
                try:
                    price = data['Close'].iloc[-1]
                    if isinstance(price, pd.Series): price = price.iloc[0]
                    live_prices[ticker] = float(price)
                except:
                    live_prices[ticker] = 0.0

        # CASE 2: Multiple Tickers
        else:
            for ticker in ticker_list:
                try:
                    if ticker in data.columns:
                        val = data[ticker]['Close'].iloc[-1]
                        if isinstance(val, pd.Series): val = val.values[0]
                        live_prices[ticker] = float(val)
                    else:
                        live_prices[ticker] = 0.0
                except:
                    live_prices[ticker] = 0.0
                    
        return live_prices
    except:
        return {}

@st.cache_data(ttl=300)
def get_nifty500():
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s = requests.get(url, headers=headers).content
        df = pd.read_csv(io.StringIO(s.decode('utf-8')))
        return [x + ".NS" for x in df['Symbol'].tolist()]
    except:
        # Fallback if NSE is down
        return ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "ITC.NS", 
                "SBIN.NS", "BHARTIARTL.NS", "LICI.NS", "HINDUNILVR.NS", "ADANIENSOL.NS", "APOLLOHOSP.NS"]

def run_scanner(scan_limit):
    tickers = get_nifty500()
    results = []
    
    progress = st.progress(0)
    status_text = st.empty()
    
    # ---------------------------------------------------------
    # SAFE SLICING LOGIC (Controlled by Slider)
    # ---------------------------------------------------------
    subset = tickers[:scan_limit] 
    
    for i, ticker in enumerate(subset):
        progress.progress((i+1)/len(subset))
        status_text.text(f"Scanning {i+1}/{len(subset)}: {ticker}...")
        try:
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if df.empty: continue
            
            prev_high = df['High'].iloc[:-1].max()
            atr = (df['High'] - df['Low']).mean()
            
            trigger = float(prev_high * 1.001)
            
            results.append({
                "Ticker": ticker,
                "Trigger": trigger,
                "Stop Loss": float(trigger - (atr * 1.5)),
                "Target": float(trigger + (atr * 3))
            })
        except: continue
    
    progress.empty()
    status_text.empty()
    return pd.DataFrame(results)

def execute_trade(ticker, price, stop, target):
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

def close_trade(index, price, reason):
    trade = st.session_state.portfolio.iloc[index]
    pnl = (price - trade['Buy Price']) * trade['Qty']
    
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([{
        "Ticker": trade['Ticker'], "Action": "SELL", "Price": price, "Time": datetime.now().strftime("%H:%M"), "PnL": round(pnl, 2), "Result": reason
    }])], ignore_index=True)
    
    st.session_state.portfolio = st.session_state.portfolio.drop(index).reset_index(drop=True)

# ==============================================================================
# 🖥️ DASHBOARD UI
# ==============================================================================
st.title("🦅 Full-Market Paper Bot")

# SIDEBAR CONTROLS
with st.sidebar:
    st.header("⚙️ Scanner Settings")
    # THE MAGIC SLIDER - CONTROLS SCAN SIZE SAFELY
    scan_size = st.slider("Stocks to Scan", min_value=10, max_value=500, value=50, step=10)
    enable_auto = st.toggle("✅ Enable Auto-Trading", value=True)
    if st.button("🧹 Clear Cache & Reset"):
        st.cache_data.clear()
        st.rerun()

st.markdown(f"**Status:** Live | **Market:** Nifty 500 | **Watching:** {scan_size} Stocks")

col1, col2 = st.columns([1, 4])
if col1.button("🚀 START SCAN", type="primary"):
    with st.spinner(f"Analyzing Top {scan_size} Stocks..."):
        st.session_state.watchlist = run_scanner(scan_size)

st.divider()

# 1. BULK FETCH DATA
all_tickers = []
if not st.session_state.portfolio.empty:
    all_tickers.extend(st.session_state.portfolio['Ticker'].tolist())
if not st.session_state.watchlist.empty:
    all_tickers.extend(st.session_state.watchlist['Ticker'].tolist())

live_map = get_live_prices_bulk(list(set(all_tickers)))

# 2. ACTIVE POSITIONS
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    disp = []
    for i, row in st.session_state.portfolio.iterrows():
        curr = float(live_map.get(row['Ticker'], row['Buy Price']))
        pnl = (curr - row['Buy Price']) * row['Qty']
        
        if enable_auto and curr > 0:
            if curr <= row['Stop Loss']: 
                close_trade(i, curr, "STOP LOSS"); st.rerun()
            elif curr >= row['Target']: 
                close_trade(i, curr, "TARGET HIT"); st.rerun()
        
        disp.append({
            "Ticker": row['Ticker'], "Buy": row['Buy Price'], "Current": f"{curr:.2f}",
            "Qty": row['Qty'], "P&L": f"{pnl:.2f}", "SL": f"{row['Stop Loss']:.2f}", "TGT": f"{row['Target']:.2f}"
        })
    st.dataframe(pd.DataFrame(disp))
else:
    st.info("No active trades.")

st.divider()

# 3. WATCHLIST
st.subheader(f"📡 Scanner Watchlist ({len(st.session_state.watchlist)} Targets)")
if not st.session_state.watchlist.empty:
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1, 1, 1.5])
        c1.write("**Ticker**"); c2.write("**Price**"); c3.write("**Trigger**"); c4.write("**Dist**"); c5.write("**Status**")
        st.write("---")
        
        for idx, row in st.session_state.watchlist.iterrows():
            curr = float(live_map.get(row['Ticker'], 0.0))
            if curr == 0.0: continue
            
            trig = float(row['Trigger'])
            dist = ((curr - trig) / trig) * 100
            
            emoji, color = "⏳", "gray"
            if curr > trig:
                emoji, color = "🔥", "green"
                if enable_auto: execute_trade(row['Ticker'], curr, row['Stop Loss'], row['Target']); st.rerun()
            elif dist > -0.5:
                emoji, color = "👀", "orange"
                
            c1.write(row['Ticker'])
            c2.write(f"{curr:.2f}")
            c3.write(f"{trig:.2f}")
            c4.markdown(f":{color}[{dist:.2f}%]")
            c5.markdown(f":{color}[{emoji}]")
else:
    st.info("Scanner is empty. Adjust slider in Sidebar and click START SCAN.")

st.divider()
st.subheader("📜 Trade History")
st.dataframe(st.session_state.trade_log)

if enable_auto:
    time.sleep(30)
    st.rerun()
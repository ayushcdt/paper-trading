import streamlit as st
import pandas as pd
import yfinance as yf
import pandas_ta as ta
import requests
import io
import time
from datetime import datetime
import warnings

warnings.filterwarnings('ignore')

# ==============================================================================
# ⚙️ CONFIGURATION & STATE
# ==============================================================================
st.set_page_config(page_title="Auto-Paper Bot", layout="wide", page_icon="🤖")

# Initialize Virtual Portfolio in Session State
if 'portfolio' not in st.session_state:
    st.session_state.portfolio = pd.DataFrame(columns=["Ticker", "Buy Price", "Qty", "Stop Loss", "Target", "Entry Time"])
if 'trade_log' not in st.session_state:
    st.session_state.trade_log = pd.DataFrame(columns=["Ticker", "Action", "Price", "Time", "PnL", "Result"])
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = pd.DataFrame()

CAPITAL = 100000  # Virtual Capital
RISK_PER_TRADE = 1000  # Max loss per trade

# ==============================================================================
# 🧠 TRADING ENGINES
# ==============================================================================

@st.cache_data(ttl=300)
def get_nifty500():
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s = requests.get(url, headers=headers).content
        df = pd.read_csv(io.StringIO(s.decode('utf-8')))
        return [x + ".NS" for x in df['Symbol'].tolist()]
    except:
        return ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "MARUTI.NS", "BHEL.NS", "BIKAJI.NS"]

def run_scanner():
    """Scans for setups"""
    tickers = get_nifty500()
    results = []
    
    # Fast scan of first 50 for demo speed
    progress = st.progress(0)
    for i, ticker in enumerate(tickers[:50]):
        progress.progress((i+1)/50)
        try:
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            
            # Simple Breakout Logic (Yesterday's High)
            prev_high = df['High'].iloc[:-1].max()
            curr_close = df['Close'].iloc[-1]
            atr = (df['High'] - df['Low']).mean()
            
            # Trigger: Recent High
            trigger = prev_high * 1.001
            
            results.append({
                "Ticker": ticker,
                "Trigger": trigger,
                "Stop Loss": trigger - (atr * 1.5),
                "Target": trigger + (atr * 3)
            })
        except: continue
    
    progress.empty()
    return pd.DataFrame(results)

def execute_paper_trade(ticker, price, stop, target):
    """AUTO-BUY LOGIC"""
    # 1. Check if already in portfolio
    if not st.session_state.portfolio.empty:
        if ticker in st.session_state.portfolio['Ticker'].values:
            return # Already bought
            
    # 2. Position Sizing
    risk = price - stop
    qty = int(RISK_PER_TRADE / risk) if risk > 0 else 1
    if qty < 1: qty = 1
    
    # 3. Add to Portfolio
    new_trade = {
        "Ticker": ticker,
        "Buy Price": price,
        "Qty": qty,
        "Stop Loss": stop,
        "Target": target,
        "Entry Time": datetime.now().strftime("%H:%M:%S")
    }
    st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_trade])], ignore_index=True)
    
    # 4. Log it
    log_entry = {
        "Ticker": ticker, "Action": "BUY", "Price": price, 
        "Time": datetime.now().strftime("%H:%M"), "PnL": 0, "Result": "OPEN"
    }
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([log_entry])], ignore_index=True)
    st.toast(f"🤖 BOUGHT {ticker} at {price}", icon="🛒")

def close_position(index, price, reason):
    """AUTO-SELL LOGIC"""
    trade = st.session_state.portfolio.iloc[index]
    pnl = (price - trade['Buy Price']) * trade['Qty']
    
    # Log Exit
    log_entry = {
        "Ticker": trade['Ticker'], "Action": "SELL", "Price": price, 
        "Time": datetime.now().strftime("%H:%M"), "PnL": round(pnl, 2), "Result": reason
    }
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([log_entry])], ignore_index=True)
    
    # Remove from Portfolio
    st.session_state.portfolio = st.session_state.portfolio.drop(index).reset_index(drop=True)
    st.toast(f"❌ SOLD {trade['Ticker']} ({reason}) PnL: {pnl:.0f}", icon="💰")

# ==============================================================================
# 🖥️ DASHBOARD UI
# ==============================================================================
st.title("🤖 Autonomous Paper-Trading Bot")

# 1. CONTROL PANEL
col1, col2 = st.columns([1, 4])
if col1.button("🚀 START MORNING SCAN"):
    with st.spinner("Scanning Market..."):
        st.session_state.watchlist = run_scanner()

enable_auto = st.checkbox("✅ ENABLE AUTO-TRADING", value=True)

st.divider()

# 2. LIVE PORTFOLIO (THE CRITICAL PART)
st.subheader("💼 Active Positions (Live Tracking)")
if not st.session_state.portfolio.empty:
    portfolio_display = []
    
    for i, row in st.session_state.portfolio.iterrows():
        # Get Live Price for Portfolio
        try:
            live = yf.download(row['Ticker'], period="1d", interval="1m", progress=False)
            curr = live['Close'].iloc[-1] if not live.empty else row['Buy Price']
            
            pnl = (curr - row['Buy Price']) * row['Qty']
            pnl_pct = ((curr - row['Buy Price']) / row['Buy Price']) * 100
            
            # EXIT LOGIC CHECK
            status = "HOLD"
            if enable_auto:
                if curr <= row['Stop Loss']:
                    close_position(i, curr, "STOP LOSS")
                    st.rerun() # Refresh immediately
                elif curr >= row['Target']:
                    close_position(i, curr, "TARGET HIT")
                    st.rerun()
            
            portfolio_display.append({
                "Ticker": row['Ticker'], "Buy": row['Buy Price'], "Curr": f"{curr:.2f}",
                "Qty": row['Qty'], "P&L": f"{pnl:.2f}", "Return": f"{pnl_pct:.2f}%",
                "SL": f"{row['Stop Loss']:.2f}", "TGT": f"{row['Target']:.2f}"
            })
        except: continue
        
    st.dataframe(pd.DataFrame(portfolio_display))
else:
    st.info("No active positions. Scanning for entries...")

st.divider()

# 3. WATCHLIST & AUTO-ENTRY LOGIC
st.subheader("📡 Scanner Watchlist")
if not st.session_state.watchlist.empty:
    for i, row in st.session_state.watchlist.iterrows():
        # Check triggers
        try:
            live = yf.download(row['Ticker'], period="1d", interval="1m", progress=False)
            if live.empty: continue
            curr = live['Close'].iloc[-1]
            
            # TRIGGER CONDITION
            if curr > row['Trigger']:
                st.success(f"🔥 BREAKOUT: {row['Ticker']} at {curr}")
                if enable_auto:
                    execute_paper_trade(row['Ticker'], curr, row['Stop Loss'], row['Target'])
                    st.rerun()
                    
        except: continue
        
    st.dataframe(st.session_state.watchlist)

st.divider()

# 4. TRADE HISTORY
st.subheader("📜 Trade History")
st.dataframe(st.session_state.trade_log)

# 5. AUTO REFRESH LOOP
if enable_auto:
    time.sleep(30) # Wait 30 seconds
    st.rerun()     # Refresh page to check prices again
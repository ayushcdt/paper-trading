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
# ⚙️ CONFIG & PAGE SETUP
# ==============================================================================
st.set_page_config(page_title="Institutional Trading Desk", layout="wide", page_icon="📈")

# Initialize Session State (To remember data between refreshes)
if 'watchlist' not in st.session_state:
    st.session_state.watchlist = pd.DataFrame()
if 'log' not in st.session_state:
    st.session_state.log = []

# ==============================================================================
# 🧠 CORE LOGIC FUNCTIONS
# ==============================================================================

@st.cache_data(ttl=300) # Cache Nifty list for 5 mins to save speed
def get_nifty500_tickers():
    try:
        url = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s = requests.get(url, headers=headers).content
        df = pd.read_csv(io.StringIO(s.decode('utf-8')))
        return [x + ".NS" for x in df['Symbol'].tolist()]
    except:
        return ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "MARUTI.NS", "LT.NS", "INFY.NS", "ITC.NS", "ICICIBANK.NS"]

def check_market_health():
    """Pillar 1: The Market Veto"""
    try:
        nifty = yf.download("^NSEI", period="1d", interval="15m", progress=False)
        if isinstance(nifty.columns, pd.MultiIndex): nifty.columns = nifty.columns.get_level_values(0)
        
        nifty_change = ((nifty['Close'].iloc[-1] - nifty['Open'].iloc[0]) / nifty['Open'].iloc[0]) * 100
        
        vix = yf.download("^INDIAVIX", period="1d", progress=False)
        current_vix = vix['Close'].iloc[-1]
        
        status = "UNKNOWN"
        color = "grey"
        
        if nifty_change < -0.8:
            status = "🛑 CRITICAL (Nifty Bleeding)"
            color = "red"
        elif current_vix > 22:
            status = "⚠️ CAUTION (High Volatility)"
            color = "orange"
        else:
            status = "✅ STABLE (Safe to Trade)"
            color = "green"
            
        return status, color, nifty_change, current_vix
    except:
        return "⚠️ DATA ERROR", "orange", 0, 0

def run_scanner():
    """Pillar 2: The VCP + Sector Scanner"""
    tickers = get_nifty500_tickers()
    results = []
    
    progress_bar = st.progress(0)
    status_text = st.empty()
    
    # Scanning top 100 for speed demo (Remove [:100] for full scan)
    scan_list = tickers[:100] 
    
    for i, ticker in enumerate(scan_list):
        if i % 10 == 0:
            status_text.text(f"Scanning {ticker} ({i}/{len(scan_list)})...")
            progress_bar.progress(i / len(scan_list))
            
        try:
            df = yf.download(ticker, period="1y", interval="1d", progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            
            close = df['Close'].iloc[-1]
            sma_50 = ta.sma(df['Close'], length=50).iloc[-1]
            atr = ta.atr(df['High'], df['Low'], df['Close'], length=14).iloc[-1]
            atr_pct = (atr / close) * 100
            
            # 1. Trend (Above 50 SMA) & 2. VCP Tightness (ATR < 2.5%)
            if close > sma_50 and atr_pct < 2.5:
                # 3. Get 15-min Range (The Trigger)
                intraday = yf.download(ticker, period="1d", interval="15m", progress=False)
                if not intraday.empty:
                    if isinstance(intraday.columns, pd.MultiIndex): intraday.columns = intraday.columns.get_level_values(0)
                    range_high = intraday['High'].iloc[0] # 9:15 candle high
                    
                    results.append({
                        "Ticker": ticker,
                        "Price": close,
                        "Trigger": range_high * 1.001, # 0.1% buffer
                        "Stop Loss": intraday['Low'].iloc[0],
                        "ATR%": round(atr_pct, 2)
                    })
        except: continue
        
    progress_bar.empty()
    status_text.empty()
    return pd.DataFrame(results)

# ==============================================================================
# 🖥️ UI LAYOUT
# ==============================================================================
st.title("🦅 Institutional VCP Scanner")
st.markdown("Automated Market Regime Filter • Sector Logic • VSA Confirmation")

# 1. MARKET HEALTH SECTION
status, color, n_chg, vix = check_market_health()
st.subheader("1. Market Regime")
col1, col2, col3 = st.columns(3)
col1.metric("Nifty 50 Change", f"{n_chg:.2f}%")
col2.metric("India VIX", f"{vix:.2f}")
col3.markdown(f":{color}[**{status}**]")

st.divider()

# 2. SCANNER SECTION
st.subheader("2. Opportunity Scanner")
col_scan, col_reset = st.columns([1, 4])
if col_scan.button("🚀 RUN MORNING SCAN", type="primary"):
    with st.spinner("Analyzing Nifty 500 Market Structure..."):
        st.session_state.watchlist = run_scanner()
        st.success(f"Scan Complete. Found {len(st.session_state.watchlist)} setups.")

# Display Watchlist
if not st.session_state.watchlist.empty:
    st.dataframe(st.session_state.watchlist.style.format({"Price": "{:.2f}", "Trigger": "{:.2f}", "Stop Loss": "{:.2f}"}))
else:
    st.info("No candidates yet. Click 'Run Morning Scan' to start.")

st.divider()

# 3. LIVE MONITOR SECTION
st.subheader("3. Live Execution Monitor")
auto_refresh = st.checkbox("🔄 Enable Auto-Refresh (Every 60s)")

if not st.session_state.watchlist.empty:
    live_data = []
    
    # Fetch live prices for watchlist
    for index, row in st.session_state.watchlist.iterrows():
        try:
            ticker = row['Ticker']
            trigger = row['Trigger']
            
            data = yf.download(ticker, period="1d", interval="1m", progress=False)
            if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
            
            curr_price = data['Close'].iloc[-1]
            curr_vol = data['Volume'].iloc[-1]
            
            # LOGIC: STATUS CHECK
            status = "⏳ WAITING"
            
            if curr_price > trigger:
                status = "🔥 BREAKOUT!"
                # Add VSA Check (Visual only for now)
                if curr_vol > 5000: status += " (Vol Confirm)"
            
            live_data.append({
                "Ticker": ticker,
                "Current Price": curr_price,
                "Trigger Level": trigger,
                "Distance %": f"{((curr_price - trigger)/trigger)*100:.2f}%",
                "STATUS": status
            })
        except: continue
        
    live_df = pd.DataFrame(live_data)
    
    # COLOR CODING THE TABLE
    def highlight_breakout(row):
        color = 'background-color: #90ee90' if "BREAKOUT" in row.STATUS else ''
        return [color] * len(row)

    st.table(live_df.style.apply(highlight_breakout, axis=1).format({"Current Price": "{:.2f}", "Trigger Level": "{:.2f}"}))
    
    # AUTO REFRESH LOOP
    if auto_refresh:
        time.sleep(60)
        st.rerun()

else:
    st.write("Waiting for scanner results...")
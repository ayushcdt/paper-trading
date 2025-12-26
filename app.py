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
# 🧠 OPTIMIZED ENGINE (FIXED FOR SERIES ERROR)
# ==============================================================================

def get_live_prices_bulk(ticker_list):
    """
    High-Performance Bulk Fetch: 
    Downloads all stocks in 1 request.
    FIXED: Ensures prices are returned as simple Floats, not Pandas Series.
    """
    if not ticker_list:
        return {}
    
    try:
        # Download all data at once
        data = yf.download(ticker_list, period="1d", interval="1m", group_by='ticker', progress=False, threads=True)
        
        live_prices = {}
        
        # CASE 1: Single Ticker (Structure is Flat)
        if len(ticker_list) == 1:
            ticker = ticker_list[0]
            if not data.empty:
                # Force convert to scalar float using .iloc[-1].item()
                try:
                    price = data['Close'].iloc[-1]
                    if isinstance(price, pd.Series): 
                        price = price.iloc[0] # Handle rare double-series case
                    live_prices[ticker] = float(price)
                except:
                    live_prices[ticker] = 0.0

        # CASE 2: Multiple Tickers (Structure is Nested)
        else:
            for ticker in ticker_list:
                try:
                    # Check for data existence
                    if ticker in data.columns:
                        # Extract the 'Close' series
                        close_series = data[ticker]['Close']
                        
                        # Get the last value safely
                        if not close_series.empty:
                            raw_price = close_series.iloc[-1]
                            
                            # CRITICAL FIX: Ensure it's a float, not a Series
                            if isinstance(raw_price, pd.Series):
                                raw_price = raw_price.values[0]
                                
                            live_prices[ticker] = float(raw_price)
                        else:
                            live_prices[ticker] = 0.0
                    else:
                        live_prices[ticker] = 0.0
                except Exception:
                    live_prices[ticker] = 0.0
                    
        return live_prices
    except Exception as e:
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
        return ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "MARUTI.NS", "BHEL.NS", "BIKAJI.NS", "ADANIENSOL.NS", "APOLLOHOSP.NS"]

def run_scanner():
    tickers = get_nifty500()
    results = []
    
    progress = st.progress(0)
    status_text = st.empty()
    
    # Scanning first 50 stocks
    scan_limit = tickers
    subset = tickers[:scan_limit]
    
    for i, ticker in enumerate(subset):
        progress.progress((i+1)/scan_limit)
        status_text.text(f"Scanning {ticker}...")
        try:
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if df.empty: continue
            
            # Logic: Breakout of Yesterday's High
            prev_high = df['High'].iloc[:-1].max()
            atr = (df['High'] - df['Low']).mean()
            
            trigger = prev_high * 1.001
            
            # Ensure Trigger is a float
            trigger = float(trigger)
            
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

def execute_paper_trade(ticker, price, stop, target):
    if not st.session_state.portfolio.empty:
        if ticker in st.session_state.portfolio['Ticker'].values:
            return 
            
    risk = price - stop
    qty = int(RISK_PER_TRADE / risk) if risk > 0 else 1
    if qty < 1: qty = 1
    
    new_trade = {
        "Ticker": ticker, "Buy Price": price, "Qty": qty, 
        "Stop Loss": stop, "Target": target, "Entry Time": datetime.now().strftime("%H:%M:%S")
    }
    st.session_state.portfolio = pd.concat([st.session_state.portfolio, pd.DataFrame([new_trade])], ignore_index=True)
    
    log_entry = {
        "Ticker": ticker, "Action": "BUY", "Price": price, 
        "Time": datetime.now().strftime("%H:%M"), "PnL": 0, "Result": "OPEN"
    }
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([log_entry])], ignore_index=True)
    st.toast(f"🤖 BOUGHT {ticker}", icon="🛒")

def close_position(index, price, reason):
    trade = st.session_state.portfolio.iloc[index]
    pnl = (price - trade['Buy Price']) * trade['Qty']
    
    log_entry = {
        "Ticker": trade['Ticker'], "Action": "SELL", "Price": price, 
        "Time": datetime.now().strftime("%H:%M"), "PnL": round(pnl, 2), "Result": reason
    }
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([log_entry])], ignore_index=True)
    
    st.session_state.portfolio = st.session_state.portfolio.drop(index).reset_index(drop=True)
    st.toast(f"❌ SOLD {trade['Ticker']} PnL: {pnl:.0f}", icon="💰")

# ==============================================================================
# 🖥️ DASHBOARD UI
# ==============================================================================
st.title("⚡ High-Speed Paper Bot")
st.markdown(f"**Status:** Stable V2 | **Time:** {datetime.now().strftime('%H:%M:%S')}")

col1, col2 = st.columns([1, 4])
if col1.button("🚀 START MORNING SCAN", type="primary"):
    with st.spinner("Scanning Market..."):
        st.session_state.watchlist = run_scanner()

enable_auto = st.checkbox("✅ ENABLE AUTO-TRADING", value=True)
st.divider()

# ------------------------------------------------------------------
# 1. BULK FETCH ALL DATA FIRST
# ------------------------------------------------------------------
all_tickers_needed = []
if not st.session_state.portfolio.empty:
    all_tickers_needed.extend(st.session_state.portfolio['Ticker'].tolist())
if not st.session_state.watchlist.empty:
    all_tickers_needed.extend(st.session_state.watchlist['Ticker'].tolist())

# Remove duplicates
all_tickers_needed = list(set(all_tickers_needed))

# Single Request to Yahoo
live_price_map = get_live_prices_bulk(all_tickers_needed)

# ------------------------------------------------------------------
# 2. RENDER ACTIVE POSITIONS
# ------------------------------------------------------------------
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    portfolio_display = []
    
    for i, row in st.session_state.portfolio.iterrows():
        ticker = row['Ticker']
        
        # Safe Get with Float Conversion
        curr = live_price_map.get(ticker, 0.0)
        if curr == 0.0: curr = row['Buy Price'] # Fallback if fetch fails
        
        curr = float(curr) # Double Safety
        
        pnl = (curr - row['Buy Price']) * row['Qty']
        pnl_pct = ((curr - row['Buy Price']) / row['Buy Price']) * 100
        
        if enable_auto and curr > 0:
            if curr <= row['Stop Loss']:
                close_position(i, curr, "STOP LOSS")
                st.rerun() 
            elif curr >= row['Target']:
                close_position(i, curr, "TARGET HIT")
                st.rerun()
        
        portfolio_display.append({
            "Ticker": ticker, "Buy": row['Buy Price'], "Curr": f"{curr:.2f}",
            "Qty": row['Qty'], "P&L": f"{pnl:.2f}", "Return": f"{pnl_pct:.2f}%",
            "SL": f"{row['Stop Loss']:.2f}", "TGT": f"{row['Target']:.2f}"
        })
        
    st.dataframe(pd.DataFrame(portfolio_display))
else:
    st.info("No active positions.")

st.divider()

# ------------------------------------------------------------------
# 3. RENDER WATCHLIST
# ------------------------------------------------------------------
st.subheader("📡 Scanner Watchlist")

if not st.session_state.watchlist.empty:
    with st.container():
        c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1, 1, 1.5])
        c1.markdown("**Ticker**"); c2.markdown("**Price**"); c3.markdown("**Trigger**")
        c4.markdown("**Dist**"); c5.markdown("**Status**")
        st.markdown("---")
        
        for index, row in st.session_state.watchlist.iterrows():
            ticker = row['Ticker']
            trigger = float(row['Trigger'])
            
            current_price = live_price_map.get(ticker, 0.0)
            
            if current_price == 0.0: continue
            
            # THE FIX: Ensure boolean comparison is safe
            current_price = float(current_price) 
            
            dist = ((current_price - trigger) / trigger) * 100
            
            status_emoji = "⏳"
            color = "gray"
            
            if current_price > trigger:
                status_emoji = "🔥"
                color = "green"
                if enable_auto:
                    execute_paper_trade(ticker, current_price, row['Stop Loss'], row['Target'])
                    st.rerun()
            elif dist > -0.5:
                status_emoji = "👀"
                color = "orange"
                
            c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1, 1, 1.5])
            c1.write(ticker)
            c2.write(f"{current_price:.2f}")
            c3.write(f"{trigger:.2f}")
            c4.markdown(f":{color}[{dist:.2f}%]")
            c5.markdown(f":{color}[{status_emoji}]")

else:
    st.info("Scanner Empty.")

st.divider()
st.subheader("📜 Trade History")
st.dataframe(st.session_state.trade_log)

if enable_auto:
    time.sleep(30)
    st.rerun()
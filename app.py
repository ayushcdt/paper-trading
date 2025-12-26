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

RISK_PER_TRADE = 1000  # Max loss per trade (in INR)

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
        # Fallback list if NSE website is slow
        return ["RELIANCE.NS", "TCS.NS", "SBIN.NS", "MARUTI.NS", "BHEL.NS", "BIKAJI.NS", "ITC.NS", "INFY.NS", "TATAMOTORS.NS"]

def run_scanner():
    """Scans for setups based on Yesterday's High"""
    tickers = get_nifty500()
    results = []
    
    # Progress Bar for UX
    progress = st.progress(0)
    status_text = st.empty()
    
    # Scanning first 50 stocks for speed (Increase number for full market)
    scan_limit = 50 
    
    for i, ticker in enumerate(tickers[:scan_limit]):
        progress.progress((i+1)/scan_limit)
        status_text.text(f"Scanning {ticker}...")
        
        try:
            # Get 5 days of 15m data to find trend & levels
            df = yf.download(ticker, period="5d", interval="15m", progress=False)
            if df.empty: continue
            if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
            
            # Logic: Breakout of Previous Day's High
            # (Simplifying logic for robustness)
            prev_high = df['High'].iloc[:-1].max()
            atr = (df['High'] - df['Low']).mean()
            
            # Define Trigger (Breakout Point)
            trigger = prev_high * 1.001 # 0.1% Buffer
            
            results.append({
                "Ticker": ticker,
                "Trigger": trigger,
                "Stop Loss": trigger - (atr * 1.5),
                "Target": trigger + (atr * 3)
            })
        except: continue
    
    progress.empty()
    status_text.empty()
    return pd.DataFrame(results)

def execute_paper_trade(ticker, price, stop, target):
    """AUTO-BUY LOGIC"""
    # 1. Check if already in portfolio to avoid duplicate buys
    if not st.session_state.portfolio.empty:
        if ticker in st.session_state.portfolio['Ticker'].values:
            return 
            
    # 2. Position Sizing (Risk Management)
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
    
    # 4. Log the Trade
    log_entry = {
        "Ticker": ticker, "Action": "BUY", "Price": price, 
        "Time": datetime.now().strftime("%H:%M"), "PnL": 0, "Result": "OPEN"
    }
    st.session_state.trade_log = pd.concat([st.session_state.trade_log, pd.DataFrame([log_entry])], ignore_index=True)
    
    # 5. User Feedback
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
st.markdown(f"**Status:** System Live | **Time:** {datetime.now().strftime('%H:%M:%S')}")

# 1. CONTROL PANEL
col1, col2 = st.columns([1, 4])
if col1.button("🚀 START MORNING SCAN", type="primary"):
    with st.spinner("Analyzing Market Structure..."):
        st.session_state.watchlist = run_scanner()
        st.success(f"Scan Complete! Found {len(st.session_state.watchlist)} targets.")

enable_auto = st.checkbox("✅ ENABLE AUTO-TRADING", value=True, help="If checked, bot will auto-buy breakouts.")

st.divider()

# 2. LIVE PORTFOLIO (Active Trades)
st.subheader("💼 Active Positions")
if not st.session_state.portfolio.empty:
    portfolio_display = []
    
    for i, row in st.session_state.portfolio.iterrows():
        try:
            # Live Price Check
            live = yf.download(row['Ticker'], period="1d", interval="1m", progress=False)
            curr = live['Close'].iloc[-1] if not live.empty else row['Buy Price']
            
            pnl = (curr - row['Buy Price']) * row['Qty']
            pnl_pct = ((curr - row['Buy Price']) / row['Buy Price']) * 100
            
            # EXIT LOGIC
            if enable_auto:
                if curr <= row['Stop Loss']:
                    close_position(i, curr, "STOP LOSS")
                    st.rerun() 
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
    st.info("No active positions. Waiting for triggers...")

# 3. LIVE WATCHLIST (THE HEARTBEAT)
st.divider()
st.subheader("📡 Scanner Watchlist (Live Monitor)")

if not st.session_state.watchlist.empty:
    # We use a container to make the visual updates look stable
    with st.container():
        # Table Headers
        h1, h2, h3, h4, h5 = st.columns([1.5, 1, 1, 1, 1.5])
        h1.markdown("**Ticker**")
        h2.markdown("**Live Price**")
        h3.markdown("**Trigger**")
        h4.markdown("**Distance**")
        h5.markdown("**Status**")
        st.markdown("---")
        
        # Iterating through watchlist
        watch_data = st.session_state.watchlist.to_dict('records')
        
        for row in watch_data:
            ticker = row['Ticker']
            trigger = row['Trigger']
            stop = row['Stop Loss']
            target = row['Target']
            
            try:
                # Fetch Real-Time Data
                data = yf.download(ticker, period="1d", interval="1m", progress=False)
                if data.empty: 
                    current_price = 0.0
                else:
                    if isinstance(data.columns, pd.MultiIndex): data.columns = data.columns.get_level_values(0)
                    current_price = data['Close'].iloc[-1]
                
                # Calculate Distance to Breakout
                dist = ((current_price - trigger) / trigger) * 100
                
                # Determine Status & Color
                status_emoji = "⏳"
                status_text = "WAITING"
                text_color = "gray"
                
                if current_price > trigger:
                    status_emoji = "🔥"
                    status_text = "BREAKOUT"
                    text_color = "green"
                    
                    # AUTO-EXECUTE TRADE
                    if enable_auto:
                        execute_paper_trade(ticker, current_price, stop, target)
                        
                elif dist > -0.5: # If within 0.5% of trigger
                    status_emoji = "👀"
                    status_text = "NEAR"
                    text_color = "orange"
                
                # Render Row
                c1, c2, c3, c4, c5 = st.columns([1.5, 1, 1, 1, 1.5])
                c1.write(ticker)
                c2.write(f"₹{current_price:.2f}")
                c3.write(f"₹{trigger:.2f}")
                c4.markdown(f":{text_color}[{dist:.2f}%]")
                c5.markdown(f":{text_color}[**{status_emoji} {status_text}**]")
                
            except Exception as e:
                continue

else:
    st.info("Scanner is empty. Click 'START MORNING SCAN' to initialize.")

st.divider()

# 4. TRADE HISTORY
st.subheader("📜 Trade History")
st.dataframe(st.session_state.trade_log)

# 5. AUTO REFRESH (Keep the Heartbeat Alive)
if enable_auto:
    time.sleep(30) # Wait 30 seconds
    st.rerun()     # Refresh the page
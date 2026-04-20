"""
Stock Picker - Artha 2.0 Analysis Engine
Screens and ranks stocks based on quality, momentum, and technicals
Uses Angel One API for real-time price data
"""

import json
import pandas as pd
import yfinance as yf  # Only for fundamentals (ROE, margins, etc.)
from datetime import datetime, timedelta
from pathlib import Path
from logzero import logger
from technicals import (
    add_all_indicators,
    calculate_relative_strength,
    identify_trend,
    calculate_support_resistance,
    calculate_atr
)
from config import ANALYSIS_CONFIG
from data_fetcher import get_fetcher, SYMBOL_TOKENS
from strategy_v2 import (
    TOP_20_DROP,
    momentum_score_v2,
    assess_regime,
)
from adaptive.engine import decide as adaptive_decide
from adaptive.variants import build_variants as _build_variants


# Weekly fundamentals cache so a single yfinance failure doesn't zero out quality scores
FUNDAMENTALS_CACHE = Path(__file__).parent.parent / "data" / "fundamentals_cache.json"
FUNDAMENTALS_TTL_DAYS = 7
FUNDAMENTALS_FIELDS = (
    "returnOnEquity", "returnOnAssets", "debtToEquity", "profitMargins",
    "revenueGrowth", "earningsGrowth", "longName", "sector",
)


def _load_fundamentals_cache() -> dict:
    if FUNDAMENTALS_CACHE.exists():
        try:
            return json.loads(FUNDAMENTALS_CACHE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_fundamentals_cache(cache: dict) -> None:
    FUNDAMENTALS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    FUNDAMENTALS_CACHE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def get_fundamentals(symbol: str) -> tuple[dict, str]:
    """
    Returns (info_dict, status) where status is one of:
      'ok'          — fresh fetch succeeded
      'cached'      — using cached values within TTL
      'stale'       — using cached values past TTL (yfinance currently failing)
      'unavailable' — no cache and fetch failed; quality cannot be scored
    """
    cache = _load_fundamentals_cache()
    entry = cache.get(symbol)
    now = datetime.now()

    if entry:
        age = now - datetime.fromisoformat(entry["fetched_at"])
        if age < timedelta(days=FUNDAMENTALS_TTL_DAYS):
            return entry["info"], "cached"

    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        raw = ticker.info or {}
        if not raw or not any(raw.get(f) for f in FUNDAMENTALS_FIELDS):
            raise ValueError("yfinance returned empty info")
        info = {f: raw.get(f) for f in FUNDAMENTALS_FIELDS}
        cache[symbol] = {"info": info, "fetched_at": now.isoformat()}
        _save_fundamentals_cache(cache)
        return info, "ok"
    except Exception as e:
        logger.warning(f"Fundamentals fetch failed for {symbol}: {e}")
        if entry:
            return entry["info"], "stale"
        return {}, "unavailable"


# Nifty 50 symbols - must match SYMBOL_TOKENS in data_fetcher.py
NIFTY_50 = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "HINDUNILVR", "BHARTIARTL", "SBIN", "KOTAKBANK",
    "ITC", "LT", "AXISBANK", "BAJFINANCE", "ASIANPAINT",
    "MARUTI", "TITAN", "SUNPHARMA", "ULTRACEMCO", "WIPRO",
    "NESTLEIND", "HCLTECH", "TECHM", "POWERGRID", "NTPC",
    "M&M", "ONGC", "JSWSTEEL", "TATASTEEL",
    "BAJAJFINSV", "ADANIENT", "ADANIPORTS", "COALINDIA", "GRASIM",
    "BRITANNIA", "CIPLA", "DRREDDY", "EICHERMOT", "DIVISLAB",
    "BPCL", "SBILIFE", "HDFCLIFE", "APOLLOHOSP", "TATACONSUM",
    "HEROMOTOCO", "UPL", "INDUSINDBK", "HINDALCO", "BAJAJ-AUTO"
]

# Extended universe -- resolved via SYMBOL_TOKENS at runtime.
# Run `python scripts/fetch_tokens.py` to populate Angel tokens for these.
NIFTY_NEXT_50 = [
    "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL", "BAJAJHLDNG",
    "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "CANBK", "CGPOWER",
    "CHOLAFIN", "COLPAL", "DABUR", "DLF", "DMART",
    "GAIL", "GODREJCP", "HAL", "HAVELLS", "ICICIGI",
    "ICICIPRULI", "INDHOTEL", "INDIGO", "IOC", "IRCTC",
    "IRFC", "JINDALSTEL", "JIOFIN", "LICI", "LODHA",
    "LTIM", "MARICO", "MOTHERSON", "NAUKRI", "NHPC",
    "PFC", "PIDILITIND", "PNB", "POWERGRID", "RECLTD",
    "SHREECEM", "SIEMENS", "SRF", "TATAMOTORS", "TATAPOWER",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "UNITDSPR", "VBL",
    "VEDL", "ZOMATO", "ZYDUSLIFE",
]

NIFTY_MIDCAP = [
    "AUROPHARMA", "BHEL", "BIOCON", "CONCOR", "CUMMINSIND",
    "ESCORTS", "EXIDEIND", "FEDERALBNK", "GLENMARK", "GMRINFRA",
    "GUJGASLTD", "HINDPETRO", "IDFCFIRSTB", "INDIANB", "INDUSTOWER",
    "LICHSGFIN", "LUPIN", "MFSL", "MPHASIS", "MRF",
    "NMDC", "OBEROIRLTY", "OFSS", "PAGEIND", "PERSISTENT",
    "PETRONET", "PIIND", "POLYCAB", "PRESTIGE", "RVNL",
    "SAIL", "SBICARD", "SUNTV", "SUPREMEIND", "SYNGENE",
    "TATACOMM", "TATAELXSI", "TIINDIA", "TORNTPOWER", "UBL",
    "UNIONBANK", "VOLTAS", "YESBANK", "ZEEL",
]


def fetch_stock_data(symbol: str, period: str = "1y") -> pd.DataFrame:
    """Fetch historical data from Angel One API"""
    try:
        # Check if symbol exists in Angel token list
        if symbol not in SYMBOL_TOKENS:
            logger.warning(f"Symbol {symbol} not in Angel token list, skipping")
            return pd.DataFrame()

        fetcher = get_fetcher()
        if not fetcher.logged_in:
            fetcher.login()

        # Calculate days from period
        days = 365 if "1y" in period else 180

        df = fetcher.get_historical_data(symbol, interval="ONE_DAY", days=days)

        if df.empty:
            logger.warning(f"No data for {symbol} from Angel API")
            return pd.DataFrame()

        return df

    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()


def fetch_nifty_data(period: str = "1y") -> pd.DataFrame:
    """Fetch Nifty 50 index data from Angel One API"""
    try:
        fetcher = get_fetcher()
        if not fetcher.logged_in:
            fetcher.login()

        days = 365 if "1y" in period else 180
        df = fetcher.get_historical_data("NIFTY", interval="ONE_DAY", days=days)

        if df.empty:
            logger.warning("No Nifty data from Angel API")
            return pd.DataFrame()

        return df
    except Exception as e:
        logger.error(f"Error fetching Nifty: {e}")
        return pd.DataFrame()


def calculate_quality_score(info: dict) -> int:
    """
    Calculate quality score based on fundamentals
    Score: 0-100
    """
    score = 0

    # ROE > 15%
    roe = info.get('returnOnEquity', 0) or 0
    if roe > 0.20:
        score += 20
    elif roe > 0.15:
        score += 15
    elif roe > 0.10:
        score += 10

    # ROCE (approximation using ROA * leverage)
    roa = info.get('returnOnAssets', 0) or 0
    if roa > 0.12:
        score += 15
    elif roa > 0.08:
        score += 10

    # Debt/Equity < 0.5
    de_ratio = info.get('debtToEquity', 100) or 100
    if de_ratio < 30:
        score += 20
    elif de_ratio < 50:
        score += 15
    elif de_ratio < 100:
        score += 10

    # Profit Margins
    profit_margin = info.get('profitMargins', 0) or 0
    if profit_margin > 0.20:
        score += 15
    elif profit_margin > 0.10:
        score += 10

    # Revenue Growth
    revenue_growth = info.get('revenueGrowth', 0) or 0
    if revenue_growth > 0.15:
        score += 15
    elif revenue_growth > 0.10:
        score += 10

    # Earnings Growth
    earnings_growth = info.get('earningsGrowth', 0) or 0
    if earnings_growth > 0.15:
        score += 15
    elif earnings_growth > 0.10:
        score += 10

    return min(score, 100)


def calculate_momentum_score(df: pd.DataFrame, nifty_df: pd.DataFrame) -> dict:
    """
    Calculate momentum metrics
    """
    if len(df) < 200 or len(nifty_df) < 200:
        return {'score': 0, 'rs_6m': 0, 'rs_3m': 0}

    # Calculate returns
    df['returns'] = df['Close'].pct_change()
    nifty_df['returns'] = nifty_df['Close'].pct_change()

    # 6-month relative strength
    rs_6m = calculate_relative_strength(
        df['returns'].tail(126),
        nifty_df['returns'].tail(126)
    )

    # 3-month relative strength
    rs_3m = calculate_relative_strength(
        df['returns'].tail(63),
        nifty_df['returns'].tail(63)
    )

    # 1-month relative strength
    rs_1m = calculate_relative_strength(
        df['returns'].tail(21),
        nifty_df['returns'].tail(21)
    )

    # Momentum score
    score = 0

    if rs_6m > 1.2:
        score += 35
    elif rs_6m > 1.1:
        score += 25
    elif rs_6m > 1.0:
        score += 15

    if rs_3m > 1.15:
        score += 35
    elif rs_3m > 1.05:
        score += 25
    elif rs_3m > 1.0:
        score += 15

    if rs_1m > 1.1:
        score += 30
    elif rs_1m > 1.0:
        score += 20

    return {
        'score': min(score, 100),
        'rs_6m': round(rs_6m, 2),
        'rs_3m': round(rs_3m, 2),
        'rs_1m': round(rs_1m, 2)
    }


def calculate_technical_score(df: pd.DataFrame) -> dict:
    """
    Calculate technical setup score
    """
    if len(df) < 200:
        return {'score': 0, 'setup': 'INSUFFICIENT_DATA'}

    df = add_all_indicators(df)
    latest = df.iloc[-1]

    score = 0
    setup_notes = []

    # Trend alignment
    trend = identify_trend(df)
    if trend == 'STRONG_BULLISH':
        score += 30
        setup_notes.append("Strong uptrend")
    elif trend == 'BULLISH':
        score += 20
        setup_notes.append("Uptrend")
    elif trend == 'SIDEWAYS':
        score += 10
        setup_notes.append("Sideways")

    # Price vs 20 EMA (pullback opportunity)
    price = latest['Close']
    ema_20 = latest['EMA_20']
    distance_from_ema = (price - ema_20) / ema_20 * 100

    if 0 <= distance_from_ema <= 3:
        score += 25
        setup_notes.append("Near 20 EMA support")
    elif -3 <= distance_from_ema < 0:
        score += 20
        setup_notes.append("Pullback to 20 EMA")
    elif 3 < distance_from_ema <= 8:
        score += 15
        setup_notes.append("Slight extension")
    elif distance_from_ema > 15:
        score -= 10
        setup_notes.append("Extended - wait for pullback")

    # RSI
    rsi = latest['RSI']
    if 45 <= rsi <= 65:
        score += 20
        setup_notes.append(f"RSI healthy ({rsi:.0f})")
    elif 30 <= rsi < 45:
        score += 15
        setup_notes.append(f"RSI oversold ({rsi:.0f})")
    elif rsi > 70:
        score -= 5
        setup_notes.append(f"RSI overbought ({rsi:.0f})")

    # Volume
    vol_ratio = latest['Volume_Ratio']
    if vol_ratio > 1.5:
        score += 15
        setup_notes.append(f"High volume ({vol_ratio:.1f}x)")
    elif vol_ratio > 1.0:
        score += 10

    # Above 200 DMA
    if price > latest['EMA_200']:
        score += 10
        setup_notes.append("Above 200 DMA")
    else:
        score -= 20
        setup_notes.append("Below 200 DMA - avoid")

    return {
        'score': max(0, min(score, 100)),
        'trend': trend,
        'rsi': round(rsi, 1),
        'volume_ratio': round(vol_ratio, 2),
        'distance_from_ema20': round(distance_from_ema, 2),
        'setup': " | ".join(setup_notes)
    }


def calculate_entry_exit(df: pd.DataFrame, atr_multiplier: float = 1.5) -> dict:
    """
    Calculate entry, stop loss, and target levels
    """
    if len(df) < 20:
        return {}

    df = add_all_indicators(df)
    latest = df.iloc[-1]

    cmp = latest['Close']
    atr = latest['ATR']
    ema_20 = latest['EMA_20']

    # Entry: Current price or pullback to EMA20
    entry = cmp

    # Stop Loss: Below recent swing low or ATR-based
    recent_low = df['Low'].tail(10).min()
    atr_stop = entry - (atr * atr_multiplier)
    stop_loss = max(recent_low * 0.99, atr_stop)  # Use higher of the two

    # Risk
    risk = entry - stop_loss
    risk_pct = (risk / entry) * 100

    # Target: 2:1 risk-reward minimum
    target_1 = entry + (risk * 2)
    target_2 = entry + (risk * 3)

    # Calculate support/resistance
    levels = calculate_support_resistance(df)

    return {
        'cmp': round(cmp, 2),
        'entry': round(entry, 2),
        'stop_loss': round(stop_loss, 2),
        'target_1': round(target_1, 2),
        'target_2': round(target_2, 2),
        'risk_pct': round(risk_pct, 2),
        'risk_reward': "1:2",
        'atr': round(atr, 2),
        'support': [round(s, 2) for s in levels['support']],
        'resistance': [round(r, 2) for r in levels['resistance']]
    }


def generate_reasoning(symbol: str, quality: int, momentum: dict, technical: dict, levels: dict) -> str:
    """
    Generate AI-like reasoning for the stock pick
    """
    reasons = []

    # Quality
    if quality >= 80:
        reasons.append(f"Excellent quality score ({quality}/100) with strong fundamentals")
    elif quality >= 60:
        reasons.append(f"Good quality score ({quality}/100)")

    # Momentum
    if momentum['rs_6m'] > 1.1:
        reasons.append(f"Outperforming Nifty by {(momentum['rs_6m']-1)*100:.0f}% over 6 months")
    if momentum['rs_3m'] > 1.05:
        reasons.append(f"Strong 3-month momentum (RS: {momentum['rs_3m']})")

    # Technical
    if 'Near 20 EMA' in technical['setup'] or 'Pullback' in technical['setup']:
        reasons.append("Pulled back to 20 EMA offering low-risk entry")
    if 'Strong uptrend' in technical['setup']:
        reasons.append("Strong uptrend with all EMAs aligned")
    if technical['rsi'] and 45 <= technical['rsi'] <= 60:
        reasons.append(f"RSI at {technical['rsi']:.0f} - not overbought")
    if technical['volume_ratio'] and technical['volume_ratio'] > 1.5:
        reasons.append(f"Volume confirmation at {technical['volume_ratio']:.1f}x average")

    # Risk-Reward
    risk_pct = levels.get('risk_pct', 0)
    if risk_pct and risk_pct < 8:
        reasons.append(f"Favorable risk-reward with {risk_pct:.1f}% downside to stop loss")

    return ". ".join(reasons) + "."


def analyze_stock(symbol: str, nifty_df: pd.DataFrame) -> dict:
    """
    Complete analysis for a single stock
    Uses Angel API for price data, yfinance for fundamentals only
    """
    logger.info(f"Analyzing {symbol}...")

    # Check if symbol exists in Angel token list
    if symbol not in SYMBOL_TOKENS:
        logger.warning(f"Symbol {symbol} not in Angel token list")
        return None

    # Fetch historical data from Angel API
    df = fetch_stock_data(symbol)
    if df.empty or len(df) < 200:
        return None

    # Get real-time LTP from Angel API
    fetcher = get_fetcher()
    ltp_data = fetcher.get_ltp(symbol)
    current_ltp = ltp_data.get("ltp", 0)

    # Get fundamentals from yfinance, with weekly cache + explicit failure status
    info, fundamentals_status = get_fundamentals(symbol)

    # Calculate scores
    if fundamentals_status == "unavailable":
        quality_score = None
    else:
        quality_score = calculate_quality_score(info)
    momentum = calculate_momentum_score(df, nifty_df)
    technical = calculate_technical_score(df)
    levels = calculate_entry_exit(df)

    # Override CMP with real-time LTP from Angel API
    if current_ltp > 0:
        levels['cmp'] = round(current_ltp, 2)
        # Recalculate entry/stop/target with live price
        if levels.get('atr', 0) > 0:
            atr = levels['atr']
            risk = current_ltp - levels['stop_loss']
            if risk > 0:
                levels['entry'] = round(current_ltp, 2)
                levels['target_1'] = round(current_ltp + (risk * 2), 2)
                levels['target_2'] = round(current_ltp + (risk * 3), 2)
                levels['risk_pct'] = round((risk / current_ltp) * 100, 2)

    # Overall score (weighted). If fundamentals unavailable, redistribute quality's
    # weight to momentum + technical equally — and flag the pick downstream.
    if quality_score is None:
        overall_score = momentum['score'] * 0.5 + technical['score'] * 0.5
    else:
        overall_score = (
            quality_score * 0.30 +
            momentum['score'] * 0.35 +
            technical['score'] * 0.35
        )

    # Conviction
    if overall_score >= 75:
        conviction = "HIGH"
    elif overall_score >= 60:
        conviction = "MEDIUM"
    else:
        conviction = "LOW"

    # Generate reasoning
    reasoning = generate_reasoning(symbol, quality_score, momentum, technical, levels)

    return {
        'symbol': symbol,
        'name': info.get('longName', symbol),
        'sector': info.get('sector', 'Unknown'),
        'cmp': levels.get('cmp', 0),
        'target': levels.get('target_1', 0),
        'target_2': levels.get('target_2', 0),
        'stop_loss': levels.get('stop_loss', 0),
        'risk_pct': levels.get('risk_pct', 0),
        'upside_pct': round((levels.get('target_1', 0) / levels.get('cmp', 1) - 1) * 100, 1) if levels.get('cmp') else 0,
        'conviction': conviction,
        'fundamentals_status': fundamentals_status,
        'scores': {
            'quality': quality_score,
            'momentum': momentum['score'],
            'technical': technical['score'],
            'overall': round(overall_score, 1)
        },
        'momentum': {
            'rs_6m': momentum['rs_6m'],
            'rs_3m': momentum['rs_3m']
        },
        'technicals': {
            'trend': technical['trend'],
            'rsi': technical['rsi'],
            'volume_ratio': technical['volume_ratio'],
            'setup': technical['setup']
        },
        'levels': {
            'support': levels.get('support', []),
            'resistance': levels.get('resistance', []),
            'atr': levels.get('atr', 0)
        },
        'reasoning': reasoning
    }


def run_stock_picker(universe: list = None, max_picks: int = 10) -> list:
    """
    Run the stock picker on given universe
    Returns top picks sorted by score
    Uses Angel One API for all price data
    """
    if universe is None:
        universe = NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP

    # Filter to only stocks available in Angel API
    available_symbols = [s for s in universe if s in SYMBOL_TOKENS]
    logger.info(f"Running stock picker on {len(available_symbols)} stocks (of {len(universe)} in universe)...")

    # Fetch Nifty data for benchmark
    nifty_df = fetch_nifty_data()
    if nifty_df.empty:
        logger.error("Failed to fetch Nifty data")
        return []

    # Analyze each stock
    results = []
    for symbol in available_symbols:
        try:
            analysis = analyze_stock(symbol, nifty_df)
            if analysis:
                # Apply filters
                if analysis['scores']['overall'] < 50:
                    continue
                if analysis['technicals']['trend'] in ['BEARISH', 'STRONG_BEARISH']:
                    continue
                if analysis['cmp'] == 0:
                    continue

                results.append(analysis)
        except Exception as e:
            logger.error(f"Error analyzing {symbol}: {e}")
            continue

    # Sort by overall score
    results.sort(key=lambda x: x['scores']['overall'], reverse=True)

    # Return top picks
    top_picks = results[:max_picks]

    # Add rank
    for i, pick in enumerate(top_picks):
        pick['rank'] = i + 1

    logger.info(f"Found {len(top_picks)} stock picks")
    return top_picks


def _compute_breadth_live(universe: list, fetcher) -> float:
    """% of universe stocks above their own 200 DMA right now."""
    above = 0
    total = 0
    for sym in universe:
        if sym not in SYMBOL_TOKENS:
            continue
        df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=260)
        if df.empty or len(df) < 200:
            continue
        ma_200 = df.tail(200)["Close"].mean()
        if df.iloc[-1]["Close"] > ma_200:
            above += 1
        total += 1
    return (above / total * 100) if total else 0.0


def run_stock_picker_v2(max_picks: int = 15) -> dict:
    """
    V2 picker: vol-adjusted 12-1 momentum on Nifty 200 minus top 20.
    Returns {regime: {...}, picks: [...]} -- regime may force empty picks (cash).
    """
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()

    universe = [s for s in (NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP)
                if s in SYMBOL_TOKENS and s not in TOP_20_DROP]
    logger.info(f"V2 picker: universe={len(universe)} (top-20 mega-caps excluded)")

    # ---- Regime gates ----
    nifty_df = fetcher.get_historical_data("NIFTY", interval="ONE_DAY", days=400)
    if nifty_df.empty:
        logger.error("V2 picker: cannot fetch Nifty for regime gate")
        return {"regime": {"deploy_pct": 0, "reason": "Nifty data unavailable"}, "picks": []}

    vix_df = fetcher.get_historical_data("INDIAVIX", interval="ONE_DAY", days=400)
    vix_value = float(fetcher.get_ltp("INDIAVIX").get("ltp", 15)) if not vix_df.empty else 15
    vix_history = vix_df["Close"] if not vix_df.empty else pd.Series([vix_value] * 60)

    breadth_pct = _compute_breadth_live(universe[:60], fetcher)  # sample first 60 for speed

    regime = assess_regime(
        nifty_close=nifty_df["Close"],
        breadth_above_200dma_pct=breadth_pct,
        vix_value=vix_value,
        vix_history=vix_history,
    )

    regime_block = {
        "deploy_pct": round(regime.deployment_pct * 100, 1),
        "reason": regime.reason,
        "gates": {
            "nifty_above_200dma": regime.nifty_above_200dma,
            "breadth_above_50pct": regime.breadth_above_50pct,
            "breadth_actual_pct": round(breadth_pct, 1),
            "vix_below_75th_pct": regime.vix_below_75th_pct,
            "vix_value": round(vix_value, 2),
        },
    }

    if regime.deployment_pct == 0:
        logger.info(f"V2 picker: regime gate CLOSED ({regime.reason}). Recommending cash.")
        return {"regime": regime_block, "picks": []}

    # ---- Score every symbol ----
    scored: list[tuple[str, float, pd.DataFrame]] = []
    for sym in universe:
        df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=400)
        if df.empty or len(df) < 252:
            continue
        # Trend gate: above own 200 DMA
        ma_200 = df.tail(200)["Close"].mean()
        if df.iloc[-1]["Close"] <= ma_200:
            continue
        score = momentum_score_v2(df["Close"])
        if score is None:
            continue
        scored.append((sym, score, df))

    scored.sort(key=lambda x: x[1], reverse=True)
    top = scored[:max_picks]

    # ---- Build pick objects with entry levels for dashboard display ----
    picks_out = []
    for rank, (sym, score, df) in enumerate(top, 1):
        df = add_all_indicators(df)
        latest = df.iloc[-1]
        cmp = float(latest["Close"])
        atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else cmp * 0.02
        ema_50 = float(latest["EMA_50"])
        low_20d = float(df["Low"].tail(20).min())

        # V2 stop logic: max(20-day low, 50 EMA), with -15% hard floor
        trail_stop = max(low_20d, ema_50)
        hard_floor = cmp * 0.85
        stop_loss = max(trail_stop, hard_floor)

        # No fixed targets in v2 (trail until exit signal); show 1R/2R for reference
        risk = cmp - stop_loss
        target_1 = cmp + risk * 2 if risk > 0 else cmp * 1.10
        target_2 = cmp + risk * 4 if risk > 0 else cmp * 1.20

        # Try to enrich with sector + name from yfinance cache (no point-in-time issue)
        info, fund_status = get_fundamentals(sym)

        picks_out.append({
            "rank": rank,
            "symbol": sym,
            "name": info.get("longName", sym),
            "sector": info.get("sector", "Unknown"),
            "cmp": round(cmp, 2),
            "target": round(target_1, 2),
            "target_2": round(target_2, 2),
            "stop_loss": round(stop_loss, 2),
            "risk_pct": round((risk / cmp) * 100, 2) if risk > 0 else 0,
            "upside_pct": round(((target_1 / cmp) - 1) * 100, 1),
            "conviction": "HIGH" if rank <= 5 else "MEDIUM" if rank <= 10 else "LOW",
            "fundamentals_status": fund_status,
            "scores": {
                "momentum_v2": round(score, 3),
                "quality": None,  # v2 doesn't use quality (yfinance not point-in-time)
                "momentum": None,
                "technical": None,
                "overall": round(min(100, max(0, 50 + score * 10)), 1),
            },
            "momentum": {
                "rs_6m": None,
                "rs_3m": None,
            },
            "technicals": {
                "trend": "BULLISH",  # Filtered to above 200 DMA
                "rsi": round(float(latest["RSI"]), 1) if pd.notna(latest.get("RSI")) else None,
                "volume_ratio": round(float(latest["Volume_Ratio"]), 2) if pd.notna(latest.get("Volume_Ratio")) else None,
                "setup": f"V2 momentum rank #{rank}; trail stop at {stop_loss:.0f}",
            },
            "levels": {
                "support": [round(low_20d, 2), round(ema_50, 2)],
                "resistance": [],
                "atr": round(atr, 2),
            },
            "reasoning": (
                f"Vol-adjusted 12-1 momentum score {score:.2f} ranks #{rank} in extended-Nifty universe "
                f"(top-20 mega-caps excluded). Above 200 DMA. Regime: {regime.reason}. "
                f"Position size: {regime.deployment_pct*100:.0f}% of normal. "
                f"Exit on close below ₹{stop_loss:.0f} (trailing), drop from top 30, or -15% hard stop."
            ),
        })

    logger.info(f"V2 picker: {len(picks_out)} picks at {regime.deployment_pct*100:.0f}% deployment")
    return {"regime": regime_block, "picks": picks_out}


def run_stock_picker_v3(max_picks: int = 15) -> dict:
    """
    V3 adaptive picker: regime classifier picks a variant; variant picks stocks.
    Returns {regime, variant, guardrails, picks} -- may be empty (defensive).
    """
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()

    universe = [s for s in (NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP)
                if s in SYMBOL_TOKENS and s not in TOP_20_DROP]
    logger.info(f"V3 adaptive picker: universe={len(universe)}")

    # Build histories dict keyed by symbol
    histories: dict[str, pd.DataFrame] = {}
    for sym in universe:
        df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=400)
        if df.empty or len(df) < 252:
            continue
        df = df.sort_values("Date").reset_index(drop=True)
        histories[sym] = df
    logger.info(f"V3 picker: loaded {len(histories)} histories")

    nifty_df = fetcher.get_historical_data("NIFTY", interval="ONE_DAY", days=400)
    if nifty_df.empty:
        return {"regime": "UNKNOWN", "variant": "none", "picks": [], "reason": "Nifty unavailable"}
    nifty_df = nifty_df.sort_values("Date").reset_index(drop=True)

    vix_df = fetcher.get_historical_data("INDIAVIX", interval="ONE_DAY", days=400)
    vix_value = float(fetcher.get_ltp("INDIAVIX").get("ltp", 15)) if not vix_df.empty else 15
    vix_series = vix_df["Close"] if not vix_df.empty else pd.Series([vix_value] * 60)

    # Pull news snapshot to feed overlay (fail-safe if unavailable)
    try:
        from news.feed import fetch_news_snapshot
        news_snap = fetch_news_snapshot(list(histories.keys()))
    except Exception as e:
        logger.warning(f"News snapshot failed (non-fatal): {e}")
        news_snap = None

    target_date = pd.Timestamp(nifty_df.iloc[-1]["Date"])
    decision = adaptive_decide(
        histories=histories,
        nifty_close=nifty_df["Close"],
        vix_value=vix_value,
        vix_history=vix_series,
        target_date=target_date,
        universe=list(histories.keys()),
        current_equity=None,
        persist_state=True,
        news_snap=news_snap,
    )

    # Enrich picks with full trading levels for dashboard display
    enriched_picks = []
    for p in decision.picks[:max_picks]:
        sym = p["symbol"]
        df = add_all_indicators(histories[sym])
        latest = df.iloc[-1]
        cmp = float(latest["Close"])
        atr = float(latest["ATR"]) if pd.notna(latest["ATR"]) else cmp * 0.02
        info, fund_status = get_fundamentals(sym)
        risk = cmp - p["stop"]
        target_1 = cmp + risk * 2 if risk > 0 else cmp * 1.10
        target_2 = cmp + risk * 4 if risk > 0 else cmp * 1.20

        enriched_picks.append({
            "rank": p["rank"],
            "symbol": sym,
            "name": info.get("longName", sym),
            "sector": info.get("sector", "Unknown"),
            "cmp": round(cmp, 2),
            "target": round(target_1, 2),
            "target_2": round(target_2, 2),
            "stop_loss": round(p["stop"], 2),
            "risk_pct": round((risk / cmp) * 100, 2) if risk > 0 else 0,
            "upside_pct": round(((target_1 / cmp) - 1) * 100, 1),
            "conviction": "HIGH" if p["rank"] <= 5 else "MEDIUM" if p["rank"] <= 10 else "LOW",
            "variant": p["variant"],
            "regime": decision.regime,
            "fundamentals_status": fund_status,
            "scores": {
                "momentum_v2": p["score"],
                "quality": None,
                "momentum": None,
                "technical": None,
                "overall": round(min(100, max(0, 50 + p["score"] * 10)), 1),
            },
            "momentum": {"rs_6m": None, "rs_3m": None},
            "technicals": {
                "trend": "BULLISH",
                "rsi": round(float(latest["RSI"]), 1) if pd.notna(latest.get("RSI")) else None,
                "volume_ratio": round(float(latest["Volume_Ratio"]), 2) if pd.notna(latest.get("Volume_Ratio")) else None,
                "setup": f"{p['variant']} rank #{p['rank']}; regime {decision.regime}",
            },
            "levels": {"support": [round(p["stop"], 2)], "resistance": [], "atr": round(atr, 2)},
            "reasoning": (
                f"Regime: {decision.regime} ({decision.regime_reason}). "
                f"Active variant: {p['variant']}. "
                f"Score {p['score']:.2f} -> rank #{p['rank']}. "
                f"Stop at ₹{p['stop']:.0f}. "
                f"Deploy: {decision.deploy_pct*100:.0f}%."
            ),
        })

    return {
        "as_of": decision.as_of,
        "regime": decision.regime,
        "regime_reason": decision.regime_reason,
        "regime_inputs": decision.regime_inputs,
        "variant": decision.variant,
        "variant_reason": decision.variant_reason,
        "deploy_pct": round(decision.deploy_pct * 100, 1),
        "kill_switch_active": decision.kill_switch_active,
        "kill_switch_reason": decision.kill_switch_reason,
        "picks": enriched_picks,
        "news_adjustments": decision.news_adjustments or [],
    }


if __name__ == "__main__":
    # Test run
    picks = run_stock_picker(max_picks=5)
    for pick in picks:
        print(f"\n#{pick['rank']} {pick['symbol']}")
        print(f"   CMP: {pick['cmp']} | Target: {pick['target']} | SL: {pick['stop_loss']}")
        print(f"   Score: {pick['scores']['overall']} | Conviction: {pick['conviction']}")
        print(f"   {pick['reasoning'][:100]}...")

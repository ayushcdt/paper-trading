"""
Market Analyzer - Artha 2.0
Analyzes overall market conditions, sectors, and generates outlook
Uses Angel One API for real-time data
"""

import pandas as pd
from datetime import datetime, timedelta
from logzero import logger
from technicals import (
    add_all_indicators,
    identify_trend,
    calculate_support_resistance,
    calculate_ema,
    calculate_rsi
)
from data_fetcher import get_fetcher
from macro_analyzer import run_macro_analysis, macro_stance_contribution


# Sector indices - Using Angel API symbol names
SECTOR_INDICES = {
    "NIFTY_BANK": "NIFTY_BANK",
    "NIFTY_IT": "NIFTY_IT",
    "NIFTY_PHARMA": "NIFTY_PHARMA",
    "NIFTY_AUTO": "NIFTY_AUTO",
    "NIFTY_FMCG": "NIFTY_FMCG",
    "NIFTY_METAL": "NIFTY_METAL",
    "NIFTY_REALTY": "NIFTY_REALTY",
    "NIFTY_ENERGY": "NIFTY_ENERGY",
    "NIFTY_INFRA": "NIFTY_INFRA"
}

# Display names
SECTOR_NAMES = {
    "NIFTY_BANK": "Bank",
    "NIFTY_IT": "IT",
    "NIFTY_PHARMA": "Pharma",
    "NIFTY_AUTO": "Auto",
    "NIFTY_FMCG": "FMCG",
    "NIFTY_METAL": "Metal",
    "NIFTY_REALTY": "Realty",
    "NIFTY_ENERGY": "Energy",
    "NIFTY_INFRA": "Infra",
    "NIFTY_PSE": "PSU"
}


def fetch_index_data(symbol: str, period: str = "6mo") -> pd.DataFrame:
    """Fetch index data from Angel One API"""
    try:
        fetcher = get_fetcher()
        if not fetcher.logged_in:
            fetcher.login()

        # Map old yfinance symbols to Angel format
        symbol_map = {
            "^NSEI": "NIFTY",
            "^NSEBANK": "BANKNIFTY",
            "^INDIAVIX": "INDIAVIX",
        }
        angel_symbol = symbol_map.get(symbol, symbol)

        # Get historical data
        days = 180 if "6mo" in period else 365
        df = fetcher.get_historical_data(angel_symbol, interval="ONE_DAY", days=days)

        if df.empty:
            logger.warning(f"No data from Angel API for {symbol}")
        return df
    except Exception as e:
        logger.error(f"Error fetching {symbol}: {e}")
        return pd.DataFrame()


def analyze_nifty() -> dict:
    """
    Analyze Nifty 50 index with real-time data from Angel API
    """
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()

    # Get real-time LTP
    ltp_data = fetcher.get_ltp("NIFTY")
    nifty_value = ltp_data.get("ltp", 0)
    prev_close = ltp_data.get("close", nifty_value)

    if nifty_value == 0:
        logger.error("Failed to get Nifty LTP")
        return {}

    # Get historical data for indicators
    df = fetch_index_data("^NSEI", period="1y")
    if df.empty:
        return {}

    df = add_all_indicators(df)
    latest = df.iloc[-1]

    # Use real-time values
    change = nifty_value - prev_close
    change_pct = (change / prev_close) * 100 if prev_close else 0

    # Trend
    trend = identify_trend(df)

    # EMA status (convert to native Python bool for JSON)
    above_20ema = bool(nifty_value > latest['EMA_20'])
    above_50ema = bool(nifty_value > latest['EMA_50'])
    above_200ema = bool(nifty_value > latest['EMA_200'])

    # RSI
    rsi = latest['RSI']

    # Support/Resistance
    levels = calculate_support_resistance(df, lookback=60)

    # Distance from 200 DMA (market health)
    distance_200dma = ((nifty_value - latest['EMA_200']) / latest['EMA_200']) * 100

    return {
        'value': round(nifty_value, 2),
        'change': round(change, 2),
        'change_pct': round(change_pct, 2),
        'trend': trend,
        'above_20ema': above_20ema,
        'above_50ema': above_50ema,
        'above_200ema': above_200ema,
        'rsi': round(rsi, 1),
        'ema_20': round(latest['EMA_20'], 2),
        'ema_50': round(latest['EMA_50'], 2),
        'ema_200': round(latest['EMA_200'], 2),
        'support': [round(s, 0) for s in levels['support']],
        'resistance': [round(r, 0) for r in levels['resistance']],
        'distance_200dma': round(distance_200dma, 2)
    }


def analyze_banknifty() -> dict:
    """
    Analyze Bank Nifty index with real-time data
    """
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()

    ltp_data = fetcher.get_ltp("BANKNIFTY")
    value = ltp_data.get("ltp", 0)
    prev_close = ltp_data.get("close", value)

    if value == 0:
        return {}

    change = value - prev_close
    change_pct = (change / prev_close) * 100 if prev_close else 0

    df = fetch_index_data("^NSEBANK", period="1y")
    if df.empty or len(df) < 200:
        # Need 200 bars for trend identification
        return {
            'value': round(value, 2),
            'change': round(change, 2),
            'change_pct': round(change_pct, 2),
            'trend': 'INSUFFICIENT_DATA',
            'rsi': None,
            'data_status': 'partial',
        }

    df = add_all_indicators(df)
    latest = df.iloc[-1]
    trend = identify_trend(df)
    rsi = latest['RSI']

    return {
        'value': round(value, 2),
        'change': round(change, 2),
        'change_pct': round(change_pct, 2),
        'trend': trend,
        'rsi': round(rsi, 1) if rsi == rsi else None,  # NaN guard
        'above_20ema': bool(value > latest['EMA_20']),
        'above_50ema': bool(value > latest['EMA_50']),
        'above_200ema': bool(value > latest['EMA_200']),
    }


def analyze_vix() -> dict:
    """
    Analyze India VIX using Angel One API
    """
    try:
        fetcher = get_fetcher()
        if not fetcher.logged_in:
            fetcher.login()

        # Get real-time VIX from Angel API
        ltp_data = fetcher.get_ltp("INDIAVIX")
        vix = ltp_data.get("ltp", 0)

        if vix == 0:
            # Fallback to historical data
            df = fetch_index_data("INDIAVIX", period="3mo")
            if not df.empty:
                vix = df.iloc[-1]['Close']

        if vix == 0:
            return {'value': 0, 'interpretation': 'UNKNOWN', 'message': 'VIX data unavailable'}

        # Interpretation
        if vix < 12:
            interpretation = "EXTREME_COMPLACENCY"
            message = "Markets extremely calm - correction risk elevated"
        elif vix < 15:
            interpretation = "LOW_FEAR"
            message = "Low volatility - favorable for trending markets"
        elif vix < 20:
            interpretation = "NORMAL"
            message = "Normal volatility levels"
        elif vix < 25:
            interpretation = "ELEVATED"
            message = "Elevated fear - be cautious with new positions"
        else:
            interpretation = "HIGH_FEAR"
            message = "High fear - potential buying opportunity forming"

        return {
            'value': round(vix, 2),
            'interpretation': interpretation,
            'message': message
        }
    except Exception as e:
        logger.error(f"Error analyzing VIX: {e}")
        return {'value': 0, 'interpretation': 'UNKNOWN', 'message': 'VIX data unavailable'}


def analyze_sectors() -> dict:
    """
    Analyze sector performance and rotation
    """
    sector_data = []

    for sector_key, symbol in SECTOR_INDICES.items():
        try:
            df = fetch_index_data(symbol, period="3mo")
            if df.empty:
                continue

            # Calculate returns
            latest = df.iloc[-1]['Close']

            # 1-week return
            if len(df) >= 5:
                week_ago = df.iloc[-6]['Close']
                return_1w = ((latest - week_ago) / week_ago) * 100
            else:
                return_1w = 0

            # 1-month return
            if len(df) >= 21:
                month_ago = df.iloc[-22]['Close']
                return_1m = ((latest - month_ago) / month_ago) * 100
            else:
                return_1m = 0

            sector_data.append({
                'key': sector_key,
                'name': SECTOR_NAMES.get(sector_key, sector_key),
                'value': round(latest, 2),
                'return_1w': round(return_1w, 2),
                'return_1m': round(return_1m, 2)
            })

        except Exception as e:
            logger.error(f"Error analyzing {sector_key}: {e}")
            continue

    # Sort by 1-week return
    sector_data.sort(key=lambda x: x['return_1w'], reverse=True)

    # Identify leaders and laggards
    leaders = sector_data[:3] if len(sector_data) >= 3 else sector_data
    laggards = sector_data[-3:] if len(sector_data) >= 3 else []
    laggards.reverse()

    return {
        'all': sector_data,
        'leaders': [{'name': s['name'], 'return_1w': s['return_1w']} for s in leaders],
        'laggards': [{'name': s['name'], 'return_1w': s['return_1w']} for s in laggards]
    }


def generate_market_stance(nifty: dict, vix: dict, sectors: dict, macro: dict | None = None) -> dict:
    """
    Generate overall market stance.

    Trend score is the composite EMA-stack signal (identify_trend uses
    EMA ordering). To avoid double-counting price-vs-MA, we add only one
    extra MA gate: above/below the 200 EMA (long-term trend filter).
    VIX and RSI are independent signals.
    """
    contributions: dict[str, int] = {}

    # Composite trend signal (uses EMA20/50/200 ordering internally)
    trend_pts = {
        'STRONG_BULLISH': 30,
        'BULLISH': 20,
        'SIDEWAYS': 5,
        'BEARISH': -20,
        'STRONG_BEARISH': -30,
    }.get(nifty.get('trend'), 0)
    contributions['trend'] = trend_pts

    # 200-DMA gate: independent long-term filter (the only EMA bonus we keep)
    contributions['long_term_gate'] = 15 if nifty.get('above_200ema') else -20

    # VIX regime
    vix_value = vix.get('value') or 15
    if vix_value < 12:
        contributions['vix'] = -5   # too complacent, correction risk
    elif vix_value < 18:
        contributions['vix'] = 10   # favorable
    elif vix_value < 25:
        contributions['vix'] = 0    # normal
    else:
        contributions['vix'] = 5    # fear = opportunity (mild)

    # RSI regime
    rsi = nifty.get('rsi') or 50
    if 40 <= rsi <= 60:
        contributions['rsi'] = 10   # healthy zone
    elif 30 <= rsi < 40 or 60 < rsi <= 70:
        contributions['rsi'] = 5
    elif rsi > 75:
        contributions['rsi'] = -10  # overbought
    elif rsi < 30:
        contributions['rsi'] = 5    # oversold bounce potential
    else:
        contributions['rsi'] = 0

    # Macro overlay (FII/DII flows, USD/INR, US 10Y, Brent)
    if macro:
        contributions['macro'] = macro_stance_contribution(macro)

    stance_score = sum(contributions.values())

    # Thresholds calibrated to new max ~65 / min ~-65
    if stance_score >= 40:
        stance, cash = "BULLISH", 10
    elif stance_score >= 20:
        stance, cash = "CAUTIOUSLY_BULLISH", 15
    elif stance_score >= 0:
        stance, cash = "NEUTRAL", 25
    elif stance_score >= -20:
        stance, cash = "CAUTIOUSLY_BEARISH", 35
    else:
        stance, cash = "BEARISH", 50

    return {
        'stance': stance,
        'score': stance_score,
        'cash_recommendation': cash,
        'contributions': contributions,
    }


def generate_outlook(nifty: dict, vix: dict, sectors: dict, stance: dict) -> str:
    """
    Generate human-readable market outlook
    """
    outlook_parts = []

    # Nifty analysis
    trend_desc = {
        'STRONG_BULLISH': "strong bullish structure with all EMAs aligned",
        'BULLISH': "bullish trend",
        'SIDEWAYS': "sideways consolidation",
        'BEARISH': "bearish trend",
        'STRONG_BEARISH': "strong bearish structure"
    }

    outlook_parts.append(
        f"Nifty 50 at {nifty.get('value', 0):,.0f} maintains {trend_desc.get(nifty.get('trend'), 'mixed trend')}."
    )

    # EMA status
    if nifty.get('above_200ema'):
        outlook_parts.append(
            f"Price is {nifty.get('distance_200dma', 0):.1f}% above 200 DMA - long-term trend intact."
        )
    else:
        outlook_parts.append(
            "Price below 200 DMA - long-term trend has weakened. Exercise caution."
        )

    # Support/Resistance
    if nifty.get('support'):
        outlook_parts.append(
            f"Key support at {nifty['support'][0]:,.0f}. Resistance at {nifty.get('resistance', [0])[0]:,.0f}."
        )

    # VIX
    outlook_parts.append(f"India VIX at {vix.get('value', 0):.1f} - {vix.get('message', '')}.")

    # Sectors
    leaders = sectors.get('leaders', [])
    laggards = sectors.get('laggards', [])

    if leaders:
        leader_names = ", ".join([s['name'] for s in leaders])
        outlook_parts.append(f"Sector leadership from {leader_names}.")

    if laggards:
        laggard_names = ", ".join([s['name'] for s in laggards])
        outlook_parts.append(f"Weakness in {laggard_names}.")

    return " ".join(outlook_parts)


def generate_strategy(stance: dict, sectors: dict, vix: dict) -> str:
    """
    Generate actionable strategy recommendation
    """
    strategy_parts = []

    stance_value = stance.get('stance', 'NEUTRAL')
    cash = stance.get('cash_recommendation', 20)

    if stance_value == 'BULLISH':
        strategy_parts.append(
            f"Deploy capital on pullbacks to support levels. Maintain {cash}% cash buffer."
        )
        strategy_parts.append("Favor momentum stocks in leading sectors.")
    elif stance_value == 'CAUTIOUSLY_BULLISH':
        strategy_parts.append(
            f"Deploy capital gradually. Keep {cash}% cash for volatility."
        )
        strategy_parts.append("Focus on quality stocks with strong fundamentals.")
    elif stance_value == 'NEUTRAL':
        strategy_parts.append(
            f"Range-bound strategy. Maintain {cash}% cash."
        )
        strategy_parts.append("Buy at support, book profits at resistance. Reduce position sizes.")
    elif stance_value == 'CAUTIOUSLY_BEARISH':
        strategy_parts.append(
            f"Defensive mode. Keep {cash}% in cash/debt."
        )
        strategy_parts.append("Avoid aggressive entries. Focus on capital preservation.")
    else:  # BEARISH
        strategy_parts.append(
            f"Capital preservation priority. {cash}% in cash/liquid funds."
        )
        strategy_parts.append("Avoid new long positions. Wait for trend reversal confirmation.")

    # Sector-specific
    leaders = sectors.get('leaders', [])
    laggards = sectors.get('laggards', [])

    if leaders:
        strategy_parts.append(
            f"Overweight: {', '.join([s['name'] for s in leaders])}."
        )

    if laggards:
        strategy_parts.append(
            f"Underweight/Avoid: {', '.join([s['name'] for s in laggards])}."
        )

    # VIX warning
    vix_value = vix.get('value', 15)
    if vix_value < 12:
        strategy_parts.append("VIX extremely low - hedge existing positions, expect volatility spike.")
    elif vix_value > 25:
        strategy_parts.append("High VIX often precedes bounces - consider averaging into quality names.")

    return " ".join(strategy_parts)


def run_market_analysis() -> dict:
    """
    Run complete market analysis (Nifty + sectors + VIX + macro -> stance + strategy)
    """
    logger.info("Running market analysis...")

    nifty = analyze_nifty()
    banknifty = analyze_banknifty()
    vix = analyze_vix()
    sectors = analyze_sectors()
    macro = run_macro_analysis()

    stance = generate_market_stance(nifty, vix, sectors, macro=macro)

    outlook = generate_outlook(nifty, vix, sectors, stance)
    strategy = generate_strategy(stance, sectors, vix)

    result = {
        'generated_at': datetime.now().isoformat(),
        'nifty': nifty,
        'banknifty': banknifty,
        'vix': vix,
        'sectors': sectors,
        'macro': macro,
        'stance': stance,
        'outlook': outlook,
        'strategy': strategy,
    }

    logger.info(f"Market analysis complete. Stance: {stance['stance']}")
    return result


if __name__ == "__main__":
    analysis = run_market_analysis()
    print(f"\n=== MARKET ANALYSIS ===")
    print(f"Nifty: {analysis['nifty'].get('value', 'N/A')}")
    print(f"Trend: {analysis['nifty'].get('trend', 'N/A')}")
    print(f"VIX: {analysis['vix'].get('value', 'N/A')}")
    print(f"Stance: {analysis['stance'].get('stance', 'N/A')}")
    print(f"\nOutlook: {analysis['outlook']}")
    print(f"\nStrategy: {analysis['strategy']}")

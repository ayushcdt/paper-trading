"""
Technical Analysis Indicators
Based on Artha 2.0 - Correct implementations
"""

import pandas as pd
import numpy as np


def calculate_true_range(df: pd.DataFrame) -> pd.Series:
    """
    Calculate True Range (NOT just High - Low)

    TR = max(
        High - Low,
        |High - Previous Close|,
        |Low - Previous Close|
    )
    """
    high = df['High']
    low = df['Low']
    prev_close = df['Close'].shift(1)

    tr1 = high - low
    tr2 = abs(high - prev_close)
    tr3 = abs(low - prev_close)

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return true_range


def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Average True Range
    """
    tr = calculate_true_range(df)
    atr = tr.rolling(window=period).mean()
    return atr


def calculate_ema(series: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average"""
    return series.ewm(span=period, adjust=False).mean()


def calculate_sma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average"""
    return series.rolling(window=period).mean()


def calculate_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Calculate Relative Strength Index

    RSI = 100 - (100 / (1 + RS))
    RS = Average Gain / Average Loss
    """
    delta = df['Close'].diff()

    gain = delta.where(delta > 0, 0)
    loss = (-delta).where(delta < 0, 0)

    avg_gain = gain.rolling(window=period).mean()
    avg_loss = loss.rolling(window=period).mean()

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def calculate_macd(
    df: pd.DataFrame,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> tuple:
    """
    Calculate MACD (Moving Average Convergence Divergence)

    Returns: (macd_line, signal_line, histogram)
    """
    ema_fast = calculate_ema(df['Close'], fast)
    ema_slow = calculate_ema(df['Close'], slow)

    macd_line = ema_fast - ema_slow
    signal_line = calculate_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return macd_line, signal_line, histogram


def calculate_bollinger_bands(
    df: pd.DataFrame,
    period: int = 20,
    std_dev: float = 2.0
) -> tuple:
    """
    Calculate Bollinger Bands

    Returns: (upper_band, middle_band, lower_band)
    """
    middle = calculate_sma(df['Close'], period)
    std = df['Close'].rolling(window=period).std()

    upper = middle + (std * std_dev)
    lower = middle - (std * std_dev)

    return upper, middle, lower


def calculate_volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """
    Calculate volume ratio vs average

    Volume Ratio = Current Volume / Average Volume
    """
    avg_volume = df['Volume'].rolling(window=period).mean()
    volume_ratio = df['Volume'] / avg_volume
    return volume_ratio


def calculate_relative_strength(
    stock_returns: pd.Series,
    benchmark_returns: pd.Series
) -> float:
    """
    Calculate Relative Strength vs Benchmark

    RS Ratio = Stock Return / Benchmark Return
    RS > 1 means stock is outperforming
    """
    stock_return = (1 + stock_returns).prod() - 1
    benchmark_return = (1 + benchmark_returns).prod() - 1

    if benchmark_return == 0:
        return 1.0

    return (1 + stock_return) / (1 + benchmark_return)


def identify_trend(df: pd.DataFrame) -> str:
    """
    Identify trend based on EMA alignment

    Returns: 'BULLISH', 'BEARISH', or 'SIDEWAYS'
    """
    if len(df) < 200:
        return 'INSUFFICIENT_DATA'

    close = df['Close'].iloc[-1]
    ema_20 = calculate_ema(df['Close'], 20).iloc[-1]
    ema_50 = calculate_ema(df['Close'], 50).iloc[-1]
    ema_200 = calculate_ema(df['Close'], 200).iloc[-1]

    # Strong bullish: Price > EMA20 > EMA50 > EMA200
    if close > ema_20 > ema_50 > ema_200:
        return 'STRONG_BULLISH'

    # Bullish: Price > EMA200 and EMA20 > EMA50
    if close > ema_200 and ema_20 > ema_50:
        return 'BULLISH'

    # Strong bearish: Price < EMA20 < EMA50 < EMA200
    if close < ema_20 < ema_50 < ema_200:
        return 'STRONG_BEARISH'

    # Bearish: Price < EMA200
    if close < ema_200:
        return 'BEARISH'

    return 'SIDEWAYS'


def calculate_support_resistance(df: pd.DataFrame, lookback: int = 50) -> dict:
    """
    Calculate support and resistance levels based on recent pivots
    """
    recent = df.tail(lookback)

    # Find swing highs and lows
    highs = recent['High'].nlargest(3).tolist()
    lows = recent['Low'].nsmallest(3).tolist()

    current_price = df['Close'].iloc[-1]

    # Resistance: levels above current price
    resistance = sorted([h for h in highs if h > current_price])

    # Support: levels below current price
    support = sorted([l for l in lows if l < current_price], reverse=True)

    return {
        'support': support[:3],
        'resistance': resistance[:3]
    }


def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicators to dataframe
    """
    df = df.copy()

    # Moving Averages
    df['EMA_20'] = calculate_ema(df['Close'], 20)
    df['EMA_50'] = calculate_ema(df['Close'], 50)
    df['EMA_200'] = calculate_ema(df['Close'], 200)
    df['SMA_20'] = calculate_sma(df['Close'], 20)

    # ATR
    df['ATR'] = calculate_atr(df, 14)

    # RSI
    df['RSI'] = calculate_rsi(df, 14)

    # MACD
    df['MACD'], df['MACD_Signal'], df['MACD_Hist'] = calculate_macd(df)

    # Bollinger Bands
    df['BB_Upper'], df['BB_Middle'], df['BB_Lower'] = calculate_bollinger_bands(df)

    # Volume
    df['Volume_Ratio'] = calculate_volume_ratio(df, 20)

    return df

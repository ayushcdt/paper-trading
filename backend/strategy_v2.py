"""
Artha Momentum v2 -- evidence-based redesign after the v1 backtest lost 32%.

Key changes from v1 (per Artha's fix plan):
  1. Universe: drops the top-20 mega-caps (where momentum dies per Nigam-Pandey 2023).
  2. Score: vol-adjusted 12-1 momentum (canonical), 60/30/10 weights for 12-1/6m/3m.
  3. Rebalance: monthly (was 5d). Hold until exit signal (was 10d).
  4. Regime filter: Nifty>200DMA + breadth>50% + VIX<75th-percentile gates.
  5. Exits: trailing max(20d low, 50 EMA), -15% hard stop, drop-from-top-30.
  6. Drawdown circuit breaker: 10% / 20% / 25% triggers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd


# Universe lists -- "drop top-20 mega-caps" implementation.
# Source: typical Nifty 50 top-20 by free-float market cap. Hardcoded since we
# don't have point-in-time market cap data; refresh annually.
TOP_20_DROP = {
    "RELIANCE", "TCS", "HDFCBANK", "BHARTIARTL", "ICICIBANK",
    "INFY", "SBIN", "LICI", "HINDUNILVR", "ITC",
    "LT", "BAJFINANCE", "KOTAKBANK", "HCLTECH", "MARUTI",
    "SUNPHARMA", "AXISBANK", "M&M", "ONGC", "NTPC",
}


# ---------- Scoring ----------------------------------------------------------

def annualized_volatility(close: pd.Series, window: int = 252) -> float:
    """Annualized stdev of daily log returns over the trailing window."""
    if len(close) < window + 1:
        return float("nan")
    rets = np.log(close).diff().dropna().tail(window)
    if len(rets) < 20:
        return float("nan")
    return float(rets.std() * np.sqrt(252))


def vol_adjusted_return(close: pd.Series, lookback_days: int, skip_days: int = 0) -> float | None:
    """
    Vol-adjusted return over lookback_days, optionally skipping the most recent
    skip_days bars (e.g., 12-1 momentum = lookback=252, skip=21).
    """
    if len(close) < lookback_days + skip_days + 1:
        return None
    end_idx = -1 - skip_days if skip_days else -1
    start_idx = end_idx - lookback_days
    if abs(start_idx) > len(close):
        return None
    p_start = close.iloc[start_idx]
    p_end = close.iloc[end_idx]
    if p_start <= 0:
        return None
    raw_return = (p_end / p_start) - 1
    vol = annualized_volatility(close)
    if not np.isfinite(vol) or vol == 0:
        return None
    return float(raw_return / vol)


def momentum_score_v2(close: pd.Series) -> float | None:
    """
    Composite momentum: 60% (12m-1m vol-adj) + 30% (6m vol-adj) + 10% (3m vol-adj).
    Returns None if any component cannot be computed (e.g., insufficient history).
    """
    s_12_1 = vol_adjusted_return(close, lookback_days=231, skip_days=21)  # 252-21 trading days
    s_6m   = vol_adjusted_return(close, lookback_days=126)
    s_3m   = vol_adjusted_return(close, lookback_days=63)
    if s_12_1 is None or s_6m is None or s_3m is None:
        return None
    return 0.60 * s_12_1 + 0.30 * s_6m + 0.10 * s_3m


# ---------- Regime gates -----------------------------------------------------

@dataclass
class RegimeState:
    nifty_above_200dma: bool
    breadth_above_50pct: bool
    vix_below_75th_pct: bool
    deployment_pct: float       # 1.0 = full, 0.5 = half, 0.0 = cash
    reason: str


def assess_regime(
    nifty_close: pd.Series,
    breadth_above_200dma_pct: float,
    vix_value: float,
    vix_history: pd.Series,
) -> RegimeState:
    """
    Three-gate regime check per Artha plan.
        Gate 1: Nifty 200 > its own 200 DMA
        Gate 2: market breadth (% of constituents above own 200 DMA) > 50
        Gate 3: India VIX < 75th percentile of trailing 252 days

    Deployment:
        Gate 1+2 fail -> 0% (cash)
        Only Gate 3 fails -> 50%
        All pass -> 100%
    """
    if len(nifty_close) < 200:
        return RegimeState(False, False, False, 0.0, "Insufficient Nifty history")

    nifty_200dma = nifty_close.tail(200).mean()
    g1 = nifty_close.iloc[-1] > nifty_200dma
    g2 = breadth_above_200dma_pct > 50

    vix_75 = vix_history.dropna().tail(252).quantile(0.75) if len(vix_history) >= 60 else 25
    g3 = vix_value < vix_75

    if not g1 or not g2:
        deploy = 0.0
        reason = f"Risk-off: Nifty>200DMA={g1}, breadth>50%={g2}"
    elif not g3:
        deploy = 0.5
        reason = f"High vol: VIX={vix_value:.1f} >= 75th pct ({vix_75:.1f}); half size"
    else:
        deploy = 1.0
        reason = "All gates pass: full deployment"

    return RegimeState(g1, g2, g3, deploy, reason)


# ---------- Exits ------------------------------------------------------------

@dataclass
class ExitSignal:
    triggered: bool
    reason: str
    exit_price: Optional[float] = None


def check_exit_v2(
    df_so_far: pd.DataFrame,
    entry_price: float,
    entry_idx: int,
    current_idx: int,
    current_rank: Optional[int],
    max_hold_days: int = 90,
    hard_stop_pct: float = -15.0,
    rank_drop_threshold: int = 30,
) -> ExitSignal:
    """
    Multi-trigger exit:
      1. Hard stop: -15% from entry
      2. Trailing exit: close < max(20-day low, 50 EMA)
      3. Rank exit: dropped out of top N at rebalance
      4. Time stop: held >= max_hold_days
    """
    if current_idx >= len(df_so_far):
        return ExitSignal(False, "out of data")

    row = df_so_far.iloc[current_idx]
    close = float(row["Close"])

    # 1. Hard stop
    pct_from_entry = ((close - entry_price) / entry_price) * 100
    if pct_from_entry <= hard_stop_pct:
        return ExitSignal(True, f"hard stop {pct_from_entry:.1f}%", close)

    # 2. Trailing structural exit
    window = df_so_far.iloc[max(0, current_idx - 19) : current_idx + 1]
    low_20d = float(window["Low"].min())
    ema_50 = float(window["Close"].ewm(span=50, adjust=False).mean().iloc[-1])
    trailing_floor = max(low_20d, ema_50)
    if close < trailing_floor:
        return ExitSignal(True, f"trail break <{trailing_floor:.2f}", close)

    # 3. Rank dropout
    if current_rank is not None and current_rank > rank_drop_threshold:
        return ExitSignal(True, f"rank dropped to {current_rank}", close)

    # 4. Time stop
    bars_held = current_idx - entry_idx
    if bars_held >= max_hold_days:
        return ExitSignal(True, f"time stop {bars_held}d", close)

    return ExitSignal(False, "hold")


# ---------- Drawdown circuit breaker -----------------------------------------

def position_size_multiplier(equity_curve: list[float]) -> float:
    """
    Per Artha plan:
       portfolio DD <10%  -> 1.0 (full size)
       10% <= DD < 20%   -> 0.5
       20% <= DD < 25%   -> 0.25
       DD >= 25%         -> 0.0 (no new entries; close existing only)
    """
    if not equity_curve:
        return 1.0
    peak = max(equity_curve)
    current = equity_curve[-1]
    if peak <= 0:
        return 1.0
    dd = (peak - current) / peak
    if dd >= 0.25:
        return 0.0
    if dd >= 0.20:
        return 0.25
    if dd >= 0.10:
        return 0.5
    return 1.0

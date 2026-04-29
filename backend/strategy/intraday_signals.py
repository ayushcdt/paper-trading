"""
Intraday signals for the rebalance loop.

Daily-bar momentum (used by momentum_agg) barely shifts intraday because the
12-1m component dominates and uses prices from 21+ days ago. To catch real
intraday opportunities we need different signals:

  - today_gap_pct       — open vs prior close
  - today_move_pct      — current LTP vs today's open
  - intraday_total_pct  — current LTP vs prior close (gap + move)
  - twenty_day_high     — today's high vs 20-day high (breakout flag)
  - vs_50dma_pct        — today's price vs 50-day moving average

A composite intraday_strength score combines these. The rebalance loop uses
this as a *secondary* signal: swap a held weak-intraday position for a
non-held strong-intraday candidate, even if neither has changed daily-bar pick
status.

Cost of computing per symbol: 1 LTP fetch (already done by mark_to_market) +
read 22-day bars from cache. ~5ms per symbol; ~2.5s for 500 symbols.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_store import get_bars
from data_fetcher import SYMBOL_TOKENS
import pandas as pd


@dataclass
class IntradayFeatures:
    symbol: str
    today_gap_pct: float       # (today_open - yesterday_close) / yesterday_close * 100
    intraday_move_pct: float   # (current - today_open) / today_open * 100
    total_pct: float           # (current - yesterday_close) / yesterday_close * 100
    breakout_20d: bool         # current > 20-day high (excl today)
    pct_above_20d_high: float  # how far above (negative if below)
    pct_above_50dma: float
    composite_strength: float  # 0..100 composite ranking score


def _safe_pct(numerator, denominator):
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100


def compute_intraday_features(symbol: str, current_ltp: float, today_open: float | None = None) -> IntradayFeatures | None:
    """Compute intraday features for one symbol. Requires bars.db has at least
    20 days of history."""
    df = get_bars(symbol, n_days=80)
    if len(df) < 22:
        return None
    df = df.copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)
    yesterday_close = float(df["Close"].iloc[-1])
    twenty_day_high = float(df["High"].iloc[-20:].max())
    fifty_day_avg = float(df["Close"].iloc[-50:].mean()) if len(df) >= 50 else yesterday_close

    if today_open is None:
        # If today's first bar is in DB use that, else fall back to yesterday close as proxy
        today_open = yesterday_close

    today_gap_pct = _safe_pct(today_open - yesterday_close, yesterday_close)
    intraday_move_pct = _safe_pct(current_ltp - today_open, today_open)
    total_pct = _safe_pct(current_ltp - yesterday_close, yesterday_close)
    pct_above_20d_high = _safe_pct(current_ltp - twenty_day_high, twenty_day_high)
    pct_above_50dma = _safe_pct(current_ltp - fifty_day_avg, fifty_day_avg)
    breakout_20d = current_ltp > twenty_day_high

    # Composite strength score: positive = strong, negative = weak. Range roughly -50..+50.
    composite = (
        2.0 * total_pct                              # core: today's net move (most important)
        + 0.5 * (intraday_move_pct - today_gap_pct)  # sustained intraday strength (not just gap up + fade)
        + (10.0 if breakout_20d else 0.0)            # breakout bonus
        + 0.3 * pct_above_50dma                      # trend confirmation
    )

    return IntradayFeatures(
        symbol=symbol,
        today_gap_pct=round(today_gap_pct, 2),
        intraday_move_pct=round(intraday_move_pct, 2),
        total_pct=round(total_pct, 2),
        breakout_20d=breakout_20d,
        pct_above_20d_high=round(pct_above_20d_high, 2),
        pct_above_50dma=round(pct_above_50dma, 2),
        composite_strength=round(composite, 2),
    )


def rank_intraday(symbols: list[str], ltps: dict[str, float], today_opens: dict[str, float] | None = None) -> list[IntradayFeatures]:
    """Compute intraday features for many symbols and return sorted by composite strength desc."""
    today_opens = today_opens or {}
    out = []
    for sym in symbols:
        ltp = ltps.get(sym)
        if not ltp or ltp <= 0:
            continue
        feat = compute_intraday_features(sym, ltp, today_opens.get(sym))
        if feat:
            out.append(feat)
    out.sort(key=lambda f: -f.composite_strength)
    return out

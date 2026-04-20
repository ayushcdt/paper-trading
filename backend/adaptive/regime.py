"""
Regime classifier -- labels current market into one of 4 states.

Rules (not ML; transparent so you can audit every decision):

BULL_LOW_VOL  : Nifty > 200DMA, breadth > 55%, VIX in bottom 50% of 252d
BULL_HIGH_VOL : Nifty > 200DMA, breadth > 55%, VIX in top 50% of 252d
RANGE         : (Nifty > 200DMA but breadth 40-55%) or Nifty within 5% of 200DMA
BEAR          : Nifty < 200DMA by >5% OR breadth < 40%

Each state maps to a single strategy variant downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class Regime(str, Enum):
    BULL_LOW_VOL = "BULL_LOW_VOL"
    BULL_HIGH_VOL = "BULL_HIGH_VOL"
    RANGE = "RANGE"
    BEAR = "BEAR"


@dataclass
class RegimeAssessment:
    regime: Regime
    reason: str
    inputs: dict
    # Deploy percent is the ceiling; variants may still size down internally
    deploy_pct: float


def classify_regime(
    nifty_close: pd.Series,
    breadth_above_200dma_pct: float,
    vix_value: float | None,
    vix_history: pd.Series,
) -> RegimeAssessment:
    """
    Returns regime + why. All inputs must be as-of the assessment date
    (no look-ahead -- caller is responsible for slicing).
    """
    if len(nifty_close) < 200:
        return RegimeAssessment(
            regime=Regime.BEAR,
            reason="Insufficient Nifty history (<200 bars)",
            inputs={"bars": len(nifty_close)},
            deploy_pct=0.0,
        )

    last = float(nifty_close.iloc[-1])
    ma_200 = float(nifty_close.tail(200).mean())
    pct_vs_200 = (last - ma_200) / ma_200 * 100

    vix = float(vix_value) if vix_value is not None and not np.isnan(vix_value) else 15.0
    vix_hist = vix_history.dropna().tail(252)
    vix_median = float(vix_hist.median()) if len(vix_hist) >= 60 else 16.0
    vix_low = vix < vix_median  # in bottom half of recent range

    inputs = {
        "nifty_vs_200dma_pct": round(pct_vs_200, 2),
        "breadth_above_200dma_pct": round(breadth_above_200dma_pct, 1),
        "vix": round(vix, 2),
        "vix_median_252d": round(vix_median, 2),
    }

    # BEAR: structurally broken tape
    if pct_vs_200 < -5 or breadth_above_200dma_pct < 40:
        return RegimeAssessment(
            regime=Regime.BEAR,
            reason=f"Nifty {pct_vs_200:+.1f}% vs 200DMA; breadth {breadth_above_200dma_pct:.0f}%",
            inputs=inputs,
            deploy_pct=0.0,
        )

    # RANGE: mixed signals
    if abs(pct_vs_200) <= 5 or 40 <= breadth_above_200dma_pct < 55:
        return RegimeAssessment(
            regime=Regime.RANGE,
            reason=f"Chop: Nifty {pct_vs_200:+.1f}% vs 200DMA; breadth {breadth_above_200dma_pct:.0f}%",
            inputs=inputs,
            deploy_pct=0.5,
        )

    # Clean bullish tape: split by volatility
    if vix_low:
        return RegimeAssessment(
            regime=Regime.BULL_LOW_VOL,
            reason=f"Bull+calm: VIX {vix:.1f} < median {vix_median:.1f}",
            inputs=inputs,
            deploy_pct=1.0,
        )
    return RegimeAssessment(
        regime=Regime.BULL_HIGH_VOL,
        reason=f"Bull+choppy: VIX {vix:.1f} >= median {vix_median:.1f}",
        inputs=inputs,
        deploy_pct=0.75,
    )


def compute_breadth(histories: dict[str, pd.DataFrame], target_date) -> float:
    """% of universe stocks trading above their own 200 DMA as of target_date."""
    # Normalize target_date to a naive pandas Timestamp for robust comparison
    td = pd.Timestamp(target_date)
    if td.tz is not None:
        td = td.tz_localize(None)

    above = 0
    total = 0
    for df in histories.values():
        if "Date" not in df.columns or len(df) < 200:
            continue
        dates = pd.to_datetime(df["Date"])
        if hasattr(dates.dtype, "tz") and dates.dtype.tz is not None:
            dates = dates.dt.tz_localize(None)
        idxs = df.index[dates <= td]
        if len(idxs) < 200:
            continue
        latest_idx = int(idxs[-1])
        window = df.iloc[latest_idx - 199 : latest_idx + 1]
        if df.iloc[latest_idx]["Close"] > window["Close"].mean():
            above += 1
        total += 1
    return (above / total * 100) if total else 0.0

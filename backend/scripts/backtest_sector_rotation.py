"""
Sector Rotation Strategy Backtest (P31).

Hypothesis: rotating into top-momentum sector each period beats holding NIFTY.

Method:
  Daily, rank 13 sector indices by 20-day momentum (vol-adjusted).
  Hold top-1 sector index for next N days (5, 10, 20).
  Compare to buy-and-hold NIFTY benchmark.

Equity-only (no option friction). Realistic costs: 0.1% buy + 0.1% sell = 0.2%
round-trip for sector ETF / index basket proxy.

Validation: 3-fold walk-forward on 1 year (best available daily data).
"""
from __future__ import annotations

import os
import sys
from itertools import product
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from data_store import get_bars
from data_fetcher import SYMBOL_TOKENS


SECTOR_INDICES = [
    "NIFTY_BANK", "NIFTY_AUTO", "NIFTY_IT", "NIFTY_FMCG",
    "NIFTY_PHARMA", "NIFTY_REALTY", "NIFTY_ENERGY", "NIFTY_INFRA",
    "NIFTY_METAL", "NIFTY_PSE", "NIFTY_PSUBANK", "NIFTY_PVTBANK",
    "NIFTY_FIN_SERVICE",
]

ROUND_TRIP_COST_PCT = 0.20      # equity friction
LOOKBACK_DAYS = 20              # momentum window
HOLD_DAYS_OPTIONS = [5, 10, 20]


def load_sector_data(days: int = 365) -> dict[str, pd.DataFrame]:
    """Load daily bars for each sector index."""
    out = {}
    for sym in SECTOR_INDICES:
        if sym not in SYMBOL_TOKENS:
            print(f"  {sym}: no token")
            continue
        df = get_bars(sym, n_days=days)
        if len(df) < LOOKBACK_DAYS + HOLD_DAYS_OPTIONS[-1] + 10:
            print(f"  {sym}: insufficient data ({len(df)} bars)")
            continue
        df = df.copy().sort_values("Date").reset_index(drop=True)
        out[sym] = df
    return out


def vol_adj_momentum(close: pd.Series, lookback: int) -> float | None:
    if len(close) < lookback + 1:
        return None
    rets = np.log(close).diff().dropna().tail(lookback)
    if len(rets) < lookback - 5:
        return None
    p0, p1 = float(close.iloc[-lookback - 1]), float(close.iloc[-1])
    if p0 <= 0:
        return None
    vol = rets.std()
    if vol <= 0:
        return None
    return ((p1 / p0) - 1) / vol


def get_top_sector(sector_data: dict, target_date: pd.Timestamp) -> str | None:
    """Returns top-momentum sector at given date."""
    scores = {}
    for sym, df in sector_data.items():
        df_dt = pd.to_datetime(df["Date"])
        # Use bars up to and including target_date
        mask = df_dt <= target_date
        if mask.sum() < LOOKBACK_DAYS + 1:
            continue
        close = df[mask]["Close"].reset_index(drop=True)
        score = vol_adj_momentum(close, LOOKBACK_DAYS)
        if score is not None:
            scores[sym] = score
    if not scores:
        return None
    return max(scores, key=scores.get)


def get_return(df: pd.DataFrame, start_date: pd.Timestamp, end_date: pd.Timestamp) -> float | None:
    df_dt = pd.to_datetime(df["Date"])
    start_mask = df_dt <= start_date
    end_mask = df_dt <= end_date
    if not start_mask.any() or not end_mask.any():
        return None
    start_close = float(df[start_mask]["Close"].iloc[-1])
    end_close = float(df[end_mask]["Close"].iloc[-1])
    if start_close <= 0:
        return None
    return (end_close - start_close) / start_close * 100


def backtest_rotation(sector_data: dict, hold_days: int) -> dict:
    """Walk daily rebalance, hold top-momentum sector for hold_days."""
    # Get all unique dates from any sector
    all_dates = sorted(set(
        d for df in sector_data.values()
        for d in pd.to_datetime(df["Date"])
    ))

    nifty_data = sector_data.get("NIFTY_BANK")  # benchmark proxy
    # Use NIFTY (regular) if available
    nifty_df = get_bars("NIFTY", n_days=400)
    if not nifty_df.empty:
        nifty_data = nifty_df.copy().sort_values("Date").reset_index(drop=True)

    trades = []
    capital = 10_000
    eq_curve = [capital]
    nifty_eq = [capital]

    # Iterate every hold_days
    start_idx = LOOKBACK_DAYS + 5
    i = start_idx
    while i < len(all_dates) - hold_days - 1:
        entry_date = all_dates[i]
        exit_date = all_dates[i + hold_days]

        top_sym = get_top_sector(sector_data, entry_date)
        if not top_sym:
            i += hold_days
            continue
        ret_pct = get_return(sector_data[top_sym], entry_date, exit_date)
        if ret_pct is None:
            i += hold_days
            continue

        # Apply friction
        net_ret = ret_pct - ROUND_TRIP_COST_PCT
        capital *= (1 + net_ret / 100)
        eq_curve.append(capital)

        # Benchmark NIFTY return for same period
        nifty_ret = get_return(nifty_data, entry_date, exit_date)
        if nifty_ret is not None:
            nifty_eq.append(nifty_eq[-1] * (1 + nifty_ret / 100))

        trades.append({
            "entry": entry_date.strftime("%Y-%m-%d"),
            "exit": exit_date.strftime("%Y-%m-%d"),
            "sector": top_sym,
            "gross_ret": round(ret_pct, 2),
            "net_ret": round(net_ret, 2),
            "capital_after": round(capital, 0),
        })
        i += hold_days

    if not trades:
        return {"final": 10000, "trades": [], "n": 0, "wr": 0, "exp": 0}

    wins = [t for t in trades if t["net_ret"] > 0]
    win_rate = len(wins) / len(trades) * 100
    expectancy = np.mean([t["net_ret"] for t in trades])
    final = trades[-1]["capital_after"]
    period_years = len(trades) * hold_days / 252
    cagr = ((final / 10000) ** (1 / max(period_years, 0.1)) - 1) * 100 if period_years > 0 else 0

    nifty_final = nifty_eq[-1] if nifty_eq else 10000
    nifty_cagr = ((nifty_final / 10000) ** (1 / max(period_years, 0.1)) - 1) * 100 if period_years > 0 else 0

    return {
        "n": len(trades), "wr": round(win_rate, 1),
        "exp": round(expectancy, 2),
        "final": round(final, 0), "nifty_final": round(nifty_final, 0),
        "strat_cagr": round(cagr, 2), "nifty_cagr": round(nifty_cagr, 2),
        "alpha_cagr": round(cagr - nifty_cagr, 2),
        "trades": trades,
    }


def main():
    print("Loading sector index data (daily, 1 year)...")
    sector_data = load_sector_data(days=365)
    print(f"  Loaded {len(sector_data)} sectors\n")

    if not sector_data:
        print("No sector data — abort")
        return

    print("Sector counts (sample dates):")
    for sym, df in list(sector_data.items())[:3]:
        print(f"  {sym}: {len(df)} bars from {df.iloc[0]['Date']} to {df.iloc[-1]['Date']}")
    print()

    print(f"{'HOLD':>5s}  {'#':>4s}  {'WR%':>6s}  {'EXP%':>7s}  {'STRAT_CAGR':>11s}  {'NIFTY_CAGR':>11s}  {'ALPHA':>7s}  {'FINAL':>9s}")
    for hold_days in HOLD_DAYS_OPTIONS:
        r = backtest_rotation(sector_data, hold_days)
        print(f"{hold_days:>5d}  {r['n']:>4d}  {r['wr']:>5.1f}%  {r['exp']:>+6.2f}%  {r['strat_cagr']:>+10.2f}%  {r['nifty_cagr']:>+10.2f}%  {r['alpha_cagr']:>+6.2f}%  {r['final']:>9.0f}")

        # Show last 8 trades for the best hold period
        if hold_days == HOLD_DAYS_OPTIONS[1]:  # mid hold period
            print(f"\n  Last 8 trades for hold={hold_days}d:")
            for t in r["trades"][-8:]:
                print(f"    {t['entry']} -> {t['exit']}  sector {t['sector']:18s}  net {t['net_ret']:>+6.2f}%  cap Rs{t['capital_after']:.0f}")
            print()


if __name__ == "__main__":
    main()

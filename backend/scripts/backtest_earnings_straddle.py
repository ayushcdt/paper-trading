"""
Earnings Straddle Backtest (P29).

Hypothesis: ATM CE+PE bought 1 day before scheduled earnings.
- Pre-earnings: IV pumps as event approaches
- Earnings day: stock moves (often >1.5%)
- Post-earnings: IV crushes (-15-30% IV drop)

Win condition: actual stock move > implied move (priced into straddle)
Empirical: ~50-55% of large-cap earnings beat the implied straddle move.

Method:
  For each NIFTY-50 stock with available earnings date:
    Buy ATM CE + ATM PE 1 day before earnings (T-1 close)
    Sell both at T+1 open (next session after earnings)
    P&L = combined option price change minus friction

Reality limits:
  - Stock options have wider bid-ask (1-3% vs 0.5% NIFTY)
  - Lot sizes vary; many cost Rs 25K+ for ATM straddle (out of Rs 13K budget)
  - We model what would have happened on liquid names with affordable straddles
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from data_store import get_bars
from fno.black_scholes import bs_price


# Test on top 12 most liquid NIFTY stocks
LIQUID_FNO_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "ITC", "BAJFINANCE", "MARUTI", "HCLTECH", "SBIN", "AXISBANK", "LT",
]

RISK_FREE = 0.065
STOCK_IV_PRE_EARNINGS = 0.30  # IV typically pumps to 30%+ pre-earnings
STOCK_IV_POST_EARNINGS = 0.20  # crushes back to 20% after
DAYS_TO_EARNINGS_EXPIRY = 7   # weekly option, ~1 week to expiry
STOCK_OPT_SLIPPAGE_PCT = 3.0  # wider bid-ask on stock options
STOCK_OPT_FEE_RS = 80.0       # per round-trip


def _get_earnings_dates_via_yf(symbol):
    """Lookup historical earnings dates."""
    try:
        import yfinance as yf
        for suffix in (".NS", ".BO"):
            ticker = yf.Ticker(f"{symbol}{suffix}")
            hist = ticker.get_earnings_dates(limit=8)
            if hist is not None and not hist.empty:
                # Past earnings only
                past = hist[hist.index < pd.Timestamp.now(tz=hist.index.tz)]
                if len(past) > 0:
                    dates = [d.to_pydatetime().replace(tzinfo=None) for d in past.index]
                    return [d for d in dates if (pd.Timestamp.now() - d).days < 400]
    except Exception as e:
        return []
    return []


def simulate_straddle(stock_df, earnings_date):
    """Simulate buying ATM straddle T-1 close, selling T+1 open."""
    df = stock_df.copy().sort_values("Date").reset_index(drop=True)
    df["Date"] = pd.to_datetime(df["Date"])

    # Find T-1 (last trading day before earnings)
    pre_mask = df["Date"] < earnings_date
    if not pre_mask.any():
        return None
    t_minus_1 = df[pre_mask].iloc[-1]
    pre_idx = df[pre_mask].index[-1]

    # Find T+1 (first trading day after earnings)
    post_mask = df["Date"] > earnings_date
    if not post_mask.any():
        return None
    t_plus_1 = df[post_mask].iloc[0]

    spot_pre = float(t_minus_1["Close"])
    spot_post_open = float(t_plus_1["Open"])
    actual_move_pct = abs(spot_post_open - spot_pre) / spot_pre * 100

    # ATM strike (round to 50 for most stocks)
    strike = round(spot_pre / 50) * 50

    # Pre-earnings (T-1 close): buy CE + PE
    dte_pre = DAYS_TO_EARNINGS_EXPIRY / 365
    ce_pre = bs_price(spot_pre, strike, dte_pre, RISK_FREE, STOCK_IV_PRE_EARNINGS, "CE").premium
    pe_pre = bs_price(spot_pre, strike, dte_pre, RISK_FREE, STOCK_IV_PRE_EARNINGS, "PE").premium
    straddle_pre = (ce_pre + pe_pre) * (1 + STOCK_OPT_SLIPPAGE_PCT / 100)

    # Post-earnings (T+1 open): sell CE + PE with IV crushed
    dte_post = (DAYS_TO_EARNINGS_EXPIRY - 2) / 365  # 2 days passed
    ce_post = bs_price(spot_post_open, strike, dte_post, RISK_FREE, STOCK_IV_POST_EARNINGS, "CE").premium
    pe_post = bs_price(spot_post_open, strike, dte_post, RISK_FREE, STOCK_IV_POST_EARNINGS, "PE").premium
    straddle_post = (ce_post + pe_post) * (1 - STOCK_OPT_SLIPPAGE_PCT / 100)

    # Implied move (rough): straddle_pre as % of spot
    implied_move_pct = (straddle_pre / spot_pre) * 100

    # P&L per straddle
    pnl_pct = (straddle_post - straddle_pre) / straddle_pre * 100
    # Subtract Rs 80 fee per round-trip; assume Rs 5K notional per straddle
    notional_proxy = straddle_pre * 100  # rough lot proxy
    fee_pct = -(STOCK_OPT_FEE_RS / max(notional_proxy, 1000)) * 100
    pnl_pct += fee_pct

    return {
        "earnings_date": earnings_date.strftime("%Y-%m-%d"),
        "spot_pre": round(spot_pre, 2),
        "spot_post": round(spot_post_open, 2),
        "actual_move_pct": round(actual_move_pct, 2),
        "implied_move_pct": round(implied_move_pct, 2),
        "straddle_pre": round(straddle_pre, 2),
        "straddle_post": round(straddle_post, 2),
        "pnl_pct": round(pnl_pct, 2),
        "beat_implied": actual_move_pct > implied_move_pct,
    }


def main():
    print("Fetching earnings dates for liquid F&O stocks...")
    print("(yfinance — may take 1-2 min)\n")
    all_results = []
    for sym in LIQUID_FNO_STOCKS:
        dates = _get_earnings_dates_via_yf(sym)
        if not dates:
            print(f"  {sym}: no earnings dates found, skip")
            continue
        stock_df = get_bars(sym, n_days=400)
        if stock_df.empty or len(stock_df) < 50:
            print(f"  {sym}: insufficient price data, skip")
            continue
        for ed in dates:
            try:
                result = simulate_straddle(stock_df, ed)
                if result:
                    result["symbol"] = sym
                    all_results.append(result)
            except Exception as e:
                continue

    if not all_results:
        print("\nNo earnings results — abort")
        return

    print(f"\n=== Tested {len(all_results)} straddle trades across {len(LIQUID_FNO_STOCKS)} stocks ===\n")

    # Stats
    pnls = [r["pnl_pct"] for r in all_results]
    wins = [p for p in pnls if p > 0]
    win_rate = len(wins) / len(pnls) * 100
    avg_win = np.mean([p for p in pnls if p > 0]) if wins else 0
    avg_loss = np.mean([p for p in pnls if p <= 0]) if (len(pnls) - len(wins)) > 0 else 0
    expectancy = np.mean(pnls)
    median = np.median(pnls)

    print(f"Win rate:        {win_rate:.1f}%  ({len(wins)}/{len(pnls)})")
    print(f"Avg win:         {avg_win:+.2f}%")
    print(f"Avg loss:        {avg_loss:+.2f}%")
    print(f"Expectancy:      {expectancy:+.2f}% per trade")
    print(f"Median:          {median:+.2f}%")
    print(f"Best trade:      {max(pnls):+.2f}%")
    print(f"Worst trade:     {min(pnls):+.2f}%")

    # Did actual move beat implied?
    beat = sum(1 for r in all_results if r["beat_implied"])
    print(f"\nBeat implied move: {beat}/{len(all_results)} = {beat/len(all_results)*100:.1f}%")

    # Compound effect
    cum = 1.0
    for p in pnls:
        cum *= (1 + p / 100 * 0.5)  # 50% capital per trade
    print(f"\nCompound (50% capital per trade): {(cum - 1) * 100:+.1f}%")

    # Show recent trades
    print(f"\n=== LAST 10 TRADES ===")
    print(f"{'symbol':10s} {'date':12s} {'spot_pre':>10s} {'spot_post':>10s} {'move%':>7s} {'impl%':>7s} {'pnl%':>+7s}")
    for r in all_results[-10:]:
        print(f"{r['symbol']:10s} {r['earnings_date']:12s} {r['spot_pre']:>10.2f} {r['spot_post']:>10.2f} "
              f"{r['actual_move_pct']:>+6.2f} {r['implied_move_pct']:>+6.2f} {r['pnl_pct']:>+6.2f}")


if __name__ == "__main__":
    main()

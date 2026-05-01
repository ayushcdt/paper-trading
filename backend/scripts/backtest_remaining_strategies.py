"""
Test all remaining proposed strategies in one script:

  S12: Expiry-day max-pain wing fade
  S13: Day-of-week NIFTY effect
  S14: Cash equity gap continuation (>1.5% gap, hold to EOD)
  S15: Cash equity gap fade (>1.5% gap, hold 1-3 days)
  S16: Multi-day swing on monthly options (mean-rev variant)
  S17: 200-DMA crossover momentum (cash equity)

If any of these shows real positive edge, we have something. If not, the
F&O-strategies-for-retail hypothesis is conclusively dead and we accept the
equity momentum_agg path + capital growth via deposits.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from data_store import get_bars
from data_fetcher import SYMBOL_TOKENS
from fno.black_scholes import bs_price


RISK_FREE = 0.065
NIFTY_IV = 0.159
STRIKE_STEP = 50
LOT_SIZE = 75
OPT_SLIPPAGE = 1.5
OPT_FEE = 80
EQUITY_FRICTION_PCT = 0.20


# ============================================================
# S12: Max-pain wing fade
# ============================================================
def s12_max_pain_test():
    """Expiry day mean-reversion to max-pain.

    Max-pain proxy: ATM strike (most option OI is concentrated near ATM).
    On expiry day at 11:30, if NIFTY > 0.5% from ATM strike, bet on pinning.

    NIFTY weekly expiry is Tuesday (since SEBI 2024). Test all Tuesdays.
    """
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["Date"] = pd.to_datetime(df["Date"])
    df["weekday"] = df["Date"].dt.weekday  # 0=Mon, 1=Tue
    expiries = df[df["weekday"] == 1].reset_index(drop=True)  # Tuesdays
    print(f"\nS12 max-pain wing fade — {len(expiries)} Tuesdays in dataset")

    results = []
    for i, exp_day in expiries.iterrows():
        spot_open = float(exp_day["Open"])
        spot_close = float(exp_day["Close"])
        atm_strike = round(spot_open / STRIKE_STEP) * STRIKE_STEP
        deviation_pct = (spot_open - atm_strike) / atm_strike * 100
        if abs(deviation_pct) < 0.5:
            continue
        # Bet on pinning: if spot > strike, buy PE; if spot < strike, buy CE
        opt_type = "PE" if deviation_pct > 0 else "CE"
        # Theoretical option price at open and close (0.25 days to expiry at open)
        dte_open = 0.25 / 365
        dte_close = 0.001 / 365
        prem_open = bs_price(spot_open, atm_strike, dte_open, RISK_FREE, NIFTY_IV, opt_type).premium
        prem_close = bs_price(spot_close, atm_strike, dte_close, RISK_FREE, NIFTY_IV, opt_type).premium
        if prem_open <= 1:
            continue
        entry = prem_open * (1 + OPT_SLIPPAGE / 100)
        exit_price = prem_close * (1 - OPT_SLIPPAGE / 100)
        gross_pct = (exit_price - entry) / entry * 100
        notional = entry * LOT_SIZE
        if notional <= 0:
            continue
        net_pnl = gross_pct / 100 * notional - OPT_FEE
        net_pct = net_pnl / notional * 100
        results.append({
            "date": exp_day["Date"].strftime("%Y-%m-%d"),
            "spot_open": spot_open, "spot_close": spot_close,
            "atm": atm_strike, "deviation": round(deviation_pct, 2),
            "opt_type": opt_type, "premium_pct": round(net_pct, 2),
        })
    if not results:
        print("  no signals")
        return
    pcts = [r["premium_pct"] for r in results]
    wins = [p for p in pcts if p > 0]
    print(f"  trades: {len(results)}  WR: {len(wins)/len(results)*100:.1f}%  "
          f"exp: {np.mean(pcts):+.2f}%  best: {max(pcts):+.2f}%  worst: {min(pcts):+.2f}%")


# ============================================================
# S13: Day-of-week effect
# ============================================================
def s13_day_of_week_test():
    """Statistical test: do NIFTY daily returns differ by weekday?"""
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["Date"] = pd.to_datetime(df["Date"])
    df["ret"] = df["Close"].pct_change() * 100
    df["weekday"] = df["Date"].dt.day_name()
    print(f"\nS13 day-of-week effect (1-year NIFTY)")
    print(f"  {'day':10s}  {'n':>4s}  {'mean%':>7s}  {'median%':>8s}  {'wr%':>6s}")
    for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
        subset = df[df["weekday"] == d]["ret"].dropna()
        if len(subset) == 0:
            continue
        wr = (subset > 0).sum() / len(subset) * 100
        print(f"  {d:10s}  {len(subset):>4d}  {subset.mean():>+6.2f}%  {subset.median():>+7.2f}%  {wr:>5.1f}%")


# ============================================================
# S14: Gap continuation cash equity
# ============================================================
def s14_gap_continuation():
    """Stocks gapping up >1.5% -> ride to EOD."""
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"\nS14 gap continuation (NIFTY >1.5% gap -> hold to EOD)")
    results = []
    for i in range(1, len(df)):
        prev_close = df.iloc[i - 1]["Close"]
        today_open = df.iloc[i]["Open"]
        today_close = df.iloc[i]["Close"]
        gap_pct = (today_open - prev_close) / prev_close * 100
        if abs(gap_pct) < 1.5:
            continue
        # Direction = same as gap
        if gap_pct > 0:
            ret = (today_close - today_open) / today_open * 100
        else:
            ret = -(today_close - today_open) / today_open * 100
        net_ret = ret - EQUITY_FRICTION_PCT
        results.append({"date": df.iloc[i]["Date"], "gap": round(gap_pct, 2),
                       "ret": round(net_ret, 2)})
    if not results:
        print("  no gaps")
        return
    pcts = [r["ret"] for r in results]
    wr = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    print(f"  trades: {len(results)}  WR: {wr:.1f}%  exp: {np.mean(pcts):+.2f}%  "
          f"compound: {(np.prod([1 + p/100 for p in pcts]) - 1) * 100:+.1f}%")


# ============================================================
# S15: Gap fade cash equity
# ============================================================
def s15_gap_fade():
    """Stocks gapping >1.5% -> fade for 1-3 days."""
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"\nS15 gap fade (NIFTY >1.5% gap -> fade 2 days)")
    results = []
    for i in range(1, len(df) - 2):
        prev_close = df.iloc[i - 1]["Close"]
        today_open = df.iloc[i]["Open"]
        gap_pct = (today_open - prev_close) / prev_close * 100
        if abs(gap_pct) < 1.5:
            continue
        # Fade direction
        end_close = df.iloc[i + 2]["Close"]
        if gap_pct > 0:
            ret = -(end_close - today_open) / today_open * 100
        else:
            ret = (end_close - today_open) / today_open * 100
        net_ret = ret - EQUITY_FRICTION_PCT
        results.append({"date": df.iloc[i]["Date"], "gap": round(gap_pct, 2),
                       "ret": round(net_ret, 2)})
    if not results:
        return
    pcts = [r["ret"] for r in results]
    wr = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    print(f"  trades: {len(results)}  WR: {wr:.1f}%  exp: {np.mean(pcts):+.2f}%  "
          f"compound: {(np.prod([1 + p/100 for p in pcts]) - 1) * 100:+.1f}%")


# ============================================================
# S16: Multi-day swing on monthly options (mean-rev)
# ============================================================
def s16_monthly_mean_rev():
    """Same mean-rev signal but use 25-day-to-expiry option (lower theta)."""
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"\nS16 monthly options mean-rev (25 DTE, hold up to 5 days)")

    signals = []
    for i in range(1, len(df) - 6):
        prev = df.iloc[i - 1]["Close"]
        today = df.iloc[i]["Close"]
        move = (today - prev) / prev * 100
        if abs(move) >= 1.0:
            direction = "BEARISH" if move > 0 else "BULLISH"
            signals.append({"entry_idx": i + 1, "direction": direction,
                          "trigger": round(move, 2)})

    results = []
    for sig in signals:
        idx = sig["entry_idx"]
        if idx + 5 >= len(df):
            continue
        spot = df.iloc[idx]["Open"]
        opt_type = "CE" if sig["direction"] == "BULLISH" else "PE"
        atm = round(spot / STRIKE_STEP) * STRIKE_STEP
        # 25 DTE monthly option
        entry_dte = 25 / 365
        entry_theory = bs_price(spot, atm, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
        if entry_theory <= 1.0:
            continue
        entry = entry_theory * (1 + OPT_SLIPPAGE / 100)

        # Hold up to 5 days, exit on -25%/+50%
        exit_pct = None
        for hold in range(1, 6):
            bar = df.iloc[idx + hold]
            for ohlc in ["High", "Low"]:
                spot_now = bar[ohlc]
                dte_now = max(0.001, entry_dte - hold / 365)
                curr_theory = bs_price(spot_now, atm, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
                curr = curr_theory * (1 - OPT_SLIPPAGE / 100)
                ret_pct = (curr - entry) / entry * 100
                if ret_pct <= -25:
                    exit_pct = -25
                    break
                if ret_pct >= 50:
                    exit_pct = 50
                    break
            if exit_pct is not None:
                break
        if exit_pct is None:
            # Time exit
            bar = df.iloc[idx + 5]
            spot_now = bar["Close"]
            dte_now = max(0.001, entry_dte - 5 / 365)
            curr_theory = bs_price(spot_now, atm, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
            curr = curr_theory * (1 - OPT_SLIPPAGE / 100)
            exit_pct = (curr - entry) / entry * 100
        # Apply fee
        notional = entry * LOT_SIZE
        net_pct = exit_pct - (OPT_FEE / notional * 100)
        results.append({"date": df.iloc[idx]["Date"], "direction": sig["direction"],
                       "exit_pct": round(net_pct, 2)})

    if not results:
        return
    pcts = [r["exit_pct"] for r in results]
    wr = sum(1 for p in pcts if p > 0) / len(pcts) * 100
    print(f"  trades: {len(results)}  WR: {wr:.1f}%  exp: {np.mean(pcts):+.2f}%  "
          f"compound: {(np.prod([1 + p/100 * 0.5 for p in pcts]) - 1) * 100:+.1f}%")


# ============================================================
# S17: 200-DMA crossover momentum (cash equity)
# ============================================================
def s17_dma_crossover():
    """NIFTY closes above 200-DMA -> long. Below -> flat."""
    df = get_bars("NIFTY", n_days=600)  # need 200 days warmup
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"\nS17 NIFTY 200-DMA crossover (cash equity, no leverage)")
    df["MA200"] = df["Close"].rolling(200).mean()
    df["above"] = df["Close"] > df["MA200"]
    df["pos"] = df["above"].shift(1)  # yesterday's signal -> today's position
    df["ret"] = df["Close"].pct_change()
    df["strat_ret"] = df["pos"].astype(float) * df["ret"]
    valid = df.dropna(subset=["MA200", "strat_ret"])
    if valid.empty:
        return
    # Buy/sell trades on crossovers
    crossovers = (valid["above"] != valid["above"].shift(1)).sum()
    cum = (1 + valid["strat_ret"]).cumprod().iloc[-1]
    nifty_cum = (1 + valid["ret"]).cumprod().iloc[-1]
    n_days = len(valid)
    yrs = n_days / 252
    cagr = (cum ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
    nifty_cagr = (nifty_cum ** (1 / yrs) - 1) * 100 if yrs > 0 else 0
    print(f"  crossovers: {crossovers}  days: {n_days}")
    print(f"  STRAT  cum_return: {(cum-1)*100:+.1f}%  CAGR: {cagr:+.2f}%")
    print(f"  NIFTY  cum_return: {(nifty_cum-1)*100:+.1f}%  CAGR: {nifty_cagr:+.2f}%")
    print(f"  ALPHA: {cagr - nifty_cagr:+.2f}%")


def main():
    print("=== ALL REMAINING STRATEGY TESTS ===")
    s12_max_pain_test()
    s13_day_of_week_test()
    s14_gap_continuation()
    s15_gap_fade()
    s16_monthly_mean_rev()
    s17_dma_crossover()


if __name__ == "__main__":
    main()

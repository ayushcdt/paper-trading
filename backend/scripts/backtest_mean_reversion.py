"""
Mean Reversion on NIFTY Extremes Backtest (P32).

Hypothesis: when NIFTY makes extreme intraday move (>= 1.5%), expect
mean reversion in next 1-3 days. Different from intraday-reversal autotrader
which exited same-day; this holds OVERNIGHT.

Two variants:
  V1: Equity-only — buy/short NIFTY ETF (proxy via NIFTY index)
  V2: F&O — buy ATM weekly option in reversion direction

Tests:
  - Trigger thresholds: 1.0%, 1.5%, 2.0%, 2.5% intraday move
  - Hold periods: 1, 2, 3, 5 days
  - Direction: opposite to intraday move (mean reversion)

Friction:
  Equity: 0.2% round-trip
  Options: 1.5% slippage + Rs 80 fees
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from data_store import get_bars
from fno.black_scholes import bs_price


# ---------- Constants ----------
RISK_FREE = 0.065
NIFTY_IV = 0.159
DEFAULT_DTE_DAYS = 4
STRIKE_STEP = 50
EQUITY_FRICTION_PCT = 0.20
OPT_SLIPPAGE_PCT = 1.5
OPT_FEE_RS = 80.0
LOT_SIZE = 75


def detect_extreme_days(nifty_df: pd.DataFrame, threshold_pct: float) -> list[dict]:
    """Find days where NIFTY had extreme intraday move (close vs prev close)."""
    df = nifty_df.copy().sort_values("Date").reset_index(drop=True)
    signals = []
    for i in range(1, len(df) - 5):
        prev_close = df.iloc[i - 1]["Close"]
        today_close = df.iloc[i]["Close"]
        move_pct = (today_close - prev_close) / prev_close * 100
        if abs(move_pct) >= threshold_pct:
            # Mean-reversion direction: opposite
            direction = "BEARISH" if move_pct > 0 else "BULLISH"
            signals.append({
                "entry_idx": i + 1,         # next day
                "entry_date": pd.Timestamp(df.iloc[i + 1]["Date"]),
                "entry_close": float(df.iloc[i]["Close"]),  # signal day's close
                "direction": direction,
                "trigger_move_pct": round(move_pct, 2),
            })
    return signals


def simulate_equity_trade(df: pd.DataFrame, signal: dict, hold_days: int) -> dict:
    entry_idx = signal["entry_idx"]
    if entry_idx + hold_days >= len(df):
        return None
    entry_price = df.iloc[entry_idx]["Open"]  # next day open
    exit_price = df.iloc[entry_idx + hold_days]["Close"]
    direction = signal["direction"]
    raw_ret = (exit_price - entry_price) / entry_price * 100
    if direction == "BEARISH":
        raw_ret = -raw_ret  # short -> profit when price falls
    net_ret = raw_ret - EQUITY_FRICTION_PCT
    return {**signal, "exit_idx": entry_idx + hold_days,
            "exit_date": pd.Timestamp(df.iloc[entry_idx + hold_days]["Date"]),
            "raw_ret": round(raw_ret, 2), "net_ret": round(net_ret, 2)}


def simulate_option_trade(df: pd.DataFrame, signal: dict, hold_days: int,
                          stop_pct: float = -25, target_pct: float = 50) -> dict:
    """Buy ATM option in mean-reversion direction. Hold up to hold_days."""
    entry_idx = signal["entry_idx"]
    if entry_idx + hold_days >= len(df):
        return None
    entry_spot = df.iloc[entry_idx]["Open"]
    direction = signal["direction"]
    opt_type = "CE" if direction == "BULLISH" else "PE"
    # ATM strike
    atm_strike = round(entry_spot / STRIKE_STEP) * STRIKE_STEP

    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry_theory = bs_price(entry_spot, atm_strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
    if entry_theory <= 1.0:
        return None
    entry_premium = entry_theory * (1 + OPT_SLIPPAGE_PCT / 100)

    # Walk forward day-by-day checking stop/target
    for hold in range(1, hold_days + 1):
        idx = entry_idx + hold
        if idx >= len(df):
            return None
        bar = df.iloc[idx]
        for ohlc_field in ["High", "Low", "Close"]:
            if ohlc_field == "Close":
                spot = bar[ohlc_field]
            else:
                spot = bar[ohlc_field]
            elapsed_days = hold
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr_theory = bs_price(spot, atm_strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
            curr = curr_theory * (1 - OPT_SLIPPAGE_PCT / 100)
            ret_pct = (curr - entry_premium) / entry_premium * 100
            if ohlc_field != "Close":
                # Best/worst check
                if ret_pct <= stop_pct:
                    pnl_rs = stop_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
                    adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
                    return {**signal, "exit_idx": idx,
                            "exit_date": pd.Timestamp(bar["Date"]),
                            "premium_pct": round(adj_pct, 2),
                            "exit_reason": "STOP", "hold_days": hold}
                if ret_pct >= target_pct:
                    pnl_rs = target_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
                    adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
                    return {**signal, "exit_idx": idx,
                            "exit_date": pd.Timestamp(bar["Date"]),
                            "premium_pct": round(adj_pct, 2),
                            "exit_reason": "TARGET", "hold_days": hold}
    # Time exit
    bar = df.iloc[entry_idx + hold_days]
    spot = bar["Close"]
    elapsed_days = hold_days
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr_theory = bs_price(spot, atm_strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
    curr = curr_theory * (1 - OPT_SLIPPAGE_PCT / 100)
    gross_pct = (curr - entry_premium) / entry_premium * 100
    pnl_rs = gross_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
    adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
    return {**signal, "exit_idx": entry_idx + hold_days,
            "exit_date": pd.Timestamp(bar["Date"]),
            "premium_pct": round(adj_pct, 2),
            "exit_reason": "TIME", "hold_days": hold_days}


def stats_equity(trades):
    valid = [t for t in trades if t]
    if not valid:
        return {"n": 0, "wr": 0, "exp": 0, "compound": 0, "trades": []}
    wins = [t for t in valid if t["net_ret"] > 0]
    wr = len(wins) / len(valid) * 100
    exp = np.mean([t["net_ret"] for t in valid])
    cum = 1.0
    for t in valid:
        cum *= (1 + t["net_ret"] / 100)
    return {"n": len(valid), "wr": round(wr, 1), "exp": round(exp, 2),
            "compound": round((cum - 1) * 100, 1), "trades": valid}


def stats_options(trades):
    valid = [t for t in trades if t]
    if not valid:
        return {"n": 0, "wr": 0, "exp": 0, "compound": 0, "trades": []}
    wins = [t for t in valid if t["premium_pct"] > 0]
    wr = len(wins) / len(valid) * 100
    exp = np.mean([t["premium_pct"] for t in valid])
    cum = 1.0
    for t in valid:
        cum *= (1 + t["premium_pct"] / 100 * 0.6)  # assume 60% capital deployed
    return {"n": len(valid), "wr": round(wr, 1), "exp": round(exp, 2),
            "compound": round((cum - 1) * 100, 1), "trades": valid}


def main():
    print("Loading NIFTY daily bars (1 year)...")
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"  {len(df)} bars from {df.iloc[0]['Date']} to {df.iloc[-1]['Date']}\n")

    print("=== EQUITY VARIANT (long/short NIFTY) ===")
    print(f"{'THR%':>5s} {'HOLD':>4s} {'#':>4s} {'WR%':>6s} {'EXP%':>7s} {'COMPOUND%':>10s}")
    for thr, hold in product([1.0, 1.5, 2.0, 2.5], [1, 2, 3, 5]):
        signals = detect_extreme_days(df, thr)
        trades = [simulate_equity_trade(df, s, hold) for s in signals]
        st = stats_equity(trades)
        print(f"{thr:>5.1f} {hold:>4d} {st['n']:>4d} {st['wr']:>5.1f}% {st['exp']:>+6.2f}% {st['compound']:>+9.1f}%")
    print()

    print("=== OPTION VARIANT (buy ATM in reversion direction) ===")
    print(f"{'THR%':>5s} {'HOLD':>4s} {'#':>4s} {'WR%':>6s} {'EXP%':>7s} {'COMPOUND%':>10s}")
    for thr, hold in product([1.0, 1.5, 2.0, 2.5], [1, 2, 3]):
        signals = detect_extreme_days(df, thr)
        trades = [simulate_option_trade(df, s, hold) for s in signals]
        st = stats_options(trades)
        print(f"{thr:>5.1f} {hold:>4d} {st['n']:>4d} {st['wr']:>5.1f}% {st['exp']:>+6.2f}% {st['compound']:>+9.1f}%")


if __name__ == "__main__":
    main()

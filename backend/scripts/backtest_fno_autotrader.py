"""
Backtest the F&O autotrader's reversal-detection signal on historical NIFTY data.

Uses 5y of daily NIFTY bars (open/high/low/close). Detects daily-bar
reversal patterns analogous to the intraday autotrader signal:

  BULLISH (-> CALL):
    - prior day close is >=0.5% below 5-day prior close (downtrend setup)
    - today opens above prior close (recovery indication)
    - exit: hold 1-3 days, take profit if NIFTY moves up >X% from open

  BEARISH (-> PUT):
    - prior day close is >=0.5% above 5-day prior close
    - today opens below prior close
    - exit: similar to bullish

Option P&L approximation:
  Yesterday's actual trade: NIFTY +0.67% in 84 min -> CE premium +50%
  Implies sensitivity factor ~75x for OTM 100pt weekly @ 4d to expiry.
  Conservative model: option_pct_move = nifty_pct_move * 50
  Plus theta decay: -5% per day held
  Stop at -25%, target at +50%

Outputs: trade list, win rate, avg win, avg loss, profit factor, max drawdown.
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


# ---------- Backtest config ----------
SETUP_LOOKBACK_DAYS = 5         # days back to measure trend setup
SETUP_MIN_MOVE_PCT = 0.5        # min trend setup
HOLD_MAX_DAYS = 3               # max hold for the option
OPTION_LEVERAGE = 50.0          # nifty 1% move -> option ~50% move (OTM 100pt 4dte)
THETA_DECAY_PER_DAY = 5.0       # premium % decay per calendar day
STOP_PREMIUM_PCT = -25.0        # close at -25%
TARGET_PREMIUM_PCT = 50.0       # close at +50%
PER_TRADE_RISK_RS = 8000        # capital per trade (matches yesterday's setup)


def detect_signals(df: pd.DataFrame) -> list[dict]:
    """Walk daily bars, emit reversal signals."""
    signals = []
    closes = df["Close"].values
    opens = df["Open"].values
    highs = df["High"].values
    lows = df["Low"].values
    dates = df["Date"].values

    for i in range(SETUP_LOOKBACK_DAYS, len(df) - 1):
        # Setup over lookback window
        prior_setup_pct = (closes[i] - closes[i - SETUP_LOOKBACK_DAYS]) / closes[i - SETUP_LOOKBACK_DAYS] * 100

        # Today's open vs prior close
        today_open = opens[i + 1]
        prior_close = closes[i]
        gap_pct = (today_open - prior_close) / prior_close * 100

        # BULLISH signal: prior trend down + today opens recovering
        if prior_setup_pct <= -SETUP_MIN_MOVE_PCT and gap_pct >= 0:
            signals.append({
                "date": pd.Timestamp(dates[i + 1]).date().isoformat(),
                "direction": "BULLISH",
                "setup_pct": prior_setup_pct,
                "open": today_open,
                "entry_idx": i + 1,
            })
        # BEARISH signal: prior trend up + today opens declining
        elif prior_setup_pct >= SETUP_MIN_MOVE_PCT and gap_pct <= 0:
            signals.append({
                "date": pd.Timestamp(dates[i + 1]).date().isoformat(),
                "direction": "BEARISH",
                "setup_pct": prior_setup_pct,
                "open": today_open,
                "entry_idx": i + 1,
            })
    return signals


def simulate_trade(df: pd.DataFrame, signal: dict) -> dict:
    """Simulate option P&L for a signal. Hold up to HOLD_MAX_DAYS, exit on
    stop/target/time."""
    direction = signal["direction"]
    entry_price = signal["open"]
    entry_idx = signal["entry_idx"]

    # Walk forward day by day, compute option premium move
    for hold_day in range(1, HOLD_MAX_DAYS + 1):
        idx = entry_idx + hold_day - 1
        if idx >= len(df):
            break
        bar_high = df.iloc[idx]["High"]
        bar_low = df.iloc[idx]["Low"]
        bar_close = df.iloc[idx]["Close"]

        # Compute today's option premium movement based on intraday extremes
        if direction == "BULLISH":
            best_underlying_pct = (bar_high - entry_price) / entry_price * 100
            worst_underlying_pct = (bar_low - entry_price) / entry_price * 100
        else:  # BEARISH
            best_underlying_pct = -(bar_low - entry_price) / entry_price * 100
            worst_underlying_pct = -(bar_high - entry_price) / entry_price * 100

        # Option premium moves (with theta decay)
        theta_drag = THETA_DECAY_PER_DAY * hold_day
        best_option_pct = best_underlying_pct * OPTION_LEVERAGE - theta_drag
        worst_option_pct = worst_underlying_pct * OPTION_LEVERAGE - theta_drag

        # Did stop hit during this bar?
        if worst_option_pct <= STOP_PREMIUM_PCT:
            return {**signal, "exit_pct": STOP_PREMIUM_PCT, "exit_reason": "STOP",
                    "hold_days": hold_day, "exit_date": pd.Timestamp(df.iloc[idx]["Date"]).date().isoformat()}
        # Did target hit?
        if best_option_pct >= TARGET_PREMIUM_PCT:
            return {**signal, "exit_pct": TARGET_PREMIUM_PCT, "exit_reason": "TARGET",
                    "hold_days": hold_day, "exit_date": pd.Timestamp(df.iloc[idx]["Date"]).date().isoformat()}

    # Time exit on last day held - close at theoretical premium
    if direction == "BULLISH":
        underlying_pct = (df.iloc[idx]["Close"] - entry_price) / entry_price * 100
    else:
        underlying_pct = -(df.iloc[idx]["Close"] - entry_price) / entry_price * 100
    final_option_pct = underlying_pct * OPTION_LEVERAGE - THETA_DECAY_PER_DAY * hold_day
    return {**signal, "exit_pct": round(final_option_pct, 2), "exit_reason": "TIME",
            "hold_days": hold_day, "exit_date": pd.Timestamp(df.iloc[idx]["Date"]).date().isoformat()}


def backtest(years: int = 1):
    """Run backtest over N years of NIFTY history."""
    df = get_bars("NIFTY", n_days=years * 252)
    if df.empty:
        print("No NIFTY data available")
        return
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"Backtest period: {df.iloc[0]['Date']} to {df.iloc[-1]['Date']} ({len(df)} bars)")

    signals = detect_signals(df)
    print(f"Signals detected: {len(signals)}")

    if not signals:
        return

    trades = [simulate_trade(df, s) for s in signals]
    wins = [t for t in trades if t["exit_pct"] > 0]
    losses = [t for t in trades if t["exit_pct"] <= 0]

    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = np.mean([t["exit_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["exit_pct"] for t in losses]) if losses else 0
    expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

    print(f"\n=== RESULTS ===")
    print(f"Total trades:     {len(trades)}")
    print(f"Wins:             {len(wins)}  ({win_rate:.1f}%)")
    print(f"Losses:           {len(losses)}")
    print(f"Avg win:          {avg_win:+.2f}% premium")
    print(f"Avg loss:         {avg_loss:+.2f}% premium")
    print(f"Expectancy/trade: {expectancy:+.2f}% premium")
    print(f"  -- in Rs (per Rs {PER_TRADE_RISK_RS} sized trade): Rs {expectancy / 100 * PER_TRADE_RISK_RS:+.0f}")

    # Equity curve simulation: Rs 10K start, compound winners
    capital = 10_000
    eq_curve = [capital]
    for t in trades:
        risk_size = min(capital * 0.85, PER_TRADE_RISK_RS)  # 85% of capital per trade
        if risk_size < 1000:
            continue  # bust
        pct = t["exit_pct"] / 100
        capital += risk_size * pct
        eq_curve.append(capital)

    peak = max(eq_curve)
    final = eq_curve[-1]
    max_dd = min(0, (min(eq_curve[eq_curve.index(peak):]) - peak) / peak * 100) if eq_curve else 0
    print(f"\n=== EQUITY CURVE ===")
    print(f"Start: Rs 10,000  End: Rs {final:,.0f}  ({(final - 10_000) / 100:+.0f}%)")
    print(f"Peak:  Rs {peak:,.0f}")
    print(f"Max drawdown: {max_dd:.1f}%")

    # Sample of recent trades
    print(f"\n=== LAST 8 TRADES ===")
    print(f"{'DATE':12s} {'DIR':8s} {'SETUP%':>7s} {'EXIT%':>8s} {'REASON':8s} {'HOLD':>5s}")
    for t in trades[-8:]:
        print(f"{t['date']:12s} {t['direction']:8s} {t['setup_pct']:>+7.2f} {t['exit_pct']:>+8.2f} {t['exit_reason']:8s} {t['hold_days']:>5d}")


if __name__ == "__main__":
    print("--- 1y backtest ---")
    backtest(years=1)
    print("\n--- 3y backtest ---")
    backtest(years=3)

"""
Proper intraday backtest of F&O autotrader signal.

Methodology (matches autotrader logic LITERALLY):
  - Track NIFTY 5-min bars rolling window (last 30 bars = 2.5 hours)
  - Detect BULLISH signal: NIFTY <= -0.5% intraday + within 0.3% of intraday low
                         + last 5 bars (25 min) net +0.05%+
  - Detect BEARISH signal: NIFTY >= +0.5% intraday + within 0.3% of intraday high
                         + last 5 bars net -0.05%+

For each signal:
  - Compute ATM/OTM strike (same logic as autotrader: spot + 100 OTM offset, round to 50)
  - Use Black-Scholes with calibrated IV 15.9% (validated against 30-Apr trade)
  - Walk forward 5-min bars checking premium stop/target
  - Stop: -25%, Target: +50%, Time exit: end of day or 4 hours max

Outputs: signal count, win rate, expectancy, equity curve, regime breakdown.

Honest about limitations:
  - 30 days of data (April 2026) — limited regime variety
  - IV held constant at 15.9% (real IV moves intraday)
  - Friction not modeled (would reduce wins ~3-5%)
  - Assumes fills at theoretical price (real has 0.5-1% slippage on options)
"""
from __future__ import annotations

import os
import sys
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from fno.black_scholes import bs_price
from data_fetcher import get_fetcher


# ---------- Backtest config ----------
ROLLING_WINDOW_BARS = 30          # ~2.5 hours
MOVE_THRESHOLD_PCT = 0.5          # min intraday move to consider reversal
NEAR_EXTREME_PCT = 0.3            # how close to high/low
RECOVERY_BARS = 5                 # last N bars for recovery confirmation
RECOVERY_MIN_PCT = 0.05           # min recovery move

OTM_OFFSET = 100                  # NIFTY OTM strike offset
STRIKE_STEP = 50

DEFAULT_DTE_DAYS = 4              # weekly option, midpoint of 3-7 day window
RISK_FREE = 0.065
NIFTY_IV = 0.159                  # calibrated from 30-Apr trade

STOP_PREMIUM_PCT = -25.0
TARGET_PREMIUM_PCT = +50.0
MAX_HOLD_BARS = 48                # 48 * 5min = 4 hours max

PER_TRADE_RISK_RS = 8000          # for equity curve simulation


def fetch_nifty_5min(days: int = 30) -> pd.DataFrame:
    f = get_fetcher()
    if not f.logged_in:
        f.login()
    df = f.get_historical_data("NIFTY", interval="FIVE_MINUTE", days=days)
    if df.empty:
        raise RuntimeError("No NIFTY intraday data fetched")
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["DateOnly"] = pd.to_datetime(df["Date"]).dt.date
    return df


def detect_signals(df: pd.DataFrame) -> list[dict]:
    """Walk through 5-min bars day by day, emit reversal signals.
    Each new day resets the intraday-extreme tracker."""
    signals = []
    for date, day_df in df.groupby("DateOnly"):
        day_df = day_df.reset_index(drop=True)
        if len(day_df) < ROLLING_WINDOW_BARS:
            continue
        prev_close = day_df.iloc[0]["Open"]  # approximate; actual would be prev day close
        # use prev day's last close if available
        idx_global = df[df["DateOnly"] == date].index[0]
        if idx_global > 0:
            prev_close = df.iloc[idx_global - 1]["Close"]

        intraday_high = day_df.iloc[0]["High"]
        intraday_low = day_df.iloc[0]["Low"]

        for i in range(ROLLING_WINDOW_BARS, len(day_df)):
            bar = day_df.iloc[i]
            spot = bar["Close"]
            intraday_high = max(intraday_high, bar["High"])
            intraday_low = min(intraday_low, bar["Low"])

            intraday_pct = (spot - prev_close) / prev_close * 100
            near_low = (spot - intraday_low) / spot * 100 < NEAR_EXTREME_PCT
            near_high = (intraday_high - spot) / spot * 100 < NEAR_EXTREME_PCT

            # Recovery in last 5 bars
            recovery_first = day_df.iloc[i - RECOVERY_BARS]["Close"]
            recovery_pct = (spot - recovery_first) / recovery_first * 100

            sig = None
            if (intraday_pct <= -MOVE_THRESHOLD_PCT and near_low
                    and recovery_pct >= RECOVERY_MIN_PCT):
                sig = "BULLISH"
            elif (intraday_pct >= MOVE_THRESHOLD_PCT and near_high
                    and recovery_pct <= -RECOVERY_MIN_PCT):
                sig = "BEARISH"

            if sig:
                signals.append({
                    "datetime": bar["Date"],
                    "date": str(date),
                    "direction": sig,
                    "spot": float(spot),
                    "prev_close": float(prev_close),
                    "intraday_pct": round(intraday_pct, 2),
                    "global_idx": idx_global + i,
                })
                # Cooldown: skip next 6 bars (30 min) after a signal to avoid duplicates
                pass  # Not using cooldown in detection; handled in trade simulation
    return signals


def simulate_trade(df: pd.DataFrame, signal: dict) -> dict:
    """Open option at signal bar, walk forward checking stop/target."""
    direction = signal["direction"]
    spot = signal["spot"]
    if direction == "BULLISH":
        strike = int((spot + OTM_OFFSET) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "CE"
    else:
        strike = int((spot - OTM_OFFSET) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "PE"

    # Entry premium
    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry = bs_price(spot, strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type)
    entry_premium = entry.premium
    if entry_premium <= 0:
        return {**signal, "exit_pct": 0.0, "exit_reason": "INVALID", "hold_bars": 0}

    stop_premium = entry_premium * (1 + STOP_PREMIUM_PCT / 100)
    target_premium = entry_premium * (1 + TARGET_PREMIUM_PCT / 100)

    entry_time = pd.Timestamp(signal["datetime"])
    start_idx = signal["global_idx"]

    for hold_bars in range(1, MAX_HOLD_BARS + 1):
        idx = start_idx + hold_bars
        if idx >= len(df):
            # End of data
            return {**signal, "exit_pct": 0.0, "exit_reason": "DATA_END", "hold_bars": hold_bars}
        bar = df.iloc[idx]
        bar_time = pd.Timestamp(bar["Date"])

        # End-of-day exit: don't hold past 15:25
        if bar_time.hour >= 15 and bar_time.minute >= 25:
            spot_now = bar["Close"]
            elapsed_days = (bar_time - entry_time).total_seconds() / 86400
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type)
            premium_pct = (curr.premium - entry_premium) / entry_premium * 100
            return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "EOD",
                    "hold_bars": hold_bars, "exit_spot": float(spot_now), "exit_premium": curr.premium}

        # Check stop/target on bar high/low
        elapsed_days = (bar_time - entry_time).total_seconds() / 86400
        dte_now = max(0.001, entry_dte - elapsed_days / 365)
        # Best premium in bar (use high for CE, low for PE)
        best_spot = bar["High"] if opt_type == "CE" else bar["Low"]
        worst_spot = bar["Low"] if opt_type == "CE" else bar["High"]
        best_premium = bs_price(best_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_premium = bs_price(worst_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium

        # Stop hit?
        if worst_premium <= stop_premium:
            return {**signal, "exit_pct": STOP_PREMIUM_PCT, "exit_reason": "STOP",
                    "hold_bars": hold_bars, "exit_spot": float(worst_spot)}
        # Target hit?
        if best_premium >= target_premium:
            return {**signal, "exit_pct": TARGET_PREMIUM_PCT, "exit_reason": "TARGET",
                    "hold_bars": hold_bars, "exit_spot": float(best_spot)}

    # Time exit at MAX_HOLD_BARS
    bar = df.iloc[start_idx + MAX_HOLD_BARS]
    spot_now = bar["Close"]
    bar_time = pd.Timestamp(bar["Date"])
    elapsed_days = (bar_time - entry_time).total_seconds() / 86400
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type)
    premium_pct = (curr.premium - entry_premium) / entry_premium * 100
    return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "TIME",
            "hold_bars": MAX_HOLD_BARS, "exit_spot": float(spot_now)}


def filter_cooldown(signals: list[dict], cooldown_bars: int = 12) -> list[dict]:
    """Remove signals that fire within `cooldown_bars` of the previous one
    (matches autotrader's behavior of not opening multiple positions back-to-back)."""
    if not signals:
        return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["global_idx"] - out[-1]["global_idx"] >= cooldown_bars:
            out.append(s)
    return out


def main():
    print("Fetching 30 days of 5-min NIFTY bars...")
    df = fetch_nifty_5min(days=30)
    print(f"Loaded {len(df)} bars from {df.iloc[0]['Date']} to {df.iloc[-1]['Date']}\n")

    print("Detecting signals (intraday autotrader logic)...")
    signals_raw = detect_signals(df)
    signals = filter_cooldown(signals_raw, cooldown_bars=12)  # 60 min cooldown
    print(f"Raw signals: {len(signals_raw)}, after cooldown: {len(signals)}\n")

    if not signals:
        print("No signals to test")
        return

    print("Simulating trades with Black-Scholes pricing...")
    trades = [simulate_trade(df, s) for s in signals]
    valid_trades = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]

    wins = [t for t in valid_trades if t["exit_pct"] > 0]
    losses = [t for t in valid_trades if t["exit_pct"] <= 0]

    win_rate = len(wins) / len(valid_trades) * 100 if valid_trades else 0
    avg_win = np.mean([t["exit_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["exit_pct"] for t in losses]) if losses else 0
    expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

    print(f"\n=== RESULTS ===")
    print(f"Total trades:     {len(valid_trades)}")
    print(f"Wins:             {len(wins)}  ({win_rate:.1f}%)")
    print(f"Losses:           {len(losses)}")
    print(f"Avg win:          {avg_win:+.2f}% premium")
    print(f"Avg loss:         {avg_loss:+.2f}% premium")
    print(f"Expectancy:       {expectancy:+.2f}% premium per trade")
    print(f"  in Rs:          Rs {expectancy / 100 * PER_TRADE_RISK_RS:+.0f} per trade")

    # Exit reason breakdown
    print(f"\nExit reasons:")
    for reason in ["TARGET", "STOP", "EOD", "TIME"]:
        ct = sum(1 for t in valid_trades if t["exit_reason"] == reason)
        avg = np.mean([t["exit_pct"] for t in valid_trades if t["exit_reason"] == reason]) if ct else 0
        print(f"  {reason:10s}: {ct:3d} trades, avg {avg:+.2f}%")

    # Direction breakdown
    bull_trades = [t for t in valid_trades if t["direction"] == "BULLISH"]
    bear_trades = [t for t in valid_trades if t["direction"] == "BEARISH"]
    print(f"\nBULLISH: {len(bull_trades)} trades, win rate {sum(1 for t in bull_trades if t['exit_pct']>0)/max(1,len(bull_trades))*100:.1f}%")
    print(f"BEARISH: {len(bear_trades)} trades, win rate {sum(1 for t in bear_trades if t['exit_pct']>0)/max(1,len(bear_trades))*100:.1f}%")

    # Equity curve
    capital = 10_000
    eq = [capital]
    for t in valid_trades:
        risk = min(capital * 0.85, PER_TRADE_RISK_RS)
        if risk < 1000:
            continue
        capital += risk * (t["exit_pct"] / 100)
        eq.append(capital)
    peak = max(eq)
    final = eq[-1]
    max_dd = 0
    running_peak = eq[0]
    for v in eq:
        if v > running_peak:
            running_peak = v
        dd = (v - running_peak) / running_peak * 100
        if dd < max_dd:
            max_dd = dd
    print(f"\n=== EQUITY CURVE (Rs 10K start, ~85% capital per trade) ===")
    print(f"Final:            Rs {final:,.0f}  ({(final - 10_000) / 100:+.0f}%)")
    print(f"Peak:             Rs {peak:,.0f}")
    print(f"Max drawdown:     {max_dd:.1f}%")

    # Last 10 trades
    print(f"\n=== LAST 10 TRADES ===")
    print(f"{'DATETIME':20s} {'DIR':8s} {'INTRA%':>7s} {'EXIT%':>8s} {'REASON':8s} {'BARS':>5s}")
    for t in valid_trades[-10:]:
        dt = pd.Timestamp(t['datetime']).strftime('%Y-%m-%d %H:%M')
        print(f"{dt:20s} {t['direction']:8s} {t['intraday_pct']:>+7.2f} {t['exit_pct']:>+8.2f} {t['exit_reason']:8s} {t['hold_bars']:>5d}")


if __name__ == "__main__":
    main()

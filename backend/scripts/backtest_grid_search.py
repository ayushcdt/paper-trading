"""
Grid search over autotrader strategy parameters using real intraday backtest.

Tests:
  - Move threshold: 0.5% / 0.7% / 1.0% / 1.5%
  - Recovery confirmation: 0.05% / 0.15% / 0.30%
  - Cooldown: 30 min / 60 min / 120 min
  - Direction: BOTH / BULLISH-only / BEARISH-only
  - Max hold: 24 bars (2hr) / 36 bars (3hr) / 48 bars (4hr)
  - OTM offset: 50 / 100 / 150 / 200
  - Stop / Target combos: (-25/+50), (-20/+60), (-30/+45)

Outputs ranked list of (params, expectancy, win_rate, equity_curve_final).
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

from fno.black_scholes import bs_price
from data_fetcher import get_fetcher


# ---------- Constants ----------
RISK_FREE = 0.065
NIFTY_IV = 0.159
DEFAULT_DTE_DAYS = 4
STRIKE_STEP = 50
PER_TRADE_RISK_RS = 8000


def fetch_data() -> pd.DataFrame:
    f = get_fetcher()
    if not f.logged_in:
        f.login()
    df = f.get_historical_data("NIFTY", interval="FIVE_MINUTE", days=30)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    df["DateOnly"] = pd.to_datetime(df["Date"]).dt.date
    return df


def detect_signals(df: pd.DataFrame, cfg: dict) -> list[dict]:
    rolling = cfg["rolling_window_bars"]
    move_thr = cfg["move_threshold_pct"]
    near_extreme = cfg["near_extreme_pct"]
    recovery_bars = cfg["recovery_bars"]
    recovery_min = cfg["recovery_min_pct"]
    direction_filter = cfg["direction_filter"]   # "BOTH", "BULLISH", "BEARISH"

    signals = []
    for date, day_df in df.groupby("DateOnly"):
        day_df = day_df.reset_index(drop=True)
        if len(day_df) < rolling:
            continue
        idx_global = df[df["DateOnly"] == date].index[0]
        prev_close = df.iloc[idx_global - 1]["Close"] if idx_global > 0 else day_df.iloc[0]["Open"]
        intraday_high = day_df.iloc[0]["High"]
        intraday_low = day_df.iloc[0]["Low"]

        for i in range(rolling, len(day_df)):
            bar = day_df.iloc[i]
            spot = bar["Close"]
            intraday_high = max(intraday_high, bar["High"])
            intraday_low = min(intraday_low, bar["Low"])
            intraday_pct = (spot - prev_close) / prev_close * 100
            near_low = (spot - intraday_low) / spot * 100 < near_extreme
            near_high = (intraday_high - spot) / spot * 100 < near_extreme
            recovery_first = day_df.iloc[i - recovery_bars]["Close"]
            recovery_pct = (spot - recovery_first) / recovery_first * 100

            sig = None
            if direction_filter in ("BOTH", "BULLISH"):
                if intraday_pct <= -move_thr and near_low and recovery_pct >= recovery_min:
                    sig = "BULLISH"
            if direction_filter in ("BOTH", "BEARISH") and sig is None:
                if intraday_pct >= move_thr and near_high and recovery_pct <= -recovery_min:
                    sig = "BEARISH"

            if sig:
                signals.append({
                    "datetime": bar["Date"], "date": str(date),
                    "direction": sig, "spot": float(spot),
                    "intraday_pct": round(intraday_pct, 2),
                    "global_idx": idx_global + i,
                })
    return signals


def filter_cooldown(signals: list[dict], cooldown_bars: int) -> list[dict]:
    if not signals:
        return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["global_idx"] - out[-1]["global_idx"] >= cooldown_bars:
            out.append(s)
    return out


def simulate_trade(df: pd.DataFrame, signal: dict, cfg: dict) -> dict:
    direction = signal["direction"]
    spot = signal["spot"]
    otm_offset = cfg["otm_offset"]
    stop_pct = cfg["stop_pct"]
    target_pct = cfg["target_pct"]
    max_hold_bars = cfg["max_hold_bars"]

    if direction == "BULLISH":
        strike = int((spot + otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "CE"
    else:
        strike = int((spot - otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "PE"

    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry = bs_price(spot, strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type)
    entry_premium = entry.premium
    if entry_premium <= 0.5:    # min Rs 0.50 premium
        return {**signal, "exit_pct": 0.0, "exit_reason": "INVALID", "hold_bars": 0}

    stop_premium = entry_premium * (1 + stop_pct / 100)
    target_premium = entry_premium * (1 + target_pct / 100)

    entry_time = pd.Timestamp(signal["datetime"])
    start_idx = signal["global_idx"]

    for hold_bars in range(1, max_hold_bars + 1):
        idx = start_idx + hold_bars
        if idx >= len(df):
            return {**signal, "exit_pct": 0.0, "exit_reason": "DATA_END", "hold_bars": hold_bars}
        bar = df.iloc[idx]
        bar_time = pd.Timestamp(bar["Date"])

        # EOD exit
        if bar_time.hour >= 15 and bar_time.minute >= 25:
            spot_now = bar["Close"]
            elapsed_days = (bar_time - entry_time).total_seconds() / 86400
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type)
            premium_pct = (curr.premium - entry_premium) / entry_premium * 100
            return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "EOD",
                    "hold_bars": hold_bars}

        elapsed_days = (bar_time - entry_time).total_seconds() / 86400
        dte_now = max(0.001, entry_dte - elapsed_days / 365)
        best_spot = bar["High"] if opt_type == "CE" else bar["Low"]
        worst_spot = bar["Low"] if opt_type == "CE" else bar["High"]
        best_premium = bs_price(best_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_premium = bs_price(worst_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium

        if worst_premium <= stop_premium:
            return {**signal, "exit_pct": stop_pct, "exit_reason": "STOP", "hold_bars": hold_bars}
        if best_premium >= target_premium:
            return {**signal, "exit_pct": target_pct, "exit_reason": "TARGET", "hold_bars": hold_bars}

    bar = df.iloc[start_idx + max_hold_bars]
    spot_now = bar["Close"]
    bar_time = pd.Timestamp(bar["Date"])
    elapsed_days = (bar_time - entry_time).total_seconds() / 86400
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type)
    premium_pct = (curr.premium - entry_premium) / entry_premium * 100
    return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "TIME",
            "hold_bars": max_hold_bars}


def run_backtest(df: pd.DataFrame, cfg: dict) -> dict:
    signals = filter_cooldown(detect_signals(df, cfg), cfg["cooldown_bars"])
    trades = [simulate_trade(df, s, cfg) for s in signals]
    valid = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]

    if not valid:
        return {"n_trades": 0, "win_rate": 0, "expectancy": 0, "final_equity": 10000, "max_dd": 0}

    wins = [t for t in valid if t["exit_pct"] > 0]
    losses = [t for t in valid if t["exit_pct"] <= 0]
    win_rate = len(wins) / len(valid) * 100
    avg_win = np.mean([t["exit_pct"] for t in wins]) if wins else 0
    avg_loss = np.mean([t["exit_pct"] for t in losses]) if losses else 0
    expectancy = (win_rate / 100) * avg_win + (1 - win_rate / 100) * avg_loss

    capital = 10_000
    eq = [capital]
    running_peak = capital
    max_dd = 0
    for t in valid:
        risk = min(capital * 0.85, PER_TRADE_RISK_RS)
        if risk < 1000:
            break
        capital += risk * (t["exit_pct"] / 100)
        eq.append(capital)
        if capital > running_peak:
            running_peak = capital
        dd = (capital - running_peak) / running_peak * 100
        if dd < max_dd:
            max_dd = dd

    return {
        "n_trades": len(valid),
        "win_rate": round(win_rate, 1),
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "expectancy": round(expectancy, 2),
        "final_equity": round(eq[-1] if eq else capital, 0),
        "max_dd": round(max_dd, 1),
    }


def main():
    print("Fetching 30-day intraday NIFTY...")
    df = fetch_data()
    print(f"Loaded {len(df)} 5-min bars\n")

    # Grid search across key params
    configs = []
    for move_thr, recovery_min, cooldown_min, direction, max_hold, otm, stop_target in product(
        [0.5, 0.8, 1.2],            # move threshold
        [0.05, 0.15, 0.30],         # recovery min
        [30, 60, 120],              # cooldown
        ["BOTH", "BULLISH"],        # direction (drop BEARISH-only since we know it underperforms)
        [18, 30, 48],               # max hold (90min, 2.5hr, 4hr)
        [50, 100, 150],             # OTM offset
        [(-25, 50), (-20, 60), (-30, 45)],  # stop, target combos
    ):
        configs.append({
            "rolling_window_bars": 30,
            "move_threshold_pct": move_thr,
            "near_extreme_pct": 0.3,
            "recovery_bars": 5,
            "recovery_min_pct": recovery_min,
            "cooldown_bars": cooldown_min // 5,
            "direction_filter": direction,
            "max_hold_bars": max_hold,
            "otm_offset": otm,
            "stop_pct": stop_target[0],
            "target_pct": stop_target[1],
        })

    print(f"Testing {len(configs)} parameter combinations...\n")
    results = []
    for i, cfg in enumerate(configs):
        if i % 50 == 0:
            print(f"  [{i}/{len(configs)}] tested...")
        r = run_backtest(df, cfg)
        results.append({**cfg, **r})

    # Sort by expectancy (most positive first)
    results.sort(key=lambda r: -r["expectancy"])

    print(f"\n=== TOP 10 BY EXPECTANCY ===")
    print(f"{'expect':>8s} {'WR%':>5s} {'#':>4s} {'final':>8s} {'DD%':>6s}  CFG")
    for r in results[:10]:
        cfg_str = (f"mv{r['move_threshold_pct']} "
                   f"rec{r['recovery_min_pct']} "
                   f"cd{r['cooldown_bars']*5}m "
                   f"{r['direction_filter']:8s} "
                   f"hold{r['max_hold_bars']*5}m "
                   f"otm{r['otm_offset']} "
                   f"S{r['stop_pct']}/T{r['target_pct']}")
        print(f"{r['expectancy']:>+7.2f}% {r['win_rate']:>5.1f} {r['n_trades']:>4d} {r['final_equity']:>8.0f} {r['max_dd']:>+6.1f}  {cfg_str}")

    # Filter to MEANINGFUL results: at least 8 trades for stat significance
    meaningful = [r for r in results if r["n_trades"] >= 8]
    print(f"\n=== TOP 10 (>=8 trades for significance) ===")
    print(f"{'expect':>8s} {'WR%':>5s} {'#':>4s} {'final':>8s} {'DD%':>6s}  CFG")
    for r in meaningful[:10]:
        cfg_str = (f"mv{r['move_threshold_pct']} "
                   f"rec{r['recovery_min_pct']} "
                   f"cd{r['cooldown_bars']*5}m "
                   f"{r['direction_filter']:8s} "
                   f"hold{r['max_hold_bars']*5}m "
                   f"otm{r['otm_offset']} "
                   f"S{r['stop_pct']}/T{r['target_pct']}")
        print(f"{r['expectancy']:>+7.2f}% {r['win_rate']:>5.1f} {r['n_trades']:>4d} {r['final_equity']:>8.0f} {r['max_dd']:>+6.1f}  {cfg_str}")


if __name__ == "__main__":
    main()

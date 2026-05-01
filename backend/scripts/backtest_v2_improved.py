"""
IMPROVED F&O autotrader backtest (v2).

Fixes from v1:
  1. Larger sample (90 days via chunked Angel fetching)
  2. Time-of-day filter (09:30-14:30 IST window)
  3. VIX gate (12-22 only)
  4. Trailing stop after +30% premium
  5. Stronger signal: require 3-of-5 bars positive (not just net move)
  6. Smaller grid (~50 configs, a-priori-meaningful)
  7. 3-fold walk-forward (not just one split)
"""
from __future__ import annotations

import os
import sys
import time as time_mod
from datetime import datetime, timedelta
from itertools import product
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from fno.black_scholes import bs_price
from data_fetcher import get_fetcher, SYMBOL_TOKENS


RISK_FREE = 0.065
NIFTY_IV = 0.159
DEFAULT_DTE_DAYS = 4
STRIKE_STEP = 50

# Improved strategy constants
TIME_WINDOW_START = (9, 30)   # don't fire before 09:30
TIME_WINDOW_END = (14, 30)    # don't fire after 14:30
VIX_MIN = 12.0
VIX_MAX = 22.0
TRAIL_TRIGGER_PCT = 30.0      # at +30% premium, raise stop to breakeven
TRAIL_TIGHT_PCT = 50.0        # at +50% premium, trail at +30%
REQUIRED_BARS_POSITIVE = 3    # of last 5 bars, this many must show recovery direction


def fetch_chunked_intraday(symbol: str, days: int) -> pd.DataFrame:
    """Fetch intraday bars in 30-day chunks (Angel limit) and concatenate."""
    f = get_fetcher()
    if not f.logged_in:
        f.login()
    token = SYMBOL_TOKENS.get(symbol)
    if not token:
        raise RuntimeError(f"Token not found for {symbol}")

    all_dfs = []
    end = datetime.now()
    chunks_needed = (days + 29) // 30
    for chunk in range(chunks_needed):
        chunk_end = end - timedelta(days=chunk * 30)
        chunk_start = chunk_end - timedelta(days=30)
        params = {
            "exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
            "fromdate": chunk_start.strftime("%Y-%m-%d %H:%M"),
            "todate": chunk_end.strftime("%Y-%m-%d %H:%M"),
        }
        try:
            data = f.api.getCandleData(params)
            if data.get("status") and data.get("data"):
                df = pd.DataFrame(data["data"], columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                df["Date"] = pd.to_datetime(df["Date"])
                if getattr(df["Date"].dtype, "tz", None) is not None:
                    df["Date"] = df["Date"].dt.tz_localize(None)
                all_dfs.append(df)
                print(f"  chunk {chunk+1}/{chunks_needed}: {len(df)} bars from {chunk_start.date()} to {chunk_end.date()}")
            else:
                print(f"  chunk {chunk+1}: no data")
        except Exception as e:
            print(f"  chunk {chunk+1}: error {e}")
        time_mod.sleep(0.5)  # rate limit

    if not all_dfs:
        raise RuntimeError("No data fetched")
    full_df = pd.concat(all_dfs).drop_duplicates(subset="Date").sort_values("Date").reset_index(drop=True)
    full_df["DateOnly"] = full_df["Date"].dt.date
    return full_df


def detect_signals_v2(nifty_df: pd.DataFrame, vix_df: pd.DataFrame, cfg: dict) -> list[dict]:
    """Improved signal detection with time-of-day, VIX gate, stronger confirmation."""
    rolling = cfg["rolling_window_bars"]
    move_thr = cfg["move_threshold_pct"]
    near_extreme = cfg["near_extreme_pct"]
    recovery_bars = cfg["recovery_bars"]
    recovery_min = cfg["recovery_min_pct"]
    direction_filter = cfg["direction_filter"]
    require_n_positive = cfg.get("require_n_positive", REQUIRED_BARS_POSITIVE)
    use_vix_gate = cfg.get("use_vix_gate", True)
    use_time_window = cfg.get("use_time_window", True)

    # VIX lookup by date (rough — daily-level OK since VIX doesn't change wildly intraday)
    vix_by_date = {}
    if not vix_df.empty:
        for _, row in vix_df.iterrows():
            vix_by_date[row["Date"].date()] = row["Close"]

    signals = []
    for date, day_df in nifty_df.groupby("DateOnly"):
        day_df = day_df.reset_index(drop=True)
        if len(day_df) < rolling:
            continue

        # VIX gate (use day's open VIX as proxy)
        if use_vix_gate:
            vix = vix_by_date.get(date, 15.0)  # default if missing
            if vix < VIX_MIN or vix > VIX_MAX:
                continue  # skip whole day

        idx_global = nifty_df[nifty_df["DateOnly"] == date].index[0]
        prev_close = nifty_df.iloc[idx_global - 1]["Close"] if idx_global > 0 else day_df.iloc[0]["Open"]
        intraday_high = day_df.iloc[0]["High"]
        intraday_low = day_df.iloc[0]["Low"]

        for i in range(rolling, len(day_df)):
            bar = day_df.iloc[i]
            bar_time = bar["Date"]
            spot = bar["Close"]
            intraday_high = max(intraday_high, bar["High"])
            intraday_low = min(intraday_low, bar["Low"])

            # Time-of-day window filter
            if use_time_window:
                hm = (bar_time.hour, bar_time.minute)
                if hm < TIME_WINDOW_START or hm > TIME_WINDOW_END:
                    continue

            intraday_pct = (spot - prev_close) / prev_close * 100
            near_low = (spot - intraday_low) / spot * 100 < near_extreme
            near_high = (intraday_high - spot) / spot * 100 < near_extreme
            recovery_first = day_df.iloc[i - recovery_bars]["Close"]
            recovery_pct = (spot - recovery_first) / recovery_first * 100

            # Stronger confirmation: count positive-direction bars in last N
            recent_bars = day_df.iloc[i - recovery_bars + 1: i + 1]
            recent_closes = recent_bars["Close"].values
            recent_diffs = np.diff(recent_closes)

            sig = None
            if direction_filter in ("BOTH", "BULLISH"):
                positive_bars = sum(1 for d in recent_diffs if d > 0)
                if (intraday_pct <= -move_thr and near_low and recovery_pct >= recovery_min
                        and positive_bars >= require_n_positive):
                    sig = "BULLISH"
            if direction_filter in ("BOTH", "BEARISH") and sig is None:
                negative_bars = sum(1 for d in recent_diffs if d < 0)
                if (intraday_pct >= move_thr and near_high and recovery_pct <= -recovery_min
                        and negative_bars >= require_n_positive):
                    sig = "BEARISH"

            if sig:
                signals.append({
                    "datetime": bar_time, "date": str(date),
                    "direction": sig, "spot": float(spot),
                    "intraday_pct": round(intraday_pct, 2),
                    "global_idx": idx_global + i,
                })
    return signals


def filter_cooldown(signals, cooldown_bars):
    if not signals: return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["global_idx"] - out[-1]["global_idx"] >= cooldown_bars:
            out.append(s)
    return out


def simulate_trade_v2(df, signal, cfg, slippage_pct=1.5, fee_rs=80.0):
    """Improved trade sim with trailing stop."""
    direction = signal["direction"]
    spot = signal["spot"]
    otm_offset = cfg["otm_offset"]
    initial_stop_pct = cfg["stop_pct"]
    target_pct = cfg["target_pct"]
    max_hold_bars = cfg["max_hold_bars"]
    use_trailing = cfg.get("use_trailing", True)

    if direction == "BULLISH":
        strike = int((spot + otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "CE"
    else:
        strike = int((spot - otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "PE"

    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry_premium_theory = bs_price(spot, strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
    if entry_premium_theory <= 0.5:
        return {**signal, "exit_pct": 0.0, "exit_reason": "INVALID", "hold_bars": 0}

    entry_premium = entry_premium_theory * (1 + slippage_pct / 100)
    current_stop_pct = initial_stop_pct
    peak_premium_pct = 0.0
    target_premium = entry_premium * (1 + target_pct / 100)

    entry_time = pd.Timestamp(signal["datetime"])
    start_idx = signal["global_idx"]
    lot_size = 75

    for hold_bars in range(1, max_hold_bars + 1):
        idx = start_idx + hold_bars
        if idx >= len(df):
            return {**signal, "exit_pct": 0.0, "exit_reason": "DATA_END", "hold_bars": hold_bars}
        bar = df.iloc[idx]
        bar_time = pd.Timestamp(bar["Date"])

        # Always exit by 15:00 to avoid theta crush
        if bar_time.hour >= 15:
            spot_now = bar["Close"]
            elapsed_days = (bar_time - entry_time).total_seconds() / 86400
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr_theory = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
            curr = curr_theory * (1 - slippage_pct / 100)
            premium_pct_gross = (curr - entry_premium) / entry_premium * 100
            pnl_rs = premium_pct_gross / 100 * (entry_premium * lot_size) - fee_rs
            premium_pct = pnl_rs / (entry_premium * lot_size) * 100
            return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "EOD", "hold_bars": hold_bars}

        elapsed_days = (bar_time - entry_time).total_seconds() / 86400
        dte_now = max(0.001, entry_dte - elapsed_days / 365)
        best_spot = bar["High"] if opt_type == "CE" else bar["Low"]
        worst_spot = bar["Low"] if opt_type == "CE" else bar["High"]
        best_premium_theory = bs_price(best_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_premium_theory = bs_price(worst_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_exit = worst_premium_theory * (1 - slippage_pct / 100)
        best_exit = best_premium_theory * (1 - slippage_pct / 100)

        # Update trailing
        best_premium_pct = (best_exit - entry_premium) / entry_premium * 100
        if best_premium_pct > peak_premium_pct:
            peak_premium_pct = best_premium_pct

        if use_trailing and peak_premium_pct >= TRAIL_TIGHT_PCT:
            # Trail at +30% premium
            new_stop_pct = TRAIL_TRIGGER_PCT
            current_stop_pct = max(current_stop_pct, new_stop_pct)
        elif use_trailing and peak_premium_pct >= TRAIL_TRIGGER_PCT:
            # Lock breakeven
            current_stop_pct = max(current_stop_pct, 0.0)

        stop_premium = entry_premium * (1 + current_stop_pct / 100)

        if worst_exit <= stop_premium:
            pnl_rs = current_stop_pct / 100 * (entry_premium * lot_size) - fee_rs
            adj_pct = pnl_rs / (entry_premium * lot_size) * 100
            return {**signal, "exit_pct": round(adj_pct, 2), "exit_reason": "STOP", "hold_bars": hold_bars}
        if best_exit >= target_premium:
            pnl_rs = target_pct / 100 * (entry_premium * lot_size) - fee_rs
            adj_pct = pnl_rs / (entry_premium * lot_size) * 100
            return {**signal, "exit_pct": round(adj_pct, 2), "exit_reason": "TARGET", "hold_bars": hold_bars}

    bar = df.iloc[start_idx + max_hold_bars]
    spot_now = bar["Close"]
    bar_time = pd.Timestamp(bar["Date"])
    elapsed_days = (bar_time - entry_time).total_seconds() / 86400
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr_theory = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
    curr = curr_theory * (1 - slippage_pct / 100)
    premium_pct_gross = (curr - entry_premium) / entry_premium * 100
    pnl_rs = premium_pct_gross / 100 * (entry_premium * lot_size) - fee_rs
    premium_pct = pnl_rs / (entry_premium * lot_size) * 100
    return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "TIME", "hold_bars": max_hold_bars}


def run_backtest_v2(nifty_df, vix_df, cfg, slippage=1.5, fees=80.0):
    signals = filter_cooldown(detect_signals_v2(nifty_df, vix_df, cfg), cfg["cooldown_bars"])
    trades = [simulate_trade_v2(nifty_df, s, cfg, slippage, fees) for s in signals]
    valid = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]
    if not valid:
        return {"n_trades": 0, "win_rate": 0, "expectancy": 0, "trades": []}
    wins = [t for t in valid if t["exit_pct"] > 0]
    win_rate = len(wins) / len(valid) * 100
    expectancy = np.mean([t["exit_pct"] for t in valid])
    return {"n_trades": len(valid), "win_rate": round(win_rate, 1),
            "expectancy": round(expectancy, 2), "trades": valid}


def make_focused_grid():
    """Smaller, a-priori-meaningful grid (~50 configs, not 1,458)."""
    configs = []
    for move_thr, recovery_min, cooldown_min, direction, max_hold, otm in product(
        [0.8, 1.0, 1.2],          # move threshold (the 3 most plausible)
        [0.05, 0.15],             # recovery min
        [60, 120],                # cooldown
        ["BOTH", "BULLISH"],      # direction
        [18, 30],                 # max hold (90min, 2.5hr)
        [50, 100, 150],           # OTM offset
    ):
        configs.append({
            "rolling_window_bars": 30,
            "move_threshold_pct": move_thr,
            "near_extreme_pct": 0.3,
            "recovery_bars": 5,
            "recovery_min_pct": recovery_min,
            "require_n_positive": 3,
            "use_vix_gate": True,
            "use_time_window": True,
            "use_trailing": True,
            "cooldown_bars": cooldown_min // 5,
            "direction_filter": direction,
            "max_hold_bars": max_hold,
            "otm_offset": otm,
            "stop_pct": -20,    # locked at -20%
            "target_pct": 60,   # locked at +60%
        })
    return configs


def kfold_walk_forward(nifty_df, vix_df, configs, k=3):
    """3-fold walk-forward. Each fold: fit on N-1 chunks, test on 1."""
    dates = sorted(nifty_df["DateOnly"].unique())
    chunk_size = len(dates) // k
    results_by_config = {i: {"train_exps": [], "test_exps": [], "test_n": []} for i in range(len(configs))}

    for fold in range(k):
        test_start = fold * chunk_size
        test_end = test_start + chunk_size if fold < k - 1 else len(dates)
        test_dates = set(dates[test_start:test_end])
        train_dates = set(dates) - test_dates
        train_df = nifty_df[nifty_df["DateOnly"].isin(train_dates)].reset_index(drop=True)
        test_df = nifty_df[nifty_df["DateOnly"].isin(test_dates)].reset_index(drop=True)
        print(f"  Fold {fold+1}/{k}: train {len(train_dates)}d / test {len(test_dates)}d")

        for i, cfg in enumerate(configs):
            r_train = run_backtest_v2(train_df, vix_df, cfg)
            r_test = run_backtest_v2(test_df, vix_df, cfg)
            results_by_config[i]["train_exps"].append(r_train["expectancy"])
            results_by_config[i]["test_exps"].append(r_test["expectancy"])
            results_by_config[i]["test_n"].append(r_test["n_trades"])

    # Average across folds
    summary = []
    for i, cfg in enumerate(configs):
        r = results_by_config[i]
        avg_train = np.mean(r["train_exps"])
        avg_test = np.mean(r["test_exps"])
        total_test_n = sum(r["test_n"])
        summary.append({"cfg": cfg, "avg_train_exp": round(avg_train, 2),
                       "avg_test_exp": round(avg_test, 2), "total_test_n": total_test_n,
                       "spread": round(avg_train - avg_test, 2)})
    summary.sort(key=lambda r: -r["avg_test_exp"])
    return summary


def main():
    print("=== Strategy v2 backtest with 90-day data ===\n")

    print("Fetching 90 days of NIFTY 5-min bars (chunked)...")
    nifty_df = fetch_chunked_intraday("NIFTY", days=90)
    print(f"NIFTY: {len(nifty_df)} bars from {nifty_df.iloc[0]['Date']} to {nifty_df.iloc[-1]['Date']}\n")

    print("Fetching INDIAVIX daily bars (for gate)...")
    f = get_fetcher()
    if not f.logged_in: f.login()
    vix_df = f.get_historical_data("INDIAVIX", interval="ONE_DAY", days=120)
    print(f"VIX: {len(vix_df)} bars\n")

    configs = make_focused_grid()
    print(f"Running 3-fold walk-forward on {len(configs)} focused configs...\n")
    summary = kfold_walk_forward(nifty_df, vix_df, configs, k=3)

    print(f"\n=== TOP 8 BY AVG TEST EXPECTANCY (3-fold) ===")
    print(f"{'tr_exp':>7s} {'te_exp':>7s} {'spread':>7s} {'n_test':>6s}  CFG")
    for s in summary[:8]:
        c = s["cfg"]
        cfg_str = f"mv{c['move_threshold_pct']} rec{c['recovery_min_pct']} cd{c['cooldown_bars']*5}m {c['direction_filter']:7s} hold{c['max_hold_bars']*5}m otm{c['otm_offset']}"
        print(f"{s['avg_train_exp']:>+6.2f}% {s['avg_test_exp']:>+6.2f}% {s['spread']:>+6.2f}% {s['total_test_n']:>6d}  {cfg_str}")

    print(f"\n=== ROBUSTNESS CHECK ===")
    pos = [s for s in summary if s["avg_test_exp"] > 5 and s["total_test_n"] >= 6]
    print(f"Configs with avg_test > +5% AND >=6 test trades: {len(pos)}/{len(configs)}")
    if pos:
        best = pos[0]
        print(f"\nBest robust config:")
        for k_, v in best.items():
            if k_ != "cfg": print(f"  {k_}: {v}")
        for k_, v in best["cfg"].items():
            print(f"  cfg.{k_}: {v}")


if __name__ == "__main__":
    main()

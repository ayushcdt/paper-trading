"""
Validation tests A, C, E for the F&O autotrader strategy.

Test A: Walk-forward / OOS split
  - Fit grid search on days 1-15
  - Take top-5 configs by expectancy on training
  - Apply unchanged to days 16-30 (test)
  - If avg test expectancy < +10%, strategy is overfit

Test C: Friction-adjusted backtest
  - Add 1.5% slippage on entry AND exit
  - Subtract Rs 80 fees per round-trip
  - Re-run best config; check if edge survives

Test E: Bootstrap CI on 8-trade sample
  - Resample 1000x with replacement
  - Compute expectancy + 5/50/95 percentiles
  - If 5th percentile < 0, statistically unjustified
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
    direction_filter = cfg["direction_filter"]

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


def filter_cooldown(signals, cooldown_bars):
    if not signals: return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["global_idx"] - out[-1]["global_idx"] >= cooldown_bars:
            out.append(s)
    return out


def simulate_trade(df, signal, cfg, slippage_pct=0.0, fee_rs=0.0):
    """Run option trade. slippage_pct + fee_rs apply friction in reality-mode."""
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
    entry_premium_theory = bs_price(spot, strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
    if entry_premium_theory <= 0.5:
        return {**signal, "exit_pct": 0.0, "exit_reason": "INVALID", "hold_bars": 0}

    # Apply slippage on entry: buy higher than theoretical
    entry_premium = entry_premium_theory * (1 + slippage_pct / 100)

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

        if bar_time.hour >= 15 and bar_time.minute >= 25:
            spot_now = bar["Close"]
            elapsed_days = (bar_time - entry_time).total_seconds() / 86400
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr_theory = bs_price(spot_now, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
            curr = curr_theory * (1 - slippage_pct / 100)  # sell lower than theory
            premium_pct_gross = (curr - entry_premium) / entry_premium * 100
            # Subtract fee from rupee P&L; convert back to %
            lot_size = 75
            pnl_rs = premium_pct_gross / 100 * (entry_premium * lot_size) - fee_rs
            premium_pct = pnl_rs / (entry_premium * lot_size) * 100
            return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "EOD", "hold_bars": hold_bars}

        elapsed_days = (bar_time - entry_time).total_seconds() / 86400
        dte_now = max(0.001, entry_dte - elapsed_days / 365)
        best_spot = bar["High"] if opt_type == "CE" else bar["Low"]
        worst_spot = bar["Low"] if opt_type == "CE" else bar["High"]
        best_premium_theory = bs_price(best_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_premium_theory = bs_price(worst_spot, strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium

        # Slippage applies to the executed exit price
        worst_exit = worst_premium_theory * (1 - slippage_pct / 100)
        best_exit = best_premium_theory * (1 - slippage_pct / 100)

        if worst_exit <= stop_premium:
            lot_size = 75
            pnl_rs = stop_pct / 100 * (entry_premium * lot_size) - fee_rs
            adj_pct = pnl_rs / (entry_premium * lot_size) * 100
            return {**signal, "exit_pct": round(adj_pct, 2), "exit_reason": "STOP", "hold_bars": hold_bars}
        if best_exit >= target_premium:
            lot_size = 75
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
    lot_size = 75
    pnl_rs = premium_pct_gross / 100 * (entry_premium * lot_size) - fee_rs
    premium_pct = pnl_rs / (entry_premium * lot_size) * 100
    return {**signal, "exit_pct": round(premium_pct, 2), "exit_reason": "TIME", "hold_bars": max_hold_bars}


def run_backtest(df, cfg, slippage_pct=0.0, fee_rs=0.0):
    signals = filter_cooldown(detect_signals(df, cfg), cfg["cooldown_bars"])
    trades = [simulate_trade(df, s, cfg, slippage_pct, fee_rs) for s in signals]
    valid = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]
    if not valid:
        return {"n_trades": 0, "win_rate": 0, "expectancy": 0, "trades": []}
    wins = [t for t in valid if t["exit_pct"] > 0]
    losses = [t for t in valid if t["exit_pct"] <= 0]
    win_rate = len(wins) / len(valid) * 100
    expectancy = np.mean([t["exit_pct"] for t in valid])
    return {"n_trades": len(valid), "win_rate": round(win_rate, 1),
            "expectancy": round(expectancy, 2), "trades": valid}


def make_configs():
    """Same grid as full search."""
    configs = []
    for move_thr, recovery_min, cooldown_min, direction, max_hold, otm, stop_target in product(
        [0.5, 0.8, 1.2],
        [0.05, 0.15, 0.30],
        [30, 60, 120],
        ["BOTH", "BULLISH"],
        [18, 30, 48],
        [50, 100, 150],
        [(-25, 50), (-20, 60), (-30, 45)],
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
    return configs


# =====================================================================
# TEST A: Walk-forward / OOS
# =====================================================================
def test_a_walk_forward(df: pd.DataFrame):
    print("\n" + "="*70)
    print("TEST A: Walk-forward (fit days 1-15, test days 16-30)")
    print("="*70)

    dates = sorted(df["DateOnly"].unique())
    if len(dates) < 20:
        print(f"Not enough days ({len(dates)}) for split")
        return
    split_idx = len(dates) // 2
    train_dates = dates[:split_idx]
    test_dates = dates[split_idx:]
    print(f"Train: {train_dates[0]} to {train_dates[-1]} ({len(train_dates)} days)")
    print(f"Test:  {test_dates[0]} to {test_dates[-1]} ({len(test_dates)} days)")

    train_df = df[df["DateOnly"].isin(train_dates)].reset_index(drop=True)
    test_df = df[df["DateOnly"].isin(test_dates)].reset_index(drop=True)

    configs = make_configs()
    print(f"Grid-searching {len(configs)} configs on TRAIN...")
    train_results = []
    for cfg in configs:
        r = run_backtest(train_df, cfg)
        train_results.append({"cfg": cfg, "train_exp": r["expectancy"],
                             "train_wr": r["win_rate"], "train_n": r["n_trades"]})
    # Top 5 by train expectancy (with min 4 trades to avoid trivial)
    train_results.sort(key=lambda r: -r["train_exp"])
    top_5 = [r for r in train_results if r["train_n"] >= 4][:5]

    print(f"\nTop 5 configs on TRAIN (min 4 trades):")
    print(f"{'tr_exp':>7s} {'tr_WR':>6s} {'tr_n':>5s}  CFG")
    for tr in top_5:
        c = tr["cfg"]
        cfg_str = f"mv{c['move_threshold_pct']} rec{c['recovery_min_pct']} cd{c['cooldown_bars']*5} {c['direction_filter']} hold{c['max_hold_bars']*5} otm{c['otm_offset']} S{c['stop_pct']}/T{c['target_pct']}"
        print(f"{tr['train_exp']:>+6.2f}% {tr['train_wr']:>5.1f}% {tr['train_n']:>5d}  {cfg_str}")

    # Apply each unchanged to test set
    print(f"\nApplying same 5 configs to TEST set:")
    print(f"{'tr_exp':>7s} {'te_exp':>7s} {'te_WR':>6s} {'te_n':>5s}  delta")
    test_exps = []
    for tr in top_5:
        r_test = run_backtest(test_df, tr["cfg"])
        test_exps.append(r_test["expectancy"])
        delta = r_test["expectancy"] - tr["train_exp"]
        print(f"{tr['train_exp']:>+6.2f}% {r_test['expectancy']:>+6.2f}% {r_test['win_rate']:>5.1f}% {r_test['n_trades']:>5d}  {delta:>+6.2f}%")

    avg_test = np.mean(test_exps) if test_exps else 0
    print(f"\nAvg test expectancy: {avg_test:+.2f}%")
    if avg_test < 10:
        print("VERDICT: STRATEGY IS OVERFIT (avg test < +10%)")
    else:
        print("VERDICT: Strategy survives OOS test")


# =====================================================================
# TEST C: Friction
# =====================================================================
def test_c_friction(df: pd.DataFrame):
    print("\n" + "="*70)
    print("TEST C: Friction-adjusted (1.5% slippage + Rs 80 fees per trade)")
    print("="*70)

    # Best config from full grid: mv1.2 rec0.05 cd120m BOTH hold150m otm100 S-20/T60
    best_cfg = {
        "rolling_window_bars": 30, "move_threshold_pct": 1.2, "near_extreme_pct": 0.3,
        "recovery_bars": 5, "recovery_min_pct": 0.05,
        "cooldown_bars": 120 // 5,
        "direction_filter": "BOTH",
        "max_hold_bars": 30, "otm_offset": 100,
        "stop_pct": -20, "target_pct": 60,
    }

    print("Without friction:")
    r0 = run_backtest(df, best_cfg, slippage_pct=0.0, fee_rs=0.0)
    print(f"  trades={r0['n_trades']}  WR={r0['win_rate']}%  expect={r0['expectancy']:+.2f}%")

    print("\nWith 1.5% slippage only (no fees):")
    r1 = run_backtest(df, best_cfg, slippage_pct=1.5, fee_rs=0.0)
    print(f"  trades={r1['n_trades']}  WR={r1['win_rate']}%  expect={r1['expectancy']:+.2f}%")

    print("\nWith Rs 80 fees only (no slippage):")
    r2 = run_backtest(df, best_cfg, slippage_pct=0.0, fee_rs=80.0)
    print(f"  trades={r2['n_trades']}  WR={r2['win_rate']}%  expect={r2['expectancy']:+.2f}%")

    print("\nWith BOTH slippage + fees (realistic):")
    r3 = run_backtest(df, best_cfg, slippage_pct=1.5, fee_rs=80.0)
    print(f"  trades={r3['n_trades']}  WR={r3['win_rate']}%  expect={r3['expectancy']:+.2f}%")

    print()
    if r3["expectancy"] < 5:
        print("VERDICT: Edge does NOT survive realistic friction")
    elif r3["expectancy"] < 10:
        print("VERDICT: Edge marginal after friction; risky to deploy")
    else:
        print("VERDICT: Edge survives friction with margin")
    return r0, r3


# =====================================================================
# TEST E: Bootstrap CI
# =====================================================================
def test_e_bootstrap(trades_no_friction, trades_with_friction):
    print("\n" + "="*70)
    print("TEST E: Bootstrap CI (resample trades 1000x with replacement)")
    print("="*70)

    for label, trades in [("No friction", trades_no_friction["trades"]),
                          ("With friction", trades_with_friction["trades"])]:
        if not trades:
            continue
        exits = np.array([t["exit_pct"] for t in trades])
        n = len(exits)
        boot_exps = []
        np.random.seed(42)
        for _ in range(1000):
            sample = np.random.choice(exits, size=n, replace=True)
            boot_exps.append(np.mean(sample))
        boot_exps = np.array(boot_exps)
        p5 = np.percentile(boot_exps, 5)
        p50 = np.percentile(boot_exps, 50)
        p95 = np.percentile(boot_exps, 95)
        print(f"\n{label}  (n={n} trades):")
        print(f"  5th percentile:  {p5:+.2f}%  (worst-case scenario)")
        print(f"  50th percentile: {p50:+.2f}%  (median)")
        print(f"  95th percentile: {p95:+.2f}%  (best-case scenario)")
        if p5 < 0:
            print(f"  -> 5th percentile NEGATIVE -- statistically unjustified to deploy")
        else:
            print(f"  -> 5th percentile POSITIVE -- CI bounded above zero")


def main():
    print("Fetching 30 days of 5-min NIFTY...")
    df = fetch_data()
    print(f"Loaded {len(df)} bars\n")

    # Test A
    test_a_walk_forward(df)

    # Test C - returns trades for Test E
    r_no_friction, r_with_friction = test_c_friction(df)

    # Test E
    test_e_bootstrap(r_no_friction, r_with_friction)


if __name__ == "__main__":
    main()

"""
Walk-forward validation of the mean-reversion options strategy.

The +19.9% compound finding from backtest_mean_reversion.py was on full year
of data with one config (1.0% threshold + 1-3 day hold). Could be:
  - Real edge (consistent across time)
  - Overfit to specific 12-month regime
  - Lucky concentration of trades in a window

3-fold walk-forward: split year into 3 chunks of ~4 months each. Train on 2/3,
test on 1/3, rotate. If test expectancy > 0 in all 3 folds, we have edge.
If only 1 of 3 positive, it's regime-dependent or overfit.
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


RISK_FREE = 0.065
NIFTY_IV = 0.159
DEFAULT_DTE_DAYS = 4
STRIKE_STEP = 50
OPT_SLIPPAGE_PCT = 1.5
OPT_FEE_RS = 80.0
LOT_SIZE = 75


def detect_extreme_days(df, threshold_pct):
    df = df.copy().sort_values("Date").reset_index(drop=True)
    signals = []
    for i in range(1, len(df) - 5):
        prev = df.iloc[i - 1]["Close"]
        today = df.iloc[i]["Close"]
        move = (today - prev) / prev * 100
        if abs(move) >= threshold_pct:
            direction = "BEARISH" if move > 0 else "BULLISH"
            signals.append({
                "entry_idx": i + 1,
                "entry_date": pd.Timestamp(df.iloc[i + 1]["Date"]),
                "direction": direction,
                "trigger_pct": round(move, 2),
            })
    return signals


def simulate_option_trade(df, signal, hold_days, stop_pct=-25, target_pct=50):
    entry_idx = signal["entry_idx"]
    if entry_idx + hold_days >= len(df):
        return None
    entry_spot = df.iloc[entry_idx]["Open"]
    direction = signal["direction"]
    opt_type = "CE" if direction == "BULLISH" else "PE"
    atm_strike = round(entry_spot / STRIKE_STEP) * STRIKE_STEP
    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry_theory = bs_price(entry_spot, atm_strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
    if entry_theory <= 1.0:
        return None
    entry_premium = entry_theory * (1 + OPT_SLIPPAGE_PCT / 100)

    for hold in range(1, hold_days + 1):
        idx = entry_idx + hold
        if idx >= len(df):
            return None
        bar = df.iloc[idx]
        for ohlc_field in ["High", "Low"]:
            spot = bar[ohlc_field]
            elapsed_days = hold
            dte_now = max(0.001, entry_dte - elapsed_days / 365)
            curr_theory = bs_price(spot, atm_strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
            curr = curr_theory * (1 - OPT_SLIPPAGE_PCT / 100)
            ret_pct = (curr - entry_premium) / entry_premium * 100
            if ret_pct <= stop_pct:
                pnl_rs = stop_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
                adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
                return {**signal, "premium_pct": round(adj_pct, 2),
                        "exit_reason": "STOP", "hold_days": hold}
            if ret_pct >= target_pct:
                pnl_rs = target_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
                adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
                return {**signal, "premium_pct": round(adj_pct, 2),
                        "exit_reason": "TARGET", "hold_days": hold}

    bar = df.iloc[entry_idx + hold_days]
    spot = bar["Close"]
    elapsed_days = hold_days
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr_theory = bs_price(spot, atm_strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
    curr = curr_theory * (1 - OPT_SLIPPAGE_PCT / 100)
    gross_pct = (curr - entry_premium) / entry_premium * 100
    pnl_rs = gross_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
    adj_pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
    return {**signal, "premium_pct": round(adj_pct, 2),
            "exit_reason": "TIME", "hold_days": hold_days}


def stats(trades):
    valid = [t for t in trades if t]
    if not valid:
        return {"n": 0, "wr": 0, "exp": 0, "compound": 0}
    wins = [t for t in valid if t["premium_pct"] > 0]
    wr = len(wins) / len(valid) * 100
    exp = np.mean([t["premium_pct"] for t in valid])
    cum = 1.0
    for t in valid:
        cum *= (1 + t["premium_pct"] / 100 * 0.6)
    return {"n": len(valid), "wr": round(wr, 1), "exp": round(exp, 2),
            "compound": round((cum - 1) * 100, 1)}


def walk_forward(df, threshold_pct=1.0, hold_days=2, k=3):
    """K-fold walk-forward. Split chronologically into k chunks."""
    n = len(df)
    chunk = n // k
    results = []
    for fold in range(k):
        test_start = fold * chunk
        test_end = test_start + chunk if fold < k - 1 else n
        test_df = df.iloc[test_start:test_end].reset_index(drop=True)
        train_df = pd.concat([df.iloc[:test_start], df.iloc[test_end:]]).reset_index(drop=True)

        # NOTE: For mean-reversion, we don't actually FIT params on train —
        # parameters are pre-specified. Walk-forward here just tests OOS
        # consistency of the SAME strategy across different time periods.
        train_signals = detect_extreme_days(train_df, threshold_pct)
        train_trades = [simulate_option_trade(train_df, s, hold_days) for s in train_signals]
        train_stats = stats(train_trades)

        test_signals = detect_extreme_days(test_df, threshold_pct)
        test_trades = [simulate_option_trade(test_df, s, hold_days) for s in test_signals]
        test_stats = stats(test_trades)

        train_period = f"{train_df.iloc[0]['Date']} to {train_df.iloc[-1]['Date']}"
        test_period = f"{test_df.iloc[0]['Date']} to {test_df.iloc[-1]['Date']}"
        results.append({
            "fold": fold + 1,
            "test_period": test_period,
            "train_n": train_stats["n"], "train_wr": train_stats["wr"],
            "train_exp": train_stats["exp"], "train_compound": train_stats["compound"],
            "test_n": test_stats["n"], "test_wr": test_stats["wr"],
            "test_exp": test_stats["exp"], "test_compound": test_stats["compound"],
        })
    return results


def bootstrap_ci(trades_pcts, n_iter=1000, seed=42):
    if not trades_pcts:
        return None
    np.random.seed(seed)
    boots = []
    n = len(trades_pcts)
    for _ in range(n_iter):
        sample = np.random.choice(trades_pcts, size=n, replace=True)
        boots.append(np.mean(sample))
    p5 = np.percentile(boots, 5)
    p50 = np.percentile(boots, 50)
    p95 = np.percentile(boots, 95)
    return {"p5": round(p5, 2), "p50": round(p50, 2), "p95": round(p95, 2)}


def main():
    print("Loading 1y NIFTY daily...")
    df = get_bars("NIFTY", n_days=400)
    df = df.copy().sort_values("Date").reset_index(drop=True)
    print(f"  {len(df)} bars from {df.iloc[0]['Date']} to {df.iloc[-1]['Date']}\n")

    # Test multiple param combos within 9%-friendly range
    print("=== Walk-forward sensitivity ===")
    print(f"{'thr%':>5s} {'hold':>5s} {'fold':>5s} {'period':30s} {'tr_n':>5s} {'tr_exp':>7s} {'te_n':>5s} {'te_exp':>7s} {'te_cmp':>7s}")
    for thr, hold in [(1.0, 1), (1.0, 2), (1.0, 3), (1.5, 2), (1.5, 3)]:
        results = walk_forward(df, threshold_pct=thr, hold_days=hold, k=3)
        for r in results:
            print(f"{thr:>5.1f} {hold:>5d} {r['fold']:>5d} {r['test_period'][:30]:30s} "
                  f"{r['train_n']:>5d} {r['train_exp']:>+6.2f}% "
                  f"{r['test_n']:>5d} {r['test_exp']:>+6.2f}% {r['test_compound']:>+6.1f}%")
        # Average across folds
        avg_test_exp = np.mean([r["test_exp"] for r in results])
        avg_test_n = sum(r["test_n"] for r in results)
        print(f"  AVG test exp across folds: {avg_test_exp:+.2f}% (total test trades: {avg_test_n})\n")

    # Bootstrap on full-year trades for the best config
    print("=== Bootstrap CI on best config (thr=1.0, hold=2) ===")
    signals = detect_extreme_days(df, 1.0)
    trades = [simulate_option_trade(df, s, 2) for s in signals]
    valid = [t for t in trades if t]
    pcts = [t["premium_pct"] for t in valid]
    ci = bootstrap_ci(pcts)
    print(f"Trades: {len(pcts)}")
    print(f"Median expectancy: {np.median(pcts):+.2f}%")
    print(f"5th percentile (worst case): {ci['p5']:+.2f}%")
    print(f"95th percentile (best case): {ci['p95']:+.2f}%")
    print(f"Mean: {np.mean(pcts):+.2f}%")


if __name__ == "__main__":
    main()

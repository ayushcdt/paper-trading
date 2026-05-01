"""
Sector-breadth filtered NIFTY directional (Agent's #1 strategy, edge 4/5).

Hypothesis: when 9+ of 13 sectors all align bullish/bearish, NIFTY direction
the next 1-3 days has empirically ~58-62% persistence (Zweig breadth-thrust
adapted for India).

Method:
  Daily, count sector indices closing above/below 5-day MA.
  If breadth_up >= 9: BULLISH signal -> buy NIFTY ATM CE
  If breadth_up <= 4: BEARISH signal -> buy NIFTY ATM PE
  Else: no trade

  Hold 1-3 days, exit on stop/target/time.

Tests across 1 year of NIFTY daily + sector indices.
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
from fno.black_scholes import bs_price


SECTOR_INDICES = [
    "NIFTY_BANK", "NIFTY_AUTO", "NIFTY_IT", "NIFTY_FMCG",
    "NIFTY_PHARMA", "NIFTY_REALTY", "NIFTY_ENERGY", "NIFTY_INFRA",
    "NIFTY_METAL",
]

RISK_FREE = 0.065
NIFTY_IV = 0.159
DEFAULT_DTE_DAYS = 4
STRIKE_STEP = 50
OPT_SLIPPAGE_PCT = 1.5
OPT_FEE_RS = 80.0
LOT_SIZE = 75
MA_PERIOD = 5

BULLISH_THRESHOLD = 7  # of 9 available sectors
BEARISH_THRESHOLD = 2


def load_sectors():
    out = {}
    for sym in SECTOR_INDICES:
        if sym not in SYMBOL_TOKENS:
            continue
        df = get_bars(sym, n_days=400)
        if len(df) < MA_PERIOD + 50:
            continue
        df = df.copy().sort_values("Date").reset_index(drop=True)
        df["MA"] = df["Close"].rolling(MA_PERIOD).mean()
        df["Above_MA"] = df["Close"] > df["MA"]
        out[sym] = df
    return out


def compute_breadth(sector_data, target_date):
    above = 0
    total = 0
    for sym, df in sector_data.items():
        df_dt = pd.to_datetime(df["Date"])
        mask = df_dt <= target_date
        if mask.sum() < MA_PERIOD + 1:
            continue
        last = df[mask].iloc[-1]
        if pd.notna(last["Above_MA"]):
            total += 1
            if last["Above_MA"]:
                above += 1
    return above, total


def detect_breadth_signals(nifty_df, sector_data):
    signals = []
    nifty_df = nifty_df.copy().sort_values("Date").reset_index(drop=True)
    for i in range(MA_PERIOD + 5, len(nifty_df) - 5):
        date = pd.Timestamp(nifty_df.iloc[i]["Date"])
        above, total = compute_breadth(sector_data, date)
        if total < 7:
            continue
        sig = None
        if above >= BULLISH_THRESHOLD:
            sig = "BULLISH"
        elif above <= BEARISH_THRESHOLD:
            sig = "BEARISH"
        if sig:
            entry_idx = i + 1  # next day
            if entry_idx >= len(nifty_df):
                continue
            signals.append({
                "entry_idx": entry_idx,
                "entry_date": pd.Timestamp(nifty_df.iloc[entry_idx]["Date"]),
                "direction": sig,
                "breadth": above,
                "breadth_total": total,
            })
    return signals


def filter_cooldown(signals, days=3):
    if not signals:
        return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["entry_idx"] - out[-1]["entry_idx"] >= days:
            out.append(s)
    return out


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
                adj = pnl_rs / (entry_premium * LOT_SIZE) * 100
                return {**signal, "premium_pct": round(adj, 2), "exit_reason": "STOP", "hold_days": hold}
            if ret_pct >= target_pct:
                pnl_rs = target_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
                adj = pnl_rs / (entry_premium * LOT_SIZE) * 100
                return {**signal, "premium_pct": round(adj, 2), "exit_reason": "TARGET", "hold_days": hold}
    bar = df.iloc[entry_idx + hold_days]
    spot = bar["Close"]
    elapsed_days = hold_days
    dte_now = max(0.001, entry_dte - elapsed_days / 365)
    curr_theory = bs_price(spot, atm_strike, dte_now, RISK_FREE, NIFTY_IV, opt_type).premium
    curr = curr_theory * (1 - OPT_SLIPPAGE_PCT / 100)
    gross_pct = (curr - entry_premium) / entry_premium * 100
    pnl_rs = gross_pct / 100 * (entry_premium * LOT_SIZE) - OPT_FEE_RS
    adj = pnl_rs / (entry_premium * LOT_SIZE) * 100
    return {**signal, "premium_pct": round(adj, 2), "exit_reason": "TIME", "hold_days": hold_days}


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


def walk_forward_3fold(nifty_df, sector_data, hold_days, k=3):
    n = len(nifty_df)
    chunk = n // k
    results = []
    for fold in range(k):
        ts = fold * chunk
        te = ts + chunk if fold < k - 1 else n
        test_df = nifty_df.iloc[ts:te].reset_index(drop=True)
        signals = detect_breadth_signals(test_df, sector_data)
        signals = filter_cooldown(signals, hold_days)
        trades = [simulate_option_trade(test_df, s, hold_days) for s in signals]
        st = stats(trades)
        results.append({
            "fold": fold + 1,
            "period": f"{test_df.iloc[0]['Date']} to {test_df.iloc[-1]['Date']}",
            **st,
        })
    return results


def main():
    print("Loading NIFTY daily 1y...")
    nifty_df = get_bars("NIFTY", n_days=400)
    nifty_df = nifty_df.copy().sort_values("Date").reset_index(drop=True)
    print(f"  {len(nifty_df)} bars\n")

    print("Loading 9 sector indices...")
    sector_data = load_sectors()
    print(f"  {len(sector_data)} sectors loaded\n")

    print("=== SECTOR-BREADTH NIFTY OPTIONS ===")
    print(f"Threshold: bullish >= {BULLISH_THRESHOLD} of {len(sector_data)} sectors above {MA_PERIOD}DMA")
    print(f"           bearish <= {BEARISH_THRESHOLD}")
    print()
    print(f"{'HOLD':>5s} {'fold':>5s} {'period':30s} {'#':>4s} {'WR%':>6s} {'EXP%':>7s} {'CMP%':>7s}")
    for hold_days in [1, 2, 3]:
        results = walk_forward_3fold(nifty_df, sector_data, hold_days)
        for r in results:
            print(f"{hold_days:>5d} {r['fold']:>5d} {r['period'][:30]:30s} "
                  f"{r['n']:>4d} {r['wr']:>5.1f}% {r['exp']:>+6.2f}% {r['compound']:>+6.1f}%")
        avg_exp = np.mean([r["exp"] for r in results])
        total_n = sum(r["n"] for r in results)
        print(f"  AVG fold exp: {avg_exp:+.2f}%  (total trades across folds: {total_n})\n")

    # Also show full-year stats
    print("=== FULL YEAR STATS (no walk-forward) ===")
    for hold_days in [1, 2, 3]:
        signals = filter_cooldown(detect_breadth_signals(nifty_df, sector_data), hold_days)
        trades = [simulate_option_trade(nifty_df, s, hold_days) for s in signals]
        st = stats(trades)
        print(f"  hold={hold_days}d  trades={st['n']}  WR={st['wr']}%  exp={st['exp']:+.2f}%  compound={st['compound']:+.1f}%")


if __name__ == "__main__":
    main()

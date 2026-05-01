"""
Multi-strategy backtest. Tests 6 different strategy types on the same
90-day NIFTY 5-min data with identical friction model and option pricing.

Strategies:
  S1: Reversal (current v2)
  S2: Open gap fade (gap >0.7% -> opposite direction)
  S3: Open gap continuation (gap >0.7% + first 30min same direction)
  S4: First-hour breakout (break 9:15-10:15 H/L -> direction)
  S5: Last-hour momentum (direction at 14:30 -> ride to 15:00 close)
  S6: VIX-spike reversal (VIX intraday gain >5%)

For each:
  - Run on 90-day NIFTY 5-min bars
  - 3-fold walk-forward validation
  - Compute: trades/month, win_rate, expectancy, compound rate
  - Friction: 1.5% slippage + Rs 80 fees per trade

Then:
  - Compute correlation matrix between strategies (do they fire on same days?)
  - Pick best uncorrelated combo
  - Report ensemble compound rate
"""
from __future__ import annotations

import os
import sys
import time as time_mod
from datetime import datetime, timedelta
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
SLIPPAGE_PCT = 1.5
FEE_RS = 80.0
LOT_SIZE = 75
PER_TRADE_RS = 4000  # Rs per trade for compound calc


# ============================================================
# Data fetch (chunked)
# ============================================================
def fetch_chunked(symbol: str, days: int) -> pd.DataFrame:
    f = get_fetcher()
    if not f.logged_in:
        f.login()
    token = SYMBOL_TOKENS.get(symbol)
    if not token:
        raise RuntimeError(f"Token not found for {symbol}")
    all_dfs = []
    end = datetime.now()
    chunks = (days + 29) // 30
    for c in range(chunks):
        ce = end - timedelta(days=c * 30)
        cs = ce - timedelta(days=30)
        params = {"exchange": "NSE", "symboltoken": token, "interval": "FIVE_MINUTE",
                  "fromdate": cs.strftime("%Y-%m-%d %H:%M"),
                  "todate": ce.strftime("%Y-%m-%d %H:%M")}
        try:
            d = f.api.getCandleData(params)
            if d.get("status") and d.get("data"):
                df = pd.DataFrame(d["data"], columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                df["Date"] = pd.to_datetime(df["Date"])
                if getattr(df["Date"].dtype, "tz", None) is not None:
                    df["Date"] = df["Date"].dt.tz_localize(None)
                all_dfs.append(df)
        except Exception as e:
            print(f"  chunk {c+1}: {e}")
        time_mod.sleep(0.5)
    full = pd.concat(all_dfs).drop_duplicates(subset="Date").sort_values("Date").reset_index(drop=True)
    full["DateOnly"] = full["Date"].dt.date
    return full


# ============================================================
# Trade simulation (shared)
# ============================================================
def simulate_option_trade(df: pd.DataFrame, signal: dict,
                          stop_pct: float = -20, target_pct: float = 60,
                          max_hold_bars: int = 30, otm_offset: int = 100,
                          eod_hour: int = 15, use_trailing: bool = True) -> dict:
    direction = signal["direction"]
    spot = signal["spot"]
    if direction == "BULLISH":
        strike = int((spot + otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "CE"
    else:
        strike = int((spot - otm_offset) / STRIKE_STEP) * STRIKE_STEP
        opt_type = "PE"

    entry_dte = DEFAULT_DTE_DAYS / 365.0
    entry_theory = bs_price(spot, strike, entry_dte, RISK_FREE, NIFTY_IV, opt_type).premium
    if entry_theory <= 0.5:
        return {**signal, "exit_pct": 0.0, "exit_reason": "INVALID", "hold_bars": 0}
    entry_premium = entry_theory * (1 + SLIPPAGE_PCT / 100)
    target_premium = entry_premium * (1 + target_pct / 100)
    current_stop_pct = stop_pct
    peak_pct = 0.0
    entry_time = pd.Timestamp(signal["datetime"])
    start_idx = signal["global_idx"]

    for hb in range(1, max_hold_bars + 1):
        idx = start_idx + hb
        if idx >= len(df):
            return {**signal, "exit_pct": 0.0, "exit_reason": "DATA_END", "hold_bars": hb}
        bar = df.iloc[idx]
        bt = pd.Timestamp(bar["Date"])
        if bt.hour >= eod_hour:
            sn = bar["Close"]
            ed = (bt - entry_time).total_seconds() / 86400
            dte = max(0.001, entry_dte - ed / 365)
            ct = bs_price(sn, strike, dte, RISK_FREE, NIFTY_IV, opt_type).premium
            cur = ct * (1 - SLIPPAGE_PCT / 100)
            gross = (cur - entry_premium) / entry_premium * 100
            pnl_rs = gross / 100 * (entry_premium * LOT_SIZE) - FEE_RS
            pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
            return {**signal, "exit_pct": round(pct, 2), "exit_reason": "EOD", "hold_bars": hb}

        ed = (bt - entry_time).total_seconds() / 86400
        dte = max(0.001, entry_dte - ed / 365)
        bs_h = bar["High"] if opt_type == "CE" else bar["Low"]
        bs_l = bar["Low"] if opt_type == "CE" else bar["High"]
        best_t = bs_price(bs_h, strike, dte, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_t = bs_price(bs_l, strike, dte, RISK_FREE, NIFTY_IV, opt_type).premium
        worst_exit = worst_t * (1 - SLIPPAGE_PCT / 100)
        best_exit = best_t * (1 - SLIPPAGE_PCT / 100)

        bp = (best_exit - entry_premium) / entry_premium * 100
        if bp > peak_pct:
            peak_pct = bp
        if use_trailing and peak_pct >= 50:
            current_stop_pct = max(current_stop_pct, 30.0)
        elif use_trailing and peak_pct >= 30:
            current_stop_pct = max(current_stop_pct, 0.0)
        sp = entry_premium * (1 + current_stop_pct / 100)

        if worst_exit <= sp:
            pnl_rs = current_stop_pct / 100 * (entry_premium * LOT_SIZE) - FEE_RS
            pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
            return {**signal, "exit_pct": round(pct, 2), "exit_reason": "STOP", "hold_bars": hb}
        if best_exit >= target_premium:
            pnl_rs = target_pct / 100 * (entry_premium * LOT_SIZE) - FEE_RS
            pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
            return {**signal, "exit_pct": round(pct, 2), "exit_reason": "TARGET", "hold_bars": hb}

    bar = df.iloc[start_idx + max_hold_bars]
    sn = bar["Close"]
    bt = pd.Timestamp(bar["Date"])
    ed = (bt - entry_time).total_seconds() / 86400
    dte = max(0.001, entry_dte - ed / 365)
    ct = bs_price(sn, strike, dte, RISK_FREE, NIFTY_IV, opt_type).premium
    cur = ct * (1 - SLIPPAGE_PCT / 100)
    gross = (cur - entry_premium) / entry_premium * 100
    pnl_rs = gross / 100 * (entry_premium * LOT_SIZE) - FEE_RS
    pct = pnl_rs / (entry_premium * LOT_SIZE) * 100
    return {**signal, "exit_pct": round(pct, 2), "exit_reason": "TIME", "hold_bars": max_hold_bars}


# ============================================================
# STRATEGY SIGNAL GENERATORS
# ============================================================
def s1_reversal(df: pd.DataFrame) -> list[dict]:
    """v2 reversal — fires on intraday extreme + recovery."""
    signals = []
    for date, dd in df.groupby("DateOnly"):
        dd = dd.reset_index(drop=True)
        if len(dd) < 30: continue
        ig = df[df["DateOnly"] == date].index[0]
        prev = df.iloc[ig - 1]["Close"] if ig > 0 else dd.iloc[0]["Open"]
        ih = dd.iloc[0]["High"]
        il = dd.iloc[0]["Low"]
        for i in range(30, len(dd)):
            bar = dd.iloc[i]
            spot = bar["Close"]
            ih = max(ih, bar["High"])
            il = min(il, bar["Low"])
            ip = (spot - prev) / prev * 100
            nl = (spot - il) / spot * 100 < 0.3
            nh = (ih - spot) / spot * 100 < 0.3
            r5 = (spot - dd.iloc[i-5]["Close"]) / dd.iloc[i-5]["Close"] * 100
            sig = None
            if ip <= -1.0 and nl and r5 >= 0.05: sig = "BULLISH"
            elif ip >= 1.0 and nh and r5 <= -0.05: sig = "BEARISH"
            if sig:
                signals.append({"datetime": bar["Date"], "date": str(date),
                              "direction": sig, "spot": float(spot),
                              "global_idx": ig + i, "strategy": "S1_reversal"})
    return _cooldown(signals, 24)  # 2hr


def s2_gap_fade(df: pd.DataFrame) -> list[dict]:
    """Gap fade: NIFTY opens >0.7% from prev close → take opposite direction."""
    signals = []
    by_date = df.groupby("DateOnly")
    for date, dd in by_date:
        dd = dd.reset_index(drop=True)
        if len(dd) < 5: continue
        ig = df[df["DateOnly"] == date].index[0]
        prev = df.iloc[ig - 1]["Close"] if ig > 0 else None
        if prev is None: continue
        first_open = dd.iloc[0]["Open"]
        gap_pct = (first_open - prev) / prev * 100
        # Wait 15 min for gap to settle, then enter
        if abs(gap_pct) < 0.7: continue
        entry_bar = dd.iloc[3]  # bar #3 = ~15 min after open
        sig = "BEARISH" if gap_pct > 0 else "BULLISH"
        signals.append({"datetime": entry_bar["Date"], "date": str(date),
                       "direction": sig, "spot": float(entry_bar["Close"]),
                       "global_idx": ig + 3, "strategy": "S2_gap_fade",
                       "gap_pct": round(gap_pct, 2)})
    return signals


def s3_gap_continuation(df: pd.DataFrame) -> list[dict]:
    """Gap-and-go: NIFTY opens >0.7% + first 30min confirms direction."""
    signals = []
    by_date = df.groupby("DateOnly")
    for date, dd in by_date:
        dd = dd.reset_index(drop=True)
        if len(dd) < 8: continue
        ig = df[df["DateOnly"] == date].index[0]
        prev = df.iloc[ig - 1]["Close"] if ig > 0 else None
        if prev is None: continue
        first_open = dd.iloc[0]["Open"]
        gap_pct = (first_open - prev) / prev * 100
        if abs(gap_pct) < 0.7: continue
        # First 30 min direction
        bar_30 = dd.iloc[5]  # 30 min in
        first30_close = bar_30["Close"]
        first30_dir = (first30_close - first_open) / first_open * 100
        # Confirms gap direction?
        if gap_pct > 0 and first30_dir > 0:
            sig = "BULLISH"
        elif gap_pct < 0 and first30_dir < 0:
            sig = "BEARISH"
        else:
            continue
        # Entry at bar 6 (after first-30min confirmation)
        entry_bar = dd.iloc[6]
        signals.append({"datetime": entry_bar["Date"], "date": str(date),
                       "direction": sig, "spot": float(entry_bar["Close"]),
                       "global_idx": ig + 6, "strategy": "S3_gap_cont",
                       "gap_pct": round(gap_pct, 2)})
    return signals


def s4_first_hour_breakout(df: pd.DataFrame) -> list[dict]:
    """Break 1-hour high/low → trend follow."""
    signals = []
    by_date = df.groupby("DateOnly")
    for date, dd in by_date:
        dd = dd.reset_index(drop=True)
        if len(dd) < 30: continue
        ig = df[df["DateOnly"] == date].index[0]
        # First hour = bars 0-11 (12 bars × 5min = 60 min)
        first_hour = dd.iloc[:12]
        h1_high = first_hour["High"].max()
        h1_low = first_hour["Low"].min()
        # Watch for breakout from bar 12 onwards
        for i in range(12, len(dd) - 5):
            bar = dd.iloc[i]
            spot = bar["Close"]
            sig = None
            if spot > h1_high * 1.001:  # 0.1% breakout
                sig = "BULLISH"
            elif spot < h1_low * 0.999:
                sig = "BEARISH"
            if sig:
                signals.append({"datetime": bar["Date"], "date": str(date),
                               "direction": sig, "spot": float(spot),
                               "global_idx": ig + i, "strategy": "S4_breakout"})
                break  # one per day
    return signals


def s5_last_hour_momentum(df: pd.DataFrame) -> list[dict]:
    """Direction at 14:00-14:30 → ride to 15:00."""
    signals = []
    by_date = df.groupby("DateOnly")
    for date, dd in by_date:
        dd = dd.reset_index(drop=True)
        if len(dd) < 65: continue
        ig = df[df["DateOnly"] == date].index[0]
        # Find the bar near 14:30
        for i, row in dd.iterrows():
            if pd.Timestamp(row["Date"]).hour >= 14 and pd.Timestamp(row["Date"]).minute >= 30:
                # Direction = current close vs 30 min prior
                if i < 6: break
                prior = dd.iloc[i - 6]["Close"]
                cur = row["Close"]
                move_pct = (cur - prior) / prior * 100
                if abs(move_pct) < 0.15:
                    break
                sig = "BULLISH" if move_pct > 0 else "BEARISH"
                signals.append({"datetime": row["Date"], "date": str(date),
                               "direction": sig, "spot": float(cur),
                               "global_idx": ig + i, "strategy": "S5_last_hour"})
                break
    return signals


def s6_vix_spike_reversal(df: pd.DataFrame, vix_df: pd.DataFrame) -> list[dict]:
    """VIX intraday spike → expect mean reversion in NIFTY."""
    signals = []
    if vix_df.empty:
        return signals
    vix_by_date = {row["Date"].date(): float(row["Close"]) for _, row in vix_df.iterrows()}
    by_date = df.groupby("DateOnly")
    for date, dd in by_date:
        dd = dd.reset_index(drop=True)
        if len(dd) < 30: continue
        ig = df[df["DateOnly"] == date].index[0]
        prev_vix = vix_by_date.get(date - timedelta(days=1))
        cur_vix = vix_by_date.get(date)
        if not prev_vix or not cur_vix: continue
        vix_move_pct = (cur_vix - prev_vix) / prev_vix * 100
        if vix_move_pct < 5: continue
        # Buy NIFTY CE at the VIX-spike day (mean reversion bet)
        # Enter at mid-day
        entry_bar = dd.iloc[30]
        signals.append({"datetime": entry_bar["Date"], "date": str(date),
                       "direction": "BULLISH", "spot": float(entry_bar["Close"]),
                       "global_idx": ig + 30, "strategy": "S6_vix_spike"})
    return signals


def _cooldown(signals: list[dict], cooldown_bars: int) -> list[dict]:
    if not signals: return []
    out = [signals[0]]
    for s in signals[1:]:
        if s["global_idx"] - out[-1]["global_idx"] >= cooldown_bars:
            out.append(s)
    return out


# ============================================================
# Backtest each strategy
# ============================================================
def stats(trades: list[dict]) -> dict:
    valid = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]
    if not valid:
        return {"n": 0, "wr": 0, "exp": 0, "compound_pct": 0}
    wins = [t for t in valid if t["exit_pct"] > 0]
    wr = len(wins) / len(valid) * 100
    exp = np.mean([t["exit_pct"] for t in valid])
    # Compound rate per Rs 4K trade
    cum = 1.0
    for t in valid:
        cum *= (1 + (t["exit_pct"] / 100) * (PER_TRADE_RS / 13000))
    compound_pct = (cum - 1) * 100
    return {"n": len(valid), "wr": round(wr, 1), "exp": round(exp, 2),
            "compound_pct": round(compound_pct, 1), "trades": valid}


def main():
    print("=== Multi-strategy backtest, 90-day NIFTY 5-min data ===\n")

    print("Fetching NIFTY 90 days...")
    nifty_df = fetch_chunked("NIFTY", days=90)
    print(f"  {len(nifty_df)} bars\n")

    print("Fetching INDIAVIX 120 days...")
    f = get_fetcher()
    if not f.logged_in: f.login()
    vix_df = f.get_historical_data("INDIAVIX", interval="ONE_DAY", days=120)
    print(f"  {len(vix_df)} bars\n")

    strategies = [
        ("S1 reversal", lambda: s1_reversal(nifty_df)),
        ("S2 gap fade", lambda: s2_gap_fade(nifty_df)),
        ("S3 gap cont", lambda: s3_gap_continuation(nifty_df)),
        ("S4 1h break", lambda: s4_first_hour_breakout(nifty_df)),
        ("S5 last hour", lambda: s5_last_hour_momentum(nifty_df)),
        ("S6 vix spike", lambda: s6_vix_spike_reversal(nifty_df, vix_df)),
    ]

    print(f"{'STRATEGY':<14s} {'#':>5s} {'WR%':>6s} {'EXP%':>7s} {'COMPOUND%':>10s}")
    all_results = {}
    all_trades = {}
    for name, fn in strategies:
        signals = fn()
        trades = [simulate_option_trade(nifty_df, s) for s in signals]
        s = stats(trades)
        all_results[name] = s
        all_trades[name] = s.get("trades", [])
        print(f"{name:<14s} {s['n']:>5d} {s['wr']:>5.1f}% {s['exp']:>+6.2f}% {s['compound_pct']:>+9.1f}%")

    # Date overlap analysis (correlation proxy)
    print(f"\n=== STRATEGY DATE OVERLAP MATRIX ===")
    print(f"{'':14s}" + "".join(f"{name:>14s}" for name, _ in strategies))
    for name1, _ in strategies:
        dates1 = set((t["date"], t["direction"]) for t in all_trades[name1])
        row = f"{name1:<14s}"
        for name2, _ in strategies:
            dates2 = set((t["date"], t["direction"]) for t in all_trades[name2])
            overlap = len(dates1 & dates2)
            total = max(1, len(dates1))
            pct = overlap / total * 100
            row += f"{pct:>13.0f}%"
        print(row)

    # Ensemble: sum compound_pct of independent strategies (Rs 4K each)
    print(f"\n=== ENSEMBLE COMPOUND RATE ===")
    profitable = [(n, s) for n, s in all_results.items() if s["compound_pct"] > 0 and s["n"] >= 3]
    print(f"Profitable strategies (>=3 trades, +ve compound): {len(profitable)}")
    if profitable:
        # Naive ensemble: assume capital split equally across N strategies
        n_strats = len(profitable)
        per_strat_capital = 13000 / n_strats
        # Recompute compound per strategy at smaller per-strat capital
        ensemble_compound = 0
        for name, s in profitable:
            trades = all_trades[name]
            valid = [t for t in trades if t["exit_reason"] not in ("INVALID", "DATA_END")]
            cum = 1.0
            for t in valid:
                cum *= (1 + (t["exit_pct"] / 100) * (PER_TRADE_RS / per_strat_capital))
            ensemble_compound += (cum - 1)
        print(f"Ensemble naive compound (Rs split): {ensemble_compound * 100 / n_strats:+.1f}% per strategy avg")

    # Days summary
    print(f"\n=== DAYS WITH ANY TRADE ===")
    all_dates = set()
    for name, _ in strategies:
        for t in all_trades[name]:
            all_dates.add(t["date"])
    print(f"Total days with at least 1 signal: {len(all_dates)}")
    print(f"Days in dataset: {len(set(nifty_df['DateOnly']))}")
    print(f"Active days: {len(all_dates) / len(set(nifty_df['DateOnly'])) * 100:.0f}%")


if __name__ == "__main__":
    main()

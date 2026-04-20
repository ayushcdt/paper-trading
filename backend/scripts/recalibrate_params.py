"""
Phase 3: Bounded parameter recalibration.

Monthly (1st trading day), tune each variant's key parameters on trailing 1y,
but within HARD BOUNDS so the strategy can't drift into nonsense.

Tunable parameters + bounds:
  momentum_agg:
    hard_stop_pct:        [-18,  -10]   (default -15)
    max_hold_days:        [ 60,   120]  (default 90)
    rank_drop_threshold:  [ 20,    40]  (default 30)

  momentum_cons:
    hard_stop_pct:        [-15,   -8]   (default -12)
    atr_buffer_multiple:  [0.5,   1.5]  (default 1.0)
    max_hold_days:        [ 45,    90]  (default 60)

  mean_reversion:
    hard_stop_pct:        [ -8,   -3]   (default -5)
    max_hold_days:        [ 10,    30]  (default 20)

For each variant:
  - Grid search 3x3x3 combinations on trailing 1y
  - Pick the combo with best Sharpe WITHIN BOUNDS
  - If best-Sharpe combo's out-of-sample Sharpe (last 3 months) is below 0, REJECT -- keep defaults
  - Write chosen params to data/variant_params.json

Engine reads variant_params.json at startup; if absent, uses defaults.
"""

from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import yfinance as yf
from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.variants import MomentumAgg, MomentumCons, MeanReversion
from strategy_v2 import TOP_20_DROP
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP


OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "variant_params.json"
WARMUP = 252
COST = 0.4

# Parameter grids + bounds
GRIDS = {
    "momentum_agg": {
        "hard_stop_pct":       [-18, -15, -12],
        "max_hold_days":       [60, 90, 120],
        "rank_drop_threshold": [20, 30, 40],
    },
    "momentum_cons": {
        "hard_stop_pct":        [-15, -12, -9],
        "atr_buffer_multiple":  [0.5, 1.0, 1.5],
        "max_hold_days":        [45, 60, 90],
    },
    "mean_reversion": {
        "hard_stop_pct":  [-8, -5, -3],
        "max_hold_days":  [10, 20, 30],
    },
}

VARIANT_CLASSES = {
    "momentum_agg":   MomentumAgg,
    "momentum_cons":  MomentumCons,
    "mean_reversion": MeanReversion,
}


def universe():
    return [s for s in sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP)) if s not in TOP_20_DROP]


def fetch(symbols, start, end):
    tickers = [f"{s}.NS" for s in symbols] + ["^NSEI"]
    chunks = []
    for i in range(0, len(tickers), 10):
        d = yf.download(tickers[i : i + 10], start=start, end=end, progress=False,
                        auto_adjust=True, threads=False, group_by="ticker")
        if d is not None and not d.empty:
            chunks.append(d)
    big = pd.concat(chunks, axis=1)
    big = big.loc[:, ~big.columns.duplicated()]

    def g(t):
        if t not in big.columns.get_level_values(0):
            return None
        sub = big[t].copy().dropna(how="all")
        return sub if not sub.empty else None

    nf = g("^NSEI")
    nifty_df = nf["Close"].dropna().reset_index() if nf is not None else pd.DataFrame()
    nifty_df.columns = ["Date", "Close"] if not nifty_df.empty else []
    hists = {}
    for s in symbols:
        sub = g(f"{s}.NS")
        if sub is None:
            continue
        df = sub[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < WARMUP + 50:
            continue
        df = df.reset_index()
        df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
        hists[s] = df
    return nifty_df, hists


def run_variant_backtest(variant, nifty_df, histories, symbols, start_idx, end_idx):
    """Simulates and returns list of trade net returns."""
    nifty_df = nifty_df.copy()
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    nifty_df["YM"] = nifty_df["Date"].dt.to_period("M")
    month_starts = [i for i in nifty_df.drop_duplicates("YM", keep="first").index if start_idx <= i <= end_idx]

    rets = []
    open_positions = {}
    for n_idx in month_starts:
        rebalance_date = nifty_df.iloc[n_idx]["Date"]
        picks = variant.pick(histories, rebalance_date, symbols)
        target = {p.symbol for p in picks}
        ranked = {p.symbol: p.rank for p in picks}
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = histories[sym]
            idxs = df.index[df["Date"] <= rebalance_date]
            if not len(idxs):
                continue
            cur_idx = int(idxs[-1])
            ex = variant.check_exit(df, pos["entry_price"], pos["entry_idx"], cur_idx, ranked.get(sym))
            if ex.triggered or sym not in target:
                ep = ex.exit_price if ex.triggered else float(df.iloc[cur_idx]["Close"])
                rets.append((ep - pos["entry_price"]) / pos["entry_price"] * 100 - COST)
                del open_positions[sym]
        for p in picks:
            if p.symbol in open_positions:
                continue
            df = histories[p.symbol]
            idxs = df.index[df["Date"] <= rebalance_date]
            if not len(idxs):
                continue
            li = int(idxs[-1])
            if li + 1 >= len(df):
                continue
            ep = float(df.iloc[li + 1]["Open"])
            open_positions[p.symbol] = {"entry_price": ep, "entry_idx": li + 1, "entry_date": df.iloc[li + 1]["Date"]}
    return rets


def sharpe_of(rets):
    if not rets:
        return 0
    a = np.array(rets)
    return float(a.mean() / a.std()) if a.std() > 0 else 0


def recalibrate_one(variant_name: str, nifty_df, histories, symbols, start_idx, end_idx):
    cls = VARIANT_CLASSES[variant_name]
    grid = GRIDS[variant_name]
    keys = list(grid.keys())
    combos = list(itertools.product(*[grid[k] for k in keys]))

    default_variant = cls()
    default_rets = run_variant_backtest(default_variant, nifty_df, histories, symbols, start_idx, end_idx)
    default_sharpe = sharpe_of(default_rets)

    logger.info(f"  {variant_name}: testing {len(combos)} combos (default Sharpe={default_sharpe:.3f})")

    best = {"params": None, "sharpe": default_sharpe, "rets": default_rets}
    for combo in combos:
        kwargs = dict(zip(keys, combo))
        v = cls(**kwargs)
        rets = run_variant_backtest(v, nifty_df, histories, symbols, start_idx, end_idx)
        s = sharpe_of(rets)
        if s > best["sharpe"]:
            best = {"params": kwargs, "sharpe": s, "rets": rets}

    if best["params"] is None:
        logger.info(f"    no improvement over defaults; keeping defaults")
        return None, default_sharpe

    # Validate: out-of-sample on the last 3 months not seen by the grid search
    oos_start = max(start_idx, end_idx - 63)
    oos_v = cls(**best["params"])
    oos_rets = run_variant_backtest(oos_v, nifty_df, histories, symbols, oos_start, end_idx)
    oos_sharpe = sharpe_of(oos_rets)
    if oos_sharpe < 0:
        logger.info(f"    rejected: best combo has negative OOS Sharpe ({oos_sharpe:.3f})")
        return None, default_sharpe

    logger.info(f"    chose {best['params']}: IS Sharpe {best['sharpe']:.3f}, OOS Sharpe {oos_sharpe:.3f}")
    return best["params"], best["sharpe"]


def main():
    today = datetime.now()
    start = (today - timedelta(days=1 * 365 + WARMUP)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    logger.info(f"Recalibration window: {start} -> {end}")

    syms = universe()
    nifty_df, hists = fetch(syms, start, end)
    if nifty_df.empty:
        logger.error("No Nifty data; aborting")
        return
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    total_bars = len(nifty_df)

    chosen = {"generated_at": today.isoformat(), "window_start": start, "window_end": end, "variants": {}}
    for v_name in GRIDS:
        params, sharpe = recalibrate_one(v_name, nifty_df, hists, syms, WARMUP, total_bars - 1)
        chosen["variants"][v_name] = {
            "params": params,   # None means use defaults
            "sharpe": round(sharpe, 3),
        }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(chosen, indent=2), encoding="utf-8")
    logger.info(f"Wrote calibrated params -> {OUT_PATH}")


if __name__ == "__main__":
    main()

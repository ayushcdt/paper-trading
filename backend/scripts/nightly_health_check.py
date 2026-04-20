"""
Phase 2 nightly job:
  1. Re-backtest all 4 variants on trailing 1y and 3y windows.
  2. Compute expected-3M-mean and expected-3M-stdev per variant.
  3. Compare against live P&L (from history snapshots); flag decay.
  4. Write summary to data/variant_health.json (read by dashboard).

Schedule via Windows Task Scheduler at 17:00 daily (post-close).
"""

from __future__ import annotations

import json
import os
import sys
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

from adaptive.regime import classify_regime, compute_breadth
from adaptive.variants import build_variants
from adaptive.guardrails import load_state, save_state, check_variant_decay, update_portfolio
from strategy_v2 import TOP_20_DROP
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP


OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "variant_health.json"
WARMUP = 252
COST = 0.4


def universe():
    return [s for s in sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP)) if s not in TOP_20_DROP]


def fetch(symbols, start, end):
    tickers = [f"{s}.NS" for s in symbols] + ["^NSEI", "^INDIAVIX"]
    chunks = []
    cs = 10
    for i in range(0, len(tickers), cs):
        d = yf.download(tickers[i : i + cs], start=start, end=end, progress=False,
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
    nifty_close = nf["Close"].dropna() if nf is not None else pd.Series(dtype=float)
    nifty_df = nifty_close.reset_index()
    nifty_df.columns = ["Date", "Close"]
    vs = g("^INDIAVIX")
    vix = vs["Close"].dropna() if vs is not None else pd.Series(dtype=float)
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
    return nifty_df, hists, vix


def simulate_variant(variant, nifty_df, histories, vix_series, symbols, start_idx, end_idx, regime_filter=None):
    """
    Walk monthly from start_idx to end_idx. Return list of trade net_returns (pct).
    If regime_filter set, only trade when classifier produces that regime.
    """
    nifty_df = nifty_df.copy()
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    nifty_df["YM"] = nifty_df["Date"].dt.to_period("M")
    month_starts = [i for i in nifty_df.drop_duplicates("YM", keep="first").index if start_idx <= i <= end_idx]

    rets = []
    open_positions = {}
    for n_idx in month_starts:
        rebalance_date = nifty_df.iloc[n_idx]["Date"]
        # Optional regime filter
        if regime_filter:
            breadth = compute_breadth(histories, rebalance_date)
            vix_idx = vix_series.index.get_indexer([rebalance_date], method="ffill")[0]
            vv = float(vix_series.iloc[vix_idx]) if vix_idx >= 0 else 15
            vh = vix_series.iloc[: max(0, vix_idx) + 1]
            r = classify_regime(nifty_df.iloc[: n_idx + 1]["Close"], breadth, vv, vh).regime.value
            if r != regime_filter:
                # Close all, skip new
                for sym, pos in list(open_positions.items()):
                    idxs = histories[sym].index[histories[sym]["Date"] <= rebalance_date]
                    if not len(idxs):
                        continue
                    ep = float(histories[sym].iloc[int(idxs[-1])]["Close"])
                    net = (ep - pos["entry_price"]) / pos["entry_price"] * 100 - COST
                    rets.append(net)
                    del open_positions[sym]
                continue

        picks = variant.pick(histories, rebalance_date, symbols)
        target = {p.symbol for p in picks}
        ranked = {p.symbol: p.rank for p in picks}

        # Close
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
                net = (ep - pos["entry_price"]) / pos["entry_price"] * 100 - COST
                rets.append(net)
                del open_positions[sym]

        # Open
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


def summarize(name: str, rets: list[float]) -> dict:
    if not rets:
        return {"name": name, "trades": 0, "avg_return_pct": 0, "stdev_pct": 0,
                "sharpe_proxy": 0, "win_rate_pct": 0, "expected_3m_mean_pct": 0, "expected_3m_stdev_pct": 0}
    arr = np.array(rets)
    mean = float(arr.mean())
    std = float(arr.std())
    wins = float((arr > 0).sum())
    # Rough 3M expectation: ~3 trades per 3 months per slot, so aggregate 3 returns
    return {
        "name": name,
        "trades": len(rets),
        "avg_return_pct": round(mean, 3),
        "stdev_pct": round(std, 3),
        "sharpe_proxy": round(mean / std, 3) if std > 0 else 0,
        "win_rate_pct": round(wins / len(arr) * 100, 1),
        "expected_3m_mean_pct": round(mean * 3, 2),
        "expected_3m_stdev_pct": round(std * np.sqrt(3), 2),
    }


def main():
    today = datetime.now()
    start_3y = (today - timedelta(days=3 * 365 + WARMUP)).strftime("%Y-%m-%d")
    end = today.strftime("%Y-%m-%d")
    logger.info(f"Nightly health check: {start_3y} -> {end}")

    syms = universe()
    nifty_df, hists, vix = fetch(syms, start_3y, end)
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])

    total_bars = len(nifty_df)
    start_idx_1y = max(WARMUP, total_bars - 252)
    start_idx_3y = WARMUP
    end_idx = total_bars - 1

    variants = build_variants()

    health = {"generated_at": today.isoformat(), "windows": {}}
    for window_name, start_i in (("trailing_1y", start_idx_1y), ("trailing_3y", start_idx_3y)):
        window_summary = {}
        for v_name, v in variants.items():
            if v.max_picks == 0:  # defensive (cash)
                window_summary[v_name] = summarize(v_name, [])
                continue
            rets = simulate_variant(v, nifty_df, hists, vix, syms, start_i, end_idx)
            window_summary[v_name] = summarize(v_name, rets)
            logger.info(f"  [{window_name}] {v_name}: trades={len(rets)} "
                        f"mean={window_summary[v_name]['avg_return_pct']:+.2f}% "
                        f"sharpe≈{window_summary[v_name]['sharpe_proxy']:.2f}")
        health["windows"][window_name] = window_summary

    # Update guardrails: mark variant decay if live 3M is worse than 2σ below expected
    state = load_state()
    for v_name, w in health["windows"]["trailing_3y"].items():
        if v_name == "defensive" or w["trades"] == 0:
            continue
        # Load live P&L from Redis via local history snapshots if available
        live_3m = estimate_live_3m_return(v_name)
        if live_3m is None:
            continue
        state = check_variant_decay(
            state, v_name,
            live_3m_return_pct=live_3m,
            expected_3m_return_pct=w["expected_3m_mean_pct"],
            expected_3m_stdev_pct=w["expected_3m_stdev_pct"],
        )
    save_state(state)
    health["guardrail_state"] = state.to_dict()

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(health, indent=2), encoding="utf-8")
    logger.info(f"Health report -> {OUT_PATH}")


def estimate_live_3m_return(variant_name: str) -> float | None:
    """
    Pull actual realized 3M P&L % for this variant from the paper portfolio.
    If no trades yet (system just started), return None so decay check is skipped.
    """
    try:
        from paper.portfolio import PaperPortfolio
        pf = PaperPortfolio()
        per_variant = pf.live_3m_return_by_variant()
        return per_variant.get(variant_name)
    except Exception as e:
        logger.warning(f"Could not read live 3M from paper portfolio: {e}")
        return None


if __name__ == "__main__":
    main()

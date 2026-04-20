"""
Backtest the Artha Momentum v2 strategy on 5+ years of data.

Why yfinance instead of Angel for backtest:
  - Angel historical API caps at ~1-2 years per request; we need a multi-year window.
  - Daily OHLCV is not point-in-time-revised, so yfinance is fine for prices.

Compare against v1 baseline (-32% total, 41% win rate, PF 0.62).
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

# Tame BLAS thread fan-out before importing numpy/pandas (OpenBLAS OOM on Win)
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import yfinance as yf
from logzero import logger

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategy_v2 import (  # noqa: E402
    TOP_20_DROP,
    momentum_score_v2,
    assess_regime,
    check_exit_v2,
    position_size_multiplier,
)
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP  # noqa: E402


# ---------- Config -----------------------------------------------------------

START_DATE = os.environ.get("BACKTEST_START", "2020-01-01")
END_DATE   = os.environ.get("BACKTEST_END", "2025-12-31")
OUT_FILE   = os.environ.get("BACKTEST_OUT", "backtest_v2_results.json")
INITIAL_CAPITAL = 1_000_000
TOP_N_PICKS = 15
WARMUP_BARS = 252
ROUND_TRIP_COST_PCT = 0.4

OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / OUT_FILE


# ---------- Universe + data --------------------------------------------------

def build_universe() -> list[str]:
    """Apply Artha's universe rule: drop top-20 mega-caps."""
    raw = sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP))
    filtered = [s for s in raw if s not in TOP_20_DROP]
    logger.info(f"Universe: {len(filtered)} symbols (dropped {len(raw) - len(filtered)} mega-caps)")
    return filtered


def fetch_history(symbols: list[str]) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], pd.Series]:
    """
    Returns (nifty_df, {symbol: df}, vix_series).
    Uses yfinance .NS tickers for stocks, ^NSEI for Nifty 50, ^INDIAVIX for VIX.
    """
    logger.info(f"Downloading {START_DATE} -> {END_DATE} from yfinance ...")
    tickers_yf = [f"{s}.NS" for s in symbols] + ["^NSEI", "^INDIAVIX"]

    # Chunked, single-threaded downloads to avoid BLAS/yfinance thread fan-out
    chunks: list[pd.DataFrame] = []
    chunk_size = 10
    for i in range(0, len(tickers_yf), chunk_size):
        batch = tickers_yf[i : i + chunk_size]
        df = yf.download(batch, start=START_DATE, end=END_DATE, progress=False, auto_adjust=True, threads=False, group_by="ticker")
        if df is None or df.empty:
            continue
        chunks.append(df)
        logger.info(f"  Downloaded chunk {i // chunk_size + 1}/{(len(tickers_yf) + chunk_size - 1) // chunk_size}")

    if not chunks:
        raise SystemExit("All yfinance downloads returned empty")
    big = pd.concat(chunks, axis=1)
    big = big.loc[:, ~big.columns.duplicated()]

    # group_by='ticker' makes columns ('TICKER', 'Open'/'Close'/...). Extract per-ticker.
    def get_ticker_df(tk: str) -> pd.DataFrame | None:
        if tk not in big.columns.get_level_values(0):
            return None
        sub = big[tk].copy().dropna(how="all")
        return sub if not sub.empty else None

    nifty_sub = get_ticker_df("^NSEI")
    nifty_close = nifty_sub["Close"].dropna() if nifty_sub is not None else pd.Series(dtype=float)
    nifty_df = nifty_close.reset_index()
    nifty_df.columns = ["Date", "Close"]

    vix_sub = get_ticker_df("^INDIAVIX")
    vix_series = vix_sub["Close"].dropna() if vix_sub is not None else pd.Series(dtype=float)

    histories: dict[str, pd.DataFrame] = {}
    for s in symbols:
        sub = get_ticker_df(f"{s}.NS")
        if sub is None:
            continue
        df = sub[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < WARMUP_BARS + 100:
            continue
        df = df.reset_index()
        df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
        histories[s] = df

    logger.info(f"Loaded {len(histories)} usable symbol histories, Nifty bars={len(nifty_df)}, VIX bars={len(vix_series)}")
    return nifty_df, histories, vix_series


# ---------- Backtest loop ----------------------------------------------------

def trading_calendar_monthly_starts(nifty_df: pd.DataFrame, warmup: int) -> list[int]:
    """Return Nifty df indices that are the first trading day of each month."""
    nifty_df = nifty_df.copy()
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    nifty_df["YM"] = nifty_df["Date"].dt.to_period("M")
    starts = nifty_df.drop_duplicates("YM", keep="first").index.tolist()
    return [i for i in starts if i >= warmup]


def compute_breadth(histories: dict[str, pd.DataFrame], target_date: pd.Timestamp) -> float:
    """% of universe stocks trading above their own 200 DMA on target_date."""
    above = 0
    total = 0
    for sym, df in histories.items():
        idxs = df.index[df["Date"] <= target_date]
        if len(idxs) < 200:
            continue
        latest_idx = int(idxs[-1])
        ma_200 = df.iloc[latest_idx - 199 : latest_idx + 1]["Close"].mean()
        if df.iloc[latest_idx]["Close"] > ma_200:
            above += 1
        total += 1
    return (above / total * 100) if total else 0.0


def backtest_v2():
    universe = build_universe()
    nifty_df, histories, vix_series = fetch_history(universe)

    if nifty_df.empty:
        logger.error("Nifty data missing; aborting")
        return
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])

    rebalance_indices = trading_calendar_monthly_starts(nifty_df, WARMUP_BARS)
    logger.info(f"V2 backtest: {len(rebalance_indices)} monthly rebalances over {(nifty_df.iloc[-1]['Date'] - nifty_df.iloc[WARMUP_BARS]['Date']).days} days")

    open_positions: dict[str, dict] = {}     # symbol -> {entry_price, entry_idx, entry_date}
    trades: list[dict] = []
    equity_curve: list[float] = [INITIAL_CAPITAL]
    capital = INITIAL_CAPITAL
    monthly_log: list[dict] = []

    for r_count, n_idx in enumerate(rebalance_indices, 1):
        rebalance_date = nifty_df.iloc[n_idx]["Date"]
        nifty_close_so_far = nifty_df.iloc[: n_idx + 1]["Close"]

        # Regime gates
        breadth = compute_breadth(histories, rebalance_date)
        vix_idx = vix_series.index.get_indexer([rebalance_date], method="ffill")[0]
        vix_value = float(vix_series.iloc[vix_idx]) if vix_idx >= 0 and not np.isnan(vix_series.iloc[vix_idx]) else 15.0
        vix_history = vix_series.iloc[: max(0, vix_idx) + 1]
        regime = assess_regime(nifty_close_so_far, breadth, vix_value, vix_history)

        # DD circuit breaker
        dd_mult = position_size_multiplier(equity_curve)
        deploy = regime.deployment_pct * dd_mult

        # Score every symbol
        scored: list[tuple[str, float, int]] = []
        for sym, df in histories.items():
            idxs = df.index[df["Date"] <= rebalance_date]
            if len(idxs) < WARMUP_BARS:
                continue
            local_idx = int(idxs[-1])
            sub_close = df.iloc[: local_idx + 1]["Close"]
            score = momentum_score_v2(sub_close)
            if score is None:
                continue
            # Trend gate: above own 200 DMA
            if df.iloc[local_idx]["Close"] <= df.iloc[local_idx - 199 : local_idx + 1]["Close"].mean():
                continue
            scored.append((sym, score, local_idx))

        scored.sort(key=lambda x: x[1], reverse=True)
        ranked = {sym: rank + 1 for rank, (sym, _, _) in enumerate(scored)}
        target_picks = [s for s, _, _ in scored[:TOP_N_PICKS]] if deploy > 0 else []

        # Step 1: exit anything not in target list OR exit-signaled
        for sym in list(open_positions.keys()):
            df = histories[sym]
            pos = open_positions[sym]
            idxs = df.index[df["Date"] <= rebalance_date]
            if len(idxs) == 0:
                continue
            current_idx = int(idxs[-1])
            current_rank = ranked.get(sym)

            exit_sig = check_exit_v2(
                df_so_far=df.iloc[: current_idx + 1],
                entry_price=pos["entry_price"],
                entry_idx=pos["entry_idx"],
                current_idx=current_idx,
                current_rank=current_rank,
            )
            # Forced exit if regime says no deployment OR symbol no longer in target
            forced = exit_sig.triggered or sym not in target_picks or deploy == 0

            if forced:
                exit_price = exit_sig.exit_price if exit_sig.triggered else float(df.iloc[current_idx]["Close"])
                gross_ret = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
                net_ret = gross_ret - ROUND_TRIP_COST_PCT
                trades.append({
                    "symbol": sym,
                    "entry_date": str(pos["entry_date"].date()),
                    "exit_date": str(rebalance_date.date()),
                    "entry": round(pos["entry_price"], 2),
                    "exit": round(exit_price, 2),
                    "gross_return_pct": round(gross_ret, 2),
                    "net_return_pct": round(net_ret, 2),
                    "exit_reason": exit_sig.reason if exit_sig.triggered else ("not in top N" if sym not in target_picks else "regime off"),
                    "bars_held": current_idx - pos["entry_idx"],
                })
                # Apply to equity (equal-weight slice)
                slot_capital = capital / TOP_N_PICKS
                capital += slot_capital * (net_ret / 100)
                del open_positions[sym]

        # Step 2: open new positions for target picks not already held
        if deploy > 0:
            for sym in target_picks:
                if sym in open_positions:
                    continue
                df = histories[sym]
                idxs = df.index[df["Date"] <= rebalance_date]
                if len(idxs) == 0:
                    continue
                # Buy at next-day open if available
                local_idx = int(idxs[-1])
                if local_idx + 1 >= len(df):
                    continue
                entry_price = float(df.iloc[local_idx + 1]["Open"])
                if entry_price <= 0:
                    continue
                open_positions[sym] = {
                    "entry_price": entry_price,
                    "entry_idx": local_idx + 1,
                    "entry_date": df.iloc[local_idx + 1]["Date"],
                }

        equity_curve.append(capital)
        monthly_log.append({
            "date": str(rebalance_date.date()),
            "regime_deploy_pct": round(deploy * 100, 1),
            "regime_reason": regime.reason,
            "breadth_pct": round(breadth, 1),
            "vix": round(vix_value, 2),
            "open_positions": len(open_positions),
            "equity": round(capital, 0),
        })

        if r_count % 12 == 0:
            logger.info(f"  Rebalance {r_count}/{len(rebalance_indices)} ({rebalance_date.date()}): "
                        f"deploy={deploy*100:.0f}%, positions={len(open_positions)}, equity={capital:,.0f}")

    # Final liquidation at last bar
    last_idx = len(nifty_df) - 1
    last_date = nifty_df.iloc[last_idx]["Date"]
    for sym, pos in list(open_positions.items()):
        df = histories[sym]
        idxs = df.index[df["Date"] <= last_date]
        if len(idxs) == 0:
            continue
        exit_price = float(df.iloc[int(idxs[-1])]["Close"])
        gross = (exit_price - pos["entry_price"]) / pos["entry_price"] * 100
        net = gross - ROUND_TRIP_COST_PCT
        trades.append({
            "symbol": sym,
            "entry_date": str(pos["entry_date"].date()),
            "exit_date": str(last_date.date()),
            "entry": round(pos["entry_price"], 2),
            "exit": round(exit_price, 2),
            "gross_return_pct": round(gross, 2),
            "net_return_pct": round(net, 2),
            "exit_reason": "end of backtest",
            "bars_held": int(idxs[-1]) - pos["entry_idx"],
        })
        capital += (capital / TOP_N_PICKS) * (net / 100)
        del open_positions[sym]
    equity_curve.append(capital)

    # ---- Metrics ----
    if not trades:
        logger.error("No trades produced.")
        return

    df_t = pd.DataFrame(trades)
    winners = df_t[df_t["net_return_pct"] > 0]
    losers = df_t[df_t["net_return_pct"] <= 0]
    win_rate = len(winners) / len(df_t) * 100
    avg_w = winners["net_return_pct"].mean() if len(winners) else 0
    avg_l = losers["net_return_pct"].mean() if len(losers) else 0
    total_ret = (capital / INITIAL_CAPITAL - 1) * 100

    eq = pd.Series(equity_curve)
    peak = eq.cummax()
    max_dd = ((eq - peak) / peak * 100).min()

    # Years for CAGR
    years = (nifty_df.iloc[last_idx]["Date"] - nifty_df.iloc[WARMUP_BARS]["Date"]).days / 365.25
    cagr = ((capital / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0

    pf = (winners["net_return_pct"].sum() / abs(losers["net_return_pct"].sum())) if len(losers) and losers["net_return_pct"].sum() != 0 else float("inf")

    # Nifty buy-hold benchmark over same window
    nifty_bh = (nifty_df.iloc[last_idx]["Close"] / nifty_df.iloc[WARMUP_BARS]["Close"] - 1) * 100
    nifty_cagr = ((nifty_df.iloc[last_idx]["Close"] / nifty_df.iloc[WARMUP_BARS]["Close"]) ** (1 / years) - 1) * 100

    summary = {
        "generated_at": datetime.now().isoformat(),
        "version": "v2",
        "config": {
            "start_date": START_DATE,
            "end_date": END_DATE,
            "initial_capital": INITIAL_CAPITAL,
            "top_n_picks": TOP_N_PICKS,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "universe_size": len(histories),
            "rebalance": "monthly",
            "exits": "trailing max(20d low, 50EMA), -15% hard stop, rank>30, 90d time stop",
        },
        "metrics": {
            "total_trades": len(df_t),
            "winners": int(len(winners)),
            "losers": int(len(losers)),
            "win_rate_pct": round(win_rate, 2),
            "avg_winner_pct": round(avg_w, 2),
            "avg_loser_pct": round(avg_l, 2),
            "expectancy_pct": round(df_t["net_return_pct"].mean(), 2),
            "profit_factor": round(pf, 2) if pf != float("inf") else None,
            "total_return_pct": round(total_ret, 2),
            "cagr_pct": round(cagr, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "nifty_buyhold_total_pct": round(nifty_bh, 2),
            "nifty_buyhold_cagr_pct": round(nifty_cagr, 2),
            "alpha_vs_nifty_cagr_pct": round(cagr - nifty_cagr, 2),
            "years": round(years, 2),
        },
        "monthly_log": monthly_log,
        "trades": trades,
        "caveats": [
            "Universe is current Nifty 200 minus top 20; survivorship bias remains.",
            "Breadth proxy uses backtest universe (not actual Nifty 200 constituents).",
            "VIX before 2018 may be patchy; gate degrades to default 15.",
            "Costs assume 0.4% round-trip; actual will vary.",
            "Backtest does NOT model dividends, taxes, or corporate actions.",
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(f"V2 backtest done -> {OUT_PATH}")
    logger.info(
        f"  Trades: {len(df_t)} | Win rate: {win_rate:.1f}% | CAGR: {cagr:.1f}% | "
        f"Nifty CAGR: {nifty_cagr:.1f}% | Alpha: {cagr - nifty_cagr:+.1f}% | Max DD: {max_dd:.1f}%"
    )


if __name__ == "__main__":
    backtest_v2()

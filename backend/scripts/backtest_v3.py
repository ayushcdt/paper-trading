"""
V3 adaptive backtest: regime classifier switches between 4 strategy variants
each monthly rebalance. Outputs per-regime performance attribution.

Runs on same data-loading harness as backtest_v2 but routes decisions through
the adaptive engine.
"""

from __future__ import annotations

import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import numpy as np
import pandas as pd
import yfinance as yf
from logzero import logger

warnings.filterwarnings("ignore")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adaptive.regime import classify_regime, compute_breadth
from adaptive.variants import build_variants, REGIME_TO_VARIANT
from strategy_v2 import TOP_20_DROP
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP


START_DATE = os.environ.get("BACKTEST_START", "2015-01-01")
END_DATE   = os.environ.get("BACKTEST_END",   "2025-12-31")
OUT_FILE   = os.environ.get("BACKTEST_OUT",   "backtest_v3_results.json")
INITIAL_CAPITAL = 1_000_000
WARMUP_BARS = 252
ROUND_TRIP_COST_PCT = 0.4

OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / OUT_FILE


# ---------- Data -------------------------------------------------------------

def build_universe() -> list[str]:
    raw = sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP))
    return [s for s in raw if s not in TOP_20_DROP]


def fetch_history(symbols: list[str]):
    logger.info(f"Downloading {START_DATE} -> {END_DATE} ...")
    tickers = [f"{s}.NS" for s in symbols] + ["^NSEI", "^INDIAVIX"]
    chunks = []
    cs = 10
    for i in range(0, len(tickers), cs):
        batch = tickers[i : i + cs]
        df = yf.download(batch, start=START_DATE, end=END_DATE, progress=False, auto_adjust=True, threads=False, group_by="ticker")
        if df is not None and not df.empty:
            chunks.append(df)
        logger.info(f"  chunk {i // cs + 1}/{(len(tickers) + cs - 1) // cs}")
    if not chunks:
        raise SystemExit("no data")
    big = pd.concat(chunks, axis=1)
    big = big.loc[:, ~big.columns.duplicated()]

    def get(tk):
        if tk not in big.columns.get_level_values(0):
            return None
        sub = big[tk].copy().dropna(how="all")
        return sub if not sub.empty else None

    ns = get("^NSEI")
    nifty_close = ns["Close"].dropna() if ns is not None else pd.Series(dtype=float)
    nifty_df = nifty_close.reset_index()
    nifty_df.columns = ["Date", "Close"]

    vs = get("^INDIAVIX")
    vix_series = vs["Close"].dropna() if vs is not None else pd.Series(dtype=float)

    histories = {}
    for s in symbols:
        sub = get(f"{s}.NS")
        if sub is None:
            continue
        df = sub[["Open", "High", "Low", "Close", "Volume"]].dropna()
        if len(df) < WARMUP_BARS + 100:
            continue
        df = df.reset_index()
        df.columns = ["Date", "Open", "High", "Low", "Close", "Volume"]
        histories[s] = df
    return nifty_df, histories, vix_series


def monthly_starts(nifty_df: pd.DataFrame, warmup: int) -> list[int]:
    nifty_df = nifty_df.copy()
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    nifty_df["YM"] = nifty_df["Date"].dt.to_period("M")
    starts = nifty_df.drop_duplicates("YM", keep="first").index.tolist()
    return [i for i in starts if i >= warmup]


# ---------- Backtest ---------------------------------------------------------

def backtest_v3():
    universe = build_universe()
    logger.info(f"Universe: {len(universe)} symbols (top-20 mega-caps excluded)")
    nifty_df, histories, vix_series = fetch_history(universe)
    nifty_df["Date"] = pd.to_datetime(nifty_df["Date"])
    logger.info(f"Loaded: {len(histories)} symbol histories, Nifty={len(nifty_df)}, VIX={len(vix_series)}")

    variants = build_variants()
    rebalances = monthly_starts(nifty_df, WARMUP_BARS)
    logger.info(f"V3 adaptive backtest: {len(rebalances)} monthly rebalances")

    open_positions: dict[str, dict] = {}
    trades: list[dict] = []
    equity_curve = [INITIAL_CAPITAL]
    capital = INITIAL_CAPITAL
    monthly_log = []

    regime_trade_counts = {r: 0 for r in ["BULL_LOW_VOL", "BULL_HIGH_VOL", "RANGE", "BEAR"]}
    regime_pnl = {r: 0.0 for r in regime_trade_counts}

    for r_count, n_idx in enumerate(rebalances, 1):
        rebalance_date = nifty_df.iloc[n_idx]["Date"]
        nifty_close_so_far = nifty_df.iloc[: n_idx + 1]["Close"]

        breadth = compute_breadth(histories, rebalance_date)
        vix_idx = vix_series.index.get_indexer([rebalance_date], method="ffill")[0]
        vix_value = float(vix_series.iloc[vix_idx]) if vix_idx >= 0 and not np.isnan(vix_series.iloc[vix_idx]) else 15.0
        vix_history = vix_series.iloc[: max(0, vix_idx) + 1]

        regime_asmt = classify_regime(nifty_close_so_far, breadth, vix_value, vix_history)
        variant_name = REGIME_TO_VARIANT[regime_asmt.regime.value]
        variant = variants[variant_name]

        # Score universe via the active variant
        picks = variant.pick(histories, rebalance_date, universe) if regime_asmt.deploy_pct > 0 else []
        target_symbols = [p.symbol for p in picks]
        picks_by_symbol = {p.symbol: p for p in picks}
        ranked = {p.symbol: p.rank for p in picks}

        # --- Close existing positions: forced (variant changed) or exit signal
        for sym in list(open_positions.keys()):
            pos = open_positions[sym]
            df = histories[sym]
            idxs = df.index[df["Date"] <= rebalance_date]
            if len(idxs) == 0:
                continue
            cur_idx = int(idxs[-1])
            opening_variant = variants.get(pos["variant"], variant)
            exit_sig = opening_variant.check_exit(df, pos["entry_price"], pos["entry_idx"], cur_idx, ranked.get(sym))
            regime_changed = pos["variant"] != variant_name
            forced = exit_sig.triggered or regime_changed or sym not in target_symbols or regime_asmt.deploy_pct == 0

            if forced:
                ep = exit_sig.exit_price if exit_sig.triggered else float(df.iloc[cur_idx]["Close"])
                gross = (ep - pos["entry_price"]) / pos["entry_price"] * 100
                net = gross - ROUND_TRIP_COST_PCT
                trades.append({
                    "symbol": sym,
                    "entry_date": str(pos["entry_date"].date()),
                    "exit_date": str(rebalance_date.date()),
                    "entry": round(pos["entry_price"], 2),
                    "exit": round(ep, 2),
                    "net_return_pct": round(net, 2),
                    "exit_reason": exit_sig.reason if exit_sig.triggered else ("regime switch" if regime_changed else "not in top N"),
                    "variant": pos["variant"],
                    "regime_at_entry": pos.get("regime_at_entry"),
                    "bars_held": cur_idx - pos["entry_idx"],
                })
                # FIXED: use the notional recorded at OPEN, not current variant's max_picks
                pnl = pos["notional"] * (net / 100)
                capital += pnl
                regime_pnl[pos.get("regime_at_entry", "BULL_LOW_VOL")] += pnl
                regime_trade_counts[pos.get("regime_at_entry", "BULL_LOW_VOL")] += 1
                del open_positions[sym]

        # --- Open new positions for target picks
        if regime_asmt.deploy_pct > 0 and variant_name != "defensive":
            # Equal-weight across variant.max_picks slots; notional locked at open time
            n_slots = max(1, variant.max_picks)
            slot_notional = capital / n_slots
            for sym in target_symbols:
                if sym in open_positions:
                    continue
                df = histories[sym]
                idxs = df.index[df["Date"] <= rebalance_date]
                if len(idxs) == 0:
                    continue
                li = int(idxs[-1])
                if li + 1 >= len(df):
                    continue
                entry_price = float(df.iloc[li + 1]["Open"])
                if entry_price <= 0:
                    continue
                open_positions[sym] = {
                    "entry_price": entry_price,
                    "entry_idx": li + 1,
                    "entry_date": df.iloc[li + 1]["Date"],
                    "variant": variant_name,
                    "regime_at_entry": regime_asmt.regime.value,
                    "notional": slot_notional,
                }

        equity_curve.append(capital)
        monthly_log.append({
            "date": str(rebalance_date.date()),
            "regime": regime_asmt.regime.value,
            "variant": variant_name,
            "deploy_pct": round(regime_asmt.deploy_pct * 100, 1),
            "reason": regime_asmt.reason,
            "breadth": round(breadth, 1),
            "vix": round(vix_value, 2),
            "open_positions": len(open_positions),
            "equity": round(capital, 0),
        })
        if r_count % 12 == 0:
            logger.info(f"  Rebalance {r_count}/{len(rebalances)} ({rebalance_date.date()}): "
                        f"{regime_asmt.regime.value} -> {variant_name}, positions={len(open_positions)}, equity={capital:,.0f}")

    # Close remaining at final bar
    last_idx = len(nifty_df) - 1
    last_date = nifty_df.iloc[last_idx]["Date"]
    for sym, pos in list(open_positions.items()):
        df = histories[sym]
        idxs = df.index[df["Date"] <= last_date]
        if len(idxs) == 0:
            continue
        ep = float(df.iloc[int(idxs[-1])]["Close"])
        gross = (ep - pos["entry_price"]) / pos["entry_price"] * 100
        net = gross - ROUND_TRIP_COST_PCT
        trades.append({
            "symbol": sym, "entry_date": str(pos["entry_date"].date()),
            "exit_date": str(last_date.date()), "entry": round(pos["entry_price"], 2),
            "exit": round(ep, 2), "net_return_pct": round(net, 2),
            "exit_reason": "end of backtest", "variant": pos["variant"],
            "regime_at_entry": pos.get("regime_at_entry"),
            "bars_held": int(idxs[-1]) - pos["entry_idx"],
        })
        pnl = pos["notional"] * (net / 100)
        capital += pnl
        regime_pnl[pos.get("regime_at_entry", "BULL_LOW_VOL")] += pnl
        regime_trade_counts[pos.get("regime_at_entry", "BULL_LOW_VOL")] += 1
    equity_curve.append(capital)

    # Metrics
    if not trades:
        logger.error("No trades.")
        return
    df_t = pd.DataFrame(trades)
    winners = df_t[df_t["net_return_pct"] > 0]
    losers = df_t[df_t["net_return_pct"] <= 0]
    win_rate = len(winners) / len(df_t) * 100
    total_ret = (capital / INITIAL_CAPITAL - 1) * 100
    eq = pd.Series(equity_curve)
    peak = eq.cummax()
    max_dd = ((eq - peak) / peak * 100).min()
    years = (nifty_df.iloc[last_idx]["Date"] - nifty_df.iloc[WARMUP_BARS]["Date"]).days / 365.25
    cagr = ((capital / INITIAL_CAPITAL) ** (1 / years) - 1) * 100 if years > 0 else 0
    pf = (winners["net_return_pct"].sum() / abs(losers["net_return_pct"].sum())) if len(losers) and losers["net_return_pct"].sum() != 0 else float("inf")
    nifty_bh = (nifty_df.iloc[last_idx]["Close"] / nifty_df.iloc[WARMUP_BARS]["Close"] - 1) * 100
    nifty_cagr = ((nifty_df.iloc[last_idx]["Close"] / nifty_df.iloc[WARMUP_BARS]["Close"]) ** (1 / years) - 1) * 100

    # Per-regime attribution
    regime_attrib = {}
    for reg, pnl in regime_pnl.items():
        trades_in = [t for t in trades if t.get("regime_at_entry") == reg]
        if trades_in:
            wins = [t for t in trades_in if t["net_return_pct"] > 0]
            regime_attrib[reg] = {
                "trades": len(trades_in),
                "win_rate_pct": round(len(wins) / len(trades_in) * 100, 1),
                "avg_return_pct": round(sum(t["net_return_pct"] for t in trades_in) / len(trades_in), 2),
                "total_pnl_inr": round(pnl, 0),
            }
        else:
            regime_attrib[reg] = {"trades": 0, "win_rate_pct": 0, "avg_return_pct": 0, "total_pnl_inr": 0}

    summary = {
        "generated_at": datetime.now().isoformat(),
        "version": "v3-adaptive",
        "config": {
            "start_date": START_DATE, "end_date": END_DATE,
            "initial_capital": INITIAL_CAPITAL, "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "universe_size": len(histories), "rebalance": "monthly",
            "regime_variants": REGIME_TO_VARIANT,
        },
        "metrics": {
            "total_trades": len(df_t),
            "winners": int(len(winners)), "losers": int(len(losers)),
            "win_rate_pct": round(win_rate, 2),
            "avg_winner_pct": round(winners["net_return_pct"].mean(), 2) if len(winners) else 0,
            "avg_loser_pct": round(losers["net_return_pct"].mean(), 2) if len(losers) else 0,
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
        "regime_attribution": regime_attrib,
        "monthly_log": monthly_log,
        "trades": trades,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(f"V3 backtest done -> {OUT_PATH}")
    logger.info(
        f"  Trades: {len(df_t)} | WR: {win_rate:.1f}% | CAGR: {cagr:.1f}% | "
        f"Nifty: {nifty_cagr:.1f}% | Alpha: {cagr - nifty_cagr:+.1f}% | MDD: {max_dd:.1f}%"
    )
    logger.info("  Regime attribution:")
    for reg, a in regime_attrib.items():
        if a["trades"]:
            logger.info(f"    {reg:15s}: {a['trades']:3d} trades, WR {a['win_rate_pct']:.0f}%, avg {a['avg_return_pct']:+.2f}%")


if __name__ == "__main__":
    backtest_v3()

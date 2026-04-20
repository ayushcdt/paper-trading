"""
Walk-forward backtest of the stock_picker on historical data.

For each rebalance date in the test window:
  1. Use ONLY price data up to that date (no look-ahead)
  2. Run the picker on the universe -> top N picks
  3. Buy at next-day open, hold for HOLD_DAYS, exit at close
  4. Apply round-trip cost (slippage + brokerage + STT estimate)

Outputs:
  data/backtest_results.json -- summary metrics + per-trade list

Limitations (be honest about these):
  - Survivorship bias: universe is current Nifty 50 + Next 50 + Midcap.
    Stocks that were in those indices but later dropped are NOT modeled.
  - Fundamentals: yfinance.info is a CURRENT snapshot, not point-in-time.
    Quality scores in backtest = live values, not what you'd have seen then.
  - We use Angel daily candles -- so timeframe is end-of-day, not intraday.
  - No dividend / corporate action adjustments.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from logzero import logger

# Make backend/ importable when run as `python scripts/backtest.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_fetcher import get_fetcher, SYMBOL_TOKENS  # noqa: E402
from stock_picker import (  # noqa: E402
    NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP,
    calculate_momentum_score, calculate_technical_score, calculate_entry_exit,
)


# ---- Config -----------------------------------------------------------------
BACKTEST_DAYS = 365            # how far back to test
REBALANCE_EVERY_DAYS = 5       # weekly rebalance
HOLD_DAYS = 10                 # exit each pick after N trading days
TOP_N_PICKS = 5                # pick top N each rebalance
ROUND_TRIP_COST_PCT = 0.4      # slippage + brokerage + STT (Indian intraday equity)
WARMUP_BARS = 200              # need 200 bars for EMA200

OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "backtest_results.json"


# ---- Data prep --------------------------------------------------------------

def load_universe_history() -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """
    Returns (nifty_df, {symbol: df}). Each df is daily OHLCV indexed by Date,
    sorted ascending. Symbols missing data or with too few bars are skipped.
    """
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            raise SystemExit("Angel login failed; cannot fetch history for backtest")

    days = BACKTEST_DAYS + WARMUP_BARS + 50
    logger.info(f"Fetching {days} days of history per symbol ...")

    nifty_df = fetcher.get_historical_data("NIFTY", interval="ONE_DAY", days=days)
    if nifty_df.empty:
        raise SystemExit("Nifty history fetch failed")
    nifty_df = nifty_df.sort_values("Date").reset_index(drop=True)

    universe = sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP))
    available = [s for s in universe if s in SYMBOL_TOKENS]
    logger.info(f"Universe: {len(available)}/{len(universe)} symbols have Angel tokens")

    histories: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(available, 1):
        df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=days)
        if df.empty or len(df) < WARMUP_BARS + 30:
            logger.debug(f"  {sym}: insufficient history ({len(df)} bars)")
            continue
        histories[sym] = df.sort_values("Date").reset_index(drop=True)
        if i % 25 == 0:
            logger.info(f"  Fetched {i}/{len(available)} ...")
    logger.info(f"Loaded {len(histories)} usable symbol histories")
    return nifty_df, histories


# ---- Backtest loop ----------------------------------------------------------

def score_symbol_at(df: pd.DataFrame, nifty_df: pd.DataFrame, end_idx: int) -> float | None:
    """Score this symbol using ONLY data up to end_idx (no look-ahead)."""
    sub = df.iloc[: end_idx + 1].copy()
    nsub = nifty_df.iloc[: nifty_df.index[nifty_df["Date"] <= df.iloc[end_idx]["Date"]][-1] + 1].copy()
    if len(sub) < WARMUP_BARS or len(nsub) < WARMUP_BARS:
        return None
    try:
        mom = calculate_momentum_score(sub, nsub)
        tech = calculate_technical_score(sub)
    except Exception:
        return None
    if mom["score"] == 0 and tech["score"] == 0:
        return None
    # Backtest uses momentum + technical only (no fundamentals -- those are not point-in-time)
    return mom["score"] * 0.5 + tech["score"] * 0.5


def find_index_at_or_after(df: pd.DataFrame, target_date: pd.Timestamp) -> int | None:
    matches = df.index[df["Date"] >= target_date]
    return int(matches[0]) if len(matches) else None


def backtest():
    nifty_df, histories = load_universe_history()

    # Build rebalance dates from Nifty's trading calendar
    last_idx = len(nifty_df) - 1
    first_test_idx = WARMUP_BARS
    rebalance_indices = list(range(first_test_idx, last_idx - HOLD_DAYS, REBALANCE_EVERY_DAYS))
    logger.info(f"Backtest: {len(rebalance_indices)} rebalances over {BACKTEST_DAYS}d window")

    trades: list[dict] = []

    for r_idx, n_idx in enumerate(rebalance_indices, 1):
        rebalance_date = nifty_df.iloc[n_idx]["Date"]

        # Score every symbol as of rebalance_date
        scored: list[tuple[str, float, int]] = []  # (symbol, score, idx_in_its_df)
        for sym, df in histories.items():
            local_idx_arr = df.index[df["Date"] <= rebalance_date]
            if len(local_idx_arr) == 0:
                continue
            local_idx = int(local_idx_arr[-1])
            s = score_symbol_at(df, nifty_df, local_idx)
            if s is not None and s >= 50:
                scored.append((sym, s, local_idx))

        scored.sort(key=lambda x: x[1], reverse=True)
        picks = scored[:TOP_N_PICKS]

        # Simulate each pick: buy next-day open, hold HOLD_DAYS, exit close
        for sym, score, local_idx in picks:
            df = histories[sym]
            entry_idx = local_idx + 1
            exit_idx = entry_idx + HOLD_DAYS - 1
            if exit_idx >= len(df):
                continue
            entry_price = float(df.iloc[entry_idx]["Open"])
            exit_price = float(df.iloc[exit_idx]["Close"])
            gross_ret = (exit_price - entry_price) / entry_price * 100
            net_ret = gross_ret - ROUND_TRIP_COST_PCT

            trades.append({
                "rebalance_date": str(rebalance_date.date()),
                "symbol": sym,
                "score": round(score, 1),
                "entry_date": str(df.iloc[entry_idx]["Date"].date()),
                "exit_date": str(df.iloc[exit_idx]["Date"].date()),
                "entry": round(entry_price, 2),
                "exit": round(exit_price, 2),
                "gross_return_pct": round(gross_ret, 2),
                "net_return_pct": round(net_ret, 2),
            })

        if r_idx % 10 == 0:
            logger.info(f"  Rebalance {r_idx}/{len(rebalance_indices)} done; {len(trades)} trades so far")

    # Compute metrics
    if not trades:
        logger.error("No trades generated -- check universe / scoring threshold")
        return

    df_t = pd.DataFrame(trades)
    winners = df_t[df_t["net_return_pct"] > 0]
    losers = df_t[df_t["net_return_pct"] <= 0]
    win_rate = len(winners) / len(df_t) * 100
    avg_winner = winners["net_return_pct"].mean() if len(winners) else 0
    avg_loser = losers["net_return_pct"].mean() if len(losers) else 0
    profit_factor = (winners["net_return_pct"].sum() / abs(losers["net_return_pct"].sum())) if len(losers) and losers["net_return_pct"].sum() != 0 else float("inf")

    # Equity curve assuming equal-weight 1% risk per trade (rough)
    df_t = df_t.sort_values("entry_date").reset_index(drop=True)
    cum_return = 1.0
    equity = [cum_return]
    for r in df_t["net_return_pct"]:
        cum_return *= 1 + (r / 100) / TOP_N_PICKS  # diversified across N picks
        equity.append(cum_return)
    peak = pd.Series(equity).cummax()
    drawdown = ((pd.Series(equity) - peak) / peak * 100).min()

    summary = {
        "generated_at": datetime.now().isoformat(),
        "config": {
            "backtest_days": BACKTEST_DAYS,
            "rebalance_every_days": REBALANCE_EVERY_DAYS,
            "hold_days": HOLD_DAYS,
            "top_n_picks": TOP_N_PICKS,
            "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
            "universe_size": len(histories),
        },
        "metrics": {
            "total_trades": len(df_t),
            "winners": int(len(winners)),
            "losers": int(len(losers)),
            "win_rate_pct": round(win_rate, 2),
            "avg_winner_pct": round(avg_winner, 2),
            "avg_loser_pct": round(avg_loser, 2),
            "expectancy_pct": round(df_t["net_return_pct"].mean(), 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
            "total_return_pct": round((cum_return - 1) * 100, 2),
            "max_drawdown_pct": round(drawdown, 2),
        },
        "trades": trades,
        "caveats": [
            "Survivorship bias: universe = current index members; delisted/dropped stocks not modeled.",
            "Fundamentals not used (yfinance returns current snapshot, not point-in-time).",
            "EOD bars only -- intraday slippage may be larger than the 0.4% round-trip estimate.",
            "No dividend / corporate-action adjustment.",
        ],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    logger.info(f"Backtest complete -> {OUT_PATH}")
    logger.info(
        f"  Trades: {len(df_t)} | Win rate: {win_rate:.1f}% | Expectancy: {df_t['net_return_pct'].mean():.2f}% | "
        f"Total: {(cum_return - 1) * 100:.1f}% | Max DD: {drawdown:.1f}%"
    )


if __name__ == "__main__":
    backtest()

"""
Backtest harness — research-grade, used to validate strategies + new mechanisms
BEFORE shipping them to shadow/live.

Core abstractions:
  Strategy        — plug-in interface every strategy implements
  BacktestEngine  — walks the strategy through historical bars, applies costs,
                     records trades + equity curve
  BacktestResult  — full metrics: CAGR, Sharpe, Sortino, Calmar, MaxDD,
                     win rate, profit factor, per-regime attribution,
                     bootstrap CIs on key metrics

Design choices:
  - Single source of price data: bars.db (same as live system; no yfinance drift)
  - Walk-forward by default (no in-sample-only fooling-yourself)
  - Realistic transaction costs (0.4% round-trip default)
  - Bootstrap confidence intervals on monthly returns
  - Pluggable mechanisms — test V3 alone, V3+trailing, V3+swap, etc independently

Usage:
    from research.harness import BacktestEngine
    from research.strategies.v3_baseline import V3BaselineStrategy

    eng = BacktestEngine(
        strategy=V3BaselineStrategy(),
        start="2021-01-01", end="2026-04-01",
        universe="nifty500",
    )
    result = eng.run()
    result.print_summary()
    result.save("data/research/backtest_v3_baseline.json")
"""
from __future__ import annotations

import json
import math
import statistics
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Protocol

import numpy as np
import pandas as pd

# Project imports — work both when imported as module and when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data_store import get_bars
from data_fetcher import SYMBOL_TOKENS


# ----------------------------------------------------------------------------
# Strategy interface
# ----------------------------------------------------------------------------

@dataclass
class MarketState:
    """Snapshot of market data passed to strategy on rebalance."""
    date: pd.Timestamp
    histories: dict[str, pd.DataFrame]   # symbol -> daily bars up to and including `date`
    nifty_history: pd.DataFrame
    open_positions: dict[str, "Position"]
    equity: float
    capital_initial: float


@dataclass
class Position:
    symbol: str
    qty: int
    entry_date: pd.Timestamp
    entry_price: float
    entry_score: float = 0.0
    target: float = 0.0
    stop: float = 0.0
    variant: str = ""
    regime_at_entry: str = ""
    notes: dict = field(default_factory=dict)


@dataclass
class Decision:
    action: str             # "OPEN", "CLOSE", "ADJUST_TARGET", "ADJUST_STOP"
    symbol: str
    qty: int = 0
    price: Optional[float] = None   # None = market at next bar's open
    new_target: Optional[float] = None
    new_stop: Optional[float] = None
    reason: str = ""


class Strategy(Protocol):
    name: str

    def initialize(self, universe: list[str], capital: float) -> None: ...
    def on_rebalance(self, state: MarketState) -> list[Decision]: ...
    def on_mark(self, state: MarketState) -> list[Decision]:
        """Called every bar between rebalances. Default: no action."""
        return []


# ----------------------------------------------------------------------------
# Universe loaders
# ----------------------------------------------------------------------------

def load_universe(name: str) -> list[str]:
    """Load symbol list. 'nifty500' = all stocks in SYMBOL_TOKENS (excludes indices)."""
    if name == "nifty500":
        return sorted(s for s, t in SYMBOL_TOKENS.items() if not t.startswith("999"))
    elif name == "nifty50":
        from stock_picker import NIFTY_50
        return sorted(NIFTY_50)
    elif name == "nifty100":
        from stock_picker import NIFTY_50, NIFTY_NEXT_50
        return sorted(set(NIFTY_50 + NIFTY_NEXT_50))
    raise ValueError(f"Unknown universe: {name}")


# ----------------------------------------------------------------------------
# Engine
# ----------------------------------------------------------------------------

@dataclass
class TradeRecord:
    symbol: str
    open_date: pd.Timestamp
    close_date: pd.Timestamp
    entry_price: float
    exit_price: float
    qty: int
    notional: float
    gross_pnl: float
    cost: float
    net_pnl: float
    return_pct: float
    holding_days: int
    open_reason: str
    close_reason: str
    variant: str = ""
    regime_at_entry: str = ""


class BacktestEngine:
    def __init__(
        self,
        strategy: Strategy,
        start: str,
        end: str,
        universe: str | list[str] = "nifty500",
        capital: float = 1_000_000.0,
        round_trip_cost_pct: float = 0.4,
        rebalance: str = "monthly",   # "monthly" | "weekly" | "daily"
        max_positions: int = 5,
    ):
        self.strategy = strategy
        self.start = pd.Timestamp(start)
        self.end = pd.Timestamp(end)
        self.universe = (load_universe(universe) if isinstance(universe, str) else universe)
        self.capital = capital
        self.cost_pct = round_trip_cost_pct
        self.rebalance = rebalance
        self.max_positions = max_positions

    # ---------- Data loading -------------------------------------------------

    def _load_all_bars(self) -> dict[str, pd.DataFrame]:
        """Load all available bars up to self.end. Don't strip pre-start bars —
        strategies need warmup data (e.g., momentum_agg uses 252d lookback).
        Trade-decision dates are restricted to [self.start, self.end] separately."""
        out = {}
        for sym in self.universe:
            df = get_bars(sym, n_days=5000)
            if len(df) < 100:
                continue
            df = df.copy()
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.sort_values("Date").drop_duplicates(subset=["Date"]).reset_index(drop=True)
            df = df[df["Date"] <= self.end]
            if len(df) >= 100:
                out[sym] = df
        return out

    def _rebalance_dates(self, all_dates: pd.DatetimeIndex) -> list[pd.Timestamp]:
        if self.rebalance == "daily":
            return list(all_dates)
        df = pd.DataFrame({"date": all_dates})
        df["yyyymm"] = df["date"].dt.strftime("%Y-%m")
        df["yyyyww"] = df["date"].dt.strftime("%Y-%U")
        if self.rebalance == "monthly":
            return list(df.groupby("yyyymm")["date"].first())
        if self.rebalance == "weekly":
            return list(df.groupby("yyyyww")["date"].first())
        raise ValueError(f"unknown rebalance {self.rebalance}")

    # ---------- Run ----------------------------------------------------------

    def run(self) -> "BacktestResult":
        bars = self._load_all_bars()
        if not bars:
            raise SystemExit("No bars loaded for any universe symbol.")
        nifty = get_bars("NIFTY", n_days=5000).copy()
        nifty["Date"] = pd.to_datetime(nifty["Date"])
        nifty = nifty.sort_values("Date").drop_duplicates(subset=["Date"]).reset_index(drop=True)
        nifty_full = nifty[nifty["Date"] <= self.end]   # warmup-inclusive, used by strategy
        nifty_window = nifty[(nifty["Date"] >= self.start) & (nifty["Date"] <= self.end)]
        if len(nifty_window) < 20:
            raise SystemExit("NIFTY history insufficient in window")
        nifty = nifty_window  # for results comparison + benchmark
        # Master timeline = window dates only (decisions restricted to window)
        all_dates = pd.DatetimeIndex(nifty_window["Date"].values)
        rebal_dates = self._rebalance_dates(all_dates)

        self.strategy.initialize(list(bars.keys()), self.capital)

        positions: dict[str, Position] = {}
        trades: list[TradeRecord] = []
        equity_curve: list[tuple[pd.Timestamp, float]] = []
        cash = self.capital

        # Build a Date-indexed lookup for O(log n) per-day access in our engine,
        # but keep the original int-indexed df for strategies that use .iloc semantics.
        bars_indexed: dict[str, pd.DataFrame] = {sym: df.set_index("Date", drop=False) for sym, df in bars.items()}

        for d in all_dates:
            # Mark-to-market — current equity
            mtm_value = cash
            today_prices = {}
            for sym, pos in positions.items():
                if sym not in bars_indexed or d not in bars_indexed[sym].index:
                    today_prices[sym] = pos.entry_price   # stale fallback
                    mtm_value += pos.qty * pos.entry_price
                else:
                    px = bars_indexed[sym].at[d, "Close"]
                    today_prices[sym] = px
                    mtm_value += pos.qty * px
            equity_curve.append((d, mtm_value))

            # Apply stops/targets first
            for sym in list(positions.keys()):
                if sym not in bars_indexed or d not in bars_indexed[sym].index:
                    continue
                row = bars_indexed[sym].loc[d]
                pos = positions[sym]
                exit_price = None; reason = ""
                if pos.stop > 0 and row["Low"] <= pos.stop:
                    exit_price = pos.stop; reason = "stop hit"
                elif pos.target > 0 and row["High"] >= pos.target:
                    exit_price = pos.target; reason = "target hit"
                if exit_price is not None:
                    cash += self._close_and_record(positions, sym, exit_price, d, reason, trades)

            # Rebalance / on_mark — pass int-indexed histories with full warmup
            # (variant code uses .iloc semantics + needs 252d lookback)
            histories_until_d = {s: df[df["Date"] <= d].reset_index(drop=True) for s, df in bars.items() if len(df) and df["Date"].iloc[0] <= d <= df["Date"].iloc[-1]}
            state = MarketState(
                date=d,
                histories=histories_until_d,
                nifty_history=nifty_full[nifty_full["Date"] <= d],
                open_positions=dict(positions),
                equity=mtm_value,
                capital_initial=self.capital,
            )

            decisions = self.strategy.on_rebalance(state) if d in rebal_dates else self.strategy.on_mark(state)
            for dec in decisions:
                cash = self._apply_decision(dec, positions, bars_indexed, d, cash, trades)

        # Force-close all open at end
        last_d = all_dates[-1]
        for sym in list(positions.keys()):
            if sym in bars_indexed and last_d in bars_indexed[sym].index:
                exit_price = bars_indexed[sym].at[last_d, "Close"]
                cash += self._close_and_record(positions, sym, exit_price, last_d, "end of backtest", trades)

        return BacktestResult.from_run(
            strategy_name=self.strategy.name,
            start=self.start, end=self.end,
            initial_capital=self.capital,
            final_equity=equity_curve[-1][1] if equity_curve else self.capital,
            trades=trades,
            equity_curve=equity_curve,
            nifty=nifty,
            cost_pct=self.cost_pct,
        )

    def _apply_decision(self, dec: Decision, positions, bars_indexed, d, cash, trades) -> float:
        if dec.action == "OPEN":
            if dec.symbol not in bars_indexed or d not in bars_indexed[dec.symbol].index:
                return cash
            if dec.symbol in positions:
                return cash  # already open
            if len(positions) >= self.max_positions:
                return cash
            entry_price = dec.price if dec.price else bars_indexed[dec.symbol].at[d, "Close"]
            qty = dec.qty if dec.qty > 0 else int((cash / max(1, self.max_positions - len(positions))) / entry_price)
            if qty <= 0:
                return cash
            notional = qty * entry_price
            if notional > cash:
                qty = int(cash / entry_price)
                notional = qty * entry_price
                if qty <= 0:
                    return cash
            cash -= notional
            positions[dec.symbol] = Position(
                symbol=dec.symbol, qty=qty, entry_date=d, entry_price=entry_price,
                target=dec.new_target or 0.0, stop=dec.new_stop or 0.0,
                notes={"open_reason": dec.reason},
            )
        elif dec.action == "CLOSE":
            if dec.symbol not in positions:
                return cash
            exit_price = dec.price if dec.price else (
                bars_indexed[dec.symbol].at[d, "Close"] if dec.symbol in bars_indexed and d in bars_indexed[dec.symbol].index
                else positions[dec.symbol].entry_price
            )
            cash += self._close_and_record(positions, dec.symbol, exit_price, d, dec.reason, trades)
        elif dec.action == "ADJUST_TARGET":
            if dec.symbol in positions and dec.new_target is not None:
                positions[dec.symbol].target = dec.new_target
        elif dec.action == "ADJUST_STOP":
            if dec.symbol in positions and dec.new_stop is not None:
                positions[dec.symbol].stop = dec.new_stop
        return cash

    def _close_and_record(self, positions, sym, exit_price, d, reason, trades) -> float:
        pos = positions[sym]
        gross_pnl = (exit_price - pos.entry_price) * pos.qty
        notional = pos.qty * pos.entry_price
        cost = notional * (self.cost_pct / 100)
        net_pnl = gross_pnl - cost
        ret_pct = (net_pnl / notional) * 100 if notional else 0.0
        trades.append(TradeRecord(
            symbol=sym, open_date=pos.entry_date, close_date=d,
            entry_price=pos.entry_price, exit_price=exit_price, qty=pos.qty,
            notional=notional, gross_pnl=gross_pnl, cost=cost, net_pnl=net_pnl,
            return_pct=ret_pct,
            holding_days=int((d - pos.entry_date).days),
            open_reason=pos.notes.get("open_reason", ""),
            close_reason=reason,
            variant=pos.variant, regime_at_entry=pos.regime_at_entry,
        ))
        del positions[sym]
        return pos.qty * exit_price


# ----------------------------------------------------------------------------
# Result + metrics
# ----------------------------------------------------------------------------

@dataclass
class BacktestResult:
    strategy_name: str
    start: str
    end: str
    initial_capital: float
    final_equity: float
    metrics: dict
    per_regime: dict
    bootstrap_ci: dict
    trades: list[dict]
    equity_curve: list[tuple]
    nifty_curve: list[tuple]
    cost_pct: float

    @classmethod
    def from_run(cls, strategy_name, start, end, initial_capital, final_equity,
                 trades: list[TradeRecord], equity_curve, nifty: pd.DataFrame, cost_pct):
        # Build equity series
        eq_series = pd.Series([v for _, v in equity_curve], index=[d for d, _ in equity_curve])
        nifty_series = nifty.set_index("Date")["Close"]
        nifty_series = nifty_series.reindex(eq_series.index, method="ffill")

        # Daily returns
        daily_ret = eq_series.pct_change().dropna()
        nifty_ret = nifty_series.pct_change().dropna()

        # Aligned for comparison
        common = daily_ret.index.intersection(nifty_ret.index)
        daily_ret = daily_ret.loc[common]
        nifty_ret = nifty_ret.loc[common]

        years = max(0.01, (eq_series.index[-1] - eq_series.index[0]).days / 365.25)
        total_return = (final_equity - initial_capital) / initial_capital
        cagr = (final_equity / initial_capital) ** (1 / years) - 1 if final_equity > 0 else -1
        nifty_total = (nifty_series.iloc[-1] - nifty_series.iloc[0]) / nifty_series.iloc[0]
        nifty_cagr = (nifty_series.iloc[-1] / nifty_series.iloc[0]) ** (1 / years) - 1

        # Sharpe (annualised, risk-free 0)
        sharpe = (daily_ret.mean() / daily_ret.std() * math.sqrt(252)) if daily_ret.std() > 0 else 0
        downside = daily_ret[daily_ret < 0]
        sortino = (daily_ret.mean() / downside.std() * math.sqrt(252)) if len(downside) and downside.std() > 0 else 0

        # Max drawdown
        roll_max = eq_series.cummax()
        drawdown = (eq_series - roll_max) / roll_max
        max_dd = drawdown.min() if len(drawdown) else 0
        calmar = (cagr / abs(max_dd)) if max_dd < 0 else 0

        # Trades stats
        if trades:
            df_t = pd.DataFrame([asdict(t) for t in trades])
            wins = df_t[df_t["net_pnl"] > 0]
            losses = df_t[df_t["net_pnl"] <= 0]
            win_rate = (len(wins) / len(df_t)) * 100
            avg_win = wins["return_pct"].mean() if len(wins) else 0
            avg_loss = losses["return_pct"].mean() if len(losses) else 0
            profit_factor = (wins["net_pnl"].sum() / abs(losses["net_pnl"].sum())) if len(losses) and losses["net_pnl"].sum() != 0 else float("inf")
            trades_dict = df_t.to_dict("records")
        else:
            win_rate = avg_win = avg_loss = profit_factor = 0
            trades_dict = []

        metrics = {
            "years": round(years, 2),
            "initial_capital": initial_capital,
            "final_equity": round(final_equity, 2),
            "total_return_pct": round(total_return * 100, 2),
            "cagr_pct": round(cagr * 100, 2),
            "sharpe": round(sharpe, 3),
            "sortino": round(sortino, 3),
            "max_drawdown_pct": round(max_dd * 100, 2),
            "calmar": round(calmar, 3),
            "n_trades": len(trades),
            "win_rate_pct": round(win_rate, 1),
            "avg_winner_pct": round(avg_win, 2),
            "avg_loser_pct": round(avg_loss, 2),
            "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else None,
            "nifty_total_return_pct": round(nifty_total * 100, 2),
            "nifty_cagr_pct": round(nifty_cagr * 100, 2),
            "alpha_vs_nifty_cagr_pct": round((cagr - nifty_cagr) * 100, 2),
        }

        # Bootstrap CIs (1000 resamples of monthly returns)
        bootstrap_ci = cls._bootstrap_ci(daily_ret, n_boot=1000)

        # Per-regime attribution (best-effort: trade-level)
        per_regime: dict[str, dict] = {}
        if trades:
            for t in trades:
                reg = t.regime_at_entry or "UNKNOWN"
                if reg not in per_regime:
                    per_regime[reg] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
                per_regime[reg]["trades"] += 1
                if t.net_pnl > 0:
                    per_regime[reg]["wins"] += 1
                per_regime[reg]["total_pnl"] += t.net_pnl
            for reg, a in per_regime.items():
                a["win_rate_pct"] = round(a["wins"] / a["trades"] * 100, 1) if a["trades"] else 0
                a["total_pnl_inr"] = round(a["total_pnl"], 2)

        return cls(
            strategy_name=strategy_name,
            start=str(start)[:10], end=str(end)[:10],
            initial_capital=initial_capital, final_equity=final_equity,
            metrics=metrics, per_regime=per_regime, bootstrap_ci=bootstrap_ci,
            trades=trades_dict,
            equity_curve=[(str(d)[:10], round(v, 2)) for d, v in equity_curve],
            nifty_curve=[(str(d)[:10], round(v, 2)) for d, v in zip(nifty_series.index, nifty_series.values)],
            cost_pct=cost_pct,
        )

    @staticmethod
    def _bootstrap_ci(daily_ret: pd.Series, n_boot: int = 1000, ci: float = 0.95) -> dict:
        """Block bootstrap on daily returns for CIs on key metrics."""
        if len(daily_ret) < 30:
            return {}
        rng = np.random.default_rng(42)
        n = len(daily_ret)
        sharpes, cagrs, mdds = [], [], []
        for _ in range(n_boot):
            idx = rng.integers(0, n, size=n)
            sample = daily_ret.iloc[idx]
            mu, sigma = sample.mean(), sample.std()
            if sigma > 0:
                sharpes.append(mu / sigma * math.sqrt(252))
            cagrs.append(((1 + sample).prod() ** (252 / n) - 1) * 100)
            cum = (1 + sample).cumprod()
            roll = cum.cummax()
            dd = (cum - roll) / roll
            mdds.append(dd.min() * 100)
        lo, hi = (1 - ci) / 2, 1 - (1 - ci) / 2
        out = {
            "cagr_pct_95ci": [round(float(np.quantile(cagrs, lo)), 2), round(float(np.quantile(cagrs, hi)), 2)] if cagrs else None,
            "max_dd_pct_95ci": [round(float(np.quantile(mdds, lo)), 2), round(float(np.quantile(mdds, hi)), 2)] if mdds else None,
        }
        if sharpes:
            out["sharpe_95ci"] = [round(float(np.quantile(sharpes, lo)), 3), round(float(np.quantile(sharpes, hi)), 3)]
        return out

    def print_summary(self) -> None:
        m = self.metrics
        print(f"\n=== {self.strategy_name} | {self.start} -> {self.end} ===")
        print(f"  Final equity:   Rs {m['final_equity']:,.0f}  (initial Rs {m['initial_capital']:,.0f})")
        print(f"  Total return:   {m['total_return_pct']:+.2f}% over {m['years']:.1f}y")
        print(f"  CAGR:           {m['cagr_pct']:+.2f}%   (Nifty: {m['nifty_cagr_pct']:+.2f}%, alpha {m['alpha_vs_nifty_cagr_pct']:+.2f}%)")
        print(f"  Sharpe:         {m['sharpe']}   Sortino: {m['sortino']}   Calmar: {m['calmar']}")
        print(f"  Max DD:         {m['max_drawdown_pct']:.2f}%")
        print(f"  Trades:         {m['n_trades']}  WR {m['win_rate_pct']}%  PF {m['profit_factor']}")
        if self.bootstrap_ci:
            ci = self.bootstrap_ci
            print(f"  95% CIs:        sharpe {ci['sharpe_95ci']}  cagr {ci['cagr_pct_95ci']}%  maxDD {ci['max_dd_pct_95ci']}%")
        if self.per_regime:
            print("  Per-regime attribution:")
            for reg, a in sorted(self.per_regime.items(), key=lambda x: -x[1]["trades"]):
                print(f"    {reg:18s}  trades={a['trades']:4d}  WR={a['win_rate_pct']:5.1f}%  pnl={a['total_pnl_inr']:+.0f}")

    def save(self, path: str) -> None:
        out = {
            "strategy": self.strategy_name,
            "start": self.start, "end": self.end,
            "metrics": self.metrics,
            "per_regime": self.per_regime,
            "bootstrap_ci": self.bootstrap_ci,
            "n_trades": len(self.trades),
            # equity_curve + trades omitted from main file (large); save separately if needed
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

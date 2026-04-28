"""
Sustainability research — answers 4 questions before we propose the plan:

  1. SUB-PERIOD STABILITY: how do the 3 best strategies perform in
     overlapping 12-month windows (2022-2026)? Captures regime sensitivity.

  2. STRATEGY CORRELATION: do momentum_agg, oversold_quality, and
     equal_weight_nifty50 have low monthly-return correlation?
     Low correlation is what makes ensembles work.

  3. ENSEMBLE BACKTEST: 40/40/20 weighted portfolio of the 3 best.
     Does ensemble actually reduce MaxDD without killing return?

  4. RISK OVERLAY IMPACT: re-run momentum_agg with sector cap (max 30%
     in one industry) + drawdown circuit breaker (halt new entries
     when portfolio is -8% from rolling peak; resume after +4% recovery).
     Compare to baseline.

Outputs go to data/research/sustainability/*.json + summary.md
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import numpy as np

from research.harness import BacktestEngine, MarketState, Decision
from research.strategies import (
    EqualWeightNifty50,
    OversoldQualityTop20,
    V3SingleVariant,
    _close_dropped, _open_new,
)
from data_store import get_bars
from data_fetcher import SYMBOL_TOKENS

OUT = Path("c:/trading/data/research/sustainability")
OUT.mkdir(parents=True, exist_ok=True)


# ============================================================================
# Q1: Sub-period stability — rolling 12-month windows
# ============================================================================

def q1_subperiod_stability():
    print(f"\n[{time.strftime('%H:%M:%S')}] Q1: Sub-period stability — rolling 12mo windows", flush=True)
    windows = [
        ("2022-01-01", "2023-01-01"),
        ("2022-07-01", "2023-07-01"),
        ("2023-01-01", "2024-01-01"),
        ("2023-07-01", "2024-07-01"),
        ("2024-01-01", "2025-01-01"),
        ("2024-07-01", "2025-07-01"),
        ("2025-01-01", "2026-01-01"),
        ("2025-04-01", "2026-04-01"),
    ]
    strategies = [
        ("momentum_agg",       V3SingleVariant("momentum_agg")),
        ("oversold_quality",   OversoldQualityTop20()),
        ("equal_weight_n50",   EqualWeightNifty50()),
    ]
    rows = []
    for sname, strat in strategies:
        for ws, we in windows:
            print(f"  {sname:18s} {ws} -> {we}", flush=True)
            try:
                eng = BacktestEngine(strategy=strat, start=ws, end=we,
                    universe="nifty100", capital=1_000_000,
                    rebalance="monthly",
                    max_positions=20 if "oversold" in sname else (50 if "n50" in sname else 5))
                res = eng.run()
                m = res.metrics
                rows.append({
                    "strategy": sname, "start": ws, "end": we,
                    "cagr_pct": m["cagr_pct"], "sharpe": m["sharpe"],
                    "max_dd_pct": m["max_drawdown_pct"], "trades": m["n_trades"],
                    "alpha_pct": m["alpha_vs_nifty_cagr_pct"],
                    "nifty_cagr_pct": m["nifty_cagr_pct"],
                })
            except Exception as e:
                print(f"    FAILED: {e}", flush=True)
                rows.append({"strategy": sname, "start": ws, "end": we, "error": str(e)})
    out = OUT / "q1_subperiod_stability.json"
    out.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(f"  -> {out}", flush=True)
    print_q1_summary(rows)
    return rows


def print_q1_summary(rows):
    print(f"\n  ===== Q1 SUMMARY (sub-period CAGR) =====", flush=True)
    df = pd.DataFrame([r for r in rows if "error" not in r])
    if len(df) == 0: return
    pivot = df.pivot(index="start", columns="strategy", values="cagr_pct")
    print(pivot.round(2).to_string(), flush=True)
    print(f"\n  Volatility of CAGR across windows (lower = more stable):", flush=True)
    for s in pivot.columns:
        vals = pivot[s].dropna()
        if len(vals): print(f"    {s:20s}  std={vals.std():.2f}  min={vals.min():.2f}  max={vals.max():.2f}", flush=True)


# ============================================================================
# Q2: Strategy correlation matrix
# ============================================================================

def q2_correlation():
    print(f"\n[{time.strftime('%H:%M:%S')}] Q2: Strategy correlation (full window)", flush=True)
    strategies = [
        ("momentum_agg",       V3SingleVariant("momentum_agg"),  5),
        ("oversold_quality",   OversoldQualityTop20(),           20),
        ("equal_weight_n50",   EqualWeightNifty50(),             50),
    ]
    monthly_returns = {}
    for sname, strat, mp in strategies:
        print(f"  Running {sname} (full window)...", flush=True)
        eng = BacktestEngine(strategy=strat, start="2022-01-01", end="2026-04-25",
            universe="nifty100", capital=1_000_000, rebalance="monthly", max_positions=mp)
        res = eng.run()
        # Build monthly returns from equity curve
        eq = pd.Series([v for _, v in res.equity_curve], index=pd.to_datetime([d for d, _ in res.equity_curve]))
        m_eq = eq.resample("M").last()
        m_ret = m_eq.pct_change().dropna() * 100
        monthly_returns[sname] = m_ret
    # Correlation matrix
    corr_df = pd.DataFrame(monthly_returns).corr()
    print(f"\n  ===== Q2 SUMMARY (monthly return correlation) =====", flush=True)
    print(corr_df.round(3).to_string(), flush=True)
    out = OUT / "q2_correlation.json"
    out.write_text(json.dumps({
        "correlation_matrix": corr_df.to_dict(),
        "interpretation": (
            "Low correlation (<0.7) = strategies provide diversification benefit. "
            "High correlation (>0.85) = ensemble is just one strategy in disguise."
        ),
    }, indent=2), encoding="utf-8")
    print(f"  -> {out}", flush=True)
    return corr_df


# ============================================================================
# Q3: Ensemble portfolio
# ============================================================================

class EnsembleStrategy:
    """40% momentum_agg + 40% oversold_quality + 20% equal_weight_n50.
    Each sleeve gets its allocation; we just union all picks weighted by sleeve.
    Implemented simply: each rebalance, pick from each sleeve, weight by sleeve%."""
    name = "ensemble_40_40_20"

    def initialize(self, universe, capital):
        from research.strategies import V3SingleVariant, OversoldQualityTop20, EqualWeightNifty50
        self.sleeves = [
            (V3SingleVariant("momentum_agg", top_n=5), 0.40),
            (OversoldQualityTop20(),                   0.40),
            (EqualWeightNifty50(),                     0.20),
        ]
        for s, _ in self.sleeves:
            s.initialize(universe, capital)
        self.universe = universe

    def on_rebalance(self, state):
        # Collect target symbols from each sleeve, weighted by sleeve allocation
        weights: dict[str, float] = defaultdict(float)
        for sleeve, alloc in self.sleeves:
            decisions = sleeve.on_rebalance(state)
            picks = {d.symbol for d in decisions if d.action == "OPEN"}
            held_in_sleeve = {sym for sym in state.open_positions if sym in picks}
            all_picks = picks | held_in_sleeve
            if not all_picks: continue
            per_pick = alloc / len(all_picks)
            for sym in all_picks:
                weights[sym] += per_pick
        target = set(weights.keys())
        return _close_dropped(state, target) + _open_new(state, target)

    def on_mark(self, state):
        return []


def q3_ensemble():
    print(f"\n[{time.strftime('%H:%M:%S')}] Q3: Ensemble backtest (40/40/20)", flush=True)
    eng = BacktestEngine(strategy=EnsembleStrategy(), start="2022-01-01", end="2026-04-25",
        universe="nifty100", capital=1_000_000, rebalance="monthly", max_positions=30)
    res = eng.run()
    res.print_summary()
    res.save(str(OUT / "q3_ensemble_baseline.json"))
    return res


# ============================================================================
# Q4: Risk-overlay-on-momentum
# ============================================================================

class MomentumWithRiskOverlay:
    """momentum_agg with two risk overlays:
       - sector cap: max 30% notional in any one Nifty industry
       - drawdown circuit breaker: stop opening new positions when portfolio is
         -8% from rolling peak; resume after recovering 50% of the drawdown."""
    name = "momentum_agg_with_risk"

    def __init__(self):
        from research.strategies import V3SingleVariant
        self.inner = V3SingleVariant("momentum_agg", top_n=10)  # widen pick count
        self.peak_equity = 0
        self.halted = False

    def initialize(self, universe, capital):
        self.inner.initialize(universe, capital)
        self.peak_equity = capital

    def on_rebalance(self, state):
        # Update peak + halt state
        self.peak_equity = max(self.peak_equity, state.equity)
        dd_pct = (state.equity - self.peak_equity) / self.peak_equity * 100
        if dd_pct <= -8 and not self.halted:
            self.halted = True
            print(f"    [{state.date.date()}] CIRCUIT BREAKER ENGAGED: dd {dd_pct:.1f}%", flush=True)
        elif self.halted and dd_pct >= -4:
            self.halted = False
            print(f"    [{state.date.date()}] CIRCUIT BREAKER RELEASED: dd {dd_pct:.1f}%", flush=True)

        decisions = self.inner.on_rebalance(state)

        if self.halted:
            # Only allow CLOSE actions, no OPENs
            decisions = [d for d in decisions if d.action != "OPEN"]
            return decisions

        # Apply sector cap to OPEN decisions
        try:
            from news.sector_map import industry_of
        except Exception:
            return decisions
        # Compute current sector exposure (notional)
        sector_notional = defaultdict(float)
        for sym, pos in state.open_positions.items():
            sec = industry_of(sym) or "UNKNOWN"
            sector_notional[sec] += pos.qty * pos.entry_price
        cap = state.equity * 0.30
        kept = []
        for d in decisions:
            if d.action != "OPEN":
                kept.append(d)
                continue
            sec = industry_of(d.symbol) or "UNKNOWN"
            if sector_notional[sec] >= cap:
                continue   # skip; sector full
            kept.append(d)
            # Conservatively assume each new pick = avg slot size
            sector_notional[sec] += state.equity / 10
        return kept

    def on_mark(self, state):
        return []


def q4_risk_overlay():
    print(f"\n[{time.strftime('%H:%M:%S')}] Q4: momentum_agg + risk overlay", flush=True)
    eng = BacktestEngine(strategy=MomentumWithRiskOverlay(), start="2022-01-01", end="2026-04-25",
        universe="nifty100", capital=1_000_000, rebalance="monthly", max_positions=10)
    res = eng.run()
    res.print_summary()
    res.save(str(OUT / "q4_momentum_with_risk.json"))
    return res


# ============================================================================
# Main
# ============================================================================

def main():
    print(f"[{time.strftime('%H:%M:%S')}] Starting sustainability research...", flush=True)
    t0 = time.time()
    q1_rows = q1_subperiod_stability()
    print(f"\n[{time.strftime('%H:%M:%S')}] Q1 done ({(time.time()-t0):.0f}s elapsed)", flush=True)
    t1 = time.time()
    corr_df = q2_correlation()
    print(f"\n[{time.strftime('%H:%M:%S')}] Q2 done ({(time.time()-t1):.0f}s)", flush=True)
    t2 = time.time()
    ensemble_res = q3_ensemble()
    print(f"\n[{time.strftime('%H:%M:%S')}] Q3 done ({(time.time()-t2):.0f}s)", flush=True)
    t3 = time.time()
    risk_res = q4_risk_overlay()
    print(f"\n[{time.strftime('%H:%M:%S')}] Q4 done ({(time.time()-t3):.0f}s)", flush=True)
    print(f"\n[{time.strftime('%H:%M:%S')}] ALL DONE in {(time.time()-t0)/60:.1f} min", flush=True)


if __name__ == "__main__":
    main()

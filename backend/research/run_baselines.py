"""
Run all baseline strategies on the same window + universe.
Compare against V3 to see if V3 actually beats simple alternatives.

Usage:
    cd c:/trading/backend
    python -m research.run_baselines
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research.harness import BacktestEngine
from research.strategies import (
    EqualWeightNifty50,
    MomentumTop20,
    OversoldQualityTop20,
    V3BaselineWrapper,
)


# ~5y window (will extend back as data permits — bars.db starts 2021-04)
START = "2022-01-01"
END   = "2026-04-25"
UNIVERSE = "nifty100"
CAPITAL = 1_000_000


def main():
    strategies = [
        EqualWeightNifty50(),
        MomentumTop20(),
        OversoldQualityTop20(),
        V3BaselineWrapper(),
    ]

    results = []
    for s in strategies:
        print(f"\n{'='*70}\nRunning {s.name}...")
        t0 = time.time()
        try:
            engine = BacktestEngine(
                strategy=s, start=START, end=END,
                universe=UNIVERSE, capital=CAPITAL,
                round_trip_cost_pct=0.4, rebalance="monthly",
                max_positions=20 if "top20" in s.name else (50 if "nifty50" in s.name else 5),
            )
            res = engine.run()
            res.print_summary()
            res.save(f"c:/trading/data/research/backtest_{s.name}.json")
            results.append(res)
            print(f"  (took {time.time()-t0:.1f}s)")
        except Exception as e:
            import traceback
            print(f"  FAILED: {e}")
            traceback.print_exc()

    # Side-by-side summary
    print(f"\n\n{'='*70}\nSIDE-BY-SIDE COMPARISON\n{'='*70}")
    print(f"{'strategy':24s} {'CAGR%':>8s} {'Sharpe':>8s} {'MaxDD%':>8s} {'Calmar':>8s} {'Trades':>8s} {'WR%':>6s} {'Alpha%':>8s}")
    for r in results:
        m = r.metrics
        print(f"{r.strategy_name:24s} {m['cagr_pct']:>8.2f} {m['sharpe']:>8.3f} {m['max_drawdown_pct']:>8.2f} {m['calmar']:>8.3f} {m['n_trades']:>8d} {m['win_rate_pct']:>6.1f} {m['alpha_vs_nifty_cagr_pct']:>8.2f}")


if __name__ == "__main__":
    main()

"""
V3 variant-by-variant breakdown — one variant at a time, write result immediately,
flush stdout so we have visibility.

Usage:
    cd c:/trading/backend
    python -u research/run_v3_breakdown.py
        OR
    python -u research/run_v3_breakdown.py --variant momentum_agg
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Force unbuffered stdout (defense in depth on top of `python -u`)
os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research.harness import BacktestEngine
from research.strategies import V3BaselineWrapper, V3SingleVariant


# Use Nifty 100 to keep runtime tractable. Can rerun per-variant on Nifty 500
# afterwards if any variant looks promising.
CONFIGS = [
    ("v3_adaptive_vix",   V3BaselineWrapper(),                  "nifty100"),
    ("v3_momentum_agg",   V3SingleVariant("momentum_agg"),      "nifty100"),
    ("v3_momentum_cons",  V3SingleVariant("momentum_cons"),     "nifty100"),
    ("v3_mean_reversion", V3SingleVariant("mean_reversion"),    "nifty100"),
    ("v3_defensive",      V3SingleVariant("defensive"),         "nifty100"),
]


def run_one(name, strat, uni):
    print(f"\n[{time.strftime('%H:%M:%S')}] === {name} on {uni} ===", flush=True)
    t0 = time.time()
    try:
        eng = BacktestEngine(
            strategy=strat, start="2022-01-01", end="2026-04-25",
            universe=uni, capital=1_000_000,
            round_trip_cost_pct=0.4, rebalance="monthly", max_positions=5,
        )
        res = eng.run()
        res.print_summary()
        path = f"c:/trading/data/research/backtest_{name}.json"
        res.save(path)
        print(f"  written: {path}  ({time.time()-t0:.1f}s)", flush=True)
        return name, res
    except Exception as e:
        import traceback
        print(f"  FAILED: {e}", flush=True)
        traceback.print_exc()
        return name, None


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--variant", help="Run only this variant by short name (e.g. momentum_agg)")
    args = p.parse_args()

    configs = CONFIGS
    if args.variant:
        configs = [c for c in CONFIGS if args.variant in c[0]]
        if not configs:
            print(f"No variant matches {args.variant!r}. Choose from: {[c[0] for c in CONFIGS]}")
            sys.exit(1)

    print(f"[{time.strftime('%H:%M:%S')}] Running {len(configs)} configs serially...", flush=True)
    results = []
    for name, strat, uni in configs:
        results.append(run_one(name, strat, uni))

    print(f"\n{'='*70}\nFINAL COMPARISON")
    hdr = f"{'name':25s} {'CAGR%':>8s} {'Sharpe':>8s} {'MaxDD%':>8s} {'Trades':>8s} {'WR%':>6s} {'Alpha%':>8s}"
    print(hdr, flush=True)
    for name, r in results:
        if r is None:
            print(f"{name:25s}  FAILED", flush=True)
            continue
        m = r.metrics
        print(f"{name:25s} {m['cagr_pct']:>8.2f} {m['sharpe']:>8.3f} {m['max_drawdown_pct']:>8.2f} "
              f"{m['n_trades']:>8d} {m['win_rate_pct']:>6.1f} {m['alpha_vs_nifty_cagr_pct']:>8.2f}", flush=True)


if __name__ == "__main__":
    main()

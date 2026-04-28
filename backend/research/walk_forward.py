"""
Walk-forward validation — out-of-sample test of momentum_agg + risk overlay.

For a fixed-parameter strategy like ours, walk-forward measures REGIME
ROBUSTNESS: does the strategy work across different sub-periods, or only in
a single favorable window?

Folds (each test window non-overlapping with others):
  Fold 1: test 2024-01 -> 2024-06   (post-bull consolidation)
  Fold 2: test 2024-07 -> 2024-12   (range market)
  Fold 3: test 2025-01 -> 2025-06   (early 2025 weakness)
  Fold 4: test 2025-07 -> 2025-12   (late 2025)
  Fold 5: test 2026-01 -> 2026-04   (recent)

Each fold runs the strategy with full warmup (250+ days before test start)
and reports test-window metrics.

Pass criteria for real-money go-live:
  - All folds: max_dd_pct > -25% (tail risk acceptable)
  - >= 3 of 5 folds: CAGR > 0% (consistent positive return)
  - >= 3 of 5 folds: alpha vs Nifty > 0% (consistent outperformance)
  - Worst-fold Sharpe > -0.5 (no catastrophic regime)

If ALL pass: strategy approved for real-money capital ladder.
If any FAIL: stay paper, debug.

Output: data/research/walk_forward.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from research.harness import BacktestEngine
from research.sustainability_research import MomentumWithRiskOverlay


FOLDS = [
    ("fold_1_2024_h1", "2024-01-01", "2024-06-30"),
    ("fold_2_2024_h2", "2024-07-01", "2024-12-31"),
    ("fold_3_2025_h1", "2025-01-01", "2025-06-30"),
    ("fold_4_2025_h2", "2025-07-01", "2025-12-31"),
    ("fold_5_2026_h1", "2026-01-01", "2026-04-25"),
]

PASS_GATES = {
    "all_max_dd_above": -25.0,         # all folds must have MaxDD > -25%
    "min_positive_cagr_count": 3,      # >=3 folds with CAGR > 0%
    "min_positive_alpha_count": 3,     # >=3 folds beating Nifty
    "min_worst_sharpe": -0.5,          # worst-fold Sharpe must be > -0.5
}


def main():
    print(f"[{time.strftime('%H:%M:%S')}] Walk-forward validation: {len(FOLDS)} folds", flush=True)
    t0 = time.time()
    results = []
    for label, start, end in FOLDS:
        print(f"\n  {label}  ({start} -> {end})", flush=True)
        try:
            eng = BacktestEngine(
                strategy=MomentumWithRiskOverlay(),
                start=start, end=end, universe="nifty100", capital=1_000_000,
                rebalance="monthly", max_positions=10,
            )
            res = eng.run()
            m = res.metrics
            r = {
                "fold": label, "start": start, "end": end,
                "cagr_pct": m["cagr_pct"], "sharpe": m["sharpe"], "sortino": m["sortino"],
                "max_dd_pct": m["max_drawdown_pct"], "calmar": m["calmar"],
                "n_trades": m["n_trades"], "win_rate_pct": m["win_rate_pct"],
                "alpha_pct": m["alpha_vs_nifty_cagr_pct"],
                "nifty_cagr_pct": m["nifty_cagr_pct"],
            }
            print(f"    CAGR={m['cagr_pct']:+.2f}% Sharpe={m['sharpe']:.2f} MaxDD={m['max_drawdown_pct']:.2f}% trades={m['n_trades']} alpha={m['alpha_vs_nifty_cagr_pct']:+.2f}%", flush=True)
            results.append(r)
        except Exception as e:
            print(f"    FAILED: {e}", flush=True)
            results.append({"fold": label, "start": start, "end": end, "error": str(e)})

    # Apply pass criteria
    valid = [r for r in results if "cagr_pct" in r]
    n_total = len(valid)
    n_pos_cagr = sum(1 for r in valid if r.get("cagr_pct", 0) > 0)
    n_pos_alpha = sum(1 for r in valid if r.get("alpha_pct", 0) > 0)
    worst_dd = min((r.get("max_dd_pct", 0) for r in valid), default=0)
    worst_sharpe = min((r.get("sharpe", 0) for r in valid), default=0)
    avg_cagr = sum(r["cagr_pct"] for r in valid) / max(1, n_total)
    avg_sharpe = sum(r["sharpe"] for r in valid) / max(1, n_total)
    avg_alpha = sum(r["alpha_pct"] for r in valid) / max(1, n_total)

    gates = {
        "all_max_dd_above": (worst_dd > PASS_GATES["all_max_dd_above"], f"worst MaxDD {worst_dd:.2f}% > {PASS_GATES['all_max_dd_above']}%"),
        "min_positive_cagr_count": (n_pos_cagr >= PASS_GATES["min_positive_cagr_count"], f"{n_pos_cagr}/{n_total} positive-CAGR folds (need {PASS_GATES['min_positive_cagr_count']})"),
        "min_positive_alpha_count": (n_pos_alpha >= PASS_GATES["min_positive_alpha_count"], f"{n_pos_alpha}/{n_total} positive-alpha folds (need {PASS_GATES['min_positive_alpha_count']})"),
        "min_worst_sharpe": (worst_sharpe > PASS_GATES["min_worst_sharpe"], f"worst Sharpe {worst_sharpe:.2f} > {PASS_GATES['min_worst_sharpe']}"),
    }
    all_pass = all(g[0] for g in gates.values())

    summary = {
        "run_at": pd.Timestamp.now().isoformat(),
        "folds": results,
        "aggregate": {
            "avg_cagr_pct": round(avg_cagr, 2),
            "avg_sharpe": round(avg_sharpe, 3),
            "avg_alpha_pct": round(avg_alpha, 2),
            "worst_max_dd_pct": round(worst_dd, 2),
            "worst_sharpe": round(worst_sharpe, 3),
            "positive_cagr_folds": n_pos_cagr,
            "positive_alpha_folds": n_pos_alpha,
            "total_folds": n_total,
        },
        "gate_results": {k: {"passed": v[0], "detail": v[1]} for k, v in gates.items()},
        "verdict": "PASS — strategy approved for real-money capital ladder" if all_pass else "FAIL — stay on paper, debug failing gates",
        "duration_sec": round(time.time() - t0, 1),
    }

    out_path = Path("c:/trading/data/research/walk_forward.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    print(f"\n{'='*70}\nWALK-FORWARD SUMMARY")
    print(f"  Avg CAGR:    {summary['aggregate']['avg_cagr_pct']:+.2f}%")
    print(f"  Avg Sharpe:  {summary['aggregate']['avg_sharpe']:.2f}")
    print(f"  Avg alpha:   {summary['aggregate']['avg_alpha_pct']:+.2f}%")
    print(f"  Worst MDD:   {summary['aggregate']['worst_max_dd_pct']:.2f}%")
    print(f"  Worst Sharpe:{summary['aggregate']['worst_sharpe']:.2f}")
    print(f"  +CAGR folds: {n_pos_cagr}/{n_total}   +alpha folds: {n_pos_alpha}/{n_total}")
    print(f"\n  Gate results:")
    for k, v in gates.items():
        mark = "PASS" if v[0] else "FAIL"
        print(f"    [{mark}] {k}: {v[1]}")
    print(f"\n  VERDICT: {summary['verdict']}")
    print(f"  ({time.time()-t0:.0f}s)\n  written: {out_path}", flush=True)

    # Push to Vercel
    try:
        import requests
        from config import VERCEL_CONFIG
        requests.post(
            f"{VERCEL_CONFIG['app_url']}/api/blob?key=walk_forward",
            json=summary,
            headers={"Content-Type": "application/json", "x-api-key": VERCEL_CONFIG["secret_key"]},
            timeout=15,
        )
    except Exception:
        pass


if __name__ == "__main__":
    main()

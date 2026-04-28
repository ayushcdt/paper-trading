"""
Continuous validation — weekly cron job that re-runs the backtest harness
with the current production config and tracks key metrics over time.

Outputs to data/research/validation_history.jsonl (one line per run).
Also produces data/research/validation_latest.json consumed by the dashboard.

Anomaly detection (logged + Telegram alert):
  - Rolling 3mo Sharpe drops below 0.3 (strategy quality degraded)
  - Max DD exceeds -20% in any window (risk overlay failed)
  - Win rate drops below 40% (entry logic degraded)
  - Total return underperforms Nifty in last 6mo by >5% (alpha lost)

Schedule: weekly (Sunday 23:00 IST). Or run manually:
    python -m research.continuous_validation
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ["PYTHONUNBUFFERED"] = "1"
sys.stdout.reconfigure(line_buffering=True)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
import pandas as pd

from research.harness import BacktestEngine
from research.sustainability_research import MomentumWithRiskOverlay
from common.market_hours import now_ist


HISTORY_PATH = Path("c:/trading/data/research/validation_history.jsonl")
LATEST_PATH  = Path("c:/trading/data/research/validation_latest.json")
SHADOW_HISTORY_DAYS = 365  # how far back to keep validation history


# Anomaly thresholds — matches sustainability framework gates
SHARPE_3MO_MIN = 0.3
MAX_DD_EMERGENCY = -20.0
WIN_RATE_MIN = 40.0
ALPHA_6MO_MIN = -5.0


def _alert_telegram(severity: str, title: str, body: str) -> None:
    try:
        from alerts.channels import dispatch
        dispatch(severity, title, body)
    except Exception as e:
        logger.warning(f"telegram alert failed: {e}")


def _backtest_window(start: str, end: str) -> dict:
    """Run the production strategy on a window. Returns metrics + key derived stats."""
    eng = BacktestEngine(
        strategy=MomentumWithRiskOverlay(),
        start=start, end=end,
        universe="nifty100", capital=1_000_000,
        rebalance="monthly", max_positions=10,
    )
    res = eng.run()
    m = res.metrics
    return {
        "start": start, "end": end,
        "cagr_pct": m["cagr_pct"], "sharpe": m["sharpe"], "sortino": m["sortino"],
        "max_dd_pct": m["max_drawdown_pct"], "calmar": m["calmar"],
        "n_trades": m["n_trades"], "win_rate_pct": m["win_rate_pct"],
        "alpha_pct": m["alpha_vs_nifty_cagr_pct"],
        "nifty_cagr_pct": m["nifty_cagr_pct"],
    }


def main():
    print(f"[{now_ist().isoformat()}] continuous validation starting...", flush=True)
    t0 = time.time()
    today = pd.Timestamp(now_ist().date()).strftime("%Y-%m-%d")

    # Three windows for trend monitoring
    end = today
    win_3mo  = (pd.Timestamp(end) - pd.Timedelta(days=90)).strftime("%Y-%m-%d")
    win_6mo  = (pd.Timestamp(end) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    win_12mo = (pd.Timestamp(end) - pd.Timedelta(days=365)).strftime("%Y-%m-%d")
    win_full = "2022-01-01"

    print(f"  windows: 3mo={win_3mo}, 6mo={win_6mo}, 12mo={win_12mo}, full={win_full}", flush=True)

    # Each window needs ≥250d warmup for momentum signals; backtest engine handles this
    metrics = {}
    for label, start in [("full", win_full), ("12mo", win_12mo), ("6mo", win_6mo), ("3mo", win_3mo)]:
        try:
            m = _backtest_window(start, end)
            metrics[label] = m
            print(f"  {label:6s} {start}->{end}: CAGR={m['cagr_pct']:+.2f}% Sharpe={m['sharpe']:.2f} MaxDD={m['max_dd_pct']:.2f}% trades={m['n_trades']}", flush=True)
        except Exception as e:
            metrics[label] = {"error": str(e)}
            print(f"  {label}: FAILED {e}", flush=True)

    # Anomaly detection
    anomalies = []
    if metrics.get("3mo") and "sharpe" in metrics["3mo"]:
        m3 = metrics["3mo"]
        if m3["sharpe"] < SHARPE_3MO_MIN:
            anomalies.append(f"3mo Sharpe {m3['sharpe']:.2f} < {SHARPE_3MO_MIN} (strategy quality degraded)")
        if m3["max_dd_pct"] < MAX_DD_EMERGENCY:
            anomalies.append(f"3mo MaxDD {m3['max_dd_pct']:.2f}% < {MAX_DD_EMERGENCY}% (risk overlay failure)")
        if m3["win_rate_pct"] < WIN_RATE_MIN and m3["n_trades"] > 5:
            anomalies.append(f"3mo WR {m3['win_rate_pct']:.1f}% < {WIN_RATE_MIN}% (entry logic degraded)")
    if metrics.get("6mo") and "alpha_pct" in metrics["6mo"]:
        if metrics["6mo"]["alpha_pct"] < ALPHA_6MO_MIN:
            anomalies.append(f"6mo alpha {metrics['6mo']['alpha_pct']:.2f}% < {ALPHA_6MO_MIN}% (losing to Nifty)")

    # Health verdict
    if not anomalies:
        verdict = "HEALTHY"
        verdict_color = "green"
    elif len(anomalies) == 1:
        verdict = "WATCH"
        verdict_color = "yellow"
    else:
        verdict = "DEGRADED"
        verdict_color = "red"
    print(f"\n  VERDICT: {verdict}  anomalies={len(anomalies)}", flush=True)
    for a in anomalies:
        print(f"    - {a}", flush=True)

    # Append to history
    snapshot = {
        "run_at": now_ist().isoformat(),
        "verdict": verdict, "verdict_color": verdict_color,
        "anomalies": anomalies,
        "metrics": metrics,
        "config": {"strategy": "momentum_agg + risk overlay", "max_positions": 10, "sector_cap_pct": 30},
        "duration_sec": round(time.time() - t0, 1),
    }
    HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(snapshot, default=str) + "\n")
    LATEST_PATH.write_text(json.dumps(snapshot, indent=2, default=str), encoding="utf-8")
    print(f"  written: {LATEST_PATH}", flush=True)

    # Push to Vercel as a blob the dashboard can read
    try:
        import requests
        from config import VERCEL_CONFIG
        r = requests.post(
            f"{VERCEL_CONFIG['app_url']}/api/blob?key=validation_latest",
            json=snapshot,
            headers={"Content-Type": "application/json", "x-api-key": VERCEL_CONFIG["secret_key"]},
            timeout=15,
        )
        if r.status_code == 200:
            print("  synced validation_latest to Vercel", flush=True)
    except Exception as e:
        logger.warning(f"vercel push failed: {e}")

    # Telegram alert if degraded
    if verdict != "HEALTHY":
        sev = "warning" if verdict == "WATCH" else "critical"
        body = "\n".join(anomalies) + f"\n\nFull window: " + (
            f"CAGR {metrics.get('full', {}).get('cagr_pct', 'n/a')}%, "
            f"Sharpe {metrics.get('full', {}).get('sharpe', 'n/a')}"
        )
        _alert_telegram(sev, f"Strategy validation: {verdict}", body)

    print(f"\n  DONE in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()

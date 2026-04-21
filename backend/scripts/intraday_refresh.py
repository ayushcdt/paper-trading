"""
Intraday refresh job. Runs every 15 min during NSE hours and updates:
  - Market data (Nifty, Bank Nifty, VIX, sector indices LTPs + trend updates)
  - Regime classifier (fires alert if regime changes intraday)
  - Paper portfolio mark-to-market
  - News snapshot (if cache >15 min old)
  - Macro overlay (if cache >1h old)

Does NOT recompute stock picks -- those are slow signals (daily) and refreshing
them intraday creates whipsaw + hits Angel API rate limits.

Self-skips outside market hours, so safe to leave on a 15-min cron.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

import requests
from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
from data_fetcher import get_fetcher
from market_analyzer import analyze_nifty, analyze_banknifty, analyze_vix, analyze_sectors
from adaptive.regime import classify_regime, compute_breadth
from adaptive.targets import compute_status
from config import VERCEL_CONFIG


MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
ALERT_STATE_PATH = DATA_DIR / "alert_state.json"


def is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def post_blob(key: str, payload):
    try:
        r = requests.post(
            f"{VERCEL_CONFIG['app_url']}/api/blob?key={key}",
            json=payload,
            headers={"Content-Type": "application/json", "x-api-key": VERCEL_CONFIG["secret_key"]},
            timeout=15,
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"  blob {key} sync error: {e}")
        return False


def post_main_analysis(combined: dict) -> bool:
    """Push the merged analysis blob to /api/update-data so /, /market, /stocks all refresh."""
    try:
        r = requests.post(
            f"{VERCEL_CONFIG['app_url']}/api/update-data",
            json=combined,
            headers={"Content-Type": "application/json", "x-api-key": VERCEL_CONFIG["secret_key"]},
            timeout=20,
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"  update-data sync error: {e}")
        return False


def intraday_refresh():
    if not is_market_hours():
        logger.info("Outside NSE market hours; skipping intraday refresh")
        return

    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            logger.error("Angel login failed; aborting")
            return

    # ---- 1. Refresh market data (Nifty + Bank Nifty + VIX + sectors) ----
    logger.info("Refreshing market data...")
    nifty = analyze_nifty()
    banknifty = analyze_banknifty()
    vix = analyze_vix()
    sectors = analyze_sectors()

    # ---- 2. Re-classify regime ----
    nifty_df = fetcher.get_historical_data("NIFTY", interval="ONE_DAY", days=400)
    if not nifty_df.empty:
        nifty_close = nifty_df.sort_values("Date").reset_index(drop=True)["Close"]
    else:
        nifty_close = None

    vix_df = fetcher.get_historical_data("INDIAVIX", interval="ONE_DAY", days=300)
    vix_history = vix_df["Close"] if not vix_df.empty else None
    vix_value = vix.get("value", 15)

    regime_value = None
    regime_reason = None
    if nifty_close is not None and vix_history is not None:
        # Quick breadth from 30 sample symbols (vs 120 -- 4x faster)
        from stock_picker import NIFTY_50
        from data_fetcher import SYMBOL_TOKENS
        from strategy_v2 import TOP_20_DROP
        sample_syms = [s for s in NIFTY_50 if s in SYMBOL_TOKENS and s not in TOP_20_DROP][:30]
        sample_hist = {}
        for sym in sample_syms:
            df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=260)
            if not df.empty and len(df) >= 200:
                sample_hist[sym] = df.sort_values("Date").reset_index(drop=True)
        target_date = nifty_df.sort_values("Date").iloc[-1]["Date"]
        breadth = compute_breadth(sample_hist, target_date)
        rs = classify_regime(nifty_close, breadth, vix_value, vix_history)
        regime_value = rs.regime.value
        regime_reason = rs.reason
        logger.info(f"Regime: {regime_value} ({regime_reason})")

    # ---- 3. Read existing combined data, merge in fresh market + regime ----
    combined_path = DATA_DIR / "analysis_combined.json"
    combined = {}
    if combined_path.exists():
        try:
            combined = json.loads(combined_path.read_text(encoding="utf-8"))
        except Exception:
            combined = {}

    combined["generated_at"] = datetime.now().isoformat()
    combined.setdefault("market", {})
    combined["market"]["nifty"] = nifty
    combined["market"]["banknifty"] = banknifty
    combined["market"]["vix"] = vix
    combined["market"]["sectors"] = sectors

    # Update regime in stocks block (picks themselves stay from morning)
    if regime_value:
        combined.setdefault("stocks", {})
        prev_regime = combined["stocks"].get("regime")
        combined["stocks"]["regime"] = regime_value
        combined["stocks"]["regime_reason"] = regime_reason
        combined["stocks"]["intraday_refresh_at"] = datetime.now().isoformat()
        if prev_regime and prev_regime != regime_value:
            logger.warning(f"REGIME CHANGED INTRADAY: {prev_regime} -> {regime_value}")
            _fire_regime_alert(prev_regime, regime_value, regime_reason)

    combined_path.write_text(json.dumps(combined, indent=2, default=str), encoding="utf-8")

    # ---- 4. Paper portfolio mark-to-market ----
    pf = PaperPortfolio()
    open_syms = pf.get_open_symbols()
    prices = {}
    for sym in open_syms:
        try:
            ltp = float(fetcher.get_ltp(sym).get("ltp", 0))
            if ltp > 0:
                prices[sym] = ltp
        except Exception as e:
            logger.warning(f"LTP fetch failed for {sym}: {e}")
    pf.mark_to_market(prices)
    snap = pf.export_snapshot(prices)
    target_status = compute_status(STARTING_CAPITAL, snap.get("equity_curve", []))
    snap["target_status"] = target_status
    (DATA_DIR / "paper_portfolio.json").write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    logger.info(f"Paper: equity Rs{snap['current_equity']:,.0f} | P&L {snap['total_pnl_pct']:+.2f}%")

    # ---- 5. Sync to Vercel ----
    logger.info("Syncing to Vercel...")
    main_ok = post_main_analysis(combined)
    pp_ok = post_blob("paper_portfolio", snap)
    logger.info(f"  main analysis: {'OK' if main_ok else 'FAIL'} | paper portfolio: {'OK' if pp_ok else 'FAIL'}")

    logger.info("Intraday refresh complete")


def _fire_regime_alert(prev: str, new: str, reason: str):
    """Push a regime-change alert to alerts.json so dashboard /alerts shows it."""
    try:
        from alerts.channels import dispatch
        dispatch("warning",
                 f"REGIME CHANGED INTRADAY: {prev} -> {new}",
                 f"{reason} | Picks won't change until next 15:45 run unless forced")
    except Exception as e:
        logger.warning(f"Alert dispatch failed: {e}")


if __name__ == "__main__":
    intraday_refresh()

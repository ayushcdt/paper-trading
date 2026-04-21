"""
Lightweight intraday mark-to-market.

Pulls live LTPs for currently-open paper positions only, updates the paper
portfolio JSON, and syncs to Vercel. ~5 seconds per run; no analysis, no
news, no picks.

Schedule: every 15 min during NSE hours (09:15 - 15:30 IST).

Bails out cleanly outside market hours so it can be safely scheduled to run
all day without spamming Angel API.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper.portfolio import PaperPortfolio
from data_fetcher import get_fetcher


MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)


def is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:                   # Sat=5, Sun=6
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def mark_only():
    if not is_market_hours():
        logger.info("Outside NSE market hours; skipping mark-to-market")
        return

    pf = PaperPortfolio()
    open_syms = pf.get_open_symbols()
    if not open_syms:
        logger.info("No open positions; nothing to mark")
        return

    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            logger.error("Angel login failed")
            return

    prices = {}
    for sym in open_syms:
        try:
            ltp = float(fetcher.get_ltp(sym).get("ltp", 0))
            if ltp > 0:
                prices[sym] = ltp
        except Exception as e:
            logger.warning(f"LTP fetch failed for {sym}: {e}")

    if not prices:
        logger.warning("No LTPs fetched; aborting")
        return

    n = pf.mark_to_market(prices)
    snap = pf.export_snapshot(prices)
    logger.info(
        f"Marked {n} positions | equity Rs{snap['current_equity']:,.0f} | "
        f"P&L {snap['total_pnl_pct']:+.2f}% | unrealized Rs{snap['unrealized_pnl']:,.0f}"
    )

    # Also recompute target status with fresh equity curve
    try:
        from adaptive.targets import compute_status
        from paper.portfolio import STARTING_CAPITAL
        target_status = compute_status(STARTING_CAPITAL, snap.get("equity_curve", []))
        snap["target_status"] = target_status
        Path(__file__).resolve().parent.parent.parent.joinpath(
            "data", "paper_portfolio.json"
        ).write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Target status update failed: {e}")

    # Sync just the paper_portfolio blob (skip the heavy backtest blobs)
    try:
        import requests
        from config import VERCEL_CONFIG
        r = requests.post(
            f"{VERCEL_CONFIG['app_url']}/api/blob?key=paper_portfolio",
            json=snap,
            headers={
                "Content-Type": "application/json",
                "x-api-key": VERCEL_CONFIG["secret_key"],
            },
            timeout=15,
        )
        if r.status_code == 200:
            logger.info("Synced paper_portfolio to Vercel")
        else:
            logger.warning(f"Vercel sync failed: {r.status_code}")
    except Exception as e:
        logger.warning(f"Vercel sync error: {e}")


if __name__ == "__main__":
    mark_only()

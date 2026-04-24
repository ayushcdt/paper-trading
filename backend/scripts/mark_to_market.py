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
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper.portfolio import PaperPortfolio
from data_fetcher import get_fetcher
from common.market_hours import is_market_hours, now_ist

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Discipline rule: skip the entry if the open price gapped >2% from the
# intended_entry_price (the prior-day close). Captures both gap-ups (chase risk)
# and gap-downs (catching a falling knife / overnight news shock).
GAP_GUARD_PCT = 2.0
# Cancel pending_opens that have been waiting for more than this many days.
PENDING_TTL_DAYS = 2


def _load_open_ticks_today() -> dict:
    """Read today's first-post-open tick map captured by ws_runner."""
    today = now_ist().strftime("%Y-%m-%d")
    p = DATA_DIR / f"open_ticks_{today}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("ticks", {}) or {}
    except Exception as e:
        logger.warning(f"Failed reading open_ticks file {p.name}: {e}")
        return {}


def _fill_pending(pf: PaperPortfolio) -> tuple[int, int, int]:
    """Try to fill any pending_opens at today's open price.
    Returns (filled, gap_skipped, expired) counts."""
    pendings = pf.get_pending_opens()
    if not pendings:
        return (0, 0, 0)
    open_ticks = _load_open_ticks_today()
    if not open_ticks:
        logger.info(f"{len(pendings)} pending_opens but no open_ticks captured yet today; will retry")
    filled = gap_skipped = expired = 0
    today_iso = now_ist().isoformat()
    cutoff = (now_ist() - timedelta(days=PENDING_TTL_DAYS)).isoformat()
    for p in pendings:
        sym = p["symbol"]
        # TTL check
        if p["queued_at"] < cutoff:
            pf.cancel_pending_open(sym)
            logger.warning(f"Pending {sym} expired (queued {p['queued_at'][:16]}, TTL {PENDING_TTL_DAYS}d) -- cancelled")
            expired += 1
            continue
        tick = open_ticks.get(sym)
        if not tick:
            continue  # not yet captured; will retry next run
        fill_price = float(tick.get("ltp", 0))
        ref_price = float(p["intended_entry_price"])
        if fill_price <= 0 or ref_price <= 0:
            continue
        gap_pct = abs(fill_price - ref_price) / ref_price * 100
        if gap_pct > GAP_GUARD_PCT:
            pf.cancel_pending_open(sym)
            logger.warning(
                f"Pending {sym} GAP-SKIPPED: open Rs{fill_price:.2f} vs intended Rs{ref_price:.2f} "
                f"({gap_pct:+.1f}% gap > {GAP_GUARD_PCT}%) -- cancelled"
            )
            gap_skipped += 1
            continue
        pos = pf.execute_pending(sym, fill_price=fill_price, fill_time_iso=tick.get("captured_at_ist", today_iso))
        if pos:
            logger.info(
                f"Pending {sym} FILLED at Rs{fill_price:.2f} (intended Rs{ref_price:.2f}, "
                f"gap {gap_pct:+.1f}%, qty {pos.qty})"
            )
            filled += 1
    return (filled, gap_skipped, expired)


def mark_only():
    if not is_market_hours():
        logger.info("Outside NSE market hours; skipping mark-to-market")
        return

    pf = PaperPortfolio()

    # Phase 1: try to fill any pending_opens (Option C: next-day-open execution)
    filled, gap_skipped, expired = _fill_pending(pf)
    if filled or gap_skipped or expired:
        logger.info(f"Pending fills: filled={filled}, gap_skipped={gap_skipped}, expired={expired}")

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

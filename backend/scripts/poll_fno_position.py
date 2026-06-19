"""
Polls open F&O positions every N seconds, fetches option LTPs via Angel API,
and injects them into paper_portfolio.json + Vercel push.

Workaround: ws_runner subscribes only to NSE (exchange_type=1). Options live
on NFO (exchange_type=2). Without this script, F&O positions show entry_price
but no current_price and the per-tick stop/target check never fires.

This script effectively brings the option position to life on the dashboard
and provides stop/target enforcement via periodic checks.

Stop/target rules in premium space (P12 design):
  premium <= entry * 0.75   -> close at -25% (stop)
  premium >= entry * 1.50   -> close at +50% (target)

Run during market hours, backgrounded.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from logzero import logger
from paper.portfolio import PaperPortfolio
from common.market_hours import is_market_hours
from config import VERCEL_CONFIG


# 2026-05-04: reduced from 20s to 5s after a 0-DTE NIFTY 24350CE position
# had its -25% stop slip to -33% on slippage between 20s polls. Premium on
# near-expiry options can move 5-10% between checks on a sharp NIFTY move.
POLL_INTERVAL_SEC = 5


def _resolve_fno_contract(symbol: str):
    """Given a tradingsymbol like NIFTY05MAY2623800PE, find the contract row."""
    try:
        from fno.nfo_master import index_by_underlying
        idx = index_by_underlying()
        # Heuristic: take prefix before first digit as underlying
        underlying = ""
        for ch in symbol:
            if ch.isdigit():
                break
            underlying += ch
        for c in idx.get(underlying, []):
            if c.get("symbol", "").upper() == symbol.upper():
                return c
    except Exception as e:
        logger.warning(f"resolve {symbol} failed: {e}")
    return None


def _fetch_option_ltp(contract):
    try:
        from fno.option_chain import get_option_ltp
        return get_option_ltp(contract)
    except Exception as e:
        logger.warning(f"option LTP fetch failed: {e}")
        return None


def main():
    pf = PaperPortfolio()
    logger.info("F&O position poller starting")
    while True:
        try:
            if not is_market_hours():
                time.sleep(60)
                continue
            positions = pf.get_open_positions()
            fno_positions = {sym: p for sym, p in positions.items()
                            if p.variant in ("fno_put", "fno_call", "fno_future")}
            if not fno_positions:
                time.sleep(POLL_INTERVAL_SEC)
                continue

            opt_prices = {}
            for sym, pos in fno_positions.items():
                contract = _resolve_fno_contract(sym)
                if not contract:
                    logger.warning(f"contract not found: {sym}")
                    continue
                ltp = _fetch_option_ltp(contract)
                if not ltp or ltp <= 0:
                    continue
                opt_prices[sym] = ltp

                # Stop/target check
                entry = pos.entry_price
                pnl_pct = (ltp - entry) / entry * 100 if entry > 0 else 0
                if ltp <= pos.current_stop or (pos.stop_at_entry and ltp <= pos.stop_at_entry):
                    logger.warning(f"F&O STOP HIT: {sym} @ Rs{ltp:.2f} <= stop")
                    pf.close_position(sym, ltp, f"F&O premium stop hit @ Rs{ltp:.2f} ({pnl_pct:+.1f}%)")
                    try:
                        from alerts.channels import dispatch
                        dispatch("warning", f"F&O STOP: {sym}",
                                f"Closed at Rs{ltp:.2f} ({pnl_pct:+.1f}%)")
                    except Exception:
                        pass
                elif pos.target_price > 0 and ltp >= pos.target_price:
                    logger.info(f"F&O TARGET HIT: {sym} @ Rs{ltp:.2f}")
                    pf.close_position(sym, ltp, f"F&O premium target hit @ Rs{ltp:.2f} ({pnl_pct:+.1f}%)")
                    try:
                        from alerts.channels import dispatch
                        dispatch("info", f"F&O TARGET: {sym}",
                                f"Closed at Rs{ltp:.2f} (+{pnl_pct:.1f}%)")
                    except Exception:
                        pass

            # Push snapshot with F&O prices to Vercel
            if opt_prices:
                # Get equity stock prices via held set so combined snapshot is correct
                from streaming.paper_marker import get_marker
                stock_prices = dict(get_marker()._prices) if get_marker()._prices else {}
                stock_prices.update(opt_prices)
                snap = pf.export_snapshot(stock_prices)
                try:
                    requests.post(
                        f"{VERCEL_CONFIG['app_url']}/api/blob?key=paper_portfolio",
                        json=snap,
                        headers={"Content-Type": "application/json",
                                 "x-api-key": VERCEL_CONFIG["secret_key"]},
                        timeout=10,
                    )
                except Exception as e:
                    logger.debug(f"vercel push failed: {e}")

            time.sleep(POLL_INTERVAL_SEC)
        except Exception as e:
            logger.warning(f"poller iteration error: {e}")
            time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main()

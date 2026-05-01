"""
Catalyst-driven stock options auto-trader (P27).

When the existing news catalyst pipeline fires on an F&O-eligible single-stock
(Reliance, TCS, HDFC Bank, ITC, etc.), instead of buying the cash equity at
50% slot, we buy the OTM weekly stock option for leveraged exposure.

Why: catalyst-driven moves (USFDA approval, earnings beat, M&A) commonly
deliver +5-15% on the underlying in 1-3 days. Option premium gain on these
moves can be +50-200% if entered before the move completes.

Activation gate:
- ENABLE_CATALYST_OPTIONS flag (default True)
- Only fires for stocks in F&O segment
- Existing catalyst gates apply: 3+ articles, 2+ sources, positive sentiment,
  no negative keywords, no earnings in T-2/T+1 window, junk filter passed

Position sizing:
- 30% of equity per leg (smaller than NIFTY index plays)
- -25% premium stop, +75% premium target (catalyst moves bigger than index)
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


ENABLE_CATALYST_OPTIONS = False  # P28: disabled until catalyst-with-conviction is validated
CHECK_INTERVAL_SEC = 120          # check news catalysts every 2 min
SLOT_PCT_OF_EQUITY = 0.30
STOP_PCT = 0.75                   # -25% premium
TARGET_PCT = 1.75                 # +75% premium (catalyst gives bigger moves than index)
MIN_DAYS_TO_EXPIRY = 4
MAX_DAYS_TO_EXPIRY = 14

# F&O-eligible stock subset (most liquid; expand as needed)
FNO_ELIGIBLE_STOCKS = {
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "ITC", "SBIN", "KOTAKBANK",
    "LT", "AXISBANK", "BAJFINANCE", "ASIANPAINT", "MARUTI", "TITAN", "SUNPHARMA",
    "ULTRACEMCO", "WIPRO", "NESTLEIND", "HCLTECH", "TECHM", "ONGC", "JSWSTEEL",
    "TATASTEEL", "BAJAJFINSV", "ADANIENT", "ADANIPORTS", "COALINDIA", "GRASIM",
    "BRITANNIA", "CIPLA", "DRREDDY", "EICHERMOT", "DIVISLAB", "BPCL", "HEROMOTOCO",
    "INDUSINDBK", "HINDALCO", "BHARTIARTL", "BAJAJ-AUTO", "POWERGRID", "NTPC",
    "TATAMOTORS", "BANDHANBNK", "EXIDEIND", "VEDL",
}


def _open_stock_option(symbol: str, direction: str, equity: float) -> Optional[dict]:
    """Open ATM/OTM weekly stock option for catalyst trade."""
    try:
        from fno.nfo_master import list_expiries
        from fno.option_chain import find_atm_strike, find_contract, get_option_ltp, days_to_expiry
        from data_fetcher import get_fetcher
        from paper.portfolio import PaperPortfolio
    except Exception as e:
        logger.warning(f"catalyst_options imports failed: {e}")
        return None

    f = get_fetcher()
    if not f.logged_in:
        f.login()
    spot = float(f.get_ltp(symbol).get("ltp", 0))
    if spot <= 0:
        return None

    expiries = list_expiries(symbol, "OPTSTK")
    target_expiry = next(
        (e for e in expiries if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY),
        None,
    )
    if not target_expiry:
        logger.debug(f"catalyst_options[{symbol}]: no expiry in window")
        return None

    atm = find_atm_strike(symbol, spot, target_expiry, "OPTSTK")
    if not atm:
        return None
    opt_type = "CE" if direction == "BULLISH" else "PE"
    contract = find_contract(symbol, target_expiry, atm, opt_type, "OPTSTK")
    if not contract:
        return None
    premium = get_option_ltp(contract)
    if not premium or premium <= 0:
        return None
    lot_size = int(contract.get("lotsize", 0))
    cost = premium * lot_size

    max_spend = equity * SLOT_PCT_OF_EQUITY
    if cost > max_spend:
        logger.info(f"catalyst_options[{symbol}]: lot Rs{cost:.0f} > spend cap Rs{max_spend:.0f}")
        return None

    pf = PaperPortfolio()
    variant = "fno_call" if opt_type == "CE" else "fno_put"
    pos = pf.open_position(
        symbol=contract.get("symbol"),
        variant=variant,
        regime="CATALYST_STOCK_OPT",
        entry_price=premium, slot_notional=cost,
        stop=premium * STOP_PCT, target=premium * TARGET_PCT,
        reason=f"CATALYST OPT {direction}: {symbol} {atm}{opt_type} weekly",
    )
    if pos:
        logger.info(f"CATALYST OPT OPEN {contract.get('symbol')} qty={pos.qty} @ Rs{premium:.2f}")
        try:
            from alerts.channels import dispatch
            dispatch("info", f"Catalyst option: {symbol}",
                     f"{atm}{opt_type} @ Rs{premium:.2f}, lot {lot_size}, cost Rs{cost:.0f}")
        except Exception:
            pass
        return {"symbol": contract.get("symbol"), "cost": cost}
    return None


def catalyst_options_loop():
    """Polls catalyst signals every 2 min, opens stock options for F&O-eligible
    catalysts."""
    from common.market_hours import is_market_hours
    from paper.portfolio import PaperPortfolio
    from data_fetcher import get_fetcher

    logger.info("Catalyst options trader starting")
    while True:
        time.sleep(CHECK_INTERVAL_SEC)
        if not ENABLE_CATALYST_OPTIONS:
            continue
        if not is_market_hours():
            continue

        try:
            from news.catalyst_injection import scan_for_catalysts
            pf = PaperPortfolio()
            f = get_fetcher()
            if not f.logged_in:
                f.login()
            held_ltps = {s: float(f.get_ltp(s).get("ltp", 0)) for s in pf.get_open_symbols()}
            equity = pf.current_equity(held_ltps)
            held_underlyings = set()
            for sym in pf.get_open_symbols():
                # Extract underlying from option tradingsymbol
                und = ""
                for ch in sym:
                    if ch.isdigit():
                        break
                    und += ch
                held_underlyings.add(und.upper())

            catalysts = scan_for_catalysts(
                held_symbols=held_underlyings,
                available_cash=equity * SLOT_PCT_OF_EQUITY,
                target_slot=equity * SLOT_PCT_OF_EQUITY,
                risk_overlay_active=False,
            )
            if not catalysts:
                continue

            # Filter to F&O-eligible only and bullish-implied (catalyst implies upside)
            for c in catalysts[:2]:
                if c.symbol not in FNO_ELIGIBLE_STOCKS:
                    logger.debug(f"catalyst {c.symbol}: not in F&O-eligible set")
                    continue
                # Default to BULLISH unless catalyst keyword is bearish
                # (handled by negative-keyword block in scan_for_catalysts)
                _open_stock_option(c.symbol, "BULLISH", equity)

        except Exception as e:
            import traceback
            logger.warning(f"catalyst_options iteration error: {e}\n{traceback.format_exc()[:200]}")


if __name__ == "__main__":
    catalyst_options_loop()

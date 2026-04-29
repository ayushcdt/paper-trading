"""
Option chain fetcher: pulls live LTP for option contracts via Angel SmartAPI.

Used to:
  - Find ATM strike given underlying spot
  - Get current premium for ITM/ATM/OTM contracts
  - Estimate Greeks (delta proxy from moneyness, theta from days-to-expiry)

Dependencies: nfo_master loaded; data_fetcher (Angel API client).

Capital gate: module loads but is INERT until enabled by ENABLE_FNO_TRADING flag.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger

from fno.nfo_master import get_option_chain, list_expiries


def find_atm_strike(underlying: str, spot: float, expiry: str,
                    instrument: str = "OPTIDX") -> Optional[int]:
    """Round spot to nearest available strike for the given expiry."""
    contracts = get_option_chain(underlying, expiry=expiry, instrument=instrument)
    if not contracts:
        return None
    # Strikes in scrip-master are stored as INR x 100
    strikes = sorted({int(c.get("strike", 0)) // 100 for c in contracts if c.get("strike")})
    if not strikes:
        return None
    return min(strikes, key=lambda s: abs(s - spot))


def find_contract(underlying: str, expiry: str, strike: int, opt_type: str,
                  instrument: str = "OPTIDX") -> Optional[dict]:
    """opt_type: 'CE' or 'PE'."""
    contracts = get_option_chain(underlying, expiry=expiry, instrument=instrument)
    target_strike_units = strike * 100
    for c in contracts:
        sym = c.get("symbol", "")
        if int(c.get("strike", 0)) == target_strike_units and sym.upper().endswith(opt_type.upper()):
            return c
    return None


def get_option_ltp(contract: dict) -> Optional[float]:
    """Fetch live LTP for an option contract via Angel API.
    contract is one dict from nfo_master. Uses exchange_type=2 (NFO)."""
    try:
        from data_fetcher import get_fetcher
        from SmartApi import SmartConnect
        f = get_fetcher()
        if not f.logged_in:
            f.login()
        # Angel ltpData expects exchange + tradingsymbol + symboltoken
        token = contract.get("token")
        sym = contract.get("symbol")
        if not (token and sym):
            return None
        resp = f.api.ltpData("NFO", sym, token)
        if resp.get("status") and resp.get("data"):
            return float(resp["data"].get("ltp", 0))
        return None
    except Exception as e:
        logger.warning(f"option LTP fetch failed: {e}")
        return None


def days_to_expiry(expiry: str) -> int:
    """Days from today to the expiry date string (DDMMMYYYY format)."""
    try:
        d = datetime.strptime(expiry, "%d%b%Y")
        return max(0, (d - datetime.now()).days)
    except Exception:
        return 0


def estimate_premium_pct_change(underlying_pct_move: float, days_to_exp: int,
                                 moneyness: str = "atm") -> float:
    """Quick-and-dirty premium % change estimator.
    Used to size positions and set stops in premium-space.

    For a +1% underlying move:
      ATM weekly (~5d): premium moves ~30-50%
      ITM weekly:       premium moves ~80-120%
      OTM weekly:       premium moves ~50-100% (high gamma, but small base)

    This is a heuristic; production would use Black-Scholes Greeks."""
    delta = {"itm": 0.7, "atm": 0.5, "otm": 0.3}.get(moneyness, 0.5)
    # Effective move = delta * underlying% / option_premium_pct
    # For ATM weekly NIFTY at ~150 premium with spot 24500: 1% spot = 245 points
    # delta=0.5 => 122 points option move on Rs 150 premium = ~80% move
    # Crude approximation: premium_change_pct = delta * underlying_pct * leverage_factor
    leverage = 80 / max(1, days_to_exp)  # higher leverage closer to expiry
    return delta * underlying_pct_move * leverage


if __name__ == "__main__":
    print("== NIFTY option chain demo ==")
    expiries = list_expiries("NIFTY", "OPTIDX")
    print(f"NIFTY expiries: {expiries[:5]}")
    if expiries:
        next_exp = expiries[0]
        # Approximate spot
        spot_proxy = 24000  # would fetch live in real use
        atm = find_atm_strike("NIFTY", spot_proxy, next_exp, "OPTIDX")
        print(f"Nearest expiry {next_exp}, ATM strike at spot {spot_proxy}: {atm}")
        if atm:
            ce = find_contract("NIFTY", next_exp, atm, "CE", "OPTIDX")
            if ce:
                print(f"ATM CE: {ce.get('symbol')} token={ce.get('token')} lot={ce.get('lotsize')}")

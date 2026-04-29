"""
F&O signal translator. Converts equity momentum signals into option trades.

Activation gate: ENABLE_FNO_TRADING flag (default OFF). Even when ON, only
fires if account equity >= MIN_FNO_CAPITAL (Rs 25,000). Below that, the
SEBI 2024 lot-size rules make F&O impractical at retail scale.

Logic: when equity picker says "strong bullish on NIFTY universe / strong
breakout NIFTY 50 names", buy ATM weekly NIFTY call. Conversely for puts.
For single-stock signals, only trade options on stocks with active F&O
contracts AND sufficient liquidity (lot * premium <= 50% of available cash).

Risk caps:
  - Max 1 option position open at a time (avoid theta-stack)
  - Max 30% of equity in option premium (premium can go to zero)
  - Hard stop at -25% premium loss
  - Profit target +50% premium gain (1:2 risk-reward)
  - Hard same-day exit if held overnight (theta crush)

This module is INERT pending: capital >= MIN_FNO_CAPITAL AND
ENABLE_FNO_TRADING=True. P12 ships scaffolding; P13 will activate.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


# ---------- Activation gates ----------
ENABLE_FNO_TRADING = False     # Master switch — must flip True to enable
MIN_FNO_CAPITAL = 25_000.0     # Rs 25K minimum (covers 1 NIFTY ATM weekly lot)
MAX_FNO_PREMIUM_PCT = 30.0     # Cap option premium spend at 30% of equity
MAX_OPEN_FNO_POSITIONS = 1     # One option leg at a time

STOP_PREMIUM_LOSS_PCT = -25.0  # -25% premium = stop out
TARGET_PREMIUM_GAIN_PCT = 50.0 # +50% premium = book


@dataclass
class FNODecision:
    underlying: str
    direction: str         # "CALL" or "PUT"
    expiry: str
    strike: int
    contract_symbol: str
    contract_token: str
    lot_size: int
    intended_lots: int
    estimated_premium: float
    estimated_cost: float
    stop_premium: float
    target_premium: float
    reason: str


def can_trade_fno(current_equity: float) -> tuple[bool, str]:
    """Capital + flag gate. Returns (allowed, reason)."""
    if not ENABLE_FNO_TRADING:
        return False, "ENABLE_FNO_TRADING flag is OFF"
    if current_equity < MIN_FNO_CAPITAL:
        return False, f"equity Rs {current_equity:.0f} < MIN_FNO_CAPITAL Rs {MIN_FNO_CAPITAL:.0f}"
    return True, "ok"


def translate_signal(underlying: str, direction: str, current_equity: float,
                     spot: float) -> Optional[FNODecision]:
    """Equity signal -> option trade decision.
    direction: 'BULLISH' -> CALL, 'BEARISH' -> PUT.
    Returns None if gate fails or capital insufficient."""
    allowed, reason = can_trade_fno(current_equity)
    if not allowed:
        logger.debug(f"F&O trade blocked: {reason}")
        return None

    try:
        from fno.nfo_master import list_expiries
        from fno.option_chain import find_atm_strike, find_contract, get_option_ltp, days_to_expiry
    except Exception as e:
        logger.warning(f"fno imports failed: {e}")
        return None

    instrument = "OPTIDX" if underlying.upper() in ("NIFTY", "BANKNIFTY", "FINNIFTY") else "OPTSTK"
    expiries = list_expiries(underlying, instrument)
    if not expiries:
        return None
    # Pick the nearest expiry that has at least 2 days to go (avoid same-day expiry)
    for exp in expiries:
        if days_to_expiry(exp) >= 2:
            target_expiry = exp
            break
    else:
        return None

    atm = find_atm_strike(underlying, spot, target_expiry, instrument)
    if not atm:
        return None

    opt_type = "CE" if direction.upper() == "BULLISH" else "PE"
    contract = find_contract(underlying, target_expiry, atm, opt_type, instrument)
    if not contract:
        return None

    premium = get_option_ltp(contract)
    if not premium or premium <= 0:
        return None
    lot_size = int(contract.get("lotsize", 0))
    if lot_size <= 0:
        return None

    cost_per_lot = premium * lot_size
    max_spend = current_equity * (MAX_FNO_PREMIUM_PCT / 100.0)
    if cost_per_lot > max_spend:
        logger.info(f"F&O blocked: lot cost Rs {cost_per_lot:.0f} > max spend Rs {max_spend:.0f}")
        return None

    intended_lots = min(MAX_OPEN_FNO_POSITIONS, int(max_spend / cost_per_lot))
    if intended_lots < 1:
        return None

    return FNODecision(
        underlying=underlying,
        direction="CALL" if opt_type == "CE" else "PUT",
        expiry=target_expiry,
        strike=atm,
        contract_symbol=contract.get("symbol", ""),
        contract_token=str(contract.get("token", "")),
        lot_size=lot_size,
        intended_lots=intended_lots,
        estimated_premium=premium,
        estimated_cost=cost_per_lot * intended_lots,
        stop_premium=premium * (1 + STOP_PREMIUM_LOSS_PCT / 100.0),
        target_premium=premium * (1 + TARGET_PREMIUM_GAIN_PCT / 100.0),
        reason=f"{direction} signal on {underlying}; ATM {atm}{opt_type} weekly @ Rs {premium:.2f}",
    )


if __name__ == "__main__":
    # Demo: hypothetical bullish NIFTY signal at spot 24000 with Rs 50K equity
    d = translate_signal("NIFTY", "BULLISH", current_equity=50_000.0, spot=24000)
    if d:
        print(f"Decision: {d.contract_symbol} qty={d.intended_lots}lot ({d.intended_lots * d.lot_size}sh)")
        print(f"  Cost Rs {d.estimated_cost:.0f}  stop Rs {d.stop_premium:.2f}  target Rs {d.target_premium:.2f}")
    else:
        print(f"No F&O trade (gate: {can_trade_fno(50_000.0)})")

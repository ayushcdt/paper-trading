"""
Position management — runs every 15 min during market hours via mark_to_market.

For each held position, applies in priority order:
  1. STOP HIT       — current price <= current_stop -> close at LTP
  2. TARGET HIT     — current price >= target_price -> close at LTP
  3. TRAILING       — if price has moved favourably, raise stop to lock in profit
  4. TIME EXIT      — if held > MAX_HOLD_DAYS, close (rotate capital)

Rules:
  - TRAILING: when price is +5% from entry, raise stop to entry (lock in zero loss).
              When price is +10% from entry, raise stop to entry + half the gain.
              When price is +15%+, raise stop to current - 3% (trail by 3%).
  - TIME EXIT: 30 days for momentum_agg picks, 5 days for catalyst opens.
  - All exits logged to trade_log with explicit reason for audit.
  - Telegram alert per exit so you see it immediately.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from paper.portfolio import PaperPortfolio


# ---------- Config ----------
MAX_HOLD_DAYS_DEFAULT = 30
MAX_HOLD_DAYS_CATALYST = 5

TRAIL_LEVEL_1_PCT = 5.0     # at +5% gain
TRAIL_LEVEL_1_NEW_STOP = "breakeven"   # raise stop to entry

TRAIL_LEVEL_2_PCT = 10.0    # at +10% gain
TRAIL_LEVEL_2_NEW_STOP_FRAC = 0.5  # raise stop to entry + 50% of gain

TRAIL_LEVEL_3_PCT = 15.0    # at +15% gain
TRAIL_LEVEL_3_TRAIL_PCT = 3.0  # trail by 3% from current price


def _alert(severity: str, title: str, body: str) -> None:
    try:
        from alerts.channels import dispatch
        dispatch(severity, title, body)
    except Exception:
        pass


def _check_position(pf: PaperPortfolio, sym: str, pos, ltp: float) -> dict | None:
    """Apply all checks to one position. Returns action dict if something fires, else None.
    Action dict: {'action': 'CLOSE'|'TRAIL', 'reason': str, 'pnl_inr': float, ...}"""
    if ltp <= 0 or pos.entry_price <= 0:
        return None

    pnl_pct = (ltp - pos.entry_price) / pos.entry_price * 100
    today = datetime.now().date()
    try:
        entry_date = datetime.fromisoformat(pos.entry_date).date()
    except Exception:
        entry_date = today
    days_held = (today - entry_date).days

    effective_stop = pos.current_stop or pos.stop_at_entry
    target = pos.target_price

    # 1. STOP HIT
    if effective_stop > 0 and ltp <= effective_stop:
        result = pf.close_position(sym, ltp, f"stop hit (Rs{ltp:.2f} <= stop Rs{effective_stop:.2f}, P&L {pnl_pct:+.2f}%)")
        if result:
            _alert("warning", f"STOP HIT: {sym}",
                   f"Closed at Rs{ltp:.2f} (stop Rs{effective_stop:.2f})\nP&L: Rs{result.get('pnl_inr', 0):+.0f} ({pnl_pct:+.2f}%)")
            return {"action": "CLOSE", "reason": "stop", "pnl_inr": result.get("pnl_inr", 0)}

    # 2. TARGET HIT
    if target > 0 and ltp >= target:
        result = pf.close_position(sym, ltp, f"target hit (Rs{ltp:.2f} >= target Rs{target:.2f}, P&L {pnl_pct:+.2f}%)")
        if result:
            _alert("info", f"TARGET HIT: {sym}",
                   f"Closed at Rs{ltp:.2f} (target Rs{target:.2f})\nP&L: Rs{result.get('pnl_inr', 0):+.0f} ({pnl_pct:+.2f}%)")
            return {"action": "CLOSE", "reason": "target", "pnl_inr": result.get("pnl_inr", 0)}

    # 3. TIME EXIT
    max_hold = MAX_HOLD_DAYS_CATALYST if pos.variant == "catalyst" else MAX_HOLD_DAYS_DEFAULT
    if days_held >= max_hold:
        result = pf.close_position(sym, ltp, f"time exit ({days_held}d held >= {max_hold}d max, P&L {pnl_pct:+.2f}%)")
        if result:
            _alert("info", f"TIME EXIT: {sym}",
                   f"Closed at Rs{ltp:.2f} after {days_held} days\nP&L: Rs{result.get('pnl_inr', 0):+.0f} ({pnl_pct:+.2f}%)")
            return {"action": "CLOSE", "reason": "time", "pnl_inr": result.get("pnl_inr", 0)}

    # 4. TRAILING STOP — raise stop as price climbs
    new_stop = effective_stop
    raised = False
    if pnl_pct >= TRAIL_LEVEL_3_PCT:
        candidate = ltp * (1 - TRAIL_LEVEL_3_TRAIL_PCT / 100)
        if candidate > new_stop:
            new_stop = candidate
            raised = True
    elif pnl_pct >= TRAIL_LEVEL_2_PCT:
        gain = ltp - pos.entry_price
        candidate = pos.entry_price + (gain * TRAIL_LEVEL_2_NEW_STOP_FRAC)
        if candidate > new_stop:
            new_stop = candidate
            raised = True
    elif pnl_pct >= TRAIL_LEVEL_1_PCT:
        candidate = pos.entry_price  # breakeven
        if candidate > new_stop:
            new_stop = candidate
            raised = True

    if raised:
        pf.update_position_stop_target(sym, new_stop=new_stop)
        logger.info(f"TRAILING {sym}: stop raised Rs{effective_stop:.2f} -> Rs{new_stop:.2f} (P&L {pnl_pct:+.2f}%)")
        # No alert for trailing — too noisy; only stop/target hits get alerts
        return {"action": "TRAIL", "reason": f"stop raised to Rs{new_stop:.2f}", "pnl_pct": pnl_pct}

    return None


def manage_positions(pf: PaperPortfolio, latest_prices: dict[str, float]) -> dict:
    """Iterate all open positions, apply position-management checks.
    Returns summary {'closed': [...], 'trailed': [...]}.
    Caller (mark_to_market) calls AFTER fill_pending and BEFORE rebalance,
    so freshly opened positions get a clean check on first 15-min cycle."""
    out = {"closed": [], "trailed": []}
    open_positions = pf.get_open_positions()
    for sym, pos in open_positions.items():
        ltp = latest_prices.get(sym)
        if not ltp:
            continue
        result = _check_position(pf, sym, pos, ltp)
        if not result:
            continue
        if result["action"] == "CLOSE":
            out["closed"].append({"symbol": sym, "reason": result["reason"], "pnl_inr": result.get("pnl_inr", 0)})
        elif result["action"] == "TRAIL":
            out["trailed"].append({"symbol": sym, "reason": result["reason"]})
    if out["closed"] or out["trailed"]:
        logger.info(f"Position management: closed={len(out['closed'])} (stops/targets/time), trailed={len(out['trailed'])}")
    return out

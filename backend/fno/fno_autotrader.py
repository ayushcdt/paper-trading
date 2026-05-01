"""
F&O auto-trader (P20). Runs every 60s during market hours, detects NIFTY
directional reversal signals, and auto-opens option positions.

Replicates the manual play from 2026-04-30 (NIFTY 24050 CE, +43% in 84 min)
without requiring human intervention.

Signal logic:
  Track NIFTY price history (rolling 30 min in memory).
  Detect:
    BULLISH SIGNAL (-> buy CALL):
      - intraday move <= -0.5% from prev close (NIFTY was down)
      - currently within 0.3% of intraday low (just bottomed)
      - last 5 min: rising (recovery confirmation)
    BEARISH SIGNAL (-> buy PUT):
      - intraday move >= +0.5% from prev close (NIFTY was up)
      - currently within 0.3% of intraday high (just topped)
      - last 5 min: falling (rolloff confirmation)

Position sizing:
  - Pick OTM 1 strike (cheaper, higher gamma than ATM)
  - Lot cost <= 90% of available cash
  - -25% stop / +50% target (matches yesterday's winning bet)

Constraints:
  - Days to expiry 3-7 (theta-safe window)
  - Max 1 F&O position open (poller handles this; auto-trader checks)
  - Per-day max trades cap (default 4) -- prevent runaway
  - Drawdown circuit breaker integration (auto-halt if -15% from 7-day peak)

Run inside ws_runner thread or as standalone script.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


# ---------- Activation gates ----------
ENABLE_AUTOTRADER = True       # MASTER switch
SIGNAL_INTERVAL_SEC = 30       # check every 30s (P25: was 60s)
MAX_TRADES_PER_DAY = 8         # P25: was 4. Allows 1-2 trades per hour during active session

# ---------- Signal thresholds ----------
NIFTY_MOVE_THRESHOLD_PCT = 0.5     # min intraday move to consider a reversal
NEAR_EXTREME_PCT = 0.3             # how close to the day's high/low to trigger
RECOVERY_WINDOW_MIN = 5            # last N min to confirm recovery
RECOVERY_MIN_MOVE_PCT = 0.05       # min recovery/rolloff move in window (tuned 0.10 -> 0.05 for sensitivity)

# ---------- Per-index config ----------
# Add new index by adding entry here. Strike-step is the option chain spacing
# (NIFTY = 50, BANKNIFTY = 100). OTM_OFFSET adds N points beyond spot for
# OTM selection.
INDEX_CONFIG = {
    "NIFTY": {
        "underlying_token": "99926000",
        "otm_offset_points": 100,
        "strike_step": 50,
    },
    "BANKNIFTY": {
        "underlying_token": "99926009",
        "otm_offset_points": 200,    # BANKNIFTY moves more, wider OTM
        "strike_step": 100,
    },
}

# Legacy NIFTY-only config kept for backward compat
OTM_OFFSET_POINTS = 100            # NIFTY OTM strike offset (e.g. spot 24000 -> 24100 CE / 23900 PE)
MIN_DAYS_TO_EXPIRY = 3             # avoid expiry-day theta crush
MAX_DAYS_TO_EXPIRY = 7             # don't pay for too much time decay
LOT_COST_MAX_PCT = 0.90            # max % of available cash per leg
STOP_PREMIUM_PCT = 0.75            # premium drops 25% -> stop
TARGET_PREMIUM_PCT = 1.50          # premium rises 50% -> target


# ---------- Drawdown circuit breaker ----------
DD_HALT_PCT = -15.0                # halve sizes if equity drops this much from 7-day peak
DD_RESUME_PCT = -8.0               # restore full size when recovered


STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "fno_autotrader_state.json"


class NiftyHistory:
    """Rolling NIFTY price history (1 sample/min for last 30 min)."""
    def __init__(self, max_samples: int = 30):
        self.samples: deque = deque(maxlen=max_samples)  # (timestamp, price)

    def add(self, price: float):
        self.samples.append((datetime.now(), price))

    def latest(self) -> Optional[float]:
        return self.samples[-1][1] if self.samples else None

    def intraday_high(self) -> Optional[float]:
        return max((s[1] for s in self.samples), default=None) if self.samples else None

    def intraday_low(self) -> Optional[float]:
        return min((s[1] for s in self.samples), default=None) if self.samples else None

    def move_in_window(self, window_min: int) -> Optional[float]:
        """Return % move over last N minutes."""
        if not self.samples:
            return None
        cutoff = datetime.now() - timedelta(minutes=window_min)
        in_window = [s for s in self.samples if s[0] >= cutoff]
        if len(in_window) < 2:
            return None
        first = in_window[0][1]
        last = in_window[-1][1]
        return (last - first) / first * 100 if first > 0 else None


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"trades_today": 0, "last_trade_date": "", "drawdown_halt_active": False}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"trades_today": 0, "last_trade_date": "", "drawdown_halt_active": False}


def _save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def _check_drawdown_halt() -> tuple[bool, str]:
    """Returns (halt_active, reason). Halt = halve sizes / skip trades."""
    try:
        from paper.portfolio import PaperPortfolio
        pf = PaperPortfolio()
        curve = pf.equity_curve(days=7)
        if len(curve) < 3:
            return False, "drawdown gate: insufficient history"
        peak = max(c.get("equity", 0) for c in curve)
        latest = curve[-1].get("equity", 0)
        if peak <= 0:
            return False, "drawdown gate: peak zero"
        dd = (latest - peak) / peak * 100
        if dd <= DD_HALT_PCT:
            return True, f"drawdown {dd:.1f}% from 7d peak (Rs{peak:.0f} -> Rs{latest:.0f}): HALT"
        return False, f"drawdown {dd:+.1f}% (within tolerance)"
    except Exception as e:
        logger.warning(f"drawdown check failed: {e}")
        return False, "drawdown gate skipped"


def _detect_signal(history: NiftyHistory, prev_close: float) -> Optional[str]:
    """Returns 'BULLISH' (-> buy CALL), 'BEARISH' (-> buy PUT), or None."""
    spot = history.latest()
    high = history.intraday_high()
    low = history.intraday_low()
    if not (spot and high and low and prev_close > 0):
        return None
    intraday_pct = (spot - prev_close) / prev_close * 100
    recovery_pct = history.move_in_window(RECOVERY_WINDOW_MIN)

    # BULLISH reversal: was down, just bottomed, now recovering
    near_low = (spot - low) / spot * 100 < NEAR_EXTREME_PCT
    if (intraday_pct <= -NIFTY_MOVE_THRESHOLD_PCT
            and near_low
            and recovery_pct is not None and recovery_pct >= RECOVERY_MIN_MOVE_PCT):
        return "BULLISH"

    # BEARISH reversal: was up, just topped, now declining
    near_high = (high - spot) / spot * 100 < NEAR_EXTREME_PCT
    if (intraday_pct >= NIFTY_MOVE_THRESHOLD_PCT
            and near_high
            and recovery_pct is not None and recovery_pct <= -RECOVERY_MIN_MOVE_PCT):
        return "BEARISH"

    return None


# P26 multi-strike ladder: when signal fires AND equity allows, open BOTH
# OTM (high gamma, cheap leverage) AND ATM (high delta) of same direction.
# Two-bet exposure captures both small + large moves of underlying.
ENABLE_MULTI_STRIKE = True
MULTI_STRIKE_MIN_EQUITY = 25_000   # only ladder when capital permits both legs


def _open_option(direction: str, spot: float, equity: float,
                 underlying: str = "NIFTY") -> Optional[dict]:
    """Open the option position. underlying = 'NIFTY' or 'BANKNIFTY'.
    Returns dict on success, None on failure."""
    try:
        from fno.nfo_master import list_expiries
        from fno.option_chain import find_contract, get_option_ltp, days_to_expiry
        from paper.portfolio import PaperPortfolio
    except Exception as e:
        logger.warning(f"fno imports failed: {e}")
        return None

    cfg = INDEX_CONFIG.get(underlying.upper(), INDEX_CONFIG["NIFTY"])
    otm_offset = cfg["otm_offset_points"]
    strike_step = cfg["strike_step"]

    # Pick expiry in window
    expiries = list_expiries(underlying, "OPTIDX")
    target_expiry = None
    for exp in expiries:
        d = days_to_expiry(exp)
        if MIN_DAYS_TO_EXPIRY <= d <= MAX_DAYS_TO_EXPIRY:
            target_expiry = exp
            break
    if not target_expiry:
        logger.info(f"autotrader[{underlying}]: no expiry in {MIN_DAYS_TO_EXPIRY}-{MAX_DAYS_TO_EXPIRY}d window")
        return None

    # Pick strike (round to strike step)
    if direction == "BULLISH":
        strike = int((spot + otm_offset) / strike_step) * strike_step
        opt_type = "CE"
    else:
        strike = int((spot - otm_offset) / strike_step) * strike_step
        opt_type = "PE"

    contract = find_contract(underlying, target_expiry, strike, opt_type, "OPTIDX")
    if not contract:
        logger.warning(f"autotrader[{underlying}]: contract not found for {strike}{opt_type} {target_expiry}")
        return None

    premium = get_option_ltp(contract)
    if not premium or premium <= 0:
        logger.warning(f"autotrader[{underlying}]: premium fetch failed for {contract.get('symbol')}")
        return None

    lot_size = int(contract.get("lotsize", 0))
    cost = premium * lot_size
    if cost > equity * LOT_COST_MAX_PCT:
        logger.info(f"autotrader: lot cost Rs{cost:.0f} > {LOT_COST_MAX_PCT*100:.0f}% of equity Rs{equity:.0f}")
        return None

    sym = contract.get("symbol")
    stop = premium * STOP_PREMIUM_PCT
    target = premium * TARGET_PREMIUM_PCT

    pf = PaperPortfolio()
    variant = "fno_call" if opt_type == "CE" else "fno_put"
    regime = "BULLISH_REVERSAL" if direction == "BULLISH" else "BEARISH_REVERSAL"
    pos = pf.open_position(
        symbol=sym, variant=variant, regime=regime,
        entry_price=premium, slot_notional=cost,
        stop=stop, target=target,
        reason=f"AUTOTRADER {direction}: NIFTY reversal at spot {spot:.0f}, {strike}{opt_type}",
    )
    if not pos:
        logger.warning(f"autotrader: open_position rejected {sym}")
        return None
    logger.info(f"AUTOTRADER OPEN {sym} qty={pos.qty} @ Rs{premium:.2f} "
                f"(spot {spot:.0f}, stop Rs{stop:.2f}, target Rs{target:.2f})")
    try:
        from alerts.channels import dispatch
        dispatch("info", f"AUTOTRADER {direction}: {sym}",
                 f"Spot {spot:.0f}, strike {strike}{opt_type}\n"
                 f"Premium Rs {premium:.2f}, lot {lot_size}, cost Rs {cost:.0f}\n"
                 f"Stop Rs {stop:.2f} (-25%), target Rs {target:.2f} (+50%)")
    except Exception:
        pass
    return {"symbol": sym, "direction": direction, "premium": premium, "cost": cost}


def _open_option_atm(direction: str, spot: float, equity: float,
                      underlying: str = "NIFTY") -> Optional[dict]:
    """ATM-strike variant for ladder mode. Higher delta, more responsive
    to small underlying moves. More expensive than OTM."""
    try:
        from fno.nfo_master import list_expiries
        from fno.option_chain import find_atm_strike, find_contract, get_option_ltp, days_to_expiry
        from paper.portfolio import PaperPortfolio
    except Exception:
        return None

    cfg = INDEX_CONFIG.get(underlying.upper(), INDEX_CONFIG["NIFTY"])
    expiries = list_expiries(underlying, "OPTIDX")
    target_expiry = next((e for e in expiries if MIN_DAYS_TO_EXPIRY <= days_to_expiry(e) <= MAX_DAYS_TO_EXPIRY), None)
    if not target_expiry:
        return None

    atm_strike = find_atm_strike(underlying, spot, target_expiry, "OPTIDX")
    if not atm_strike:
        return None
    opt_type = "CE" if direction == "BULLISH" else "PE"
    contract = find_contract(underlying, target_expiry, atm_strike, opt_type, "OPTIDX")
    if not contract:
        return None
    premium = get_option_ltp(contract)
    if not premium or premium <= 0:
        return None
    lot_size = int(contract.get("lotsize", 0))
    cost = premium * lot_size
    if cost > equity * LOT_COST_MAX_PCT:
        return None

    pf = PaperPortfolio()
    variant = "fno_call" if opt_type == "CE" else "fno_put"
    pos = pf.open_position(
        symbol=contract.get("symbol"), variant=variant,
        regime=f"{direction}_LADDER_ATM",
        entry_price=premium, slot_notional=cost,
        stop=premium * STOP_PREMIUM_PCT, target=premium * TARGET_PREMIUM_PCT,
        reason=f"AUTOTRADER LADDER ATM {direction}: {underlying} spot {spot:.0f}, {atm_strike}{opt_type}",
    )
    if pos:
        logger.info(f"AUTOTRADER LADDER ATM {contract.get('symbol')} qty={pos.qty} @ Rs{premium:.2f}")
        return {"symbol": contract.get("symbol"), "premium": premium, "cost": cost}
    return None


def autotrader_loop(underlying: str = "NIFTY"):
    """Main loop for one underlying. Run from ws_runner per index.
    underlying: 'NIFTY' or 'BANKNIFTY'."""
    from common.market_hours import is_market_hours
    from data_fetcher import get_fetcher
    from paper.portfolio import PaperPortfolio

    logger.info(f"F&O autotrader starting for {underlying}")
    history = NiftyHistory()
    prev_close = None

    while True:
        time.sleep(SIGNAL_INTERVAL_SEC)
        if not ENABLE_AUTOTRADER:
            continue
        if not is_market_hours():
            continue

        try:
            f = get_fetcher()
            if not f.logged_in:
                f.login()
            data = f.get_ltp(underlying)
            spot = float(data.get("ltp", 0))
            if spot <= 0:
                continue
            if prev_close is None:
                prev_close = float(data.get("close", 0))
            history.add(spot)
            if len(history.samples) < 5:
                # Need history before signal
                continue

            # Reset daily trade counter
            state = _load_state()
            today = datetime.now().strftime("%Y-%m-%d")
            if state.get("last_trade_date") != today:
                state["trades_today"] = 0
                state["last_trade_date"] = today
                _save_state(state)

            if state.get("trades_today", 0) >= MAX_TRADES_PER_DAY:
                continue

            # Check drawdown halt
            halt, halt_reason = _check_drawdown_halt()
            if halt:
                logger.info(f"autotrader: {halt_reason}")
                continue

            # Detect signal first (cheap)
            signal = _detect_signal(history, prev_close)
            if not signal:
                continue

            # P23/P25: multi-leg support with capital-scaled cap.
            # Cap scales: 2 at <Rs 25K, 4 at Rs 25K-1L, 6 at Rs 1L+
            pf = PaperPortfolio()
            held_ltps = {s: float(f.get_ltp(s).get("ltp", 0)) for s in pf.get_open_symbols()}
            equity = pf.current_equity(held_ltps)
            if equity >= 100_000:
                MAX_OPEN_FNO_LEGS = 6
            elif equity >= 25_000:
                MAX_OPEN_FNO_LEGS = 4
            else:
                MAX_OPEN_FNO_LEGS = 2

            # Total cap across all F&O
            held_calls = sum(1 for p in pf.get_open_positions().values() if p.variant == "fno_call")
            held_puts = sum(1 for p in pf.get_open_positions().values() if p.variant == "fno_put")
            if (held_calls + held_puts) >= MAX_OPEN_FNO_LEGS:
                continue
            # Per-underlying duplicate-direction guard. Blocks 2nd NIFTY CALL
            # but allows 1 NIFTY CALL + 1 BANKNIFTY CALL (different underlyings).
            held_calls_this_und = sum(
                1 for sym, p in pf.get_open_positions().items()
                if p.variant == "fno_call" and sym.upper().startswith(underlying.upper())
            )
            held_puts_this_und = sum(
                1 for sym, p in pf.get_open_positions().items()
                if p.variant == "fno_put" and sym.upper().startswith(underlying.upper())
            )
            if signal == "BULLISH" and held_calls_this_und > 0:
                continue
            if signal == "BEARISH" and held_puts_this_und > 0:
                continue

            # P26 ladder: at sufficient capital, open BOTH OTM + ATM legs
            # for higher upside capture if signal is correct
            opens_done = 0
            if ENABLE_MULTI_STRIKE and equity >= MULTI_STRIKE_MIN_EQUITY and (held_calls + held_puts) <= MAX_OPEN_FNO_LEGS - 2:
                # OTM leg first (matches the original)
                otm_res = _open_option(signal, spot, equity * 0.5, underlying=underlying)
                if otm_res:
                    opens_done += 1
                    state["trades_today"] = state.get("trades_today", 0) + 1
                    # Now try ATM leg with remaining cash
                    atm_res = _open_option_atm(signal, spot, equity * 0.5, underlying=underlying)
                    if atm_res:
                        opens_done += 1
                        state["trades_today"] = state.get("trades_today", 0) + 1
                _save_state(state)
            else:
                # Single leg (original behavior at small capital)
                result = _open_option(signal, spot, equity, underlying=underlying)
                if result:
                    state["trades_today"] = state.get("trades_today", 0) + 1
                    _save_state(state)
                    opens_done += 1
            if opens_done > 0:
                logger.info(f"autotrader[{underlying}]: opened {opens_done} legs for {signal} signal")

        except Exception as e:
            import traceback
            logger.warning(f"autotrader iteration error: {e}\n{traceback.format_exc()[:300]}")


if __name__ == "__main__":
    autotrader_loop()

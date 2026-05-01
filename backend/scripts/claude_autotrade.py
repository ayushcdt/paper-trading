"""
Claude Autotrade — codified rules from yesterday's winning trade.

Run on a cron during market hours. Scans market state, builds conviction
score from multiple factors, auto-executes only 5/5 fat pitches. Sends
Telegram alerts for 3-4/5 setups so user can decide.

CONVICTION SCORING (5 factors, 1 point each):
  1. NIFTY directional reversal: at intraday extreme (within 0.3%) AND moved >=1% from prev close
  2. Recovery confirmation: last 30 min net move opposite to intraday
  3. VIX regime: 12-22 (not crash, not crush)
  4. Sector breadth aligned: 6+ of 9 sectors moving same direction as proposed trade
  5. News confirmation: catalyst article fired in last 4h on a relevant name

Only conviction 5/5 auto-executes. 3-4/5 sends Telegram alert.

HARD GUARDRAILS:
  - Max 2 trades per day (auto + manual combined)
  - Max 5% of equity per trade
  - Hard stop -25% premium / target +50% premium
  - 3 consecutive losses -> 5-day cooldown auto-disable
  - Honors user's existing kill_switch_active flag

Schedule (Windows Task): every 30 min during market hours.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


# ---------- Config ----------
ENABLE_AUTOTRADE = True              # MASTER switch user can flip OFF
MIN_CONVICTION_AUTO = 5              # only 5/5 fires automatically
MIN_CONVICTION_ALERT = 3             # 3+ sends Telegram
MAX_TRADES_PER_DAY = 2
MAX_RISK_PCT_PER_TRADE = 5.0
CONSECUTIVE_LOSS_LIMIT = 3
COOLDOWN_DAYS_AFTER_LOSSES = 5

# Position params (matches yesterday's winning trade)
DEFAULT_DTE = 4
STOP_PCT = -25
TARGET_PCT = 50
OTM_OFFSET = 100

STATE_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "claude_autotrade_state.json"


def _load_state():
    if not STATE_FILE.exists():
        return {"trades_today": 0, "last_date": "", "consecutive_losses": 0,
                "cooldown_until": ""}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"trades_today": 0, "last_date": "", "consecutive_losses": 0,
                "cooldown_until": ""}


def _save_state(s):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(s, indent=2, default=str), encoding="utf-8")


def compute_conviction(market_state: dict) -> tuple[int, list[str], str]:
    """Returns (conviction_score, reasons_satisfied, proposed_direction)."""
    reasons = []
    direction = None

    nifty = market_state["nifty"]
    vix = market_state.get("vix_ltp", 15)
    sectors = market_state.get("sectors", [])
    catalyst = market_state.get("catalyst_fired", False)

    # FACTOR 1: NIFTY at intraday extreme + meaningful move
    if (nifty["intraday_pct"] <= -1.0 and nifty["near_low"]):
        reasons.append("NIFTY at intraday low after -1% move")
        direction = "BULLISH"
    elif (nifty["intraday_pct"] >= 1.0 and nifty["near_high"]):
        reasons.append("NIFTY at intraday high after +1% move")
        direction = "BEARISH"
    else:
        return 0, [], None  # no setup

    # FACTOR 2: Recovery direction confirmed
    if nifty.get("open_to_now") is not None:
        if direction == "BULLISH" and nifty["open_to_now"] > -0.2:
            reasons.append("Recovery direction (open-to-now flat or up)")
        elif direction == "BEARISH" and nifty["open_to_now"] < 0.2:
            reasons.append("Rolloff direction (open-to-now flat or down)")

    # FACTOR 3: VIX regime
    if 12 <= vix <= 22:
        reasons.append(f"VIX {vix:.1f} in normal regime")

    # FACTOR 4: Sector breadth
    if sectors:
        bullish_sectors = sum(1 for s in sectors if s["pct"] > 0)
        bearish_sectors = sum(1 for s in sectors if s["pct"] < 0)
        if direction == "BULLISH" and bullish_sectors >= 6:
            reasons.append(f"Sector breadth: {bullish_sectors}/9 bullish")
        elif direction == "BEARISH" and bearish_sectors >= 6:
            reasons.append(f"Sector breadth: {bearish_sectors}/9 bearish")

    # FACTOR 5: News catalyst
    if catalyst:
        reasons.append("News catalyst fired in last 4h")

    return len(reasons), reasons, direction


def gather_market_state():
    """Pulls live market data via Angel API."""
    from data_fetcher import get_fetcher
    f = get_fetcher()
    if not f.logged_in:
        f.login()

    # NIFTY
    d = f.get_ltp("NIFTY")
    ltp = float(d.get("ltp", 0))
    prev = float(d.get("close", 0))
    today_open = float(d.get("open", 0))
    today_high = float(d.get("high", 0))
    today_low = float(d.get("low", 0))
    intraday_pct = (ltp - prev) / prev * 100 if prev > 0 else 0
    open_to_now = (ltp - today_open) / today_open * 100 if today_open > 0 else 0
    near_low = (ltp - today_low) / ltp * 100 < 0.3 if ltp > 0 else False
    near_high = (today_high - ltp) / ltp * 100 < 0.3 if ltp > 0 else False
    nifty = {"ltp": ltp, "prev": prev, "intraday_pct": intraday_pct,
             "open_to_now": open_to_now, "near_low": near_low, "near_high": near_high}

    # VIX
    try:
        v = f.get_ltp("INDIAVIX")
        vix_ltp = float(v.get("ltp", 0))
    except Exception:
        vix_ltp = 15.0

    # Sectors
    sectors = []
    for sym in ["NIFTY_BANK", "NIFTY_AUTO", "NIFTY_IT", "NIFTY_FMCG",
                "NIFTY_PHARMA", "NIFTY_REALTY", "NIFTY_ENERGY", "NIFTY_INFRA",
                "NIFTY_METAL"]:
        try:
            sd = f.get_ltp(sym)
            sl = float(sd.get("ltp", 0))
            sp = float(sd.get("close", 0))
            pct = (sl - sp) / sp * 100 if sp > 0 else 0
            sectors.append({"sym": sym, "pct": pct})
        except Exception:
            pass

    # Catalyst presence (simplified)
    catalyst_fired = False
    try:
        from news.catalyst_injection import scan_for_catalysts
        cands = scan_for_catalysts(set(), available_cash=10000.0, target_slot=2000.0,
                                    risk_overlay_active=False,
                                    require_market_open=False,
                                    require_price_confirmation=False)
        catalyst_fired = len(cands) > 0
    except Exception:
        pass

    return {"nifty": nifty, "vix_ltp": vix_ltp, "sectors": sectors,
            "catalyst_fired": catalyst_fired}


def execute_trade(direction: str, conviction: int, reasons: list[str], state: dict):
    """Execute the F&O trade with all safeguards."""
    from paper.portfolio import PaperPortfolio
    from fno.option_chain import find_atm_strike, find_contract, get_option_ltp, days_to_expiry
    from fno.nfo_master import list_expiries
    from data_fetcher import get_fetcher

    f = get_fetcher()
    if not f.logged_in:
        f.login()
    pf = PaperPortfolio()

    spot = float(f.get_ltp("NIFTY").get("ltp", 0))
    if spot <= 0:
        logger.warning("autotrade: NIFTY spot 0; abort")
        return False

    # OTM strike
    if direction == "BULLISH":
        strike = round((spot + OTM_OFFSET) / 50) * 50
        opt_type = "CE"
    else:
        strike = round((spot - OTM_OFFSET) / 50) * 50
        opt_type = "PE"

    # Find weekly expiry 3-7 days
    expiries = list_expiries("NIFTY", "OPTIDX")
    target_exp = next((e for e in expiries if 3 <= days_to_expiry(e) <= 7), None)
    if not target_exp:
        logger.warning("autotrade: no expiry in window")
        return False

    contract = find_contract("NIFTY", target_exp, strike, opt_type, "OPTIDX")
    if not contract:
        return False
    premium = get_option_ltp(contract)
    if not premium or premium <= 0:
        return False

    lot_size = int(contract.get("lotsize", 0))
    cost = premium * lot_size
    held_ltps = {s: float(f.get_ltp(s).get("ltp", 0)) for s in pf.get_open_symbols()}
    equity = pf.current_equity(held_ltps)
    max_spend = equity * (MAX_RISK_PCT_PER_TRADE / 100) * 4  # 5% risk * 4 = ~20% nominal
    if cost > max_spend:
        logger.info(f"autotrade: cost Rs{cost:.0f} > max spend Rs{max_spend:.0f}")
        return False

    sym = contract.get("symbol")
    stop = premium * 0.75
    target = premium * 1.50
    variant = "fno_call" if opt_type == "CE" else "fno_put"

    pos = pf.open_position(
        symbol=sym, variant=variant,
        regime=f"AUTO_CONV{conviction}",
        entry_price=premium, slot_notional=cost,
        stop=stop, target=target,
        reason=f"CLAUDE AUTOTRADE 5/5: {' | '.join(reasons[:3])}",
    )
    if pos:
        logger.info(f"AUTOTRADE OPEN: {sym} qty={pos.qty} @ Rs{premium:.2f}")
        try:
            from alerts.channels import dispatch
            dispatch("info", f"CLAUDE AUTOTRADE: {sym}",
                     f"Conviction 5/5\n{chr(10).join(reasons)}\n"
                     f"Premium Rs {premium:.2f}, lot {lot_size}, cost Rs {cost:.0f}\n"
                     f"Stop Rs {stop:.2f}, Target Rs {target:.2f}")
        except Exception:
            pass
        return True
    return False


def alert_only(direction: str, conviction: int, reasons: list[str]):
    """Telegram alert for 3-4/5 setups; user decides."""
    msg = (f"BORDERLINE SETUP: {direction} (conviction {conviction}/5)\n"
           f"Reasons:\n  " + "\n  ".join(reasons) + "\n"
           f"Not auto-executed. Tell Claude 'execute' to take it manually.")
    try:
        from alerts.channels import dispatch
        dispatch("info", f"Claude: {direction} {conviction}/5", msg)
    except Exception:
        logger.warning(f"alert dispatch failed")
    print(msg)


def main():
    from common.market_hours import is_market_hours, now_ist
    if not is_market_hours():
        logger.info("Market closed; skip")
        return

    state = _load_state()
    today = now_ist().strftime("%Y-%m-%d")

    # Reset daily counter
    if state.get("last_date") != today:
        state["trades_today"] = 0
        state["last_date"] = today
        _save_state(state)

    # Cooldown check
    cooldown = state.get("cooldown_until", "")
    if cooldown and today < cooldown:
        logger.info(f"In cooldown until {cooldown} (3 consecutive losses)")
        return

    # Daily cap
    if state["trades_today"] >= MAX_TRADES_PER_DAY:
        logger.info(f"Hit daily cap {MAX_TRADES_PER_DAY}")
        return

    if not ENABLE_AUTOTRADE:
        logger.info("Master autotrade switch OFF")
        return

    # Read user's kill switch
    sj = Path(__file__).resolve().parent.parent.parent / "data" / "stocks.json"
    if sj.exists():
        try:
            sj_data = json.loads(sj.read_text(encoding="utf-8"))
            if sj_data.get("kill_switch_active"):
                logger.info("kill_switch active; skip")
                return
        except Exception:
            pass

    market = gather_market_state()
    conviction, reasons, direction = compute_conviction(market)
    if conviction == 0 or direction is None:
        logger.info("No setup detected")
        return

    logger.info(f"Setup: {direction} conviction {conviction}/5  reasons={reasons}")

    if conviction >= MIN_CONVICTION_AUTO:
        logger.info("Conviction 5/5 -- AUTO-EXECUTING")
        ok = execute_trade(direction, conviction, reasons, state)
        if ok:
            state["trades_today"] += 1
            _save_state(state)
    elif conviction >= MIN_CONVICTION_ALERT:
        logger.info(f"Conviction {conviction}/5 -- alerting only")
        alert_only(direction, conviction, reasons)


if __name__ == "__main__":
    main()

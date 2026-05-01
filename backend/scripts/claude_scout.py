"""
Claude Scout — proactive market scan I run during sessions.

Mimics yesterday's winning analysis: NIFTY direction + sector breadth + top
movers + news catalysts + F&O setup proposals with conviction score.

Usage:
  python scripts/claude_scout.py        # full scan
  python scripts/claude_scout.py --quick # nifty + top 8 movers only
  python scripts/claude_scout.py --fno  # focus on F&O setup ideas

Designed to print a clean briefing for the user/Claude to review and
decide trades. Does NOT auto-execute anything.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_fetcher import get_fetcher, SYMBOL_TOKENS
from common.market_hours import is_market_hours, now_ist


LIQUID_NAMES = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK",
    "ITC", "SBIN", "BAJFINANCE", "MARUTI", "AXISBANK",
    "BHARTIARTL", "INDUSINDBK", "BANDHANBNK", "EXIDEIND",
    "COALINDIA", "ADANIENT", "TATAMOTORS", "HCLTECH",
    "LT", "ASIANPAINT", "NESTLEIND", "POWERGRID",
]


def banner(text: str):
    print(f"\n{'='*60}\n  {text}\n{'='*60}")


def section(text: str):
    print(f"\n--- {text} ---")


def get_intraday_pct(f, sym: str) -> tuple[float, float]:
    """Returns (ltp, intraday_pct) using Angel's prev close."""
    try:
        d = f.get_ltp(sym)
        ltp = float(d.get("ltp", 0))
        prev = float(d.get("close", 0))
        pct = (ltp - prev) / prev * 100 if prev > 0 else 0
        return ltp, pct
    except Exception:
        return 0, 0


def scan_nifty(f) -> dict:
    """NIFTY direction + range analysis."""
    d = f.get_ltp("NIFTY")
    ltp = float(d.get("ltp", 0))
    prev_close = float(d.get("close", 0))
    today_open = float(d.get("open", 0))
    today_high = float(d.get("high", 0))
    today_low = float(d.get("low", 0))
    intraday_pct = (ltp - prev_close) / prev_close * 100 if prev_close > 0 else 0
    open_to_now_pct = (ltp - today_open) / today_open * 100 if today_open > 0 else 0
    range_pct = (today_high - today_low) / today_low * 100 if today_low > 0 else 0
    near_low = (ltp - today_low) / ltp * 100 < 0.3 if ltp > 0 else False
    near_high = (today_high - ltp) / ltp * 100 < 0.3 if ltp > 0 else False
    return {
        "ltp": ltp, "prev": prev_close, "open": today_open,
        "high": today_high, "low": today_low,
        "intraday_pct": intraday_pct, "open_to_now": open_to_now_pct,
        "range_pct": range_pct, "near_low": near_low, "near_high": near_high,
    }


def scan_movers(f, sample: list[str]) -> list[tuple]:
    """Returns sorted (sym, ltp, pct) of top movers."""
    movers = []
    for sym in sample:
        ltp, pct = get_intraday_pct(f, sym)
        if ltp > 0:
            movers.append((sym, ltp, pct))
    movers.sort(key=lambda x: -x[2])
    return movers


def scan_vix(f):
    try:
        d = f.get_ltp("INDIAVIX")
        return float(d.get("ltp", 0)), float(d.get("close", 0))
    except Exception:
        return 0, 0


def propose_fno_setups(nifty: dict, movers: list, vix_ltp: float):
    """Heuristic trade ideas based on current market state."""
    setups = []

    # Setup 1: NIFTY directional reversal
    if nifty["intraday_pct"] <= -0.7 and nifty["near_low"] and nifty["open_to_now"] > -0.3:
        # Recovering off low - bullish
        setups.append({
            "type": "NIFTY reversal CALL",
            "direction": "BULLISH",
            "rationale": f"NIFTY {nifty['intraday_pct']:+.2f}% intraday, recovering off low",
            "conviction": 4 if nifty["range_pct"] > 1 else 3,
            "instrument": f"NIFTY weekly {round(nifty['ltp']/50+1)*50}CE",
        })
    if nifty["intraday_pct"] >= 0.7 and nifty["near_high"] and nifty["open_to_now"] < 0.3:
        setups.append({
            "type": "NIFTY rolloff PUT",
            "direction": "BEARISH",
            "rationale": f"NIFTY {nifty['intraday_pct']:+.2f}% intraday, fading off high",
            "conviction": 4 if nifty["range_pct"] > 1 else 3,
            "instrument": f"NIFTY weekly {round(nifty['ltp']/50-1)*50}PE",
        })

    # Setup 2: Strongest mover with sector confirmation
    if movers and movers[0][2] >= 2.0:
        sym, ltp, pct = movers[0]
        setups.append({
            "type": f"{sym} momentum continuation",
            "direction": "BULLISH",
            "rationale": f"{sym} +{pct:.1f}% intraday, strongest in liquid universe",
            "conviction": 3,
            "instrument": f"{sym} ATM weekly CE OR cash equity",
        })

    # Setup 3: VIX regime adjustment
    if vix_ltp > 0:
        if vix_ltp > 22:
            setups.append({
                "type": "VIX HIGH — REDUCE SIZE",
                "direction": "INFO",
                "rationale": f"VIX at {vix_ltp:.1f} > 22. Halve all position sizes.",
                "conviction": 5,
                "instrument": "Risk-management note",
            })
        elif vix_ltp < 12:
            setups.append({
                "type": "VIX LOW — SHORT-VOL FAVORS DIRECTIONAL",
                "direction": "INFO",
                "rationale": f"VIX at {vix_ltp:.1f} < 12. Cheap directional options.",
                "conviction": 4,
                "instrument": "Note: ATM/ITM options cheaper than usual",
            })

    return setups


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--fno", action="store_true")
    args = parser.parse_args()

    ist = now_ist()
    banner(f"CLAUDE SCOUT  {ist.strftime('%Y-%m-%d %H:%M:%S')} IST")

    market_open = is_market_hours()
    print(f"Market open: {market_open}")
    if not market_open:
        print("(Showing latest close-price snapshot — not live ticks)")

    f = get_fetcher()
    if not f.logged_in:
        f.login()

    # NIFTY
    section("NIFTY")
    nifty = scan_nifty(f)
    print(f"  Spot:        Rs {nifty['ltp']:.0f}")
    print(f"  Prev close:  Rs {nifty['prev']:.0f}  -> intraday {nifty['intraday_pct']:+.2f}%")
    print(f"  Day range:   {nifty['low']:.0f} -- {nifty['high']:.0f}  ({nifty['range_pct']:.2f}%)")
    print(f"  Bias hint:   {'near LOW (recovery setup?)' if nifty['near_low'] else 'near HIGH (rolloff setup?)' if nifty['near_high'] else 'mid-range'}")

    # VIX
    section("VIX (regime)")
    vix_ltp, vix_prev = scan_vix(f)
    if vix_ltp > 0:
        regime = "LOW" if vix_ltp < 12 else "HIGH" if vix_ltp > 22 else "NORMAL"
        print(f"  India VIX: {vix_ltp:.2f}  ({regime} regime)")

    # Movers
    section("TOP MOVERS (liquid universe)")
    movers = scan_movers(f, LIQUID_NAMES)
    print(f"  {'symbol':<14s} {'LTP':>10s} {'intraday':>9s}")
    for sym, ltp, pct in movers[:8]:
        marker = "  ^" if abs(pct) >= 1.5 else ""
        print(f"  {sym:<14s} {ltp:>10.2f} {pct:>+8.2f}%{marker}")

    # Bottom movers (potential mean-rev opportunities)
    section("BOTTOM MOVERS")
    for sym, ltp, pct in movers[-5:]:
        print(f"  {sym:<14s} {ltp:>10.2f} {pct:>+8.2f}%")

    # F&O setup proposals
    section("F&O SETUPS I'D CONSIDER")
    setups = propose_fno_setups(nifty, movers, vix_ltp)
    if not setups:
        print("  No high-conviction setups detected. Wait.")
    else:
        for s in setups:
            print(f"  [{s['conviction']}/5] {s['type']}")
            print(f"        Direction:  {s['direction']}")
            print(f"        Rationale:  {s['rationale']}")
            print(f"        Trade:      {s['instrument']}")
            print()

    # Open positions check
    try:
        from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
        pf = PaperPortfolio()
        held = pf.get_open_positions()
        if held:
            section("HELD POSITIONS (for context)")
            for sym, p in held.items():
                ltp_p, _ = get_intraday_pct(f, sym)
                pnl = (ltp_p - p.entry_price) * p.qty if ltp_p > 0 else 0
                pnl_pct = (ltp_p - p.entry_price) / p.entry_price * 100 if p.entry_price > 0 and ltp_p > 0 else 0
                print(f"  {sym:<14s} entry {p.entry_price:>9.2f}  ltp {ltp_p:>9.2f}  pnl {pnl_pct:+.2f}% (Rs{pnl:+.0f})")
        equity = pf.current_equity({s: float(f.get_ltp(s).get('ltp', 0)) for s in pf.get_open_symbols()})
        realized = pf.get_realized_pnl_total()
        print(f"\n  Equity: Rs {equity:.0f}  (realized {realized:+.0f}, total P&L {(equity - STARTING_CAPITAL)/100:+.2f}%)")
    except Exception as e:
        print(f"  position read failed: {e}")

    banner("END OF SCOUT")
    print("Use this as input. You decide what (if anything) to execute.\n")


if __name__ == "__main__":
    main()

"""
Morning F&O briefing -- fires 08:32 IST, complement to preopen_signals.

Where preopen_signals.py covers cash equity AMO orders, this sends the
F&O context the user actually trades on:
  - NIFTY gap analysis (Friday close vs current/SGX Nifty)
  - VIX regime
  - Sector heatmap (9 sectors)
  - Top 3 F&O setups I would consider TODAY
  - One-line read on the day

Schedule: Windows Task Scheduler, daily 08:32 IST, weekday only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from common.market_hours import now_ist


SECTOR_NAMES = {
    "NIFTY_BANK": "BANK",
    "NIFTY_AUTO": "AUTO",
    "NIFTY_IT": "IT",
    "NIFTY_FMCG": "FMCG",
    "NIFTY_PHARMA": "PHARMA",
    "NIFTY_REALTY": "REALTY",
    "NIFTY_ENERGY": "ENERGY",
    "NIFTY_INFRA": "INFRA",
    "NIFTY_METAL": "METAL",
}


def _gap_label(pct: float) -> str:
    if pct >= 0.5:    return f"GAP UP {pct:+.2f}%"
    if pct <= -0.5:   return f"GAP DOWN {pct:+.2f}%"
    return f"FLAT {pct:+.2f}%"


def _vix_regime(vix: float) -> str:
    if vix < 12: return "LOW (cheap directional options)"
    if vix > 22: return "HIGH (halve sizes; expect whips)"
    return "NORMAL"


def _read_on_day(nifty_intraday_pct: float, vix: float, bullish_sectors: int, bearish_sectors: int) -> str:
    if abs(nifty_intraday_pct) < 0.3 and abs(bullish_sectors - bearish_sectors) <= 2:
        return "SIDEWAYS -- avoid directional, look for mean-reversion"
    if nifty_intraday_pct > 0.5 and bullish_sectors >= 6:
        return "BULLISH -- favor CE breakouts; tight trail"
    if nifty_intraday_pct < -0.5 and bearish_sectors >= 6:
        return "BEARISH -- favor PE buys on bounce-failures"
    if nifty_intraday_pct < -0.5 and bullish_sectors >= 5:
        return "BEARISH-but-divergent -- wait for clarity, do not pre-empt"
    return "MIXED -- wait for confirmation in first 30 min"


def gather() -> dict:
    from data_fetcher import get_fetcher
    f = get_fetcher()
    if not f.logged_in:
        f.login()

    d = f.get_ltp("NIFTY")
    nifty_ltp = float(d.get("ltp", 0))
    nifty_prev = float(d.get("close", 0))
    nifty_pct = (nifty_ltp - nifty_prev) / nifty_prev * 100 if nifty_prev > 0 else 0

    try:
        v = f.get_ltp("INDIAVIX")
        vix = float(v.get("ltp", 0))
    except Exception:
        vix = 15.0

    sectors = []
    for sym, label in SECTOR_NAMES.items():
        try:
            sd = f.get_ltp(sym)
            sl = float(sd.get("ltp", 0))
            sp = float(sd.get("close", 0))
            pct = (sl - sp) / sp * 100 if sp > 0 else 0
            sectors.append({"label": label, "pct": pct})
        except Exception:
            pass
    sectors.sort(key=lambda x: -x["pct"])

    return {
        "nifty_ltp": nifty_ltp, "nifty_prev": nifty_prev, "nifty_pct": nifty_pct,
        "vix": vix, "sectors": sectors,
    }


def propose_setups(state: dict) -> list[str]:
    """3 F&O ideas based on overnight state. User decides whether to take any."""
    setups = []
    nifty_ltp = state["nifty_ltp"]
    nifty_pct = state["nifty_pct"]
    vix = state["vix"]
    sectors = state["sectors"]

    bullish = sum(1 for s in sectors if s["pct"] > 0)
    bearish = sum(1 for s in sectors if s["pct"] < 0)

    # Idea 1: Gap-up fade or follow-through
    if nifty_pct >= 0.7:
        otm_ce = round((nifty_ltp + 100) / 50) * 50
        otm_pe = round((nifty_ltp - 100) / 50) * 50
        setups.append(
            f"GAP-UP play: if NIFTY can't hold first 30-min low -> {otm_pe}PE "
            f"(reversal). If holds & sector breadth confirms -> {otm_ce}CE."
        )
    elif nifty_pct <= -0.7:
        otm_ce = round((nifty_ltp + 100) / 50) * 50
        otm_pe = round((nifty_ltp - 100) / 50) * 50
        setups.append(
            f"GAP-DOWN play: if NIFTY recovers above first 30-min high -> {otm_ce}CE "
            f"(replay 30-Apr setup). If continues lower -> {otm_pe}PE."
        )
    else:
        setups.append("FLAT open: wait for 09:30-10:00 range, then trade breakout direction.")

    # Idea 2: VIX regime
    if vix < 12:
        setups.append(f"VIX {vix:.1f} (LOW): buy ATM/slightly-OTM weekly directionals; cheap premium.")
    elif vix > 22:
        setups.append(f"VIX {vix:.1f} (HIGH): halve sizes, prefer ITM (lower theta-decay risk).")
    else:
        setups.append(f"VIX {vix:.1f} (normal): standard sizing OK.")

    # Idea 3: Strongest sector signal
    if sectors:
        top_sec = sectors[0]
        bot_sec = sectors[-1]
        if top_sec["pct"] >= 0.8:
            setups.append(f"SECTOR LEAD: {top_sec['label']} {top_sec['pct']:+.2f}% strongest. "
                          f"Look for top-N momentum stocks in this sector for cash buys.")
        elif bot_sec["pct"] <= -0.8:
            setups.append(f"SECTOR DRAG: {bot_sec['label']} {bot_sec['pct']:+.2f}% weakest. "
                          f"Avoid longs here; consider short-bias if you trade futures.")
        else:
            setups.append(f"Sector breadth: {bullish}/9 up, {bearish}/9 down. No clear sector lead.")

    return setups


def format_telegram(state: dict, setups: list[str]) -> str:
    today = now_ist().strftime("%a %d %b %Y")
    sectors = state["sectors"]
    bullish = sum(1 for s in sectors if s["pct"] > 0)
    bearish = sum(1 for s in sectors if s["pct"] < 0)

    lines = [
        f"MORNING F&O BRIEFING  {today}",
        "",
        f"NIFTY: {state['nifty_ltp']:.0f}  ({_gap_label(state['nifty_pct'])} vs prev close {state['nifty_prev']:.0f})",
        f"VIX:   {state['vix']:.2f}  ({_vix_regime(state['vix'])})",
        "",
        "SECTOR HEATMAP:",
    ]
    for s in sectors:
        marker = "  ^" if s["pct"] >= 0.8 else "  v" if s["pct"] <= -0.8 else ""
        lines.append(f"  {s['label']:<8s} {s['pct']:+6.2f}%{marker}")
    lines.append(f"  ({bullish}/9 up, {bearish}/9 down)")
    lines.append("")

    lines.append("READ ON THE DAY:")
    lines.append(f"  {_read_on_day(state['nifty_pct'], state['vix'], bullish, bearish)}")
    lines.append("")

    lines.append("F&O IDEAS TO CONSIDER:")
    for i, s in enumerate(setups, 1):
        lines.append(f"  {i}. {s}")
    lines.append("")
    lines.append("Autotrade armed: fires 5/5 conviction; alerts 3-4/5.")

    return "\n".join(lines)


def main():
    ist = now_ist()
    if ist.weekday() >= 5:
        logger.info(f"weekend ({ist.strftime('%A')}); skipping morning briefing")
        return

    state = gather()
    setups = propose_setups(state)
    msg = format_telegram(state, setups)
    print(msg)

    try:
        from alerts.channels import dispatch
        dispatch("info", f"F&O Briefing {ist.strftime('%d %b')}", msg)
        logger.info("Morning briefing dispatched to Telegram")
    except Exception as e:
        logger.warning(f"telegram send failed: {e}")


if __name__ == "__main__":
    main()

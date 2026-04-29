"""
Pre-open signal generator. Runs at 08:30 IST (after Postclose has refreshed
yesterday's bars + picks JSON, before NSE pre-open auction at 09:00).

Pulls from THREE sources, ranks, and sends a single Telegram message with
ranked AMO order suggestions:

  1. Daily-bar momentum picker (top 5 picks_extended)
  2. Catalyst names (12h news flow, loosened thresholds: 3+ articles, 2+ sources)
  3. F&O OI buildup (placeholder for Phase 2 — option chain available)

User then places AMO/limit orders manually pre-09:15 IST. The system tracks
fills via the existing pending_open mechanic.

Schedule: Windows Task Scheduler, daily 08:30 IST, weekday only.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from common.market_hours import now_ist


STOCKS_JSON = Path(__file__).resolve().parent.parent.parent / "data" / "stocks.json"
SIGNALS_OUT = Path(__file__).resolve().parent.parent.parent / "data" / "preopen_signals.json"


def _load_picker_picks() -> list[dict]:
    """Top 5 from yesterday's picks_extended."""
    if not STOCKS_JSON.exists():
        return []
    try:
        d = json.loads(STOCKS_JSON.read_text(encoding="utf-8"))
        ext = d.get("picks_extended") or []
        # picks_extended has {rank, symbol, score}; enrich with cmp/target/stop_loss from picks if present
        picks = {p["symbol"]: p for p in (d.get("picks") or [])}
        out = []
        for e in ext[:5]:
            sym = e["symbol"]
            base = picks.get(sym, {})
            out.append({
                "symbol": sym,
                "rank": e.get("rank"),
                "score": e.get("score"),
                "cmp": base.get("cmp"),
                "target": base.get("target"),
                "stop_loss": base.get("stop_loss"),
                "atr": base.get("atr"),
                "source": "momentum_picker",
            })
        return out
    except Exception as e:
        logger.warning(f"picker load failed: {e}")
        return []


def _scan_catalysts() -> list[dict]:
    """Catalyst-driven names from overnight news flow. Uses loosened P9 thresholds
    (3+ articles, 2+ sources, 12h lookback)."""
    try:
        from news.catalyst_injection import scan_for_catalysts
        # Pre-market call: pretend we have full cash and standard slot to get the list
        cands = scan_for_catalysts(
            held_symbols=set(), available_cash=10_000.0, target_slot=2_000.0,
            risk_overlay_active=False,
            require_market_open=False, require_price_confirmation=False,
        )
        out = []
        for c in cands[:5]:
            out.append({
                "symbol": c.symbol,
                "matched_articles": c.matched_articles,
                "distinct_sources": c.distinct_sources,
                "catalyst_kind": c.catalyst_kind,
                "sample_titles": c.sample_titles,
                "score": c.score,
                "source": "news_catalyst",
            })
        return out
    except Exception as e:
        logger.warning(f"catalyst scan failed: {e}")
        return []


def _detect_overnight_gappers() -> list[dict]:
    """Detect symbols that have moved overnight (yesterday's close vs latest LTP).
    Pre-open quotes available 09:00-09:15 IST; this script runs at 08:30 so it
    uses yesterday's last close as proxy + any pre-market index drift signal.
    More sophisticated logic (actual pre-open auction price) belongs in a 09:08
    second-pass scan."""
    # Stub for now — meaningful only after 09:08 when pre-open auction prices appear
    return []


def _format_telegram(signals: list[dict]) -> str:
    """Compose a clean ranked message for Telegram."""
    today = now_ist().strftime("%a %d %b %Y")
    if not signals:
        return f"PRE-OPEN SIGNALS {today}\nNo high-conviction signals tonight."
    lines = [f"PRE-OPEN SIGNALS {today}", ""]
    for i, s in enumerate(signals, 1):
        sym = s["symbol"]
        src = s.get("source", "")
        if src == "momentum_picker":
            cmp_p = s.get("cmp") or 0
            stop = s.get("stop_loss") or 0
            target = s.get("target") or 0
            lines.append(f"{i}. {sym}  [momentum]")
            if cmp_p:
                lines.append(f"   Entry near Rs {cmp_p:.2f}")
            if stop:
                lines.append(f"   Stop  Rs {stop:.2f}  ({((stop - cmp_p) / cmp_p * 100):+.1f}%)" if cmp_p else f"   Stop Rs {stop:.2f}")
            if target:
                lines.append(f"   Target Rs {target:.2f}  ({((target - cmp_p) / cmp_p * 100):+.1f}%)" if cmp_p else f"   Target Rs {target:.2f}")
            lines.append(f"   Rank #{s.get('rank')}, momentum score {s.get('score')}")
        elif src == "news_catalyst":
            lines.append(f"{i}. {sym}  [CATALYST: {s.get('catalyst_kind', '')}]")
            lines.append(f"   {s.get('matched_articles')} articles / {s.get('distinct_sources')} sources")
            for t in (s.get("sample_titles") or [])[:1]:
                lines.append(f"   '{t[:80]}'")
        else:
            lines.append(f"{i}. {sym}  [{src}]")
        lines.append("")
    lines.append("Place AMO orders 09:00-09:15 IST. System will track fills.")
    return "\n".join(lines)


def main():
    ist = now_ist()
    if ist.weekday() >= 5:
        logger.info(f"weekend ({ist.strftime('%A')}); skipping pre-open scan")
        return

    logger.info("Pre-open signal scan starting")

    picker_signals = _load_picker_picks()
    catalyst_signals = _scan_catalysts()
    gapper_signals = _detect_overnight_gappers()

    # Merge & dedupe by symbol (catalyst > momentum priority)
    seen = set()
    merged: list[dict] = []
    for s in catalyst_signals + picker_signals + gapper_signals:
        sym = s.get("symbol")
        if not sym or sym in seen:
            continue
        seen.add(sym)
        merged.append(s)

    # Persist
    SIGNALS_OUT.parent.mkdir(parents=True, exist_ok=True)
    SIGNALS_OUT.write_text(json.dumps({
        "generated_at": datetime.now().isoformat(),
        "signals": merged,
    }, indent=2, default=str), encoding="utf-8")
    logger.info(f"Wrote {len(merged)} signals to {SIGNALS_OUT.name}")

    # Send to Telegram
    msg = _format_telegram(merged)
    print(msg)
    try:
        from alerts.channels import dispatch
        dispatch("info", f"PRE-OPEN signals {ist.strftime('%d %b')}", msg)
        logger.info("Pre-open signals dispatched to Telegram")
    except Exception as e:
        logger.warning(f"telegram send failed: {e}")


if __name__ == "__main__":
    main()

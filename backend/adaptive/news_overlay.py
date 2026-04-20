"""
News-informed re-scoring overlay (Phase 1: SHADOW MODE by default).

Uses Loughran-McDonald finance sentiment + half-life decay.

SHADOW vs LIVE:
  - Shadow (default): logs what WOULD have been adjusted but does NOT change
    the picks list. After 30+ days of shadow data, evaluate contribution.
  - Live: applies adjustments to scores + ranks.

Toggle via config.py NEWS_OVERLAY_MODE = "shadow" | "live".

Uses calibrated percentile thresholds (not hand-picked numbers). Thresholds
are loaded from data/news_thresholds.json if present; else conservative defaults.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from news.feed import NewsSnapshot

try:
    from adaptive.variants import Pick
except Exception:
    Pick = None  # type: ignore


THRESHOLDS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "news_thresholds.json"
SHADOW_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "news_shadow_log.json"

DEFAULT_THRESHOLDS = {
    # Decay-weighted LM sentiment thresholds
    "blacklist_sentiment_24h": -3.0,     # sum of decayed LM net hits over 24h
    "strong_positive_24h":      3.0,
    "mild_positive_24h":        1.5,
    "high_noise_c24":          10,        # article count in 24h that triggers noise penalty
    # Adjustments (fraction of score)
    "strong_boost":             0.07,
    "mild_boost":               0.03,
    "earnings_penalty":        -0.04,
    "noise_penalty":           -0.02,
    "macro_boost":              0.05,
    "macro_penalty":           -0.05,
}


def _load_thresholds() -> dict:
    if THRESHOLDS_PATH.exists():
        try:
            data = json.loads(THRESHOLDS_PATH.read_text(encoding="utf-8"))
            return {**DEFAULT_THRESHOLDS, **data}
        except Exception:
            pass
    return DEFAULT_THRESHOLDS


def _get_mode() -> str:
    """Returns 'shadow' (default) or 'live'."""
    try:
        from config import NEWS_OVERLAY_MODE
        return (NEWS_OVERLAY_MODE or "shadow").lower()
    except Exception:
        return "shadow"


def _append_shadow_log(entry: dict) -> None:
    """Persist every shadow decision for later evaluation of overlay impact."""
    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    arr = []
    if SHADOW_LOG_PATH.exists():
        try:
            arr = json.loads(SHADOW_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    arr.append(entry)
    arr = arr[-2000:]   # keep last 2000
    SHADOW_LOG_PATH.write_text(json.dumps(arr, indent=2), encoding="utf-8")


# Sector tags for macro tilts (rough; expand as needed)
ENERGY_IMPORTERS = {"BPCL", "HPCL", "IOC", "GAIL"}  # hurt by high crude
ENERGY_UPSTREAM  = {"ONGC", "OIL", "RELIANCE"}      # helped by high crude
IT_EXPORTERS     = {"TCS", "INFY", "WIPRO", "HCLTECH", "TECHM", "LTIM", "MPHASIS"}  # weak rupee helps
BANKS            = {"HDFCBANK", "ICICIBANK", "AXISBANK", "KOTAKBANK", "SBIN", "INDUSINDBK",
                    "BANKBARODA", "PNB", "CANBK", "IDFCFIRSTB", "FEDERALBNK", "YESBANK"}


def _earnings_flag(symbol: str, earnings_titles: list[str]) -> bool:
    """True if the symbol appears in an earnings-titled article (pre-event risk)."""
    sym_lower = symbol.lower()
    for t in earnings_titles:
        if sym_lower in t.lower():
            return True
    return False


def _macro_tilt_for_symbol(symbol: str, macro: dict) -> float:
    """
    Additive score tilt (in the same units as the variant's score).
    Positive = boost, negative = penalty.
    """
    counts = macro.get("counts_7d", {})
    sent   = macro.get("sentiment_7d", {})

    tilt = 0.0

    # Crude trending up + positive macro sentiment → penalize importers, boost upstream
    crude_pressure = counts.get("crude", 0) >= 10 and sent.get("crude", 0) > 5
    if crude_pressure:
        if symbol in ENERGY_IMPORTERS:
            tilt -= 0.05
        elif symbol in ENERGY_UPSTREAM:
            tilt += 0.05

    # Rupee weakness commentary → IT exporters benefit
    rupee_weak = counts.get("rupee", 0) >= 3 and sent.get("rupee", 0) < -2
    if rupee_weak and symbol in IT_EXPORTERS:
        tilt += 0.03

    # RBI policy coverage with negative sentiment → banks uncertainty
    rbi_negative = counts.get("rbi", 0) >= 3 and sent.get("rbi", 0) < -2
    if rbi_negative and symbol in BANKS:
        tilt -= 0.03

    return tilt


def apply_news_overlay(picks: list, news_snap) -> tuple[list, list[dict]]:
    """
    Returns (adjusted_picks, decisions_log).
    In SHADOW mode (default), decisions_log captures what WOULD have been
    changed but picks are returned UNMODIFIED. In LIVE mode, picks are adjusted.
    """
    if not picks or news_snap is None or news_snap.status == "unavailable":
        return picks, []

    mode = _get_mode()
    thresholds = _load_thresholds()
    mentions = news_snap.symbol_mentions or {}
    earnings = news_snap.earnings_titles or []
    macro    = news_snap.macro or {}
    timestamp = datetime.now().isoformat()

    out_picks = []
    log = []

    for p in picks:
        m = mentions.get(p.symbol, {})
        sent_24h = float(m.get("sentiment_24h", 0.0))
        c24 = int(m.get("c24", 0))
        c7d = int(m.get("c7d", 0))

        reasons = []
        adjustment = 0.0
        action = "HOLD"

        # 1. Blacklist: decay-weighted LM sentiment strongly negative with volume
        if sent_24h <= thresholds["blacklist_sentiment_24h"] and c24 >= 2:
            action = "BLACKLIST"
            entry = {
                "symbol": p.symbol, "mode": mode, "action": action,
                "original_score": round(float(p.score), 4),
                "new_score": None, "adjustment_pct": None,
                "sentiment_24h": sent_24h, "c24": c24,
                "reason": f"LM sentiment {sent_24h:+.1f} across {c24} articles (decay-weighted)",
                "timestamp": timestamp,
            }
            log.append(entry)
            _append_shadow_log(entry)
            if mode == "live":
                continue
            else:
                out_picks.append(p)  # shadow: keep the pick
                continue

        # 2. Positive news boost (LM-based)
        if sent_24h >= thresholds["strong_positive_24h"] and c24 >= 2:
            adjustment += thresholds["strong_boost"]
            reasons.append(f"strong LM sentiment {sent_24h:+.1f}, {c24} articles")
        elif sent_24h >= thresholds["mild_positive_24h"] and c24 >= 1:
            adjustment += thresholds["mild_boost"]
            reasons.append(f"mild positive LM sentiment {sent_24h:+.1f}")

        # 3. Earnings pre-event penalty
        if _earnings_flag(p.symbol, earnings):
            adjustment += thresholds["earnings_penalty"]
            reasons.append("pre-earnings vol risk")

        # 4. High news noise
        if c24 >= thresholds["high_noise_c24"]:
            adjustment += thresholds["noise_penalty"]
            reasons.append(f"news noise ({c24} articles 24h)")

        # 5. Macro sector tilts
        macro_tilt = _macro_tilt_for_symbol(p.symbol, macro)
        if macro_tilt:
            adjustment += macro_tilt
            reasons.append("macro tilt")

        if abs(adjustment) < 0.001:
            out_picks.append(p)
            continue

        new_score = float(p.score) * (1 + adjustment)
        new_pick = replace(p, score=new_score) if (mode == "live" and hasattr(p, "__dataclass_fields__")) else p

        entry = {
            "symbol": p.symbol, "mode": mode, "action": "ADJUST",
            "original_score": round(float(p.score), 4),
            "new_score": round(new_score, 4),
            "adjustment_pct": round(adjustment * 100, 2),
            "sentiment_24h": sent_24h, "c24": c24,
            "reason": "; ".join(reasons) if reasons else "macro only",
            "timestamp": timestamp,
        }
        log.append(entry)
        _append_shadow_log(entry)
        out_picks.append(new_pick)

    # Re-rank only in live mode
    if mode == "live":
        out_picks.sort(key=lambda p: p.score, reverse=True)
        final = []
        for i, p in enumerate(out_picks, 1):
            final.append(replace(p, rank=i) if hasattr(p, "__dataclass_fields__") else p)
        return final, log

    # Shadow: return original picks unchanged
    return picks, log

"""
News-driven catalyst injection — finds non-held universe symbols with strong
news catalysts (M&A, earnings, USFDA approval, contract win) and opens
smaller-sized positions during market hours.

This is what would have caught the SUNPHARMA / Organon move on 2026-04-27.

Trigger conditions (ALL must hit, conservative on purpose to avoid noise):
  - 5+ articles in last 6h mentioning the symbol via entity-precise match
  - 3+ distinct sources
  - At least one CATALYST keyword in title or excerpt
  - Symbol IS in our trading universe (SYMBOL_TOKENS)
  - Symbol is NOT currently held (else trailing stop / DD overlay handles it)
  - Risk overlay healthy (no DD halt, no tail halt)
  - Within market hours, not in last 30 min

Sizing:
  - 50% of normal slot size (asymmetric risk on news catalyst)
  - Tighter stop: 1.5x ATR vs base picker's 2x

Auto-cleanup:
  - Mark catalyst positions with reason="catalyst_open"
  - Separate exit logic later: if no follow-through over 2 days, exit at LTP
    (handled by next-day scoring drop from picks)

Returns list of CatalystDecision; caller (mark_to_market) executes them.
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger

from news.feed import _fetch_recent_articles, _matches_symbol, _article_orgs
from news.symbols import names_for, SYMBOL_TO_NAMES
from data_fetcher import SYMBOL_TOKENS
from common.market_hours import is_market_hours, now_ist


# ---------- Config ----------------------------------------------------------
# P9 (2026-04-29): loosened thresholds. Today missed BANDHANBNK +13% intraday
# and the 04-27 SUNPHARMA/Organon move because both failed the 5-article gate.
# Counter-balance: added intraday-price-confirmation gate so we don't fire on
# rumor noise -- only opens catalyst position if the symbol is ALREADY moving
# (today_total_pct >= MIN_INTRADAY_MOVE_PCT). This filters most false positives
# while still catching the real catalysts the 5/3/6 thresholds were missing.

MIN_ARTICLES = 3
MIN_DISTINCT_SOURCES = 2
LOOKBACK_HOURS = 12
MIN_INTRADAY_MOVE_PCT = 1.5    # require price confirmation: stock must be moving
SKIP_LAST_MINUTES = 30
SLOT_SIZE_FACTOR = 0.5         # 50% of normal slot
STOP_ATR_MULTIPLIER = 1.5      # tighter than base picker

CATALYST_KEYWORDS = [
    r"\bacqui(re|sition|red|sitions)\b",
    r"\bmerger\b", r"\bmerged?\b",
    r"\btakeover\b",
    r"\bUSFDA\b", r"\bFDA approval\b", r"\bdrug approval\b",
    r"\bcontract win\b", r"\border (win|book)\b", r"\bbagged.{0,20}order\b",
    r"\bresults?\s*(beat|surge|jump|miss)\b",
    r"\bdividend\b.*\bdeclared\b", r"\bbonus issue\b",
    r"\bblock deal\b", r"\bbulk deal\b",
    r"\bqualified institutional\b", r"\bQIP\b",
    r"\b(profit|revenue|earnings)\s*(jump|surge|rises?|grew)\b",
    r"\bbuyback\b",
    r"\brights issue\b",
]


@dataclass
class CatalystDecision:
    symbol: str
    score: float                    # heuristic confidence (article count + decay)
    matched_articles: int
    distinct_sources: int
    sample_titles: list[str] = field(default_factory=list)
    catalyst_kind: str = ""
    intended_slot_notional: float = 0.0
    intended_stop: float = 0.0


def _has_catalyst(text: str) -> Optional[str]:
    for pat in CATALYST_KEYWORDS:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return m.group(0)
    return None


def _article_age_hours(article: dict, now: datetime) -> float:
    try:
        pub_str = article.get("published_at") or article.get("fetched_at") or ""
        pub = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return max(0.0, (now - pub).total_seconds() / 3600.0)
    except Exception:
        return 999.0


def scan_for_catalysts(
    held_symbols: set[str],
    available_cash: float,
    target_slot: float,
    risk_overlay_active: bool = False,
    require_market_open: bool = True,
    require_price_confirmation: bool = True,
) -> list[CatalystDecision]:
    """Main entry. Returns list of catalyst decisions ready to execute.

    Caller (mark_to_market) is responsible for actually opening the positions.

    require_market_open=False: used by pre-open signal generator (08:30 IST scan)
                               where we want catalyst names BEFORE market opens.
    require_price_confirmation=False: skip the intraday-move gate (used pre-open
                                      since LTP doesn't reflect tomorrow's open).
    """
    if require_market_open and not is_market_hours():
        return []
    if require_market_open:
        ist = now_ist()
        minutes_to_close = (15 * 60 + 30) - (ist.hour * 60 + ist.minute)
        if minutes_to_close <= SKIP_LAST_MINUTES:
            return []
    if risk_overlay_active:
        return []

    # Pull recent articles
    articles = _fetch_recent_articles(hours=LOOKBACK_HOURS)
    if not articles:
        return []

    now_utc = datetime.now(timezone.utc)
    # Build per-symbol article cluster
    by_symbol: dict[str, list[dict]] = {}
    universe = set(SYMBOL_TOKENS.keys()) - held_symbols
    for sym in universe:
        names_lower = {n.strip().lower() for n in names_for(sym)}
        if not names_lower:
            continue
        sym_articles = []
        for a in articles:
            if _article_age_hours(a, now_utc) > LOOKBACK_HOURS:
                continue
            if _matches_symbol(a, names_lower):
                sym_articles.append(a)
        if len(sym_articles) >= MIN_ARTICLES:
            by_symbol[sym] = sym_articles

    # Filter by source diversity + catalyst keyword presence + price confirmation
    # Lazy-fetch intraday move only for shortlisted symbols to keep cost bounded
    decisions: list[CatalystDecision] = []
    candidates = []
    for sym, arts in by_symbol.items():
        sources = {a.get("source") for a in arts if a.get("source")}
        if len(sources) < MIN_DISTINCT_SOURCES:
            continue
        catalyst_kind = None
        for a in arts:
            text = (a.get("title") or "") + " " + (a.get("excerpt") or "")
            kind = _has_catalyst(text)
            if kind:
                catalyst_kind = kind
                break
        if not catalyst_kind:
            continue
        candidates.append((sym, arts, sources, catalyst_kind))

    # Price confirmation: require today_total_pct >= MIN_INTRADAY_MOVE_PCT
    # (the catalyst should already be moving the price; if not, skip — likely
    # priced in or rumor). Skipped when called pre-open.
    if candidates and require_price_confirmation:
        try:
            from data_fetcher import get_fetcher
            from strategy.intraday_signals import compute_intraday_features
            f = get_fetcher()
            if not f.logged_in:
                f.login()
            confirmed = []
            for sym, arts, sources, catalyst_kind in candidates:
                try:
                    ltp = float(f.get_ltp(sym).get("ltp", 0))
                    if ltp <= 0:
                        continue
                    feat = compute_intraday_features(sym, ltp)
                    if feat is None or feat.total_pct < MIN_INTRADAY_MOVE_PCT:
                        logger.debug(f"catalyst {sym}: price not confirming "
                                     f"(today {feat.total_pct if feat else 0:+.2f}% < "
                                     f"{MIN_INTRADAY_MOVE_PCT}%) -- skip")
                        continue
                    confirmed.append((sym, arts, sources, catalyst_kind, feat))
                except Exception as e:
                    logger.debug(f"catalyst price check failed for {sym}: {e}")
            candidates_filtered = confirmed
        except Exception as e:
            logger.warning(f"catalyst price-confirmation pass failed: {e}; falling back to news-only")
            candidates_filtered = [(s, a, src, k, None) for s, a, src, k in candidates]
    elif candidates:
        # No price confirmation required (pre-open use): keep all news-passing candidates
        candidates_filtered = [(s, a, src, k, None) for s, a, src, k in candidates]
    else:
        candidates_filtered = []

    for sym, arts, sources, catalyst_kind, *_ in candidates_filtered:
        # Heuristic score = decayed article count, weighted by source diversity
        score = sum(0.5 ** (_article_age_hours(a, now_utc) / 3.0) for a in arts) * (len(sources) / 5.0)
        sample_titles = [(a.get("title") or "")[:120] for a in arts[:3]]
        slot = target_slot * SLOT_SIZE_FACTOR
        if slot > available_cash:
            slot = available_cash
        decisions.append(CatalystDecision(
            symbol=sym,
            score=round(score, 2),
            matched_articles=len(arts),
            distinct_sources=len(sources),
            sample_titles=sample_titles,
            catalyst_kind=catalyst_kind,
            intended_slot_notional=slot,
            intended_stop=0.0,  # caller will compute from current price * (1 - 1.5*atr_pct)
        ))
    decisions.sort(key=lambda d: -d.score)
    return decisions

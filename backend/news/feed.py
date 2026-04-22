"""
News feed integration: reads newsapp's Supabase `articles` table to enrich
trading signals with real-time news-flow data.

Three features:
  1. Per-symbol mention count + sentiment proxy (last 24h / 7d)
  2. Macro signal scan (FII, RBI, Fed, crude, inflation keywords)
  3. Event alerts (earnings, SEBI/RBI announcements, M&A) per held symbol

Fail-safe: if Supabase is unreachable, returns empty / None so trading pipeline
still works without news enrichment.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests
from logzero import logger


# Read-only anon key (newsapp makes articles publicly readable via RLS policy)
# Falls back to config.NEWSAPP_CONFIG if env vars not set.
try:
    from config import NEWSAPP_CONFIG as _CFG
except Exception:
    _CFG = {"url": "", "anon_key": ""}

SUPABASE_URL = os.environ.get("NEWSAPP_SUPABASE_URL") or _CFG.get("url") or ""
SUPABASE_ANON_KEY = os.environ.get("NEWSAPP_SUPABASE_ANON_KEY") or _CFG.get("anon_key") or ""
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "news_cache.json"
CACHE_TTL_MINUTES = 30


from news.lexicon import score_text as _lm_score_text

# Half-life: 24h for liquid names means an article from 24h ago has half the
# impact of one from now; 48h ago has 1/4; 72h ago has 1/8. After 7 days:
# weight is ~0.1% — practically zero. This models alpha decay faithfully.
DECAY_HALF_LIFE_HOURS = 24.0

MACRO_KEYWORDS = {
    "fii":            ["FII", "foreign institutional", "foreign investor"],
    "dii":            ["DII", "domestic institutional"],
    "rbi":            ["RBI", "Reserve Bank of India", "repo rate"],
    "fed":            ["Fed ", "Federal Reserve", "FOMC"],
    "crude":          ["crude", "Brent", "oil price"],
    "inflation":      ["inflation", "CPI", "WPI"],
    "rupee":          ["rupee", "INR", "USD/INR"],
    "gdp":            ["GDP"],
}

EARNINGS_KEYWORDS = ["Q1 results", "Q2 results", "Q3 results", "Q4 results",
                     "quarterly results", "earnings", "profit after tax", "net profit"]

# Phrases that indicate an exchange filing IS an actual results announcement
# (the numbers have been filed) versus housekeeping. Body match preferred over
# title because NSE/BSE titles are templated ("Outcome of Board Meeting").
RESULTS_FILED_BODY_PATTERNS = [
    r"submitted.*?financial result",
    r"audited.*?financial result",
    r"unaudited.*?financial result",
    r"financial result.*?for the (quarter|year|period)",
    r"earnings call",
]
RESULTS_FILED_TITLE_PATTERNS = [
    r"audited financial result",
    r"unaudited financial result",
    r"earnings call transcript",
]
# Phrases on board-meeting intimations that pre-announce upcoming results
RESULTS_INTIMATION_PATTERNS = [
    r"board meeting intimation.*?(financial result|audited|unaudited)",
    r"approve.*?financial result",
    r"consider.*?financial result",
]


# ---------- Cache ------------------------------------------------------------

def _load_cache() -> Optional[dict]:
    if not CACHE_PATH.exists():
        return None
    try:
        cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        fetched_at = datetime.fromisoformat(cache.get("fetched_at", ""))
        if datetime.now() - fetched_at < timedelta(minutes=CACHE_TTL_MINUTES):
            return cache["data"]
    except Exception:
        return None
    return None


def _save_cache(data: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"fetched_at": datetime.now().isoformat(), "data": data}, indent=2, default=str),
        encoding="utf-8",
    )


# ---------- Supabase REST -----------------------------------------------------

def _fetch_recent_articles(hours: int = 168) -> list[dict]:
    """
    Pull last `hours` articles from the business + wire categories.
    Uses Supabase REST (PostgREST). Returns [] on any failure so trading still runs.
    """
    if not SUPABASE_ANON_KEY:
        logger.info("NEWSAPP_SUPABASE_ANON_KEY not set; news enrichment disabled")
        return []
    try:
        cutoff = (datetime.utcnow() - timedelta(hours=hours)).isoformat()
        url = f"{SUPABASE_URL}/rest/v1/articles"
        params = {
            "select": "id,title,excerpt,body,source,category,published_at,url",
            "category": "in.(business,wire,filings)",
            "published_at": f"gte.{cutoff}",
            "order": "published_at.desc",
            "limit": "2000",
        }
        headers = {
            "apikey": SUPABASE_ANON_KEY,
            "Authorization": f"Bearer {SUPABASE_ANON_KEY}",
        }
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        logger.warning(f"News fetch failed (non-fatal): {e}")
        return []


# ---------- Analysis ---------------------------------------------------------

def _sentiment(text: str) -> float:
    """Loughran-McDonald finance-specific sentiment score (net, signed)."""
    return _lm_score_text(text or "").get("net", 0.0)


def _decay_weight(age_hours: float) -> float:
    """Exponential half-life decay. weight(0h)=1.0, weight(24h)=0.5, weight(72h)=0.125."""
    if age_hours < 0:
        return 1.0
    return 0.5 ** (age_hours / DECAY_HALF_LIFE_HOURS)


def _article_age_hours(article: dict, now: datetime) -> float:
    try:
        pub = datetime.fromisoformat((article.get("published_at") or "").replace("Z", "+00:00"))
        pub = pub.replace(tzinfo=None)
        return max(0.0, (now - pub).total_seconds() / 3600.0)
    except Exception:
        return 999.0  # very stale -> near-zero weight


def _mention_count(articles: list[dict], symbol: str, company_keywords: list[str]) -> tuple[int, int]:
    """Return (count_24h, count_7d) of articles mentioning symbol or any company_keyword."""
    now = datetime.utcnow()
    c24 = 0
    c7d = 0
    needles = [re.escape(symbol)] + [re.escape(k) for k in company_keywords if k]
    pattern = re.compile(r"\b(" + "|".join(needles) + r")\b", re.IGNORECASE)
    for a in articles:
        hay = (a.get("title") or "") + " " + (a.get("excerpt") or "") + " " + ((a.get("body") or "")[:2000])
        if not pattern.search(hay):
            continue
        c7d += 1
        if _article_age_hours(a, now) <= 24:
            c24 += 1
    return c24, c7d


def _decayed_sentiment(articles: list[dict], symbol: str, hours: float | None = None) -> float:
    """
    Aggregate decay-weighted sentiment across articles mentioning `symbol`.
    If hours is set, only articles newer than that count (with decay still applied).
    """
    now = datetime.utcnow()
    pattern = re.compile(r"\b" + re.escape(symbol) + r"\b", re.IGNORECASE)
    total = 0.0
    for a in articles:
        hay = (a.get("title") or "") + " " + (a.get("excerpt") or "")
        if not pattern.search(hay):
            continue
        age = _article_age_hours(a, now)
        if hours is not None and age > hours:
            continue
        w = _decay_weight(age)
        total += _sentiment(hay) * w
    return round(total, 2)


def _macro_scan(articles: list[dict]) -> dict:
    """
    Decay-weighted macro scan. Each article's sentiment contribution decays
    exponentially with article age (half-life = 24h).
    """
    now = datetime.utcnow()
    counts = {k: 0.0 for k in MACRO_KEYWORDS}
    sentiment = {k: 0.0 for k in MACRO_KEYWORDS}
    for a in articles:
        hay = (a.get("title") or "") + " " + (a.get("excerpt") or "")
        hay_lower = hay.lower()
        s = _sentiment(hay)
        w = _decay_weight(_article_age_hours(a, now))
        for macro_key, needles in MACRO_KEYWORDS.items():
            for n in needles:
                if n.lower() in hay_lower:
                    counts[macro_key] += w
                    sentiment[macro_key] += s * w
                    break
    return {
        "counts_7d":    {k: round(v, 2) for k, v in counts.items()},
        "sentiment_7d": {k: round(v, 2) for k, v in sentiment.items()},
    }


def _earnings_mentions(articles: list[dict]) -> list[str]:
    """Return unique list of article titles that mention earnings/results."""
    out = set()
    for a in articles:
        hay = (a.get("title") or "")
        if any(k.lower() in hay.lower() for k in EARNINGS_KEYWORDS):
            out.add(hay)
        if len(out) >= 30:
            break
    return list(out)


def _classify_filing(article: dict) -> Optional[str]:
    """For a category=filings article, return 'filed' | 'intimation' | None."""
    if (article.get("category") or "") != "filings":
        return None
    title = (article.get("title") or "").lower()
    body = (article.get("body") or "").lower()[:600]
    for p in RESULTS_INTIMATION_PATTERNS:
        if re.search(p, title) or re.search(p, body):
            return "intimation"
    for p in RESULTS_FILED_TITLE_PATTERNS:
        if re.search(p, title):
            return "filed"
    for p in RESULTS_FILED_BODY_PATTERNS:
        if re.search(p, body):
            return "filed"
    return None


def _company_from_title(title: str) -> str:
    """NSE/BSE titles look like 'Tata Elxsi Ltd ? Audited Financial Results...'.
    Strip from the first dash/em-dash onward to get the company portion."""
    if not title:
        return ""
    # The em-dash gets normalized to '?' in DB; handle both.
    for sep in [" \u2014 ", " \u2013 ", " - ", " ? ", "?"]:
        if sep in title:
            return title.split(sep, 1)[0].strip()
    return title.strip()


def _extract_results_filings(articles: list[dict]) -> tuple[list[dict], list[dict]]:
    """Returns (today_results, pending_results) lists. Today = same calendar
    day in IST as 'now'. Both lists are sorted newest first, capped at 50."""
    today_ist = datetime.utcnow() + timedelta(hours=5, minutes=30)
    today_str = today_ist.strftime("%Y-%m-%d")
    today, pending = [], []
    for a in articles:
        kind = _classify_filing(a)
        if not kind:
            continue
        pub = (a.get("published_at") or "")[:10]
        entry = {
            "kind": kind,
            "company": _company_from_title(a.get("title") or ""),
            "title": a.get("title") or "",
            "source": a.get("source") or "",
            "published_at": a.get("published_at") or "",
            "url": a.get("url") or "",
        }
        if kind == "filed" and pub == today_str:
            today.append(entry)
        elif kind == "intimation":
            pending.append(entry)
    return today[:50], pending[:50]


# ---------- Public API --------------------------------------------------------

@dataclass
class NewsSnapshot:
    fetched_at: str
    article_count: int
    macro: dict
    symbol_mentions: dict       # {symbol: {c24, c7d, sentiment_24h}}
    earnings_titles: list[str]
    status: str                 # 'ok' | 'unavailable' | 'cached'
    today_results: list[dict] = field(default_factory=list)    # NSE/BSE results filed today
    pending_results: list[dict] = field(default_factory=list)  # board-meeting intimations for upcoming results


def fetch_news_snapshot(symbols: list[str], use_cache: bool = True) -> NewsSnapshot:
    """Main entry: returns a NewsSnapshot for the given symbols. Always succeeds (possibly with empty data)."""
    if use_cache:
        cached = _load_cache()
        if cached is not None:
            return NewsSnapshot(**cached, status="cached")

    articles = _fetch_recent_articles(hours=168)
    if not articles:
        snap = NewsSnapshot(
            fetched_at=datetime.now().isoformat(),
            article_count=0,
            macro={"counts_7d": {}, "sentiment_7d": {}},
            symbol_mentions={},
            earnings_titles=[],
            status="unavailable",
        )
        return snap

    macro = _macro_scan(articles)
    symbol_mentions = {}
    for sym in symbols:
        c24, c7d = _mention_count(articles, sym, [])
        # Decay-weighted finance sentiment from LM dictionary over last 24h and 7d
        sent_24h = _decayed_sentiment(articles, sym, hours=24)
        sent_7d = _decayed_sentiment(articles, sym, hours=None)
        symbol_mentions[sym] = {
            "c24": c24, "c7d": c7d,
            "sentiment_24h": sent_24h,
            "sentiment_7d": sent_7d,
        }

    earnings = _earnings_mentions(articles)
    today_results, pending_results = _extract_results_filings(articles)

    snap_data = {
        "fetched_at": datetime.now().isoformat(),
        "article_count": len(articles),
        "macro": macro,
        "symbol_mentions": symbol_mentions,
        "earnings_titles": earnings,
        "today_results": today_results,
        "pending_results": pending_results,
    }
    _save_cache(snap_data)
    return NewsSnapshot(**snap_data, status="ok")

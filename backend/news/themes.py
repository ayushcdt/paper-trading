"""
Theme detection layer (Phase 3B).

A theme is a recurring news pattern (e.g., 'ethanol_blending_push', 'rbi_rate_easing')
with documented keywords + sector implications. Detection runs against the current
news snapshot and returns:
  - active_themes: list of dicts with theme_id, score, supporting_articles
  - sector_tilts: dict {thematic_sector: tilt_score in [-1, +1]}

The hybrid_overlay then translates sector_tilts into per-symbol score adjustments.

DESIGN PRINCIPLES (informed by MVR findings, see data/research/mvr_findings.md):
  - Negativity asymmetry: penalty weights >= 2x boost weights (LM data shows
    negative news is much more predictive of price moves than positive)
  - Multi-source confirmation: theme requires >= min_distinct_sources articles
    from different publishers (mitigates single-outlet noise)
  - Time decay: each article's contribution decays with article age
  - Conservative thresholds initially: better to miss a theme than false-fire
  - All output is consumed in shadow mode first (see hybrid_overlay.py)

When adding a theme:
  1. Confirm at least one historical event you can point to where the theme
     fired and the named sectors actually moved in the predicted direction
  2. Set min_articles >= 3 and min_distinct_sources >= 2 for confirmation
  3. Note the half-life realistically (most themes decay in 3-7 days)
  4. Document the directional rationale in `notes`
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional


# ---------- Theme catalog ----------------------------------------------------
# IMPORTANT: thematic_sectors here are STRINGS that must match keys in
# news/sector_map.py THEME_SECTORS dict. Don't invent new ones without
# also adding the symbol list there.
#
# half_life_hours: weight(0h)=1.0, weight(half_life)=0.5, weight(2*hl)=0.25 ...
# A 72h half-life means an article 1 week old contributes ~10% of weight.

THEMES: dict[str, dict] = {
    "ethanol_blending_push": {
        "keywords": [
            r"ethanol blend", r"\bE\d{2,3}\b", r"flex[\- ]?fuel",
            r"sugarcane control order", r"100% ethanol",
            r"biofuel.*polic", r"ethanol.*polic",
        ],
        "positive_for": ["sugar", "auto_2w_ev"],
        "negative_for": [],   # OMC margin compression is too multi-factor to assert
        "half_life_hours": 72,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Govt ethanol-blending mandate moves sugar mill margins (ethanol-from-cane "
                 "is a higher-value use of cane than sugar). Flex-fuel push helps EV-2W. "
                 "OMC impact is ambiguous (volume shift but margin pressure) so no negative_for.",
    },
    "rbi_rate_easing": {
        "keywords": [
            r"RBI.*(cut|reduce|lower).*(repo|rate)",
            r"(repo|policy).*rate.*(cut|reduced)",
            r"monetary policy.*(easing|dovish)",
            r"rate.*cut.*RBI",
        ],
        "positive_for": ["realty", "nbfc", "psu_banks", "auto_4w"],
        "negative_for": [],
        "half_life_hours": 168,  # rate-cycle theme persists ~1 week in news
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Rate cuts boost interest-sensitive sectors. Realty + auto-4w are "
                 "well-documented beneficiaries; NBFCs benefit from cheaper funding.",
    },
    "rbi_rate_tightening": {
        "keywords": [
            r"RBI.*(hike|raise|increase).*(repo|rate)",
            r"(repo|policy).*rate.*(hike|increased)",
            r"monetary policy.*(tightening|hawkish)",
        ],
        "positive_for": [],
        "negative_for": ["realty", "nbfc", "auto_4w"],
        "half_life_hours": 168,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Mirror of rate_easing. Negative for the same sectors.",
    },
    "defence_capex_push": {
        "keywords": [
            r"\bdefence\b.*\bbudget\b", r"\bdefence\b.*\ballocation\b",
            r"\bDRDO\b",
            r"\b(BEL|HAL|MAZDOCK|BDL)\b.*\b(order|contract|capex|tender|deal)\b",
            r"\bmake in india\b.*\bdefence\b",
            r"\bAatmaNirbhar\b.*\bdefence\b",
            r"\bdefence\b.*\bexport\b",
            r"\bindigenisation\b.*\bdefence\b",
        ],
        "positive_for": ["defence_psu"],
        "negative_for": [],
        "half_life_hours": 120,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Defence PSU stocks (BEL, BDL, HAL, MAZDOCK) move on indigenisation "
                 "headlines + budget allocation news. PARAS DEFENCE is private.",
    },
    "iran_oil_supply_shock": {
        "keywords": [
            r"iran.*(crude|oil).*supply",
            r"strait of hormuz.*close",
            r"iran.*oil.*sanctions",
            r"middle east.*oil.*supply",
            r"(crude|brent).*surge.*(iran|conflict|war)",
        ],
        "positive_for": ["oil_upstream"],
        "negative_for": ["oil_omc", "aviation", "paint"],
        "half_life_hours": 96,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Crude price spike. Upstream (ONGC, Oil India) gain on realisation. "
                 "OMCs (BPCL, IOC, HPCL) get squeezed on marketing margins. "
                 "Aviation (IndiGo) and paint (Asian, Berger) get input-cost hit.",
    },
    "budget_infra_push": {
        "keywords": [
            r"infrastructure.*(budget|allocation|capex)",
            r"capex.*infrastructure",
            r"national infrastructure pipeline",
            r"budget.*(railways|roads|highways)",
            r"PM Gati Shakti",
            r"(NHAI|MoRTH).*allocation",
        ],
        "positive_for": ["railways_psu", "capital_goods", "cement", "infrastructure"],
        "negative_for": [],
        "half_life_hours": 168,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "Annual budget infrastructure capex announcements move L&T, BHEL, "
                 "cement names, and railway PSUs (IRCTC, IRFC, RVNL, RAILTEL).",
    },
    "china_plus_one_chemicals": {
        "keywords": [
            r"china.{0,30}plus one",
            r"specialty chemicals.*(india|export)",
            r"(china|chinese).*(supply|export).*(restriction|disruption)",
            r"chemical export.*india.*surge",
        ],
        "positive_for": ["specialty_chemicals"],
        "negative_for": [],
        "half_life_hours": 120,
        "min_articles": 3,
        "min_distinct_sources": 2,
        "notes": "China supply disruption boosts Indian specialty chem exports. "
                 "SRF, Aarti Industries, Deepak Nitrite, Navin Fluor benefit.",
    },
    "fii_strong_inflow": {
        "keywords": [
            r"FII.*(net|cumulative).*buy",
            r"foreign.*institutional.*invest.*(buying|inflow)",
            r"FII.*pump.*\d+",
            r"FII.*billion.*invest",
        ],
        "positive_for": ["bluechips", "psu_banks"],
        "negative_for": [],
        "half_life_hours": 48,
        "min_articles": 4,  # FII commentary is high-volume; require more confirmation
        "min_distinct_sources": 3,
        "notes": "Sustained FII inflow lifts large-caps (Nifty 50 names) and PSU banks "
                 "disproportionately. Effect is short-lived; 48h half-life.",
    },
    "fii_strong_outflow": {
        "keywords": [
            r"FII.*(net|cumulative).*sell",
            r"foreign.*institutional.*invest.*(selling|outflow)",
            r"FII.*sold.*\d+",
        ],
        "positive_for": [],
        "negative_for": ["bluechips", "psu_banks"],
        "half_life_hours": 48,
        "min_articles": 4,
        "min_distinct_sources": 3,
        "notes": "Mirror of fii_strong_inflow. Negative for same sectors.",
    },
}


# ---------- Detection -------------------------------------------------------

@dataclass
class ActiveTheme:
    theme_id: str
    score: float                         # weighted article count (after decay)
    matched_articles: int                # raw count meeting keywords
    distinct_sources: int                # number of unique source publishers
    sample_titles: list[str] = field(default_factory=list)
    positive_for: list[str] = field(default_factory=list)
    negative_for: list[str] = field(default_factory=list)


def _decay_weight(age_hours: float, half_life_hours: float) -> float:
    if age_hours < 0:
        return 1.0
    return 0.5 ** (age_hours / half_life_hours)


def _article_age_hours(article: dict, now: datetime) -> float:
    try:
        pub = datetime.fromisoformat((article.get("published_at") or "").replace("Z", "+00:00"))
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        return max(0.0, (now - pub).total_seconds() / 3600.0)
    except Exception:
        return 999.0


def _matches_theme(article: dict, theme: dict) -> bool:
    hay = ((article.get("title") or "") + " " + (article.get("excerpt") or "") + " " +
           ((article.get("body") or "")[:2000]))
    for pattern in theme["keywords"]:
        if re.search(pattern, hay, re.IGNORECASE):
            return True
    return False


def detect_active_themes(articles: list[dict], now: Optional[datetime] = None) -> list[ActiveTheme]:
    """Run all themes against the article list. Returns themes meeting the
    min_articles + min_distinct_sources thresholds, sorted by score desc."""
    if now is None:
        now = datetime.now(timezone.utc)
    out: list[ActiveTheme] = []
    for theme_id, theme in THEMES.items():
        matched = []
        for a in articles:
            if _matches_theme(a, theme):
                matched.append(a)
        if len(matched) < theme["min_articles"]:
            continue
        sources = {a.get("source") for a in matched if a.get("source")}
        if len(sources) < theme["min_distinct_sources"]:
            continue
        # Decay-weighted score
        score = sum(
            _decay_weight(_article_age_hours(a, now), theme["half_life_hours"])
            for a in matched
        )
        sample_titles = [(a.get("title") or "")[:120] for a in matched[:3]]
        out.append(ActiveTheme(
            theme_id=theme_id,
            score=round(score, 2),
            matched_articles=len(matched),
            distinct_sources=len(sources),
            sample_titles=sample_titles,
            positive_for=list(theme.get("positive_for") or []),
            negative_for=list(theme.get("negative_for") or []),
        ))
    out.sort(key=lambda t: -t.score)
    return out


def sector_tilts(active_themes: list[ActiveTheme]) -> dict[str, float]:
    """Aggregate per-sector tilts from all active themes.
    Tilt is signed in [-1, +1]; positive = boost candidates in sector,
    negative = penalize. Magnitude scales with theme score (capped)."""
    tilts: dict[str, float] = {}
    for t in active_themes:
        # Cap individual theme contribution to avoid one runaway theme dominating
        contribution = min(0.5, t.score / 10.0)
        for sec in t.positive_for:
            tilts[sec] = tilts.get(sec, 0.0) + contribution
        # MVR.3 finding: negative news is ~3x more predictive than positive.
        # Reflect this in tilt magnitudes.
        for sec in t.negative_for:
            tilts[sec] = tilts.get(sec, 0.0) - 2.0 * contribution
    # Clamp to [-1, +1]
    return {k: max(-1.0, min(1.0, v)) for k, v in tilts.items()}

"""
Hybrid scoring overlay (Phase 3C).

Takes V3 picks + news_snap + active_themes; computes what the score WOULD have
been if news/themes were factored in. Returns ORIGINAL picks unchanged in
shadow mode (default). Logs every decision to data/research/shadow_log.jsonl
for later evaluation by scripts/evaluate_shadow.py.

CONFIG:
  HYBRID_OVERLAY_MODE in config.py (string):
    "shadow" (default) -- log decisions, return picks unchanged
    "live"             -- actually adjust scores + reorder picks

  When flipping to "live", also:
    - Verify shadow_log shows >=20 trading days of data
    - Verify hybrid IR > V3 IR + 0.2 in scripts/evaluate_shadow.py output
    - Confirm no regime where hybrid is >10% worse than V3

DESIGN (informed by data/research/mvr_findings.md):
  - Negative news penalty is 2x positive news boost (negativity asymmetry)
  - Per-theme contribution capped to prevent one runaway theme from dominating
  - Pure additive: V3 score is the base; hybrid_score = v3_score * (1 + adjustment)
  - All adjustments capped at +/- 25% of V3 score
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SHADOW_LOG_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "research" / "shadow_log.jsonl"

# Maximum allowed adjustment to V3 score (in fractional terms)
MAX_BOOST = 0.15      # +15% maximum positive lift
MAX_PENALTY = -0.30   # -30% maximum negative penalty (asymmetric per MVR.3)


def _get_mode() -> str:
    try:
        from config import HYBRID_OVERLAY_MODE
        return HYBRID_OVERLAY_MODE
    except Exception:
        return "shadow"


@dataclass
class ShadowDecision:
    timestamp: str
    symbol: str
    v3_score: float
    hybrid_score: float
    adjustment_pct: float            # (hybrid - v3) / v3 * 100
    reasons: list[str] = field(default_factory=list)
    sector_tilts_applied: dict[str, float] = field(default_factory=dict)
    news_sentiment_24h: float = 0.0
    article_count_24h: int = 0
    story_buzz_24h: int = 0
    active_theme_ids: list[str] = field(default_factory=list)
    mode: str = "shadow"


def _append_log(entry: ShadowDecision) -> None:
    SHADOW_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with SHADOW_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), default=str) + "\n")
    except Exception:
        pass


def _symbol_news_features(symbol: str, news_snap) -> tuple[float, int, int]:
    """Returns (sentiment_24h, article_count_24h, story_buzz_24h) for a symbol."""
    if not news_snap:
        return (0.0, 0, 0)
    mentions = getattr(news_snap, "symbol_mentions", None) or {}
    m = mentions.get(symbol, {})
    return (
        float(m.get("sentiment_24h", 0.0)),
        int(m.get("c24", 0)),
        int(m.get("story_buzz_24h", 0)),
    )


def _sector_adjustment(symbol: str, sector_tilts: dict[str, float]) -> tuple[float, dict[str, float]]:
    """Sum of theme-sector tilts that include this symbol. Returns (adjustment, applied_tilts)."""
    if not sector_tilts:
        return (0.0, {})
    try:
        from news.sector_map import symbols_in_theme
    except Exception:
        return (0.0, {})
    applied = {}
    total = 0.0
    for sector, tilt in sector_tilts.items():
        if symbol in symbols_in_theme(sector):
            applied[sector] = tilt
            # Each tilt contributes up to +/- 5% of the V3 score
            total += tilt * 0.05
    return (total, applied)


def _news_adjustment(sentiment_24h: float, article_count_24h: int, story_buzz_24h: int) -> tuple[float, list[str]]:
    """Per-symbol news adjustment. Asymmetric: penalty > boost.

    Thresholds (calibrated from MVR.3 percentiles):
      sentiment_24h <= -3.0 with c24 >= 2  ->  -10% (strong negative)
      sentiment_24h <= -1.5 with c24 >= 2  ->  -4%  (mild negative)
      sentiment_24h >= +5.0 with c24 >= 2  ->  +5%  (strong positive)
      sentiment_24h >= +2.5 with c24 >= 1  ->  +2%  (mild positive)
      story_buzz_24h >= 3   ->  ambiguous; flag but no adjustment yet
    """
    adj = 0.0
    reasons = []
    if sentiment_24h <= -3.0 and article_count_24h >= 2:
        adj -= 0.10
        reasons.append(f"strong negative sent {sentiment_24h:+.1f} ({article_count_24h} arts) -10%")
    elif sentiment_24h <= -1.5 and article_count_24h >= 2:
        adj -= 0.04
        reasons.append(f"mild negative sent {sentiment_24h:+.1f} -4%")
    elif sentiment_24h >= 5.0 and article_count_24h >= 2:
        adj += 0.05
        reasons.append(f"strong positive sent {sentiment_24h:+.1f} +5%")
    elif sentiment_24h >= 2.5 and article_count_24h >= 1:
        adj += 0.02
        reasons.append(f"mild positive sent {sentiment_24h:+.1f} +2%")
    if story_buzz_24h >= 3:
        reasons.append(f"high buzz: {story_buzz_24h} stories (no adjustment yet)")
    return (adj, reasons)


def apply_hybrid_overlay(picks: list, news_snap, active_themes_payload: list[dict]) -> tuple[list, list[ShadowDecision]]:
    """Main entry. Returns (adjusted_or_original_picks, decisions_logged).

    `picks` is a list of dicts with at least 'symbol' and 'overall_score' (or 'score').
    `active_themes_payload` is the JSON-serialised list from themes.detect_active_themes
    (each item: {theme_id, score, positive_for, negative_for, ...}).
    """
    if not picks:
        return picks, []
    mode = _get_mode()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Build sector_tilts dict from active themes payload
    sector_tilts: dict[str, float] = {}
    active_ids = []
    for t in active_themes_payload or []:
        active_ids.append(t.get("theme_id", ""))
        contribution = min(0.5, float(t.get("score", 0.0)) / 10.0)
        for sec in t.get("positive_for") or []:
            sector_tilts[sec] = sector_tilts.get(sec, 0.0) + contribution
        for sec in t.get("negative_for") or []:
            sector_tilts[sec] = sector_tilts.get(sec, 0.0) - 2.0 * contribution
    sector_tilts = {k: max(-1.0, min(1.0, v)) for k, v in sector_tilts.items()}

    decisions: list[ShadowDecision] = []
    adjusted: list = []
    for p in picks:
        symbol = p.get("symbol") if isinstance(p, dict) else getattr(p, "symbol", None)
        if not symbol:
            adjusted.append(p)
            continue
        v3_score = float(
            (p.get("overall_score") if isinstance(p, dict) else getattr(p, "score", 0))
            or (p.get("score", 0) if isinstance(p, dict) else 0)
        )
        sent, c24, buzz = _symbol_news_features(symbol, news_snap)
        sec_adj, applied_sectors = _sector_adjustment(symbol, sector_tilts)
        news_adj, news_reasons = _news_adjustment(sent, c24, buzz)
        total_adj = max(MAX_PENALTY, min(MAX_BOOST, sec_adj + news_adj))
        hybrid = v3_score * (1.0 + total_adj)
        reasons = list(news_reasons)
        if applied_sectors:
            for sec, t in applied_sectors.items():
                reasons.append(f"sector {sec} tilt {t:+.2f}")
        decision = ShadowDecision(
            timestamp=now_iso, symbol=symbol,
            v3_score=round(v3_score, 4),
            hybrid_score=round(hybrid, 4),
            adjustment_pct=round((hybrid - v3_score) / v3_score * 100, 2) if v3_score else 0,
            reasons=reasons,
            sector_tilts_applied=applied_sectors,
            news_sentiment_24h=round(sent, 2),
            article_count_24h=c24,
            story_buzz_24h=buzz,
            active_theme_ids=active_ids,
            mode=mode,
        )
        decisions.append(decision)
        _append_log(decision)
        # Shadow mode: don't mutate picks
        if mode == "live":
            if isinstance(p, dict):
                new_p = dict(p)
                new_p["overall_score"] = hybrid
                new_p["hybrid_overlay_adjustment_pct"] = decision.adjustment_pct
                adjusted.append(new_p)
            else:
                # dataclass-like: just append unchanged for now (caller would handle)
                adjusted.append(p)
        else:
            adjusted.append(p)
    return adjusted, decisions


def summary_for_blob(decisions: list[ShadowDecision], active_themes_payload: list[dict]) -> dict:
    """Compact summary suitable for the analysis blob (consumed by /news-shadow page)."""
    return {
        "active_themes": active_themes_payload[:8],
        "decisions_count": len(decisions),
        "decisions": [asdict(d) for d in decisions[:30]],
        "mode": _get_mode(),
    }

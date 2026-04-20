"""
Adaptive Engine -- ties classifier + variants + guardrails into one decision.

Entry points:
    decide_live(histories, nifty_df, vix_series, universe)
        -> { regime, variant, picks, guardrail_state }

Used by both:
    - generate_analysis.py (live picker)
    - scripts/backtest_v3.py (historical simulator)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime

import pandas as pd

from .regime import classify_regime, compute_breadth, Regime
from .variants import build_variants, Pick, REGIME_TO_VARIANT
from .guardrails import (
    GuardrailState,
    load_state,
    save_state,
    update_portfolio,
    choose_variant,
)
from .targets import load_escalation_level, apply_escalation_to_variants
from .news_overlay import apply_news_overlay


@dataclass
class Decision:
    as_of: str
    regime: str
    regime_reason: str
    regime_inputs: dict
    variant: str
    variant_reason: str
    deploy_pct: float
    kill_switch_active: bool
    kill_switch_reason: str | None
    picks: list[dict]
    news_adjustments: list[dict] | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def decide(
    histories: dict[str, pd.DataFrame],
    nifty_close: pd.Series,
    vix_value: float | None,
    vix_history: pd.Series,
    target_date: pd.Timestamp,
    universe: list[str],
    current_equity: float | None = None,
    persist_state: bool = True,
    news_snap=None,
) -> Decision:
    """
    One-shot decision. Returns Decision object.
    """
    breadth = compute_breadth(histories, target_date)
    regime_asmt = classify_regime(
        nifty_close=nifty_close,
        breadth_above_200dma_pct=breadth,
        vix_value=vix_value,
        vix_history=vix_history,
    )

    state = load_state()
    if current_equity is not None:
        state = update_portfolio(state, current_equity)

    preferred = REGIME_TO_VARIANT[regime_asmt.regime.value]
    chosen_name, chosen_reason = choose_variant(state, preferred)

    # Apply target-based escalation (if we've been under-target for months)
    escalation_level = load_escalation_level()
    variants = apply_escalation_to_variants(build_variants(), escalation_level)

    # Level 2: if under-target for 6+ months AND regime is RANGE,
    # force momentum_cons to actually participate in trends
    if escalation_level >= 2 and chosen_name == "mean_reversion":
        chosen_name = "momentum_cons"
        chosen_reason += f" | escalation L{escalation_level}: forcing momentum_cons"

    variant = variants[chosen_name]
    picks = variant.pick(histories, target_date, universe)

    # News overlay: blacklist/boost/penalize based on live news sentiment
    news_log = []
    if news_snap is not None and chosen_name != "defensive":
        picks, news_log = apply_news_overlay(picks, news_snap)

    # Apply deploy_pct to effective pick count (defensive is already min-sized)
    if chosen_name == "defensive":
        pass
    else:
        effective_count = max(1, int(round(len(picks) * regime_asmt.deploy_pct)))
        picks = picks[:effective_count]

    if persist_state:
        save_state(state)

    return Decision(
        as_of=str(target_date.date()) if hasattr(target_date, "date") else str(target_date),
        regime=regime_asmt.regime.value,
        regime_reason=regime_asmt.reason,
        regime_inputs=regime_asmt.inputs,
        variant=chosen_name,
        variant_reason=chosen_reason + (f" (escalation L{escalation_level})" if escalation_level > 0 else ""),
        deploy_pct=regime_asmt.deploy_pct,
        kill_switch_active=state.kill_switch_active,
        kill_switch_reason=state.kill_switch_reason,
        picks=[
            {
                "symbol": p.symbol,
                "score": round(p.score, 4),
                "rank": p.rank,
                "stop": round(p.stop, 2),
                "entry_ref": round(p.entry_ref, 2),
                "variant": p.variant,
            }
            for p in picks
        ],
        news_adjustments=news_log,
    )

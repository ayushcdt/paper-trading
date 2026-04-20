"""
Live guardrails -- the survival layer.

Every rebalance/run checks:
  1. Variant decay: any variant's live 3M return > 2 sigma worse than backtest expectation?
  2. Portfolio DD: current equity vs peak. >15% triggers kill switch.
  3. Variant suspend list: if decay confirmed over 2 consecutive checks, suspend variant
     and fall back to the NEXT more conservative variant in priority order.

State is persisted in data/guardrail_state.json so it survives restarts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path


STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "guardrail_state.json"

# Priority: when a variant is suspended, we fall back LEFT (more conservative)
FALLBACK_ORDER = ["defensive", "mean_reversion", "momentum_cons", "momentum_agg"]


@dataclass
class VariantHealth:
    name: str
    expected_3m_return_pct: float      # from backtest
    expected_3m_stdev_pct: float       # from backtest
    live_3m_return_pct: float | None = None
    consecutive_decay_flags: int = 0
    suspended: bool = False
    last_checked: str | None = None


@dataclass
class GuardrailState:
    updated_at: str
    portfolio_peak: float
    portfolio_current: float
    drawdown_pct: float
    kill_switch_active: bool
    kill_switch_reason: str | None
    variants: dict[str, VariantHealth] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["variants"] = {k: asdict(v) for k, v in self.variants.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "GuardrailState":
        vs = {k: VariantHealth(**v) for k, v in d.get("variants", {}).items()}
        return cls(
            updated_at=d["updated_at"],
            portfolio_peak=d["portfolio_peak"],
            portfolio_current=d["portfolio_current"],
            drawdown_pct=d["drawdown_pct"],
            kill_switch_active=d["kill_switch_active"],
            kill_switch_reason=d.get("kill_switch_reason"),
            variants=vs,
        )


def load_state() -> GuardrailState:
    if STATE_PATH.exists():
        try:
            return GuardrailState.from_dict(json.loads(STATE_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return GuardrailState(
        updated_at=datetime.now().isoformat(),
        portfolio_peak=0.0,
        portfolio_current=0.0,
        drawdown_pct=0.0,
        kill_switch_active=False,
        kill_switch_reason=None,
    )


def save_state(state: GuardrailState) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")


def update_portfolio(state: GuardrailState, current_equity: float, kill_threshold_pct: float = 15.0) -> GuardrailState:
    state.portfolio_current = current_equity
    if current_equity > state.portfolio_peak:
        state.portfolio_peak = current_equity

    peak = state.portfolio_peak if state.portfolio_peak > 0 else current_equity
    state.drawdown_pct = (peak - current_equity) / peak * 100 if peak else 0

    if state.drawdown_pct >= kill_threshold_pct and not state.kill_switch_active:
        state.kill_switch_active = True
        state.kill_switch_reason = f"Portfolio DD {state.drawdown_pct:.1f}% >= {kill_threshold_pct}% at {datetime.now().isoformat()}"
    elif state.drawdown_pct < kill_threshold_pct / 2 and state.kill_switch_active:
        # Re-enable when DD recovers to half the threshold
        state.kill_switch_active = False
        state.kill_switch_reason = None

    state.updated_at = datetime.now().isoformat()
    return state


def check_variant_decay(
    state: GuardrailState,
    variant_name: str,
    live_3m_return_pct: float,
    expected_3m_return_pct: float,
    expected_3m_stdev_pct: float,
    sigma_threshold: float = 2.0,
) -> GuardrailState:
    """
    If live return is > sigma_threshold stdev worse than expected, flag decay.
    Two consecutive flags -> suspend the variant.
    """
    vh = state.variants.get(variant_name) or VariantHealth(
        name=variant_name,
        expected_3m_return_pct=expected_3m_return_pct,
        expected_3m_stdev_pct=expected_3m_stdev_pct,
    )
    vh.live_3m_return_pct = live_3m_return_pct
    vh.expected_3m_return_pct = expected_3m_return_pct
    vh.expected_3m_stdev_pct = expected_3m_stdev_pct
    vh.last_checked = datetime.now().isoformat()

    z = (live_3m_return_pct - expected_3m_return_pct) / expected_3m_stdev_pct if expected_3m_stdev_pct > 0 else 0
    if z < -sigma_threshold:
        vh.consecutive_decay_flags += 1
        if vh.consecutive_decay_flags >= 2:
            vh.suspended = True
    else:
        vh.consecutive_decay_flags = 0
        # Only reactivate a suspended variant after 30 days of no check activity
        # (handled manually or by nightly recalibration; don't flip automatically here)

    state.variants[variant_name] = vh
    return state


def choose_variant(
    state: GuardrailState,
    regime_preferred: str,
) -> tuple[str, str]:
    """
    Returns (variant_name, reason). Applies kill switch + suspension fallback.
    """
    if state.kill_switch_active:
        return "defensive", f"kill switch: {state.kill_switch_reason}"

    if regime_preferred not in state.variants or not state.variants[regime_preferred].suspended:
        return regime_preferred, "regime default"

    # Find next non-suspended variant to the left in FALLBACK_ORDER
    try:
        idx = FALLBACK_ORDER.index(regime_preferred)
    except ValueError:
        return "defensive", "unknown variant; fallback defensive"
    for fb in FALLBACK_ORDER[:idx]:
        if fb not in state.variants or not state.variants[fb].suspended:
            return fb, f"{regime_preferred} suspended; fell back to {fb}"
    return "defensive", "all variants suspended"

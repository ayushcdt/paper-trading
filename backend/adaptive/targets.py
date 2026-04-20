"""
Performance targets + accountability.

Without targets the adaptive engine can "play safe" forever -- sitting in
defensive or mean reversion and delivering 0% returns. That's worse than
Nifty (10-13% annual).

Targets define what "success" means. Accountability measures actual
performance against targets and escalates if we're consistently under.

Targets (retail momentum realistic):
    Monthly:    +1.5%   (~18% annual CAGR)
    Quarterly:  +4.5%
    Annual:     +18%

Escalation ladder (if under monthly target 3 months in a row):
    Level 1: Relax mean reversion RSI cutoff (40 -> 45 floor)
    Level 2: Force momentum_cons even in RANGE regime
    Level 3: Increase position count by 50%

All relaxations REVERT automatically once we're back on target for 1 month.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path


TARGETS = {
    # Ambitious targets -- the whole point of this engineering is to BEAT
    # passive. 18% is Nifty Midcap index; anyone can get that with zero effort.
    # 36% annual is what distinguishes top-quartile active from the rest.
    "monthly_pct":    2.6,   # ~36% CAGR compounded
    "quarterly_pct":  8.0,
    "annual_pct":    36.0,
}

STATE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "target_state.json"


@dataclass
class TargetStatus:
    period: str               # 'month' | 'quarter' | 'year'
    target_pct: float
    actual_pct: float
    on_track: bool
    months_under: int         # consecutive months below target
    escalation_level: int     # 0-3

    def to_dict(self):
        return asdict(self)


def compute_status(starting_equity: float, equity_curve: list[dict], now: datetime | None = None) -> dict:
    """
    equity_curve: list of {date, equity, ...} entries (from paper_portfolio.equity_curve()).
    Returns per-period status + overall escalation_level.
    """
    now = now or datetime.now()
    if not equity_curve:
        return {
            "monthly":   TargetStatus("month",    TARGETS["monthly_pct"],   0, True,  0, 0).to_dict(),
            "quarterly": TargetStatus("quarter",  TARGETS["quarterly_pct"], 0, True,  0, 0).to_dict(),
            "annual":    TargetStatus("year",     TARGETS["annual_pct"],    0, True,  0, 0).to_dict(),
            "escalation_level": 0,
            "months_under_target": 0,
        }

    # Sort by date
    ec = sorted(equity_curve, key=lambda x: x["date"])
    current = float(ec[-1]["equity"])

    def equity_on_or_after(cutoff: datetime) -> float:
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        for row in ec:
            if row["date"] >= cutoff_str:
                return float(row["equity"])
        return starting_equity

    month_start = equity_on_or_after(now - timedelta(days=30))
    quarter_start = equity_on_or_after(now - timedelta(days=90))
    year_start = equity_on_or_after(now - timedelta(days=365))

    month_pct = ((current - month_start) / month_start * 100) if month_start > 0 else 0
    quarter_pct = ((current - quarter_start) / quarter_start * 100) if quarter_start > 0 else 0
    year_pct = ((current - year_start) / year_start * 100) if year_start > 0 else 0

    # Need actual history to judge "under target" -- not enough data = L0
    first_date = datetime.strptime(ec[0]["date"], "%Y-%m-%d")
    days_running = (now - first_date).days

    months_under = 0
    if days_running >= 30:
        # We have at least a month of data; count consecutive months under target
        for i in range(1, min(13, days_running // 30 + 1)):
            prev = equity_on_or_after(now - timedelta(days=30 * i))
            later = equity_on_or_after(now - timedelta(days=30 * (i - 1)))
            if prev > 0:
                monthly_return = (later - prev) / prev * 100
                if monthly_return < TARGETS["monthly_pct"]:
                    months_under += 1
                else:
                    break

    escalation_level = 0
    if months_under >= 3:
        escalation_level = 1
    if months_under >= 6:
        escalation_level = 2
    if months_under >= 9:
        escalation_level = 3

    state = {
        "monthly": TargetStatus(
            "month", TARGETS["monthly_pct"], round(month_pct, 2),
            month_pct >= TARGETS["monthly_pct"], months_under, escalation_level,
        ).to_dict(),
        "quarterly": TargetStatus(
            "quarter", TARGETS["quarterly_pct"], round(quarter_pct, 2),
            quarter_pct >= TARGETS["quarterly_pct"], months_under, escalation_level,
        ).to_dict(),
        "annual": TargetStatus(
            "year", TARGETS["annual_pct"], round(year_pct, 2),
            year_pct >= TARGETS["annual_pct"], months_under, escalation_level,
        ).to_dict(),
        "escalation_level": escalation_level,
        "months_under_target": months_under,
        "updated_at": now.isoformat(),
    }

    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    return state


def load_escalation_level() -> int:
    if not STATE_PATH.exists():
        return 0
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("escalation_level", 0))
    except Exception:
        return 0


def apply_escalation_to_variants(variants: dict, level: int) -> dict:
    """
    Level 0: no changes (default)
    Level 1: relax mean_reversion criteria (done via min_picks already, but raise picks)
    Level 2: swap RANGE's mean_reversion for momentum_cons (force trend participation)
    Level 3: increase every variant's max_picks by 50%
    """
    if level == 0:
        return variants

    if level >= 1 and "mean_reversion" in variants:
        mr = variants["mean_reversion"]
        if hasattr(mr, "min_picks"):
            mr.min_picks = max(mr.min_picks, 7)
        if hasattr(mr, "max_picks"):
            mr.max_picks = max(mr.max_picks, 12)

    if level >= 3:
        for name, v in variants.items():
            if hasattr(v, "max_picks") and v.max_picks > 0:
                v.max_picks = int(v.max_picks * 1.5)

    return variants

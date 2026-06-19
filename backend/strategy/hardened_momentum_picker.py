"""
Hardened momentum picker — Track 2 / Portfolio 3.

Wraps the existing `momentum_picker.run_momentum_picker()` and layers
"public-track-record" disciplines on top of the raw output:

  1. STOP TIGHTENING — every per-position stop_loss is clamped to at most
     -8% from entry. The base picker uses wider ATR-based stops (we saw
     -24 to -28% on today's picks). Public subscribers won't tolerate
     that drawdown variance.

  2. SECTOR CAP TIGHTENING — at most 2 names per sector (base picker
     caps notional at 30%; we cap by count, more aggressive).

  3. VIX DEFENSIVE TILT — when VIX is elevated (> 22), halve the pick
     count from 10 to 5. Reduces gross exposure precisely when the
     market is most likely to whipsaw subscribers.

  4. AUDIT TRAIL — log every hardening decision into the output for
     SEBI/SmallCase due diligence.

What's NOT in this file (deferred to follow-ups):
  - Single-name cap 20% — already satisfied by equity_executor's 10%
    equal-weight slotting, so no enforcement code is needed at the
    picker layer.
  - 12-month LTCG hold preference — belongs in equity_executor (don't
    drop a held winner just because picker rank changed).
  - Monthly rebalance cadence — belongs in equity_executor (skip days
    that aren't the 1st trading day of the month).

Output is written to data/stocks_p3.json with the same schema as
data/stocks.json, so equity_executor.py reads it with `--portfolio p3`.

Run as: python -m strategy.hardened_momentum_picker
Cron:   09:15 IST after generate_analysis (which fires at 09:00)
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

from strategy.momentum_picker import run_momentum_picker


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "stocks_p3.json"

# Hardening parameters
MAX_STOP_PCT = -8.0          # stops can never be wider than this
MAX_NAMES_PER_SECTOR = 2     # tighter than base picker's notional cap
VIX_DEFENSIVE_THRESHOLD = 22
VIX_DEFENSIVE_MAX_PICKS = 5  # halved from 10
BASE_MAX_PICKS = 10


def _industry_of(symbol: str) -> str:
    """Look up sector/industry. Falls back to 'UNKNOWN' if not found."""
    try:
        from strategy.momentum_picker import _industry_of as _base
        return _base(symbol)
    except Exception:
        return "UNKNOWN"


def _parse_vix_value(regime_inputs: dict) -> float:
    """Extract numeric VIX from regime_inputs.vix_gate string like 'VIX 16.6 normal'."""
    s = regime_inputs.get("vix_gate", "") or ""
    for tok in s.split():
        try:
            return float(tok)
        except ValueError:
            continue
    return 0.0


def _harden(base: dict) -> dict:
    """Apply hardening overlay to a run_momentum_picker() result dict.

    Returns a new dict with the same schema as stocks.json but with
    hardened picks, hardened variant labels, and an audit trail.
    """
    # If the base picker said halt, pass it through unchanged — never
    # second-guess the drawdown circuit breaker.
    if base.get("kill_switch_active"):
        base["strategy_variant"] = "momentum-hardened-p3-v1"
        base["variant"] = "momentum_hardened_p3"
        base["hardening_notes"] = ["base picker halted; hardening pass-through only"]
        return base
    overlay = base.get("risk_overlay") or {}
    if overlay.get("tail_halt") or overlay.get("halt_active"):
        base["strategy_variant"] = "momentum-hardened-p3-v1"
        base["variant"] = "momentum_hardened_p3"
        base["hardening_notes"] = [
            f"base picker halted ({'tail_halt' if overlay.get('tail_halt') else 'halt_active'}); pass-through"
        ]
        return base

    audit: list[str] = []
    picks = list(base.get("picks") or [])
    extended = list(base.get("picks_extended") or [])
    regime_inputs = base.get("regime_inputs") or {}

    # 3. VIX defensive tilt
    vix_value = _parse_vix_value(regime_inputs)
    max_picks_now = BASE_MAX_PICKS
    if vix_value >= VIX_DEFENSIVE_THRESHOLD:
        max_picks_now = VIX_DEFENSIVE_MAX_PICKS
        audit.append(
            f"VIX {vix_value:.1f} >= {VIX_DEFENSIVE_THRESHOLD} -> defensive tilt: "
            f"picks reduced from {BASE_MAX_PICKS} to {VIX_DEFENSIVE_MAX_PICKS}"
        )

    # 2. Sector cap by count, applied while keeping rank order
    sector_count: dict[str, int] = {}
    hardened_picks: list[dict] = []
    rejected_for_sector: list[tuple[str, str]] = []
    for p in picks:
        sym = p.get("symbol")
        if not sym:
            continue
        sector = _industry_of(sym)
        if sector_count.get(sector, 0) >= MAX_NAMES_PER_SECTOR:
            rejected_for_sector.append((sym, sector))
            continue
        if len(hardened_picks) >= max_picks_now:
            break
        sector_count[sector] = sector_count.get(sector, 0) + 1
        hardened_picks.append(p)
    for sym, sector in rejected_for_sector:
        audit.append(f"dropped {sym} (sector '{sector}' already had {MAX_NAMES_PER_SECTOR} picks)")

    # 1. Stop normalization on the surviving picks.
    # Three problem cases get clamped to MAX_STOP_PCT:
    #   - stop missing / <= 0
    #   - stop wider than -8% from entry (the discipline)
    #   - stop >= entry price (stale / corporate-action data: would trip
    #     PaperPortfolio's P18 guard and the pick would never open)
    for p in hardened_picks:
        cmp_val = float(p.get("cmp") or 0)
        stop = float(p.get("stop_loss") or 0)
        if cmp_val <= 0:
            continue
        worst_acceptable = cmp_val * (1.0 + MAX_STOP_PCT / 100.0)
        needs_fix = (stop <= 0) or (stop < worst_acceptable) or (stop >= cmp_val)
        if needs_fix:
            old_stop_pct = (stop / cmp_val - 1) * 100 if stop > 0 else None
            p["stop_loss"] = round(worst_acceptable, 2)
            p["stop_pct"] = MAX_STOP_PCT
            if stop >= cmp_val and stop > 0:
                audit.append(
                    f"{p.get('symbol')}: stop {stop:.2f} >= cmp {cmp_val:.2f} "
                    f"(stale / corporate action); clamped to {worst_acceptable:.2f} (-8%)"
                )
            elif old_stop_pct is not None:
                audit.append(
                    f"{p.get('symbol')}: stop tightened from {old_stop_pct:.1f}% "
                    f"({stop:.2f}) to {MAX_STOP_PCT:.1f}% ({worst_acceptable:.2f})"
                )
            else:
                audit.append(f"{p.get('symbol')}: stop set to {MAX_STOP_PCT:.1f}% ({worst_acceptable:.2f})")

    kept = {p.get("symbol") for p in hardened_picks}
    hardened_extended = [e for e in extended if e.get("symbol") in kept][:max_picks_now]

    base["picks"] = hardened_picks
    base["picks_extended"] = hardened_extended
    base["strategy_variant"] = "momentum-hardened-p3-v1"
    base["variant"] = "momentum_hardened_p3"
    base["variant_reason"] = (
        f"momentum_agg base + hardening overlay: stops capped at {MAX_STOP_PCT}%, "
        f"max {MAX_NAMES_PER_SECTOR} per sector, VIX-defensive tilt at >= {VIX_DEFENSIVE_THRESHOLD}"
    )
    base["hardening_notes"] = audit
    base["hardening_params"] = {
        "max_stop_pct": MAX_STOP_PCT,
        "max_names_per_sector": MAX_NAMES_PER_SECTOR,
        "vix_defensive_threshold": VIX_DEFENSIVE_THRESHOLD,
        "vix_defensive_max_picks": VIX_DEFENSIVE_MAX_PICKS,
        "base_max_picks": BASE_MAX_PICKS,
    }
    base["generated_by"] = "Artha 2.0 -- Hardened Momentum (P3 public-track-record)"
    base["generated_at"] = datetime.now().isoformat()
    return base


def main():
    logger.info("Hardened picker P3 starting")
    base = run_momentum_picker(max_picks=BASE_MAX_PICKS)
    hardened = _harden(base)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(hardened, indent=2, default=str), encoding="utf-8")

    n_picks = len(hardened.get("picks") or [])
    notes = hardened.get("hardening_notes") or []
    logger.info(f"Hardened picker wrote {n_picks} picks to {OUTPUT_PATH.name}")
    for note in notes[:10]:
        logger.info(f"  audit: {note}")
    if len(notes) > 10:
        logger.info(f"  audit: ... and {len(notes) - 10} more")


if __name__ == "__main__":
    main()

"""
Quality / junk filter for momentum picks.

Reuses the existing fundamentals_cache via stock_picker.get_fundamentals.
Designed as a JUNK FILTER (reject obvious bad apples), not a quality screen.
Why: 79% universe coverage post-refetch, but ROE is null in 77% of records
(yfinance returns null for banks/financials and many mid-caps), so a hard
ROE gate would silently exclude most of the universe.

Thresholds picked to fire only on extremes:
  - debtToEquity > 200      -> REJECT (very high leverage; null exempted)
  - profitMargins < 0       -> REJECT (loss-making)
  - earningsGrowth < -0.50  -> REJECT (>50% earnings collapse YoY)

Pass-through on null fields.

Validated against today's losers: BANDHANBNK (earnings -51.7%) -> REJECT.
NATIONALUM, GESHIP, ANURAS, EXIDEIND all pass (legitimately positive earnings).
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


# Thresholds — extreme cuts only
MAX_DEBT_TO_EQUITY = 200.0
MIN_PROFIT_MARGIN = 0.0
MIN_EARNINGS_GROWTH = -0.50


def passes_junk_filter(symbol: str) -> tuple[bool, str]:
    """Returns (allowed, reason). True if pick is allowed; False if junk-flagged.
    Pass-through on missing fundamentals data so we don't break uncovered names."""
    try:
        from stock_picker import get_fundamentals
    except Exception:
        return True, "fundamentals fetcher unavailable"

    info, status = get_fundamentals(symbol)
    if status == "unavailable":
        return True, "no fundamentals data (passed)"

    de = info.get("debtToEquity")
    pm = info.get("profitMargins")
    eg = info.get("earningsGrowth")

    if de is not None and de > MAX_DEBT_TO_EQUITY:
        return False, f"debtToEquity {de:.0f} > {MAX_DEBT_TO_EQUITY}"
    if pm is not None and pm < MIN_PROFIT_MARGIN:
        return False, f"profitMargins {pm:+.3f} < {MIN_PROFIT_MARGIN} (loss-making)"
    if eg is not None and eg < MIN_EARNINGS_GROWTH:
        return False, f"earningsGrowth {eg:+.2f} < {MIN_EARNINGS_GROWTH} (collapse)"

    return True, "pass"


def filter_picks(symbols: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """Apply junk filter to a list of symbols. Returns (kept, rejected_with_reason)."""
    kept = []
    rejected = []
    for sym in symbols:
        ok, reason = passes_junk_filter(sym)
        if ok:
            kept.append(sym)
        else:
            rejected.append((sym, reason))
    return kept, rejected


if __name__ == "__main__":
    test_set = ["BANDHANBNK", "NATIONALUM", "GESHIP", "ANURAS", "EXIDEIND",
                "RELIANCE", "TCS", "RPOWER"]
    for sym in test_set:
        ok, reason = passes_junk_filter(sym)
        flag = "PASS" if ok else "REJECT"
        print(f"{sym:14s} {flag:6s}  {reason}")

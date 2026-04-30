"""
Earnings calendar — block new entries within T-2 to T+1 trading days
of scheduled earnings to avoid overnight gap risk.

Source: yfinance .calendar / .get_earnings_dates with .NS suffix.
Cache: data/earnings_calendar.json, 24h TTL.

Used by:
  - momentum_picker._build_universe_histories (filter at universe level)
  - catalyst_injection.scan_for_catalysts (don't open into earnings)

Published evidence: NSE small/mid caps show overnight gap stdev of 4-8%
on earnings nights vs 1-2% normal. Skipping the T-2..T+1 window is
essentially free expected return for the strategy.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "earnings_calendar.json"
CACHE_TTL_HOURS = 24
DEFAULT_DAYS_BEFORE = 2
DEFAULT_DAYS_AFTER = 1


def _load_cache() -> dict:
    if not CACHE_PATH.exists():
        return {}
    try:
        return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, default=str), encoding="utf-8")


def _is_cache_fresh(entry: dict) -> bool:
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        return (datetime.now() - fetched).total_seconds() < CACHE_TTL_HOURS * 3600
    except Exception:
        return False


def get_next_earnings_date(symbol: str) -> Optional[datetime]:
    """Returns the next scheduled earnings date (datetime). None if unavailable."""
    cache = _load_cache()
    entry = cache.get(symbol)

    if entry and _is_cache_fresh(entry):
        date_str = entry.get("next_earnings")
        if date_str:
            try:
                return datetime.fromisoformat(date_str)
            except Exception:
                pass

    # Refetch
    try:
        import yfinance as yf
        import pandas as pd
        for suffix in (".NS", ".BO"):
            try:
                ticker = yf.Ticker(f"{symbol}{suffix}")
                next_date: Optional[datetime] = None

                # Newer yfinance: .calendar returns dict {'Earnings Date': [datetime, ...], ...}
                cal = ticker.calendar
                if isinstance(cal, dict):
                    ed_list = cal.get("Earnings Date") or []
                    if ed_list:
                        v = ed_list[0]
                        if isinstance(v, datetime):
                            next_date = v
                        else:
                            try:
                                next_date = datetime.fromisoformat(str(v))
                            except Exception:
                                pass
                elif cal is not None and hasattr(cal, "empty") and not cal.empty:
                    # Older yfinance: DataFrame
                    if "Earnings Date" in cal.index:
                        ed = cal.loc["Earnings Date"]
                        if hasattr(ed, "values") and len(ed.values) > 0:
                            v = ed.values[0]
                            if v is not None:
                                next_date = datetime.fromisoformat(str(v)) if isinstance(v, str) else v.to_pydatetime()

                # Fallback: get_earnings_dates returns DataFrame with DatetimeIndex
                if next_date is None:
                    try:
                        ed = ticker.get_earnings_dates(limit=4)
                        if ed is not None and hasattr(ed, "empty") and not ed.empty:
                            now_aware = datetime.now().astimezone(ed.index.tz) if ed.index.tz else datetime.now()
                            future_idx = [i for i in ed.index if i > now_aware]
                            if future_idx:
                                next_date = future_idx[-1].to_pydatetime().replace(tzinfo=None)
                    except Exception:
                        pass

                if next_date is not None:
                    if next_date.tzinfo is not None:
                        next_date = next_date.replace(tzinfo=None)
                    cache[symbol] = {
                        "next_earnings": next_date.isoformat(),
                        "fetched_at": datetime.now().isoformat(),
                    }
                    _save_cache(cache)
                    return next_date
            except Exception as e:
                logger.debug(f"earnings fetch {symbol}{suffix}: {e}")
                continue
    except ImportError:
        return None

    # Fetch failed; cache the failure briefly so we don't hammer
    cache[symbol] = {"next_earnings": None, "fetched_at": datetime.now().isoformat()}
    _save_cache(cache)
    return None


def is_in_earnings_window(symbol: str,
                          days_before: int = DEFAULT_DAYS_BEFORE,
                          days_after: int = DEFAULT_DAYS_AFTER) -> tuple[bool, str]:
    """Returns (in_window, reason). True if today falls within T-days_before
    to T+days_after of the next scheduled earnings."""
    next_date = get_next_earnings_date(symbol)
    if next_date is None:
        return False, "no earnings date available"
    today = datetime.now()
    delta_days = (next_date - today).total_seconds() / 86400.0
    if -days_after <= delta_days <= days_before:
        return True, f"earnings on {next_date.date()} ({delta_days:+.1f}d away)"
    return False, f"next earnings {next_date.date()} (out of T-{days_before}/T+{days_after} window)"


if __name__ == "__main__":
    for sym in ["RELIANCE", "TCS", "BANDHANBNK", "EXIDEIND"]:
        d = get_next_earnings_date(sym)
        in_window, reason = is_in_earnings_window(sym)
        print(f"{sym:14s} next={d}  in_window={in_window}  ({reason})")

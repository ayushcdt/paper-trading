"""
Single source of truth for 'is NSE market open right now'.

Independent of the machine's local timezone -- always computes in IST.
Previously duplicated in 3 Python files + JS dashboard; bugs arose when the
machine was set to UTC or the JS version diverged.

Usage:
    from common.market_hours import is_market_hours
    if is_market_hours():
        ...

For testing / scripts that want a custom reference time:
    is_market_hours(ref=datetime(2026, 4, 22, 10, 30, tzinfo=timezone.utc))
"""

from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from typing import Optional


IST_OFFSET_HOURS = 5.5
NSE_OPEN  = dtime(9, 15)
NSE_CLOSE = dtime(15, 30)

# NSE trading holidays (YYYY-MM-DD). Verified against NSE 2026 calendar.
# Update annually. On these dates, is_market_hours() returns False even if
# the day is a weekday and time is within session window.
# Discovered after May 1 2026 (Maharashtra Day) caused ws_runner to enter
# an infinite restart loop when WS subscription failed silently on holiday.
NSE_HOLIDAYS_2026 = {
    "2026-01-26",  # Republic Day
    "2026-03-17",  # Holi
    "2026-03-31",  # Eid-ul-Fitr
    "2026-04-03",  # Mahavir Jayanti
    "2026-04-14",  # Dr. B.R. Ambedkar Jayanti
    "2026-04-18",  # Good Friday
    "2026-05-01",  # Maharashtra Day
    "2026-08-15",  # Independence Day
    "2026-09-07",  # Ganesh Chaturthi
    "2026-10-02",  # Mahatma Gandhi Jayanti
    "2026-10-21",  # Diwali Laxmi Pujan (muhurat trading separately)
    "2026-11-04",  # Guru Nanak Jayanti
    "2026-12-25",  # Christmas
}

NSE_HOLIDAYS = NSE_HOLIDAYS_2026  # alias for forward-compat


def now_ist(ref: Optional[datetime] = None) -> datetime:
    """Return current IST time as a naive datetime (tz stripped)."""
    if ref is None:
        ref = datetime.now(timezone.utc)
    elif ref.tzinfo is None:
        # Assume UTC when tz absent (most callers will pass tz-aware)
        ref = ref.replace(tzinfo=timezone.utc)
    ist = ref.astimezone(timezone(timedelta(hours=IST_OFFSET_HOURS)))
    return ist.replace(tzinfo=None)


def is_market_hours(ref: Optional[datetime] = None) -> bool:
    """True if NSE is currently in the trading session (09:15-15:30 IST, Mon-Fri,
    not on an NSE holiday)."""
    ist = now_ist(ref)
    if ist.weekday() >= 5:   # Saturday = 5, Sunday = 6
        return False
    if ist.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        return False
    return NSE_OPEN <= ist.time() <= NSE_CLOSE


def is_holiday(ref: Optional[datetime] = None) -> bool:
    """True if today is an NSE-listed trading holiday."""
    ist = now_ist(ref)
    return ist.strftime("%Y-%m-%d") in NSE_HOLIDAYS


def minutes_to_open(ref: Optional[datetime] = None) -> int:
    """
    Minutes until NSE opens next. Returns 0 if currently open. Handles day
    rollover (after 15:30 Friday -> wait until 09:15 Monday).
    """
    ist = now_ist(ref)
    if is_market_hours(ref):
        return 0

    # Walk forward day-by-day until we hit a weekday and the 09:15 mark
    target = ist.replace(hour=NSE_OPEN.hour, minute=NSE_OPEN.minute, second=0, microsecond=0)
    if target <= ist:
        target = target + timedelta(days=1)
    while target.weekday() >= 5 or target < ist or target.strftime("%Y-%m-%d") in NSE_HOLIDAYS:
        target = target + timedelta(days=1)
    return int((target - ist).total_seconds() / 60)

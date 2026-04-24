"""
Tick store: holds the latest tick per symbol in memory, persists to disk,
pushes to Redis via /api/blob.

Also captures the first tick per symbol that arrives at/after 09:15:15 IST
each trading day -- used by the paper engine to fill pending_opens at a
realistic post-open price (matches how AMOs actually execute on Angel).

Thread-safe (WebSocket callbacks run on a separate thread vs the pusher).
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common.market_hours import now_ist


LIVE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "live_ticks.json"
OPEN_TICKS_DIR = Path(__file__).resolve().parent.parent.parent / "data"

# Capture the first tick from 09:15:15 IST onwards. The 15-second buffer
# avoids the auction-discovery price (which AMOs don't fill at) and matches
# the first tick of continuous trading.
OPEN_CAPTURE_TIME_MIN_HMS = (9, 15, 15)
# Stop capturing after 09:30 IST -- if a tick arrives later than that for a
# given symbol it doesn't represent the open anymore.
OPEN_CAPTURE_TIME_MAX_HMS = (9, 30, 0)


class TickStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._ticks: dict[str, dict] = {}       # {symbol: {ltp, ...}}
        self._open_ticks: dict[str, dict] = {}  # {symbol: {ltp, captured_at}} -- today only
        self._open_ticks_date: str = ""         # IST date string when _open_ticks was last reset
        self._last_push = 0.0
        self._vercel_url = None
        self._vercel_key = None
        try:
            from config import VERCEL_CONFIG
            self._vercel_url = VERCEL_CONFIG["app_url"]
            self._vercel_key = VERCEL_CONFIG["secret_key"]
        except Exception:
            pass

    def _maybe_capture_open_tick(self, symbol: str, payload: dict) -> None:
        """If this is the first tick for `symbol` in today's open-capture window,
        record it. Caller must hold self._lock."""
        ist = now_ist()
        today_ist = ist.strftime("%Y-%m-%d")
        # Roll the open-tick dict over at IST midnight
        if today_ist != self._open_ticks_date:
            self._open_ticks = {}
            self._open_ticks_date = today_ist
        # Only capture during the post-open window
        hms = (ist.hour, ist.minute, ist.second)
        if not (OPEN_CAPTURE_TIME_MIN_HMS <= hms <= OPEN_CAPTURE_TIME_MAX_HMS):
            return
        if symbol in self._open_ticks:
            return  # already captured today's first
        ltp = payload.get("ltp")
        if not ltp:
            return
        self._open_ticks[symbol] = {
            "ltp": float(ltp),
            "captured_at_ist": ist.isoformat(),
            "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        }

    def update(self, symbol: str, payload: dict) -> None:
        """Called by WebSocket callback. `payload` keys: ltp, open, high, low, close, volume, exchange_timestamp."""
        with self._lock:
            self._ticks[symbol] = {
                **payload,
                "received_at": datetime.now(timezone.utc).isoformat(),
            }
            self._maybe_capture_open_tick(symbol, payload)

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "tick_count": len(self._ticks),
                "ticks": dict(self._ticks),
            }

    def persist(self) -> None:
        snap = self.snapshot()
        LIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        try:
            LIVE_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Tick persist failed: {e}")
        self.persist_open_ticks()

    def persist_open_ticks(self) -> None:
        """Write today's first-post-open ticks to a date-keyed file. Idempotent."""
        with self._lock:
            if not self._open_ticks_date or not self._open_ticks:
                return
            data = {
                "date_ist": self._open_ticks_date,
                "captured_after_ist": "%02d:%02d:%02d" % OPEN_CAPTURE_TIME_MIN_HMS,
                "ticks": dict(self._open_ticks),
            }
            target = OPEN_TICKS_DIR / f"open_ticks_{self._open_ticks_date}.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.warning(f"Open-ticks persist failed: {e}")

    def push_to_redis(self, min_interval_sec: float = 3.0) -> bool:
        """Throttled POST to /api/blob?key=live_ticks."""
        now = time.time()
        if now - self._last_push < min_interval_sec:
            return False
        self._last_push = now
        if not self._vercel_url or not self._vercel_key:
            return False
        snap = self.snapshot()
        try:
            r = requests.post(
                f"{self._vercel_url}/api/blob?key=live_ticks",
                json=snap,
                headers={"Content-Type": "application/json", "x-api-key": self._vercel_key},
                timeout=5,
            )
            return r.status_code == 200
        except Exception as e:
            logger.debug(f"Redis push failed: {e}")
            return False

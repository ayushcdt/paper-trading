"""
Tick store: holds the latest tick per symbol in memory, persists to disk,
pushes to Redis via /api/blob.

Thread-safe (WebSocket callbacks run on a separate thread vs the pusher).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from logzero import logger


LIVE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "live_ticks.json"


class TickStore:
    def __init__(self):
        self._lock = threading.Lock()
        self._ticks: dict[str, dict] = {}       # {symbol: {ltp, ...}}
        self._last_push = 0.0
        self._vercel_url = None
        self._vercel_key = None
        try:
            from config import VERCEL_CONFIG
            self._vercel_url = VERCEL_CONFIG["app_url"]
            self._vercel_key = VERCEL_CONFIG["secret_key"]
        except Exception:
            pass

    def update(self, symbol: str, payload: dict) -> None:
        """Called by WebSocket callback. `payload` keys: ltp, open, high, low, close, volume, exchange_timestamp."""
        with self._lock:
            self._ticks[symbol] = {
                **payload,
                "received_at": datetime.now().isoformat(),
            }

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "generated_at": datetime.now().isoformat(),
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

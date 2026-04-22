"""
Tick-driven paper portfolio marker.

Wraps PaperPortfolio and hooks into the WebSocket stream: every tick on a held
position updates the in-memory price map and (throttled) pushes a full paper
snapshot to Redis for the dashboard.

Throttling: full snapshot push happens at most once per MIN_PUSH_INTERVAL_SEC
to avoid hammering Vercel / SQLite. In between, ticks just update the price
map in memory -- so next push captures the latest values.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from pathlib import Path

import requests
from logzero import logger

from paper.portfolio import PaperPortfolio, STARTING_CAPITAL


MIN_PUSH_INTERVAL_SEC = 5.0   # full snapshot to Redis at most every 5 sec
MIN_DB_WRITE_INTERVAL_SEC = 30.0  # daily_marks DB write at most every 30 sec
EXPORT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "paper_portfolio.json"


class PaperMarker:
    def __init__(self):
        self._lock = threading.Lock()
        self._prices: dict[str, float] = {}         # symbol -> latest LTP
        self._held: set[str] = set()                # symbols we actually have positions in
        self._last_push = 0.0
        self._last_db_write = 0.0
        self._pf = PaperPortfolio()
        self._vercel_url = None
        self._vercel_key = None
        try:
            from config import VERCEL_CONFIG
            self._vercel_url = VERCEL_CONFIG["app_url"]
            self._vercel_key = VERCEL_CONFIG["secret_key"]
        except Exception:
            pass
        self.refresh_held()

    def refresh_held(self) -> set[str]:
        """Re-read currently-open position symbols from the paper DB."""
        try:
            held = set(self._pf.get_open_symbols())
            with self._lock:
                self._held = held
            return held
        except Exception as e:
            logger.warning(f"PaperMarker refresh_held error: {e}")
            return set()

    def update_tick(self, symbol: str, ltp: float) -> None:
        """Called by WS on_data callback for any ticked symbol."""
        if symbol not in self._held or ltp <= 0:
            return
        with self._lock:
            self._prices[symbol] = ltp
        self._maybe_push()

    def _maybe_push(self) -> None:
        now = time.time()
        if now - self._last_push < MIN_PUSH_INTERVAL_SEC:
            return
        # Snapshot under lock, push outside lock
        with self._lock:
            prices = dict(self._prices)
            held = set(self._held)
        if not prices or not held:
            return
        self._last_push = now

        # Throttle heavier DB work
        do_db_write = (now - self._last_db_write) >= MIN_DB_WRITE_INTERVAL_SEC
        try:
            if do_db_write:
                self._pf.mark_to_market(prices)
                self._last_db_write = now
            snap = self._pf.export_snapshot(prices)
            # Attach target status so dashboard has it too
            try:
                from adaptive.targets import compute_status
                snap["target_status"] = compute_status(STARTING_CAPITAL, snap.get("equity_curve", []))
            except Exception:
                pass
            self._write_local(snap)
            self._push_to_redis(snap)
        except Exception as e:
            logger.warning(f"PaperMarker push error: {e}")

    def _write_local(self, snap: dict) -> None:
        try:
            EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            EXPORT_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug(f"local write failed: {e}")

    def _push_to_redis(self, snap: dict) -> bool:
        if not self._vercel_url or not self._vercel_key:
            return False
        try:
            r = requests.post(
                f"{self._vercel_url}/api/blob?key=paper_portfolio",
                json=snap,
                headers={"Content-Type": "application/json", "x-api-key": self._vercel_key},
                timeout=5,
            )
            return r.status_code == 200
        except Exception as e:
            logger.debug(f"Redis push failed: {e}")
            return False


# Module-level singleton
_marker: PaperMarker | None = None


def get_marker() -> PaperMarker:
    global _marker
    if _marker is None:
        _marker = PaperMarker()
    return _marker

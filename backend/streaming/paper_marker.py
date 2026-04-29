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
        # Cached stop/target levels per held symbol — refreshed by refresh_held().
        # Format: {symbol: {"stop": float, "target": float, "variant": str, "entry": float}}
        # Per-tick stop/target check uses this so we don't hit SQLite on every tick.
        self._levels: dict[str, dict] = {}
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
        """Re-read currently-open positions + their stop/target levels from DB."""
        try:
            positions = self._pf.get_open_positions()
            held = set(positions.keys())
            levels = {}
            for sym, pos in positions.items():
                stop = pos.current_stop or pos.stop_at_entry or 0.0
                target = pos.target_price or 0.0
                levels[sym] = {
                    "stop": float(stop),
                    "target": float(target),
                    "variant": pos.variant,
                    "entry": float(pos.entry_price),
                }
            with self._lock:
                self._held = held
                self._levels = levels
            return held
        except Exception as e:
            logger.warning(f"PaperMarker refresh_held error: {e}")
            return set()

    def update_tick(self, symbol: str, ltp: float) -> None:
        """Called by WS on_data callback for any ticked symbol.
        Also checks stop/target per tick (sub-second latency vs the old 15-min
        scheduled mark_to_market check)."""
        if symbol not in self._held or ltp <= 0:
            return
        with self._lock:
            self._prices[symbol] = ltp
            lvl = self._levels.get(symbol)
        # Per-tick stop/target check — fires immediately when crossed
        if lvl:
            self._check_stop_target(symbol, ltp, lvl)
        self._maybe_push()

    def _check_stop_target(self, symbol: str, ltp: float, lvl: dict) -> None:
        """Fire close if ltp crossed stop or target. Drops symbol from _held
        on success so we don't refire before refresh_held picks up DB state."""
        stop = lvl.get("stop", 0.0)
        target = lvl.get("target", 0.0)
        entry = lvl.get("entry", 0.0)
        hit_stop = stop > 0 and ltp <= stop
        hit_target = target > 0 and ltp >= target
        if not (hit_stop or hit_target):
            return
        # Remove from _held FIRST under lock so concurrent ticks don't refire
        with self._lock:
            if symbol not in self._held:
                return  # already fired by another tick
            self._held.discard(symbol)
            self._levels.pop(symbol, None)
        # Now close outside the lock
        try:
            pnl_pct = (ltp - entry) / entry * 100 if entry else 0.0
            if hit_stop:
                reason = f"per-tick stop hit (Rs{ltp:.2f} <= stop Rs{stop:.2f}, P&L {pnl_pct:+.2f}%)"
                severity, label = "warning", "STOP HIT"
            else:
                reason = f"per-tick target hit (Rs{ltp:.2f} >= target Rs{target:.2f}, P&L {pnl_pct:+.2f}%)"
                severity, label = "info", "TARGET HIT"
            result = self._pf.close_position(symbol, ltp, reason)
            if result:
                logger.info(f"PER-TICK {label}: {symbol} @ Rs{ltp:.2f}  P&L Rs{result.get('pnl_inr', 0):+.0f}")
                try:
                    from alerts.channels import dispatch
                    dispatch(severity, f"{label}: {symbol}",
                             f"Closed at Rs{ltp:.2f}\nP&L: Rs{result.get('pnl_inr', 0):+.0f} ({pnl_pct:+.2f}%)\nReason: {reason}")
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"per-tick close failed for {symbol}: {e}")

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

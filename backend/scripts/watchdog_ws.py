"""
WebSocket watchdog: runs every 5 min, checks freshness of last tick.

If no ticks in last 5 min during market hours, logs a warning. (Actual restart
is handled by NSSM's auto-restart policy -- watchdog just alerts.)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger

from common.market_hours import is_market_hours

LIVE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "live_ticks.json"


def main():
    if not is_market_hours():
        return
    now = datetime.now()

    if not LIVE_PATH.exists():
        logger.warning("No live_ticks.json -- WS not producing any data")
        return

    try:
        data = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
        gen = datetime.fromisoformat(data["generated_at"])
        age = (now - gen).total_seconds()
        if age > 300:
            logger.warning(f"Live ticks stale: {age:.0f}s old (during market hours)")
            # Best-effort: fire an alert
            try:
                from alerts.channels import dispatch
                dispatch("warning",
                         f"WebSocket stream stale: {age:.0f}s",
                         f"Last tick at {gen}. NSSM should auto-restart; check logs.")
            except Exception:
                pass
        else:
            logger.info(f"WS healthy: last tick {age:.0f}s ago, {data.get('tick_count', 0)} symbols")
    except Exception as e:
        logger.warning(f"Watchdog read failed: {e}")


if __name__ == "__main__":
    main()

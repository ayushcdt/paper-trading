"""
Sync all dashboard data files to Vercel Redis via the /api/blob endpoint.

This runs after every pipeline / backtest / recalibration so the Vercel-hosted
dashboard has the same data the local files do.

Files mapped:
  data/paper_portfolio.json        -> blob:paper_portfolio
  data/backtest_results.json       -> blob:backtest_v1
  data/backtest_v2_results.json    -> blob:backtest_v2
  data/backtest_v3_results.json    -> blob:backtest_v3
  data/backtest_v3_oos_results.json -> blob:backtest_v3_oos
  data/variant_health.json         -> blob:variant_health
  data/variant_params.json         -> blob:variant_params
  data/target_state.json           -> blob:target_state
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests
from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import VERCEL_CONFIG


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

FILE_MAP = {
    "paper_portfolio":     "paper_portfolio.json",
    "backtest_v1":         "backtest_results.json",
    "backtest_v2":         "backtest_v2_results.json",
    "backtest_v3":         "backtest_v3_results.json",
    "backtest_v3_oos":     "backtest_v3_oos_results.json",
    "variant_health":      "variant_health.json",
    "variant_params":      "variant_params.json",
    "target_state":        "target_state.json",
    "alerts":              "alerts.json",
    "news_shadow_log":     "news_shadow_log.json",
    "live_ticks":          "live_ticks.json",
}


def sync_one(key: str, filename: str) -> bool:
    path = DATA_DIR / filename
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Cannot parse {filename}: {e}")
        return False

    url = f"{VERCEL_CONFIG['app_url']}/api/blob?key={key}"
    try:
        r = requests.post(
            url,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "x-api-key": VERCEL_CONFIG["secret_key"],
            },
            timeout=30,
        )
        if r.status_code == 200:
            logger.info(f"  synced {key} ({path.stat().st_size // 1024}KB)")
            return True
        logger.warning(f"  {key} sync failed: {r.status_code} {r.text[:150]}")
        return False
    except Exception as e:
        logger.warning(f"  {key} sync error: {e}")
        return False


def sync_all() -> dict:
    logger.info("Syncing dashboard data blobs to Vercel Redis...")
    results = {}
    for key, filename in FILE_MAP.items():
        results[key] = sync_one(key, filename)
    ok = sum(1 for v in results.values() if v)
    logger.info(f"Sync done: {ok}/{len(FILE_MAP)} blobs uploaded")
    return results


if __name__ == "__main__":
    sync_all()

"""
One-time bootstrap: download 400 days of daily bars for every universe symbol
+ Nifty + Bank Nifty + VIX + sector indices, and write to local bars.db.

After this runs, `data_fetcher.get_historical_data(ONE_DAY)` serves from DB.

Run: python scripts/bootstrap_bars.py
Expected time: ~15-20 min (rate-limit-polite 0.5s between calls)
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_fetcher import get_fetcher, SYMBOL_TOKENS
from data_store import upsert_bars, bar_count
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP


BOOTSTRAP_DAYS = 400
SLEEP_BETWEEN_CALLS = 0.4  # seconds -- polite to Angel rate limit


def main():
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            logger.error("Angel login failed; aborting bootstrap")
            return

    # Build universe: stocks + indices + sectors
    index_syms = ["NIFTY", "BANKNIFTY", "INDIAVIX"]
    sector_syms = [s for s in SYMBOL_TOKENS if s.startswith("NIFTY_")]
    stock_syms = [s for s in sorted(set(NIFTY_50 + NIFTY_NEXT_50 + NIFTY_MIDCAP))
                  if s in SYMBOL_TOKENS]
    universe = index_syms + sector_syms + stock_syms
    logger.info(f"Bootstrap: {len(universe)} symbols x {BOOTSTRAP_DAYS} days")

    ok = 0
    skipped = 0
    failed = 0
    for i, sym in enumerate(universe, 1):
        existing = bar_count(sym)
        if existing >= BOOTSTRAP_DAYS * 0.7:
            skipped += 1
            if i % 20 == 0:
                logger.info(f"  [{i}/{len(universe)}] {sym}: already have {existing} bars, skipping")
            continue

        try:
            df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=BOOTSTRAP_DAYS)
            if df.empty:
                failed += 1
                logger.warning(f"  [{i}/{len(universe)}] {sym}: empty response")
            else:
                n = upsert_bars(sym, df)
                ok += 1
                if i % 10 == 0:
                    logger.info(f"  [{i}/{len(universe)}] {sym}: {n} bars cached (running total ok={ok}, fail={failed})")
        except Exception as e:
            failed += 1
            logger.error(f"  [{i}/{len(universe)}] {sym}: {e}")

        time.sleep(SLEEP_BETWEEN_CALLS)

    logger.info(f"Bootstrap complete: {ok} cached, {skipped} already had data, {failed} failed")


if __name__ == "__main__":
    main()

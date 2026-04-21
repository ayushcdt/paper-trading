"""
Daily bar updater. Runs at 16:00 IST (post-close). Appends new bars since the
latest cached date for each symbol.

Typical run: 1 bar per symbol per day. Fast, low API footprint.
"""

from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data_fetcher import get_fetcher, SYMBOL_TOKENS
from data_store import upsert_bars, latest_date, all_symbols
from stock_picker import NIFTY_50, NIFTY_NEXT_50, NIFTY_MIDCAP


SLEEP_BETWEEN_CALLS = 0.3


def main():
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            logger.error("Angel login failed; aborting update")
            return

    # Only update symbols we already have in DB
    syms = all_symbols()
    if not syms:
        logger.warning("No symbols in DB yet. Run scripts/bootstrap_bars.py first.")
        return
    logger.info(f"Updating {len(syms)} symbols")

    today = datetime.now().date()
    updated = 0
    skipped = 0
    failed = 0

    for i, sym in enumerate(syms, 1):
        if sym not in SYMBOL_TOKENS:
            skipped += 1
            continue
        last = latest_date(sym)
        if last:
            last_dt = datetime.strptime(last, "%Y-%m-%d").date()
            gap_days = (today - last_dt).days
        else:
            gap_days = 400
        if gap_days <= 0:
            skipped += 1
            continue

        try:
            df = fetcher.get_historical_data(sym, interval="ONE_DAY", days=gap_days + 3)
            if df.empty:
                failed += 1
                continue
            # Only keep rows strictly newer than last
            if last:
                df = df[df["Date"] > last]
            if not df.empty:
                upsert_bars(sym, df)
                updated += 1
        except Exception as e:
            failed += 1
            logger.debug(f"  {sym}: {e}")

        time.sleep(SLEEP_BETWEEN_CALLS)

        if i % 25 == 0:
            logger.info(f"  [{i}/{len(syms)}] updated={updated} skipped={skipped} failed={failed}")

    logger.info(f"Update done: {updated} updated, {skipped} skipped, {failed} failed")


if __name__ == "__main__":
    main()

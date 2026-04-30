"""
Refetch fundamentals for the FULL universe (~540 symbols).

Today's coverage: 140/540 = 26%. Many losing trades (BANDHANBNK, ANURAS, GESHIP)
were stocks NOT in the cache, so the junk filter would silently pass them.
This script expands the cache to cover everything in SYMBOL_TOKENS.

Throttle: yfinance can hit rate limits at >1 req/sec. We sleep 0.5s between
calls. ~540 symbols × 0.5s = ~5 min runtime, plus retry jitter.

Run manually or via Postclose schedule (currently 15:35 IST).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from stock_picker import _load_fundamentals_cache, _save_fundamentals_cache, FUNDAMENTALS_FIELDS
from data_fetcher import SYMBOL_TOKENS

import yfinance as yf


THROTTLE_SEC = 0.5


def main():
    cache = _load_fundamentals_cache()
    initial_count = len(cache)
    logger.info(f"Initial cache: {initial_count} symbols")

    # Universe: all stocks in SYMBOL_TOKENS (excluding indices)
    universe = [s for s, t in SYMBOL_TOKENS.items() if not str(t).startswith("999")]
    logger.info(f"Universe to fetch: {len(universe)} symbols")

    fetched = 0
    failed = 0
    skipped = 0
    now = datetime.now()

    for i, sym in enumerate(universe, 1):
        # Skip if cached and fresh (< 7 days)
        entry = cache.get(sym)
        if entry:
            try:
                age_days = (now - datetime.fromisoformat(entry["fetched_at"])).days
                if age_days < 7:
                    skipped += 1
                    continue
            except Exception:
                pass

        try:
            ticker = yf.Ticker(f"{sym}.NS")
            raw = ticker.info or {}
            if not raw or not any(raw.get(f) for f in FUNDAMENTALS_FIELDS):
                # Try BSE suffix as fallback
                ticker = yf.Ticker(f"{sym}.BO")
                raw = ticker.info or {}
            if not raw or not any(raw.get(f) for f in FUNDAMENTALS_FIELDS):
                failed += 1
                logger.debug(f"  [{i}/{len(universe)}] {sym}: no data")
            else:
                info = {f: raw.get(f) for f in FUNDAMENTALS_FIELDS}
                cache[sym] = {"info": info, "fetched_at": now.isoformat()}
                fetched += 1
                if i % 25 == 0:
                    _save_fundamentals_cache(cache)
                    logger.info(f"  [{i}/{len(universe)}] checkpointed; "
                                f"fetched={fetched} skipped={skipped} failed={failed}")
        except Exception as e:
            failed += 1
            logger.debug(f"  [{i}/{len(universe)}] {sym}: {e}")

        time.sleep(THROTTLE_SEC)

    _save_fundamentals_cache(cache)
    final_count = len(cache)
    logger.info(f"=== DONE ===")
    logger.info(f"Initial: {initial_count}  Final: {final_count}  Added: {final_count - initial_count}")
    logger.info(f"This run: fetched={fetched}  skipped={skipped}  failed={failed}")


if __name__ == "__main__":
    main()

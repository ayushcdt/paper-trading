"""
Long-running Angel SmartAPI WebSocket v2 streamer.

Subscribes to: NIFTY + BANKNIFTY + INDIAVIX + 9 sector indices + currently-held
paper positions.

On each tick: updates TickStore in memory, persists to disk every ~3s, pushes
to Redis every ~3s.

Intended to run as a Windows service via NSSM (see scheduler/nssm_install.md).
Self-handles:
  - Reconnect on disconnect (SmartWebSocketV2 built-in)
  - Daily re-login at 05:00 IST (tokens expire at ~04:30)
  - Pause outside market hours (09:15 - 15:30 IST)
  - Periodic re-check of held positions (every 60s) to keep subscriptions fresh
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger, logfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from data_fetcher import get_fetcher, SYMBOL_TOKENS
from streaming.tick_store import TickStore
from paper.portfolio import PaperPortfolio
from config import ANGEL_CREDENTIALS


LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logfile(str(LOG_DIR / "ws.log"), maxBytes=5_000_000, backupCount=3)

MARKET_OPEN  = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Static symbols to always subscribe to
STATIC_INDICES = ["NIFTY", "BANKNIFTY", "INDIAVIX"]
STATIC_SECTORS = [s for s in SYMBOL_TOKENS if s.startswith("NIFTY_")]


def is_market_hours() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def build_subscription_list(extra_symbols: list[str]) -> list[dict]:
    """
    Build Angel WS subscription payload. Exchange type codes:
      1 = NSE CM (stocks)
      2 = NFO  (F&O)
      3 = BSE CM
      5 = MCX
     51 = NSE CDS
    Indices live on NSE CM (exchange_type=1).
    """
    symbols = set(STATIC_INDICES + STATIC_SECTORS + extra_symbols)
    nse_tokens = []
    for sym in symbols:
        token = SYMBOL_TOKENS.get(sym)
        if token:
            nse_tokens.append(token)
    if not nse_tokens:
        return []
    return [{"exchangeType": 1, "tokens": nse_tokens}]


def token_to_symbol() -> dict[str, str]:
    return {v: k for k, v in SYMBOL_TOKENS.items()}


class StreamerState:
    def __init__(self):
        self.store = TickStore()
        self.tok2sym = token_to_symbol()
        self.ws: SmartWebSocketV2 | None = None
        self.subscribed_tokens: set[str] = set()
        self.last_login_date = None


state = StreamerState()


def _ensure_login():
    """Login if needed; re-login if tokens are a day old."""
    today = datetime.now().date()
    if state.last_login_date == today and state.ws is not None:
        return True
    fetcher = get_fetcher()
    if not fetcher.login():
        logger.error("Angel login failed")
        return False
    state.last_login_date = today
    # Build WS with fresh tokens
    state.ws = SmartWebSocketV2(
        auth_token=fetcher.api.access_token if hasattr(fetcher.api, "access_token") else None,
        api_key=ANGEL_CREDENTIALS["api_key"],
        client_code=ANGEL_CREDENTIALS["client_id"],
        feed_token=fetcher.api.feed_token if hasattr(fetcher.api, "feed_token") else None,
    )
    return True


def on_data(wsapp, message):
    """WebSocket tick callback. Message is a dict with token, ltp, etc."""
    try:
        token = str(message.get("token", ""))
        sym = state.tok2sym.get(token)
        if not sym:
            return
        # LTP is in paise for indices, rupees for stocks (divide by 100 for LTP mode)
        ltp = message.get("last_traded_price", 0)
        if ltp:
            ltp = float(ltp) / 100.0
        state.store.update(sym, {
            "ltp": ltp,
            "volume": message.get("volume_trade_for_the_day", 0),
            "open": float(message.get("open_price_of_the_day", 0) or 0) / 100.0,
            "high": float(message.get("high_price_of_the_day", 0) or 0) / 100.0,
            "low":  float(message.get("low_price_of_the_day", 0) or 0) / 100.0,
            "close": float(message.get("closed_price", 0) or 0) / 100.0,
            "exchange_timestamp": message.get("exchange_timestamp"),
        })
        # Persist + push throttled
        state.store.persist()
        state.store.push_to_redis()
    except Exception as e:
        logger.warning(f"on_data error: {e}")


def on_open(wsapp):
    logger.info("WebSocket opened; subscribing...")
    subscribe_current()


def on_error(wsapp, error):
    logger.warning(f"WebSocket error: {error}")


def on_close(wsapp):
    logger.info("WebSocket closed")


def subscribe_current():
    """Compute current subscription list (indices + sectors + open positions) and subscribe."""
    if state.ws is None:
        return
    try:
        pf = PaperPortfolio()
        held = pf.get_open_symbols()
    except Exception:
        held = []
    subs = build_subscription_list(held)
    if not subs:
        return
    tokens_now = {t for batch in subs for t in batch["tokens"]}
    new_tokens = tokens_now - state.subscribed_tokens
    dropped = state.subscribed_tokens - tokens_now
    if new_tokens or dropped:
        logger.info(f"Subscribing {len(tokens_now)} tokens (new={len(new_tokens)}, dropped={len(dropped)})")
        try:
            # mode 1 = LTP only (cheapest stream)
            state.ws.subscribe(correlation_id="artha", mode=1, token_list=subs)
            state.subscribed_tokens = tokens_now
        except Exception as e:
            logger.warning(f"Subscribe failed: {e}")


def subscription_refresher():
    """Every 60s, re-check held positions and add/drop subscriptions."""
    while True:
        time.sleep(60)
        if is_market_hours() and state.ws is not None:
            subscribe_current()


def main_loop():
    logger.info("Artha WS streamer starting")
    refresher = threading.Thread(target=subscription_refresher, daemon=True)
    refresher.start()

    while True:
        if not is_market_hours():
            logger.info("Outside market hours; sleeping 5 min")
            # Close socket if open
            if state.ws is not None:
                try:
                    state.ws.close_connection()
                except Exception:
                    pass
                state.ws = None
                state.subscribed_tokens.clear()
            time.sleep(300)
            continue

        if not _ensure_login():
            logger.error("Login failed; retrying in 60s")
            time.sleep(60)
            continue

        # Wire callbacks
        state.ws.on_open = on_open
        state.ws.on_data = on_data
        state.ws.on_error = on_error
        state.ws.on_close = on_close

        try:
            logger.info("Connecting WebSocket...")
            state.ws.connect()   # blocks until disconnect
        except Exception as e:
            logger.warning(f"WebSocket loop exited: {e}")

        logger.info("Connection dropped; reconnecting in 10s")
        time.sleep(10)


if __name__ == "__main__":
    main_loop()

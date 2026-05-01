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
from datetime import datetime, timedelta
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")

from logzero import logger, logfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from SmartApi.smartWebSocketV2 import SmartWebSocketV2
from data_fetcher import get_fetcher, SYMBOL_TOKENS
from streaming.tick_store import TickStore
from streaming.paper_marker import get_marker
from paper.portfolio import PaperPortfolio
from config import ANGEL_CREDENTIALS
from common.market_hours import is_market_hours


LOG_DIR = Path(__file__).resolve().parent.parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logfile(str(LOG_DIR / "ws.log"), maxBytes=5_000_000, backupCount=3)

# Static symbols to always subscribe to
STATIC_INDICES = ["NIFTY", "BANKNIFTY", "INDIAVIX"]
STATIC_SECTORS = [s for s in SYMBOL_TOKENS if s.startswith("NIFTY_")]


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
    """Prefer canonical short names over sector-prefixed aliases when tokens collide."""
    priority = {"BANKNIFTY": 10, "NIFTY": 10, "INDIAVIX": 10}
    mapping: dict[str, str] = {}
    for sym, tok in SYMBOL_TOKENS.items():
        if tok in mapping:
            if priority.get(sym, 0) > priority.get(mapping[tok], 0):
                mapping[tok] = sym
        else:
            mapping[tok] = sym
    return mapping


class StreamerState:
    def __init__(self):
        self.store = TickStore()
        self.tok2sym = token_to_symbol()
        self.ws: SmartWebSocketV2 | None = None
        self.subscribed_tokens: set[str] = set()
        self.last_login_date = None
        self.last_tick_at: float = 0.0     # epoch seconds of most recent tick received


state = StreamerState()


# Silent-stall watchdog: if we haven't received any tick in STALL_THRESHOLD_SEC
# during market hours, force the WebSocket closed so main_loop reconnects.
STALL_THRESHOLD_SEC = 120
WATCHDOG_INTERVAL_SEC = 30


def _stall_watchdog():
    while True:
        time.sleep(WATCHDOG_INTERVAL_SEC)
        if not is_market_hours() or state.ws is None or state.last_tick_at == 0:
            continue
        age = time.time() - state.last_tick_at
        if age > STALL_THRESHOLD_SEC:
            logger.warning(f"STALL DETECTED: no ticks in {age:.0f}s -- forcing WS close to trigger reconnect")
            try:
                state.ws.close_connection()
            except Exception as e:
                logger.warning(f"close_connection failed: {e}")


def _ensure_login():
    """Login if needed; re-login if tokens are a day old. Captures tokens from session response."""
    today = datetime.now().date()
    if state.last_login_date == today and state.ws is not None:
        return True

    from SmartApi import SmartConnect
    import pyotp
    try:
        api = SmartConnect(api_key=ANGEL_CREDENTIALS["api_key"])
        totp = pyotp.TOTP(ANGEL_CREDENTIALS["totp_secret"]).now()
        resp = api.generateSession(
            clientCode=ANGEL_CREDENTIALS["client_id"],
            password=ANGEL_CREDENTIALS["pin"],
            totp=totp,
        )
        if not resp.get("status"):
            logger.error(f"Angel login failed: {resp.get('message')}")
            return False
        d = resp["data"]
        jwt_token = d.get("jwtToken")
        feed_token = d.get("feedToken")
        if not jwt_token or not feed_token:
            logger.error(f"Login returned no tokens: {resp}")
            return False
        logger.info(f"Angel logged in; got jwt + feed tokens")
        state.last_login_date = today
        state.ws = SmartWebSocketV2(
            auth_token=jwt_token,
            api_key=ANGEL_CREDENTIALS["api_key"],
            client_code=ANGEL_CREDENTIALS["client_id"],
            feed_token=feed_token,
        )
        return True
    except Exception as e:
        logger.error(f"Login exception: {e}")
        return False


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
        state.store.persist()
        state.store.push_to_redis()
        state.last_tick_at = time.time()    # watchdog heartbeat
        # Live paper portfolio mark -- refreshes P&L + equity in near real-time
        if ltp > 0:
            try:
                get_marker().update_tick(sym, ltp)
            except Exception as e:
                logger.debug(f"paper_marker update failed: {e}")
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
    """Compute current subscription list and subscribe.

    Subscribes to: static indices/sectors + currently held positions +
    any pending_opens (so we capture their first-post-open ticks at 09:15
    for the next-day-open execution model -- see paper/portfolio.py
    pending_opens table)."""
    if state.ws is None:
        return
    try:
        pf = PaperPortfolio()
        held = pf.get_open_symbols()
        pending = [p["symbol"] for p in pf.get_pending_opens()]
    except Exception:
        held = []
        pending = []
    extras = sorted(set(held) | set(pending))
    subs = build_subscription_list(extras)
    if not subs:
        return
    tokens_now = {t for batch in subs for t in batch["tokens"]}
    new_tokens = tokens_now - state.subscribed_tokens
    dropped = state.subscribed_tokens - tokens_now
    if new_tokens or dropped:
        logger.info(f"Subscribing {len(tokens_now)} tokens (new={len(new_tokens)}, dropped={len(dropped)})")
        try:
            # mode 2 = Quote (LTP + OHLC + prev close + volume) -- needed for % change display
            state.ws.subscribe(correlation_id="artha", mode=2, token_list=subs)
            state.subscribed_tokens = tokens_now
        except Exception as e:
            logger.warning(f"Subscribe failed: {e}")


def subscription_refresher():
    """Every 60s, re-check held positions + refresh paper marker held list."""
    while True:
        time.sleep(60)
        if is_market_hours() and state.ws is not None:
            subscribe_current()
            try:
                get_marker().refresh_held()
            except Exception as e:
                logger.debug(f"paper_marker refresh_held failed: {e}")


REALTIME_REBALANCE_INTERVAL_SEC = 30
PICKER_RERUN_EVERY_N_CYCLES = 10  # 10 * 30s = 5 min picker refresh with live LTPs


def _rerun_picker_with_live_ltps(ltps: dict) -> bool:
    """P19: real-time picker refresh. Re-runs run_momentum_picker with current
    LTPs as intraday_ltps so today's price moves are baked into 12-1m / 6m / 3m
    momentum scores. Persists fresh picks_extended back to stocks.json so the
    intraday_rebalance loop and per-tick trailing logic operate on a CURRENT
    universe rank, not yesterday's postclose snapshot. Preserves any manual
    flags (kill_switch_active, kill_switch_reason) from the file."""
    try:
        from strategy.momentum_picker import run_momentum_picker
        import json as _json
        from pathlib import Path as _P
        picks_path = _P(__file__).resolve().parent.parent.parent / "data" / "stocks.json"
        existing = {}
        if picks_path.exists():
            try:
                existing = _json.loads(picks_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        # Preserve manual overrides (kill switch + reason)
        manual_kill = existing.get("kill_switch_active")
        manual_reason = existing.get("kill_switch_reason")
        out = run_momentum_picker(max_picks=10, intraday_ltps=ltps)
        existing.update(out)
        # Re-apply manual kill switch if it was set externally
        if manual_kill is True and manual_reason and "manual" in str(manual_reason).lower():
            existing["kill_switch_active"] = True
            existing["kill_switch_reason"] = manual_reason
        existing["last_intraday_picker_run"] = datetime.now().isoformat()
        picks_path.write_text(_json.dumps(existing, indent=2, default=str), encoding="utf-8")
        n_picks = len(out.get("picks", []))
        n_ext = len(out.get("picks_extended", []))
        logger.info(f"REALTIME picker re-ran with {len(ltps)} live LTPs: "
                    f"{n_picks} picks + {n_ext} extended")
        return True
    except Exception as e:
        logger.warning(f"realtime picker rerun failed: {e}")
        return False


def realtime_rebalance_loop():
    """Replaces the 15-min mark_to_market scheduled task for intraday rebalance.
    Runs every 30s during market hours:
      - position management (time-exit, trail-as-fallback)
      - intraday strength scan + catalyst injection
      - every N cycles (5 min): full picker re-run with live LTPs (P19)
    """
    cycle = 0
    while True:
        time.sleep(REALTIME_REBALANCE_INTERVAL_SEC)
        if not is_market_hours():
            continue
        cycle += 1
        try:
            # Lazy imports inside loop to avoid import order weirdness at startup
            from paper.portfolio import PaperPortfolio
            from paper.runner import intraday_rebalance
            from paper.position_mgmt import manage_positions
            import json as _json
            from pathlib import Path as _P
            pf = PaperPortfolio()
            # Reuse marker's price cache as primary LTP source (no extra fetches)
            from streaming.paper_marker import get_marker as _gm
            ltps = dict(_gm()._prices)
            # P19: every 5 min, re-run the daily-bar momentum picker with live
            # intraday LTPs so picks reflect today's regime, not yesterday's close.
            if cycle % PICKER_RERUN_EVERY_N_CYCLES == 0:
                _rerun_picker_with_live_ltps(ltps)
            held = pf.get_open_symbols()
            if not held:
                continue
            # Position mgmt (stops/targets are now per-tick in paper_marker;
            # this still handles time-exit + trailing for held names whose
            # ATR proxy doesn't have a target_price).
            manage_positions(pf, ltps)
            # Intraday rebalance using current (now-fresh-every-5min) picker JSON
            picks_path = _P(__file__).resolve().parent.parent.parent / "data" / "stocks.json"
            if picks_path.exists():
                try:
                    picker_out = _json.loads(picks_path.read_text(encoding="utf-8"))
                    intraday_rebalance(pf, picker_out, ltps)
                except Exception as e:
                    logger.debug(f"realtime intraday_rebalance failed: {e}")
        except Exception as e:
            logger.warning(f"realtime_rebalance_loop iteration error: {e}")


def main_loop():
    logger.info("Artha WS streamer starting")
    refresher = threading.Thread(target=subscription_refresher, daemon=True)
    refresher.start()
    watchdog = threading.Thread(target=_stall_watchdog, daemon=True)
    watchdog.start()
    rebalancer = threading.Thread(target=realtime_rebalance_loop, daemon=True)
    rebalancer.start()
    # P20: F&O auto-trader (60s reversal-detection loop)
    try:
        from fno.fno_autotrader import autotrader_loop
        fno_thread = threading.Thread(target=autotrader_loop, daemon=True)
        fno_thread.start()
        logger.info("F&O autotrader thread started")
    except Exception as e:
        logger.warning(f"F&O autotrader failed to start: {e}")

    while True:
        if not is_market_hours():
            # Wake up earlier when market is about to open: poll every 30s
            # in the 09:14-09:15 IST window so we're connected by 09:15:00 and
            # the dashboard never reports "down" during the open. Otherwise the
            # 5-min sleep cycle could leave us sleeping until 09:17, missing
            # the first 2 min of session.
            from common.market_hours import now_ist as _now_ist
            ist = _now_ist()
            mins_to_open = (9 * 60 + 15) - (ist.hour * 60 + ist.minute)
            # If within 1 min of market open AND on a weekday, poll fast
            if 0 <= mins_to_open <= 1 and ist.weekday() < 5:
                logger.info(f"Pre-open window: sleeping 30s (open in {mins_to_open}min)")
                # Close socket if still open
                if state.ws is not None:
                    try:
                        state.ws.close_connection()
                    except Exception:
                        pass
                    state.ws = None
                    state.subscribed_tokens.clear()
                time.sleep(30)
                continue
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

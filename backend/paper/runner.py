"""
Paper trade runner -- orchestrates daily:
  1. Reads the freshest stocks.json produced by generate_analysis.py
  2. Compares picks vs currently-open paper positions
     - symbol in new picks but not open   -> OPEN
     - symbol currently open but not in new picks -> CLOSE (dropped from ranks)
     - kill switch active or regime=BEAR  -> CLOSE ALL
  3. Marks all open positions to market using live Angel LTPs
  4. Exports paper_portfolio.json for the dashboard

Called automatically at the end of generate_analysis.py; can also be invoked
standalone.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
from data_fetcher import get_fetcher
from adaptive.targets import compute_status


STOCKS_JSON = Path(__file__).resolve().parent.parent.parent / "data" / "stocks.json"


def _load_picks_json() -> dict:
    if not STOCKS_JSON.exists():
        return {}
    try:
        return json.loads(STOCKS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"Cannot read stocks.json: {e}")
        return {}


def _live_prices(symbols: list[str]) -> dict[str, float]:
    if not symbols:
        return {}
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()
    prices = {}
    for sym in symbols:
        try:
            data = fetcher.get_ltp(sym)
            ltp = float(data.get("ltp", 0))
            if ltp > 0:
                prices[sym] = ltp
        except Exception as e:
            logger.warning(f"LTP fetch failed for {sym}: {e}")
    return prices


def run_paper_runner() -> dict:
    """Main entry point. Returns the exported snapshot."""
    logger.info("Paper runner: starting daily reconciliation")
    pf = PaperPortfolio()

    picks_file = _load_picks_json()
    new_picks = picks_file.get("picks", []) or []
    regime = picks_file.get("regime", "UNKNOWN")
    variant_chosen = picks_file.get("variant", "")
    deploy_pct = float(picks_file.get("deploy_pct", 0)) / 100.0 if picks_file.get("deploy_pct") else 0
    kill_switch = bool(picks_file.get("kill_switch_active", False))

    open_positions = pf.get_open_positions()
    open_syms = set(open_positions.keys())
    new_syms = {p["symbol"] for p in new_picks}

    # Fetch live LTPs for everyone we care about (open + new)
    all_syms = sorted(open_syms | new_syms)
    prices = _live_prices(all_syms)

    # ----- Close: positions no longer in picks, kill switch, or BEAR regime
    force_all_close = kill_switch or regime == "BEAR" or deploy_pct == 0
    closed = []
    for sym in list(open_syms):
        pos = open_positions[sym]
        current_price = prices.get(sym, pos.entry_price)
        if force_all_close:
            reason = "kill switch" if kill_switch else ("regime=BEAR" if regime == "BEAR" else "deploy=0")
            result = pf.close_position(sym, current_price, reason)
            if result:
                closed.append(result)
        elif sym not in new_syms:
            result = pf.close_position(sym, current_price, "dropped from picks")
            if result:
                closed.append(result)

    # ----- Open: new picks not yet held (only if we're actually deploying)
    opened = []
    if not force_all_close and new_picks:
        # Current equity determines slot notional
        current_equity = pf.current_equity(prices)
        n_slots = len(new_picks) if new_picks else 1
        # Size per the picks list (the engine already applied deploy_pct to pick count)
        slot_notional = current_equity / n_slots if n_slots > 0 else current_equity
        for pick in new_picks:
            sym = pick["symbol"]
            if sym in open_syms:
                continue
            entry_price = float(pick.get("cmp", prices.get(sym, 0)))
            if entry_price <= 0:
                continue
            stop = float(pick.get("stop_loss", entry_price * 0.85))
            pos = pf.open_position(
                symbol=sym,
                variant=pick.get("variant") or variant_chosen,
                regime=regime,
                entry_price=entry_price,
                slot_notional=slot_notional,
                stop=stop,
            )
            opened.append({"symbol": sym, "entry": entry_price, "qty": pos.qty, "notional": slot_notional})

    # ----- Mark remaining positions to market
    marks = pf.mark_to_market(prices)

    # ----- Export for dashboard
    snap = pf.export_snapshot(prices)

    # ----- Compute target status + escalation level (feeds next run's engine)
    try:
        target_status = compute_status(STARTING_CAPITAL, snap.get("equity_curve", []))
        snap["target_status"] = target_status
        # Re-export with targets included
        import json as _json
        from pathlib import Path as _P
        _P(__file__).resolve().parent.parent.parent
        ep = _P(__file__).resolve().parent.parent.parent / "data" / "paper_portfolio.json"
        ep.write_text(_json.dumps(snap, indent=2, default=str), encoding="utf-8")
        logger.info(
            f"Target: monthly {target_status['monthly']['actual_pct']:+.2f}% "
            f"(need +{target_status['monthly']['target_pct']}%), "
            f"escalation L{target_status['escalation_level']}"
        )
    except Exception as e:
        logger.warning(f"Target computation failed: {e}")

    logger.info(
        f"Paper runner done: opened={len(opened)}, closed={len(closed)}, "
        f"marks={marks}, open_now={snap['open_positions_count']}, "
        f"equity=₹{snap['current_equity']:,.0f}, pnl={snap['total_pnl_pct']:+.2f}%"
    )
    return snap


if __name__ == "__main__":
    run_paper_runner()

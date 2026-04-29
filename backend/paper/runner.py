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
from datetime import datetime, timedelta
from pathlib import Path

from logzero import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
from data_fetcher import get_fetcher
from adaptive.targets import compute_status
from common.market_hours import is_market_hours, now_ist


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


def _next_market_open_iso() -> str:
    """ISO timestamp of the next 09:15 IST market open from now."""
    ist = now_ist()
    target = ist.replace(hour=9, minute=15, second=0, microsecond=0)
    if target <= ist:
        target = target + timedelta(days=1)
    while target.weekday() >= 5:
        target = target + timedelta(days=1)
    return target.isoformat()


def run_paper_runner() -> dict:
    """Main entry point. Returns the exported snapshot.

    Market-hours behaviour (Option C — next-day-open execution):
      - During market hours: opens fill at current LTP, closes happen at LTP.
      - Outside market hours: new picks are queued as pending_open for next
        09:15 IST fill. Closes are skipped with a warning (the next in-hours
        run will pick them up). The 09:15+ MarkToMarket task fills pendings.
    """
    logger.info("Paper runner: starting daily reconciliation")
    pf = PaperPortfolio()
    market_open = is_market_hours()
    if not market_open:
        logger.info(
            "Market closed -- opens will be queued as pending_open for next 09:15 IST; "
            "closes deferred to next in-hours run."
        )

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
    deferred_closes = []
    for sym in list(open_syms):
        pos = open_positions[sym]
        current_price = prices.get(sym, pos.entry_price)
        should_close = force_all_close or (sym not in new_syms)
        if not should_close:
            continue
        reason = (
            "kill switch" if kill_switch else
            "regime=BEAR" if regime == "BEAR" else
            "deploy=0"   if deploy_pct == 0 else
            "dropped from picks"
        )
        if not market_open:
            deferred_closes.append((sym, reason))
            continue
        result = pf.close_position(sym, current_price, reason)
        if result:
            closed.append(result)
    if deferred_closes:
        logger.info(f"Deferred {len(deferred_closes)} closes to next in-hours run: "
                    f"{[s for s,_ in deferred_closes]}")

    # ----- Cancel any pending_opens whose symbols are no longer in current picks.
    for pending in pf.get_pending_opens():
        if pending["symbol"] not in new_syms or force_all_close:
            pf.cancel_pending_open(pending["symbol"])
            logger.info(f"Cancelled pending_open {pending['symbol']} (no longer in picks)")

    # ----- Open: new picks not yet held (only if we're actually deploying)
    #
    # Two-pass slot sizing to maximize capital utilization at small portfolio sizes:
    #   Pass 1: filter to picks not already held + not already pending + with valid price
    #   Pass 2: estimate cash deployable per pick by: cash_available / max(1, n_affordable_picks).
    #     A pick is "affordable" if its 1-share price <= cash_available.
    #     Then redivide cash among affordable picks only.
    # Effect: with Rs 10K equity and 10 picks of which 8 are too expensive, we deploy
    # ~96% of cash into the 2 affordable picks (instead of ~16% under naive equal-split).
    opened = []
    queued = []
    if not force_all_close and new_picks:
        current_equity = pf.current_equity(prices)
        next_open_iso = _next_market_open_iso()
        already_pending = {p["symbol"] for p in pf.get_pending_opens()}

        # Cash already locked in existing positions
        held_notional = sum(p.qty * p.entry_price for p in open_positions.values())
        cash_available = max(0.0, current_equity - held_notional)

        # Filter to candidates that aren't already held / pending
        candidates = []
        for pick in new_picks:
            sym = pick["symbol"]
            if sym in open_syms or sym in already_pending:
                continue
            entry_price = float(pick.get("cmp", prices.get(sym, 0)))
            if entry_price <= 0:
                continue
            candidates.append((pick, entry_price))

        # Greedy fill in rank order:
        #   target_slot = current_equity / picker.max_positions  (the "fair" slot size)
        #   for each candidate (ranked):
        #     n_target = max(1, floor(target_slot / price))    // at least 1 share if slot
        #                                                       // size is below 1-share cost
        #     n_max    = floor(remaining_cash / price)         // cap by remaining cash
        #     qty      = min(n_target, n_max)
        #     if qty > 0: open at qty shares; deduct cost from remaining_cash
        #     else: skip
        # This deploys ~95-100% of available cash even when picks are pricier than
        # the fair slot size (it overweights early-rank picks rather than leaving
        # cash idle). At Rs 10K paper this is appropriate; at Rs 1L+ each pick
        # naturally fits the fair slot so no overweighting happens.
        max_positions = max(1, len(new_picks))
        target_slot = current_equity / max_positions
        remaining_cash = cash_available
        for pick, entry_price in candidates:
            sym = pick["symbol"]
            stop = float(pick.get("stop_loss", entry_price * 0.85))
            variant_for_pick = pick.get("variant") or variant_chosen

            if market_open:
                if entry_price > remaining_cash:
                    logger.info(f"Skipped {sym}: 1 share Rs{entry_price:.2f} > remaining cash Rs{remaining_cash:.0f}")
                    continue
                # Compute target qty (at least 1 share if any slot at all)
                n_target = max(1, int(target_slot / entry_price))
                n_max    = int(remaining_cash / entry_price)
                qty      = min(n_target, n_max)
                if qty <= 0:
                    continue
                cost = qty * entry_price
                # Reuse open_position by passing slot_notional = cost (so its
                # internal int(slot/price) computes the same qty)
                pos = pf.open_position(
                    symbol=sym, variant=variant_for_pick, regime=regime,
                    entry_price=entry_price, slot_notional=cost,
                    stop=stop,
                )
                if pos is None:
                    logger.info(f"Skipped {sym}: open_position rejected qty={qty} at Rs{entry_price:.2f}")
                    continue
                opened.append({"symbol": sym, "entry": entry_price, "qty": pos.qty, "notional": pos.qty * pos.entry_price})
                remaining_cash -= cost
                if remaining_cash < min(p[1] for p in candidates):
                    # No remaining candidate can fit even 1 share
                    break
            else:
                # Off-hours: queue with target_slot; the morning fill will redo the
                # greedy with actual open prices.
                pf.queue_pending_open(
                    symbol=sym, variant=variant_for_pick, regime=regime,
                    intended_entry_price=entry_price,
                    planned_slot_notional=target_slot,
                    stop=stop, intended_fill_at=next_open_iso,
                )
                queued.append({"symbol": sym, "ref_price": entry_price, "fill_at": next_open_iso})

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
        f"Paper runner done: opened={len(opened)}, queued={len(queued)}, "
        f"closed={len(closed)}, deferred_closes={len(deferred_closes)}, "
        f"marks={marks}, open_now={snap['open_positions_count']}, "
        f"equity=Rs.{snap['current_equity']:,.0f}, pnl={snap['total_pnl_pct']:+.2f}%"
    )
    return snap


if __name__ == "__main__":
    run_paper_runner()

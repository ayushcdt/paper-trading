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


# ---------- Intraday rebalance config ----------
# min-hold-days dropped from 1 to 0 on 2026-04-29: real traders cut same-day
# when signal proves wrong.
# max-swaps-per-day cap REMOVED on 2026-04-29: original 0.4%/swap friction
# math was wrong (that's delivery roundtrip; intraday swap is ~0.1%). The
# 8-point strength-gap guard already filters whipsaw, so the swap cap was
# a redundant arbitrary brake. If signal noise becomes a problem, tighten
# INTRADAY_STRENGTH_GAP_REQUIRED from 8 -> 10 instead.
INTRADAY_MIN_HOLD_DAYS = 0
INTRADAY_SKIP_LAST_MINUTES = 30     # don't swap in last 30min of session (avoid bad fills)
# Intraday-strength-based swap thresholds:
INTRADAY_CANDIDATE_MIN_STRENGTH = 8.0    # candidate must be clearly strong (composite > +8)
INTRADAY_STRENGTH_GAP_REQUIRED = 8.0     # candidate must beat weakest held by this many composite points

# P10 Concentration mode (2026-04-29):
# When ANY held position rallies past CONCENTRATE_LEADER_THRESHOLD_PCT intraday,
# rotate cash from FLAT/RED positions (composite_strength < CONCENTRATE_LAGGARD_MAX_STRENGTH)
# into the leader. Cap: leader can hold max CONCENTRATE_MAX_PORTFOLIO_PCT of equity.
# Mechanism: today's +5.7% intraday peak happened because held names had momentum.
# When that happens, the system should DOUBLE DOWN on the winner, not
# diversify into laggards. Mandates the per-tick trailing stop is armed
# (P6 already does this) so concentration risk is bounded.
CONCENTRATE_LEADER_THRESHOLD_PCT = 3.0   # leader must be up at least this much intraday
CONCENTRATE_LAGGARD_MAX_STRENGTH = 3.0   # only cut laggards weaker than this composite
CONCENTRATE_MAX_PORTFOLIO_PCT = 50.0     # leader caps at this % of equity (avoid all-in)


def _today_swap_count(pf: PaperPortfolio) -> int:
    """Count CLOSE actions today whose reason indicates a swap/rebalance (not stop/target)."""
    today = now_ist().strftime("%Y-%m-%d")
    import sqlite3
    with sqlite3.connect(pf.db_path) as c:
        rows = c.execute(
            "SELECT reason FROM trade_log WHERE action='CLOSE' AND date=? "
            "AND (reason LIKE '%swap%' OR reason LIKE '%rebalance%')",
            (today,),
        ).fetchall()
    return len(rows)


def intraday_rebalance(pf: PaperPortfolio, picker_out: dict, latest_prices: dict[str, float]) -> dict:
    """Intraday opportunity-cost rebalance. Called every 15min during market hours.

    Logic:
      1. Compare current picks vs held positions.
      2. Identify held positions not in latest picks (drop candidates).
      3. Filter drops by min-holding-period (1 day) and max-swaps-per-day (2).
      4. Close eligible drops at LTP.
      5. Use freed cash + existing free cash to open candidates not yet held,
         with greedy fill semantics (same as paper.runner main loop).
      6. Skip everything if within last 30 min of session (avoid end-of-day fills).

    Returns: {closed: [...], opened: [...], skipped_reasons: [...]}
    """
    out = {"closed": [], "opened": [], "skipped_reasons": []}
    ist = now_ist()
    if not is_market_hours():
        out["skipped_reasons"].append("market closed")
        return out
    # Skip last 30 minutes
    minutes_to_close = (15 * 60 + 30) - (ist.hour * 60 + ist.minute)
    if minutes_to_close <= INTRADAY_SKIP_LAST_MINUTES:
        out["skipped_reasons"].append(f"within last {INTRADAY_SKIP_LAST_MINUTES}min of session")
        return out

    # Risk overlay halts apply
    if picker_out.get("kill_switch_active"):
        out["skipped_reasons"].append(f"kill switch active: {picker_out.get('kill_switch_reason')}")
        return out

    new_picks = picker_out.get("picks", []) or []
    new_pick_syms = {p["symbol"] for p in new_picks}
    extended = picker_out.get("picks_extended") or []
    hold_pick_syms = {p["symbol"] for p in extended} if extended else new_pick_syms
    open_positions = pf.get_open_positions()
    held_syms = set(open_positions.keys())

    # Drop candidates: held positions not in the wider hold universe.
    # EXCLUDE intraday_strength + catalyst variants -- those positions have
    # their own exit rules (tight stops/targets, news-tail) and should NOT
    # be churned out just because they fell out of the daily-bar picker. This
    # was the bug that closed manually-opened EOD-chase positions every 15
    # min and the catalyst injections within minutes of opening.
    SELF_MANAGED_VARIANTS = {"intraday_strength", "catalyst"}
    drop_candidates = {
        sym for sym in (held_syms - hold_pick_syms)
        if open_positions[sym].variant not in SELF_MANAGED_VARIANTS
    }

    # Filter by min holding period
    today = ist.date()
    eligible_drops = []
    for sym in drop_candidates:
        pos = open_positions[sym]
        try:
            entry_date = datetime.fromisoformat(pos.entry_date).date()
        except Exception:
            continue
        days_held = (today - entry_date).days
        if days_held < INTRADAY_MIN_HOLD_DAYS:
            out["skipped_reasons"].append(f"{sym}: held only {days_held}d, min {INTRADAY_MIN_HOLD_DAYS}d")
            continue
        eligible_drops.append(sym)

    # No daily swap cap -- strength-gap guard (8+ composite points) handles whipsaw.
    today_swaps = _today_swap_count(pf)  # kept for telemetry/log only
    swap_budget = len(eligible_drops)

    # Execute drops first to free cash
    for sym in eligible_drops:
        price = latest_prices.get(sym) or open_positions[sym].entry_price
        result = pf.close_position(sym, price, "intraday swap (dropped from picks)")
        if result:
            out["closed"].append({"symbol": sym, "price": price, "pnl_inr": result.get("pnl_inr", 0)})
            logger.info(f"INTRADAY SWAP_OUT {sym} @ Rs{price:.2f}  pnl Rs{result.get('pnl_inr', 0):+.0f}")
            try:
                from alerts.channels import dispatch
                dispatch("info", f"SWAP_OUT {sym}",
                         f"Closed at Rs{price:.2f}, P&L Rs{result.get('pnl_inr', 0):+.0f}\nReason: dropped from picks (intraday)")
            except Exception:
                pass

    # SECOND PATH: intraday-strength swap.
    # Swap a held position for a non-held candidate based on intraday momentum
    # signals (today's gap + move + breakout), independent of daily-bar picks.
    # This fires even when picks haven't changed -- captures stocks moving NOW.
    if swap_budget - len(out["closed"]) > 0:
        try:
            from strategy.intraday_signals import rank_intraday
            from data_fetcher import SYMBOL_TOKENS
            # Fetch LTPs for held + a sample of universe (top-100 by recent activity)
            f = get_fetcher()
            if not f.logged_in:
                f.login()
            # Was [:100] -- missed BANDHANBNK at index 114 today during a
            # +13% intraday move. Bumped to 250 to cover the whole liquid
            # universe (currently 512 tokens). Each LTP fetch is ~50ms so
            # 250 fetches take ~12s -- acceptable for sub-minute scan loop.
            sample_universe = list(set(SYMBOL_TOKENS.keys()) - held_syms)[:250]
            sample_ltps = dict(latest_prices)
            for sym in sample_universe:
                if sym in sample_ltps:
                    continue
                try:
                    ltp = float(f.get_ltp(sym).get("ltp", 0))
                    if ltp > 0:
                        sample_ltps[sym] = ltp
                except Exception:
                    pass
            held_after = pf.get_open_positions()
            held_features = rank_intraday(list(held_after.keys()), sample_ltps)
            cand_features = rank_intraday(sample_universe, sample_ltps)
            # Identify weakest held + strongest candidate
            weakest_held = held_features[-1] if held_features else None
            strongest_cand = cand_features[0] if cand_features else None
            if (weakest_held and strongest_cand
                and strongest_cand.composite_strength >= INTRADAY_CANDIDATE_MIN_STRENGTH
                and (strongest_cand.composite_strength - weakest_held.composite_strength) >= INTRADAY_STRENGTH_GAP_REQUIRED):
                # Apply min-holding-period guard
                pos = held_after[weakest_held.symbol]
                try:
                    entry_date = datetime.fromisoformat(pos.entry_date).date()
                except Exception:
                    entry_date = today
                if (today - entry_date).days >= INTRADAY_MIN_HOLD_DAYS:
                    # Execute the strength swap
                    weak_price = sample_ltps.get(weakest_held.symbol, pos.entry_price)
                    strong_price = sample_ltps.get(strongest_cand.symbol)
                    if strong_price and strong_price > 0:
                        result = pf.close_position(weakest_held.symbol, weak_price,
                            f"intraday strength swap (held {weakest_held.composite_strength:+.1f}, replaced by {strongest_cand.symbol} {strongest_cand.composite_strength:+.1f})")
                        if result:
                            out["closed"].append({"symbol": weakest_held.symbol, "price": weak_price, "pnl_inr": result.get("pnl_inr", 0)})
                            logger.info(f"INTRADAY STRENGTH SWAP_OUT {weakest_held.symbol} (strength {weakest_held.composite_strength:+.1f}) -> intend SWAP_IN {strongest_cand.symbol} (strength {strongest_cand.composite_strength:+.1f})")
                            # Open the strong candidate using greedy fill
                            current_equity_now = pf.current_equity(sample_ltps)
                            held_now = pf.get_open_positions()
                            held_notional_now = sum(p.qty * p.entry_price for p in held_now.values())
                            cash_now = max(0.0, current_equity_now - held_notional_now)
                            target_slot = current_equity_now / 10
                            qty = min(max(1, int(target_slot / strong_price)), int(cash_now / strong_price))
                            if qty > 0:
                                cost = qty * strong_price
                                stop = strong_price * 0.97  # tight 3% stop on intraday strength entries
                                # intraday_strength target: very tight (~2.25% above) since
                                # the signal is intraday-only and the move is already in progress
                                target = strong_price * 1.0225
                                pos_new = pf.open_position(
                                    symbol=strongest_cand.symbol, variant="intraday_strength",
                                    regime="INTRADAY", entry_price=strong_price,
                                    slot_notional=cost, stop=stop, target=target,
                                    reason=f"intraday strength swap (composite +{strongest_cand.composite_strength:.1f}, breakout={strongest_cand.breakout_20d}, total {strongest_cand.total_pct:+.2f}%)")
                                if pos_new:
                                    out["opened"].append({"symbol": strongest_cand.symbol, "price": strong_price, "qty": qty, "notional": cost})
                                    logger.info(f"INTRADAY STRENGTH SWAP_IN {strongest_cand.symbol} qty={qty} @ Rs{strong_price:.2f}")
        except Exception as e:
            import traceback
            logger.warning(f"intraday strength swap failed: {e}\n{traceback.format_exc()[:300]}")

    if not out["closed"]:
        # No drops -> nothing to do; openings only happen if we freed up cash
        return out

    # Re-read state after closes
    open_positions = pf.get_open_positions()
    held_syms = set(open_positions.keys())
    held_notional = sum(p.qty * p.entry_price for p in open_positions.values())
    current_equity = pf.current_equity(latest_prices)
    cash_available = max(0.0, current_equity - held_notional)

    # Open candidates: picks not yet held (greedy fill same as main loop).
    # CRITICAL: pick["cmp"] is the entry_ref captured when the picker JSON was
    # written (often hours earlier at postclose). For an intraday rebalance
    # firing at 11:30, that price is stale -- use a fresh LTP. Falls back to
    # cmp only if live fetch fails.
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()
    add_candidates = []
    for pick in new_picks:
        sym = pick["symbol"]
        if sym in held_syms:
            continue
        live_ltp = latest_prices.get(sym, 0.0)
        if live_ltp <= 0:
            try:
                live_ltp = float(fetcher.get_ltp(sym).get("ltp", 0))
            except Exception:
                live_ltp = 0.0
        entry_price = live_ltp if live_ltp > 0 else float(pick.get("cmp", 0))
        if entry_price <= 0:
            continue
        add_candidates.append((pick, entry_price))

    target_slot = current_equity / max(1, len(new_picks))
    remaining_cash = cash_available
    variant_chosen = picker_out.get("variant", "")
    regime = picker_out.get("regime", "")
    for pick, entry_price in add_candidates:
        sym = pick["symbol"]
        if entry_price > remaining_cash:
            continue
        n_target = max(1, int(target_slot / entry_price))
        n_max = int(remaining_cash / entry_price)
        qty = min(n_target, n_max)
        if qty <= 0:
            continue
        cost = qty * entry_price
        stop = float(pick.get("stop_loss", entry_price * 0.85))
        pos = pf.open_position(
            symbol=sym, variant=pick.get("variant") or variant_chosen,
            regime=regime, entry_price=entry_price, slot_notional=cost,
            stop=stop, reason="intraday swap (new pick)",
            target=float(pick.get("target", 0)) or None,
        )
        if pos is None:
            continue
        out["opened"].append({"symbol": sym, "price": entry_price, "qty": qty, "notional": cost})
        logger.info(f"INTRADAY SWAP_IN {sym} qty={qty} @ Rs{entry_price:.2f}  (cost Rs{cost:.0f})")
        remaining_cash -= cost

    # P10 CONCENTRATION MODE: if any held position is leading by >= 3% intraday,
    # cut flat laggards and route freed cash into the leader (cap 50% equity).
    try:
        _concentration_pass(pf, latest_prices, out)
    except Exception as e:
        import traceback
        logger.warning(f"concentration_pass failed: {e}\n{traceback.format_exc()[:300]}")

    return out


def _concentration_pass(pf: PaperPortfolio, latest_prices: dict[str, float], out: dict) -> None:
    """Detect a leader (>= +3% intraday with breakout), cut weak laggards,
    route cash into leader. Caps leader at CONCENTRATE_MAX_PORTFOLIO_PCT."""
    open_positions = pf.get_open_positions()
    if len(open_positions) < 2:
        return
    # Get fresh LTPs for held + composite strength
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        if not fetcher.login():
            return
    ltps = dict(latest_prices)
    for sym in open_positions:
        if sym not in ltps:
            try:
                ltps[sym] = float(fetcher.get_ltp(sym).get("ltp", 0))
            except Exception:
                pass
    try:
        from strategy.intraday_signals import compute_intraday_features
    except Exception:
        return

    # Identify leader: position with highest pnl_pct intraday AND breakout
    leader_sym = None
    leader_pnl_pct = 0.0
    leader_pos = None
    for sym, pos in open_positions.items():
        ltp = ltps.get(sym)
        if not ltp or pos.entry_price <= 0:
            continue
        pnl_pct = (ltp - pos.entry_price) / pos.entry_price * 100
        if pnl_pct < CONCENTRATE_LEADER_THRESHOLD_PCT:
            continue
        # Confirm breakout via intraday features
        try:
            feat = compute_intraday_features(sym, ltp)
            if feat is None or not feat.breakout_20d:
                continue
        except Exception:
            continue
        if pnl_pct > leader_pnl_pct:
            leader_pnl_pct = pnl_pct
            leader_sym = sym
            leader_pos = pos

    if not leader_sym:
        return

    # Check leader's current notional vs cap
    equity = pf.current_equity(ltps)
    leader_notional = leader_pos.qty * ltps.get(leader_sym, leader_pos.entry_price)
    cap_notional = equity * (CONCENTRATE_MAX_PORTFOLIO_PCT / 100.0)
    if leader_notional >= cap_notional:
        logger.debug(f"concentration: {leader_sym} already at cap ({leader_notional:.0f}/{cap_notional:.0f})")
        return

    # Identify laggards: weak positions to cut
    laggards: list[str] = []
    for sym, pos in open_positions.items():
        if sym == leader_sym:
            continue
        ltp = ltps.get(sym)
        if not ltp:
            continue
        try:
            feat = compute_intraday_features(sym, ltp)
            if feat is None:
                continue
            if feat.composite_strength < CONCENTRATE_LAGGARD_MAX_STRENGTH:
                laggards.append(sym)
        except Exception:
            continue

    if not laggards:
        return

    # Cut weakest first, then add to leader
    cash_freed = 0.0
    for sym in laggards:
        ltp = ltps.get(sym, 0)
        if ltp <= 0:
            continue
        result = pf.close_position(sym, ltp, f"concentration: route cash to leader {leader_sym}")
        if result:
            cash_freed += ltp * open_positions[sym].qty
            out["closed"].append({"symbol": sym, "price": ltp, "pnl_inr": result.get("pnl_inr", 0)})
            logger.info(f"CONCENTRATION CUT {sym} @ Rs{ltp:.2f}  pnl Rs{result.get('pnl_inr', 0):+.0f}  "
                        f"(routing to leader {leader_sym} +{leader_pnl_pct:.2f}%)")

    # Add to leader (respecting 50% cap) via weighted-avg add_to_position
    if cash_freed > 0:
        leader_ltp = ltps.get(leader_sym, leader_pos.entry_price)
        room_to_cap = max(0.0, cap_notional - leader_notional)
        usable_cash = min(cash_freed, room_to_cap)
        add_qty = int(usable_cash / leader_ltp)
        if add_qty >= 1:
            updated = pf.add_to_position(
                leader_sym, add_price=leader_ltp, add_qty=add_qty,
                reason=f"concentration add (leader +{leader_pnl_pct:.2f}% intraday)",
            )
            if updated:
                out["opened"].append({"symbol": leader_sym, "price": leader_ltp,
                                      "qty": add_qty, "notional": add_qty * leader_ltp})
                logger.info(f"CONCENTRATION ADD {leader_sym} +{add_qty}sh @ Rs{leader_ltp:.2f}  "
                            f"new entry Rs{updated.entry_price:.2f} qty={updated.qty} "
                            f"(freed Rs{cash_freed:.0f})")


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

        # Filter to candidates that aren't already held / pending.
        # During market hours: use fresh LTP (prices dict was fetched live at
        # top of run_paper_runner) so we don't open at stale picker JSON cmp.
        # Off-hours: pick["cmp"] is fine -- it'll get re-priced at next open
        # via the pending_open fill mechanism.
        candidates = []
        for pick in new_picks:
            sym = pick["symbol"]
            if sym in open_syms or sym in already_pending:
                continue
            if market_open:
                entry_price = float(prices.get(sym, 0)) or float(pick.get("cmp", 0))
            else:
                entry_price = float(pick.get("cmp", 0))
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
                    stop=stop, target=float(pick.get("target", 0)) or None,
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

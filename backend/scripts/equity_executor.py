"""
Equity executor for Portfolio 2 / Portfolio 3 — wires the momentum_agg
(or hardened) picker output to actual paper-trade buys/sells in an
isolated PaperPortfolio instance.

Distinct from claude_autotrade.py: that drives the F&O Portfolio 1.
This one drives EQUITY portfolios.

Run daily at 09:30 IST via cron, after generate_analysis has refreshed
picks at 09:00 (so picks_extended is current).

Strategy (MVP, P2):
  - Read top-10 picks from stocks.json (or stocks_p3.json for P3)
  - Equal-weight target: 10% per slot (Rs 10K per stock at Rs 1L capital)
  - Reconcile vs current holdings:
      * drops (in portfolio, not in picks) → close at live LTP
      * new (in picks, not in portfolio) → buy at live LTP
      * holds (in both) → leave alone (no rebalance churn)
  - Stops: use picker's stop_loss if present; fall back to entry × 0.92
  - Cost modelling: PaperPortfolio.open_position handles COST_PCT internally
  - Idempotent within a day: holds stay held; opens guarded by symbol uniqueness

Usage:
  python -m scripts.equity_executor --portfolio p2
  python -m scripts.equity_executor --portfolio p3 --picks-file stocks_p3.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger

from paper.portfolio import PaperPortfolio
from common.market_hours import is_market_hours


PORTFOLIO_CONFIG = {
    "p2": {
        "db_filename": "paper_trades_p2.db",
        "snapshot_filename": "paper_portfolio_p2.json",
        "starting_capital": 100_000,
        "picks_filename": "stocks.json",
        "variant_label": "momentum_agg_p2",
        "telegram_prefix": "P2 EQUITY",
    },
    "p3": {
        "db_filename": "paper_trades_p3.db",
        "snapshot_filename": "paper_portfolio_p3.json",
        "starting_capital": 100_000,
        "picks_filename": "stocks_p3.json",
        "variant_label": "momentum_hardened_p3",
        "telegram_prefix": "P3 HARDENED",
    },
}

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
MAX_POSITIONS = 10
SLOT_PCT = 10.0          # equal-weight slot size as % of equity
DEFAULT_STOP_PCT = -8.0  # if picker didn't provide stop_loss, use -8% fallback


def _load_picks(picks_path: Path) -> tuple[list[dict], dict, list[str]]:
    """Returns (rich_picks, picker_meta, top_n_symbols)."""
    if not picks_path.exists():
        return [], {}, []
    data = json.loads(picks_path.read_text(encoding="utf-8"))
    picks = data.get("picks") or []
    extended = data.get("picks_extended") or []
    # Order from picks_extended (authoritative ranking), enrich from picks
    by_sym = {p.get("symbol"): p for p in picks}
    ordered_syms = []
    for ext in extended[:MAX_POSITIONS]:
        sym = ext.get("symbol")
        if sym:
            ordered_syms.append(sym)
    if not ordered_syms:
        # fall back: use picks list order
        ordered_syms = [p.get("symbol") for p in picks[:MAX_POSITIONS] if p.get("symbol")]
    rich_subset = [by_sym.get(s) or {"symbol": s} for s in ordered_syms]
    return rich_subset, data, ordered_syms


def _picker_halted(picker_meta: dict) -> tuple[bool, str]:
    if picker_meta.get("kill_switch_active"):
        return True, picker_meta.get("kill_switch_reason") or "kill_switch"
    overlay = picker_meta.get("risk_overlay") or {}
    if overlay.get("tail_halt"):
        return True, "TAIL_HALT (manual reset required)"
    if overlay.get("halt_active"):
        return True, "DD halt active"
    return False, ""


def _live_ltp(fetcher, symbol: str) -> float:
    try:
        d = fetcher.get_ltp(symbol)
        return float(d.get("ltp", 0))
    except Exception as e:
        logger.warning(f"LTP fetch failed for {symbol}: {e}")
        return 0.0


def reconcile(portfolio: str, picks_filename: str | None = None) -> dict:
    """Single execution cycle. Returns a summary dict for logging / Telegram."""
    cfg = PORTFOLIO_CONFIG[portfolio]
    picks_path = DATA_DIR / (picks_filename or cfg["picks_filename"])

    pf = PaperPortfolio(
        db_path=DATA_DIR / cfg["db_filename"],
        snapshot_path=DATA_DIR / cfg["snapshot_filename"],
        starting_capital=cfg["starting_capital"],
    )

    summary = {"portfolio": portfolio, "opens": [], "closes": [], "holds": [],
               "skipped": [], "halt_reason": None}

    rich_picks, picker_meta, target_syms = _load_picks(picks_path)
    if not target_syms:
        logger.warning(f"[{portfolio}] no picks in {picks_path.name}; nothing to do")
        summary["halt_reason"] = "no_picks"
        return summary

    halted, halt_reason = _picker_halted(picker_meta)
    if halted:
        logger.info(f"[{portfolio}] picker halted ({halt_reason}); no opens. Will still process drops/exits.")
        summary["halt_reason"] = halt_reason

    regime = picker_meta.get("regime") or "MOMENTUM_BASE"

    from data_fetcher import get_fetcher
    fetcher = get_fetcher()
    if not fetcher.logged_in:
        fetcher.login()

    held = pf.get_open_positions()
    held_syms = set(held.keys())
    target_set = set(target_syms)

    # Compute equity for slot sizing using live LTPs of held positions
    held_ltps = {s: _live_ltp(fetcher, s) for s in held_syms}
    equity = pf.current_equity(held_ltps)
    slot_notional = equity * (SLOT_PCT / 100.0)
    logger.info(f"[{portfolio}] equity Rs {equity:,.0f} | slot Rs {slot_notional:,.0f} "
                f"| held {len(held_syms)} | target {len(target_syms)} | regime {regime}")

    # 1) Drops — close anything held that isn't in today's picks
    for sym in sorted(held_syms - target_set):
        ltp = _live_ltp(fetcher, sym)
        if ltp <= 0:
            logger.warning(f"[{portfolio}] DROP {sym}: no LTP, skipping close")
            summary["skipped"].append({"symbol": sym, "reason": "no_ltp_for_drop"})
            continue
        closed = pf.close_position(sym, ltp, reason=f"{portfolio} drop from picks")
        if closed:
            logger.info(f"[{portfolio}] CLOSED {sym} @ Rs {ltp:.2f}  pnl={closed.get('pnl_inr', 0):+.0f}")
            summary["closes"].append({"symbol": sym, "exit": ltp,
                                     "pnl_inr": closed.get("pnl_inr", 0)})

    # 2) Opens — new picks not yet held
    if not halted:
        rich_by_sym = {p.get("symbol"): p for p in rich_picks}
        for sym in target_syms:
            if sym in held_syms:
                summary["holds"].append(sym)
                continue
            ltp = _live_ltp(fetcher, sym)
            if ltp <= 0:
                logger.warning(f"[{portfolio}] OPEN {sym}: no LTP, skipping")
                summary["skipped"].append({"symbol": sym, "reason": "no_ltp_for_open"})
                continue

            rich = rich_by_sym.get(sym) or {}
            stop = float(rich.get("stop_loss") or 0)
            if stop <= 0 or stop >= ltp:
                stop = ltp * (1.0 + DEFAULT_STOP_PCT / 100.0)  # default -8% stop
            target = float(rich.get("target") or 0) or None

            qty_affordable = int(slot_notional // ltp)
            if qty_affordable < 1:
                logger.info(f"[{portfolio}] OPEN {sym}: 1 share Rs {ltp:.2f} > slot "
                            f"Rs {slot_notional:.0f}; skipping (uninvestable at this capital)")
                summary["skipped"].append({"symbol": sym, "reason": "share_price_above_slot"})
                continue

            pos = pf.open_position(
                symbol=sym,
                variant=cfg["variant_label"],
                regime=regime,
                entry_price=ltp,
                slot_notional=qty_affordable * ltp,
                stop=stop,
                target=target,
                reason=f"{portfolio} new pick (rank-driven)",
            )
            if pos:
                logger.info(f"[{portfolio}] OPENED {sym} qty={pos.qty} @ Rs {ltp:.2f}  "
                            f"stop=Rs {stop:.2f}  target=Rs {target or 0:.2f}")
                summary["opens"].append({"symbol": sym, "qty": pos.qty,
                                        "entry": ltp, "stop": stop, "target": target})

    # 3) Export snapshot for dashboard / daily summary consumers
    try:
        held_after = pf.get_open_positions()
        held_ltps_after = {s: _live_ltp(fetcher, s) for s in held_after}
        snap = pf.export_snapshot(held_ltps_after)
        logger.info(f"[{portfolio}] snapshot: equity Rs {snap.get('current_equity', 0):,.0f}  "
                    f"realized Rs {snap.get('realized_pnl', 0):+,.0f}  "
                    f"open {snap.get('open_positions_count', 0)}")
    except Exception as e:
        logger.warning(f"[{portfolio}] snapshot export failed: {e}")

    return summary


def _telegram_summary(portfolio: str, summary: dict) -> None:
    cfg = PORTFOLIO_CONFIG[portfolio]
    prefix = cfg["telegram_prefix"]
    n_open = len(summary.get("opens") or [])
    n_close = len(summary.get("closes") or [])
    n_hold = len(summary.get("holds") or [])
    n_skip = len(summary.get("skipped") or [])
    if not (n_open or n_close):
        # quiet day, no Telegram noise
        return
    lines = [
        f"{prefix} reconcile {datetime.now().strftime('%a %d %b %H:%M IST')}",
        f"opens: {n_open}  closes: {n_close}  holds: {n_hold}  skipped: {n_skip}",
    ]
    for o in (summary.get("opens") or [])[:5]:
        lines.append(f"  + {o['symbol']:<12s} qty={o['qty']} @ Rs {o['entry']:.2f}")
    for c in (summary.get("closes") or [])[:5]:
        lines.append(f"  - {c['symbol']:<12s} exit Rs {c['exit']:.2f}  pnl Rs {c.get('pnl_inr', 0):+.0f}")
    msg = "\n".join(lines)
    try:
        from alerts.channels import dispatch
        dispatch("info", f"{prefix}: {n_open}+ / {n_close}-", msg)
    except Exception as e:
        logger.warning(f"telegram dispatch failed: {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", default="p2", choices=list(PORTFOLIO_CONFIG.keys()))
    parser.add_argument("--picks-file", default=None,
                        help="override picks filename (default: portfolio's default)")
    parser.add_argument("--force", action="store_true",
                        help="run even outside market hours (debug / backfill)")
    args = parser.parse_args()

    if not args.force and not is_market_hours():
        logger.info("Market closed; skip (use --force to override)")
        return

    summary = reconcile(args.portfolio, picks_filename=args.picks_file)
    _telegram_summary(args.portfolio, summary)
    logger.info(f"[{args.portfolio}] reconcile done")


if __name__ == "__main__":
    main()

"""
Daily P&L summary — sends a Telegram message at end of session with:
  - Today's realized + unrealized P&L
  - Top winner / loser
  - Trade count (opens, closes, swaps)
  - Equity vs target progress
  - Risk overlay state

Schedule: 15:35 IST (5 min after market close, before Postclose runs).
Or run manually:
    python -m scripts.daily_summary
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from paper.portfolio import PaperPortfolio, STARTING_CAPITAL
from common.market_hours import now_ist


def main():
    pf = PaperPortfolio()
    today = now_ist().strftime("%Y-%m-%d")

    # Today's trades
    import sqlite3
    with sqlite3.connect(pf.db_path) as c:
        rows = c.execute(
            "SELECT action, symbol, qty, price, pnl_inr, reason FROM trade_log "
            "WHERE date = ? ORDER BY id",
            (today,),
        ).fetchall()

    n_open = sum(1 for r in rows if r[0] == "OPEN")
    n_close = sum(1 for r in rows if r[0] == "CLOSE")
    n_swap = sum(1 for r in rows if r[0] == "CLOSE" and ("swap" in (r[5] or "").lower() or "intraday" in (r[5] or "").lower()))
    n_catalyst = sum(1 for r in rows if r[0] == "OPEN" and "catalyst" in (r[5] or "").lower())
    realized_today = sum((r[4] or 0) for r in rows if r[0] == "CLOSE")

    # Total state
    snap_path = Path(__file__).resolve().parent.parent.parent / "data" / "paper_portfolio.json"
    snap = {}
    if snap_path.exists():
        try:
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    equity = snap.get("current_equity", STARTING_CAPITAL)
    realized_total = snap.get("realized_pnl", 0)
    unrealized = snap.get("unrealized_pnl", 0)
    pct_total = snap.get("total_pnl_pct", 0)
    open_count = snap.get("open_positions_count", 0)

    # Top winner / loser today
    closes_today = [r for r in rows if r[0] == "CLOSE" and r[4] is not None]
    top_win = max(closes_today, key=lambda r: r[4]) if closes_today else None
    top_loss = min(closes_today, key=lambda r: r[4]) if closes_today else None

    target = snap.get("target_status", {}) or {}
    monthly = target.get("monthly", {})

    # Compose message
    lines = [
        f"DAILY SUMMARY {today}",
        "",
        f"Equity:    Rs {equity:,.0f}",
        f"Total PnL: {pct_total:+.2f}%  (Rs {realized_total:+.0f} real + Rs {unrealized:+.0f} unrealized)",
        f"Today PnL: Rs {realized_today:+.0f}",
        "",
        f"Trades today: {n_open} opens, {n_close} closes",
        f"  Of closes: {n_swap} swaps",
        f"  Of opens:  {n_catalyst} catalyst-driven",
        "",
        f"Open positions: {open_count}",
    ]
    if top_win and top_win[4] > 0:
        lines.append(f"Best:  {top_win[1]} +Rs{top_win[4]:.0f}")
    if top_loss and top_loss[4] < 0:
        lines.append(f"Worst: {top_loss[1]} -Rs{abs(top_loss[4]):.0f}")
    if monthly:
        lines.append("")
        lines.append(
            f"Monthly target: {monthly.get('actual_pct', 0):+.2f}% / "
            f"target {monthly.get('target_pct', 0):+.2f}% "
            f"({'on track' if monthly.get('on_track') else 'BEHIND'})"
        )

    body = "\n".join(lines)
    print(body)

    try:
        from alerts.channels import dispatch
        dispatch("info", f"Daily summary {today}", body)
        print("\n  -> Sent to Telegram")
    except Exception as e:
        logger.warning(f"telegram send failed: {e}")


if __name__ == "__main__":
    main()

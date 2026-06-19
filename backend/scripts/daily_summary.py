"""
Daily P&L summary — sends a Telegram message at end of session with:
  - Today's realized + unrealized P&L
  - Top winner / loser
  - Trade count (opens, closes, swaps)
  - Equity vs target progress
  - Risk overlay state

Schedule: 15:35 / 15:36 / 15:37 IST (5+ min after market close), once per
portfolio. Defaults to Portfolio 1 (F&O) for backward compatibility.

Usage:
    python -m scripts.daily_summary                     # P1 (F&O)
    python -m scripts.daily_summary --portfolio p2      # P2 (equity)
    python -m scripts.daily_summary --portfolio p3      # P3 (hardened)
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger
from paper.portfolio import PaperPortfolio, STARTING_CAPITAL, DB_PATH, EXPORT_PATH
from common.market_hours import now_ist


DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"

PORTFOLIO_CONFIG = {
    "p1": {
        # F&O autonomous test. Uses the original (unsuffixed) paths so
        # existing data files keep their location.
        "db_path": DB_PATH,
        "snapshot_path": EXPORT_PATH,
        "starting_capital": STARTING_CAPITAL,
        "header": "P1 F&O",
    },
    "p2": {
        "db_path": DATA_DIR / "paper_trades_p2.db",
        "snapshot_path": DATA_DIR / "paper_portfolio_p2.json",
        "starting_capital": 100_000,
        "header": "P2 EQUITY (momentum_agg)",
    },
    "p3": {
        "db_path": DATA_DIR / "paper_trades_p3.db",
        "snapshot_path": DATA_DIR / "paper_portfolio_p3.json",
        "starting_capital": 100_000,
        "header": "P3 EQUITY (hardened)",
    },
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--portfolio", default="p1", choices=list(PORTFOLIO_CONFIG.keys()))
    args = parser.parse_args()
    cfg = PORTFOLIO_CONFIG[args.portfolio]

    pf = PaperPortfolio(
        db_path=cfg["db_path"],
        snapshot_path=cfg["snapshot_path"],
        starting_capital=cfg["starting_capital"],
    )
    today = now_ist().strftime("%Y-%m-%d")

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

    snap = {}
    if cfg["snapshot_path"].exists():
        try:
            snap = json.loads(cfg["snapshot_path"].read_text(encoding="utf-8"))
        except Exception:
            pass

    equity = snap.get("current_equity", cfg["starting_capital"])
    realized_total = snap.get("realized_pnl", 0)
    unrealized = snap.get("unrealized_pnl", 0)
    pct_total = snap.get("total_pnl_pct", 0)
    open_count = snap.get("open_positions_count", 0)

    closes_today = [r for r in rows if r[0] == "CLOSE" and r[4] is not None]
    top_win = max(closes_today, key=lambda r: r[4]) if closes_today else None
    top_loss = min(closes_today, key=lambda r: r[4]) if closes_today else None

    target = snap.get("target_status", {}) or {}
    monthly = target.get("monthly", {})

    lines = [
        f"[{cfg['header']}] DAILY SUMMARY {today}",
        "",
        f"Equity:    Rs {equity:,.0f}",
        f"Total PnL: {pct_total:+.2f}%  (Rs {realized_total:+.0f} real + Rs {unrealized:+.0f} unrealized)",
        f"Today PnL: Rs {realized_today:+.0f}",
        "",
        f"Trades today: {n_open} opens, {n_close} closes",
    ]
    # F&O-specific counters only meaningful for P1
    if args.portfolio == "p1":
        lines.append(f"  Of closes: {n_swap} swaps")
        lines.append(f"  Of opens:  {n_catalyst} catalyst-driven")
    lines.append("")
    lines.append(f"Open positions: {open_count}")
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
        dispatch("info", f"{cfg['header']} {today}", body)
        print("\n  -> Sent to Telegram")
    except Exception as e:
        logger.warning(f"telegram send failed: {e}")


if __name__ == "__main__":
    main()

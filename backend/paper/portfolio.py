"""
SQLite-backed paper portfolio. Every V3 pick becomes a virtual position;
exits happen when the pick drops out of picks or the variant's exit rule
triggers. Live Angel prices drive mark-to-market.

Schema:
  positions    -- currently open virtual positions
  trade_log    -- every OPEN and CLOSE ever
  daily_marks  -- daily close prices of open positions (for equity curve)
  config       -- starting capital, schema version
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional


DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "paper_trades.db"
EXPORT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "paper_portfolio.json"

STARTING_CAPITAL = 10_000     # ₹10K -- realistic retail starting point
COST_PCT = 0.4                # round-trip cost estimate


@dataclass
class Position:
    symbol: str
    variant: str
    regime_at_entry: str
    entry_price: float
    qty: int
    slot_notional: float
    stop_at_entry: float
    entry_date: str


class PaperPortfolio:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _conn(self):
        return sqlite3.connect(self.db_path)

    def _init(self):
        with self._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    variant TEXT NOT NULL,
                    regime_at_entry TEXT,
                    entry_price REAL NOT NULL,
                    qty INTEGER NOT NULL,
                    slot_notional REAL NOT NULL,
                    stop_at_entry REAL,
                    entry_date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trade_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    variant TEXT NOT NULL,
                    regime TEXT,
                    action TEXT NOT NULL,      -- OPEN, CLOSE
                    price REAL NOT NULL,
                    qty INTEGER NOT NULL,
                    pnl_inr REAL,
                    pnl_pct REAL,
                    reason TEXT,
                    timestamp TEXT NOT NULL,
                    date TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS daily_marks (
                    date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    unrealized_pnl_pct REAL,
                    PRIMARY KEY (date, symbol)
                );

                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_trade_variant_date
                    ON trade_log(variant, date);
            """)
            c.execute("INSERT OR IGNORE INTO config VALUES (?, ?)",
                      ("starting_capital", str(STARTING_CAPITAL)))
            c.execute("INSERT OR IGNORE INTO config VALUES (?, ?)",
                      ("started_at", datetime.now().isoformat()))

    # ---------- Queries -----------------------------------------------------

    def get_open_positions(self) -> dict[str, Position]:
        with self._conn() as c:
            rows = c.execute("SELECT * FROM positions").fetchall()
        cols = ["symbol", "variant", "regime_at_entry", "entry_price", "qty",
                "slot_notional", "stop_at_entry", "entry_date"]
        return {r[0]: Position(**dict(zip(cols, r))) for r in rows}

    def get_open_symbols(self) -> list[str]:
        with self._conn() as c:
            rows = c.execute("SELECT symbol FROM positions").fetchall()
        return [r[0] for r in rows]

    def get_realized_pnl_total(self) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(pnl_inr), 0) FROM trade_log WHERE action='CLOSE'"
            ).fetchone()
        return float(row[0]) if row else 0.0

    def get_realized_pnl_since(self, since_iso: str) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(pnl_inr), 0) FROM trade_log "
                "WHERE action='CLOSE' AND timestamp >= ?",
                (since_iso,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    def get_unrealized_pnl(self, latest_prices: dict[str, float]) -> float:
        total = 0.0
        for pos in self.get_open_positions().values():
            price = latest_prices.get(pos.symbol)
            if price is None:
                continue
            total += (price - pos.entry_price) * pos.qty
        return total

    def current_equity(self, latest_prices: dict[str, float]) -> float:
        return STARTING_CAPITAL + self.get_realized_pnl_total() + self.get_unrealized_pnl(latest_prices)

    def live_3m_return_by_variant(self) -> dict[str, float]:
        """Variant-level 3-month realized P&L % (of notional) from CLOSE trades."""
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        with self._conn() as c:
            rows = c.execute(
                "SELECT variant, AVG(pnl_pct) FROM trade_log "
                "WHERE action='CLOSE' AND timestamp >= ? GROUP BY variant",
                (cutoff,),
            ).fetchall()
        return {r[0]: float(r[1]) for r in rows if r[0]}

    def trade_log(self, limit: int = 200) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, variant, regime, action, price, qty, pnl_inr, pnl_pct, reason, timestamp "
                "FROM trade_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        cols = ["symbol", "variant", "regime", "action", "price", "qty", "pnl_inr", "pnl_pct", "reason", "timestamp"]
        return [dict(zip(cols, r)) for r in rows]

    # ---------- Mutations ---------------------------------------------------

    def open_position(
        self,
        symbol: str,
        variant: str,
        regime: str,
        entry_price: float,
        slot_notional: float,
        stop: float,
    ) -> Position:
        qty = max(1, int(slot_notional / entry_price)) if entry_price > 0 else 1
        now = datetime.now()
        pos = Position(
            symbol=symbol, variant=variant, regime_at_entry=regime,
            entry_price=entry_price, qty=qty, slot_notional=slot_notional,
            stop_at_entry=stop, entry_date=now.isoformat(),
        )
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO positions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (pos.symbol, pos.variant, pos.regime_at_entry, pos.entry_price,
                 pos.qty, pos.slot_notional, pos.stop_at_entry, pos.entry_date),
            )
            c.execute(
                "INSERT INTO trade_log (symbol, variant, regime, action, price, qty, pnl_inr, pnl_pct, reason, timestamp, date) "
                "VALUES (?, ?, ?, 'OPEN', ?, ?, 0, 0, 'new pick', ?, ?)",
                (symbol, variant, regime, entry_price, qty, now.isoformat(), now.strftime("%Y-%m-%d")),
            )
        return pos

    def close_position(self, symbol: str, exit_price: float, reason: str) -> Optional[dict]:
        open_pos = self.get_open_positions().get(symbol)
        if not open_pos:
            return None
        gross_pnl = (exit_price - open_pos.entry_price) * open_pos.qty
        # Apply round-trip cost on notional
        cost = open_pos.slot_notional * (COST_PCT / 100)
        pnl_inr = gross_pnl - cost
        pnl_pct = (pnl_inr / open_pos.slot_notional) * 100 if open_pos.slot_notional > 0 else 0
        now = datetime.now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO trade_log (symbol, variant, regime, action, price, qty, pnl_inr, pnl_pct, reason, timestamp, date) "
                "VALUES (?, ?, ?, 'CLOSE', ?, ?, ?, ?, ?, ?, ?)",
                (symbol, open_pos.variant, open_pos.regime_at_entry, exit_price,
                 open_pos.qty, pnl_inr, pnl_pct, reason, now.isoformat(), now.strftime("%Y-%m-%d")),
            )
            c.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        return {
            "symbol": symbol, "entry": open_pos.entry_price, "exit": exit_price,
            "pnl_inr": pnl_inr, "pnl_pct": pnl_pct, "reason": reason,
        }

    def mark_to_market(self, latest_prices: dict[str, float]) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        n = 0
        with self._conn() as c:
            for pos in self.get_open_positions().values():
                price = latest_prices.get(pos.symbol)
                if price is None:
                    continue
                unrealized_pct = (price - pos.entry_price) / pos.entry_price * 100
                c.execute(
                    "INSERT OR REPLACE INTO daily_marks VALUES (?, ?, ?, ?)",
                    (today, pos.symbol, price, unrealized_pct),
                )
                n += 1
        return n

    def equity_curve(self, days: int = 90) -> list[dict]:
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        with self._conn() as c:
            # Realized P&L by date
            realized_rows = c.execute(
                "SELECT date, COALESCE(SUM(pnl_inr), 0) FROM trade_log "
                "WHERE action='CLOSE' AND date >= ? GROUP BY date ORDER BY date",
                (cutoff,),
            ).fetchall()
            # Unrealized from daily_marks (sum across symbols per date)
            unrealized_rows = c.execute(
                "SELECT dm.date, SUM((dm.price - p.entry_price) * p.qty) "
                "FROM daily_marks dm "
                "LEFT JOIN positions p ON p.symbol = dm.symbol "
                "WHERE dm.date >= ? GROUP BY dm.date ORDER BY dm.date",
                (cutoff,),
            ).fetchall()
        realized = {d: float(v) for d, v in realized_rows}
        unrealized = {d: float(v or 0) for d, v in unrealized_rows}
        all_dates = sorted(set(realized) | set(unrealized))
        cum_realized = 0.0
        curve = []
        for d in all_dates:
            cum_realized += realized.get(d, 0)
            equity = STARTING_CAPITAL + cum_realized + unrealized.get(d, 0)
            curve.append({"date": d, "equity": round(equity, 0),
                          "realized_cum": round(cum_realized, 0),
                          "unrealized": round(unrealized.get(d, 0), 0)})
        return curve

    # ---------- Export for dashboard ---------------------------------------

    def export_snapshot(self, latest_prices: dict[str, float]):
        open_pos = self.get_open_positions()
        realized = self.get_realized_pnl_total()
        unrealized = self.get_unrealized_pnl(latest_prices)
        equity = self.current_equity(latest_prices)
        starting = STARTING_CAPITAL
        with self._conn() as c:
            row = c.execute("SELECT value FROM config WHERE key='started_at'").fetchone()
        started_at = row[0] if row else None

        snap = {
            "generated_at": datetime.now().isoformat(),
            "started_at": started_at,
            "starting_capital": starting,
            "current_equity": round(equity, 0),
            "realized_pnl": round(realized, 0),
            "unrealized_pnl": round(unrealized, 0),
            "total_pnl_pct": round((equity - starting) / starting * 100, 2),
            "open_positions_count": len(open_pos),
            "open_positions": [
                {
                    "symbol": p.symbol, "variant": p.variant,
                    "regime_at_entry": p.regime_at_entry,
                    "entry_price": p.entry_price, "qty": p.qty,
                    "slot_notional": p.slot_notional,
                    "stop_at_entry": p.stop_at_entry,
                    "entry_date": p.entry_date,
                    "current_price": latest_prices.get(p.symbol, p.entry_price),
                    "unrealized_pnl_inr": round(
                        (latest_prices.get(p.symbol, p.entry_price) - p.entry_price) * p.qty, 2),
                    "unrealized_pnl_pct": round(
                        (latest_prices.get(p.symbol, p.entry_price) - p.entry_price) / p.entry_price * 100, 2),
                }
                for p in open_pos.values()
            ],
            "recent_trades": self.trade_log(limit=50),
            "live_3m_return_by_variant": self.live_3m_return_by_variant(),
            "equity_curve": self.equity_curve(days=90),
        }

        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        return snap

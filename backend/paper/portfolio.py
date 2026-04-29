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
    target_price: float = 0.0   # added 2026-04-29 for stop/target check in mark_to_market
    current_stop: float = 0.0   # trailing stop; raised by position_mgmt as profit grows


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
            """)
            # Additive migrations — idempotent
            for ddl in [
                "ALTER TABLE positions ADD COLUMN target_price REAL DEFAULT 0",
                "ALTER TABLE positions ADD COLUMN current_stop REAL DEFAULT 0",
            ]:
                try:
                    c.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            # trade_log additive migrations for real-money fees breakdown.
            # is_intraday: 1 if open+close on same date, else 0 (delivery)
            # real_fee_inr: total fees for THIS leg (close stores round-trip total)
            for ddl in [
                "ALTER TABLE trade_log ADD COLUMN is_intraday INTEGER DEFAULT 0",
                "ALTER TABLE trade_log ADD COLUMN real_fee_inr REAL DEFAULT 0",
                "ALTER TABLE trade_log ADD COLUMN fee_breakdown_json TEXT",
            ]:
                try:
                    c.execute(ddl)
                except sqlite3.OperationalError:
                    pass
            c.executescript("""

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

                -- Pending opens: picks queued during off-hours, waiting for next
                -- market open to fill at the actual open price (matches AMO mechanics).
                -- See paper/runner.py + scripts/mark_to_market.py for fill logic.
                CREATE TABLE IF NOT EXISTS pending_opens (
                    symbol TEXT PRIMARY KEY,
                    variant TEXT NOT NULL,
                    regime_at_entry TEXT,
                    intended_entry_price REAL NOT NULL,   -- prior close used as gap reference
                    planned_slot_notional REAL NOT NULL,
                    stop_at_entry REAL,
                    queued_at TEXT NOT NULL,
                    intended_fill_at TEXT NOT NULL        -- ISO of next 09:15 IST
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
            rows = c.execute(
                "SELECT symbol, variant, regime_at_entry, entry_price, qty, "
                "slot_notional, stop_at_entry, entry_date, "
                "COALESCE(target_price, 0), COALESCE(current_stop, 0) "
                "FROM positions"
            ).fetchall()
        cols = ["symbol", "variant", "regime_at_entry", "entry_price", "qty",
                "slot_notional", "stop_at_entry", "entry_date",
                "target_price", "current_stop"]
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
        entry_time: Optional[str] = None,
        reason: str = "new pick",
        target: Optional[float] = None,
    ) -> Optional[Position]:
        """Open a new paper position. Returns None if the slot can't afford
        even 1 share at the given entry_price (was previously max(1,...) which
        created fictional leveraged positions — see commit fixing 'A').

        target: explicit target price. If None, falls back to entry × 1.10
        (legacy behaviour). Picker now supplies ATR-based targets per variant
        — see strategy/momentum_picker.py."""
        if entry_price <= 0:
            return None
        qty = int(slot_notional / entry_price)
        if qty <= 0:
            return None
        when_iso = entry_time or datetime.now().isoformat()
        when_date = when_iso[:10]
        target_price = float(target) if target and target > entry_price else entry_price * 1.10
        current_stop = stop  # initial = stop_at_entry; trailing logic raises later
        pos = Position(
            symbol=symbol, variant=variant, regime_at_entry=regime,
            entry_price=entry_price, qty=qty, slot_notional=slot_notional,
            stop_at_entry=stop, entry_date=when_iso,
            target_price=target_price, current_stop=current_stop,
        )
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO positions "
                "(symbol, variant, regime_at_entry, entry_price, qty, slot_notional, "
                " stop_at_entry, entry_date, target_price, current_stop) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pos.symbol, pos.variant, pos.regime_at_entry, pos.entry_price,
                 pos.qty, pos.slot_notional, pos.stop_at_entry, pos.entry_date,
                 pos.target_price, pos.current_stop),
            )
            c.execute(
                "INSERT INTO trade_log (symbol, variant, regime, action, price, qty, pnl_inr, pnl_pct, reason, timestamp, date) "
                "VALUES (?, ?, ?, 'OPEN', ?, ?, 0, 0, ?, ?, ?)",
                (symbol, variant, regime, entry_price, qty, reason, when_iso, when_date),
            )
        return pos

    def update_position_stop_target(self, symbol: str, new_stop: Optional[float] = None,
                                     new_target: Optional[float] = None) -> None:
        """Update trailing stop or target. Caller (mark_to_market) drives this."""
        sets, vals = [], []
        if new_stop is not None:
            sets.append("current_stop = ?")
            vals.append(float(new_stop))
        if new_target is not None:
            sets.append("target_price = ?")
            vals.append(float(new_target))
        if not sets:
            return
        vals.append(symbol)
        with self._conn() as c:
            c.execute(f"UPDATE positions SET {', '.join(sets)} WHERE symbol = ?", vals)

    # ---------- Pending opens (Option C: next-day-open execution) -----------

    def queue_pending_open(
        self,
        symbol: str,
        variant: str,
        regime: str,
        intended_entry_price: float,
        planned_slot_notional: float,
        stop: float,
        intended_fill_at: str,
    ) -> None:
        """Queue a pick for execution at next market open (Option C). Idempotent
        on symbol -- re-queuing updates the existing row."""
        now_iso = datetime.now().isoformat()
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO pending_opens "
                "(symbol, variant, regime_at_entry, intended_entry_price, "
                " planned_slot_notional, stop_at_entry, queued_at, intended_fill_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, variant, regime, float(intended_entry_price),
                 float(planned_slot_notional), float(stop),
                 now_iso, intended_fill_at),
            )

    def get_pending_opens(self) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT symbol, variant, regime_at_entry, intended_entry_price, "
                "planned_slot_notional, stop_at_entry, queued_at, intended_fill_at "
                "FROM pending_opens ORDER BY queued_at"
            ).fetchall()
        cols = ["symbol", "variant", "regime_at_entry", "intended_entry_price",
                "planned_slot_notional", "stop_at_entry", "queued_at", "intended_fill_at"]
        return [dict(zip(cols, r)) for r in rows]

    def cancel_pending_open(self, symbol: str, reason: str = "cancelled") -> None:
        with self._conn() as c:
            c.execute("DELETE FROM pending_opens WHERE symbol = ?", (symbol,))

    def execute_pending(
        self,
        symbol: str,
        fill_price: float,
        fill_time_iso: str,
    ) -> Optional[Position]:
        """Move a pending_open into a real position at the given fill price/time.
        Caller is responsible for gap-guard checks before invoking this."""
        with self._conn() as c:
            row = c.execute(
                "SELECT variant, regime_at_entry, planned_slot_notional, stop_at_entry "
                "FROM pending_opens WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        if not row:
            return None
        variant, regime, slot, stop = row
        pos = self.open_position(
            symbol=symbol, variant=variant, regime=regime or "UNKNOWN",
            entry_price=float(fill_price), slot_notional=float(slot),
            stop=float(stop), entry_time=fill_time_iso,
            reason="filled at next-day open",
        )
        # Always remove the pending row regardless of whether qty was affordable;
        # if pos is None, the pending was effectively rejected (slot too small for 1 share).
        with self._conn() as c:
            c.execute("DELETE FROM pending_opens WHERE symbol = ?", (symbol,))
        return pos

    def close_position(self, symbol: str, exit_price: float, reason: str) -> Optional[dict]:
        open_pos = self.get_open_positions().get(symbol)
        if not open_pos:
            return None
        gross_pnl = (exit_price - open_pos.entry_price) * open_pos.qty
        # Apply round-trip cost on notional (legacy flat 0.4% — kept for backtest parity)
        cost = open_pos.slot_notional * (COST_PCT / 100)
        pnl_inr = gross_pnl - cost
        pnl_pct = (pnl_inr / open_pos.slot_notional) * 100 if open_pos.slot_notional > 0 else 0
        now = datetime.now()

        # Real-money fee breakdown (Indian discount-broker model)
        from common.fees import compute_round_trip_fees
        try:
            entry_date = open_pos.entry_date[:10]
        except Exception:
            entry_date = now.strftime("%Y-%m-%d")
        is_intraday = entry_date == now.strftime("%Y-%m-%d")
        fee_b = compute_round_trip_fees(
            buy_price=open_pos.entry_price, sell_price=exit_price,
            qty=open_pos.qty, is_intraday=is_intraday,
        )
        fee_dict = fee_b.as_dict()
        import json as _json

        with self._conn() as c:
            c.execute(
                "INSERT INTO trade_log (symbol, variant, regime, action, price, qty, pnl_inr, pnl_pct, reason, timestamp, date, is_intraday, real_fee_inr, fee_breakdown_json) "
                "VALUES (?, ?, ?, 'CLOSE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (symbol, open_pos.variant, open_pos.regime_at_entry, exit_price,
                 open_pos.qty, pnl_inr, pnl_pct, reason, now.isoformat(), now.strftime("%Y-%m-%d"),
                 1 if is_intraday else 0, fee_b.total, _json.dumps(fee_dict)),
            )
            c.execute("DELETE FROM positions WHERE symbol = ?", (symbol,))
        return {
            "symbol": symbol, "entry": open_pos.entry_price, "exit": exit_price,
            "pnl_inr": pnl_inr, "pnl_pct": pnl_pct, "reason": reason,
            "real_fee_inr": fee_b.total, "is_intraday": is_intraday,
            "fee_breakdown": fee_dict,
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

    def fees_summary(self) -> dict:
        """Aggregate real-money fees paid across all CLOSE trades.
        Compares to the flat 0.4% cost model the system applies internally
        so dashboard can show real-money headwind explicitly."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT COALESCE(SUM(real_fee_inr), 0), "
                "       COALESCE(SUM(CASE WHEN is_intraday=1 THEN real_fee_inr ELSE 0 END), 0), "
                "       COALESCE(SUM(CASE WHEN is_intraday=0 THEN real_fee_inr ELSE 0 END), 0), "
                "       COUNT(*), "
                "       COALESCE(SUM(CASE WHEN is_intraday=1 THEN 1 ELSE 0 END), 0), "
                "       COALESCE(SUM(price * qty), 0) "
                "FROM trade_log WHERE action='CLOSE'"
            ).fetchone()
            recent = c.execute(
                "SELECT symbol, date, is_intraday, real_fee_inr, fee_breakdown_json, price, qty, pnl_inr "
                "FROM trade_log WHERE action='CLOSE' AND real_fee_inr > 0 "
                "ORDER BY id DESC LIMIT 20"
            ).fetchall()
            # Flat estimate that internal P&L already deducts (COST_PCT on slot_notional)
            flat_row = c.execute(
                "SELECT COALESCE(SUM(slot_notional), 0) FROM positions"
            ).fetchone()
        total_real, intraday_real, delivery_real, n_close, n_intraday, sell_notional = rows
        recent_list = []
        import json as _json
        for r in recent:
            try:
                breakdown = _json.loads(r[4]) if r[4] else {}
            except Exception:
                breakdown = {}
            recent_list.append({
                "symbol": r[0], "date": r[1],
                "is_intraday": bool(r[2]),
                "real_fee_inr": round(float(r[3] or 0), 2),
                "breakdown": breakdown,
                "exit_notional": round(float(r[5] or 0) * int(r[6] or 0), 2),
                "pnl_inr": round(float(r[7] or 0), 2),
            })
        # Reconstruct flat-cost estimate the system already deducted from realized P&L
        with self._conn() as c:
            est_row = c.execute(
                "SELECT COALESCE(SUM(price * qty), 0) FROM trade_log WHERE action='OPEN'"
            ).fetchone()
        buy_notional_total = float(est_row[0]) if est_row else 0.0
        # Note: COST_PCT is applied on slot_notional at close time (== buy notional roughly).
        # But only for closed trades. So flat-estimate cumulative ~= COST_PCT * sum(buy_notional of closed trades).
        # Approximate: count = n_close, but we don't have per-trade slot easily. Use exit-side notional as proxy.
        flat_estimate_total = float(sell_notional) * (COST_PCT / 100)
        return {
            "total_real_fees_inr": round(float(total_real), 2),
            "intraday_fees_inr": round(float(intraday_real), 2),
            "delivery_fees_inr": round(float(delivery_real), 2),
            "n_closes": int(n_close),
            "n_intraday_closes": int(n_intraday),
            "n_delivery_closes": int(n_close) - int(n_intraday),
            "flat_estimate_inr": round(flat_estimate_total, 2),
            "real_vs_flat_inr": round(float(total_real) - flat_estimate_total, 2),
            "avg_fee_per_close_inr": round(float(total_real) / int(n_close), 2) if n_close else 0,
            "recent_trades": recent_list,
        }

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
            "fees_summary": self.fees_summary(),
        }

        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        EXPORT_PATH.write_text(json.dumps(snap, indent=2, default=str), encoding="utf-8")
        return snap

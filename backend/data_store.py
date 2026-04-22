"""
Local bar cache.

Replaces "every run fetches 400 days from Angel" with "fetch once, read from
SQLite forever". Dramatically cuts Angel API load + rate-limit risk.

Schema:
  daily_bars(symbol, date, open, high, low, close, volume)  PK (symbol, date)

Usage:
  from data_store import get_bars, upsert_bars, latest_date
  df = get_bars("RELIANCE", n_days=400)
  upsert_bars("RELIANCE", new_df)
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "bars.db"


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.executescript("""
        CREATE TABLE IF NOT EXISTS daily_bars (
            symbol TEXT NOT NULL,
            date   TEXT NOT NULL,
            open   REAL,
            high   REAL,
            low    REAL,
            close  REAL,
            volume INTEGER,
            PRIMARY KEY (symbol, date)
        );
        CREATE INDEX IF NOT EXISTS idx_bars_symbol_date
            ON daily_bars(symbol, date DESC);
    """)
    return c


def upsert_bars(symbol: str, df: pd.DataFrame) -> int:
    """Insert or replace rows for given symbol. df must have columns Date, Open, High, Low, Close, Volume."""
    if df is None or df.empty:
        return 0
    rows = []
    for _, r in df.iterrows():
        d = r["Date"]
        if isinstance(d, (pd.Timestamp, datetime)):
            d = d.strftime("%Y-%m-%d")
        else:
            d = str(d)[:10]
        rows.append((
            symbol, d,
            float(r.get("Open", 0) or 0),
            float(r.get("High", 0) or 0),
            float(r.get("Low", 0) or 0),
            float(r.get("Close", 0) or 0),
            int(r.get("Volume", 0) or 0),
        ))
    with _conn() as c:
        c.executemany(
            "INSERT OR REPLACE INTO daily_bars VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def get_bars(symbol: str, n_days: int | None = None, since: str | None = None) -> pd.DataFrame:
    """
    Return DataFrame(Date, Open, High, Low, Close, Volume) sorted ascending.
    If n_days set, returns most recent n_days rows.
    If since set (YYYY-MM-DD), returns rows >= since.
    If neither, returns all bars.
    """
    with _conn() as c:
        if n_days is not None:
            rows = c.execute(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_bars WHERE symbol = ? ORDER BY date DESC LIMIT ?",
                (symbol, n_days),
            ).fetchall()
            rows.reverse()
        elif since is not None:
            rows = c.execute(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_bars WHERE symbol = ? AND date >= ? ORDER BY date",
                (symbol, since),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT date, open, high, low, close, volume "
                "FROM daily_bars WHERE symbol = ? ORDER BY date",
                (symbol,),
            ).fetchall()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
    # Always tz-naive to stay consistent with data_fetcher's Angel-fallback path
    df["Date"] = pd.to_datetime(df["Date"])
    if getattr(df["Date"].dtype, "tz", None) is not None:
        df["Date"] = df["Date"].dt.tz_localize(None)
    return df


def latest_date(symbol: str) -> str | None:
    with _conn() as c:
        r = c.execute(
            "SELECT MAX(date) FROM daily_bars WHERE symbol = ?", (symbol,)
        ).fetchone()
    return r[0] if r and r[0] else None


def bar_count(symbol: str) -> int:
    with _conn() as c:
        r = c.execute(
            "SELECT COUNT(*) FROM daily_bars WHERE symbol = ?", (symbol,)
        ).fetchone()
    return int(r[0]) if r else 0


def all_symbols() -> list[str]:
    with _conn() as c:
        rows = c.execute("SELECT DISTINCT symbol FROM daily_bars").fetchall()
    return [r[0] for r in rows]


def coverage_report() -> dict:
    """Diagnostic: per-symbol row count + latest date."""
    with _conn() as c:
        rows = c.execute(
            "SELECT symbol, COUNT(*), MAX(date), MIN(date) "
            "FROM daily_bars GROUP BY symbol"
        ).fetchall()
    return {
        r[0]: {"bars": r[1], "latest": r[2], "oldest": r[3]}
        for r in rows
    }


if __name__ == "__main__":
    cov = coverage_report()
    print(f"Symbols cached: {len(cov)}")
    for sym in sorted(cov)[:5]:
        print(f"  {sym}: {cov[sym]}")

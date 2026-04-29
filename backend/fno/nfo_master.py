"""
NFO scrip-master loader for Angel SmartAPI.

Pulls the daily-refreshed F&O contract master, indexes by underlying symbol
+ expiry + strike + option type. Used by:
  - option_chain.py to fetch chain for a given underlying
  - fno_signals.py to translate equity momentum signal to ATM/OTM option selection

Schema (from Angel scrip-master):
  token, symbol, name, expiry, strike, lotsize, instrumenttype, exch_seg
    instrumenttype: OPTSTK / OPTIDX / FUTSTK / FUTIDX
    exch_seg: NFO / NSE / BSE / etc
    expiry: DD-MMM-YYYY (e.g. "30-Apr-2026")
    strike: INR x 100 for options, 0 for futures
    symbol: tradingsymbol like NIFTY26APR25450CE

Capital gate: this module loads but is INERT until enabled by config.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger


SCRIP_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
CACHE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "nfo_master.json"
CACHE_TTL_HOURS = 12


def _is_cache_fresh(path: Path) -> bool:
    if not path.exists():
        return False
    age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600.0
    return age_hours < CACHE_TTL_HOURS


def _fetch_master() -> Optional[list[dict]]:
    """Download Angel's full scrip master. ~60K rows, ~25MB. Filter to NFO only."""
    try:
        r = requests.get(SCRIP_MASTER_URL, timeout=60)
        r.raise_for_status()
        data = r.json()
        nfo_only = [d for d in data if d.get("exch_seg") == "NFO"]
        logger.info(f"Loaded NFO master: {len(nfo_only)} rows (filtered from {len(data)})")
        return nfo_only
    except Exception as e:
        logger.error(f"NFO master fetch failed: {e}")
        return None


def load(force_refresh: bool = False) -> list[dict]:
    """Returns list of NFO instruments. Uses cached file if fresh."""
    if not force_refresh and _is_cache_fresh(CACHE_PATH):
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    rows = _fetch_master()
    if rows is None:
        return []
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(rows), encoding="utf-8")
    return rows


def index_by_underlying(rows: Optional[list[dict]] = None) -> dict[str, list[dict]]:
    """Group F&O contracts by underlying symbol (NIFTY, BANKNIFTY, RELIANCE, ...).
    Underlying = symbol prefix before the expiry digits."""
    if rows is None:
        rows = load()
    out: dict[str, list[dict]] = {}
    for r in rows:
        sym = r.get("symbol", "")
        # Tradingsymbol like NIFTY26APR25450CE -> underlying NIFTY
        # Heuristic: take prefix until first digit
        underlying = ""
        for ch in sym:
            if ch.isdigit():
                break
            underlying += ch
        if not underlying:
            continue
        out.setdefault(underlying, []).append(r)
    return out


def get_option_chain(underlying: str, expiry: Optional[str] = None,
                     instrument: str = "OPTIDX") -> list[dict]:
    """Returns option contracts for an underlying.
    expiry: filter by date string (e.g. '30APR2026'); None = all expiries.
    instrument: OPTIDX (NIFTY/BANKNIFTY) or OPTSTK (single-stock options)."""
    idx = index_by_underlying()
    contracts = idx.get(underlying.upper(), [])
    out = [c for c in contracts if c.get("instrumenttype") == instrument]
    if expiry:
        out = [c for c in out if expiry.upper() in c.get("expiry", "").upper()]
    return out


def list_expiries(underlying: str, instrument: str = "OPTIDX") -> list[str]:
    """Sorted list of available expiries (latest first)."""
    contracts = get_option_chain(underlying, expiry=None, instrument=instrument)
    expiries = sorted({c.get("expiry") for c in contracts if c.get("expiry")},
                      key=lambda d: datetime.strptime(d, "%d%b%Y") if d else datetime.max,
                      reverse=False)
    return expiries


if __name__ == "__main__":
    rows = load()
    print(f"Total NFO contracts: {len(rows)}")
    if rows:
        idx = index_by_underlying(rows)
        print(f"Distinct underlyings: {len(idx)}")
        for u in ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"]:
            count = len(idx.get(u, []))
            expiries = list_expiries(u, "OPTIDX" if u in ("NIFTY", "BANKNIFTY") else "OPTSTK")[:3]
            print(f"  {u:12s}: {count} contracts, next expiries: {expiries}")

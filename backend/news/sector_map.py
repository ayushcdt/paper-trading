"""
Sector lookups for the hybrid overlay.

Two layers:
  1. SYMBOL_TO_SECTOR: NSE's official 13 industry classifications, loaded from
     data/nifty500_sectors.json. Coarse but authoritative.
  2. THEME_SECTORS: thematic buckets used by themes.py (e.g., "sugar",
     "defence_psu"). Finer than NSE industries because some themes hit
     specific subsets of an industry. These are HAND-CURATED — extend when
     adding a new theme.

Use:
    from news.sector_map import symbols_in_theme, sector_of
    sugar_symbols = symbols_in_theme("sugar")
    industry_of("RELIANCE")  -> "Oil Gas & Consumable Fuels"
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


_NIFTY500_SECTORS_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "nifty500_sectors.json"

# Lazy-load NSE industry map
_SYMBOL_TO_INDUSTRY: Optional[dict[str, str]] = None


def _load_industries() -> dict[str, str]:
    global _SYMBOL_TO_INDUSTRY
    if _SYMBOL_TO_INDUSTRY is None:
        try:
            _SYMBOL_TO_INDUSTRY = json.loads(_NIFTY500_SECTORS_PATH.read_text(encoding="utf-8"))
        except Exception:
            _SYMBOL_TO_INDUSTRY = {}
    return _SYMBOL_TO_INDUSTRY


def industry_of(symbol: str) -> Optional[str]:
    """NSE industry classification for a symbol, or None if not in Nifty 500."""
    return _load_industries().get(symbol)


# ---------- Theme buckets ---------------------------------------------------
# Hand-curated finer subsets used by news/themes.py. When adding a new theme,
# either reuse an existing bucket or add a new one here.
#
# IMPORTANT: only include symbols actually in our trading universe (i.e., that
# have an entry in data/extended_tokens.json). Symbols listed here but not in
# the universe are silently dropped at lookup time.

THEME_SECTORS: dict[str, list[str]] = {
    # --- Sugar mills (mostly outside Nifty 500) ---
    # Most pure-play sugar names are smallcaps; if you've added them to
    # data/extended_tokens.json they'll be picked up. Otherwise empty.
    "sugar": [
        "BALRAMCHIN", "TRIVENI", "RENUKA", "EIDPARRY", "DCMSRIND",
        "DHAMPURSUG", "BAJAJHIND",
    ],

    # --- 2W with EV/flex-fuel exposure ---
    "auto_2w_ev": ["TVSMOTOR", "BAJAJ-AUTO", "EICHERMOT", "OLECTRA", "GREAVESCOT"],

    # --- 4W passenger autos (rate-sensitive) ---
    "auto_4w": ["MARUTI", "M&M", "TATAMOTORS"],   # TATAMOTORS may not be in current universe

    # --- Real estate (rate-sensitive) ---
    "realty": ["DLF", "LODHA", "PRESTIGE", "OBEROIRLTY", "GODREJPROP", "BRIGADE", "MAHLIFE"],

    # --- NBFCs (rate-sensitive funding) ---
    "nbfc": ["BAJFINANCE", "BAJAJFINSV", "CHOLAFIN", "LICHSGFIN", "PFC", "RECLTD", "SBICARD"],

    # --- PSU banks (rate-sensitive deposit base) ---
    "psu_banks": ["SBIN", "BANKBARODA", "PNB", "CANBK", "INDIANB", "UNIONBANK"],

    # --- Defence PSUs (govt capex sensitivity) ---
    "defence_psu": ["HAL", "BEL", "BDL", "MAZDOCK", "BHEL"],

    # --- Oil upstream (benefits from crude price spike) ---
    "oil_upstream": ["ONGC", "OIL"],   # OIL India may not be in universe

    # --- Oil OMCs (downstream — squeezed by crude spike when retail prices regulated) ---
    "oil_omc": ["BPCL", "IOC", "HINDPETRO"],

    # --- Aviation (fuel-cost sensitive) ---
    "aviation": ["INDIGO"],

    # --- Paint (crude-derived input cost) ---
    "paint": ["ASIANPAINT", "BERGEPAINT"],

    # --- Cement (infra capex beneficiary) ---
    "cement": ["ULTRACEMCO", "AMBUJACEM", "SHREECEM", "GRASIM"],

    # --- Capital goods (infra capex) ---
    "capital_goods": ["LT", "SIEMENS", "BHEL", "CUMMINSIND"],

    # --- Railways PSU (budget capex) ---
    "railways_psu": ["IRCTC", "IRFC", "RVNL", "RAILTEL", "IRCON", "RITES", "CONCOR"],

    # --- Generic infrastructure (broad budget infra exposure) ---
    "infrastructure": ["LT", "RVNL", "IRCON", "POWERGRID", "NTPC", "GAIL"],

    # --- Specialty chemicals (China+1 beneficiary) ---
    "specialty_chemicals": [
        "SRF", "DEEPAKNTR", "AARTIIND", "ATUL", "NAVINFLUOR",
        "PIIND", "PIDILITIND",
    ],

    # --- Bluechips (FII-flow proxy) ---
    "bluechips": [
        "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR",
        "BHARTIARTL", "SBIN", "LT", "KOTAKBANK", "ITC", "AXISBANK",
        "BAJFINANCE", "ASIANPAINT", "MARUTI",
    ],
}


def symbols_in_theme(theme_sector: str) -> list[str]:
    """Symbols mapped to a theme bucket, filtered to those in the trading universe.
    Silent drop of unknown symbols keeps the catalog forgiving — you can list a
    smallcap here that's not yet in extended_tokens and it just won't fire until
    you add it."""
    raw = THEME_SECTORS.get(theme_sector, [])
    if not raw:
        return []
    try:
        from data_fetcher import SYMBOL_TOKENS
        in_universe = set(SYMBOL_TOKENS.keys())
        return [s for s in raw if s in in_universe]
    except Exception:
        return raw  # fallback: return all

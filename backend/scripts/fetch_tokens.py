"""
One-shot helper: download Angel One's scripmaster and extract NSE equity tokens
for the Nifty Next 50 + Nifty Midcap 100 symbols we care about.

Run quarterly (or whenever a stock joins/leaves an index):
    cd c:\\trading\\backend && python scripts/fetch_tokens.py

Output: c:\\trading\\data\\extended_tokens.json  (loaded automatically by data_fetcher)
"""

import json
import sys
from pathlib import Path

import requests

SCRIPMASTER_URL = "https://margincalculator.angelone.in/OpenAPI_File/files/OpenAPIScripMaster.json"
SCRIPMASTER_FALLBACK = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
OUT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "extended_tokens.json"

# Symbols we want tokens for. Stocks that overlap Nifty 50 are skipped (already in
# data_fetcher.SYMBOL_TOKENS). Update this list as index composition changes.

NIFTY_NEXT_50 = [
    "ADANIGREEN", "ADANIPOWER", "AMBUJACEM", "ATGL", "BAJAJHLDNG",
    "BANKBARODA", "BERGEPAINT", "BOSCHLTD", "CANBK", "CGPOWER",
    "CHOLAFIN", "COLPAL", "DABUR", "DLF", "DMART",
    "GAIL", "GODREJCP", "HAL", "HAVELLS", "ICICIGI",
    "ICICIPRULI", "INDHOTEL", "INDIGO", "IOC", "IRCTC",
    "IRFC", "JINDALSTEL", "JIOFIN", "LICI", "LODHA",
    "LTIM", "MARICO", "MOTHERSON", "NAUKRI", "NHPC",
    "PFC", "PIDILITIND", "PNB", "POWERGRID", "RECLTD",
    "SHREECEM", "SIEMENS", "SRF", "TATAMOTORS", "TATAPOWER",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "UNITDSPR", "VBL",
    "VEDL", "ZOMATO", "ZYDUSLIFE",
]

NIFTY_MIDCAP_100_PARTIAL = [
    "AUROPHARMA", "BHEL", "BIOCON", "CONCOR", "CUMMINSIND",
    "ESCORTS", "EXIDEIND", "FEDERALBNK", "GLENMARK", "GMRINFRA",
    "GUJGASLTD", "HINDPETRO", "IDFCFIRSTB", "INDIANB", "INDUSTOWER",
    "LICHSGFIN", "LUPIN", "MFSL", "MPHASIS", "MRF",
    "NMDC", "OBEROIRLTY", "OFSS", "PAGEIND", "PERSISTENT",
    "PETRONET", "PIIND", "POLYCAB", "PRESTIGE", "RVNL",
    "SAIL", "SBICARD", "SUNTV", "SUPREMEIND", "SYNGENE",
    "TATACOMM", "TATAELXSI", "TIINDIA", "TORNTPOWER", "UBL",
    "UNIONBANK", "VOLTAS", "YESBANK", "ZEEL",
]

WANTED = sorted(set(NIFTY_NEXT_50 + NIFTY_MIDCAP_100_PARTIAL))


def main() -> int:
    master = None
    for url in (SCRIPMASTER_URL, SCRIPMASTER_FALLBACK):
        print(f"Trying scripmaster: {url} ...")
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            master = r.json()
            print(f"  Downloaded {len(master)} entries.")
            break
        except Exception as e:
            print(f"  Failed: {e}")
    if master is None:
        print("FATAL: all scripmaster URLs failed. Check Angel docs for current URL.")
        return 1

    print(f"Scripmaster has {len(master)} entries. Filtering to NSE EQ ...")
    by_symbol: dict[str, str] = {}
    for row in master:
        if row.get("exch_seg") != "NSE":
            continue
        symbol = (row.get("symbol") or "").upper()
        if not symbol.endswith("-EQ"):
            continue
        name = symbol[:-3]
        if name in WANTED and name not in by_symbol:
            by_symbol[name] = str(row.get("token"))

    missing = [s for s in WANTED if s not in by_symbol]
    print(f"Resolved {len(by_symbol)}/{len(WANTED)} tokens.")
    if missing:
        print(f"Missing (not found in NSE EQ scripmaster): {missing}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(by_symbol, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote {OUT_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

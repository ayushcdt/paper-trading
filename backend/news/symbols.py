"""
Trading-symbol -> company-name aliases.

Used to find news/filings articles relevant to each tradeable symbol.
News articles & exchange filings reference companies by their full registered
name (e.g. "Reliance Industries Ltd"), not the trading ticker (RELIANCE).
This map lets the news pipeline match precisely without leaky regex.

Each entry: trading_symbol -> [list of name variants seen in newsapp data].
Variants should be CASE-INSENSITIVE-MATCHED. Order them with the most
specific/distinctive name first to make per-article attribution faster.

When adding a symbol:
  1. Check actual NSE/BSE filing titles in newsapp first (titles look like
     "<Company Name> ? Outcome of Board Meeting"). Use the leading portion.
  2. Add the most-common shorthand the press uses (e.g. "RIL" for Reliance).
  3. AVOID short tokens like "TCS" or "ITC" alone -- they cause false
     positives in unrelated articles. The base symbol is included
     automatically below.

Verification: scripts/validate_news_symbol_map.py checks last-7d filings
for unmatched mentions.
"""
from __future__ import annotations


# Manually curated for the Nifty 50 + Next 50 + Midcap universe in
# data/extended_tokens.json + backend/data_fetcher.SYMBOL_TOKENS.
# Names verified against actual NSE/BSE filing titles seen in newsapp
# (last 7d) where possible, else from public listing names.
SYMBOL_TO_NAMES: dict[str, list[str]] = {
    # ----- Nifty 50 (heavy-cap names) ---------------------------------
    "RELIANCE":     ["Reliance Industries"],
    "TCS":          ["Tata Consultancy Services", "Tata Consultancy"],
    "HDFCBANK":     ["HDFC Bank"],
    "INFY":         ["Infosys"],
    "ICICIBANK":    ["ICICI Bank"],
    "HINDUNILVR":   ["Hindustan Unilever", "HUL"],
    "BHARTIARTL":   ["Bharti Airtel"],
    "SBIN":         ["State Bank of India"],
    "KOTAKBANK":    ["Kotak Mahindra Bank"],
    "ITC":          ["ITC Limited", "ITC Ltd"],
    "LT":           ["Larsen & Toubro", "Larsen and Toubro", "L&T"],
    "AXISBANK":     ["Axis Bank"],
    "BAJFINANCE":   ["Bajaj Finance"],
    "ASIANPAINT":   ["Asian Paints"],
    "MARUTI":       ["Maruti Suzuki"],
    "TITAN":        ["Titan Company"],
    "SUNPHARMA":    ["Sun Pharmaceutical", "Sun Pharma"],
    "ULTRACEMCO":   ["UltraTech Cement", "Ultratech Cement"],
    "WIPRO":        ["Wipro"],
    "NESTLEIND":    ["Nestle India"],
    "HCLTECH":      ["HCL Technologies"],
    "TECHM":        ["Tech Mahindra"],
    "POWERGRID":    ["Power Grid Corporation"],
    "NTPC":         ["NTPC Limited", "NTPC Ltd"],
    "M&M":          ["Mahindra & Mahindra", "Mahindra and Mahindra"],
    "ONGC":         ["Oil and Natural Gas Corporation", "ONGC"],
    "JSWSTEEL":     ["JSW Steel"],
    "TATASTEEL":    ["Tata Steel"],
    "BAJAJFINSV":   ["Bajaj Finserv"],
    "ADANIENT":     ["Adani Enterprises"],
    "ADANIPORTS":   ["Adani Ports"],
    "COALINDIA":    ["Coal India"],
    "GRASIM":       ["Grasim Industries"],
    "BRITANNIA":    ["Britannia Industries"],
    "CIPLA":        ["Cipla Limited", "Cipla Ltd"],
    "DRREDDY":      ["Dr. Reddy's Laboratories", "Dr Reddy's", "Dr Reddys"],
    "EICHERMOT":    ["Eicher Motors"],
    "DIVISLAB":     ["Divi's Laboratories", "Divis Laboratories"],
    "BPCL":         ["Bharat Petroleum Corporation", "Bharat Petroleum", "BPCL"],
    "SBILIFE":      ["SBI Life Insurance"],
    "HDFCLIFE":     ["HDFC Life Insurance"],
    "APOLLOHOSP":   ["Apollo Hospitals"],
    "TATACONSUM":   ["Tata Consumer Products"],
    "HEROMOTOCO":   ["Hero MotoCorp"],
    "UPL":          ["UPL Limited", "UPL Ltd"],
    "INDUSINDBK":   ["IndusInd Bank"],
    "HINDALCO":     ["Hindalco Industries"],
    "BAJAJ-AUTO":   ["Bajaj Auto"],

    # ----- Next 50 + Midcap -------------------------------------------
    "ADANIGREEN":   ["Adani Green Energy"],
    "ADANIPOWER":   ["Adani Power"],
    "AMBUJACEM":    ["Ambuja Cements"],
    "ATGL":         ["Adani Total Gas"],
    "AUROPHARMA":   ["Aurobindo Pharma"],
    "BAJAJHLDNG":   ["Bajaj Holdings & Investment", "Bajaj Holdings"],
    "BANKBARODA":   ["Bank of Baroda"],
    "BERGEPAINT":   ["Berger Paints"],
    "BHEL":         ["Bharat Heavy Electricals", "BHEL"],
    "BIOCON":       ["Biocon Limited", "Biocon Ltd"],
    "BOSCHLTD":     ["Bosch Limited", "Bosch Ltd"],
    "CANBK":        ["Canara Bank"],
    "CGPOWER":      ["CG Power and Industrial Solutions", "CG Power"],
    "CHOLAFIN":     ["Cholamandalam Investment", "Cholamandalam Finance"],
    "COLPAL":       ["Colgate-Palmolive", "Colgate Palmolive"],
    "CONCOR":       ["Container Corporation of India", "Container Corporation"],
    "CUMMINSIND":   ["Cummins India"],
    "DABUR":        ["Dabur India"],
    "DLF":          ["DLF Limited", "DLF Ltd"],
    "DMART":        ["Avenue Supermarts", "DMart"],
    "ESCORTS":      ["Escorts Kubota", "Escorts Limited"],
    "EXIDEIND":     ["Exide Industries"],
    "FEDERALBNK":   ["Federal Bank"],
    "GAIL":         ["GAIL (India)", "GAIL India"],
    "GLENMARK":     ["Glenmark Pharmaceuticals", "Glenmark Pharma"],
    "GODREJCP":     ["Godrej Consumer Products"],
    "GUJGASLTD":    ["Gujarat Gas"],
    "HAL":          ["Hindustan Aeronautics", "HAL"],
    "HAVELLS":      ["Havells India"],
    "HINDPETRO":    ["Hindustan Petroleum Corporation", "Hindustan Petroleum", "HPCL"],
    "ICICIGI":      ["ICICI Lombard General Insurance", "ICICI Lombard"],
    "ICICIPRULI":   ["ICICI Prudential Life Insurance", "ICICI Prudential"],
    "IDFCFIRSTB":   ["IDFC First Bank"],
    "INDHOTEL":     ["Indian Hotels", "Indian Hotels Company"],
    "INDIANB":      ["Indian Bank"],
    "INDIGO":       ["InterGlobe Aviation", "IndiGo"],
    "INDUSTOWER":   ["Indus Towers"],
    "IOC":          ["Indian Oil Corporation", "IOCL"],
    "IRCTC":        ["Indian Railway Catering and Tourism Corporation", "IRCTC"],
    "IRFC":         ["Indian Railway Finance Corporation", "IRFC"],
    "JINDALSTEL":   ["Jindal Steel & Power", "Jindal Steel and Power"],
    "JIOFIN":       ["Jio Financial Services"],
    "LICHSGFIN":    ["LIC Housing Finance"],
    "LICI":         ["Life Insurance Corporation of India", "Life Insurance Corporation"],
    "LODHA":        ["Macrotech Developers", "Lodha Developers"],
    "LUPIN":        ["Lupin Limited", "Lupin Ltd"],
    "MARICO":       ["Marico Limited", "Marico Ltd"],
    "MFSL":         ["Max Financial Services"],
    "MOTHERSON":    ["Samvardhana Motherson International", "Motherson Sumi"],
    "MPHASIS":      ["Mphasis Limited", "Mphasis Ltd"],
    "MRF":          ["MRF Limited", "MRF Ltd"],
    "NAUKRI":       ["Info Edge (India)", "Info Edge"],
    "NHPC":         ["NHPC Limited", "NHPC Ltd"],
    "NMDC":         ["NMDC Limited", "NMDC Ltd"],
    "OBEROIRLTY":   ["Oberoi Realty"],
    "OFSS":         ["Oracle Financial Services Software", "Oracle Financial Services"],
    "PAGEIND":      ["Page Industries"],
    "PERSISTENT":   ["Persistent Systems"],
    "PETRONET":     ["Petronet LNG"],
    "PFC":          ["Power Finance Corporation"],
    "PIDILITIND":   ["Pidilite Industries"],
    "PIIND":        ["PI Industries"],
    "PNB":          ["Punjab National Bank"],
    "POLYCAB":      ["Polycab India"],
    "PRESTIGE":     ["Prestige Estates Projects", "Prestige Estates"],
    "RECLTD":       ["REC Limited", "REC Ltd"],
    "RVNL":         ["Rail Vikas Nigam"],
    "SAIL":         ["Steel Authority of India"],
    "SBICARD":      ["SBI Cards and Payment Services", "SBI Cards"],
    "SHREECEM":     ["Shree Cement"],
    "SIEMENS":      ["Siemens Limited", "Siemens Ltd"],
    "SRF":          ["SRF Limited", "SRF Ltd"],
    "SUNTV":        ["Sun TV Network"],
    "SUPREMEIND":   ["Supreme Industries"],
    "SYNGENE":      ["Syngene International"],
    "TATACOMM":     ["Tata Communications"],
    "TATAELXSI":    ["Tata Elxsi"],
    "TATAPOWER":    ["Tata Power"],
    "TIINDIA":      ["Tube Investments of India", "Tube Investments"],
    "TORNTPHARM":   ["Torrent Pharmaceuticals", "Torrent Pharma"],
    "TORNTPOWER":   ["Torrent Power"],
    "TRENT":        ["Trent Limited", "Trent Ltd"],
    "TVSMOTOR":     ["TVS Motor Company", "TVS Motors"],
    "UBL":          ["United Breweries"],
    "UNIONBANK":    ["Union Bank of India"],
    "UNITDSPR":     ["United Spirits"],
    "VBL":          ["Varun Beverages"],
    "VEDL":         ["Vedanta Limited", "Vedanta Ltd"],
    "VOLTAS":       ["Voltas Limited", "Voltas Ltd"],
    "YESBANK":      ["Yes Bank"],
    "ZEEL":         ["Zee Entertainment Enterprises", "Zee Entertainment"],
    "ZYDUSLIFE":    ["Zydus Lifesciences"],
}


def names_for(symbol: str) -> list[str]:
    """Return matchable name variants for a symbol. Always includes the symbol itself
    if length >= 4 (3-letter symbols cause too many false positives).
    Returns empty list if symbol unknown — caller should fall back to symbol-only match."""
    names = list(SYMBOL_TO_NAMES.get(symbol, []))
    if len(symbol) >= 4 and symbol not in {n.upper() for n in names}:
        names.append(symbol)
    return names


def coverage_stats() -> dict:
    """Useful for tests: how many symbols are mapped, missing, etc."""
    return {
        "mapped_symbols": len(SYMBOL_TO_NAMES),
        "total_name_variants": sum(len(v) for v in SYMBOL_TO_NAMES.values()),
    }

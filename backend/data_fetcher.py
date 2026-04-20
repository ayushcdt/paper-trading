"""
Data Fetcher using Angel One SmartAPI
Real-time and historical data
"""

import json
from pathlib import Path
from SmartApi import SmartConnect
import pyotp
import pandas as pd
from datetime import datetime, timedelta
from logzero import logger
from config import ANGEL_CREDENTIALS

# Angel One Symbol Tokens (NSE)
SYMBOL_TOKENS = {
    # Main Indices
    "NIFTY": "99926000",
    "BANKNIFTY": "99926009",
    "INDIAVIX": "99926017",

    # Sector Indices (for sector rotation analysis)
    "NIFTY_IT": "99926008",
    "NIFTY_PHARMA": "99926023",
    "NIFTY_AUTO": "99926029",
    "NIFTY_FMCG": "99926021",
    "NIFTY_METAL": "99926030",
    "NIFTY_REALTY": "99926018",
    "NIFTY_ENERGY": "99926020",
    "NIFTY_INFRA": "99926019",
    "NIFTY_BANK": "99926009",

    # Nifty 50 Stocks
    "RELIANCE": "2885",
    "TCS": "11536",
    "HDFCBANK": "1333",
    "INFY": "1594",
    "ICICIBANK": "4963",
    "HINDUNILVR": "1394",
    "BHARTIARTL": "10604",
    "SBIN": "3045",
    "KOTAKBANK": "1922",
    "ITC": "1660",
    "LT": "11483",
    "AXISBANK": "5900",
    "BAJFINANCE": "317",
    "ASIANPAINT": "236",
    "MARUTI": "10999",
    "TITAN": "3506",
    "SUNPHARMA": "3351",
    "ULTRACEMCO": "11532",
    "WIPRO": "3787",
    "NESTLEIND": "17963",
    "HCLTECH": "7229",
    "TECHM": "13538",
    "POWERGRID": "14977",
    "NTPC": "11630",
    "M&M": "2031",
    "ONGC": "2475",
    "JSWSTEEL": "11723",
    "TATASTEEL": "3499",
    "BAJAJFINSV": "16675",
    "ADANIENT": "25",
    "ADANIPORTS": "15083",
    "COALINDIA": "20374",
    "GRASIM": "1232",
    "BRITANNIA": "547",
    "CIPLA": "694",
    "DRREDDY": "881",
    "EICHERMOT": "910",
    "DIVISLAB": "10940",
    "BPCL": "526",
    "SBILIFE": "21808",
    "HDFCLIFE": "467",
    "APOLLOHOSP": "157",
    "TATACONSUM": "3432",
    "HEROMOTOCO": "1348",
    "UPL": "11287",
    "INDUSINDBK": "5258",
    "HINDALCO": "1363",
    "BAJAJ-AUTO": "16669",
}

# Merge in extended universe (Nifty Next 50 + Midcap) if user has run scripts/fetch_tokens.py
_EXTENDED_TOKENS_PATH = Path(__file__).resolve().parent.parent / "data" / "extended_tokens.json"
if _EXTENDED_TOKENS_PATH.exists():
    try:
        _extended = json.loads(_EXTENDED_TOKENS_PATH.read_text(encoding="utf-8"))
        new_count = sum(1 for s in _extended if s not in SYMBOL_TOKENS)
        SYMBOL_TOKENS.update(_extended)
        logger.info(f"Loaded {new_count} extended tokens from {_EXTENDED_TOKENS_PATH.name}")
    except Exception as e:
        logger.warning(f"Failed to load extended tokens: {e}")
else:
    logger.info("No extended_tokens.json found. Run: python scripts/fetch_tokens.py")


class AngelDataFetcher:
    def __init__(self):
        self.api = SmartConnect(api_key=ANGEL_CREDENTIALS["api_key"])
        self.logged_in = False

    def login(self) -> bool:
        """Login to Angel One API"""
        try:
            totp = ""
            if ANGEL_CREDENTIALS.get("totp_secret"):
                totp = pyotp.TOTP(ANGEL_CREDENTIALS["totp_secret"]).now()

            data = self.api.generateSession(
                clientCode=ANGEL_CREDENTIALS["client_id"],
                password=ANGEL_CREDENTIALS["pin"],
                totp=totp
            )

            if data.get("status"):
                self.logged_in = True
                logger.info("Angel API login successful")
                return True
            else:
                logger.error(f"Login failed: {data.get('message')}")
                return False
        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def get_ltp(self, symbol: str, exchange: str = "NSE") -> dict:
        """Get Last Traded Price for a symbol or index"""
        if not self.logged_in:
            self.login()

        try:
            token = SYMBOL_TOKENS.get(symbol)
            if not token:
                logger.warning(f"Token not found for {symbol}")
                return {}

            # Check if this is an index (tokens starting with 999)
            is_index = token.startswith("999")

            # For indices, use NSE exchange with index symbol
            if is_index:
                # Map internal names to Angel API index names
                index_name_map = {
                    "NIFTY": "Nifty 50",
                    "BANKNIFTY": "Nifty Bank",
                    "INDIAVIX": "India VIX",
                    "NIFTY_IT": "Nifty IT",
                    "NIFTY_PHARMA": "Nifty Pharma",
                    "NIFTY_AUTO": "Nifty Auto",
                    "NIFTY_FMCG": "Nifty FMCG",
                    "NIFTY_METAL": "Nifty Metal",
                    "NIFTY_REALTY": "Nifty Realty",
                    "NIFTY_ENERGY": "Nifty Energy",
                    "NIFTY_INFRA": "Nifty Infra",
                    "NIFTY_BANK": "Nifty Bank",
                }
                api_symbol = index_name_map.get(symbol, symbol)
                data = self.api.ltpData("NSE", api_symbol, token)
            else:
                data = self.api.ltpData(exchange, symbol, token)

            if data.get("status"):
                return data.get("data", {})
            return {}
        except Exception as e:
            logger.error(f"Error getting LTP for {symbol}: {e}")
            return {}

    def get_quote(self, symbol: str, exchange: str = "NSE") -> dict:
        """Get full quote for a symbol"""
        if not self.logged_in:
            self.login()

        try:
            token = SYMBOL_TOKENS.get(symbol)
            if not token:
                return {}

            data = self.api.getQuote(exchange, symbol, token)
            if data.get("status"):
                return data.get("data", {})
            return {}
        except Exception as e:
            logger.error(f"Error getting quote for {symbol}: {e}")
            return {}

    def get_historical_data(
        self,
        symbol: str,
        interval: str = "ONE_DAY",
        days: int = 365,
        exchange: str = "NSE"
    ) -> pd.DataFrame:
        """
        Get historical candle data

        interval: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE,
                  THIRTY_MINUTE, ONE_HOUR, ONE_DAY
        """
        if not self.logged_in:
            self.login()

        try:
            token = SYMBOL_TOKENS.get(symbol)
            if not token:
                logger.warning(f"Token not found for {symbol}")
                return pd.DataFrame()

            # Check if this is an index (tokens starting with 999)
            is_index = token.startswith("999")

            to_date = datetime.now()
            from_date = to_date - timedelta(days=days)

            params = {
                "exchange": "NSE",  # NSE for both stocks and indices
                "symboltoken": token,
                "interval": interval,
                "fromdate": from_date.strftime("%Y-%m-%d %H:%M"),
                "todate": to_date.strftime("%Y-%m-%d %H:%M")
            }

            data = self.api.getCandleData(params)

            if data.get("status") and data.get("data"):
                df = pd.DataFrame(
                    data["data"],
                    columns=["Date", "Open", "High", "Low", "Close", "Volume"]
                )
                df["Date"] = pd.to_datetime(df["Date"])
                return df
            else:
                logger.warning(f"No historical data for {symbol}: {data.get('message', 'Unknown error')}")

            return pd.DataFrame()

        except Exception as e:
            logger.error(f"Error getting historical data for {symbol}: {e}")
            return pd.DataFrame()

    def get_market_data(self) -> dict:
        """Get current market snapshot"""
        if not self.logged_in:
            self.login()

        try:
            # Get Nifty
            nifty_data = self.get_ltp("NIFTY", "NSE")
            banknifty_data = self.get_ltp("BANKNIFTY", "NSE")

            return {
                "nifty": {
                    "ltp": nifty_data.get("ltp", 0),
                    "open": nifty_data.get("open", 0),
                    "high": nifty_data.get("high", 0),
                    "low": nifty_data.get("low", 0),
                    "close": nifty_data.get("close", 0),
                },
                "banknifty": {
                    "ltp": banknifty_data.get("ltp", 0),
                    "open": banknifty_data.get("open", 0),
                    "high": banknifty_data.get("high", 0),
                    "low": banknifty_data.get("low", 0),
                    "close": banknifty_data.get("close", 0),
                }
            }
        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            return {}

    def get_multiple_ltp(self, symbols: list) -> dict:
        """Get LTP for multiple symbols"""
        if not self.logged_in:
            self.login()

        results = {}
        for symbol in symbols:
            data = self.get_ltp(symbol)
            if data:
                results[symbol] = data.get("ltp", 0)

        return results

    def logout(self):
        """Logout from API"""
        try:
            self.api.terminateSession(ANGEL_CREDENTIALS["client_id"])
            self.logged_in = False
            logger.info("Logged out")
        except Exception as e:
            logger.error(f"Logout error: {e}")


# Singleton instance
_fetcher = None

def get_fetcher() -> AngelDataFetcher:
    global _fetcher
    if _fetcher is None:
        _fetcher = AngelDataFetcher()
    return _fetcher


if __name__ == "__main__":
    # Test
    fetcher = AngelDataFetcher()
    if fetcher.login():
        print("\nMarket Data:")
        market = fetcher.get_market_data()
        print(f"Nifty: {market.get('nifty', {}).get('ltp', 'N/A')}")
        print(f"Bank Nifty: {market.get('banknifty', {}).get('ltp', 'N/A')}")

        print("\nRELIANCE LTP:")
        ltp = fetcher.get_ltp("RELIANCE")
        print(ltp)

        fetcher.logout()

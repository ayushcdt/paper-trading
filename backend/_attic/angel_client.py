"""
Angel One SmartAPI Client
Handles authentication and data fetching
"""

from SmartApi import SmartConnect
import pyotp
from logzero import logger
from config import ANGEL_CREDENTIALS


class AngelClient:
    def __init__(self):
        self.api = SmartConnect(api_key=ANGEL_CREDENTIALS["api_key"])
        self.session = None
        self.auth_token = None
        self.feed_token = None

    def login(self) -> bool:
        """Login to Angel One API"""
        try:
            # Generate TOTP if secret is provided
            totp = ""
            if ANGEL_CREDENTIALS.get("totp_secret"):
                totp = pyotp.TOTP(ANGEL_CREDENTIALS["totp_secret"]).now()

            # Generate session
            data = self.api.generateSession(
                clientCode=ANGEL_CREDENTIALS["client_id"],
                password=ANGEL_CREDENTIALS["pin"],
                totp=totp
            )

            if data.get("status"):
                self.auth_token = data["data"]["jwtToken"]
                self.feed_token = data["data"]["feedToken"]
                logger.info(f"Login successful for {ANGEL_CREDENTIALS['client_id']}")
                return True
            else:
                logger.error(f"Login failed: {data.get('message', 'Unknown error')}")
                return False

        except Exception as e:
            logger.error(f"Login error: {e}")
            return False

    def get_profile(self) -> dict:
        """Get user profile"""
        try:
            return self.api.getProfile(self.auth_token)
        except Exception as e:
            logger.error(f"Error fetching profile: {e}")
            return {}

    def get_holdings(self) -> list:
        """Get user holdings"""
        try:
            response = self.api.holding()
            if response.get("status"):
                return response.get("data", [])
            return []
        except Exception as e:
            logger.error(f"Error fetching holdings: {e}")
            return []

    def get_positions(self) -> list:
        """Get user positions"""
        try:
            response = self.api.position()
            if response.get("status"):
                return response.get("data", [])
            return []
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return []

    def get_quote(self, symbol_token: str, exchange: str = "NSE") -> dict:
        """Get real-time quote for a symbol"""
        try:
            response = self.api.ltpData(exchange, symbol_token, symbol_token)
            if response.get("status"):
                return response.get("data", {})
            return {}
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol_token}: {e}")
            return {}

    def get_historical_data(
        self,
        symbol_token: str,
        exchange: str = "NSE",
        interval: str = "ONE_DAY",
        from_date: str = None,
        to_date: str = None
    ) -> list:
        """
        Get historical candle data

        interval: ONE_MINUTE, FIVE_MINUTE, FIFTEEN_MINUTE,
                  THIRTY_MINUTE, ONE_HOUR, ONE_DAY
        """
        try:
            from datetime import datetime, timedelta

            if not to_date:
                to_date = datetime.now().strftime("%Y-%m-%d %H:%M")
            if not from_date:
                from_date = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d %H:%M")

            params = {
                "exchange": exchange,
                "symboltoken": symbol_token,
                "interval": interval,
                "fromdate": from_date,
                "todate": to_date
            }

            response = self.api.getCandleData(params)
            if response.get("status"):
                return response.get("data", [])
            return []

        except Exception as e:
            logger.error(f"Error fetching historical data: {e}")
            return []

    def search_symbol(self, symbol: str, exchange: str = "NSE") -> dict:
        """Search for symbol token"""
        try:
            response = self.api.searchScrip(exchange, symbol)
            if response.get("status") and response.get("data"):
                return response["data"][0]
            return {}
        except Exception as e:
            logger.error(f"Error searching symbol {symbol}: {e}")
            return {}

    def logout(self):
        """Logout from API"""
        try:
            self.api.terminateSession(ANGEL_CREDENTIALS["client_id"])
            logger.info("Logged out successfully")
        except Exception as e:
            logger.error(f"Logout error: {e}")

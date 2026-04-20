"""
EXAMPLE config — copy to config.py and fill in real values.
config.py is gitignored. NEVER commit real credentials.

You can also set these as environment variables (preferred); env vars
override the values in config.py.
"""

import os

ANGEL_CREDENTIALS = {
    "api_key":      os.environ.get("ANGEL_API_KEY",      "YOUR_API_KEY"),
    "secret_key":   os.environ.get("ANGEL_SECRET_KEY",   "YOUR_SECRET_KEY"),
    "client_id":    os.environ.get("ANGEL_CLIENT_ID",    "YOUR_CLIENT_ID"),
    "pin":          os.environ.get("ANGEL_PIN",          "YOUR_PIN"),
    "totp_secret":  os.environ.get("ANGEL_TOTP_SECRET",  "YOUR_TOTP_SECRET"),
}

VERCEL_CONFIG = {
    "app_url":    os.environ.get("DASHBOARD_URL", "https://artha-dashboard.vercel.app"),
    "secret_key": os.environ.get("UPDATE_SECRET", "CHANGE_ME_TO_A_LONG_RANDOM_STRING"),
}

ANALYSIS_CONFIG = {
    "risk_per_trade_pct": 1.0,
    "max_positions": 10,
    "min_roe": 15,
    "min_roce": 15,
    "max_debt_equity": 0.5,
    "min_rs_ratio": 1.0,
    "above_200dma": True,
    "min_volume_ratio": 1.5,
}

"""
Alert delivery channels.

Primary: Telegram bot (free, real-time push to phone).
Fallback: file log at data/alerts.log (always written; reads from dashboard).

Config in c:/trading/backend/config.py:
    TELEGRAM_CONFIG = {
        "bot_token": "<from @BotFather>",
        "chat_id":   "<your user id>",
    }

Get your chat_id by messaging your bot, then visiting
https://api.telegram.org/bot<token>/getUpdates
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import requests
from logzero import logger


ALERTS_LOG = Path(__file__).resolve().parent.parent.parent / "data" / "alerts.log"
ALERTS_JSON = Path(__file__).resolve().parent.parent.parent / "data" / "alerts.json"


def _telegram_config() -> dict:
    try:
        from config import TELEGRAM_CONFIG
        return TELEGRAM_CONFIG
    except Exception:
        return {"bot_token": "", "chat_id": ""}


def send_telegram(message: str) -> bool:
    cfg = _telegram_config()
    token = cfg.get("bot_token", "")
    chat_id = cfg.get("chat_id", "")
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        r = requests.post(
            url,
            json={"chat_id": chat_id, "text": message, "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        logger.warning(f"Telegram send failed: {e}")
        return False


def log_to_file(severity: str, message: str) -> None:
    """Always-on fallback. Also appends to JSON list read by dashboard."""
    ts = datetime.now().isoformat()
    ALERTS_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(ALERTS_LOG, "a", encoding="utf-8") as f:
        f.write(f"[{ts}] {severity}: {message}\n")

    # Append to JSON ring-buffer (last 100 alerts)
    try:
        arr = []
        if ALERTS_JSON.exists():
            arr = json.loads(ALERTS_JSON.read_text(encoding="utf-8"))
        arr.append({"timestamp": ts, "severity": severity, "message": message})
        arr = arr[-100:]  # keep last 100
        ALERTS_JSON.write_text(json.dumps(arr, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Alerts JSON append failed: {e}")


def dispatch(severity: str, title: str, body: str = "") -> None:
    """
    Send to all channels. Always logs to file; Telegram only if configured.
    severity: 'info' | 'warning' | 'critical'
    """
    emoji = {"info": "INFO", "warning": "WARN", "critical": "CRIT"}.get(severity, "INFO")
    formatted = f"[{emoji}] {title}"
    if body:
        formatted += f"\n{body}"
    log_to_file(severity, formatted.replace("\n", " | "))
    send_telegram(formatted)

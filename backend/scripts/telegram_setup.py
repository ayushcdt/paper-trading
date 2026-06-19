"""
One-shot helper: fetch chat_id from getUpdates and write it back to config.

Usage:
  1. Open Telegram, message t.me/arthatraderbot anything (e.g. "hi")
  2. Run: python scripts/telegram_setup.py
  3. Verifies token, picks chat_id, writes to config.py, sends test alert.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from config import TELEGRAM_CONFIG


CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.py"


def main() -> int:
    token = TELEGRAM_CONFIG.get("bot_token", "")
    if not token:
        print("ERROR: bot_token missing in config.py")
        return 1

    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=10)
    data = r.json()
    if not data.get("ok"):
        print(f"ERROR: getUpdates failed: {data}")
        return 1

    updates = data.get("result", [])
    if not updates:
        print("No messages yet. Open Telegram, message t.me/arthatraderbot, then re-run.")
        return 1

    chat_id = str(updates[-1]["message"]["chat"]["id"])
    print(f"Found chat_id = {chat_id}")

    # Write back to config.py: replace the os.environ.get default for TELEGRAM_CHAT_ID.
    src = CONFIG_PATH.read_text(encoding="utf-8")
    new = re.sub(
        r'("chat_id":\s+os\.environ\.get\("TELEGRAM_CHAT_ID",\s+)"[^"]*"',
        rf'\1"{chat_id}"',
        src,
    )
    if new == src:
        print("WARN: could not splice chat_id into config.py; set TELEGRAM_CHAT_ID env manually")
    else:
        CONFIG_PATH.write_text(new, encoding="utf-8")
        print("config.py updated.")

    # Persist for future shells
    os.system(f'setx TELEGRAM_CHAT_ID "{chat_id}" >NUL')

    # Test alert
    payload = {
        "chat_id": chat_id,
        "text": "Artha trader: Telegram alerts wired up. You'll receive setup alerts here.",
    }
    rr = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", json=payload, timeout=10)
    if rr.status_code == 200:
        print("Test alert sent. Check Telegram.")
    else:
        print(f"Test alert failed: {rr.status_code} {rr.text}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

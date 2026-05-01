"""
Gemini CLI wrapper -- second-opinion analysis for borderline setups.

Uses the user's Pro plan via cached credentials (no API key, no cost). The
gemini CLI (`gemini -p "<prompt>"`) is invoked as a subprocess.

Use cases:
  - Second opinion on 4/5 conviction setups before alerting
  - Morning briefing narrative generation
  - News article synthesis

All calls have a tight timeout because they sit in a hot path (autotrade
runs every 30 min). If Gemini stalls, we fail open and let the original
logic decide.
"""
from __future__ import annotations

import shutil
import subprocess
from logzero import logger


GEMINI_BIN = shutil.which("gemini") or r"C:\Users\ayush\AppData\Roaming\npm\gemini.cmd"
DEFAULT_TIMEOUT = 120  # seconds (CLI cold-start can take 30-60s)


def ask(prompt: str, timeout: int = DEFAULT_TIMEOUT) -> str | None:
    """
    Run a one-shot Gemini query. Returns trimmed response, or None on failure.
    Strips the leading 'Loaded cached credentials.' line if present.
    """
    try:
        r = subprocess.run(
            [GEMINI_BIN, "-p", prompt],
            capture_output=True, text=True, timeout=timeout,
            shell=False,
        )
        if r.returncode != 0:
            logger.warning(f"gemini exit {r.returncode}: {r.stderr[:200]}")
            return None
        out = r.stdout.strip()
        if out.startswith("Loaded cached credentials."):
            out = out.split("\n", 1)[1].strip() if "\n" in out else ""
        return out or None
    except subprocess.TimeoutExpired:
        logger.warning(f"gemini timeout after {timeout}s")
        return None
    except Exception as e:
        logger.warning(f"gemini call failed: {e}")
        return None


def second_opinion_setup(direction: str, reasons: list[str], market_state: dict) -> tuple[bool, str]:
    """
    Ask Gemini whether a F&O setup looks reasonable. Returns (agree, rationale).
    Used by claude_autotrade.py for borderline 4/5 conviction setups.
    """
    nifty = market_state["nifty"]
    vix = market_state.get("vix_ltp", 0)
    prompt = (
        f"You are a F&O trading analyst. Evaluate this NIFTY weekly options setup.\n\n"
        f"Proposed direction: {direction}\n"
        f"NIFTY spot: {nifty['ltp']:.0f}, intraday {nifty['intraday_pct']:+.2f}%, "
        f"open-to-now {nifty.get('open_to_now', 0):+.2f}%\n"
        f"India VIX: {vix:.2f}\n"
        f"Reasons supporting setup:\n  - " + "\n  - ".join(reasons) + "\n\n"
        f"Reply in this exact format:\n"
        f"VERDICT: AGREE or DISAGREE\n"
        f"REASON: <one sentence, max 25 words>\n"
    )
    resp = ask(prompt, timeout=90)
    if not resp:
        return True, "Gemini unavailable; defaulting to AGREE"
    agree = "AGREE" in resp.upper().split("\n")[0]
    return agree, resp[:300]

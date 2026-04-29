"""
WebSocket watchdog. Runs every 1 minute via Windows Task Scheduler.

Replaces the broken alert-only watchdog. Actually restarts ws_runner.py
when it's down -- NSSM is not configured on this machine, so without this
the WS process can die mid-session and stay dead until a human notices
(happened on 2026-04-29: died 12:21, dead 50 min until manual restart).

Two checks:
  1. Process check: is any python.exe running streaming/ws_runner.py?
     If NO during market hours -> spawn a new one (detached background).
  2. Freshness check: when last tick was received (via live_ticks.json
     generated_at). If > 120s stale during market hours -> kill + respawn.

Both writes to scheduler/watchdog_ws.log so we have an audit trail.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from logzero import logger

from common.market_hours import is_market_hours, now_ist

LIVE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "live_ticks.json"
BACKEND_DIR = Path(__file__).resolve().parent.parent
WS_RUNNER_REL = "streaming/ws_runner.py"

STALE_THRESHOLD_SEC = 120


def _find_ws_runner_pids() -> list[int]:
    """Return PIDs of any python(.exe|w.exe) whose command line includes ws_runner.py."""
    pids: list[int] = []
    try:
        out = subprocess.run(
            ["wmic", "process", "where",
             "name='python.exe' or name='pythonw.exe'",
             "get", "ProcessId,CommandLine", "/format:csv"],
            capture_output=True, text=True, timeout=10,
        )
        for line in out.stdout.splitlines():
            if "ws_runner.py" not in line:
                continue
            parts = line.rsplit(",", 1)
            if len(parts) >= 2:
                try:
                    pids.append(int(parts[-1].strip()))
                except ValueError:
                    pass
    except Exception as e:
        logger.warning(f"PID lookup failed: {e}")
    return pids


def _kill_pid(pid: int) -> bool:
    try:
        subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                       capture_output=True, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"taskkill PID {pid} failed: {e}")
        return False


def _launch_ws_runner() -> int:
    """Detached background launch. Returns child PID (0 on failure)."""
    try:
        DETACHED = 0x00000008
        NEW_PG = 0x00000200
        proc = subprocess.Popen(
            [sys.executable, WS_RUNNER_REL],
            cwd=str(BACKEND_DIR),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED | NEW_PG,
            close_fds=True,
        )
        return proc.pid
    except Exception as e:
        logger.error(f"Failed to launch ws_runner: {e}")
        return 0


def _ticks_age_sec() -> float | None:
    """Seconds since last tick. Robust to mixed naive/aware ISO timestamps."""
    if not LIVE_PATH.exists():
        return None
    try:
        data = json.loads(LIVE_PATH.read_text(encoding="utf-8"))
        gen_str = data.get("generated_at")
        if not gen_str:
            return None
        if gen_str.endswith("Z"):
            gen_str = gen_str[:-1]
        gen = datetime.fromisoformat(gen_str)
        if gen.tzinfo is not None:
            gen = gen.replace(tzinfo=None)
        now_local = now_ist().replace(tzinfo=None)
        return max(0.0, (now_local - gen).total_seconds())
    except Exception as e:
        logger.debug(f"ticks age read failed: {e}")
        return None


def main():
    if not is_market_hours():
        return

    pids = _find_ws_runner_pids()
    age = _ticks_age_sec()

    if not pids:
        logger.warning("ws_runner NOT RUNNING during market hours -- launching")
        new_pid = _launch_ws_runner()
        if new_pid:
            logger.info(f"ws_runner launched, PID {new_pid}")
        return

    if age is None:
        logger.info(f"ws_runner up (PIDs {pids}); live_ticks.json not yet present")
        return

    if age > STALE_THRESHOLD_SEC:
        logger.warning(f"ws_runner up (PIDs {pids}) but ticks {age:.0f}s stale -- restarting")
        for pid in pids:
            _kill_pid(pid)
        time.sleep(2)
        new_pid = _launch_ws_runner()
        if new_pid:
            logger.info(f"ws_runner relaunched, PID {new_pid}")
        try:
            from alerts.channels import dispatch
            dispatch("warning", "WS restart by watchdog", f"Ticks were {age:.0f}s stale")
        except Exception:
            pass
        return

    logger.info(f"WS healthy: PIDs {pids}, last tick {age:.0f}s ago")


if __name__ == "__main__":
    main()

# Install the WebSocket streamer as a Windows service (NSSM)

## One-time setup

1. **Download NSSM** (free): https://nssm.cc/download
   Extract `nssm.exe` somewhere in PATH (e.g., `c:\tools\nssm.exe`).

2. **Install the service** — run these in an **Administrator** Command Prompt:

```cmd
nssm install ArthaWS "c:\Users\ayush\AppData\Local\Python\pythoncore-3.14-64\python.exe" "c:\trading\backend\streaming\ws_runner.py"
nssm set ArthaWS AppDirectory "c:\trading\backend"
nssm set ArthaWS AppStdout "c:\trading\logs\ws_stdout.log"
nssm set ArthaWS AppStderr "c:\trading\logs\ws_stderr.log"
nssm set ArthaWS AppRotateFiles 1
nssm set ArthaWS AppRotateBytes 5242880
nssm set ArthaWS Start SERVICE_AUTO_START
nssm set ArthaWS AppExit Default Restart
nssm set ArthaWS AppRestartDelay 10000
nssm set ArthaWS DisplayName "Artha WebSocket Streamer"
nssm set ArthaWS Description "Live tick streaming from Angel SmartAPI"
nssm start ArthaWS
```

(Adjust the Python path if yours is elsewhere -- run `where python` to find it.)

## Verify

```cmd
nssm status ArthaWS
```
Should print `SERVICE_RUNNING`.

Or check logs:
```
type c:\trading\logs\ws.log
type c:\trading\logs\ws_stdout.log
```

## Manage

| Action | Command |
|---|---|
| Stop | `nssm stop ArthaWS` |
| Start | `nssm start ArthaWS` |
| Restart | `nssm restart ArthaWS` |
| Remove | `nssm remove ArthaWS confirm` |
| Check config | `nssm dump ArthaWS` |

## What it does

- Starts automatically at Windows boot
- Connects to Angel WebSocket during market hours (09:15-15:30 IST)
- Sleeps outside market hours (no API calls)
- Re-logs in daily at 05:00 IST (token refresh)
- Re-subscribes every 60s to include fresh paper positions
- Writes ticks to `data/live_ticks.json` + pushes to Vercel Redis
- Auto-restarts if crashed (10s delay)

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ArthaWS` fails to start | Check `c:\trading\logs\ws_stderr.log` for Python errors |
| Angel login fails | Verify TOTP clock; re-check `backend/config.py` creds |
| No ticks received | Check SmartWebSocketV2 installed: `pip show smartapi-python` |
| High CPU | Normal — ticks arrive every few ms for each subscribed symbol |

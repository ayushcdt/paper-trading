@echo off
REM WebSocket watchdog. Runs every 5 min during market hours.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d c:\trading\backend
python scripts\watchdog_ws.py >> "%LOGDIR%\watchdog_ws.log" 2>&1
exit /b %errorlevel%

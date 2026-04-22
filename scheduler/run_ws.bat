@echo off
REM WebSocket streamer runner. Launched at logon, runs in background.
REM Self-handles market-hours pause, reconnection, daily re-login.
set LOGDIR=c:\trading\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set OMP_NUM_THREADS=1

cd /d c:\trading\backend
python streaming\ws_runner.py >> "%LOGDIR%\ws.log" 2>&1

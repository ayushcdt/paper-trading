@echo off
REM Daily EOD summary -> Telegram. Runs at 15:35 IST after market close.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d c:\trading\backend
python -m scripts.daily_summary >> "%LOGDIR%\daily_summary.log" 2>&1
exit /b %errorlevel%

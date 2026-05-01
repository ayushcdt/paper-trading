@echo off
REM Claude Autotrade scout. Runs every 30 min during market hours.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d c:\trading\backend
python scripts\claude_autotrade.py >> "%LOGDIR%\claude_autotrade.log" 2>&1
exit /b %errorlevel%

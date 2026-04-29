@echo off
REM Pre-open signal generator. Runs at 08:30 IST weekdays.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d c:\trading\backend
python scripts\preopen_signals.py >> "%LOGDIR%\preopen_signals.log" 2>&1
exit /b %errorlevel%

@echo off
REM Morning F&O briefing -> Telegram. Runs 08:32 IST weekdays.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

cd /d c:\trading\backend
python scripts\morning_briefing.py >> "%LOGDIR%\morning_briefing.log" 2>&1
exit /b %errorlevel%

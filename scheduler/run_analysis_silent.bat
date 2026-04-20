@echo off
REM Silent runner invoked by Task Scheduler. Logs go to logs\scheduler\
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~-4,4%-%date:~-7,2%-%date:~-10,2%_%time:~0,2%-%time:~3,2%
set TS=%TS: =0%

cd /d c:\trading\backend
python generate_analysis.py >> "%LOGDIR%\run_%TS%.log" 2>&1
exit /b %errorlevel%

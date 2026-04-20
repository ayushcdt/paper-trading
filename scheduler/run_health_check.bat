@echo off
REM Nightly variant health check. Logs to logs\scheduler\
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~-4,4%-%date:~-7,2%-%date:~-10,2%_%time:~0,2%-%time:~3,2%
set TS=%TS: =0%

set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set OMP_NUM_THREADS=1

cd /d c:\trading\backend
python scripts\nightly_health_check.py >> "%LOGDIR%\health_%TS%.log" 2>&1
exit /b %errorlevel%

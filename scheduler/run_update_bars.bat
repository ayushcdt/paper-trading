@echo off
REM Daily bar updater -- 16:00 IST post-close.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set TS=%date:~-4,4%-%date:~-7,2%-%date:~-10,2%_%time:~0,2%-%time:~3,2%
set TS=%TS: =0%

set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set OMP_NUM_THREADS=1

cd /d c:\trading\backend
python scripts\update_bars.py >> "%LOGDIR%\update_bars_%TS%.log" 2>&1
exit /b %errorlevel%

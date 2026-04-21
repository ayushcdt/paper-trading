@echo off
REM Intraday mark-to-market. Runs every 15 min during market hours.
REM Script self-skips outside 09:15-15:30 IST so we can schedule it freely.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set OMP_NUM_THREADS=1

cd /d c:\trading\backend
python scripts\mark_to_market.py >> "%LOGDIR%\mtm.log" 2>&1
exit /b %errorlevel%

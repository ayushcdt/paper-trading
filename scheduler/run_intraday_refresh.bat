@echo off
REM Intraday refresh: market data + regime + paper MTM. Every 15 min.
REM Self-skips outside 09:15-15:30 IST.
set LOGDIR=c:\trading\logs\scheduler
if not exist "%LOGDIR%" mkdir "%LOGDIR%"

set OPENBLAS_NUM_THREADS=1
set MKL_NUM_THREADS=1
set OMP_NUM_THREADS=1

cd /d c:\trading\backend
python scripts\intraday_refresh.py >> "%LOGDIR%\intraday.log" 2>&1
exit /b %errorlevel%

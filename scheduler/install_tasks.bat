@echo off
REM ============================================================
REM Install Artha auto-refresh tasks in Windows Task Scheduler
REM Run this ONCE as Administrator (right-click -> Run as admin)
REM
REM Schedule:
REM   09:00 IST daily  -- pre-market: macro setup + picks
REM   15:45 IST daily  -- post-close: next-day stance + picks
REM   17:15 IST daily  -- variant health check (V3 adaptive)
REM   Monthly (1st)    -- parameter recalibration (V3 Phase 3)
REM ============================================================

setlocal

set ANALYSIS=c:\trading\scheduler\run_analysis_silent.bat
set HEALTH=c:\trading\scheduler\run_health_check.bat
set RECAL=c:\trading\scheduler\run_recalibrate.bat

echo [1/4] Installing Artha-Premarket (09:00 daily)...
schtasks /Create /F /TN "Artha-Premarket" /TR "%ANALYSIS%" /SC DAILY /ST 09:00 /RL HIGHEST

echo [2/4] Installing Artha-Postclose (15:45 daily)...
schtasks /Create /F /TN "Artha-Postclose" /TR "%ANALYSIS%" /SC DAILY /ST 15:45 /RL HIGHEST

echo [3/4] Installing Artha-HealthCheck (17:15 daily)...
schtasks /Create /F /TN "Artha-HealthCheck" /TR "%HEALTH%" /SC DAILY /ST 17:15 /RL HIGHEST

echo [4/4] Installing Artha-Recalibrate (monthly, 1st at 18:00)...
schtasks /Create /F /TN "Artha-Recalibrate" /TR "%RECAL%" /SC MONTHLY /D 1 /ST 18:00 /RL HIGHEST

echo.
echo All tasks installed. Verify with:
echo   schtasks /Query /TN Artha-Premarket /V
echo   schtasks /Query /TN Artha-Postclose /V
echo   schtasks /Query /TN Artha-HealthCheck /V
echo   schtasks /Query /TN Artha-Recalibrate /V
echo.
echo Remove any with:
echo   schtasks /Delete /TN Artha-Premarket /F
echo   schtasks /Delete /TN Artha-Postclose /F
echo   schtasks /Delete /TN Artha-HealthCheck /F
echo   schtasks /Delete /TN Artha-Recalibrate /F

pause

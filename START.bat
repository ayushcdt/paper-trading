@echo off
title Artha Trading Dashboard
color 0A

echo.
echo  ======================================
echo        ARTHA 2.0 - One Click Start
echo  ======================================
echo.

:: Check if PIN is set
findstr /C:"pin\": \"\"" "c:\trading\backend\config.py" >nul 2>&1
if %errorlevel%==0 (
    echo  [!] PIN not set. Opening config file...
    echo.
    echo  Please add your Angel One PIN in the config file
    echo  Then save and run this again.
    echo.
    notepad c:\trading\backend\config.py
    pause
    exit /b
)

echo  [1/3] Installing dependencies...
cd /d c:\trading\backend
python -m pip install -r requirements.txt -q 2>nul

echo  [2/3] Generating analysis...
echo.
python generate_analysis.py

echo.
echo  [3/3] Opening dashboard...
start https://artha-dashboard.vercel.app

echo.
echo  ======================================
echo         DONE! Dashboard opened.
echo  ======================================
echo.
pause

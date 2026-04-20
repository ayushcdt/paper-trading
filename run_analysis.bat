@echo off
echo ============================================
echo   ARTHA 2.0 - Stock Analysis Generator
echo ============================================
echo.

cd /d c:\trading\backend

echo Installing dependencies (first time only)...
pip install -r requirements.txt -q

echo.
echo Running analysis...
echo.

python generate_analysis.py

echo.
echo ============================================
echo   Analysis Complete!
echo   Check c:\trading\data\ for JSON files
echo ============================================
echo.

pause

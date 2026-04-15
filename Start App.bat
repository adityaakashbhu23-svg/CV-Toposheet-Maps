@echo off
title CV-Toposheet Map Digitization System
cd /d "%~dp0"
color 0B

echo.
echo  ====================================================
echo    CV-Toposheet Map Digitization System
echo    Historical Survey of India - Feature Extraction
echo  ====================================================
echo.

REM ── Check Python ────────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python is not installed on this computer.
    echo.
    echo  Please download and install Python 3.10 or later from:
    echo  https://www.python.org/downloads/
    echo.
    echo  Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo  Python found:
python --version
echo.

REM ── Install dependencies ─────────────────────────────────────────────────────
echo  Installing / checking dependencies (first run may take a minute)...
echo.
pip install flask --quiet --disable-pip-version-check
pip install -r requirements.txt --quiet --disable-pip-version-check
echo.
echo  Dependencies ready.
echo.

REM ── Open browser (slight delay so Flask can start first) ────────────────────
start /min "" cmd /c "ping -n 4 127.0.0.1 >nul && start http://localhost:5000"

REM ── Start Flask ──────────────────────────────────────────────────────────────
echo  ====================================================
echo    Server starting at:  http://localhost:5000
echo    (Browser will open automatically in 3 seconds)
echo.
echo    To stop the server: close this window
echo  ====================================================
echo.
python app.py

echo.
echo  Server stopped.
pause

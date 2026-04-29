@echo off
cd /d "%~dp0"

if not exist "dist\CVToposheet\CVToposheet.exe" (
    echo.
    echo  ERROR: App not built yet.
    echo  Please run  _build\build_exe.bat  first to compile the app.
    echo.
    pause
    exit /b 1
)

start "" "dist\CVToposheet\CVToposheet.exe"

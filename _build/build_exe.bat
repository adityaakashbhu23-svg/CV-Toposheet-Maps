@echo off
title CV-Toposheet EXE Builder
REM ── Always run from the PROJECT ROOT (parent of _build\) ──────────────────
cd /d "%~dp0\.."
color 0A

echo.
echo  ====================================================
echo    CV-Toposheet  -  Build Standalone EXE
echo    All build files are isolated inside _build\
echo  ====================================================
echo.
echo  Output:  _build\dist\CVToposheet\CVToposheet.exe
echo  Your project files are NOT touched.
echo.

REM ── Check Python ──────────────────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo  ERROR: Python not found. Install Python 3.10+ and try again.
    pause & exit /b 1
)
echo  Python found:
python --version
echo.

REM ── Install / upgrade PyInstaller ─────────────────────────────────────────
echo  Checking PyInstaller...
python -m pip install pyinstaller --quiet --upgrade
if errorlevel 1 (
    echo  ERROR: Could not install PyInstaller.
    pause & exit /b 1
)
echo  PyInstaller ready.
echo.

REM ── Build ─────────────────────────────────────────────────────────────────
echo  Building...  (first run can take 5-15 min -- easyocr/torch are large)
echo.
python -m PyInstaller _build\build_exe.spec --clean --noconfirm ^
    --workpath _build\work ^
    --distpath _build\dist

if errorlevel 1 (
    echo.
    echo  =============================================
    echo    BUILD FAILED  -  see errors above
    echo  =============================================
    pause & exit /b 1
)

REM ── Success ───────────────────────────────────────────────────────────────
echo.
echo  ============================================================
echo    BUILD SUCCESSFUL!
echo  ============================================================
echo.
echo  Location : _build\dist\CVToposheet\CVToposheet.exe
echo.
echo  IMPORTANT: copy your .env before running:
echo    copy .env _build\dist\CVToposheet\.env
echo.
echo  Run:  _build\dist\CVToposheet\CVToposheet.exe
echo  A browser will open at http://127.0.0.1:5000 automatically.
echo.
pause

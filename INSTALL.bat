@echo off
setlocal EnableExtensions
title Pixiv NAI Gallery Installer
cd /d "%~dp0"
set "GALLERY_INSTALL_ROOT=%~dp0"

echo ============================================
echo   Pixiv NAI Gallery - One-click installer
echo   Pixiv discovery - strict local NAI checks
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Install Python 3.11, 3.12, or 3.13 from:
    echo   https://www.python.org/downloads/
    echo Select "Add Python to PATH" during installation.
    echo.
    if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
    endlocal
    exit /b 1
)

for /f "tokens=2" %%V in ('python --version 2^>^&1') do set "PY_VER=%%V"
echo [OK] Python %PY_VER% detected.
python -c "import sys; assert sys.version_info.major == 3 and sys.version_info.minor in [11, 12, 13]"
if errorlevel 1 (
    echo [ERROR] Python 3.11, 3.12, or 3.13 is required. Found %PY_VER%.
    if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
    endlocal
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo [..] Creating the local Python environment. This may take a few minutes...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the Python environment.
        if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
        endlocal
        exit /b 1
    )
    echo [OK] Local Python environment created.
) else (
    echo [OK] Local Python environment already exists.
)

set "REQUIREMENTS_FILE=requirements.txt"
if exist "requirements.lock.txt" set "REQUIREMENTS_FILE=requirements.lock.txt"
if not exist "%REQUIREMENTS_FILE%" (
    echo [ERROR] Dependency file is missing: %REQUIREMENTS_FILE%
    if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
    endlocal
    exit /b 2
)
echo [..] Installing dependencies from %REQUIREMENTS_FILE%...
.venv\Scripts\python.exe -m pip install -r "%REQUIREMENTS_FILE%" -q
if errorlevel 1 (
    echo [ERROR] Dependency installation failed. Check the network and retry.
    if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
    endlocal
    exit /b 1
)
echo [OK] Dependencies installed.

if defined GALLERY_NONINTERACTIVE echo [INFO] Non-interactive install: skipping desktop shortcuts.
if defined GALLERY_BOOTSTRAP echo [INFO] Bootstrap mode: returning to the one-click launcher.
if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP echo [..] Creating desktop shortcuts...
if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0scripts\create_desktop_shortcuts.ps1"
if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP if errorlevel 1 echo [WARN] Shortcut creation failed. You can create it manually.
if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP if not errorlevel 1 echo [OK] Desktop shortcut created: Pixiv NAI Gallery

echo.
if defined GALLERY_BOOTSTRAP (
    echo [INFO] Bootstrap mode: the launcher will start the Gallery now.
) else if defined GALLERY_NONINTERACTIVE (
    echo [INFO] Non-interactive install: skipping automatic launch.
) else if /I "%GALLERY_SKIP_LAUNCH%"=="1" (
    echo [INFO] Automatic launch skipped by GALLERY_SKIP_LAUNCH.
) else (
    echo [..] Starting Pixiv NAI Gallery...
    echo.
    if exist "%~dp0START_GALLERY.bat" (
        start "" "%~dp0START_GALLERY.bat"
    ) else if exist "%~dp0start_gallery.bat" (
        start "" "%~dp0start_gallery.bat"
    )
)

echo.
echo ============================================
echo   Installation complete
echo   Open: http://127.0.0.1:8797/
echo   Main path: Gallery - NAI Tags - Pixiv Intake
echo ============================================
echo.
if not defined GALLERY_NONINTERACTIVE if not defined GALLERY_BOOTSTRAP pause
endlocal
exit /b 0

@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

if not exist "logs" mkdir "logs"
if not exist ".venv\Scripts\python.exe" (
  python -m venv .venv
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
start "aitag-crawler" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_crawl_background.ps1"
echo Crawler supervisor started. Progress: http://127.0.0.1:8797/progress
pause
endlocal

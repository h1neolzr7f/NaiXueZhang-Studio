@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting QQ-group crawler (watch local folders)...
if exist ".venv\Scripts\python.exe" (
  start "aitag-qq-crawler" /min ".venv\Scripts\python.exe" -u crawler_qq.py --watch
) else (
  start "aitag-qq-crawler" /min python -u crawler_qq.py --watch
)
echo QQ crawler started in watch mode.
echo Heartbeat: logs\crawler-qq-heartbeat.json
if not defined GALLERY_NONINTERACTIVE pause

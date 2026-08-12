@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "pixiv-nai-crawler" /min powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run_crawl_background.ps1"
echo Pixiv NAI crawler supervisor started. Progress: http://127.0.0.1:8797/progress
if not defined GALLERY_NONINTERACTIVE pause

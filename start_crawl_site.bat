@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo The legacy AITag site crawler is disabled.
echo Starting Pixiv direct, verified NAI intake instead...
set GALLERY_NONINTERACTIVE=1
call "%~dp0start_crawl.bat"
echo Pixiv NAI crawler started.
echo Progress: http://127.0.0.1:8797/progress
echo Status API: http://127.0.0.1:8797/api/crawler/status
if not defined GALLERY_NONINTERACTIVE pause

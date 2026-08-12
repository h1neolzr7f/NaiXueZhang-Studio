@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting the default crawler: Pixiv direct, verified NAI only.
set GALLERY_NONINTERACTIVE=1
call "%~dp0start_crawl.bat"
echo Done. Open http://127.0.0.1:8797/progress
if not defined GALLERY_NONINTERACTIVE pause

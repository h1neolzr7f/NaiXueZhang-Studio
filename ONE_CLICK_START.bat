@echo off
setlocal EnableExtensions
cd /d "%~dp0"
call "%~dp0START_GALLERY.bat" %*
exit /b %errorlevel%

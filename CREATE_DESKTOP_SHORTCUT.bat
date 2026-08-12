@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "TARGET=%~dp0START_GALLERY.bat"
set "LINK=%USERPROFILE%\Desktop\Pixiv NAI Gallery.lnk"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$ws=New-Object -ComObject WScript.Shell; $s=$ws.CreateShortcut('%LINK%'); $s.TargetPath='%TARGET%'; $s.WorkingDirectory='%~dp0'; $s.IconLocation='%~dp0web\favicon.ico'; $s.Description='Start Pixiv NAI Gallery'; $s.Save()"

if errorlevel 1 (
  echo Failed to create desktop shortcut.
  pause
  exit /b 1
)

echo Desktop shortcut created:
echo %LINK%
pause
endlocal

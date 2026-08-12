@echo off
rem Install a downloaded update beside the running EXE and restart it.
rem Usage: updater.bat <exe-path> [extra-args]
setlocal
set "EXE=%~1"
set "UPD=%~dp0..\\update\\update.exe"
if not exist "%UPD%" (
  echo No update package found.
  exit /b 1
)
set "EXEDIR=%~dp1"
timeout /t 3 /nobreak >nul
rem Keep exactly one rollback copy of the current EXE before overwriting it.
if exist "%EXE%" (
  copy /y "%EXE%" "%EXE%.bak" >nul
  if errorlevel 1 echo Warning: could not back up "%EXE%" before update.
)
copy /y "%UPD%" "%EXE%" >nul
if errorlevel 1 (
  echo Failed to replace "%EXE%".
  exit /b 1
)
del /q "%UPD%"
start "" "%EXE%" %2 %3 %4 %5
exit /b 0

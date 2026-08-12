@echo off
rem Shared Python selector for every user-facing launcher.
rem Priority: bundled runtime, project virtual environment, then global Python.
set "GALLERY_PYTHON_EXE="
set "GALLERY_PYTHON_MODE="
for %%I in ("%~dp0..") do set "_GALLERY_RUNTIME_ROOT=%%~fI"

if exist "%_GALLERY_RUNTIME_ROOT%\runtime\python.exe" (
  set "GALLERY_PYTHON_EXE=%_GALLERY_RUNTIME_ROOT%\runtime\python.exe"
  set "GALLERY_PYTHON_MODE=bundled portable runtime"
  set "_GALLERY_RUNTIME_ROOT="
  exit /b 0
)

if exist "%_GALLERY_RUNTIME_ROOT%\.venv\Scripts\python.exe" (
  set "GALLERY_PYTHON_EXE=%_GALLERY_RUNTIME_ROOT%\.venv\Scripts\python.exe"
  set "GALLERY_PYTHON_MODE=local environment"
  set "_GALLERY_RUNTIME_ROOT="
  exit /b 0
)

where python.exe >nul 2>nul
if errorlevel 1 (
  set "_GALLERY_RUNTIME_ROOT="
  exit /b 2
)
for /f "delims=" %%P in ('where python.exe 2^>nul') do if not defined GALLERY_PYTHON_EXE set "GALLERY_PYTHON_EXE=%%P"
if not defined GALLERY_PYTHON_EXE (
  set "_GALLERY_RUNTIME_ROOT="
  exit /b 2
)
set "GALLERY_PYTHON_MODE=global Python"
set "_GALLERY_RUNTIME_ROOT="
exit /b 0

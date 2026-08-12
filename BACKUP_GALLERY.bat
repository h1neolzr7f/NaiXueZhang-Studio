@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
call "%~dp0scripts\select_python_runtime.bat"
if errorlevel 1 (
  echo [错误] 未找到包内运行时、本地环境或全局 Python。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 2
)
echo [..] 正在创建不含账号凭据的图库快照...
"%GALLERY_PYTHON_EXE%" gallery_snapshot.py create
if errorlevel 1 (
  echo [错误] 备份失败。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 1
)
echo [完成] 快照已保存到 backups 目录。
if not defined GALLERY_NONINTERACTIVE pause
endlocal

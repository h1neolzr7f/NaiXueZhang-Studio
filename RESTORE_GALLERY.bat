@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
if "%~1"=="" (
  echo 用法：先关闭图库程序，再把快照 ZIP 拖到 RESTORE_GALLERY.bat 上。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 2
)
call "%~dp0scripts\select_python_runtime.bat"
if errorlevel 1 (
  echo [错误] 未找到包内运行时、本地环境或全局 Python。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 2
)
echo [警告] 即将用快照恢复数据库与图库资产；本地账号凭据不会改变。
choice /C YN /N /M "确认恢复？[Y/N] "
if errorlevel 2 exit /b 3
"%GALLERY_PYTHON_EXE%" gallery_snapshot.py restore "%~1" --confirm
if errorlevel 1 (
  echo [错误] 恢复失败，原状态已回滚。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 1
)
echo [完成] 图库快照恢复成功。
if not defined GALLERY_NONINTERACTIVE pause
endlocal

@echo off
setlocal EnableExtensions
chcp 65001 >nul
title Pixiv 登录获取 Token
cd /d "%~dp0"
color 0B
echo.
echo ========================================================
echo   Pixiv 浏览器登录 - 自动获取 refresh_token
echo ========================================================
echo.
echo 稍后会弹出 Chrome 或 Edge，请完成 Pixiv 登录。
echo Token 只保存到本机 data\pixiv_accounts.local.json。
echo.
call "%~dp0scripts\select_python_runtime.bat"
if errorlevel 1 (
  echo [失败] 未找到包内运行时、本地环境或全局 Python。
  if not defined GALLERY_NONINTERACTIVE pause
  exit /b 2
)
"%GALLERY_PYTHON_EXE%" "%~dp0scripts\get_pixiv_token.py"
set "TOKEN_RESULT=%errorlevel%"
echo.
if not "%TOKEN_RESULT%"=="0" (
  echo [失败] 请阅读上方错误信息后重试。
) else (
  echo [完成] 可以回到 Pixiv 起号页检测登录。
)
if not defined GALLERY_NONINTERACTIVE pause
endlocal & exit /b %TOKEN_RESULT%

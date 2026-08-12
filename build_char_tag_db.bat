@echo off
cd /d "%~dp0"
echo 联网拉取 D 站识别库（全角色+版权+外貌，无画师）并合并本地图库...
echo 约需下载 360MB，请保持网络畅通。
python scripts\build_char_tag_db.py --fetch-recognition
echo.
echo 仅离线重建：python scripts\build_char_tag_db.py
echo 仅明日方舟：python scripts\build_char_tag_db.py --fetch-arknights --fetch-arknights-full
pause

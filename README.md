# Nai 学长工作室 v1.4 稳定保留线

[English](README_EN.md) · [当前 v1.5+ 主线](https://github.com/h1neolzr7f/NaiXueZhang-Studio-Upgrade) · [下载 v1.4.0](https://github.com/h1neolzr7f/NaiXueZhang-Studio/releases/tag/v1.4.0)

本仓库保留 v1.4.0 的源码、界面和历史 Release。它适合需要继续使用旧界面的用户，也方便对照项目的版本演进。新安装和新功能统一在 [NaiXueZhang-Studio-Upgrade](https://github.com/h1neolzr7f/NaiXueZhang-Studio-Upgrade) 维护。

<p align="center">
  <img src="docs/screenshots/01-gallery.png" alt="v1.4 本地图库界面" width="900">
</p>

## v1.4 包含什么

- 基于 SQLite FTS 的本地图库和标签检索；
- NovelAI 元数据检查、Prompt 整理和角色替换；
- 生成任务持久化、失败恢复和多 Token 调度；
- 超分、打码、元数据清理和发布前检查；
- Pixiv 草稿准备、来源记录和可恢复清理；
- Windows DPAPI 凭据保护与 localhost 写操作会话令牌。

这条版本线已冻结。除明确的安全或数据损坏问题外，功能请求请提交到当前主线。

## 快速开始

Windows 用户可下载并完整解压 [v1.4.0 一键包](https://github.com/h1neolzr7f/NaiXueZhang-Studio/releases/tag/v1.4.0)，然后运行包内启动脚本。本地服务默认打开 `http://127.0.0.1:8797/`。

从源码运行：

```powershell
git clone https://github.com/h1neolzr7f/NaiXueZhang-Studio.git
cd NaiXueZhang-Studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.core.lock.txt
python server.py
```

源码环境以 Windows 10/11 和 Python 3.13 为准。第三方在线服务可能变更，旧版本不保证继续兼容新的接口或页面。

## 验证范围

```powershell
python -m pytest -q
python scripts/scan_sensitive.py
```

测试覆盖本地存储、路径边界、任务状态和关键前端契约；不会调用付费生成接口。真实 Token、Cookie、图库、数据库和运行日志不在源码或 Release 中。

## 项目边界

这是非官方项目，与 pixiv Inc.、NovelAI/Anlatan 或其他第三方服务没有隶属、授权或合作关系。使用者需要遵守服务条款、适用法律和第三方权利要求。详见 [DISCLAIMER.md](DISCLAIMER.md)、[RESPONSIBLE_USE.md](RESPONSIBLE_USE.md) 与 [SECURITY.md](SECURITY.md)。

代码采用 [MIT License](LICENSE)；代码许可不授予第三方图片、Prompt、角色、商标或平台数据的权利。

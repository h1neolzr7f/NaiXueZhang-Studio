<div align="center">

# 🐾 Nai学长工作室

### Pixiv NAI Gallery 的官方产品页与 Windows 下载入口

**本地图库 · NAI 元数据验证 · Prompt 资产 · 角色换角 · 批量生成 · 后处理 · Pixiv 发布**

[![Open Source](https://img.shields.io/badge/Source-Open-2ea44f?logo=github)](https://github.com/h1neolzr7f/pixiv-nai-gallery-public)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/blob/main/LICENSE)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows&logoColor=white)
![Local First](https://img.shields.io/badge/Privacy-Local--first-7A5AF8)

[下载 Windows 版本](https://github.com/h1neolzr7f/NaiXueZhang-Studio/releases) ·
[查看公开源码](https://github.com/h1neolzr7f/pixiv-nai-gallery-public) ·
[提交 Issue](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/issues) ·
[参与贡献](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/blob/main/CONTRIBUTING.md)

</div>

> [!TIP]
> **源码现已公开。** 完整源代码、测试、开发文档和贡献入口位于 [`h1neolzr7f/pixiv-nai-gallery-public`](https://github.com/h1neolzr7f/pixiv-nai-gallery-public)，采用 MIT License。本仓库继续作为产品展示、截图与 Windows Release 下载入口。

> [!IMPORTANT]
> 本项目与 pixiv Inc.、NovelAI（Anlatan Inc.）及其他第三方平台不存在隶属、授权或合作关系。使用者应自行确认访问、下载、处理和发布行为符合适用法律、平台规则及第三方权利要求。完整责任边界见公开源码仓库中的 [DISCLAIMER.md](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/blob/main/DISCLAIMER.md) 与 [RESPONSIBLE_USE.md](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/blob/main/RESPONSIBLE_USE.md)。

## 它是什么？

Nai学长工作室是一套本地优先的 **NovelAI 插画素材管理与创作生产系统**。它把素材发现、NAI 元数据验证、Prompt 研究、角色换角、批量生成、后处理和发布准备整理到同一个可恢复工作流中。

它不只是一个下载器，也不只是一个生图界面，而是为长期、高频创作准备的本地工作台。

## ✨ 核心能力

- **Pixiv 素材发现**：支持搜索、作者与榜单等候选来源，并对图片逐页验证 NovelAI 元数据；公开页面通道可直接使用，账号通道可按需配置。
- **本地图库与标签索引**：按角色、作品、画师、动作、服装、场景和构图组织大规模素材。
- **AI 智能管家**：通过自然语言查询图库、诊断运行状态、组织任务，并在明确权限边界内辅助执行本地工作流。
- **NAI 批量导演**：批量执行去背景、线稿、草图、上色、表情修改与画面清理等 Director Tools 工作流。
- **换角与换画风**：从已有作品提取可复用结构，替换角色或风格后进入生成队列。
- **多 Token 生成队列**：任务持久化、并发调度、失败恢复、结果归档与生产状态管理。
- **后处理闭环**：超分、打码、元数据处理、文案准备和发布前检查集中管理。
- **来源与责任记录**：保存作者、作品链接、源状态和作者声明，并支持导出来源清单。

## 🖼️ 界面截图

| 图库首页 | 素材发现 | 批量导演 |
|---|---|---|
| ![home](screenshots/home.png) | ![intake](screenshots/intake.png) | ![director](screenshots/director.png) |

> 截图使用干净演示环境，不包含私人图库、账号、Token 或本地运行数据。

## 🚀 下载使用

从 [Releases](https://github.com/h1neolzr7f/NaiXueZhang-Studio/releases) 下载最新 Windows 便携包：

1. 下载并完整解压 ZIP；
2. 双击发行包内的启动脚本；
3. 浏览器会自动打开本地界面；
4. 数据默认保存在程序目录的 `data/` 中。

建议同时核对 Release 页面公布的 Commit SHA 与 SHA-256。不要运行来源不明、哈希不一致的第三方修改包。

## 💻 从源码运行

源码仓库：

```text
https://github.com/h1neolzr7f/pixiv-nai-gallery-public
```

```powershell
git clone https://github.com/h1neolzr7f/pixiv-nai-gallery-public.git
cd pixiv-nai-gallery-public
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.core.lock.txt
python server.py
```

完整的依赖、测试、安全说明和贡献规则以公开源码仓库为准。

## 🔐 隐私与安全

- 本地服务默认仅监听 `127.0.0.1`；
- 用户图库、Prompt、生成历史和本地数据库不上传到项目服务器；
- NovelAI Token 与 Pixiv refresh token 在 Windows 上通过 DPAPI 加密落盘；
- Release 不应包含用户图片、数据库、Cookie、Token、缓存或私人日志；
- 写操作使用本次启动生成的本地会话令牌；
- 官方更新包使用 HTTPS 与 SHA-256 完整性校验。

安全问题请通过公开源码仓库的 [Security 页面](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/security) 私下报告，不要在公开 Issue 中粘贴凭据或完整利用细节。

## 🧭 两个仓库的分工

| 仓库 | 用途 |
|---|---|
| [`NaiXueZhang-Studio`](https://github.com/h1neolzr7f/NaiXueZhang-Studio) | 产品介绍、演示截图与 Windows Releases |
| [`pixiv-nai-gallery-public`](https://github.com/h1neolzr7f/pixiv-nai-gallery-public) | 完整源码、MIT License、测试、Issue、PR 与开发文档 |

这样可以让普通用户更快找到下载，也让开发者在独立源码仓库中查看代码和参与贡献。

## 📜 License

项目源代码采用 [MIT License](https://github.com/h1neolzr7f/pixiv-nai-gallery-public/blob/main/LICENSE)，允许使用、修改和再分发，但必须保留许可证与版权声明。

MIT License 只授权本项目代码，不授予任何第三方图片、Prompt、角色、商标、账号数据或平台内容的权利。软件按现状提供；完整免责与责任边界以公开源码仓库文档为准。

---

<div align="center">

### 喜欢这个项目，可以给公开源码仓库点一个 ⭐

[⭐ Star the source repository](https://github.com/h1neolzr7f/pixiv-nai-gallery-public)

**Public Preview · 功能持续迭代，欢迎 Issue 与 PR。**

</div>

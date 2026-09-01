# NaiXueZhang Studio v1.4 stable line

[中文](README.md) · [Current v1.5+ line](https://github.com/h1neolzr7f/NaiXueZhang-Studio-Upgrade) · [Download v1.4.0](https://github.com/h1neolzr7f/NaiXueZhang-Studio/releases/tag/v1.4.0)

This repository preserves the v1.4.0 source, interface, and historical releases. New installations and feature work belong in the current Upgrade repository; this line is kept for users who need the frozen interface and for reviewing the project's evolution.

<p align="center">
  <img src="docs/screenshots/01-gallery.png" alt="v1.4 local gallery interface" width="900">
</p>

## Scope

- SQLite FTS local gallery and tag search
- NovelAI metadata checks, prompt organization, and character replacement
- Persistent generation jobs, recovery, and multi-token scheduling
- Upscaling, censorship, metadata cleanup, and pre-publication checks
- Pixiv draft preparation, provenance records, and recoverable cleanup
- Windows DPAPI credential protection and localhost write-session tokens

The line is frozen. Except for clear security or data-loss issues, please report feature requests against [NaiXueZhang-Studio-Upgrade](https://github.com/h1neolzr7f/NaiXueZhang-Studio-Upgrade).

## Run from source

The supported source environment is Windows 10/11 with Python 3.13.

```powershell
git clone https://github.com/h1neolzr7f/NaiXueZhang-Studio.git
cd NaiXueZhang-Studio
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.core.lock.txt
python server.py
```

The service listens on `127.0.0.1:8797` by default. Source and releases exclude user images, local databases, tokens, cookies, generation history, and runtime logs. Regression tests do not call paid generation APIs.

This is an unofficial project with no affiliation with pixiv Inc., NovelAI/Anlatan, or other third-party services. See [DISCLAIMER.md](DISCLAIMER.md), [RESPONSIBLE_USE.md](RESPONSIBLE_USE.md), and [SECURITY.md](SECURITY.md).

Code is available under the [MIT License](LICENSE). That license grants no rights to third-party images, prompts, characters, trademarks, or platform data.

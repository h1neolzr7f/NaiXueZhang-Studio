"""Product management and operational health helpers for Pixiv NAI Gallery.

This module intentionally stays read-only: it summarizes local project state for
the product operations dashboard without modifying user data, tokens, database,
or image files.
"""

from __future__ import annotations

import importlib.util
import os
import sqlite3
import subprocess
import time
from contextlib import closing
from pathlib import Path
from typing import Any

from paths import storage_paths


PRODUCT_STRATEGY: dict[str, Any] = {
    "name": "Pixiv NAI Gallery",
    "positioning": "本地优先的 AI 图像资产管理与再创作工作台",
    "one_liner": (
        "把 Pixiv/aitag 镜像、本地图库、标签搜索、prompt 复用、NAI 生图、"
        "后处理和发布流程整合成个人 AI 创作资产系统。"
    ),
    "target_user": "高频使用 Pixiv / aitag / NAI / 本地 AI 图片素材的个人创作者",
    "core_loop": [
        "采集/镜像",
        "入库/索引",
        "浏览/搜索/收藏",
        "Prompt/标签复用",
        "NAI 再创作",
        "后处理",
        "Pixiv 发布与复盘",
    ],
    "modules": [
        {
            "id": "gallery",
            "name": "Gallery",
            "value": "本地作品浏览、搜索、收藏、详情查看",
            "route": "/",
        },
        {
            "id": "studio",
            "name": "Studio",
            "value": "从图库作品导入 prompt/参考图并触发 NAI 生成",
            "route": "/studio",
        },
        {
            "id": "generated",
            "name": "Generated",
            "value": "管理本地生成图、来源作品、后处理状态",
            "route": "/generated",
        },
        {
            "id": "pipeline",
            "name": "Pipeline",
            "value": "打码、放大、清理、发布前处理队列",
            "route": "/generated",
        },
        {
            "id": "publisher",
            "name": "Publisher",
            "value": "Pixiv 账号、候选图、发布历史和数据复盘",
            "route": "/pixiv",
        },
    ],
    "roadmap": [
        {
            "phase": "P0",
            "title": "稳态化与产品运营驾驶舱",
            "outcomes": [
                "README / 产品定位 / 路线图落盘",
                "健康检查、数据规模、Git 风险可视化",
                "启动脚本与核心 API 可验证",
            ],
        },
        {
            "phase": "P1",
            "title": "Gallery 核心工程化",
            "outcomes": [
                "拆分 web/app.js 和 db.py 的高耦合职责",
                "统一前端 API client",
                "搜索、详情、收藏、月榜建立回归测试",
            ],
        },
        {
            "phase": "P2",
            "title": "AI 资产能力增强",
            "outcomes": [
                "Prompt/negative prompt/artist tag/角色 tag 专题视图",
                "相似作品推荐",
                "生成来源链路与批次管理",
            ],
        },
        {
            "phase": "P3",
            "title": "Pipeline 与 Pixiv 发布闭环",
            "outcomes": [
                "后处理任务可视化、失败重试、结果关联",
                "发布候选、标签/标题检查、发布历史和效果复盘",
            ],
        },
    ],
}


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _dir_stats(
    path: Path,
    *,
    max_files: int = 20000,
    time_budget_sec: float = 1.5,
) -> dict[str, Any]:
    """Return bounded directory stats without blocking the health endpoint.

    Large local galleries can contain tens of thousands of files on slow disks.
    The operations dashboard must remain responsive, so this scan is explicitly
    capped by file count and wall time. A later background index can provide
    exact totals; health checks should prefer fast, directional evidence.
    """
    files = 0
    total_bytes = 0
    exists = path.exists()
    if not exists:
        return {"exists": False, "files": 0, "bytes": 0, "complete": True}
    deadline = time.monotonic() + time_budget_sec
    stack = [path]
    try:
        while stack:
            if files >= max_files or time.monotonic() >= deadline:
                return {
                    "exists": True,
                    "files": files,
                    "bytes": total_bytes,
                    "complete": False,
                    "limit": {"max_files": max_files, "time_budget_sec": time_budget_sec},
                }
            current = stack.pop()
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            files += 1
                            total_bytes += entry.stat(follow_symlinks=False).st_size
                    except OSError:
                        continue
    except OSError:
        return {
            "exists": True,
            "files": files,
            "bytes": total_bytes,
            "complete": False,
            "error": "scan_failed",
        }
    return {"exists": True, "files": files, "bytes": total_bytes, "complete": True}


_IMPORTANT_TABLES = {"works", "work_images", "crawl_state"}


def _sqlite_stats(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False, "tables": {}, "bytes": 0}
    tables: dict[str, int] = {}
    skipped: list[str] = []
    try:
        # sqlite3.Connection's own context manager only commits/rolls back; it
        # does not close the OS handle. ``closing`` is the lifecycle boundary.
        with closing(sqlite3.connect(db_path)) as conn:
            rows = conn.execute(
                "select name from sqlite_master where type='table' order by name"
            ).fetchall()
            for (name,) in rows:
                table = str(name)
                if table.startswith("sqlite_"):
                    continue
                if table not in _IMPORTANT_TABLES:
                    skipped.append(table)
                    continue
                try:
                    tables[table] = int(
                        conn.execute(f'select count(*) from "{table}"').fetchone()[0]
                    )
                except sqlite3.Error:
                    tables[table] = -1
    except sqlite3.Error as exc:
        return {"exists": True, "bytes": db_path.stat().st_size, "error": str(exc)}
    return {
        "exists": True,
        "bytes": db_path.stat().st_size,
        "tables": tables,
        "skipped_tables": skipped,
    }


def _git_summary(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"available": False, "message": "not a git repository"}
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--short"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception as exc:
        return {"available": False, "message": str(exc)}
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    return {
        "available": proc.returncode == 0,
        "changed_files": len(lines),
        "dirty": bool(lines),
        "sample": lines[:20],
    }


def build_product_health(config: dict[str, Any], root: Path) -> dict[str, Any]:
    """Return a read-only operational health snapshot for the local app."""
    paths = storage_paths(config, root)
    data_dir = Path(paths["data_dir"])
    images_dir = Path(paths["images_dir"])
    generated_dir = Path(paths["generated_dir"])
    db_path = Path(paths["database_path"])
    web_dir = root / "web"

    dependencies = {
        "fastapi": _module_available("fastapi"),
        "uvicorn": _module_available("uvicorn"),
        "httpx": _module_available("httpx"),
        "PIL": _module_available("PIL"),
        "playwright": _module_available("playwright"),
    }
    checks = {
        "project_root": root.exists(),
        "config_json": (root / "config.json").exists(),
        "web_dir": web_dir.exists(),
        "data_dir": data_dir.exists(),
        "database": db_path.exists(),
        "images_dir": images_dir.exists(),
        "generated_dir": generated_dir.exists(),
        "start_gallery_bat": (root / "start_gallery.bat").exists(),
        "start_gallery_ps1": (root / "start_gallery.ps1").exists(),
        "required_dependencies": all(
            dependencies[name] for name in ("fastapi", "uvicorn", "httpx")
        ),
    }
    git = _git_summary(root)
    warnings = []
    if git.get("dirty"):
        warnings.append("Git 工作区存在大量未提交变更，升级应保持小步可验证。")
    if not checks["required_dependencies"]:
        warnings.append("核心运行依赖不完整，Gallery 可能无法启动。")
    if not checks["database"]:
        warnings.append("本地数据库缺失，图库搜索不可用。")

    return {
        "ok": all(checks.values()),
        "checks": checks,
        "warnings": warnings,
        "paths": paths,
        "dependencies": dependencies,
        "data": {
            "database": _sqlite_stats(db_path),
            "images": _dir_stats(images_dir),
            "generated": _dir_stats(generated_dir),
        },
        "git": git,
    }


def build_verification_plan() -> dict[str, Any]:
    project_root = str(Path(__file__).resolve().parent)
    return {
        "manual_urls": [
            "http://127.0.0.1:8797/",
            "http://127.0.0.1:8797/ops",
            "http://127.0.0.1:8797/api/config",
            "http://127.0.0.1:8797/api/product/strategy",
            "http://127.0.0.1:8797/api/product/health",
        ],
        "commands": [
            f'cd /d "{project_root}" && .\\START_GALLERY.bat',
            f'cd /d "{project_root}" && .venv\\Scripts\\python.exe -m unittest discover -s tests -p test_product_ops.py',
        ],
        "acceptance": [
            "/ops 页面能打开并展示定位、健康、路线图。",
            "/api/product/strategy 返回产品定位和 P0-P3 路线图。",
            "/api/product/health 返回 checks/data/git/dependencies 字段。",
            "现有 /、/studio、/generated、/settings 路由不受影响。",
        ],
    }

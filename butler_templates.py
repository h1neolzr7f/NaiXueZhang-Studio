"""Local reusable Butler commands; saving a template never executes it."""

from __future__ import annotations

import json
import secrets
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from butler.redaction import redact_text
from paths import data_dir


BUILTIN_TEMPLATES = (
    {
        "id": "builtin-local-audit",
        "label": "零 Token 图库体检",
        "prompt": "体检最近一个月的图库，只做本地状态和技术质量检查，不要调用识图。",
    },
    {
        "id": "builtin-queue-summary",
        "label": "检查待生成",
        "prompt": "查看待生成队列，按图库和作品列出数量与当前状态，不要开始生图。",
    },
    {
        "id": "builtin-postprocess",
        "label": "补齐后处理",
        "prompt": "按全局配置补跑所有缺失的后处理，完成后报告成功、失败和跳过数量。",
    },
    {
        "id": "builtin-pixiv-draft",
        "label": "整理投稿草稿",
        "prompt": "把最新 3 个生成系列整理成 Pixiv 多页投稿草稿，只准备标题、简介、标签和后处理，不要上传。",
    },
)


class ButlerTemplateStore:
    def __init__(self, path: str | Path, *, max_user_templates: int = 30) -> None:
        self.path = Path(path)
        self.max_user_templates = max(1, int(max_user_templates))
        self._lock = threading.RLock()

    def _load_locked(self) -> list[dict[str, Any]]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            return []
        rows = payload.get("templates") if isinstance(payload, dict) else None
        return [dict(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []

    def _write_locked(self, rows: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "templates": rows[-self.max_user_templates :]}
        temp = self.path.with_suffix(self.path.suffix + ".tmp")
        temp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp.replace(self.path)

    def list_all(self) -> list[dict[str, Any]]:
        with self._lock:
            user_rows = self._load_locked()
        builtins = [{**row, "builtin": True, "deletable": False} for row in BUILTIN_TEMPLATES]
        users = [
            {
                "id": str(row.get("id") or ""),
                "label": str(row.get("label") or "常用任务"),
                "prompt": str(row.get("prompt") or ""),
                "created_at": str(row.get("created_at") or ""),
                "builtin": False,
                "deletable": True,
            }
            for row in reversed(user_rows)
            if row.get("id") and row.get("prompt")
        ]
        return users + builtins

    def save(self, *, label: str, prompt: str) -> dict[str, Any]:
        safe_label = redact_text(label, limit=40).strip()
        safe_prompt = redact_text(prompt, limit=4000).strip()
        if not safe_label:
            raise ValueError("模板名称不能为空")
        if not safe_prompt:
            raise ValueError("模板指令不能为空")
        row = {
            "id": secrets.token_hex(8),
            "label": safe_label,
            "prompt": safe_prompt,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        with self._lock:
            rows = self._load_locked()
            rows.append(row)
            self._write_locked(rows)
        return {**row, "builtin": False, "deletable": True}

    def delete(self, template_id: str) -> bool:
        safe_id = str(template_id or "").strip()
        with self._lock:
            rows = self._load_locked()
            kept = [row for row in rows if str(row.get("id") or "") != safe_id]
            if len(kept) == len(rows):
                return False
            self._write_locked(kept)
        return True


TEMPLATES = ButlerTemplateStore(data_dir() / "butler_templates.local.json")


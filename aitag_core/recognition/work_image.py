from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from aitag_core.storage.sqlite import load_image_json

from .contract import SlotAnalysis
from .slot_analyzer import analyze_slot_caption


@dataclass(frozen=True)
class WorkImageAnalysis:
    work_id: int
    page_index: int
    base_caption: str
    slots: tuple[SlotAnalysis, ...]

    def to_dict(self) -> dict:
        return {
            "work_id": self.work_id,
            "page_index": self.page_index,
            "base_caption": self.base_caption,
            "slots": [s.to_dict() for s in self.slots],
        }


def analyze_work_image(work_id: int, page_index: int = 0) -> WorkImageAnalysis:
    ai_json = load_image_json(work_id, page_index)
    comment = _effective_comment(ai_json)
    v4 = comment.get("v4_prompt") or {}
    cap = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
    base_caption = str(cap.get("base_caption") or comment.get("prompt") or "")
    char_caps = cap.get("char_captions") or []
    slots = []
    for item in char_caps:
        if not isinstance(item, dict):
            continue
        slots.append(analyze_slot_caption(str(item.get("char_caption") or "")))
    return WorkImageAnalysis(int(work_id), int(page_index), base_caption, tuple(slots))


def _effective_comment(ai_json: dict[str, Any]) -> dict[str, Any]:
    raw = ai_json.get("Comment") or ai_json.get("comment") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


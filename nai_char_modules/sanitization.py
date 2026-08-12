"""Prompt sanitization rules for Studio Drafts and Remix Recipes."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any, Callable

from char_tag_db import is_creature_tag, split_prompt_tags


def _join_tags(tags: list[str]) -> str:
    return ", ".join(tags)


def _is_standalone_muscular_male(value: str) -> bool:
    low = str(value or "").lower()
    female_markers = (
        "muscular female",
        "muscular_female",
        "muscular thighs",
        "muscular butt",
        "muscular arms",
        "muscular hips",
        "muscular-2",
    )
    if any(marker in low for marker in female_markers):
        return False
    normalized = re.sub(r"^\d+(?:\.\d+)?::", "", low)
    normalized = re.sub(r"::$", "", normalized).strip("{} ").strip()
    return normalized in ("muscular", "muscler", "manly", "bara", "buff")


class PromptSanitizer:
    """Apply the configured blocklist without exposing prompt internals."""

    def __init__(self, blocklist_loader: Callable[[], dict[str, list[str]]]) -> None:
        self._blocklist_loader = blocklist_loader

    def _sanitize_text(
        self,
        text: str,
        block: dict[str, list[str]],
        *,
        racial: bool,
        gore: bool,
        creature: bool,
    ) -> tuple[str, list[str]]:
        removed: list[str] = []
        kept: list[str] = []
        for tag in split_prompt_tags(text):
            low = tag.lower()
            hit = False
            if racial:
                for bad in block.get("racial", []):
                    if bad in low:
                        removed.append(tag)
                        hit = True
                        break
                if not hit and _is_standalone_muscular_male(low):
                    removed.append(tag)
                    hit = True
            if not hit and gore:
                for bad in block.get("gore", []):
                    if bad in low:
                        removed.append(tag)
                        hit = True
                        break
            if not hit and creature:
                for bad in block.get("creature", []):
                    if bad in low:
                        removed.append(tag)
                        hit = True
                        break
                if not hit and is_creature_tag(tag):
                    removed.append(tag)
                    hit = True
            if not hit:
                kept.append(tag)
        return _join_tags(kept), removed

    def sanitize_comment(
        self,
        comment: dict,
        *,
        racial: bool = True,
        gore: bool = True,
        creature: bool = False,
    ) -> dict[str, Any]:
        block = self._blocklist_loader()
        removed_fields: list[dict] = []
        patched = copy.deepcopy(comment)

        v4 = patched.get("v4_prompt") or {}
        caption = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
        base = str(caption.get("base_caption") or patched.get("prompt") or "")
        new_base, removed = self._sanitize_text(
            base, block, racial=racial, gore=gore, creature=creature
        )
        if removed:
            removed_fields.append({"field": "base_caption", "removed": removed})
        if isinstance(v4, dict) and "caption" in v4:
            v4["caption"]["base_caption"] = new_base
        patched["prompt"] = new_base

        for index, item in enumerate(caption.get("char_captions") or []):
            if not isinstance(item, dict):
                continue
            cleaned, removed = self._sanitize_text(
                str(item.get("char_caption") or ""),
                block,
                racial=racial,
                gore=gore,
                creature=creature,
            )
            item["char_caption"] = cleaned
            if removed:
                removed_fields.append(
                    {"field": f"char_caption[{index}]", "removed": removed}
                )

        if not isinstance(patched.get("v4_negative_prompt"), dict):
            patched["v4_negative_prompt"] = {}
        negative = patched["v4_negative_prompt"]
        if not isinstance(negative.get("caption"), dict):
            negative["caption"] = {}
        negative_caption = negative["caption"]
        base_uc = str(negative_caption.get("base_caption") or "")
        if base_uc.strip():
            cleaned, removed = self._sanitize_text(
                base_uc, block, racial=racial, gore=gore, creature=creature
            )
            negative_caption["base_caption"] = cleaned
            if removed:
                removed_fields.append({"field": "uc_base_caption", "removed": removed})
        for index, item in enumerate(negative_caption.get("char_captions") or []):
            if not isinstance(item, dict):
                continue
            cleaned, removed = self._sanitize_text(
                str(item.get("char_caption") or ""),
                block,
                racial=racial,
                gore=gore,
                creature=creature,
            )
            item["char_caption"] = cleaned
            if removed:
                removed_fields.append(
                    {"field": f"uc_caption[{index}]", "removed": removed}
                )

        cleaned_uc, removed = self._sanitize_text(
            str(patched.get("uc") or ""),
            block,
            racial=racial,
            gore=gore,
            creature=creature,
        )
        patched["uc"] = cleaned_uc
        if removed:
            removed_fields.append({"field": "uc", "removed": removed})

        empty_slots = [
            index
            for index, item in enumerate(caption.get("char_captions") or [])
            if not str((item or {}).get("char_caption") or "").strip()
        ]
        return {
            "ok": True,
            "patched_comment": patched,
            "removed": removed_fields,
            "empty_slots": empty_slots,
            "blocked": bool(empty_slots),
        }


def sanitizer_from_path(path: Path) -> PromptSanitizer:
    import json

    def load() -> dict[str, list[str]]:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}

    return PromptSanitizer(load)

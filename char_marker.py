"""NAI 扁平咒语里的 char1/char2 分角色写法解析与回写。"""

from __future__ import annotations

import re
from typing import Any

_CHAR_MARKER_FIND_RE = re.compile(r"char([1-6])\s*([:：])", re.IGNORECASE)


def parse_char_marker_layout(text: str) -> dict[str, Any] | None:
    """把含 char1:/char1： 的扁平 prompt 拆成底图 + 各角色段。"""
    raw = str(text or "")
    if not raw.strip():
        return None
    matches = list(_CHAR_MARKER_FIND_RE.finditer(raw))
    if not matches:
        return None

    base_caption = raw[: matches[0].start()].rstrip()
    sections: list[dict[str, Any]] = []
    prev_span_end = matches[0].start()
    for i, match in enumerate(matches):
        marker_num = int(match.group(1))
        separator = match.group(2)
        caption_start = match.end()
        caption_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw)
        glue = "" if i == 0 else raw[prev_span_end : match.start()]
        caption_region = raw[caption_start:caption_end]
        caption = caption_region.strip().rstrip(",").strip()
        glue_after = ""
        if caption:
            pos = caption_region.find(caption)
            if pos >= 0:
                glue_after = caption_region[pos + len(caption) :]
        sections.append(
            {
                "marker_num": marker_num,
                "separator": separator,
                "caption": caption,
                "glue": glue,
                "glue_after": glue_after,
                "span_start": match.start(),
                "span_end": caption_end,
            }
        )
        prev_span_end = caption_end

    return {
        "layout": "char_markers",
        "base_caption": base_caption,
        "sections": sections,
        "full_text": raw,
    }


def rebuild_char_marker_prompt(layout: dict[str, Any], chars: list[dict]) -> str:
    """按原 char1/char2 结构回写替换后的角色段。"""
    base = str(layout.get("base_caption") or "")
    sections = list(layout.get("sections") or [])
    if not sections:
        return str(layout.get("full_text") or base)

    out = base
    for i, sec in enumerate(sections):
        marker_num = int(sec.get("marker_num") or (i + 1))
        separator = str(sec.get("separator") or "：")
        glue = str(sec.get("glue") or "")
        glue_after = str(sec.get("glue_after") or "")
        old_caption = str(sec.get("caption") or "")
        new_caption = str((chars[i] or {}).get("char_caption") or old_caption)
        out += glue + f"char{marker_num}{separator}{new_caption}{glue_after}"
    return out


def marker_label(marker_num: int) -> str:
    return f"char{int(marker_num)}"
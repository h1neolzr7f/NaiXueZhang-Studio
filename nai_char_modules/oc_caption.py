"""OC 人设说明的解析与展示标签提取（从 nai_char.py 拆出）。"""

from __future__ import annotations

from aitag_core.recognition import match_oc_preset
from char_tag_db import split_prompt_tags
from nai_char_modules.tag_constants import GENDER_NOISE as _GENDER_NOISE
from typing import Any
import re


def _identity_display_tags(tags: list[str]) -> list[str]:
    from char_tag_db import (
        classify_single_tag,
        identity_tag_display,
        is_action_phrase,
        is_copyright_tag,
        is_generic_character_tag,
        is_identity_meta_noise,
        weighted_tag_inner,
    )

    out: list[str] = []
    seen: set[str] = set()
    for tag in tags or []:
        display = identity_tag_display(tag)
        low = str(display or tag or "").strip().lower()
        if not low or low in _GENDER_NOISE or low in seen:
            continue
        if is_identity_meta_noise(low) or is_generic_character_tag(low) or is_action_phrase(display or tag):
            continue
        inner = weighted_tag_inner(tag)
        if is_copyright_tag(low) and not low.startswith("cure") and not inner:
            continue
        cat = classify_single_tag(str(tag))
        if cat in {"action", "appearance", "body", "creature"}:
            continue
        seen.add(low)
        out.append(display or str(tag).strip())
    return out


def _is_oc_like_caption(caption: str) -> bool:
    """群友 OC 用 {{}}；已有明确角色名时仅把 {{}} 当姿势权重，不算 OC 预设。"""
    text = str(caption or "")
    if re.search(r"\b\w+_\(oc\)", text, re.IGNORECASE):
        return True
    if "{{" not in text:
        return False
    from char_tag_db import (
        CHAR_SUFFIX_RE,
        is_action_phrase,
        is_character_tag,
        is_generic_character_tag,
        split_prompt_tags,
    )

    for part in split_prompt_tags(text):
        low = str(part).strip().lower()
        if not low:
            continue
        if CHAR_SUFFIX_RE.match(low):
            return False
        if is_character_tag(low) and not is_generic_character_tag(low):
            return False
        if " " in low and not is_action_phrase(part) and not is_generic_character_tag(low):
            return False
    return True


_OC_GENDER_SKIP = frozenset(
    {
        "1girl",
        "female_focus",
        "1boy",
        "male_focus",
        "original_character",
        "girl",
        "boy",
    }
)

_OC_BODY_TYPE_HINTS = (
    "slim",
    "youthful",
    "petite",
    "curvy",
    "muscular",
    "years old",
    "year old",
    "breast",
    "loli",
    "teen",
    "short",
    "tall",
)

_OC_EXPRESSION_KEEP = frozenset(
    {
        "closed eyes",
        "open eyes",
        "half-closed eyes",
        "one eye closed",
        "tears",
        "crying",
        "teary eyes",
        "blush",
        "heavy breathing",
        "panting",
        "open mouth",
        "parted lips",
        "clenched teeth",
        "torogao",
        "ahegao",
        "o-face",
        "orgasm",
        "sweat",
        "sweating",
        "looking at viewer",
        "looking away",
        "looking back",
        "looking up",
        "looking down",
    }
)

_OC_POSE_PRIORITY_HINTS = (
    "lie",
    "lying",
    "lay",
    "sit",
    "sitting",
    "stand",
    "standing",
    "kneel",
    "squat",
    "lean",
    "bend",
    "hug",
    "kiss",
    "embrace",
    "sex",
    "onanism",
    "fingering",
    "missionary",
    "doggy",
    "cowgirl",
    "oral",
    "penetration",
    "grinding",
    "straddle",
    "mounting",
)

_OC_POSE_DEFER_HINTS = (
    "serious",
    "patience",
    "legs straight",
    "straighten body",
    "full body",
    "wide shot",
    "from above",
    "from below",
    "from side",
    "dynamic angle",
)


def _oc_appearance_parts(caption: str) -> list[str]:
    """OC 预设/槽位：仅外貌、体型、服装，不含动作/神态/场景。"""
    from char_tag_db import (
        classify_single_tag,
        is_action_phrase,
        is_appearance_weight_block,
        is_generic_character_tag,
    )

    out: list[str] = []
    seen: set[str] = set()
    for part in split_prompt_tags(caption):
        low = str(part).strip().lower()
        if not low or low in seen or low in _OC_GENDER_SKIP or low.endswith("_(oc)"):
            continue
        if is_generic_character_tag(low):
            continue
        if "{{" in part or "::" in part:
            if is_appearance_weight_block(part) or any(h in low for h in _OC_BODY_TYPE_HINTS):
                out.append(str(part).strip())
                seen.add(low)
            continue
        cat = classify_single_tag(part)
        if cat in {"action", "creature", "gender"}:
            continue
        if is_action_phrase(part):
            continue
        if cat in {"identity", "appearance", "body"}:
            out.append(str(part).strip())
            seen.add(low)
    return out


def _oc_display_tags(caption: str, *, max_tags: int = 8) -> list[str]:
    """群友 OC：展示外貌/身份词条，动作/姿势不进草稿区角色行。"""
    return _oc_appearance_parts(caption)[:max_tags]


def _split_scene_for_oc_merge(scene_tags: list[str]) -> tuple[list[str], list[str]]:
    """把保留的场景 tag 拆成：优先姿势/互动 vs 其余体型/氛围。"""
    from char_tag_db import classify_single_tag, is_action_phrase

    priority: list[str] = []
    rest: list[str] = []
    seen: set[str] = set()
    for tag in scene_tags:
        low = str(tag).strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        if any(h in low for h in _OC_POSE_DEFER_HINTS):
            rest.append(str(tag).strip())
            continue
        if "{{" in tag or "::" in tag:
            priority.append(str(tag).strip())
            continue
        if classify_single_tag(tag) == "action" and (
            any(h in low for h in _OC_POSE_PRIORITY_HINTS) or is_action_phrase(tag)
        ):
            priority.append(str(tag).strip())
            continue
        rest.append(str(tag).strip())
    return priority, rest


def _oc_caption_preview(caption: str, *, max_len: int = 220) -> str:
    text = ", ".join(_oc_appearance_parts(caption)).strip()
    if not text:
        text = str(caption or "").strip()
    for prefix in ("1girl, female_focus, ", "1boy, male_focus, "):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def _slot_matches_oc_preset(slot_caption: str, preset: dict[str, Any]) -> bool:
    return match_oc_preset(slot_caption, preset).matched


def _appearance_display_tags(bundle: dict[str, Any]) -> list[str]:
    """外貌/服装词条（不含角色名与动作）。"""
    from char_tag_db import is_generic_character_tag

    out: list[str] = []
    seen: set[str] = set()
    for tag in (bundle.get("body") or []) + (bundle.get("appearance") or []):
        low = str(tag).strip().lower()
        if not low or low in seen or is_generic_character_tag(low):
            continue
        seen.add(low)
        out.append(str(tag).strip())
    return out[:8]


def _action_display_tags(bundle: dict[str, Any]) -> list[str]:
    """动作/姿势词条（角色行下方单独展示）。"""
    from char_tag_db import (
        classify_single_tag,
        is_appearance_weight_block,
        is_generic_character_tag,
        is_identity_meta_noise,
        weighted_tag_inner,
    )

    out: list[str] = []
    seen: set[str] = set()
    for tag in bundle.get("action") or []:
        display = weighted_tag_inner(tag) or str(tag).strip()
        low = display.lower()
        if not low or low in seen:
            continue
        if is_generic_character_tag(low) or is_identity_meta_noise(low):
            continue
        if is_appearance_weight_block(tag):
            continue
        if classify_single_tag(tag) != "action":
            continue
        seen.add(low)
        out.append(display)
    return out[:10]

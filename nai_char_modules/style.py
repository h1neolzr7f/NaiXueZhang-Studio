"""NAI style-reference recognition and deterministic prompt mutation."""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from char_tag_db import (
    classify_single_tag,
    is_appearance_tag,
    is_body_tag,
    is_character_tag,
    is_copyright_tag,
    is_creature_tag,
    is_gender_tag,
    split_prompt_tags,
    weighted_tag_inner,
)
from paths import data_dir

STYLE_TAG_PATH = data_dir() / "danbooru_style_tags.json"

_STYLE_HINTS = frozenset(
    {
        "official style", "official_style", "official art", "official_art",
        "official color", "official_color", "anime style", "anime_style",
        "manga style", "manga_style", "game cg", "game_cg",
        "visual novel cg", "visual_novel_cg", "watercolor", "sketch",
        "lineart", "cel shading", "cel_shading", "flat color", "flat_color",
        "pixel art", "pixel_art", "retro artstyle", "retro_artstyle", "chibi",
    }
)
_STYLE_HINT_RE = re.compile(
    r"(?:^|[_\s])(style|artstyle|artist|drawn|illustration|painting|watercolor|"
    r"sketch|lineart|cel|anime|manga|comic|cartoon|chibi|pixel|retro|"
    r"monochrome|flat|render|realistic|photorealistic)(?:$|[_\s])",
    re.IGNORECASE,
)
_STYLE_BUCKET_HINT_RE = re.compile(
    r"(?:^|[_\s])(style|artstyle|artist|drawn|illustration|painting|watercolor|"
    r"sketch|lineart|cel|anime|manga|comic|cartoon|chibi|pixel|retro|"
    r"monochrome|flat|render|realistic|photorealistic|cg|painterly|"
    r"impasto|gouache|pastel)(?:$|[_\s])",
    re.IGNORECASE,
)


def _join_tags(tags: list[str]) -> str:
    return ", ".join(tags)


def normalize_style_tag(tag: str) -> str:
    low = str(tag or "").strip().lower()
    low = re.sub(r"^\{+|\}+$", "", low).strip()
    low = re.sub(r"^-?\d+(?:\.\d+)?::", "", low)
    low = re.sub(r"^::", "", low)
    low = re.sub(r"::$", "", low).strip()
    if low.startswith("artist:"):
        low = low.split(":", 1)[1].strip()
    if low.endswith(")") and not re.search(r"\([^)]*\)$", low):
        low = low[:-1].strip()
    return low


def normalize_style_tag_for_match(tag: str, *, case_insensitive: bool) -> str:
    if case_insensitive:
        return normalize_style_tag(tag)
    normalized = str(tag or "").strip()
    normalized = re.sub(r"^\{+|\}+$", "", normalized).strip()
    normalized = re.sub(r"^-?\d+(?:\.\d+)?::", "", normalized)
    normalized = re.sub(r"^::", "", normalized)
    normalized = re.sub(r"::$", "", normalized).strip()
    if normalized.lower().startswith("artist:"):
        normalized = normalized.split(":", 1)[1].strip()
    if normalized.endswith(")") and not re.search(r"\([^)]*\)$", normalized):
        normalized = normalized[:-1].strip()
    return normalized


@lru_cache(maxsize=1)
def _style_index() -> dict[str, Any]:
    if not STYLE_TAG_PATH.exists():
        return {"artists": {}, "styles": {}, "meta": {}, "copyrights": {}}
    try:
        raw = json.loads(STYLE_TAG_PATH.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {"artists": {}, "styles": {}, "meta": {}, "copyrights": {}}


def style_index_stats() -> dict[str, Any]:
    index = _style_index()
    return {
        "path": str(STYLE_TAG_PATH),
        "exists": STYLE_TAG_PATH.exists(),
        "artists": len(index.get("artists") or {}),
        "styles": len(index.get("styles") or {}),
        "meta": len(index.get("meta") or {}),
        "copyrights": len(index.get("copyrights") or {}),
        "fetched_at": str(index.get("fetched_at") or ""),
    }


def reload_style_index() -> dict[str, Any]:
    _style_index.cache_clear()
    return style_index_stats()


def _is_explicit_style_tag(value: str) -> bool:
    normalized = str(value or "").strip().lower()
    return bool(
        normalized
        and (
            normalized in _STYLE_HINTS
            or normalized.endswith("_(style)")
            or _STYLE_BUCKET_HINT_RE.search(normalized)
        )
    )


def _style_kind_and_posts(tag: str) -> tuple[str, int] | None:
    low = normalize_style_tag(tag)
    if not low:
        return None
    index = _style_index()
    for kind, bucket in (
        ("artist", index.get("artists") or {}),
        ("style", index.get("styles") or {}),
        ("meta", index.get("meta") or {}),
    ):
        for candidate in (low, low.replace(" ", "_")):
            if candidate not in bucket:
                continue
            if kind == "style" and not _is_explicit_style_tag(candidate):
                continue
            try:
                return kind, int(bucket[candidate] or 0)
            except Exception:
                return kind, 0
    inner = weighted_tag_inner(tag)
    candidate = inner or tag
    category = classify_single_tag(str(candidate))
    if (
        category in {"identity", "gender", "body", "appearance", "creature"}
        or is_character_tag(candidate)
        or is_copyright_tag(candidate)
        or is_gender_tag(candidate)
        or is_body_tag(candidate)
        or is_appearance_tag(candidate)
        or is_creature_tag(candidate)
    ):
        return None
    if _is_explicit_style_tag(low) or _STYLE_HINT_RE.search(low):
        return "style", 0
    return None


def _extract_slots(text: str, *, field: str, char_index: int | None = None) -> list[dict[str, Any]]:
    slots: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, tag in enumerate(split_prompt_tags(text)):
        hit = _style_kind_and_posts(tag)
        if not hit:
            continue
        kind, posts = hit
        key = f"{field}:{char_index}:{normalize_style_tag(tag)}"
        if key in seen:
            continue
        seen.add(key)
        slots.append(
            {
                "tag": tag,
                "normalized": normalize_style_tag(tag),
                "kind": kind,
                "posts": posts,
                "field": field,
                "char_index": char_index,
                "position": position,
            }
        )
    return slots


def extract_style_slots_from_comment(comment: dict) -> list[dict[str, Any]]:
    v4 = comment.get("v4_prompt") or {}
    caption = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
    base = str(caption.get("base_caption") or comment.get("prompt") or "")
    slots = _extract_slots(base, field="base_caption")
    for index, item in enumerate(caption.get("char_captions") or []):
        if isinstance(item, dict):
            slots.extend(
                _extract_slots(
                    str(item.get("char_caption") or ""),
                    field="char_caption",
                    char_index=index,
                )
            )
    rank = {"artist": 0, "style": 1, "meta": 2}
    slots.sort(
        key=lambda item: (
            rank.get(str(item.get("kind") or ""), 9),
            str(item.get("field") or ""),
            int(item.get("char_index") if item.get("char_index") is not None else -1),
            -int(item.get("posts") or 0),
            str(item.get("tag") or ""),
        )
    )
    return slots


def combine_style_slots(slots: list[dict[str, Any]]) -> dict[str, Any]:
    if not slots:
        return {"groups": [], "combined": "", "primary_field": ""}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for slot in slots:
        field = str(slot.get("field") or "base_caption")
        index = slot.get("char_index")
        grouped.setdefault(f"{field}:{index if index is not None else -1}", []).append(slot)
    groups: list[dict[str, Any]] = []
    for items in grouped.values():
        items.sort(key=lambda item: (int(item.get("position") or 0), str(item.get("tag") or "")))
        tags = [str(item.get("tag") or "").strip() for item in items if str(item.get("tag") or "").strip()]
        if tags:
            groups.append(
                {
                    "field": str(items[0].get("field") or "base_caption"),
                    "char_index": items[0].get("char_index"),
                    "tags": tags,
                    "combined": ", ".join(tags),
                    "slot_count": len(tags),
                    "kinds": list(dict.fromkeys(str(item.get("kind") or "") for item in items if item.get("kind"))),
                }
            )
    groups.sort(
        key=lambda group: (
            {"base_caption": 0, "char_caption": 1}.get(str(group.get("field") or ""), 9),
            int(group.get("char_index") if group.get("char_index") is not None else -1),
        )
    )
    primary = groups[0] if groups else {}
    all_tags: list[str] = []
    for group in groups:
        for tag in group.get("tags") or []:
            if tag not in all_tags:
                all_tags.append(tag)
    return {
        "groups": groups,
        "combined": str(primary.get("combined") or ""),
        "combined_all": ", ".join(all_tags),
        "primary_field": str(primary.get("field") or ""),
    }


def _cleanup_prompt_commas(text: str) -> str:
    cleaned = str(text or "")
    previous = None
    while previous != cleaned:
        previous = cleaned
        cleaned = re.sub(r"\s*,\s*,+", ",", cleaned)
    cleaned = re.sub(r"^\s*,+\s*", "", cleaned)
    cleaned = re.sub(r"\s*,+\s*$", "", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def replace_style_in_text(
    text: str,
    find: str,
    replace: str,
    *,
    case_insensitive: bool = True,
) -> tuple[str, int]:
    needle = str(find or "")
    if not needle:
        return text, 0
    replacement = str(replace or "")
    needle_parts = split_prompt_tags(needle)
    if len(needle_parts) == 1:
        raw_needle = needle.strip()
        normalized_needle = normalize_style_tag_for_match(
            raw_needle, case_insensitive=case_insensitive
        )
        next_tags: list[str] = []
        count = 0
        replacement_added = False
        for tag in split_prompt_tags(text):
            raw_match = tag.lower() == raw_needle.lower() if case_insensitive else tag == raw_needle
            normalized_match = bool(
                normalized_needle
                and normalize_style_tag_for_match(tag, case_insensitive=case_insensitive)
                == normalized_needle
            )
            if raw_match or normalized_match:
                count += 1
                if replacement.strip() and not replacement_added:
                    next_tags.append(replacement.strip())
                    replacement_added = True
            else:
                next_tags.append(tag)
        if count:
            return _cleanup_prompt_commas(_join_tags(next_tags)), count
    flags = re.IGNORECASE if case_insensitive else 0
    new_text, count = re.subn(re.escape(needle), replacement, text, flags=flags)
    return _cleanup_prompt_commas(new_text) if count else new_text, count


def replace_style_in_comment(
    comment: dict,
    find: str,
    replace: str,
    *,
    case_insensitive: bool = True,
) -> tuple[dict, int]:
    patched = copy.deepcopy(comment)
    total = 0
    v4 = patched.get("v4_prompt")
    if isinstance(v4, dict):
        caption = v4.get("caption")
        if isinstance(caption, dict):
            base = str(caption.get("base_caption") or patched.get("prompt") or "")
            new_base, count = replace_style_in_text(base, find, replace, case_insensitive=case_insensitive)
            total += count
            caption["base_caption"] = new_base
            for item in caption.get("char_captions") or []:
                if isinstance(item, dict):
                    value, count = replace_style_in_text(
                        str(item.get("char_caption") or ""), find, replace,
                        case_insensitive=case_insensitive,
                    )
                    total += count
                    item["char_caption"] = value
            v4["caption"] = caption
            patched["v4_prompt"] = v4
            patched["prompt"] = new_base
    negative = patched.get("v4_negative_prompt")
    if isinstance(negative, dict):
        caption = negative.get("caption")
        if isinstance(caption, dict):
            base = str(caption.get("base_caption") or "")
            if base:
                value, count = replace_style_in_text(base, find, replace, case_insensitive=case_insensitive)
                total += count
                caption["base_caption"] = value
            for item in caption.get("char_captions") or []:
                if isinstance(item, dict):
                    value, count = replace_style_in_text(
                        str(item.get("char_caption") or ""), find, replace,
                        case_insensitive=case_insensitive,
                    )
                    total += count
                    item["char_caption"] = value
            negative["caption"] = caption
            patched["v4_negative_prompt"] = negative
    if isinstance(v4, dict):
        return patched, total
    value, count = replace_style_in_text(
        str(patched.get("prompt") or ""), find, replace,
        case_insensitive=case_insensitive,
    )
    patched["prompt"] = value
    return patched, count


def append_style_to_comment(comment: dict, style_text: str) -> tuple[dict, int]:
    patched = copy.deepcopy(comment)
    style = str(style_text or "").strip()
    if not style:
        return patched, 0

    def append(base: str) -> tuple[str, int]:
        text = str(base or "").strip()
        existing = split_prompt_tags(text)
        normalized = {normalize_style_tag(tag) for tag in existing if normalize_style_tag(tag)}
        additions: list[str] = []
        for tag in split_prompt_tags(style):
            key = normalize_style_tag(tag)
            if key and key not in normalized:
                normalized.add(key)
                additions.append(tag)
        return (_join_tags([*existing, *additions]), 1) if additions else (text, 0)

    v4 = patched.get("v4_prompt")
    if isinstance(v4, dict) and isinstance(v4.get("caption"), dict):
        caption = v4["caption"]
        value, count = append(str(caption.get("base_caption") or patched.get("prompt") or ""))
        if count:
            caption["base_caption"] = value
            v4["caption"] = caption
            patched["v4_prompt"] = v4
            patched["prompt"] = value
        return patched, count
    value, count = append(str(patched.get("prompt") or ""))
    if count:
        patched["prompt"] = value
    return patched, count


def apply_style_preset_to_comment(
    comment: dict,
    style_text: str,
    *,
    detector=extract_style_slots_from_comment,
) -> tuple[dict, int, int, bool]:
    patched = copy.deepcopy(comment)
    recognized = sorted(
        {str(slot.get("tag") or "").strip() for slot in detector(patched) if str(slot.get("tag") or "").strip()},
        key=len,
        reverse=True,
    )
    cleared = 0
    for tag in recognized:
        patched, count = replace_style_in_comment(patched, tag, "", case_insensitive=True)
        cleared += count
    desired = str(style_text or "").strip()
    patched, added = append_style_to_comment(patched, desired)
    if not desired:
        applied = not detector(patched)
    elif added:
        applied = True
    else:
        v4 = patched.get("v4_prompt") or {}
        caption = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
        text = ", ".join(
            [str(caption.get("base_caption") or patched.get("prompt") or "")]
            + [str(item.get("char_caption") or "") for item in caption.get("char_captions") or [] if isinstance(item, dict)]
        )
        present = {normalize_style_tag(tag) for tag in split_prompt_tags(text) if normalize_style_tag(tag)}
        applied = all(
            normalize_style_tag(tag) in present
            for tag in split_prompt_tags(desired)
            if normalize_style_tag(tag)
        )
    return patched, cleared, added, applied

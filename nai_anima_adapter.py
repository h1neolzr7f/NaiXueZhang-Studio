"""Adapt AnimaDex-style character records into NovelAI character prompts.

Anima data is treated as reference evidence, never as a ready-to-send prompt.
The adapter keeps character identity and appearance facts, separates optional
artist/style hints, removes scene/quality metadata, and emits the subject
syntax expected by NovelAI V4 character prompt slots.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from char_tag_db import classify_caption_buckets, split_prompt_tags
from nai_prompt_profiles import nai_model_dialect

MAX_CHARACTER_TAGS = 32

_CHARACTER_FACETS = frozenset(
    {
        "identity",
        "appearance",
        "body",
        "hair",
        "eyes",
        "face",
        "clothing",
        "outfit",
        "accessories",
        "features",
        "species",
    }
)
_STYLE_FACETS = frozenset({"artist", "artists", "style", "styles", "medium"})
_PROMPT_META = frozenset(
    {
        "masterpiece",
        "best quality",
        "amazing quality",
        "great quality",
        "good quality",
        "normal quality",
        "worst quality",
        "very aesthetic",
        "top aesthetic",
        "aesthetic",
        "absurdres",
        "highres",
        "newest",
        "safe",
        "sensitive",
        "nsfw",
        "explicit",
    }
)
_SCENE_HINTS = (
    "background",
    "indoors",
    "outdoors",
    "scenery",
    "view",
    "angle",
    "shot",
    "looking ",
    "standing",
    "sitting",
    "lying",
    "running",
    "walking",
    "lighting",
    "depth of field",
)
_COUNT_TO_SLOT = {"1girl": "girl", "1boy": "boy", "1other": "other"}
_SLOT_TO_COUNT = {"girl": "1girl", "boy": "1boy", "other": "1other"}
_WEIGHT_RE = re.compile(r"^-?\d+(?:\.\d+)?::(.+?)::$", re.IGNORECASE)


def adapt_anima_character(record: dict[str, Any], *, model: str = "") -> dict[str, Any]:
    """Compile one AnimaDex-compatible record into a compact NAI card."""
    raw = dict(record or {})
    role = raw.get("role") if isinstance(raw.get("role"), dict) else {}
    source_id = _first_text(
        raw.get("id"),
        raw.get("slug"),
        raw.get("character"),
        role.get("id"),
        role.get("slug"),
    )
    label = _first_text(raw.get("name"), raw.get("label"), role.get("name"), role.get("label"), source_id)
    trigger = _first_tag(
        raw.get("trigger"),
        raw.get("trigger_tag"),
        role.get("trigger"),
        role.get("slug"),
        raw.get("slug"),
    )
    copyright_tag = _first_tag(
        raw.get("copyright"),
        raw.get("copyright_name"),
        raw.get("series"),
        role.get("copyright"),
        role.get("series"),
    )

    facets = raw.get("facets") if isinstance(raw.get("facets"), dict) else {}
    style_hints = _ordered_unique(
        tag
        for key, value in facets.items()
        if str(key).strip().lower() in _STYLE_FACETS
        for tag in _tag_values(value)
    )
    style_hints.extend(
        tag for tag in _tag_values(raw.get("artists")) if _tag_key(tag) not in {_tag_key(x) for x in style_hints}
    )

    fact_candidates: list[str] = []
    fact_candidates.extend(_tag_values(raw.get("core_tags")))
    # The AnimaDex export calls this column simply ``tags``.  Accept it
    # directly so its public CSV/JSON rows can be imported without a custom
    # preprocessing script.
    fact_candidates.extend(_tag_values(raw.get("tags")))
    fact_candidates.extend(_tag_values(role.get("core_tags")))
    for key, value in facets.items():
        if str(key).strip().lower() in _CHARACTER_FACETS:
            fact_candidates.extend(_tag_values(value))

    gender = _subject_kind(raw, role, fact_candidates)
    kept: list[str] = []
    dropped: list[str] = []
    for tag in fact_candidates:
        cleaned = _nai_tag(tag)
        if not cleaned:
            continue
        if _keep_character_fact(cleaned):
            kept.append(cleaned)
        else:
            dropped.append(cleaned)

    ordered = _ordered_unique(
        [gender, _nai_tag(trigger), _nai_tag(copyright_tag), *kept]
    )
    ordered = [tag for tag in ordered if tag][:MAX_CHARACTER_TAGS]
    slot_subject = _COUNT_TO_SLOT.get(gender, gender)
    if slot_subject and ordered:
        ordered[0] = slot_subject
    elif slot_subject:
        ordered.insert(0, slot_subject)

    return {
        "id": source_id,
        "label": label or trigger or source_id,
        "source": str(raw.get("source") or "animadex"),
        "source_id": source_id,
        "model_dialect": nai_model_dialect(model),
        "base_subject_tag": _SLOT_TO_COUNT.get(slot_subject, ""),
        "character_caption": ", ".join(ordered),
        "character_tags": ordered,
        # These are deliberately not injected.  They may be offered to the
        # separate NAI style-card UI when the user explicitly asks for them.
        "style_hints": _ordered_unique(_nai_tag(tag) for tag in style_hints if _nai_tag(tag)),
        "dropped_tags": _ordered_unique(dropped),
        "provenance": {
            "source": str(raw.get("source") or "animadex"),
            "version": str(raw.get("version") or raw.get("updated_at") or ""),
            "license": str(raw.get("license") or ""),
        },
    }


def apply_anima_character_to_comment(
    comment: dict[str, Any],
    record: dict[str, Any],
    *,
    slot_index: int = 0,
    model: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Place an adapted reference into a NAI V4 character prompt slot."""
    import copy

    if slot_index < 0 or slot_index >= 6:
        raise ValueError("NovelAI character slot index must be between 0 and 5")
    card = adapt_anima_character(record, model=model or str(comment.get("model") or ""))
    caption_text = str(card.get("character_caption") or "").strip()
    if not caption_text:
        raise ValueError("Anima character record does not contain usable NAI character tags")

    patched = copy.deepcopy(comment or {})
    v4 = patched.setdefault("v4_prompt", {})
    if not isinstance(v4, dict):
        v4 = {}
        patched["v4_prompt"] = v4
    caption = v4.setdefault("caption", {})
    if not isinstance(caption, dict):
        caption = {}
        v4["caption"] = caption
    slots = list(caption.get("char_captions") or [])
    while len(slots) <= slot_index:
        slots.append({"char_caption": "", "centers": [{"x": 0.5, "y": 0.5}]})
    previous = slots[slot_index] if isinstance(slots[slot_index], dict) else {}
    centers = previous.get("centers") or [{"x": 0.5, "y": 0.5}]
    slots[slot_index] = {"char_caption": caption_text, "centers": centers}
    caption["char_captions"] = slots[:6]

    base = str(caption.get("base_caption") or patched.get("prompt") or "")
    subject = str(card.get("base_subject_tag") or "")
    if subject and subject not in {_tag_key(x) for x in split_prompt_tags(base)}:
        base = f"{subject}, {base}".strip(" ,")
    caption["base_caption"] = base
    patched["prompt"] = base
    patched["_aitag_anima_reference"] = {
        "source_id": card.get("source_id"),
        "slot_index": slot_index,
        "model_dialect": card.get("model_dialect"),
    }
    return patched, card


def _keep_character_fact(tag: str) -> bool:
    low = _tag_key(tag)
    if not low or low in _PROMPT_META or low.startswith("rating:") or low.startswith("artist:"):
        return False
    if re.fullmatch(r"score[_ ]\d+", low) or re.fullmatch(r"year\s*\d{4}", low):
        return False
    if any(hint in low for hint in _SCENE_HINTS):
        return False
    buckets = classify_caption_buckets(tag)
    return bool(
        buckets.get("identity")
        or buckets.get("gender")
        or buckets.get("body")
        or buckets.get("appearance")
        or buckets.get("creature")
    )


def _subject_kind(raw: dict[str, Any], role: dict[str, Any], tags: Iterable[str]) -> str:
    values = [
        str(raw.get("gender") or ""),
        str(raw.get("sex") or ""),
        str(role.get("gender") or ""),
        *[str(tag) for tag in tags],
    ]
    text = " ".join(values).lower().replace("_", " ")
    if re.search(r"\b(1boy|boy|male|man)\b", text):
        return "1boy"
    if re.search(r"\b(1other|other|nonbinary)\b", text):
        return "1other"
    if re.search(r"\b(1girl|girl|female|woman)\b", text):
        return "1girl"
    return ""


def _tag_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return split_prompt_tags(value)
    if isinstance(value, dict):
        out: list[str] = []
        for nested in value.values():
            out.extend(_tag_values(nested))
        return out
    if isinstance(value, (list, tuple, set)):
        out = []
        for nested in value:
            out.extend(_tag_values(nested))
        return out
    return [str(value)]


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _first_tag(*values: Any) -> str:
    for value in values:
        tags = _tag_values(value)
        if tags:
            return tags[0]
    return ""


def _nai_tag(value: Any) -> str:
    raw = str(value or "").strip().strip(",")
    match = _WEIGHT_RE.match(raw)
    if match:
        raw = str(match.group(1) or "").strip()
    raw = raw.strip("{}[] ").replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _tag_key(value: Any) -> str:
    return _nai_tag(value).lower()


def _ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = _tag_key(text)
        if not text or not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out

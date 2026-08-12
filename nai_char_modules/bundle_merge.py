"""生物/场景保留与角色束合并（从 nai_char.py 拆出）。"""

from __future__ import annotations

from char_tag_db import classify_caption_buckets
from char_tag_db import pick_character_summary
from char_tag_db import split_prompt_tags
from nai_char_modules.oc_caption import _OC_EXPRESSION_KEEP, _oc_appearance_parts, _split_scene_for_oc_merge
from nai_char_modules.tag_constants import GENDER_NOISE as _GENDER_NOISE
from typing import Any
import re


_CREATURE_ACTION_DROP = frozenset(
    {"bestiality", "zoophilia", "interspecies", "animal_penetration"}
)


def _split_creature_parts(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,，\n]+", str(text or "")) if p.strip()]


def strip_creature_tags_from_caption(caption: str) -> tuple[str, list[str]]:
    """从咒语片段移除贵物/异种 tag 或短语，保留原角色身份与姿势。"""
    from char_tag_db import is_creature_phrase, is_creature_tag

    removed: list[str] = []
    kept: list[str] = []
    for part in _split_creature_parts(caption):
        low = part.lower()
        if (
            is_creature_phrase(part)
            or is_creature_tag(part)
            or low in _CREATURE_ACTION_DROP
        ):
            removed.append(part)
            continue
        kept.append(part)
    return ", ".join(kept), removed


_SCENE_BODY_HINTS = (
    "slim",
    "slender",
    "loli",
    "shota",
    "petite",
    "curvy",
    "muscular",
    "tall",
    "short",
    "years old",
    "year old",
    "legs",
    "flat chest",
    "small breasts",
    "large breasts",
    "young girl",
    "young boy",
    "adolescent",
    "teenager",
    "shorter",
    "taller",
    "skinny",
    "chubby",
    "plump",
)
_APPEARANCE_WEIGHT_HINTS = (
    "hair",
    "skin",
    "eyes",
    "eye ",
    " tail",
    " ears",
    "headphone",
    "uniform",
    "jacket",
    "hoodie",
    "shirt",
    "skirt",
    "shorts",
    "boots",
    "thighhighs",
    "panty",
    "panties",
    " bra",
    "crop top",
    "armor",
    " shoes",
    "gloves",
    "necklace",
    "earring",
)


def _weighted_block_is_appearance(block: str) -> bool:
    low = str(block or "").lower()
    if any(h in low for h in _SCENE_BODY_HINTS):
        return False
    return any(h in low for h in _APPEARANCE_WEIGHT_HINTS)


def _preserve_scene_tags(target_caption: str, *, hooded: bool) -> list[str]:
    """OC 换角：保留原图体型、姿势、神态、场景；仅剔除旧角色外貌/身份。"""
    from char_tag_db import (
        classify_single_tag,
        is_appearance_tag,
        is_body_tag,
        is_character_tag,
        is_copyright_tag,
        is_creature_tag,
        is_face_tag,
        pick_character_summary,
    )

    if not str(target_caption or "").strip():
        return []

    buckets = classify_caption_buckets(target_caption)
    blocked: set[str] = set()
    for bucket in ("identity", "gender", "appearance"):
        for tag in buckets.get(bucket) or []:
            low = str(tag).strip().lower()
            if low in _OC_EXPRESSION_KEEP:
                continue
            blocked.add(low)

    summary = str(
        pick_character_summary(target_caption, buckets.get("identity") or []) or ""
    ).strip().lower()
    if summary:
        blocked.add(summary)
        blocked.add(summary.replace(" ", "_"))

    kept: list[str] = []
    seen: set[str] = set()
    for tag in split_prompt_tags(target_caption):
        low = str(tag).strip().lower()
        if not low or low in seen or low in blocked:
            continue
        if is_creature_tag(tag):
            continue
        if is_copyright_tag(tag):
            continue
        tag_cat = classify_single_tag(tag)
        if is_character_tag(tag):
            if tag_cat != "action":
                continue
            kept.append(tag)
            seen.add(low)
            continue
        if "_(arknights)" in low or " (arknights)" in low or "_(series)" in low:
            continue
        if low in _GENDER_NOISE:
            continue
        if low in _OC_EXPRESSION_KEEP:
            kept.append(tag)
            seen.add(low)
            continue
        if "{{" in tag or "::" in tag:
            if _weighted_block_is_appearance(tag):
                continue
            kept.append(tag)
            seen.add(low)
            continue
        if is_appearance_tag(tag) and not is_body_tag(tag):
            if classify_single_tag(tag) != "action":
                continue
        if hooded and is_face_tag(tag):
            continue
        if re.search(r"^[\u4e00-\u9fff]{2,8}$", str(tag).strip()):
            continue
        kept.append(tag)
        seen.add(low)
    return kept


def _strip_swap_appearance(target_caption: str) -> str:
    """换角前剔除旧发色/瞳色/耳尾等外貌，避免残留到动作区。"""
    from char_tag_db import (
        classify_single_tag,
        is_appearance_tag,
        is_appearance_weight_block,
        is_body_tag,
        is_character_tag,
        is_copyright_tag,
        is_creature_tag,
    )

    kept: list[str] = []
    seen: set[str] = set()
    for tag in split_prompt_tags(target_caption):
        low = str(tag).strip().lower()
        if not low or low in seen:
            continue
        if is_creature_tag(tag):
            continue
        if is_character_tag(tag) or is_copyright_tag(tag):
            kept.append(tag)
            seen.add(low)
            continue
        if low in _OC_EXPRESSION_KEEP:
            kept.append(tag)
            seen.add(low)
            continue
        if is_appearance_weight_block(tag):
            continue
        if is_appearance_tag(tag) and not is_body_tag(tag):
            if classify_single_tag(tag) != "action":
                continue
        if classify_single_tag(tag) == "appearance":
            continue
        kept.append(tag)
        seen.add(low)
    return ", ".join(kept)


def _enrich_bundle_appearance(bundle: dict[str, Any]) -> dict[str, Any]:
    """预设/源槽 bundle 无发色时，按角色名补默认发色瞳色。"""
    from char_tag_db import default_appearance_for_identity

    appearance = list(bundle.get("appearance") or [])
    body = list(bundle.get("body") or [])
    combined = " ".join(appearance + body).lower()
    if "hair" in combined:
        return bundle
    defaults = default_appearance_for_identity(bundle.get("identity") or [])
    if not defaults:
        return bundle
    out = dict(bundle)
    out["appearance"] = defaults + appearance
    return out


def _preserved_target_action_tags(target_caption: str, *, hooded: bool = False) -> list[str]:
    """换角时从被替换槽位提取应保留的动作/姿势（含权重姿势块）。"""
    from char_tag_db import classify_single_tag

    seen: set[str] = set()
    out: list[str] = []
    for tag in _preserve_action_tags(target_caption, hooded=hooded):
        low = str(tag).strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(str(tag).strip())
    for tag in _preserve_scene_tags(target_caption, hooded=hooded):
        if classify_single_tag(tag) != "action":
            continue
        low = str(tag).strip().lower()
        if not low or low in seen:
            continue
        seen.add(low)
        out.append(str(tag).strip())
    return out[:10]


def _preserve_action_tags(target_caption: str, *, hooded: bool) -> list[str]:
    """D 站换角：仅保留姿势/互动（不含体型权重块）。"""
    from char_tag_db import (
        classify_single_tag,
        is_appearance_tag,
        is_appearance_weight_block,
        is_body_tag,
        is_character_tag,
        is_copyright_tag,
        is_creature_tag,
        is_face_tag,
        pick_character_summary,
    )

    buckets = classify_caption_buckets(target_caption)
    blocked: set[str] = set()
    for bucket in ("identity", "gender", "body", "appearance", "creature"):
        for tag in buckets.get(bucket) or []:
            blocked.add(str(tag).strip().lower())

    summary = str(
        pick_character_summary(target_caption, buckets.get("identity") or []) or ""
    ).strip().lower()
    if summary:
        blocked.add(summary)
        blocked.add(summary.replace(" ", "_"))

    action_tags: list[str] = []
    for tag in split_prompt_tags(target_caption):
        low = str(tag).strip().lower()
        if not low or low in blocked:
            continue
        if is_creature_tag(tag):
            continue
        if is_copyright_tag(tag):
            continue
        tag_cat = classify_single_tag(tag)
        if is_character_tag(tag) and tag_cat != "action":
            continue
        if "_(arknights)" in low or " (arknights)" in low or "_(series)" in low:
            continue
        if low in _GENDER_NOISE:
            continue
        if is_body_tag(tag) or is_appearance_tag(tag):
            continue
        if is_appearance_weight_block(tag):
            continue
        if hooded and is_face_tag(tag):
            continue
        if tag_cat != "action":
            continue
        if "{{" in tag or "::" in tag:
            continue
        if re.search(r"^[\u4e00-\u9fff]{2,8}$", str(tag).strip()):
            continue
        action_tags.append(tag)
    return action_tags


def _is_oc_bundle(bundle: dict[str, Any]) -> bool:
    if str(bundle.get("kind") or "").strip().lower() == "oc":
        return True
    return bool(str(bundle.get("char_caption") or "").strip())


def _oc_preset_summary(bundle: dict[str, Any]) -> str:
    label = str(bundle.get("label") or "").strip()
    if label:
        return label.split("（", 1)[0].split("(", 1)[0].strip() or label
    identity = bundle.get("identity") or []
    if identity:
        return str(identity[0]).split("_(")[0].strip()
    return "OC"


def merge_bundle(
    target_caption: str,
    source_bundle: dict[str, Any],
    *,
    preserve_action: bool = True,
    force_gender: str | None = None,
) -> str:
    source_bundle = _enrich_bundle_appearance(source_bundle)
    target_caption = _strip_swap_appearance(target_caption)
    hooded = bool(source_bundle.get("hooded"))
    action_tags: list[str] = []
    if preserve_action:
        action_tags = _preserve_action_tags(target_caption, hooded=hooded)
    gender = force_gender or source_bundle.get("gender") or "unknown"
    gender_tags: list[str] = []
    if gender == "male":
        gender_tags = ["1boy", "male_focus"]
    elif gender == "female":
        gender_tags = ["1girl", "female_focus"]

    direct_caption = str(source_bundle.get("char_caption") or "").strip()
    if _is_oc_bundle(source_bundle) and direct_caption:
        # 群友 OC：预设仅外貌；原图姿势/神态优先，避免被 OC 外貌块淹没导致站桩
        oc_parts = _oc_appearance_parts(direct_caption)
        scene_tags = _preserve_scene_tags(target_caption, hooded=hooded)
        pose_first, scene_rest = _split_scene_for_oc_merge(scene_tags)
        identity_tags = [
            tag
            for tag in (source_bundle.get("identity") or [])
            if str(tag or "").strip() and str(tag or "").strip() not in gender_tags
        ]
        parts = identity_tags + gender_tags + pose_first + oc_parts + scene_rest
    elif direct_caption:
        parts = gender_tags + [direct_caption] + action_tags
    else:
        parts = (
            source_bundle.get("identity", [])
            + gender_tags
            + source_bundle.get("body", [])
            + source_bundle.get("appearance", [])
            + action_tags
        )
    seen: set[str] = set()
    ordered: list[str] = []
    for tag in parts:
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(tag)

    # 支持所有角色替换的服饰替换 + 添加/减少
    # 这些字段可以来自预设（clothing/extra/remove），或运行时通过 payload 注入到 source_bundle
    clothing = str(source_bundle.get("clothing") or "").strip()
    extra_str = str(source_bundle.get("extra") or "").strip()
    remove_list = source_bundle.get("remove") or []
    if isinstance(remove_list, str):
        remove_list = [x.strip() for x in remove_list.split(",") if x.strip()]

    if clothing:
        clothing_tags = [t.strip() for t in clothing.split(",") if t.strip()]
        for t in clothing_tags:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(t)

    if extra_str:
        extra_tags = [t.strip() for t in extra_str.split(",") if t.strip()]
        for t in extra_tags:
            key = t.lower()
            if key not in seen:
                seen.add(key)
                ordered.append(t)

    if remove_list:
        remove_set = {str(r).strip().lower() for r in remove_list if str(r).strip()}
        ordered = [t for t in ordered if t.lower() not in remove_set]

    return ", ".join(ordered)


def _apply_replaced_char_state(
    ch: dict,
    source_bundle: dict[str, Any],
    old_caption: str,
    new_caption: str,
    *,
    force_gender: str | None = None,
) -> None:
    ch["char_caption"] = new_caption
    for key in ("is_oc", "oc_appearance_tags", "oc_preview", "preserved_action_tags"):
        ch.pop(key, None)
    if _is_oc_bundle(source_bundle):
        preset_cap = str(source_bundle.get("char_caption") or "").strip()
        ch["summary"] = _oc_preset_summary(source_bundle)
        ch["is_oc"] = True
        ch["oc_appearance_tags"] = (
            _oc_appearance_parts(preset_cap) if preset_cap else []
        )
        ch["preserved_action_tags"] = _preserved_target_action_tags(
            old_caption,
            hooded=bool(source_bundle.get("hooded")),
        )
    else:
        ch.pop("summary", None)
    if force_gender:
        ch["gender"] = force_gender
        bundle = ch.get("bundle")
        if isinstance(bundle, dict):
            bundle["gender"] = force_gender

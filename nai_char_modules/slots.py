"""角色槽位格式化与性别契约（从 nai_char.py 拆出）。"""

from __future__ import annotations

from aitag_core.recognition import analyze_slot_caption
from char_marker import marker_label
from char_swap_config import load_config as load_char_swap_config
from char_tag_db import classify_caption_buckets
from char_tag_db import pick_character_summary
from nai_char_modules.oc_caption import _action_display_tags, _appearance_display_tags, _identity_display_tags, _is_oc_like_caption, _oc_appearance_parts, _oc_caption_preview
from slot_gender import apply_slot_genders
from slot_gender import slot_gender_of
from typing import Any


def _format_char_slot(i: int, ch: dict, caption: str, *, gender_hint: str = "") -> dict[str, Any]:
    """从替换后的 char_caption 重建槽位展示字段（避免草稿区仍显示旧角色 tag）。"""
    from char_tag_db import is_creature_slot

    try:
        cfg = load_char_swap_config()
        cp = cfg.get("custom_presets") or {}
        oc_presets = list(cp.get("female") or []) + list(cp.get("male") or [])
    except Exception:
        oc_presets = []
    analysis = analyze_slot_caption(
        caption,
        gender_hint=gender_hint,
        oc_presets=oc_presets,
    )

    preset_summary = str(ch.get("summary") or "").strip()
    bundle = bundle_from_caption(caption, gender=gender_hint)
    is_oc_slot = bool(ch.get("is_oc")) or _is_oc_like_caption(caption)
    if is_oc_slot:
        oc_tags = list(ch.get("oc_appearance_tags") or _oc_appearance_parts(caption))
        oc_preview = _oc_caption_preview(", ".join(oc_tags)) if oc_tags else ""
    else:
        oc_tags = []
        oc_preview = ""

    marker_num = ch.get("marker_num")
    if preset_summary:
        summary = preset_summary
    else:
        from char_tag_db import is_action_phrase, is_generic_character_tag

        from char_tag_db import repair_prompt_caption

        # 如果没有 identity tags，说明这个槽没有明确的角色身份
        # summary 不应从 caption 中随便选一个标签，应留空
        identity_tags = bundle.get("identity") or []
        if not identity_tags:
            summary = ""
        else:
            summary = pick_character_summary(repair_prompt_caption(caption), identity_tags)
        if marker_num and (
            not summary
            or is_generic_character_tag(summary)
            or is_action_phrase(summary)
        ):
            summary = marker_label(int(marker_num))

    if oc_tags:
        # Bug 2 修复：OC 模式的 identity_display 应为角色名/预设标签，而非外貌词条
        # 外貌词条应只出现在 appearance_tags 和 oc_preview 中
        oc_label = str(ch.get("summary") or preset_summary or "").strip()
        identity_display = [oc_label] if oc_label else []
        bundle = {
            **bundle,
            "body": [],
            "appearance": oc_tags,
        }
        if not identity_display:
            # 没有角色名时，从 caption 中提取第一个非外貌的 identity tag
            for t in bundle.get("identity") or []:
                from char_tag_db import is_appearance_tag
                if not is_appearance_tag(t):
                    identity_display = [t]
                    break
            if not identity_display:
                identity_display = oc_tags[:1]
    elif preset_summary:
        identity_display = _identity_display_tags(bundle["identity"])
        if not identity_display:
            identity_display = [preset_summary]
    else:
        identity_display = _identity_display_tags(bundle["identity"])
        if not identity_display and not summary:
            identity_display = ["未知角色"]

    creature_tags = list(bundle.get("creature") or [])
    slot_is_creature = is_creature_slot(caption, summary=summary)
    slot: dict[str, Any] = {
        "index": i,
        "char_caption": caption,
        "uc_caption": str(ch.get("uc_caption") or ""),
        "center": ch.get("center") or {"x": 0.5, "y": 0.5},
        "summary": summary,
        "identity_tags": identity_display,
        "appearance_tags": [] if is_oc_slot else _appearance_display_tags(bundle),
        "action_tags": (
            list(ch.get("preserved_action_tags") or [])
            if is_oc_slot and ch.get("preserved_action_tags")
            else _action_display_tags(bundle)
        ),
        "creature_tags": creature_tags,
        "has_creature": slot_is_creature or bool(creature_tags),
        "is_creature_slot": slot_is_creature,
        "bundle": bundle,
        "role": analysis.role,
        "identity_name": analysis.identity_name,
        "display_name": analysis.display_name,
        "replaceable": analysis.replaceable,
        "token_groups": analysis.token_groups,
        "token_analysis": [item.to_dict() for item in analysis.tokens],
        "oc": analysis.oc.to_dict(),
    }
    if is_oc_slot and (preset_summary or oc_preview):
        slot["summary"] = preset_summary or str(ch.get("oc_label") or "OC")
        slot["identity_tags"] = [slot["summary"]]
    elif analysis.identity_name:
        slot["summary"] = analysis.identity_name
        slot["identity_tags"] = [analysis.identity_name]
    elif not preset_summary and not is_oc_slot:
        slot["summary"] = ""
        slot["identity_tags"] = [analysis.display_name]
    if analysis.oc.matched:
        slot["oc_matched"] = True
        slot["oc_label"] = analysis.oc.label
        if analysis.oc.preview:
            slot["oc_preview"] = analysis.oc.preview
    if marker_num:
        slot["marker"] = marker_label(int(marker_num))
        slot["marker_num"] = int(marker_num)
    if is_oc_slot or oc_preview:
        slot["is_oc"] = True
        slot["oc_preview"] = oc_preview or _oc_caption_preview(caption)
    return slot


_UNKNOWN_ROLE_DISPLAY = {
    "male": "\u672a\u77e5\u7537\u89d2\u8272",
    "female": "\u672a\u77e5\u5973\u89d2\u8272",
    "unknown": "\u672a\u77e5\u89d2\u8272",
}


def _sync_slot_contract_after_gender(chars: list[dict[str, Any]]) -> None:
    """Keep the new slot contract aligned after legacy gender inference mutates slots."""
    unknown_labels = set(_UNKNOWN_ROLE_DISPLAY.values())
    for ch in chars:
        bundle = ch.get("bundle") if isinstance(ch.get("bundle"), dict) else {}
        role = str(ch.get("role") or "unknown").strip().lower()
        gender = str(ch.get("gender") or bundle.get("gender") or "").strip().lower()
        if role not in {"male", "female"} and gender in {"male", "female"}:
            role = gender
            ch["role"] = role
        if role in {"male", "female"}:
            ch["replaceable"] = True
        if ch.get("identity_name") or ch.get("is_oc") or ch.get("oc_matched"):
            continue
        display_name = str(ch.get("display_name") or "").strip()
        if not display_name or display_name in unknown_labels:
            ch["display_name"] = _UNKNOWN_ROLE_DISPLAY.get(
                role, _UNKNOWN_ROLE_DISPLAY["unknown"]
            )
        if not str(ch.get("summary") or "").strip():
            ch["identity_tags"] = [str(ch.get("display_name") or "")]


def _infer_slot_gender(caption: str, buckets: dict[str, list[str]] | None = None) -> str:
    """单槽咒语快速推断（完整多槽推断见 slot_gender.apply_slot_genders）。"""
    from slot_gender import resolve_slot_genders

    meta = resolve_slot_genders(
        [{"char_caption": caption, "uc_caption": ""}],
        base_caption="",
    )
    return str(meta[0].get("gender") or "unknown") if meta else "unknown"


def _slot_gender(ch: dict) -> str:
    return slot_gender_of(ch)


def bundle_from_caption(caption: str, *, gender: str = "") -> dict[str, Any]:
    buckets = classify_caption_buckets(caption)
    if not gender:
        gender = _infer_slot_gender(caption, buckets)
        if gender == "unknown" and buckets["gender"]:
            g = buckets["gender"][0].lower()
            gender = "male" if "boy" in g or "male" in g else "female"
    identity = buckets["identity"] + buckets["gender"]
    return {
        "gender": gender or "unknown",
        "identity": identity,
        "body": buckets["body"],
        "appearance": buckets["appearance"],
        "creature": buckets.get("creature") or [],
        "action": buckets["action"],
    }

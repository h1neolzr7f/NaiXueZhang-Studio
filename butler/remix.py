"""Deep Remix Module used by Butler draft and Generation Job adapters."""

from __future__ import annotations

import copy
from typing import Any
import re

from nai_char import list_char_presets, prepare_work_draft
from studio_service import build_studio_draft, import_from_work


_MODES = frozenset(
    {
        "replace",
        "replace_male",
        "replace_female",
        "replace_creature",
        "creature_to_partner",
        "clone",
        "replace_multi",
    }
)
_TARGETS = frozenset(
    {
        "auto",
        "auto_male",
        "auto_female",
        "auto_creature",
        "all_male",
        "all_female",
    }
)


class StyleReferenceNotFound(LookupError):
    """Raised when a stable NAI Style Reference identity no longer exists."""


def _text(value: Any, *, limit: int) -> str:
    return str(value or "").replace("\x00", "").strip()[:limit]


def _positive_int(value: Any, *, name: str, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是整数") from exc
    if parsed < 0:
        raise ValueError(f"{name} 不能为负数")
    return parsed


def _target(value: Any) -> int | str:
    if value in (None, ""):
        return "auto"
    if isinstance(value, int) or str(value).strip().isdigit():
        parsed = int(value)
        if parsed > 99:
            raise ValueError("target 超出范围")
        return parsed
    target = _text(value, limit=40).lower()
    if target not in _TARGETS:
        raise ValueError("target 不受支持")
    return target


def character_preset_catalog() -> list[dict[str, str]]:
    """Compact, non-secret catalog used by both planner context and validation."""
    return [
        {
            "id": str(item.get("id") or "").strip(),
            "label": str(item.get("label") or item.get("id") or "").strip(),
            "gender": str(item.get("gender") or "").strip().lower(),
        }
        for item in list_char_presets()
        if str(item.get("id") or "").strip()
    ]


def style_preset_catalog(*, include_style: bool = False) -> list[dict[str, str]]:
    """Return the manual tool's style catalog without exposing unrelated config."""
    from char_swap_config import load_config as load_char_swap_config

    catalog: list[dict[str, str]] = []
    for item in load_char_swap_config().get("style_presets") or []:
        preset_id = str(item.get("id") or "").strip()
        if not preset_id:
            continue
        entry = {
            "id": preset_id,
            "label": str(item.get("label") or preset_id).strip(),
        }
        if include_style:
            entry["style"] = str(item.get("style", item.get("replace", "")) or "")
        catalog.append(entry)
    return catalog


def _preset_key(value: Any) -> str:
    text = str(value or "").strip().casefold()
    return re.sub(r"[\s_\-—·・()（）\[\]【】]+", "", text)


def _resolve_named_preset(selector: str, presets: list[dict[str, str]]) -> dict[str, str] | None:
    raw = str(selector or "").strip()
    if not raw:
        return None
    exact_id = next((item for item in presets if item["id"].casefold() == raw.casefold()), None)
    if exact_id:
        return exact_id
    key = _preset_key(raw)
    exact_label = [item for item in presets if _preset_key(item["label"]) == key]
    if exact_label:
        return exact_label[0]
    partial = [item for item in presets if key and key in _preset_key(item["label"])]
    return partial[0] if len(partial) == 1 else None


def _multi_preset_candidates(selector: str, presets: list[dict[str, str]]) -> list[dict[str, str]]:
    raw = str(selector or "").strip()
    if not raw:
        return []
    exact_id = [item for item in presets if item["id"].casefold() == raw.casefold()]
    if exact_id:
        return exact_id
    key = _preset_key(raw)
    exact_label = [item for item in presets if _preset_key(item["label"]) == key]
    if exact_label:
        return exact_label
    return [
        item
        for item in presets
        if key and (key in _preset_key(item["label"]) or key in _preset_key(item["id"]))
    ]


def _normalize_multi_replacements(
    raw_replacements: Any,
    presets: list[dict[str, str]],
) -> list[dict[str, Any]]:
    if not isinstance(raw_replacements, list) or not raw_replacements:
        raise ValueError("多角色替换至少需要一个角色预设")
    if len(raw_replacements) > 6:
        raise ValueError("NAI V4 最多支持 6 个角色槽")

    rows: list[tuple[dict[str, Any], list[dict[str, str]]]] = []
    reserved_genders: set[str] = set()
    for raw in raw_replacements:
        if not isinstance(raw, dict):
            raise ValueError("多角色替换项必须是对象")
        selector = _text(
            raw.get("preset_id", raw.get("name", raw.get("label"))), limit=120
        )
        if not selector:
            raise ValueError("多角色替换项缺少角色名称或 preset_id")
        candidates = _multi_preset_candidates(selector, presets)
        requested_gender = _text(raw.get("gender"), limit=12).lower()
        if requested_gender:
            if requested_gender not in {"male", "female"}:
                raise ValueError("多角色替换 gender 须为 male 或 female")
            candidates = [item for item in candidates if item.get("gender") == requested_gender]
        if not candidates:
            raise ValueError(f"没有找到角色预设“{selector}”，请先在手动换角工具中添加或选择角色")
        if len(candidates) == 1 and candidates[0].get("gender") in {"male", "female"}:
            reserved_genders.add(str(candidates[0]["gender"]))
        rows.append((raw, candidates))

    normalized: list[dict[str, Any]] = []
    gender_ordinals = {"male": 0, "female": 0}
    for raw, candidates in rows:
        if len(candidates) > 1:
            unused_gender = [
                item
                for item in candidates
                if item.get("gender") in {"male", "female"}
                and item.get("gender") not in reserved_genders
            ]
            if len(unused_gender) == 1:
                candidates = unused_gender
        if len(candidates) != 1:
            selector = _text(
                raw.get("preset_id", raw.get("name", raw.get("label"))), limit=120
            )
            labels = "、".join(str(item.get("label") or item.get("id")) for item in candidates[:5])
            raise ValueError(f"角色名“{selector}”有多个候选：{labels}；请说明男/女或给出准确名称")
        preset = candidates[0]
        gender = str(preset.get("gender") or "").lower()
        item: dict[str, Any] = {
            "preset_id": str(preset["id"]),
            "preset_label": str(preset.get("label") or preset["id"]),
            "gender": gender,
            "mode": f"replace_{gender}" if gender in {"male", "female"} else "replace",
        }
        target = raw.get("target_char_index", raw.get("target"))
        gender_slot = raw.get("gender_slot_index")
        if target not in (None, ""):
            item["target_char_index"] = _target(target)
        else:
            ordinal = _positive_int(
                gender_slot,
                name="gender_slot_index",
                default=gender_ordinals.get(gender, 0),
            )
            item["gender_slot_index"] = int(ordinal or 0)
            if gender in gender_ordinals:
                gender_ordinals[gender] = int(ordinal or 0) + 1
        match_keys = raw.get("match_identity_keys")
        if isinstance(match_keys, list):
            item["match_identity_keys"] = [
                _text(value, limit=160) for value in match_keys[:20] if _text(value, limit=160)
            ]
        normalized.append(item)
        if gender in {"male", "female"}:
            reserved_genders.add(gender)
    return normalized


def _resolve_style_reference(
    style_raw: dict[str, Any], args: dict[str, Any]
) -> dict[str, Any] | None:
    """Resolve one stable local style identity for every Remix adapter."""

    reference_id = _text(
        style_raw.get("reference_id", args.get("style_reference_id")), limit=80
    )
    reference_name = _text(
        style_raw.get("reference_name", args.get("style_reference_name")), limit=300
    )
    if not reference_id and not reference_name:
        return None
    if any(
        style_raw.get(key) not in (None, "")
        for key in ("preset_id", "name", "find", "replace")
    ):
        raise ValueError("画风资料不能同时指定手动画风预设或替换文本")

    from reference_catalog import get_reference_catalog

    catalog = get_reference_catalog()
    if reference_id:
        item = catalog.get_style(reference_id)
        if item is None:
            raise StyleReferenceNotFound("指定的 NAI 画风资料不存在")
        return item

    result = catalog.search_styles(
        query=reference_name,
        source=_text(
            style_raw.get("reference_source", args.get("reference_source")), limit=80
        ),
        limit=12,
    )
    items = list(result.get("items") or [])
    wanted = reference_name.casefold()
    exact = [
        item
        for item in items
        if wanted
        in {
            str(item.get("style_id") or "").casefold(),
            str(item.get("label") or "").casefold(),
            str(item.get("tag") or "").casefold(),
        }
    ]
    matches = exact or items
    if not matches:
        raise StyleReferenceNotFound(f"NAI 画风资料库中没有找到“{reference_name}”")
    if len(matches) > 1 and not exact:
        labels = "、".join(
            str(item.get("tag") or item.get("label")) for item in matches[:5]
        )
        raise ValueError(f"画风名“{reference_name}”有多个候选：{labels}；请给出更准确名称")
    return matches[0]


def normalize_remix_recipe(args: dict[str, Any]) -> dict[str, Any]:
    """Validate natural-language Remix arguments into the existing recipe shape."""

    character_raw = args.get("character") or {}
    style_raw = args.get("style") or {}
    if not isinstance(character_raw, dict) or not isinstance(style_raw, dict):
        raise ValueError("character/style 必须是对象")

    from char_swap_config import load_config as load_char_swap_config

    manual_config = load_char_swap_config()
    raw_preset_id = _text(
        character_raw.get("preset_id", args.get("character_preset_id")), limit=120
    )
    character_name = _text(
        character_raw.get("name", character_raw.get("label", args.get("character_name"))),
        limit=120,
    )
    presets = character_preset_catalog()
    raw_replacements = character_raw.get("replacements")
    if raw_replacements not in (None, []):
        if any(
            value not in (None, "")
            for value in (
                raw_preset_id,
                character_name,
                character_raw.get("source_work_id", args.get("source_work_id")),
                character_raw.get("custom_char_caption", args.get("custom_char_caption")),
            )
        ):
            raise ValueError("多角色替换不能同时指定单角色预设或自定义角色")
        replacements = _normalize_multi_replacements(raw_replacements, presets)
        transform = {
            "enabled": True,
            "mode": "replace_multi",
            "replacements": replacements,
            "skip_missing_slots": bool(character_raw.get("skip_missing_slots", False)),
            "preserve_action": bool(
                character_raw.get("preserve_action", manual_config.get("preserve_action", False))
            ),
            "preserve_center": bool(
                character_raw.get("preserve_center", manual_config.get("preserve_center", True))
            ),
            "replace_creature": False,
        }
    else:
        transform = None
    resolved_preset = _resolve_named_preset(raw_preset_id or character_name, presets)
    if character_name and not resolved_preset:
        raise ValueError(f"没有找到角色预设“{character_name}”，请先在手动换角工具中添加或选择角色")
    preset_id = str((resolved_preset or {}).get("id") or raw_preset_id)
    source_work_id = _positive_int(
        character_raw.get("source_work_id", args.get("source_work_id")),
        name="source_work_id",
    )
    custom_caption = _text(
        character_raw.get("custom_char_caption", args.get("custom_char_caption")),
        limit=2400,
    )
    has_character = bool(transform or preset_id or source_work_id or custom_caption)

    if transform is None:
        transform = {"enabled": has_character}
    if has_character and transform.get("mode") != "replace_multi":
        gender = _text(character_raw.get("gender", args.get("gender")), limit=12).lower()
        if gender and gender not in {"male", "female"}:
            raise ValueError("gender 须为 male 或 female")
        if not gender and preset_id:
            preset = resolved_preset or next(
                (item for item in presets if item["id"] == preset_id), None
            )
            preset_gender = str((preset or {}).get("gender") or "").lower()
            if preset_gender in {"male", "female"}:
                gender = preset_gender
        mode = _text(character_raw.get("mode", args.get("remix_mode")), limit=40).lower()
        if not mode:
            mode = "replace_female" if gender == "female" else "replace_male" if gender == "male" else "replace"
        elif mode == "replace" and gender in {"male", "female"}:
            mode = f"replace_{gender}"
        if mode not in _MODES:
            raise ValueError("mode 不受支持")
        if not gender and mode == "replace_female":
            gender = "female"
        elif not gender and mode == "replace_male":
            gender = "male"
        transform.update(
            {
                "mode": mode,
                "target_char_index": _target(
                    character_raw.get("target", character_raw.get("target_char_index"))
                ),
                    "preserve_action": bool(
                        character_raw.get("preserve_action", manual_config.get("preserve_action", False))
                    ),
                    "preserve_center": bool(
                        character_raw.get("preserve_center", manual_config.get("preserve_center", True))
                    ),
                "replace_creature": bool(character_raw.get("replace_creature", False)),
            }
        )
        if gender:
            transform["gender"] = gender
        if preset_id:
            transform["preset_id"] = preset_id
            if resolved_preset:
                transform["preset_label"] = resolved_preset["label"]
        if source_work_id:
            transform["source_work_id"] = source_work_id
            transform["source_page_index"] = _positive_int(
                character_raw.get("source_page_index"),
                name="source_page_index",
                default=0,
            )
        if custom_caption:
            transform["custom_char_caption"] = custom_caption

    style_find = _text(style_raw.get("find", args.get("style_find")), limit=600)
    style_reference_item = _resolve_style_reference(style_raw, args)
    raw_style_preset_id = _text(
        style_raw.get("preset_id", args.get("style_preset_id")), limit=120
    )
    style_name = _text(
        style_raw.get("name", style_raw.get("label", args.get("style_name"))), limit=120
    )
    style_presets = style_preset_catalog(include_style=True)
    resolved_style_preset = _resolve_named_preset(
        raw_style_preset_id or style_name, style_presets
    )
    if (style_name or raw_style_preset_id) and not resolved_style_preset:
        selector = style_name or raw_style_preset_id
        raise ValueError(f"没有找到画风预设“{selector}”，请先在手动换画风工具中添加或选择画风")
    style_replace = _text(
        (style_reference_item or {}).get(
            "tag",
            (resolved_style_preset or {}).get(
                "style",
                style_raw.get("replace", args.get("style_replace", args.get("style_append"))),
            ),
        ),
        limit=3000,
    )
    if style_reference_item is not None and not style_replace:
        raise ValueError("指定的 NAI 画风资料没有可用标签")
    has_style = bool(style_reference_item or resolved_style_preset or style_find or style_replace)
    style: dict[str, Any] = {}
    if has_style:
        style_mode = (
            "preset"
            if style_reference_item or resolved_style_preset
            else _text(style_raw.get("mode"), limit=20).lower()
        )
        requested_style_mode = _text(style_raw.get("mode"), limit=20).lower()
        if style_reference_item and requested_style_mode:
            if requested_style_mode not in {"preset", "append", "replace_detected"}:
                raise ValueError("画风资料只支持整体替换或追加")
            style_mode = "append" if requested_style_mode == "append" else "preset"
        if not style_mode:
            style_mode = "replace" if style_find else "append"
        if style_mode not in {"replace", "append", "preset"}:
            raise ValueError("style mode 须为 preset、replace 或 append")
        if style_mode == "replace" and not style_find:
            raise ValueError("替换画风需要 style.find")
        if style_mode == "append" and not style_replace:
            raise ValueError("追加画风需要 style.replace")
        style = {
            "mode": style_mode,
            "find": style_find,
            "replace": style_replace,
            "case_insensitive": bool(style_raw.get("case_insensitive", True)),
        }
        if resolved_style_preset:
            style["preset_id"] = resolved_style_preset["id"]
            style["preset_label"] = resolved_style_preset["label"]
        if style_reference_item:
            style["reference"] = {
                "style_id": str(style_reference_item.get("style_id") or ""),
                "label": str(
                    style_reference_item.get("label")
                    or style_reference_item.get("tag")
                    or style_replace
                ),
                "tag": style_replace,
                "kind": str(style_reference_item.get("kind") or "style"),
                "source": str(style_reference_item.get("source") or ""),
                "provenance": copy.deepcopy(style_reference_item.get("provenance") or {}),
            }

    if not has_character and not has_style:
        raise ValueError("至少需要换角或换画风参数")

    sanitize_raw = args.get("sanitize", None)
    if isinstance(sanitize_raw, dict):
        sanitize = {
            "enabled": bool(sanitize_raw.get("enabled", True)),
            "filter_racial": bool(sanitize_raw.get("filter_racial", manual_config.get("sanitize_racial", True))),
            "filter_gore": bool(sanitize_raw.get("filter_gore", manual_config.get("sanitize_gore", True))),
            "filter_creature": bool(sanitize_raw.get("filter_creature", manual_config.get("sanitize_creature", False))),
        }
    else:
        sanitize = {
            "enabled": bool(
                manual_config.get("auto_sanitize_on_generate", True)
                if sanitize_raw is None else sanitize_raw
            ),
            "filter_racial": bool(manual_config.get("sanitize_racial", True)),
            "filter_gore": bool(manual_config.get("sanitize_gore", True)),
            "filter_creature": bool(manual_config.get("sanitize_creature", False)),
        }
    return {
        "transform": transform,
        "style": style,
        "sanitize": sanitize,
        "prompt_profile": str(manual_config.get("prompt_profile") or "native"),
    }


def prepare_remix_draft(args: dict[str, Any]) -> dict[str, Any]:
    """Prepare a Remix Studio Draft without persisting or generating anything."""

    work_id = int(args["work_id"])
    gallery_id = str(args.get("gallery_id") or "site").strip().lower()
    recipe = dict(args.get("remix_recipe") or {})
    transform = recipe.get("transform") or {}
    if gallery_id != "site" and transform.get("enabled"):
        raise ValueError("手动换角依赖网站图库的 NovelAI v4 角色槽；法典/Q群作品暂不支持同质量换角")
    page_index = int(args.get("page_index") or 0)
    recipe["generation"] = dict(args.get("generation") or {})
    source = import_from_work(work_id, page_index, gallery_id)
    prepare_kwargs: dict[str, Any] = {"recipe": recipe}
    if gallery_id != "site":
        prepare_kwargs.update(
            {"gallery_id": gallery_id, "patched_comment": source.get("comment") or {}}
        )
    prepared = prepare_work_draft(work_id, page_index, **prepare_kwargs)
    if not prepared.get("ok"):
        raise ValueError(str(prepared.get("message") or "Remix 创作方案准备失败"))
    draft = build_studio_draft(
        prepared["patched_comment"],
        work_id=work_id,
        page_index=page_index,
        title=str(source.get("title") or f"作品 {work_id}"),
        thumb=str(source.get("thumb") or ""),
        batch_count=int(args.get("batch_count") or 1),
    )
    draft["galleryId"] = gallery_id
    reference = copy.deepcopy(transform.get("reference") or {})
    if reference:
        draft["reference"] = {
            "referenceId": str(reference.get("reference_id") or ""),
            "label": str(reference.get("label") or ""),
            "source": str(reference.get("source") or ""),
            "sourceId": str(reference.get("source_id") or ""),
            "copyright": str(reference.get("copyright") or ""),
            "provenance": copy.deepcopy(reference.get("provenance") or {}),
            "mode": str(transform.get("mode") or "replace"),
            "target": transform.get("target_char_index", "auto"),
        }
        comment = draft.get("comment")
        if isinstance(comment, dict):
            comment["_aitag_anima_reference"] = copy.deepcopy(draft["reference"])
    style_spec = recipe.get("style") or {}
    style_reference = copy.deepcopy(style_spec.get("reference") or {})
    if style_reference:
        draft["styleReference"] = {
            "styleId": str(style_reference.get("style_id") or ""),
            "label": str(style_reference.get("label") or ""),
            "tag": str(style_reference.get("tag") or ""),
            "kind": str(style_reference.get("kind") or "style"),
            "source": str(style_reference.get("source") or ""),
            "provenance": copy.deepcopy(style_reference.get("provenance") or {}),
            "mode": str(style_spec.get("mode") or "preset"),
        }
        comment = draft.get("comment")
        if isinstance(comment, dict):
            comment["_aitag_style_reference"] = copy.deepcopy(draft["styleReference"])
    message = str(prepared.get("message") or "Remix 创作方案已就绪")
    if reference.get("label"):
        message = f"{reference['label']} · {message}"
    if style_reference.get("label"):
        message = f"{style_reference['label']} · {message}"
    result = {
        "ok": True,
        "tool": "prepare_remix",
        "remix_kind": (
            "combined"
            if transform.get("enabled") and recipe.get("style")
            else "character"
            if transform.get("enabled")
            else "style"
        ),
        "title": draft.get("title") or f"作品 {work_id}",
        "thumb": draft.get("thumb") or "",
        "draft": draft,
        "studio_url": f"/studio?butler=1&remix=1&gallery={gallery_id}",
        "summary": {
            "characters": len(prepared.get("chars") or []),
            "style_replacements": int(prepared.get("style_replacements") or 0),
            "style_applied": bool(prepared.get("style_applied")),
            "style_preset_label": str((recipe.get("style") or {}).get("preset_label") or ""),
            "style_reference_label": str(style_reference.get("label") or ""),
            "sanitized": len(prepared.get("sanitize_removed") or []),
        },
        "message": message,
    }
    if reference:
        result["reference"] = reference
    if style_reference:
        result["style_reference"] = style_reference
    return result


def prepare_style_reference_draft(
    style_id: str,
    *,
    gallery_id: str,
    work_id: int,
    page_index: int = 0,
    mode: str = "preset",
) -> dict[str, Any]:
    """Prepare one editable Remix draft from a stable NAI Style Reference.

    This is the shared Interface for desktop and Butler adapters. It resolves
    provenance locally and reuses the same style Implementation as the manual
    Remix tool; it never starts a Generation Job.
    """

    normalized_mode = _text(mode, limit=20).lower() or "preset"
    if normalized_mode not in {"preset", "append"}:
        raise ValueError("画风资料应用方式只支持 preset 或 append")
    recipe = normalize_remix_recipe(
        {"style": {"mode": normalized_mode, "reference_id": style_id}}
    )
    result = prepare_remix_draft(
        {
            "gallery_id": gallery_id,
            "work_id": int(work_id),
            "page_index": int(page_index),
            "remix_recipe": recipe,
            "generation": {},
        }
    )
    result["provider"] = "local"
    result["generation_calls"] = 0
    return result


def build_remix_targets(args: dict[str, Any]) -> list[dict[str, Any]]:
    """Build per-copy Generation Job targets while keeping one shared recipe."""

    generation = dict(args.get("generation") or {})
    base_seed = generation.get("seed")
    copies = int(args.get("copies_per_work") or 1)
    targets: list[dict[str, Any]] = []
    offset = 0
    refs = args.get("work_refs") or [
        {"gallery_id": args.get("gallery_id") or "site", "work_id": work_id}
        for work_id in args.get("work_ids") or []
    ]
    for ref in refs:
        pages = ref.get("page_indexes") or [int(args.get("page_index") or 0)]
        for page_index in pages:
            for _ in range(copies):
                settings = dict(generation)
                if base_seed is not None:
                    settings["seed"] = int(base_seed) + offset
                targets.append(
                    {
                        "gallery_id": str(ref.get("gallery_id") or "site"),
                        "work_id": int(ref["work_id"]),
                        "page_index": int(page_index),
                        "generation": settings,
                    }
                )
                offset += 1
    return targets

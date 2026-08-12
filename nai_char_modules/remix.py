"""Compile a Remix Recipe into one deterministic Studio Draft.

The compiler owns orchestration, while callers inject the character-domain
primitives it needs.  This keeps the Module independent of the legacy facade
and makes the compatibility boundary an explicit Adapter instead of a cycle.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class RemixPrimitives:
    """Narrow dependency Interface required by :func:`compile_remix_recipe`."""

    extract_chars: Callable[..., dict[str, Any]]
    merge_extract_with_draft: Callable[[dict, dict], dict[str, Any]]
    clean_plain_ark_workbench_draft: Callable[..., dict]
    is_all_gender_target: Callable[[Any], bool]
    pick_target_char_index: Callable[..., int]
    infer_preset_gender: Callable[[dict], str]
    transform: Callable[..., dict[str, Any]]
    apply_style_payload: Callable[[dict], dict[str, Any]]
    replace_style_in_comment: Callable[..., tuple[dict, int]]
    sanitize_comment: Callable[..., dict[str, Any]]
    chars_from_comment: Callable[[dict], tuple[list[dict], str]]


def apply_generation_settings(
    comment: dict[str, Any],
    settings: dict[str, Any] | None,
) -> dict[str, Any]:
    patched = copy.deepcopy(comment or {})
    values = dict(settings or {})
    for key in ("width", "height", "steps", "scale", "sampler", "seed"):
        if key in values and values[key] is not None:
            patched[key] = values[key]

    prompt = str(values.get("prompt") or "").strip()
    if prompt:
        patched["prompt"] = prompt
        v4_prompt = copy.deepcopy(patched.get("v4_prompt") or {})
        caption = copy.deepcopy(v4_prompt.get("caption") or {})
        caption["base_caption"] = prompt
        v4_prompt["caption"] = caption
        patched["v4_prompt"] = v4_prompt

    uc = str(values.get("uc") or values.get("negative_prompt") or "").strip()
    if uc:
        patched["uc"] = uc
        patched["negative_prompt"] = uc
        negative = copy.deepcopy(patched.get("v4_negative_prompt") or {})
        caption = copy.deepcopy(negative.get("caption") or {})
        caption["base_caption"] = uc
        negative["caption"] = caption
        patched["v4_negative_prompt"] = negative
    return patched


def compile_remix_recipe(
    work_id: int,
    page_index: int = 0,
    *,
    recipe: dict[str, Any],
    patched_comment: dict | None = None,
    gallery_id: str = "site",
    primitives: RemixPrimitives,
) -> dict[str, Any]:
    """Compile character, style, sanitation and generation settings in order."""

    source_gallery_id = str(gallery_id or "site").strip().lower()
    if source_gallery_id == "site":
        data = primitives.extract_chars(work_id, page_index)
    else:
        # Prefer local gallery DB metadata; fall back to prepared draft comment.
        try:
            data = primitives.extract_chars(
                work_id, page_index, gallery_id=source_gallery_id
            )
        except TypeError:
            # Older extract_chars callables without gallery_id kw.
            data = primitives.extract_chars(work_id, page_index)
        except Exception:
            data = {}
        if (not data or not data.get("chars")) and isinstance(patched_comment, dict):
            data = primitives.merge_extract_with_draft(data or {}, patched_comment)
        elif not data or not (data.get("chars") or data.get("base_caption")):
            if isinstance(patched_comment, dict):
                data = primitives.merge_extract_with_draft({}, patched_comment)
            else:
                raise ValueError(
                    "Non-site galleries require local metadata or a prepared Prompt draft"
                )

    characters = data.get("chars") or []
    transform_spec = recipe.get("transform") or {}
    style_spec = recipe.get("style") or {}
    sanitize_spec = recipe.get("sanitize") or {}
    needs_character_slot = bool(
        transform_spec.get("enabled", True)
        and (
            transform_spec.get("preset_id")
            or transform_spec.get("custom_bundle")
            or transform_spec.get("source_work_id")
            or transform_spec.get("custom_char_caption")
            or transform_spec.get("replacements")
        )
    )
    if not characters and needs_character_slot:
        return {
            "ok": False,
            "work_id": work_id,
            "page_index": page_index,
            "skipped": True,
            "message": "No NovelAI character slot is available",
        }

    transform_applied = False
    from_workbench = False
    style_count = 0
    style_applied = False

    workbench_draft = patched_comment
    if isinstance(workbench_draft, dict) and workbench_draft.get("v4_prompt"):
        workbench_draft = primitives.clean_plain_ark_workbench_draft(
            workbench_draft,
            work_id,
            page_index,
            gallery_id=source_gallery_id,
        )
        data = primitives.merge_extract_with_draft(data, workbench_draft)
        patched_out = copy.deepcopy(workbench_draft)
        output_characters = copy.deepcopy(data["chars"])
        from_workbench = True
    elif transform_spec.get("enabled", True) and (
        transform_spec.get("preset_id")
        or transform_spec.get("custom_bundle")
        or transform_spec.get("source_work_id")
        or transform_spec.get("custom_char_caption")
        or transform_spec.get("replacements")
    ):
        replace_creature = bool(
            transform_spec.get("replace_creature", recipe.get("replace_creature", False))
        )
        mode = str(transform_spec.get("mode") or "replace_male")
        if replace_creature and mode not in {
            "clone", "replace", "replace_male", "replace_female", "replace_multi"
        }:
            mode = "creature_to_partner"
        target_index = transform_spec.get("target_char_index")
        if mode == "creature_to_partner" and target_index in {
            None, "", "auto", "auto_male", "auto_female"
        }:
            target_index = "auto_creature"
        keep_target = primitives.is_all_gender_target(target_index) or mode == "replace_multi"
        resolved_index = target_index if keep_target else primitives.pick_target_char_index(
            characters,
            target_index,
            mode="replace_male" if mode == "creature_to_partner" else mode,
            prefer_creature=replace_creature,
        )
        transform_payload = {
            **transform_spec,
            "mode": mode,
            "gender": transform_spec.get("gender")
            or primitives.infer_preset_gender(transform_spec),
            "target_work_id": work_id,
            "target_page_index": page_index,
            "target_char_index": resolved_index,
            "preserve_action": bool(
                transform_spec.get("preserve_action", recipe.get("preserve_action", True))
            ),
            "preserve_center": bool(
                transform_spec.get("preserve_center", recipe.get("preserve_center", True))
            ),
        }
        transformed = primitives.transform(
            transform_payload,
            source_data=data,
            include_style_slots=False,
        )
        if transformed.get("skipped"):
            return {
                "ok": False,
                "work_id": work_id,
                "page_index": page_index,
                "skipped": True,
                "message": transformed.get("message")
                or "No matching source character on this page; skipped.",
                "patched_comment": transformed.get("patched_comment"),
                "chars": transformed.get("chars") or [],
                "transform_applied": False,
                "from_workbench": from_workbench,
                "style_replacements": 0,
                "sanitize_removed": [],
                "summary": "",
            }
        patched_out = transformed["patched_comment"]
        output_characters = transformed.get("chars") or []
        transform_applied = True
    else:
        patched_out = copy.deepcopy(data["comment"])
        output_characters = copy.deepcopy(characters)

    if not from_workbench:
        style_mode = str(style_spec.get("mode") or "").strip().lower()
        find = str(style_spec.get("find") or "").strip()
        replacement = str(style_spec.get("replace") or "")
        if style_mode in {"preset", "replace_detected"}:
            styled = primitives.apply_style_payload(
                {"patched_comment": patched_out, "mode": "preset", "replace": replacement}
            )
            patched_out = styled["patched_comment"]
            style_count = int(styled.get("replacements") or 0)
            style_applied = bool(styled.get("style_applied"))
        elif style_mode == "append":
            styled = primitives.apply_style_payload(
                {"patched_comment": patched_out, "mode": "append", "replace": replacement}
            )
            patched_out = styled["patched_comment"]
            style_count = int(styled.get("replacements") or 0)
            style_applied = bool(styled.get("style_applied"))
        elif find:
            patched_out, style_count = primitives.replace_style_in_comment(
                patched_out,
                find,
                replacement,
                case_insensitive=bool(style_spec.get("case_insensitive", True)),
            )
            style_applied = style_count > 0

    removed: list[dict] = []
    try:
        from char_swap_config import load_config

        default_auto_sanitize = bool(load_config().get("auto_sanitize_on_generate", True))
    except Exception:
        default_auto_sanitize = True
    if sanitize_spec.get("enabled", recipe.get("auto_sanitize", default_auto_sanitize)):
        sanitized = primitives.sanitize_comment(
            patched_out,
            racial=bool(sanitize_spec.get("filter_racial", True)),
            gore=bool(sanitize_spec.get("filter_gore", True)),
            creature=bool(sanitize_spec.get("filter_creature", False)),
        )
        patched_out = sanitized["patched_comment"]
        removed = sanitized.get("removed") or []
        if sanitized.get("blocked"):
            return {
                "ok": False,
                "work_id": work_id,
                "page_index": page_index,
                "skipped": True,
                "message": "Sanitization left an empty character slot",
                "empty_slots": sanitized.get("empty_slots") or [],
            }

    patched_out = apply_generation_settings(
        patched_out,
        recipe.get("generation") if isinstance(recipe.get("generation"), dict) else {},
    )
    output_characters, _ = primitives.chars_from_comment(patched_out)

    summary = ""
    if output_characters:
        summary = str(output_characters[0].get("summary") or work_id)
        for character in output_characters:
            caption = str(character.get("char_caption") or "").lower()
            if "doctor_(arknights)" in caption:
                summary = "博士"
                break

    return {
        "ok": True,
        "work_id": work_id,
        "page_index": page_index,
        "patched_comment": patched_out,
        "chars": output_characters,
        "transform_applied": transform_applied,
        "from_workbench": from_workbench,
        "style_replacements": style_count,
        "style_applied": style_applied,
        "sanitize_removed": removed,
        "summary": summary,
        "message": "Workbench draft ready" if from_workbench else "Draft ready",
    }

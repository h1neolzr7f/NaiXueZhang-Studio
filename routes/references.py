"""Desktop NAI character-reference catalog routes."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Query

from nai_anima_adapter import adapt_anima_character, apply_anima_character_to_comment
from nai_prompt_optimizer import _prompt_snapshot
from reference_catalog import get_reference_catalog
from butler.remix import StyleReferenceNotFound, prepare_style_reference_draft

router = APIRouter(prefix="/api/nai/references")
IMPORT_BATCH_MAX = 1_000


@router.get("")
def api_reference_search(
    q: str = Query("", max_length=200),
    gender: str = Query("", max_length=20),
    copyright: str = Query("", max_length=300),
    source: str = Query("", max_length=80),
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    normalized_gender = gender.strip().lower()
    if normalized_gender not in {"", "female", "male", "other", "unknown"}:
        raise HTTPException(status_code=400, detail="无效的性别筛选")
    return get_reference_catalog().search(
        query=q,
        gender=normalized_gender,
        copyright_name=copyright,
        source=source,
        limit=limit,
        offset=offset,
    )


@router.get("/stats")
def api_reference_stats() -> dict:
    return get_reference_catalog().stats()


@router.post("/import")
def api_reference_import(payload: dict = Body(default_factory=dict)) -> dict:
    records = payload.get("records")
    if not isinstance(records, list):
        raise HTTPException(status_code=400, detail="records 必须是角色对象数组")
    if len(records) > IMPORT_BATCH_MAX:
        raise HTTPException(status_code=413, detail=f"每批最多 {IMPORT_BATCH_MAX} 条，请分批导入")
    try:
        return get_reference_catalog().import_records(
            records,
            source=str(payload.get("source") or "animadex"),
            source_label=str(payload.get("source_label") or "AnimaDex"),
            version=str(payload.get("version") or ""),
            license_name=str(payload.get("license") or ""),
            model=str(payload.get("model") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/custom")
def api_reference_custom_add(payload: dict = Body(default_factory=dict)) -> dict:
    label = str(payload.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=400, detail="角色显示名 (label) 不能为空")
    gender = str(payload.get("gender") or "female").strip().lower()
    if gender not in {"female", "male", "other", "unknown"}:
        gender = "female"
    copyright_name = str(payload.get("copyright") or "自定义角色").strip()
    trigger = str(payload.get("trigger") or "").strip()
    caption = str(payload.get("character_caption") or payload.get("char_caption") or "").strip()

    tags = [t.strip() for t in caption.replace("\n", ",").split(",") if t.strip()]
    if trigger and trigger not in tags:
        tags.insert(0, trigger)
    subject_tag = "1girl" if gender == "female" else ("1boy" if gender == "male" else "1other")
    if subject_tag not in tags:
        tags.insert(0, subject_tag)

    slug = trigger or f"custom_{label}_{int(__import__('time').time())}"
    rec = {
        "id": slug,
        "name": label,
        "character": label,
        "slug": slug,
        "copyright": copyright_name,
        "gender": gender,
        "trigger": trigger,
        "core_tags": tags,
        "source": "custom",
    }

    try:
        catalog_res = get_reference_catalog().import_records(
            [rec],
            source="custom",
            source_label="自定义录入",
            version="manual",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    from char_swap_config import load_config as load_char_swap_config, save_config as save_char_swap_config

    cfg = load_char_swap_config()
    custom_presets = cfg.get("custom_presets") or {"male": [], "female": []}
    gender_key = "male" if gender == "male" else "female"
    pool = list(custom_presets.get(gender_key) or [])

    preset_entry = {
        "id": f"custom_{slug}",
        "label": label,
        "gender": gender_key,
        "identity": [t for t in tags if t in {subject_tag, "female_focus", "male_focus"} or "arknights" in t or t == trigger],
        "body": [],
        "appearance": [],
        "kind": "oc" if caption else "custom",
    }
    if caption:
        preset_entry["char_caption"] = caption

    pool = [p for p in pool if p.get("id") != preset_entry["id"]]
    pool.append(preset_entry)
    custom_presets[gender_key] = pool
    save_char_swap_config({"custom_presets": custom_presets})

    return {
        "ok": True,
        "message": f"角色「{label}」已成功保存并同步至资料库与预设",
        "catalog_imported": catalog_res.get("imported", 0),
        "preset_id": preset_entry["id"],
    }



@router.get("/styles")
def api_style_reference_search(
    q: str = Query("", max_length=200),
    kind: str = Query("", max_length=20),
    source: str = Query("", max_length=80),
    limit: int = Query(60, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict:
    try:
        return get_reference_catalog().search_styles(
            query=q,
            kind=kind,
            source=source,
            limit=limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/styles/{style_id}/draft")
def api_style_reference_draft(style_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    gallery_id = str(payload.get("gallery_id") or "site").strip().lower()
    if gallery_id not in {"site", "codex", "qqgroup"}:
        raise HTTPException(status_code=400, detail="无效的图库身份")
    mode = str(payload.get("mode") or "preset").strip().lower()
    if mode not in {"preset", "append"}:
        raise HTTPException(status_code=400, detail="画风资料应用方式只支持 preset 或 append")
    try:
        work_id = int(payload.get("work_id"))
        page_index = int(payload.get("page_index") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="作品 ID 和页码必须是整数") from exc
    if work_id < 1 or page_index < 0 or page_index > 999:
        raise HTTPException(status_code=400, detail="作品 ID 或页码超出范围")
    try:
        return prepare_style_reference_draft(
            style_id,
            gallery_id=gallery_id,
            work_id=work_id,
            page_index=page_index,
            mode=mode,
        )
    except StyleReferenceNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{reference_id}")
def api_reference_detail(reference_id: str) -> dict:
    item = get_reference_catalog().get(reference_id)
    if item is None:
        raise HTTPException(status_code=404, detail="角色资料不存在")
    return {"ok": True, "item": item}


@router.get("/{reference_id}/styles")
def api_reference_related_styles(reference_id: str) -> dict:
    item = get_reference_catalog().get(reference_id)
    if item is None:
        raise HTTPException(status_code=404, detail="角色资料不存在")
    return get_reference_catalog().related_styles(reference_id)


@router.post("/{reference_id}/preview")
def api_reference_preview(reference_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    item = get_reference_catalog().get(reference_id)
    if item is None:
        raise HTTPException(status_code=404, detail="角色资料不存在")
    card = adapt_anima_character(item["raw"], model=str(payload.get("model") or ""))
    return {
        "ok": True,
        "card": card,
        "reference_id": reference_id,
        "provider": "local",
        "generation_calls": 0,
    }


@router.post("/{reference_id}/apply")
def api_reference_apply(reference_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    item = get_reference_catalog().get(reference_id)
    if item is None:
        raise HTTPException(status_code=404, detail="角色资料不存在")
    comment = payload.get("comment")
    if not isinstance(comment, dict):
        raise HTTPException(status_code=400, detail="comment 必须是 Studio 草稿对象")
    try:
        patched, card = apply_anima_character_to_comment(
            comment,
            item["raw"],
            slot_index=int(payload.get("slot_index") or 0),
            model=str(payload.get("model") or comment.get("model") or ""),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "ok": True,
        "comment": patched,
        "texts": _prompt_snapshot(patched),
        "card": card,
        "reference_id": reference_id,
        "provider": "local",
        "generation_calls": 0,
        "message": f"已把 {card.get('label') or '角色'} 放入第 {int(payload.get('slot_index') or 0) + 1} 个 NAI 角色槽",
    }

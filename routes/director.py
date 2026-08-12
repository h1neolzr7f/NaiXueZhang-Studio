"""Standalone page and API routes for NAI batch Director Tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from nai_director import (
    cancel_director_batch,
    director_batch_status,
    director_catalog,
    director_job_revision,
    get_director_source_group,
    list_director_sources,
    preview_director_batch,
    preview_director_retry,
    retry_director_batch,
    start_director_batch,
    wait_for_director_change,
)
from server_shared import WEB_DIR


router = APIRouter()


def _director_request_parts(payload: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求内容必须是一个对象")
    raw_sources = payload.get("sources")
    if not isinstance(raw_sources, list) or any(not isinstance(row, dict) for row in raw_sources):
        raise HTTPException(status_code=400, detail="来源图必须是对象列表")
    raw_recipe = payload.get("recipe")
    if not isinstance(raw_recipe, dict):
        raise HTTPException(status_code=400, detail="导演工具配置必须是一个对象")
    return [dict(row) for row in raw_sources], dict(raw_recipe)


@router.get("/director")
def director_page() -> FileResponse:
    page = WEB_DIR / "director.html"
    if not page.is_file():
        raise HTTPException(status_code=404, detail="director page is missing")
    return FileResponse(
        page,
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )


@router.get("/api/director/catalog")
def api_director_catalog() -> dict:
    return director_catalog()


@router.get("/api/director/sources")
def api_director_sources(
    kind: str = Query("generated", pattern="^(generated|gallery)$"),
    mode: str = Query("single", pattern="^(series|single)$"),
    q: str = Query("", max_length=200),
    gallery_id: str = Query("site", max_length=20),
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=60),
) -> dict:
    try:
        return list_director_sources(
            kind=kind,
            mode=mode,
            q=q,
            gallery_id=gallery_id,
            page=page,
            page_size=page_size,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/director/source-groups/{group_id:path}")
def api_director_source_group(group_id: str) -> dict:
    try:
        return get_director_source_group(group_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/api/director/preview")
def api_director_preview(payload: Any = Body(default_factory=dict)) -> dict:
    sources, recipe = _director_request_parts(payload)
    try:
        return preview_director_batch(sources, recipe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/director/jobs")
async def api_director_start(payload: Any = Body(default_factory=dict)) -> dict:
    sources, recipe = _director_request_parts(payload)
    if payload.get("confirmed") is not True:
        raise HTTPException(
            status_code=409,
            detail="请先完成零费用预检，再明确确认可能产生 Anlas 消耗的批量导演任务",
        )
    raw_token_id = payload.get("token_id") or ""
    if not isinstance(raw_token_id, str):
        raise HTTPException(status_code=400, detail="NAI 槽位身份格式无效")
    preview_id = payload.get("preview_id") or ""
    if not isinstance(preview_id, str) or not preview_id.strip():
        raise HTTPException(status_code=400, detail="预检凭证格式无效")
    result = start_director_batch(
        sources,
        recipe,
        confirmed=True,
        preview_id=preview_id,
        token_id=raw_token_id,
    )
    error = str(result.get("error") or "")
    if error == "confirmation_required":
        raise HTTPException(status_code=409, detail=result.get("message"))
    if error == "busy":
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "Director task could not start")
    return result


@router.get("/api/director/jobs/status")
def api_director_status(task_id: str = Query("", max_length=64)) -> dict:
    status = director_batch_status(task_id or None)
    if task_id and not status.get("task_id"):
        raise HTTPException(status_code=404, detail="批量导演任务不存在")
    return {"ok": True, "batch": status}


@router.get("/api/director/jobs/{task_id}")
def api_director_status_by_id(task_id: str) -> dict:
    status = director_batch_status(task_id)
    if not status.get("task_id"):
        raise HTTPException(status_code=404, detail="批量导演任务不存在")
    return {"ok": True, "batch": status}


@router.get("/api/director/jobs-stream")
async def api_director_stream(task_id: str = Query("", max_length=64)) -> StreamingResponse:
    initial = director_batch_status(task_id or None)
    if task_id and not initial.get("task_id"):
        raise HTTPException(status_code=404, detail="批量导演任务不存在")

    async def events():
        revision = -1
        while True:
            status = director_batch_status(task_id or None)
            current_revision = int(status.get("revision") or director_job_revision())
            if current_revision != revision:
                revision = current_revision
                payload = json.dumps({"ok": True, "batch": status}, ensure_ascii=False)
                yield f"event: status\ndata: {payload}\n\n"
            if status.get("terminal") or status.get("status") == "idle":
                return
            changed = await asyncio.to_thread(wait_for_director_change, revision, 15.0)
            if changed == revision:
                yield ": keepalive\n\n"

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/api/director/jobs/{task_id}/cancel")
def api_director_cancel(task_id: str) -> dict:
    result = cancel_director_batch(task_id)
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail=result.get("message"))
    return result


@router.post("/api/director/jobs/{task_id}/retry/preview")
def api_director_retry_preview(task_id: str) -> dict:
    result = preview_director_retry(task_id)
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail=result.get("message"))
    if error in {"not_terminal", "needs_review"}:
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result


@router.post("/api/director/jobs/{task_id}/retry")
def api_director_retry(task_id: str, payload: Any = Body(default_factory=dict)) -> dict:
    if not isinstance(payload, dict) or payload.get("confirmed") is not True:
        raise HTTPException(status_code=409, detail="请先预检失败项并明确确认重试费用")
    preview_id = payload.get("preview_id") or ""
    if not isinstance(preview_id, str) or not preview_id.strip():
        raise HTTPException(status_code=400, detail="预检凭证格式无效")
    result = retry_director_batch(task_id, confirmed=True, preview_id=preview_id)
    error = str(result.get("error") or "")
    if error == "not_found":
        raise HTTPException(status_code=404, detail=result.get("message"))
    if error in {"not_terminal", "busy", "needs_review", "confirmation_required"}:
        raise HTTPException(status_code=409, detail=result.get("message"))
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return result

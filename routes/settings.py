from fastapi import APIRouter, Body, HTTPException

from nai_api import token_status
from nai_prompt_optimizer import ai_status
from nai_api import save_token as save_nai_token_line
from pixiv_launch import (
    load_config as load_pixiv_config,
    save_config as save_pixiv_config,
    save_ai_key,
    list_ai_models,
    test_ai_connection,
    test_ai_vision_connection,
)
from gallery_cache import invalidate
from user_prefs import load_prefs, save_prefs
from knowledge_catalog import get_knowledge_catalog
from butler import submit_knowledge_rebuild
from usage_ledger import LEDGER, usage_summary

router = APIRouter(prefix="/api/settings")


@router.get("/config")
def api_settings_config() -> dict:
    prefs = load_prefs()
    config = load_pixiv_config()
    ai = dict(config.get("ai") or {})
    ai.pop("api_key", None)
    ai.update(ai_status())
    return {
        "ok": True,
        "prefs": prefs,
        "config": config,
        "ai": ai,
    }


@router.post("/config")
def api_settings_config_save(payload: dict = Body(default_factory=dict)) -> dict:
    prefs = payload.get("prefs")
    ai = payload.get("ai")
    if prefs is not None and not isinstance(prefs, dict):
        raise HTTPException(status_code=400, detail="prefs must be an object")
    if ai is not None and not isinstance(ai, dict):
        raise HTTPException(status_code=400, detail="ai must be an object")
    ai = dict(ai or {})
    for key in ("timeout", "temperature", "max_tokens"):
        if key in ai and not isinstance(ai[key], (int, float)):
            raise HTTPException(status_code=400, detail=f"ai.{key} must be numeric")

    if isinstance(prefs, dict):
        save_prefs(prefs)
    api_key = str(ai.pop("api_key", "") or "").strip()
    if "api_base" in ai:
        try:
            from network_safety import validate_ai_api_base

            ai["api_base"] = validate_ai_api_base(str(ai.get("api_base") or ""))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if ai:
        save_pixiv_config({"ai": ai})
    if api_key:
        save_ai_key(api_key)
    invalidate("api_config")
    return api_settings_config()


@router.get("/ai-models")
def api_settings_ai_models() -> dict:
    return list_ai_models()


@router.post("/ai-test")
def api_settings_ai_test(_payload: dict = Body(default_factory=dict)) -> dict:
    return test_ai_connection()


@router.post("/ai-vision-test")
def api_settings_ai_vision_test(_payload: dict = Body(default_factory=dict)) -> dict:
    return test_ai_vision_connection()


@router.get("/usage")
def api_settings_usage() -> dict:
    return {
        "ok": True,
        "summary": usage_summary(),
        "recent": LEDGER.recent(),
    }


@router.get("/knowledge")
def api_settings_knowledge() -> dict:
    status = dict(get_knowledge_catalog().status())
    for source in status.get("sources") or []:
        if isinstance(source, dict):
            for key in ("path", "absolute_path", "root"):
                source.pop(key, None)
    return status


@router.post("/knowledge/rebuild", status_code=202)
async def api_settings_knowledge_rebuild(
    _payload: dict = Body(default_factory=dict),
) -> dict:
    result = await submit_knowledge_rebuild()
    workflow_id = str(result.get("workflow_id") or "")
    return {
        **result,
        "task_url": (
            f"/butler?task={workflow_id}#taskCenter"
            if workflow_id
            else "/butler#taskCenter"
        ),
    }


@router.get("/status")
def api_settings_status() -> dict:
    tok = token_status()
    ai = ai_status()
    prefs = load_prefs()
    ready = {
        "gallery": True,
        "nai_token": bool(tok.get("has_token")),
        "ai_key": bool(ai.get("has_api_key")),
    }
    return {
        "ok": True,
        "ready": ready,
        "all_ready": ready["nai_token"],
        "token": tok,
        "ai": ai,
        "prefs": prefs,
    }


@router.get("/prefs")
def api_settings_prefs() -> dict:
    return {"ok": True, "prefs": load_prefs()}


@router.post("/prefs")
def api_settings_prefs_save(payload: dict = Body(default_factory=dict)) -> dict:
    patch = payload.get("prefs") if isinstance(payload.get("prefs"), dict) else payload
    invalidate("api_config")
    return save_prefs(patch or {})


@router.post("/nai-token")
def api_settings_nai_token(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        invalidate("api_config")
        return save_nai_token_line(
            str(payload.get("token") or ""),
            default_provider=str(payload.get("default_provider") or payload.get("provider") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TypeError:
        # Older save_token without default_provider kwarg
        invalidate("api_config")
        return save_nai_token_line(str(payload.get("token") or ""))


@router.post("/ai-key")
def api_settings_ai_key(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        invalidate("api_config")
        return save_ai_key(
            str(payload.get("api_key") or ""),
            model=str(payload.get("model") or "").strip() or None,
            api_base=str(payload.get("api_base") or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

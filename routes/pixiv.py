from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse
from server_shared import WEB_DIR
from pixiv_accounts import (
    add_account,
    create_account_slot,
    generate_analytics,
    generate_analytics_all,
    get_active_account,
    get_cached_analytics,
    import_accounts_batch,
    list_accounts,
    list_stats_dashboard,
    refresh_all_stats,
    remove_account,
    switch_account,
    update_account_profile,
    update_account_token,
)
from pixiv_launch import (
    _chat_completion,
    ai_auth_status,
    generate_persona,
    generate_post_copy,
    launch_one_click,
    launch_status,
    list_launch_candidates,
    list_launch_groups,
    load_prepared_submission,
    load_post_draft,
    list_upload_history,
    start_upload_job,
    load_config as load_pixiv_config,
    pixiv_auth_status,
    provider_presets,
    save_ai_key,
    save_config as save_pixiv_config,
    save_pixiv_token,
    test_ai_connection,
)

router = APIRouter()

@router.get("/api/pixiv/config")
def api_pixiv_config_get() -> dict:
    return {
        "ok": True,
        "config": load_pixiv_config(),
        "pixiv": pixiv_auth_status(),
        "ai": ai_auth_status(),
        "presets": provider_presets().get("presets") or {},
        "accounts": list_accounts(),
        "active_account": get_active_account(),
        "stats": list_stats_dashboard(),
    }

@router.post("/api/pixiv/config")
def api_pixiv_config_set(payload: dict = Body(default_factory=dict)) -> dict:
    cfg = save_pixiv_config(payload)
    return {"ok": True, "config": cfg, "message": "Pixiv config saved"}

@router.post("/api/pixiv/token")
def api_pixiv_token_set(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return save_pixiv_token(str(payload.get("refresh_token") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/api/pixiv/ai-key")
def api_pixiv_ai_key_set(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return save_ai_key(str(payload.get("api_key") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/api/pixiv/ai-test")
def api_pixiv_ai_test() -> dict:
    try:
        return test_ai_connection()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/api/pixiv/status")
def api_pixiv_status() -> dict:
    return {
        "ok": True,
        "pixiv": pixiv_auth_status(),
        "ai": ai_auth_status(),
        "job": launch_status(),
    }

@router.post("/api/pixiv/auth/test")
def api_pixiv_auth_test(payload: dict = Body(default_factory=dict)) -> dict:
    from pixiv_accounts import test_account_auth
    account_id = str(payload.get("account_id") or "").strip() or None
    return test_account_auth(account_id)

@router.post("/api/pixiv/auth/browser-login")
def api_pixiv_auth_browser_login(payload: dict = Body(default_factory=dict)) -> dict:
    from pixiv_accounts import login_with_browser
    try:
        return login_with_browser(
            account_id=str(payload.get("account_id") or "").strip() or None,
            label=str(payload.get("label") or ""),
            direction=str(payload.get("direction") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or "browser login failed"
        if "playwright" in detail.lower() or "executable doesn't exist" in detail.lower():
            detail = (
                f"{detail}. Please run locally: "
                "`.venv\\Scripts\\python.exe -m playwright install chromium`"
            )
        raise HTTPException(status_code=500, detail=detail) from exc

@router.post("/api/pixiv/auth/email-login")
def api_pixiv_auth_email_login(payload: dict = Body(default_factory=dict)) -> dict:
    from pixiv_accounts import login_with_email_password
    try:
        return login_with_email_password(
            str(payload.get("username") or payload.get("email") or ""),
            str(payload.get("password") or ""),
            account_id=str(payload.get("account_id") or "").strip() or None,
            label=str(payload.get("label") or ""),
            direction=str(payload.get("direction") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=408, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        detail = str(exc).strip() or "email login failed"
        if "playwright" in detail.lower() or "executable doesn't exist" in detail.lower():
            detail = (
                f"{detail}. Please run locally: "
                "`.venv\\Scripts\\python.exe -m playwright install chromium`"
            )
        raise HTTPException(status_code=500, detail=detail) from exc

@router.get("/api/pixiv/candidates")
def api_pixiv_candidates() -> dict:
    return {"ok": True, "items": list_launch_candidates()}

@router.get("/api/pixiv/groups")
def api_pixiv_groups() -> dict:
    return {"ok": True, "groups": list_launch_groups()}


@router.get("/api/pixiv/prepared")
def api_pixiv_prepared(package_id: str = Query("")) -> dict:
    """Load a Butler-prepared draft without starting an upload."""
    return load_prepared_submission(package_id)


@router.get("/api/pixiv/history")
def api_pixiv_history(limit: int = Query(20, ge=1, le=100)) -> dict:
    return {"ok": True, "items": list_upload_history(limit)}


@router.get("/api/pixiv/draft")
def api_pixiv_draft(image_id: str = Query("")) -> dict:
    """读取与指定图片匹配的磁盘投稿草稿（pixiv_draft.json），供刷新后恢复表单。"""
    draft = load_post_draft(image_id) if image_id else {}
    return {"ok": True, "draft": draft}

@router.post("/api/pixiv/director/persona")
def api_pixiv_director_persona(payload: dict = Body(default_factory=dict)) -> dict:
    return generate_persona(
        direction=str(payload.get("direction") or ""),
        nickname_hint=str(payload.get("nickname_hint") or ""),
        save=bool(payload.get("save", True)),
    )

@router.post("/api/pixiv/director/post")
def api_pixiv_director_post(payload: dict = Body(default_factory=dict)) -> dict:
    persona = payload.get("persona")
    if persona is not None and not isinstance(persona, dict):
        persona = None
    return generate_post_copy(
        image_id=str(payload.get("image_id") or ""),
        extra=str(payload.get("extra") or ""),
        persona=persona,
        save_draft=bool(payload.get("save_draft", True)),
        run_pipeline=bool(payload.get("run_pipeline", True)),
    )

@router.post("/api/pixiv/upload")
def api_pixiv_upload(payload: dict = Body(default_factory=dict)) -> dict:
    image_id = str(payload.get("image_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    image_ids = payload.get("image_ids")
    if not image_id and not group_id and not image_ids:
        raise HTTPException(
            status_code=400,
            detail="image_id, image_ids, or group_id is required",
        )
    try:
        result = start_upload_job(payload)
        if not result.get("ok"):
            raise HTTPException(status_code=409, detail=result.get("message") or "任务冲突")
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/pixiv/upload-selector-probe")
def api_pixiv_upload_selector_probe(payload: dict = Body(default_factory=dict)) -> dict:
    """Check the live Pixiv create-page DOM without uploading files or submitting."""
    from pixiv_web_upload import probe_pixiv_upload_page_sync

    return probe_pixiv_upload_page_sync(
        account_id=str(payload.get("account_id") or "").strip(),
        headless=bool(payload.get("headless", True)),
    )

@router.get("/api/pixiv/accounts")
def api_pixiv_accounts_list() -> dict:
    return {"ok": True, "accounts": list_accounts(), "pixiv": pixiv_auth_status()}


@router.post("/api/pixiv/accounts/slot")
def api_pixiv_accounts_slot(payload: dict = Body(default_factory=dict)) -> dict:
    """Create empty local account slot; finish with passkey/email/token login."""
    try:
        return create_account_slot(
            label=str(payload.get("label") or ""),
            direction=str(payload.get("direction") or ""),
            set_active=bool(payload.get("set_active", True)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/pixiv/accounts")
def api_pixiv_accounts_add(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return add_account(
            refresh_token=str(payload.get("refresh_token") or ""),
            label=str(payload.get("label") or ""),
            direction=str(payload.get("direction") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/api/pixiv/accounts/import")
def api_pixiv_accounts_import(payload: dict = Body(default_factory=dict)) -> dict:
    """Batch import accounts from multi-line text and/or JSON items."""
    try:
        items = payload.get("items") if isinstance(payload.get("items"), list) else None
        return import_accounts_batch(
            text=str(payload.get("text") or payload.get("raw") or ""),
            items=items,
            verify=bool(payload.get("verify", True)),
            skip_duplicates=bool(payload.get("skip_duplicates", True)),
            set_first_active=bool(payload.get("set_first_active", False)),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/api/pixiv/accounts/switch")
def api_pixiv_accounts_switch(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return switch_account(str(payload.get("account_id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.delete("/api/pixiv/accounts/{account_id}")
def api_pixiv_accounts_delete(account_id: str) -> dict:
    try:
        return remove_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/api/pixiv/accounts/{account_id}/token")
def api_pixiv_accounts_token(account_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return update_account_token(account_id, str(payload.get("refresh_token") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.patch("/api/pixiv/accounts/{account_id}")
def api_pixiv_accounts_patch(account_id: str, payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return update_account_profile(
            account_id,
            label=payload.get("label"),
            direction=payload.get("direction"),
            persona=payload.get("persona") if isinstance(payload.get("persona"), dict) else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.get("/api/pixiv/stats")
def api_pixiv_stats() -> dict:
    return list_stats_dashboard()

@router.post("/api/pixiv/stats/refresh")
def api_pixiv_stats_refresh(payload: dict = Body(default_factory=dict)) -> dict:
    return refresh_all_stats(force=bool(payload.get("force", True)))

@router.get("/api/pixiv/analytics")
def api_pixiv_analytics_get(account_id: str = Query("")) -> dict:
    return get_cached_analytics(account_id or None)

@router.post("/api/pixiv/analytics")
def api_pixiv_analytics_run(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        account_id = str(payload.get("account_id") or "").strip()
        if account_id == "all":
            return generate_analytics_all(
                chat_completion=_chat_completion,
                upload_history=list_upload_history(30),
            )
        return generate_analytics(
            account_id=account_id or None,
            chat_completion=_chat_completion,
            upload_history=list_upload_history(30),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

@router.post("/api/pixiv/launch")
def api_pixiv_launch(payload: dict = Body(default_factory=dict)) -> dict:
    image_id = str(payload.get("image_id") or "").strip()
    group_id = str(payload.get("group_id") or "").strip()
    image_ids = payload.get("image_ids")
    if not image_id and not group_id and not image_ids:
        raise HTTPException(
            status_code=400,
            detail="image_id, image_ids, or group_id is required",
        )
    result = launch_one_click(payload)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message") or "start failed")
    return result

@router.get("/pixiv")
def pixiv_page() -> FileResponse:
    path = WEB_DIR / "pixiv.html"
    if path.exists():
        return FileResponse(path)
    raise HTTPException(status_code=404, detail="not found")

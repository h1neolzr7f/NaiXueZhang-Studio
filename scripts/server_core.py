"""Minimal application entry point copied into Core release packages.

This file is a release template.  The full source checkout continues to use the
top-level ``server.py`` and its complete feature suite.
"""

from __future__ import annotations

import os

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from crawler_control import multi_crawler_status, start_crawler_target, stop_crawler_target
from pixiv_accounts import (
    add_account,
    get_active_account,
    list_accounts,
    remove_account,
    switch_account,
    test_account_auth,
    update_account_token,
)
from routes import gallery, maintenance, nai_tags, pixiv_intake
from server_shared import WEB_DIR


app = FastAPI(title="Pixiv NAI Gallery Core")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8797", "http://localhost:8797"],
    allow_methods=["*"],
    allow_headers=["*"],
)

if WEB_DIR.exists():
    app.mount("/assets", StaticFiles(directory=str(WEB_DIR)), name="assets")


@app.get("/api/pixiv/accounts")
def api_pixiv_accounts() -> dict:
    return {
        "ok": True,
        "accounts": list_accounts(),
        "active_account": get_active_account(),
    }


@app.post("/api/pixiv/accounts")
def api_pixiv_account_add(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return add_account(
            refresh_token=str(payload.get("refresh_token") or ""),
            label=str(payload.get("label") or ""),
            direction=str(payload.get("direction") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pixiv/accounts/switch")
def api_pixiv_account_switch(payload: dict = Body(default_factory=dict)) -> dict:
    try:
        return switch_account(str(payload.get("account_id") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pixiv/accounts/{account_id}/token")
def api_pixiv_account_token(
    account_id: str,
    payload: dict = Body(default_factory=dict),
) -> dict:
    try:
        return update_account_token(
            account_id,
            str(payload.get("refresh_token") or ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/pixiv/accounts/{account_id}")
def api_pixiv_account_delete(account_id: str) -> dict:
    try:
        return remove_account(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/pixiv/auth/test")
def api_pixiv_account_test(payload: dict = Body(default_factory=dict)) -> dict:
    return test_account_auth(str(payload.get("account_id") or "").strip() or None)


@app.get("/api/crawler/status")
def api_crawler_status() -> dict:
    return {"ok": True, "crawlers": multi_crawler_status()}


@app.post("/api/crawler/start")
def api_crawler_start(payload: dict = Body(default_factory=dict)) -> dict:
    return {"ok": True, **start_crawler_target("pixiv", watch=bool(payload.get("watch", True)))}


@app.post("/api/crawler/stop")
def api_crawler_stop(payload: dict = Body(default_factory=dict)) -> dict:
    _ = payload
    return {"ok": True, **stop_crawler_target("pixiv")}


app.include_router(pixiv_intake.router)
app.include_router(maintenance.router)
app.include_router(maintenance.page_router)
app.include_router(nai_tags.router)
app.include_router(nai_tags.page_router)
# Gallery owns the root page and a compatibility fallback, so it stays last.
app.include_router(gallery.router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("GALLERY_PORT", "8797")),
        reload=False,
    )

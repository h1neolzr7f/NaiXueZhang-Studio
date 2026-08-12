"""Browsable NAI Tag Index Interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from nai_tag_index import FACETS, parse_nai_facet_selections


def build_router(database) -> APIRouter:
    router = APIRouter(prefix="/api/nai-tags", tags=["nai-tag-index"])

    @router.get("")
    def list_facets(
        facet: str | None = Query(None),
        limit: int = Query(50, ge=1, le=500),
    ) -> dict:
        try:
            items = database.popular_nai_facets(facet=facet, limit=limit)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "facets": list(FACETS), "items": items}

    @router.get("/works")
    def filter_works(
        selection: list[str] = Query(default=[]),
        page: int = Query(1, ge=1),
        page_size: int = Query(60, ge=1, le=120),
        sort: str = Query("new"),
    ) -> dict:
        try:
            facets = parse_nai_facet_selections(selection)
            result = database.search_works(
                page=page,
                page_size=page_size,
                sort=sort,
                nai_only=True,
                nai_facets=facets,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        result["selected_facets"] = facets
        return result

    return router


from server_shared import DB  # noqa: E402

router = build_router(DB)
page_router = APIRouter()


@page_router.get("/nai-tags")
def nai_tag_page() -> FileResponse:
    return FileResponse(
        Path(__file__).resolve().parents[1] / "web" / "nai-tags.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
    )

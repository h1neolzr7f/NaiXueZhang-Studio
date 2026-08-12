from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from db import Database
from db_compression import compress_text
from routes.nai_tags import build_router


def _insert(db: Database, work_id: int, *tags: str) -> None:
    db.conn.execute(
        "INSERT INTO works(id, title, ai_type, create_date, image_count, list_json) "
        "VALUES (?, ?, 'NAI', '2026-08-02T00:00:00Z', 1, ?)",
        (work_id, f"work-{work_id}", json.dumps({"id": work_id, "AI_type": "NAI"})),
    )
    metadata = {
        "_local": {
            "parser_version": "test-v1",
            "parsed_nai_tags": [
                {"text": tag, "weight": 1, "raw_syntax": tag, "syntax_type": "none"}
                for tag in tags
            ],
        }
    }
    db.conn.execute(
        "INSERT INTO work_images(work_id, page_index, ai_json, downloaded) VALUES (?, 0, ?, 1)",
        (work_id, compress_text(json.dumps(metadata))),
    )


def test_nai_tag_routes_list_facets_and_filter_gallery_works(tmp_path) -> None:
    db = Database(tmp_path / "gallery.db")
    _insert(db, 1, "skadi_(arknights)", "outdoors")
    _insert(db, 2, "skadi_(arknights)", "indoors")
    db.conn.commit()
    db.rebuild_nai_tag_index()
    app = FastAPI()
    app.include_router(build_router(db))
    client = TestClient(app)
    try:
        facets = client.get("/api/nai-tags", params={"facet": "scene"})
        works = client.get(
            "/api/nai-tags/works",
            params=[
                ("selection", "character:skadi_(arknights)"),
                ("selection", "scene:outdoors"),
            ],
        )
    finally:
        db.close()

    assert facets.status_code == 200
    assert [row["tag"] for row in facets.json()["items"]] == ["indoors", "outdoors"]
    assert works.status_code == 200
    assert [row["id"] for row in works.json()["items"]] == [1]

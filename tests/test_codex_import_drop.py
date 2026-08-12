from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from db import Database
from routes import gallery as gallery_routes


def _nai_png_bytes() -> bytes:
    buf = io.BytesIO()
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("Software", "NovelAI")
    metadata.add_text("Source", "NovelAI Diffusion V4.5")
    metadata.add_text("Description", "1girl")
    metadata.add_text("Comment", json.dumps({"prompt": "1girl"}))
    Image.new("RGB", (8, 8)).save(buf, format="PNG", pnginfo=metadata)
    return buf.getvalue()


def _plain_png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")
    return buf.getvalue()


class _FakeUpload:
    def __init__(self, name: str, data: bytes) -> None:
        self.filename = name
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            return self._data
        return self._data[:size]


def _patched_env(tmp_path: Path, gallery_id: str):
    spec = gallery_routes.get_gallery_spec(gallery_id)
    tmp_spec = type(spec)(
        id=spec.id,
        label_zh=spec.label_zh,
        label_en=spec.label_en,
        description_zh=spec.description_zh,
        description_en=spec.description_en,
        db_path=tmp_path / "gallery.db",
        images_dir=tmp_path / "images",
        asset_base_url="/data/gallery/codex/",
        cdn_fallback=False,
        local_scope="",
        group_by=spec.group_by,
    )
    tmp_db = Database(tmp_spec.db_path)
    patchers = (
        patch.object(gallery_routes, "get_gallery_spec", return_value=tmp_spec),
        patch.object(gallery_routes, "get_gallery_db", return_value=tmp_db),
        patch("scripts.gallery_import_common.get_db", return_value=tmp_db),
        patch("scripts.gallery_import_common.ensure_gallery_dirs", side_effect=lambda _gid: None),
    )
    return patchers, tmp_spec, tmp_db


def test_drop_import_accepts_nai_image_and_stores_category(tmp_path: Path) -> None:
    patchers, spec, db = _patched_env(tmp_path, "codex")
    with patchers[0], patchers[1], patchers[2], patchers[3]:
        result = asyncio.run(
            gallery_routes.api_gallery_import_drop(
                "codex",
                category="角色/阿米娅",
                files=[_FakeUpload("amy.png", _nai_png_bytes())],
            )
        )
    assert result["ok"] is True
    assert len(result["accepted"]) == 1
    assert result["accepted"][0]["category"] == "角色/阿米娅"
    assert result["rejected"] == []
    works = db.search_works(page_size=20)
    assert len(works["items"]) == 1
    stored = works["items"][0]
    assert stored["category"] == "角色/阿米娅"
    assert list((spec.images_dir).rglob("*.png"))


def test_drop_import_rejects_plain_image(tmp_path: Path) -> None:
    patchers, _spec, db = _patched_env(tmp_path, "codex")
    with patchers[0], patchers[1], patchers[2], patchers[3]:
        result = asyncio.run(
            gallery_routes.api_gallery_import_drop(
                "codex",
                category="未分类",
                files=[_FakeUpload("plain.png", _plain_png_bytes())],
            )
        )
    assert result["accepted"] == []
    assert result["rejected"][0]["reason"] == "nai_metadata_missing"
    assert db.search_works(page_size=20)["items"] == []


def test_drop_import_rejects_site_gallery(tmp_path: Path) -> None:
    from fastapi import HTTPException

    try:
        asyncio.run(
            gallery_routes.api_gallery_import_drop(
                "site",
                category="x",
                files=[_FakeUpload("a.png", _nai_png_bytes())],
            )
        )
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("site gallery must reject import-drop")


def test_drop_import_is_idempotent_for_same_image(tmp_path: Path) -> None:
    patchers, _spec, db = _patched_env(tmp_path, "codex")
    data = _nai_png_bytes()
    with patchers[0], patchers[1], patchers[2], patchers[3]:
        first = asyncio.run(
            gallery_routes.api_gallery_import_drop(
                "codex", category="画集", files=[_FakeUpload("a.png", data)]
            )
        )
        second = asyncio.run(
            gallery_routes.api_gallery_import_drop(
                "codex", category="画集", files=[_FakeUpload("a.png", data)]
            )
        )
    assert first["accepted"] and second["accepted"]
    assert len(db.search_works(page_size=20)["items"]) == 1

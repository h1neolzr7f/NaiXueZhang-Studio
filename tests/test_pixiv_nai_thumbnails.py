from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

from db import Database
from gallery_asset_store import GalleryStorageQuotaExceeded
from pixiv_nai_intake import PixivNAIIntake, PixivPage, PixivWork


def test_pixiv_nai_intake_publishes_thumbnail_as_gallery_preview(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "NovelAI")
    info.add_text("Source", "NovelAI Diffusion V4.5")
    info.add_text("Description", "1girl, outdoors")
    info.add_text("Comment", json.dumps({"prompt": "1girl, outdoors"}))
    Image.new("RGB", (1400, 900), (20, 40, 80)).save(source, pnginfo=info)
    work = PixivWork(
        work_id=88,
        user_id=9,
        user_name="artist",
        title="large",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00Z",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, "https://i.pximg.test/88_p0.png"),),
    )
    with Database(tmp_path / "gallery.db") as db:
        intake = PixivNAIIntake(
            db=db,
            images_dir=tmp_path / "images",
            staging_dir=tmp_path / "staging",
            allowed_image_hosts=("i.pximg.test",),
        )
        receipt = intake.ingest_work(
            work, lambda _url, target: shutil.copyfile(source, target)
        )
        listed = db.search_works(nai_only=True)["items"][0]

    assert receipt.status == "accepted"
    assert listed["thumb_path"].startswith("_thumbs/NAI/9/88_p0_")
    thumbnail = tmp_path / "images" / listed["thumb_path"]
    assert thumbnail.is_file()
    with Image.open(thumbnail) as rendered:
        assert max(rendered.size) <= 512


def test_pixiv_nai_intake_refuses_publication_past_storage_quota(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "NovelAI")
    info.add_text("Description", "1girl")
    info.add_text("Comment", json.dumps({"prompt": "1girl"}))
    Image.new("RGB", (64, 64), (1, 2, 3)).save(source, pnginfo=info)
    work = PixivWork(
        work_id=89, user_id=9, user_name="artist", title="quota", caption="",
        tags=(), create_date="2026-08-02T00:00:00Z", total_view=0,
        total_bookmarks=0, pages=(PixivPage(0, "https://i.pximg.test/89.png"),),
    )
    images = tmp_path / "images"
    with Database(tmp_path / "gallery.db") as db:
        intake = PixivNAIIntake(
            db=db, images_dir=images, staging_dir=tmp_path / "staging",
            allowed_image_hosts=("i.pximg.test",), storage_quota_bytes=1,
        )
        with pytest.raises(GalleryStorageQuotaExceeded):
            intake.ingest_work(work, lambda _url, target: shutil.copyfile(source, target))
        assert db.search_works(nai_only=True)["items"] == []
    assert list(images.rglob("*.*")) == []

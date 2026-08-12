from __future__ import annotations

import json
import hashlib
import shutil
import threading
import time
from dataclasses import replace
from pathlib import Path

from PIL import Image, PngImagePlugin

from nai_image_metadata import parse_nai_image
from nai_prompt_tags import parse_nai_tags
from db import Database
from pixiv_nai_intake import PixivNAIIntake, PixivPage, PixivWork


def _write_nai_png(
    path: Path,
    comment: dict[str, object],
    color: tuple[int, int, int] = (32, 48, 64),
) -> None:
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Software", "NovelAI")
    png_info.add_text("Source", "NovelAI Diffusion V4.5")
    png_info.add_text("Description", str(comment.get("prompt") or ""))
    png_info.add_text("Comment", json.dumps(comment, ensure_ascii=False))
    Image.new("RGB", (64, 96), color).save(path, pnginfo=png_info)


def test_verified_image_exposes_aitag_compatible_metadata(tmp_path: Path) -> None:
    source = tmp_path / "v4.png"
    comment = {
        "prompt": "1girl, 1.5::blue eyes::, outdoors",
        "uc": "lowres",
        "steps": 28,
        "seed": 123,
        "v4_prompt": {
            "caption": {
                "base_caption": "outdoors",
                "char_captions": [{"char_caption": "alice, blue eyes"}],
            }
        },
    }
    _write_nai_png(source, comment)

    result = parse_nai_image(source)
    metadata = result.canonical_metadata()

    assert result.accepted is True
    assert metadata["Software"] == "NovelAI"
    assert metadata["Source"] == "NovelAI Diffusion V4.5"
    assert metadata["Description"] == comment["prompt"]
    assert metadata["Comment"]["prompt"] == comment["prompt"]
    assert metadata["Comment"]["uc"] == "lowres"
    assert metadata["Comment"]["steps"] == 28
    assert metadata["Comment"]["v4_prompt"] == comment["v4_prompt"]
    assert metadata["Comment"]["width"] == 64
    assert metadata["Comment"]["height"] == 96
    assert metadata["_local"]["parser_version"] == result.parser_version
    assert metadata["_local"]["parsed_nai_tags"] == [
        {
            "text": "1girl",
            "weight": 1.0,
            "raw_syntax": "1girl",
            "syntax_type": "none",
        },
        {
            "text": "blue eyes",
            "weight": 1.5,
            "raw_syntax": "1.5::blue eyes::",
            "syntax_type": "numeric",
        },
        {
            "text": "outdoors",
            "weight": 1.0,
            "raw_syntax": "outdoors",
            "syntax_type": "none",
        },
    ]


def test_nai_prompt_tags_preserve_weight_and_protected_commas() -> None:
    tags = parse_nai_tags(
        "1girl, 1.5::blue eyes::, {{masterpiece}}, [simple background], "
        "||red hair, blue hair||"
    )

    assert [tag.text for tag in tags] == [
        "1girl",
        "blue eyes",
        "masterpiece",
        "simple background",
        "||red hair, blue hair||",
    ]
    assert [tag.weight for tag in tags] == [1.0, 1.5, 1.1, 0.95, 1.0]
    assert [tag.syntax_type for tag in tags] == [
        "none",
        "numeric",
        "bracket",
        "bracket",
        "none",
    ]


def test_pixiv_work_keeps_only_verified_pages_and_is_idempotent(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    first = sources / "p0.png"
    rejected = sources / "p1.png"
    third = sources / "p2.png"
    _write_nai_png(first, {"prompt": "1girl, blue eyes", "seed": 1})
    Image.new("RGB", (64, 96), (80, 40, 20)).save(rejected)
    _write_nai_png(third, {"prompt": "1boy, green eyes", "seed": 3})
    source_by_url = {
        "https://i.pximg.test/100_p0.png": first,
        "https://i.pximg.test/100_p1.png": rejected,
        "https://i.pximg.test/100_p2.png": third,
    }
    download_calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        download_calls.append(url)
        shutil.copy2(source_by_url[url], destination)

    db = Database(tmp_path / "gallery.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    work = PixivWork(
        work_id=100,
        user_id=200,
        user_name="Alice",
        title="three pages",
        caption="caption",
        tags=("NovelAI", "original"),
        create_date="2026-08-02T01:02:03+00:00",
        total_view=30,
        total_bookmarks=12,
        pages=tuple(
            PixivPage(source_page_index=index, original_url=url)
            for index, url in enumerate(source_by_url)
        ),
    )
    try:
        first_receipt = intake.ingest_work(work, download)
        detail = db.get_work_detail(100)
        second_receipt = intake.ingest_work(work, download)
        repeated_detail = db.get_work_detail(100)
        updated_receipt = intake.ingest_work(
            replace(work, total_view=99, total_bookmarks=44),
            download,
        )
        updated_detail = db.get_work_detail(100)
    finally:
        db.close()

    assert first_receipt.status == "partial"
    assert first_receipt.accepted_pages == 2
    assert first_receipt.rejected_pages == 1
    assert [page.reason for page in first_receipt.pages] == [
        "accepted",
        "nai_metadata_missing",
        "accepted",
    ]
    assert detail is not None
    assert detail["work"]["id"] == 100
    assert detail["work"]["image_count"] == 2
    assert detail["work"]["source_page_count"] == 3
    assert json.loads(detail["work"]["tags"]) == ["NovelAI", "original"]
    assert [image["page_index"] for image in detail["images"]] == [0, 1]
    assert [image["source_page_index"] for image in detail["images"]] == [0, 2]
    assert all(image["image_type"] == "NAI" for image in detail["images"])
    # On-disk assets are compressed WebP after metadata extraction.
    assert all(image["local_path"].endswith(".webp") for image in detail["images"])
    stored_assets = [
        tmp_path / "images" / image["local_path"] for image in detail["images"]
    ]
    assert all(path.is_file() and path.stat().st_size > 0 for path in stored_assets)
    # Metadata lives in the DB; compressed WebP is for browsing only.
    assert all(image["ai_json"]["Software"] == "NovelAI" for image in detail["images"])
    serialized_detail = json.dumps(detail, ensure_ascii=False)
    assert "i.pximg.test" not in serialized_detail
    assert "source_url" not in serialized_detail
    assert "original_urls" not in serialized_detail
    assert detail["images"][0]["ai_json"]["Comment"]["seed"] == 1
    assert detail["images"][1]["ai_json"]["Comment"]["seed"] == 3
    assert second_receipt.status == "unchanged"
    assert len(download_calls) == 3
    assert repeated_detail == detail
    assert updated_receipt.status == "updated"
    assert len(download_calls) == 3
    assert updated_detail is not None
    assert updated_detail["work"]["total_view"] == 99
    assert updated_detail["work"]["total_bookmarks"] == 44


def test_work_is_removed_when_replacement_has_no_verified_nai_pages(
    tmp_path: Path,
) -> None:
    accepted_source = tmp_path / "accepted.png"
    plain_source = tmp_path / "plain.png"
    _write_nai_png(accepted_source, {"prompt": "1girl", "seed": 5})
    Image.new("RGB", (64, 96), (10, 20, 30)).save(plain_source)
    source_by_url = {
        "https://i.pximg.test/101_old.png": accepted_source,
        "https://i.pximg.test/101_new.png": plain_source,
    }

    def download(url: str, destination: Path) -> None:
        shutil.copy2(source_by_url[url], destination)

    base = PixivWork(
        work_id=101,
        user_id=201,
        user_name="Bob",
        title="replace",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, "https://i.pximg.test/101_old.png"),),
    )
    db = Database(tmp_path / "replace.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        intake.ingest_work(base, download)
        first_detail = db.get_work_detail(101)
        assert first_detail is not None
        old_asset = tmp_path / "images" / first_detail["images"][0]["local_path"]
        assert old_asset.is_file()

        receipt = intake.ingest_work(
            replace(
                base,
                pages=(PixivPage(0, "https://i.pximg.test/101_new.png"),),
            ),
            download,
        )
        removed_detail = db.get_work_detail(101)
    finally:
        db.close()

    assert receipt.status == "rejected"
    assert receipt.pages[0].reason == "nai_metadata_missing"
    assert removed_detail is None
    assert old_asset.exists() is False


def test_failed_database_commit_keeps_previous_asset_and_leaves_no_orphan(
    tmp_path: Path,
) -> None:
    first_source = tmp_path / "first.png"
    second_source = tmp_path / "second.png"
    _write_nai_png(first_source, {"prompt": "first", "seed": 1})
    _write_nai_png(
        second_source,
        {"prompt": "second", "seed": 2},
        color=(180, 20, 90),
    )
    source_by_url = {
        "https://i.pximg.test/102_first.png": first_source,
        "https://i.pximg.test/102_second.png": second_source,
    }

    def download(url: str, destination: Path) -> None:
        shutil.copy2(source_by_url[url], destination)

    base = PixivWork(
        work_id=102,
        user_id=202,
        user_name="Carol",
        title="transaction",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, "https://i.pximg.test/102_first.png"),),
    )
    db = Database(tmp_path / "transaction.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        intake.ingest_work(base, download)
        before = db.get_work_detail(102)
        assert before is not None
        old_asset = tmp_path / "images" / before["images"][0]["local_path"]
        old_thumbnail = tmp_path / "images" / before["images"][0]["thumbnail_path"]
        old_digest = hashlib.sha256(old_asset.read_bytes()).hexdigest()
        db.conn.execute(
            """
            CREATE TRIGGER reject_work_update
            BEFORE UPDATE ON works
            BEGIN
                SELECT RAISE(ABORT, 'test transaction failure');
            END
            """
        )
        db.conn.commit()

        try:
            intake.ingest_work(
                replace(
                    base,
                    pages=(PixivPage(0, "https://i.pximg.test/102_second.png"),),
                ),
                download,
            )
        except Exception as exc:
            assert "test transaction failure" in str(exc)
        else:
            raise AssertionError("expected the database transaction to fail")

        after = db.get_work_detail(102)
        assets = list((tmp_path / "images").rglob("*.*"))
    finally:
        db.close()

    assert after == before
    assert old_asset.is_file()
    assert old_thumbnail.is_file()
    assert hashlib.sha256(old_asset.read_bytes()).hexdigest() == old_digest
    assert set(assets) == {old_asset, old_thumbnail}


def test_multi_page_downloads_use_bounded_parallelism(tmp_path: Path) -> None:
    source = tmp_path / "parallel.png"
    _write_nai_png(source, {"prompt": "parallel", "seed": 7})
    active = 0
    peak_active = 0
    lock = threading.Lock()

    def download(_url: str, destination: Path) -> None:
        nonlocal active, peak_active
        with lock:
            active += 1
            peak_active = max(peak_active, active)
        try:
            time.sleep(0.05)
            shutil.copy2(source, destination)
        finally:
            with lock:
                active -= 1

    work = PixivWork(
        work_id=103,
        user_id=203,
        user_name="Parallel",
        title="parallel pages",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=tuple(
            PixivPage(index, f"https://i.pximg.test/103_p{index}.png")
            for index in range(4)
        ),
    )
    db = Database(tmp_path / "parallel.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
        page_workers=3,
    )
    try:
        receipt = intake.ingest_work(work, download)
    finally:
        db.close()

    assert receipt.accepted_pages == 4
    assert peak_active >= 2


def test_transient_page_failure_never_replaces_a_complete_existing_work(
    tmp_path: Path,
) -> None:
    original = tmp_path / "original.png"
    replacement = tmp_path / "replacement.png"
    _write_nai_png(original, {"prompt": "original", "seed": 1})
    _write_nai_png(replacement, {"prompt": "replacement", "seed": 2})
    old_url = "https://i.pximg.test/104_old.png"
    new_urls = (
        "https://i.pximg.test/104_new_p0.png",
        "https://i.pximg.test/104_new_p1.png",
    )
    should_fail = True

    def download(url: str, destination: Path) -> None:
        if url == old_url:
            shutil.copy2(original, destination)
            return
        if should_fail and url.endswith("p1.png"):
            raise OSError("temporary network failure")
        shutil.copy2(replacement, destination)

    base = PixivWork(
        work_id=104,
        user_id=204,
        user_name="Retry",
        title="retry",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, old_url),),
    )
    replacement_work = replace(
        base,
        pages=tuple(PixivPage(index, url) for index, url in enumerate(new_urls)),
    )
    db = Database(tmp_path / "retry.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        intake.ingest_work(base, download)
        before = db.get_work_detail(104)
        failed = intake.ingest_work(replacement_work, download)
        after_failure = db.get_work_detail(104)
        should_fail = False
        recovered = intake.ingest_work(replacement_work, download)
        after_recovery = db.get_work_detail(104)
    finally:
        db.close()

    assert failed.status == "failed"
    assert failed.accepted_pages == 0
    assert after_failure == before
    assert recovered.status == "accepted"
    assert after_recovery is not None
    assert len(after_recovery["images"]) == 2
    assert after_recovery["images"][0]["ai_json"]["Comment"]["seed"] == 2


def test_corrupted_cached_asset_is_downloaded_and_verified_again(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _write_nai_png(source, {"prompt": "integrity", "seed": 8})
    calls = 0

    def download(_url: str, destination: Path) -> None:
        nonlocal calls
        calls += 1
        shutil.copy2(source, destination)

    work = PixivWork(
        work_id=105,
        user_id=205,
        user_name="Integrity",
        title="integrity",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, "https://i.pximg.test/105.png"),),
    )
    db = Database(tmp_path / "integrity.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        intake.ingest_work(work, download)
        detail = db.get_work_detail(105)
        assert detail is not None
        asset = tmp_path / "images" / detail["images"][0]["local_path"]
        asset.write_bytes(b"corrupt")
        receipt = intake.ingest_work(work, download)
    finally:
        db.close()

    assert receipt.status == "accepted"
    assert calls == 2
    # Corrupted compressed asset is re-published as WebP; NAI metadata lives in DB.
    assert asset.is_file() and asset.suffix.lower() == ".webp"
    assert asset.stat().st_size > 8
    assert detail["images"][0]["ai_json"]["Comment"]["seed"] == 8


def test_thumbnail_only_mode_keeps_first_original_and_stores_thumbs(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    first = sources / "p0.png"
    thumb1 = sources / "p1.jpg"
    thumb2 = sources / "p2.jpg"
    _write_nai_png(first, {"prompt": "1girl, blue eyes", "seed": 1})
    Image.new("RGB", (64, 96), (30, 60, 90)).save(thumb1)
    Image.new("RGB", (64, 96), (90, 60, 30)).save(thumb2)
    source_by_url = {
        "https://i.pximg.test/100_p0.png": first,
        "https://i.pximg.test/100_p1_thumb.jpg": thumb1,
        "https://i.pximg.test/100_p2_thumb.jpg": thumb2,
    }
    download_calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        download_calls.append(url)
        shutil.copy2(source_by_url[url], destination)

    db = Database(tmp_path / "gallery.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
        thumbnail_only_pages=True,
    )
    work = PixivWork(
        work_id=100,
        user_id=200,
        user_name="Alice",
        title="two pages",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T01:02:03+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(
            PixivPage(0, "https://i.pximg.test/100_p0.png"),
            PixivPage(1, "https://i.pximg.test/100_p1.png", "https://i.pximg.test/100_p1_thumb.jpg"),
        ),
    )
    try:
        receipt = intake.ingest_work(work, download)
    finally:
        db.close()

    assert receipt.accepted_pages == 2
    assert [r.status for r in receipt.pages] == ["accepted", "accepted"]
    assert [r.reason for r in receipt.pages] == ["accepted", "thumbnail"]
    # the original of page 1 must never be fetched in thumbnail mode
    assert not any("100_p1.png" in url for url in download_calls)
    assert any("100_p1_thumb.jpg" in url for url in download_calls)
    thumbs = [f for f in (tmp_path / "images" / "_thumbs").rglob("*") if f.is_file()]
    originals = [f for f in (tmp_path / "images" / "NAI").rglob("*") if f.is_file()]
    # Later pages land under _thumbs as compressed WebP; page 0 is also WebP under NAI/.
    assert thumbs and all(f.suffix.lower() == ".webp" for f in thumbs)
    assert len(originals) == 1
    assert originals[0].suffix.lower() == ".webp"


def test_thumbnail_only_mode_rejects_when_p0_not_nai(tmp_path: Path) -> None:
    """Later thumbs must not admit a work whose cover failed NAI parse."""
    sources = tmp_path / "sources"
    sources.mkdir()
    plain0 = sources / "p0.png"
    thumb1 = sources / "p1.jpg"
    Image.new("RGB", (64, 96), (1, 2, 3)).save(plain0)
    Image.new("RGB", (64, 96), (30, 60, 90)).save(thumb1)
    source_by_url = {
        "https://i.pximg.test/110_p0.png": plain0,
        "https://i.pximg.test/110_p1_thumb.jpg": thumb1,
    }

    def download(url: str, destination: Path) -> None:
        shutil.copy2(source_by_url[url], destination)

    db = Database(tmp_path / "gallery.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
        thumbnail_only_pages=True,
    )
    work = PixivWork(
        work_id=110,
        user_id=200,
        user_name="Alice",
        title="no nai cover",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T01:02:03+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(
            PixivPage(0, "https://i.pximg.test/110_p0.png"),
            PixivPage(
                1,
                "https://i.pximg.test/110_p1.png",
                "https://i.pximg.test/110_p1_thumb.jpg",
            ),
        ),
    )
    try:
        receipt = intake.ingest_work(work, download)
    finally:
        db.close()

    assert receipt.accepted_pages == 0
    assert receipt.pages[0].status == "rejected"
    assert receipt.pages[1].reason == "thumbnail_requires_p0_nai"


def test_thumbnail_only_mode_rejects_page_without_thumbnail_url(
    tmp_path: Path,
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    first = sources / "p0.png"
    _write_nai_png(first, {"prompt": "1girl", "seed": 1})
    download_calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        download_calls.append(url)
        shutil.copy2(first, destination)

    db = Database(tmp_path / "gallery.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
        thumbnail_only_pages=True,
    )
    work = PixivWork(
        work_id=101,
        user_id=200,
        user_name="Alice",
        title="no thumb",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T01:02:03+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(
            PixivPage(0, "https://i.pximg.test/101_p0.png"),
            PixivPage(1, "https://i.pximg.test/101_p1.png"),
        ),
    )
    try:
        receipt = intake.ingest_work(work, download)
    finally:
        db.close()

    assert [r.status for r in receipt.pages] == ["accepted", "rejected"]
    assert receipt.pages[1].reason == "thumbnail_missing"
    assert not any("101_p1.png" in url for url in download_calls)

def test_permanent_no_nai_rejection_skips_future_downloads(tmp_path: Path) -> None:
    plain = tmp_path / "plain.png"
    Image.new("RGB", (64, 96), (11, 22, 33)).save(plain)
    calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        calls.append(url)
        shutil.copy2(plain, destination)

    work = PixivWork(
        work_id=777,
        user_id=300,
        user_name="NoNAI",
        title="never nai",
        caption="",
        tags=("foo",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=0,
        pages=(PixivPage(0, "https://i.pximg.test/777.png"),),
    )
    db = Database(tmp_path / "skip.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        first = intake.ingest_work(work, download)
        second = intake.ingest_work(replace(work, title="still never nai"), download)
        third = intake.ingest_work(work, download)
        detail = db.get_work_detail(777)
        rows = db.conn.execute(
            "SELECT status, reason FROM pixiv_nai_receipts WHERE work_id=777"
        ).fetchall()
    finally:
        db.close()

    assert first.status == "rejected"
    assert first.pages[0].reason == "nai_metadata_missing"
    assert second.status == "unchanged"
    assert third.status == "unchanged"
    assert detail is None
    assert len(calls) == 1
    assert [(row["status"], row["reason"]) for row in rows] == [
        ("rejected", "nai_metadata_missing")
    ]


def test_published_assets_are_smaller_webp_than_source_png(tmp_path: Path) -> None:
    source = tmp_path / "big.png"
    Image.new("RGB", (800, 1200), (40, 80, 120)).save(source)
    # inject NAI metadata into a large-ish PNG
    from PIL import PngImagePlugin
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Software", "NovelAI")
    png_info.add_text("Source", "NovelAI Diffusion V4.5")
    png_info.add_text("Description", "1girl")
    png_info.add_text("Comment", json.dumps({"prompt": "1girl", "seed": 9}, ensure_ascii=False))
    Image.new("RGB", (800, 1200), (40, 80, 120)).save(source, pnginfo=png_info)

    def download(_url: str, destination: Path) -> None:
        shutil.copy2(source, destination)

    work = PixivWork(
        work_id=888,
        user_id=400,
        user_name="Compress",
        title="compress me",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, "https://i.pximg.test/888.png"),),
    )
    db = Database(tmp_path / "compress.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        receipt = intake.ingest_work(work, download)
        detail = db.get_work_detail(888)
    finally:
        db.close()

    assert receipt.status == "accepted"
    assert detail is not None
    stored = tmp_path / "images" / detail["images"][0]["local_path"]
    assert stored.suffix.lower() == ".webp"
    assert stored.stat().st_size < source.stat().st_size
    assert detail["images"][0]["ai_json"]["Comment"]["seed"] == 9

def test_permanent_rejected_page_is_not_redownloaded_on_reingest(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    good = sources / "good.png"
    plain = sources / "plain.png"
    _write_nai_png(good, {"prompt": "1girl", "seed": 1})
    Image.new("RGB", (64, 96), (9, 9, 9)).save(plain)
    source_by_url = {
        "https://i.pximg.test/900_p0.png": good,
        "https://i.pximg.test/900_p1.png": plain,
    }
    calls: list[str] = []

    def download(url: str, destination: Path) -> None:
        calls.append(url)
        shutil.copy2(source_by_url[url], destination)

    work = PixivWork(
        work_id=900,
        user_id=500,
        user_name="Mix",
        title="partial",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(
            PixivPage(0, "https://i.pximg.test/900_p0.png"),
            PixivPage(1, "https://i.pximg.test/900_p1.png"),
        ),
    )
    db = Database(tmp_path / "mix.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "images",
        staging_dir=tmp_path / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    try:
        first = intake.ingest_work(work, download)
        assert first.status == "partial"
        assert first.pages[1].reason == "nai_metadata_missing"
        first_calls = list(calls)
        # Corrupt accepted asset so full re-ingest path is forced.
        detail = db.get_work_detail(900)
        asset = tmp_path / "images" / detail["images"][0]["local_path"]
        asset.write_bytes(b"broken")
        second = intake.ingest_work(replace(work, title="partial-v2"), download)
    finally:
        db.close()

    assert second.status == "accepted" or second.status == "partial"
    # Permanent rejected page must not be downloaded again.
    assert first_calls.count("https://i.pximg.test/900_p1.png") == 1
    assert calls.count("https://i.pximg.test/900_p1.png") == 1
    assert calls.count("https://i.pximg.test/900_p0.png") == 2


def test_migrate_originals_to_webp_updates_paths(tmp_path: Path) -> None:
    from gallery_maintenance import GalleryMaintenance

    # Seed a legacy PNG via temporary intake then rename style is already webp;
    # write a fake legacy row + png file to migrate.
    data = tmp_path / "data"
    images = data / "images" / "NAI" / "1"
    images.mkdir(parents=True)
    legacy = images / "legacy.png"
    Image.new("RGB", (120, 160), (20, 40, 60)).save(legacy)
    db_path = data / "aitag.db"
    import sqlite3
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE works(
          id INTEGER PRIMARY KEY, preview_path TEXT, preview_downloaded INTEGER
        );
        CREATE TABLE work_images(
          work_id INTEGER, page_index INTEGER, local_path TEXT, image_path TEXT,
          file_name TEXT, source_sha256 TEXT, source_page_index INTEGER, downloaded INTEGER
        );
        CREATE TABLE pixiv_nai_receipts(
          work_id INTEGER, display_page_index INTEGER, local_path TEXT, source_sha256 TEXT
        );
        """
    )
    conn.execute("INSERT INTO works(id, preview_path, preview_downloaded) VALUES (1, ?, 1)", ("images/NAI/1/legacy.png",))
    conn.execute(
        "INSERT INTO work_images(work_id, page_index, local_path, image_path, file_name, source_sha256, source_page_index, downloaded) VALUES (1,0,?,?,?,'abc',0,1)",
        # Legacy rows may store the images/ prefix — migration must strip it.
        ("images/NAI/1/legacy.png", "NAI/1/legacy.png", "legacy.png"),
    )
    conn.execute(
        "INSERT INTO pixiv_nai_receipts(work_id, display_page_index, local_path, source_sha256) VALUES (1,0,?,'abc')",
        ("NAI/1/legacy.png",),
    )
    conn.commit()
    conn.close()

    maintenance = GalleryMaintenance(data)
    preview = maintenance.migrate_originals_to_webp(dry_run=True)
    assert preview["candidates"] == 1
    result = maintenance.migrate_originals_to_webp(dry_run=False)
    assert result["migrated"] == 1
    assert result["failed"] == 0
    assert not legacy.exists()
    webp = data / "images" / "NAI" / "1" / "legacy.webp"
    assert webp.is_file()
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT local_path FROM work_images WHERE work_id=1").fetchone()
    preview_path = conn.execute("SELECT preview_path FROM works WHERE id=1").fetchone()[0]
    conn.close()
    assert row[0].endswith(".webp")
    assert not str(row[0]).startswith("images/")
    assert str(preview_path).endswith(".webp")

"""Regression tests for the crawler/storage pipeline defect-fix batch."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
from PIL import Image, PngImagePlugin

import crawler_control
import favorites
import production_queue
from db import Database, SCHEMA_VERSION
from db_compression import compress_text
from gallery_asset_store import GalleryAssetStore
from gallery_maintenance import GalleryMaintenance
from gallery_snapshot import (
    GallerySnapshotManager,
    maintenance_mode,
    maintenance_mode_active,
)
from pixiv_nai_intake import PageReceipt, PixivNAIIntake, PixivPage, PixivWork


def _write_nai_png(path: Path, prompt: str = "1girl", seed: int = 1) -> None:
    png_info = PngImagePlugin.PngInfo()
    png_info.add_text("Software", "NovelAI")
    png_info.add_text("Source", "NovelAI Diffusion V4.5")
    png_info.add_text("Description", prompt)
    png_info.add_text(
        "Comment",
        json.dumps({"prompt": prompt, "uc": "lowres", "steps": 28, "seed": seed}),
    )
    Image.new("RGB", (64, 96), (32, 48, 64)).save(path, pnginfo=png_info)


def _make_intake(tmp_path: Path) -> tuple[Database, PixivNAIIntake]:
    db = Database(tmp_path / "data" / "aitag.db")
    intake = PixivNAIIntake(
        db=db,
        images_dir=tmp_path / "data" / "images",
        staging_dir=tmp_path / "data" / "staging",
        allowed_image_hosts=("i.pximg.test",),
    )
    return db, intake


def _make_work(work_id: int, urls: list[str]) -> PixivWork:
    return PixivWork(
        work_id=work_id,
        user_id=200,
        user_name="Alice",
        title=f"work {work_id}",
        caption="caption",
        tags=("NovelAI",),
        create_date="2026-08-02T01:02:03+00:00",
        total_view=10,
        total_bookmarks=5,
        pages=tuple(
            PixivPage(source_page_index=index, original_url=url)
            for index, url in enumerate(urls)
        ),
    )


# --- Fix 1: snapshot create/restore stop the crawler; restore holds the lock


def test_snapshot_create_and_restore_stop_crawler_first(tmp_path: Path) -> None:
    data = tmp_path / "data"
    images = data / "images"
    images.mkdir(parents=True)
    (images / "asset.bin").write_bytes(b"asset")
    database = data / "aitag.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("CREATE TABLE works(id INTEGER PRIMARY KEY)")
        connection.commit()

    calls: list[str] = []

    def fake_stopper() -> dict[str, object]:
        calls.append("stop")
        return {"crawler_pixiv": [4321]}

    manager = GallerySnapshotManager(database, images, crawler_stopper=fake_stopper)
    created = manager.create(tmp_path / "snap.zip")
    assert calls == ["stop"]
    assert created["crawler_stopped"] == {"crawler_pixiv": [4321]}

    restored = manager.restore(tmp_path / "snap.zip", confirm=True)
    assert calls == ["stop", "stop"]
    assert restored["crawler_stopped"] == {"crawler_pixiv": [4321]}
    # The maintenance lock is always released, even on the success path.
    assert not maintenance_mode_active(data)


def test_maintenance_lock_blocks_intake_writes(tmp_path: Path) -> None:
    db, intake = _make_intake(tmp_path)
    try:
        data_dir = tmp_path / "data"
        assert not maintenance_mode_active(data_dir)
        with maintenance_mode(data_dir):
            assert maintenance_mode_active(data_dir)
            with pytest.raises(RuntimeError, match="maintenance"):
                intake._assert_writable()
        assert not maintenance_mode_active(data_dir)
    finally:
        db.close()


def test_crawler_start_refused_during_maintenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawler_control, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        crawler_control,
        "_spawn_detached_ps",
        lambda **kwargs: pytest.fail("must not spawn"),
    )
    with maintenance_mode(tmp_path):
        result = crawler_control.start_pixiv_crawler(watch=False)
    assert result["mode"] == "maintenance"
    assert result["started"] is False


# --- Fix 2: cross-process crawler lock file


def test_crawler_file_lock_excludes_second_holder(tmp_path: Path) -> None:
    path = tmp_path / "pixiv_nai_crawler.lock"
    first = crawler_control.CrawlerFileLock(path).acquire()
    try:
        assert path.read_text(encoding="ascii").strip() == str(os.getpid())
        with pytest.raises(crawler_control.CrawlerLockHeld):
            crawler_control.CrawlerFileLock(path).acquire()
    finally:
        first.release()
    assert not path.exists()


def test_crawler_file_lock_reclaims_stale_pid(tmp_path: Path) -> None:
    # A finished child's pid is dead by the time we use it.
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    dead_pid = child.pid
    assert not crawler_control.pid_alive(dead_pid)

    path = tmp_path / "pixiv_nai_crawler.lock"
    path.write_text(str(dead_pid), encoding="ascii")
    lock = crawler_control.CrawlerFileLock(path).acquire()
    try:
        assert lock.holder_pid() == os.getpid()
    finally:
        lock.release()


def test_start_pixiv_crawler_honors_lock_holder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock_path = tmp_path / "pixiv_nai_crawler.lock"
    lock_path.write_text(str(os.getpid()), encoding="ascii")
    monkeypatch.setattr(
        crawler_control, "pixiv_crawler_lock_path", lambda root=None: lock_path
    )
    monkeypatch.setattr(crawler_control, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        crawler_control, "_list_pixiv_crawler_pids_uncached", lambda: []
    )
    monkeypatch.setattr(
        crawler_control,
        "_spawn_detached_ps",
        lambda **kwargs: pytest.fail("must not spawn"),
    )
    result = crawler_control.start_pixiv_crawler(watch=False)
    assert result["already_running"] is True
    assert result["pid"] == os.getpid()


# --- Fix 3: publish/persist crash window reconcile


def test_intake_marks_dirty_around_publish_and_clears_after_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    page = sources / "p0.png"
    _write_nai_png(page, prompt="1girl, blue eyes")

    db, intake = _make_intake(tmp_path)
    seen: list[bool] = []
    original = intake._publish_staged_assets

    def spy(images: list[dict[str, object]]):
        seen.append(intake._dirty_flag.is_file())
        return original(images)

    monkeypatch.setattr(intake, "_publish_staged_assets", spy)
    try:
        receipt = intake.ingest_work(
            _make_work(100, ["https://i.pximg.test/100_p0.png"]),
            lambda url, dest: shutil.copy2(page, dest),
        )
        assert receipt.status == "accepted"
        assert seen == [True], "dirty flag must be set before publish"
        assert not intake._dirty_flag.is_file(), "flag cleared after commit"
    finally:
        db.close()


def test_intake_startup_quarantines_orphans_when_dirty(tmp_path: Path) -> None:
    db, intake = _make_intake(tmp_path)
    images = tmp_path / "data" / "images"
    live = images / "NAI" / "7" / "live_p0.webp"
    orphan = images / "NAI" / "7" / "orphan_p0.webp"
    live.parent.mkdir(parents=True, exist_ok=True)
    live.write_bytes(b"live")
    orphan.write_bytes(b"orphan")
    db.conn.execute(
        "INSERT INTO works(id, title) VALUES (7, 'live')"
    )
    db.conn.execute(
        "INSERT INTO work_images(work_id, page_index, local_path, downloaded) "
        "VALUES (7, 0, 'NAI/7/live_p0.webp', 1)"
    )
    db.conn.commit()
    # Simulate the crash: process died between publish and DB commit.
    intake._mark_intake_dirty()
    db.close()

    db2, intake2 = _make_intake(tmp_path)
    try:
        assert not intake2._dirty_flag.is_file()
        assert live.is_file(), "referenced asset must survive"
        assert not orphan.exists()
        quarantined = images / "_orphans" / "NAI" / "7" / "orphan_p0.webp"
        assert quarantined.is_file()
        assert quarantined.read_bytes() == b"orphan"
    finally:
        db2.close()


def test_asset_store_reconcile_never_reflags_quarantined_files(
    tmp_path: Path,
) -> None:
    images = tmp_path / "images"
    store = GalleryAssetStore(images)
    orphan = images / "NAI" / "1" / "x.webp"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"x")

    first = store.reconcile([], quarantine=images / "_orphans")
    assert first["quarantined_files"] == 1
    second = store.reconcile([], quarantine=images / "_orphans")
    assert second["orphan_files"] == 0
    assert second["quarantined_files"] == 0
    assert (images / "_orphans" / "NAI" / "1" / "x.webp").is_file()


# --- Fix 4: partial-page works are surfaced


def test_partial_work_exposes_page_status_in_detail(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    p0 = sources / "p0.png"
    p1 = sources / "p1.png"
    p2 = sources / "p2.png"
    _write_nai_png(p0, prompt="1girl", seed=1)
    Image.new("RGB", (64, 96), (80, 40, 20)).save(p1)  # no NAI metadata
    _write_nai_png(p2, prompt="1boy", seed=3)
    by_url = {
        "https://i.pximg.test/300_p0.png": p0,
        "https://i.pximg.test/300_p1.png": p1,
        "https://i.pximg.test/300_p2.png": p2,
    }
    db, intake = _make_intake(tmp_path)
    try:
        receipt = intake.ingest_work(
            _make_work(300, list(by_url)),
            lambda url, dest: shutil.copy2(by_url[url], dest),
        )
        assert receipt.status == "partial"
        detail = db.get_work_detail(300)
        assert detail is not None
        assert detail["page_status"] == {
            "accepted": 2,
            "total": 3,
            "partial": True,
        }
        assert detail["work"]["partial"] is True
    finally:
        db.close()


def test_complete_work_is_not_marked_partial(tmp_path: Path) -> None:
    sources = tmp_path / "sources"
    sources.mkdir()
    p0 = sources / "p0.png"
    _write_nai_png(p0, prompt="1girl", seed=9)
    db, intake = _make_intake(tmp_path)
    try:
        receipt = intake.ingest_work(
            _make_work(301, ["https://i.pximg.test/301_p0.png"]),
            lambda url, dest: shutil.copy2(p0, dest),
        )
        assert receipt.status == "accepted"
        detail = db.get_work_detail(301)
        assert detail is not None
        assert detail["page_status"]["partial"] is False
        assert detail["work"]["partial"] is False
    finally:
        db.close()


# --- Fix 5: full-reject removes favorites/queue references


def test_full_reject_drops_favorites_and_queue_refs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fav_path = tmp_path / "data" / "favorites.json"
    queue_path = tmp_path / "data" / "production_queue.json"
    monkeypatch.setattr(favorites, "FAV_PATH", fav_path)
    monkeypatch.setattr(production_queue, "QUEUE_PATH", queue_path)

    sources = tmp_path / "sources"
    sources.mkdir()
    p0 = sources / "p0.png"
    _write_nai_png(p0, prompt="1girl", seed=11)
    db, intake = _make_intake(tmp_path)
    try:
        receipt = intake.ingest_work(
            _make_work(400, ["https://i.pximg.test/400_p0.png"]),
            lambda url, dest: shutil.copy2(p0, dest),
        )
        assert receipt.status == "accepted"
        favorites.add(400, "site")
        production_queue.add(400, gallery_id="site")
        assert favorites.has(400, "site")
        assert production_queue.has(400, "site")

        work = _make_work(400, ["https://i.pximg.test/400_p0.png"])
        intake._persist(
            work,
            "fingerprint-changed",
            [],
            [
                PageReceipt(
                    0, "https://i.pximg.test/400_p0.png", "rejected", "not_novelai"
                )
            ],
        )
        assert db.get_work_detail(400) is None
        assert not favorites.has(400, "site")
        assert not production_queue.has(400, "site")
    finally:
        db.close()


# --- Fix 6: list upsert re-syncs FTS when text columns change


def test_list_upsert_resyncs_fts_when_tags_change(tmp_path: Path) -> None:
    db = Database(tmp_path / "gallery.db")
    try:
        item = {
            "id": 42,
            "userId": 7,
            "title": "fixture",
            "caption": "",
            "tags": "alpha",
            "AI_type": "NAI",
            "create_date": "2026-08-01T00:00:00",
            "image_count": 1,
        }
        db.upsert_list_item(item, "2026-08-01T00:00:00")
        db.conn.execute(
            "UPDATE works SET detail_json = ? WHERE id = ?",
            (
                compress_text(
                    json.dumps({"work": item, "images": []}, ensure_ascii=False)
                ),
                42,
            ),
        )
        db.conn.commit()
        assert not db.search_works(q="betatag")["items"]

        changed = dict(item, tags="alpha, betatag")
        db.upsert_list_item(changed, "2026-08-02T00:00:00")
        db.conn.commit()

        hits = db.search_works(q="betatag")["items"]
        assert [row["id"] for row in hits] == [42]
    finally:
        db.close()


def test_list_upsert_skips_fts_when_text_unchanged(tmp_path: Path) -> None:
    db = Database(tmp_path / "gallery.db")
    try:
        item = {
            "id": 43,
            "userId": 7,
            "title": "fixture",
            "caption": "",
            "tags": "alpha",
            "AI_type": "NAI",
            "create_date": "2026-08-01T00:00:00",
            "image_count": 1,
        }
        db.upsert_list_item(item, "2026-08-01T00:00:00")
        db.conn.execute(
            "UPDATE works SET detail_json = ? WHERE id = ?",
            (compress_text(json.dumps({"work": item, "images": []})), 43),
        )
        db.conn.commit()
        synced: list[int] = []
        original = db._sync_work_fts
        db._sync_work_fts = lambda work_id: synced.append(work_id)
        try:
            changed = dict(item, total_view=999)
            db._upsert_list_item_impl(changed, "2026-08-02T00:00:00")
        finally:
            db._sync_work_fts = original
        assert synced == [], "non-text changes must not trigger FTS sync"
    finally:
        db.close()


# --- Fix 7: rebuild_fts covers prompt_work_fts; ready flag read fresh


def test_rebuild_fts_rebuilds_prompt_work_index(tmp_path: Path) -> None:
    db = Database(tmp_path / "gallery.db")
    try:
        db.conn.execute(
            "INSERT INTO works(id, title, ai_type, list_json) "
            "VALUES (9, 'w', 'NAI', '{}')"
        )
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, prompt_text, downloaded) "
            "VALUES (9, 0, '1girl, rem', 1)"
        )
        db.conn.commit()

        db.rebuild_fts()

        assert db.get_state("prompt_work_fts_ready", "0") == "1"
        assert db.prompt_search_table() == "prompt_work_fts"
        rows = db.conn.execute(
            "SELECT work_id, prompt_text FROM prompt_work_fts"
        ).fetchall()
        assert [(int(r["work_id"]), r["prompt_text"]) for r in rows] == [
            (9, "1girl, rem")
        ]
        # Fresh read: an external invalidation is honored immediately.
        db.set_state("prompt_work_fts_ready", "0")
        assert db.prompt_search_table() == "prompt_fts"
    finally:
        db.close()


# --- Fix 8: rebuild_thumbnails skips rows already under _thumbs/


def test_rebuild_thumbnails_skips_thumbnail_rows(tmp_path: Path) -> None:
    data = tmp_path / "data"
    images = data / "images"
    original = images / "NAI" / "9" / "22_p0.png"
    original.parent.mkdir(parents=True)
    Image.new("RGB", (1200, 800), (12, 34, 56)).save(original)
    thumb_page = images / "_thumbs" / "9" / "22_p1.webp"
    thumb_page.parent.mkdir(parents=True)
    Image.new("RGB", (320, 200), (1, 2, 3)).save(thumb_page, format="WEBP")

    db = Database(data / "aitag.db")
    try:
        db.conn.execute("INSERT INTO works(id, title) VALUES (22, 'work')")
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, local_path, downloaded) "
            "VALUES (22, 0, 'NAI/9/22_p0.png', 1)"
        )
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, local_path, downloaded) "
            "VALUES (22, 1, '_thumbs/9/22_p1.webp', 1)"
        )
        db.conn.commit()
    finally:
        db.close()

    receipt = GalleryMaintenance(data).rebuild_thumbnails()

    assert receipt["skipped_thumbs"] == 1
    assert receipt["total"] == 1
    assert not (images / "_thumbs" / "_thumbs").exists(), (
        "must not derive thumbnails of thumbnails"
    )


# --- Fix 9: bounded CDN miss cache


def test_cdn_miss_cache_is_bounded() -> None:
    from server_shared import record_cdn_miss

    cache: dict[str, float] = {}
    now = 1000.0
    for index in range(12):
        record_cdn_miss(f"u{index}", now, cache=cache, ttl=60.0, max_size=10)
    # All entries are fresh, so the oldest half was dropped to make room.
    assert len(cache) <= 10
    assert "u11" in cache

    expired = {f"old{index}": now - 120.0 for index in range(10)}
    record_cdn_miss("new", now, cache=expired, ttl=60.0, max_size=10)
    assert len(expired) == 1 and "new" in expired


# --- Fix 10: schema user_version guard


def test_schema_version_recorded_and_newer_db_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "gallery.db"
    db = Database(path)
    try:
        row = db.conn.execute("PRAGMA user_version").fetchone()
        assert int(row[0]) == SCHEMA_VERSION
        db.conn.execute(f"PRAGMA user_version={SCHEMA_VERSION + 41}")
        db.conn.commit()
    finally:
        db.close()

    with caplog.at_level(logging.WARNING):
        db2 = Database(path)
        try:
            row = db2.conn.execute("PRAGMA user_version").fetchone()
            assert int(row[0]) == SCHEMA_VERSION + 41, "never downgrade the DB"
        finally:
            db2.close()
    assert any("newer than this code" in record.message for record in caplog.records)

"""Open-source readiness regression tests.

These tests focus on public-facing safety promises without limiting crawler,
generation, automation or publishing features.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from db import Database
from routes import compliance
from routes import update as update_routes


@pytest.fixture()
def compliance_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    data_dir = tmp_path / "data"
    (data_dir / "images").mkdir(parents=True)
    db = Database(data_dir / "aitag.db")
    # This table is normally created by PixivNAIIntake. Create it here so the
    # cleanup path is tested against a complete production-shaped database.
    db.conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pixiv_nai_receipts (
            work_id INTEGER NOT NULL,
            source_page_index INTEGER NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            work_fingerprint TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            display_page_index INTEGER,
            local_path TEXT,
            source_sha256 TEXT,
            parser_version TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (work_id, source_page_index)
        )
        """
    )
    db.conn.commit()
    monkeypatch.setattr(compliance, "DB", db)
    monkeypatch.setattr(compliance, "DATA_DIR", data_dir)
    try:
        yield db, data_dir
    finally:
        db.close()


def add_work(
    db: Database,
    *,
    work_id: int,
    author_id: int = 42,
    local_path: str = "",
    notice: str | None = None,
) -> None:
    db.conn.execute(
        "INSERT INTO works(id, user_id, user_name, title, source_url, no_ai_notice, crawled_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            work_id,
            author_id,
            "Test Author",
            f"Work {work_id}",
            f"https://www.pixiv.net/artworks/{work_id}",
            notice,
            "2026-08-04T00:00:00",
        ),
    )
    if local_path:
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, local_path, image_path, downloaded) "
            "VALUES (?, 0, ?, ?, 1)",
            (work_id, local_path, local_path),
        )
    db.conn.commit()


def test_source_gone_status_is_preserved(compliance_store):
    db, _ = compliance_store
    add_work(db, work_id=1001)

    result = compliance.sync_removed_works(
        {"work_ids": [1001], "status": "source_gone"}
    )

    assert result == {"ok": True, "updated": 1, "skipped": 0}
    row = db.conn.execute(
        "SELECT removed_status FROM works WHERE id = 1001"
    ).fetchone()
    assert row["removed_status"] == "source_gone"


def test_invalid_removed_status_is_rejected(compliance_store):
    with pytest.raises(HTTPException) as exc:
        compliance.sync_removed_works({"work_ids": [1], "status": "unknown"})
    assert exc.value.status_code == 400


def test_notice_version_upgrade_requires_new_acknowledgment(compliance_store):
    db, _ = compliance_store
    db.set_state(
        "responsibility_notice",
        json.dumps({"notice_version": "1.0", "accepted_at": "old"}),
    )

    stale = compliance.notice_status()
    assert stale["accepted_current"] is False
    assert stale["required"] is True

    accepted = compliance.notice_accept({"app_version": "0.9.0"})
    current = compliance.notice_status()
    assert accepted["record"]["notice_version"] == compliance.NOTICE_VERSION
    assert current["accepted_current"] is True
    assert current["required"] is False


def test_delete_scope_surfaces_existing_local_material(compliance_store):
    db, _ = compliance_store
    add_work(db, work_id=2001, author_id=99)

    result = compliance.add_blacklist(
        {"author_id": 99, "author_name": "Blocked", "scope": "delete"}
    )
    listed = compliance.list_blacklist()["items"][0]

    assert result["cleanup_required"] is True
    assert listed["scope"] == "delete"
    assert listed["local_works"] == 1
    assert listed["cleanup_required"] is True


def test_author_cleanup_moves_assets_to_recoverable_trash(compliance_store):
    db, data_dir = compliance_store
    relative = Path("images") / "NAI" / "42" / "3001.png"
    source = data_dir / relative
    source.parent.mkdir(parents=True)
    source.write_bytes(b"not-a-real-image")
    add_work(db, work_id=3001, author_id=42, local_path=relative.as_posix())

    result = compliance.delete_author_material(42)

    assert result["ok"] is True
    assert result["deleted_works"] == 1
    assert result["files_moved"] == 1
    assert not source.exists()
    trash = data_dir / "_trash" / "author_42" / relative
    assert trash.read_bytes() == b"not-a-real-image"
    assert db.conn.execute("SELECT 1 FROM works WHERE id = 3001").fetchone() is None
    assert db.conn.execute(
        "SELECT 1 FROM work_images WHERE work_id = 3001"
    ).fetchone() is None


def test_author_cleanup_rejects_paths_outside_data_roots(compliance_store, tmp_path: Path):
    db, _ = compliance_store
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"keep")
    add_work(db, work_id=3002, author_id=43, local_path=str(outside))

    result = compliance.delete_author_material(43)

    assert result["ok"] is False
    assert result["file_failures"][0]["error"] == "path_outside_allowed_roots"
    assert outside.read_bytes() == b"keep"


def test_export_manifest_keeps_author_and_source_provenance(compliance_store):
    db, _ = compliance_store
    add_work(
        db,
        work_id=4001,
        author_id=77,
        local_path="images/NAI/77/4001.png",
        notice="禁止转载",
    )

    result = compliance.export_manifest("4001")
    item = result["items"][0]

    assert item["author_id"] == 77
    assert item["author_name"] == "Test Author"
    assert item["work_url"].endswith("/4001")
    assert item["no_ai_notice"] == "禁止转载"
    assert item["local_files"] == ["images/NAI/77/4001.png"]


def test_update_manifest_itself_must_use_https(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(update_routes, "CONFIG", {"update_url": "http://example.test"})

    class ClientThatMustNotBeCalled:
        def get(self, *_args, **_kwargs):  # pragma: no cover - safety assertion
            raise AssertionError("HTTP manifest should be rejected before network access")

    with pytest.raises(HTTPException) as exc:
        update_routes._fetch_manifest(ClientThatMustNotBeCalled())
    assert exc.value.status_code == 400
    assert "HTTPS" in str(exc.value.detail)


def test_global_notice_and_compliance_navigation_are_wired():
    source = Path("web/shared/site-nav.js").read_text(encoding="utf-8")
    assert 'href: "/compliance"' in source
    # site-nav.js 通过统一 ApiClient 访问合规接口（same-origin 契约）
    assert '"/api/compliance/notice/status"' in source
    assert '"/api/compliance/notice/accept"' in source
    assert "window.ApiClient" in source
    assert "if (status.required)" in source

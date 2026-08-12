from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from gallery_maintenance import GalleryMaintenance


def _legacy_gallery(tmp_path: Path) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    original = data / "images" / "NAI" / "1" / "legacy.png"
    original.parent.mkdir(parents=True)
    Image.new("RGB", (80, 120), (20, 40, 60)).save(original)
    database = data / "aitag.db"
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE works(
              id INTEGER PRIMARY KEY, preview_path TEXT, preview_downloaded INTEGER
            );
            CREATE TABLE work_images(
              work_id INTEGER, page_index INTEGER, local_path TEXT, image_path TEXT,
              file_name TEXT, source_sha256 TEXT, source_page_index INTEGER,
              downloaded INTEGER
            );
            CREATE TABLE pixiv_nai_receipts(
              work_id INTEGER, display_page_index INTEGER, local_path TEXT,
              source_sha256 TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO works(id, preview_path, preview_downloaded) VALUES (1, ?, 1)",
            ("NAI/1/legacy.png",),
        )
        connection.execute(
            """INSERT INTO work_images(
                   work_id, page_index, local_path, image_path, file_name,
                   source_sha256, source_page_index, downloaded
               ) VALUES (1, 0, ?, ?, 'legacy.png', 'old', 0, 1)""",
            ("NAI/1/legacy.png", "NAI/1/legacy.png"),
        )
        connection.execute(
            """INSERT INTO pixiv_nai_receipts(
                   work_id, display_page_index, local_path, source_sha256
               ) VALUES (1, 0, ?, 'old')""",
            ("NAI/1/legacy.png",),
        )
        connection.commit()
    return data, database, original


def _stored_path(database: Path) -> str:
    with closing(sqlite3.connect(database)) as connection:
        return str(
            connection.execute(
                "SELECT local_path FROM work_images WHERE work_id=1 AND page_index=0"
            ).fetchone()[0]
        )


def test_migrate_keeps_new_webp_when_old_original_cleanup_fails(tmp_path: Path) -> None:
    data, database, original = _legacy_gallery(tmp_path)
    concrete_path_type = type(original)
    real_unlink = concrete_path_type.unlink

    def fail_only_original(path, *args, **kwargs):
        if Path(path) == original:
            raise PermissionError("original is locked")
        return real_unlink(path, *args, **kwargs)

    with patch.object(concrete_path_type, "unlink", autospec=True, side_effect=fail_only_original):
        receipt = GalleryMaintenance(data).migrate_originals_to_webp()

    webp = original.with_suffix(".webp")
    assert receipt["migrated"] == 1
    assert receipt["failed"] == 0
    assert receipt["cleanup_failed"] == 1
    assert original.is_file()
    assert webp.is_file()
    assert _stored_path(database) == "NAI/1/legacy.webp"


def test_migrate_db_failure_preserves_original_and_rolls_back_new_file(
    tmp_path: Path,
) -> None:
    data, database, original = _legacy_gallery(tmp_path)
    real_connect = sqlite3.connect
    first_read_connection = real_connect(database)

    with patch(
        "gallery_maintenance.sqlite3.connect",
        side_effect=[first_read_connection, sqlite3.OperationalError("database locked")],
    ):
        receipt = GalleryMaintenance(data).migrate_originals_to_webp()

    assert receipt["migrated"] == 0
    assert receipt["failed"] == 1
    assert original.is_file()
    assert not original.with_suffix(".webp").exists()
    assert _stored_path(database) == "NAI/1/legacy.png"

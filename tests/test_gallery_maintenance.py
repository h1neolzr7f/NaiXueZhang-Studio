from __future__ import annotations

import sqlite3
import json
from contextlib import closing
from pathlib import Path

from PIL import Image

from db import Database
from db_compression import compress_text
from gallery_maintenance import GalleryMaintenance


def test_gallery_maintenance_rebuilds_thumbnails_and_updates_gallery_preview(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    images = data / "images"
    original = images / "NAI" / "9" / "22_p0.png"
    original.parent.mkdir(parents=True)
    Image.new("RGB", (1200, 800), (12, 34, 56)).save(original)
    database = data / "aitag.db"
    db = Database(database)
    try:
        db.conn.execute(
            "INSERT INTO works(id, title, preview_path, preview_downloaded) "
            "VALUES (22, 'work', ?, 1)",
            ("NAI/9/22_p0.png",),
        )
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, local_path, downloaded) "
            "VALUES (22, 0, ?, 1)",
            ("NAI/9/22_p0.png",),
        )
        db.conn.commit()
    finally:
        db.close()

    receipt = GalleryMaintenance(data).rebuild_thumbnails()

    assert receipt["created"] == 1
    assert (images / "_thumbs" / "NAI" / "9" / "22_p0.webp").is_file()
    with closing(sqlite3.connect(database)) as connection:
        preview = connection.execute(
            "SELECT preview_path FROM works WHERE id=22"
        ).fetchone()[0]
    assert preview == "_thumbs/NAI/9/22_p0.webp"


def test_gallery_maintenance_rebuilds_nai_tag_index_for_existing_work(tmp_path: Path) -> None:
    data = tmp_path / "data"
    database = data / "aitag.db"
    db = Database(database)
    try:
        db.conn.execute("INSERT INTO works(id, ai_type) VALUES (23, 'NAI')")
        metadata = {
            "_local": {
                "parser_version": "v1",
                "parsed_nai_tags": [
                    {"text": "outdoors", "weight": 1, "raw_syntax": "outdoors", "syntax_type": "none"}
                ],
            }
        }
        db.conn.execute(
            "INSERT INTO work_images(work_id, page_index, ai_json, downloaded) VALUES (23, 0, ?, 1)",
            (compress_text(json.dumps(metadata)),),
        )
        db.conn.commit()
    finally:
        db.close()

    receipt = GalleryMaintenance(data).rebuild_nai_tag_index()

    assert receipt == {"works": 1}

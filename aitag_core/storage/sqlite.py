from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from db_compression import decompress_if_needed
from paths import data_dir


class ImageMetadataStore:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or (data_dir() / "aitag.db")
        self._local = threading.local()

    def connection(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(
                self.db_path, check_same_thread=False, timeout=30.0
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            self._local.conn = conn
        return conn

    def load_image_json(self, work_id: int, page_index: int = 0) -> dict:
        page = int(page_index or 0)
        row = self.connection().execute(
            "SELECT ai_json, page_index FROM work_images WHERE work_id = ? "
            "AND page_index = ?",
            (int(work_id), page),
        ).fetchone()
        if row is None:
            # Fallback: ordered list only when exact page missing — do not
            # silently remap out-of-range indices to page 0.
            rows = self.connection().execute(
                "SELECT ai_json, page_index FROM work_images WHERE work_id = ? "
                "ORDER BY page_index ASC",
                (int(work_id),),
            ).fetchall()
            if not rows:
                raise ValueError(f"作品 {work_id} 无图片元数据")
            available = [int(r["page_index"]) for r in rows]
            raise ValueError(
                f"作品 {work_id} 没有 page_index={page} 的元数据"
                f"（可用: {available}）"
            )
        raw = row["ai_json"]
        if not raw:
            raise ValueError("该图无 AI 元数据")
        return json.loads(decompress_if_needed(raw))


_DEFAULT_STORE = ImageMetadataStore()


def load_image_json(work_id: int, page_index: int = 0) -> dict:
    return _DEFAULT_STORE.load_image_json(work_id, page_index)


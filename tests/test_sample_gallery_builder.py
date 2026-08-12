from __future__ import annotations

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from db import Database


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "build_sample_gallery.py"
SPEC = importlib.util.spec_from_file_location("build_sample_gallery", SCRIPT)
assert SPEC and SPEC.loader
sample_builder = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sample_builder)


class SampleGalleryBuilderTests(unittest.TestCase):
    def test_normalizes_direct_intake_paths_without_admitting_other_model_roots(self) -> None:
        self.assertEqual(
            sample_builder._normalize_image_relative("NAI/42/work_p0.png"),
            "images/NAI/42/work_p0.png",
        )
        self.assertEqual(
            sample_builder._normalize_image_relative("images/NAI/42/work_p0.png"),
            "images/NAI/42/work_p0.png",
        )
        self.assertIsNone(sample_builder._normalize_image_relative("SD/42/work_p0.png"))
        self.assertIsNone(sample_builder._normalize_image_relative("../NAI/42/work_p0.png"))

    def test_builds_consistent_sample_and_excludes_private_or_unsafe_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_data = root / "source" / "data"
            source_images = source_data / "images" / "safe"
            source_images.mkdir(parents=True)
            safe_image = source_images / "one.webp"
            safe_image.write_bytes(b"safe-image" * 128)
            adult_image = source_images / "adult.webp"
            adult_image.write_bytes(b"adult-image" * 128)
            outside = root / "outside.webp"
            outside.write_bytes(b"must-not-copy")

            source_db = source_data / "aitag.db"
            database = Database(source_db)
            try:
                database.conn.executemany(
                    """
                    INSERT INTO works(
                        id, title, caption, tags, ai_type, create_date,
                        image_count, preview_path, preview_downloaded, list_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    [
                        (1, "Safe landscape", "blue sky", "scenery", "NAI", "2026-01-03", 1, "images/safe/one.webp", '{"source":"pixiv-direct"}'),
                        (2, "R-18 adult", "blocked", "nsfw", "NAI", "2026-01-02", 1, "images/safe/adult.webp", '{"source":"pixiv-direct"}'),
                        (3, "Path escape", "unsafe", "scenery", "NAI", "2026-01-01", 1, "images/../../outside.webp", '{"source":"pixiv-direct"}'),
                    ],
                )
                database.conn.executemany(
                    """
                    INSERT INTO work_images(
                        work_id, page_index, local_path, downloaded, prompt_text
                    ) VALUES (?, 0, ?, 1, ?)
                    """,
                    [
                        (1, "images/safe/one.webp", "blue sky landscape"),
                        (2, "images/safe/adult.webp", "blocked"),
                        (3, "images/../../outside.webp", "unsafe"),
                    ],
                )
                database.conn.execute(
                    "INSERT INTO crawl_state(key, value) VALUES ('last_search_page', '999')"
                )
                database.conn.commit()
            finally:
                database.close()

            output_data = root / "release" / "data"
            manifest = sample_builder.build_sample_gallery(
                source_db,
                source_data,
                output_data,
                target_bytes=512,
                minimum_bytes=1,
            )

            self.assertEqual(manifest["work_ids"], [1])
            self.assertTrue((output_data / "images" / "safe" / "one.webp").is_file())
            self.assertFalse((output_data / "outside.webp").exists())
            self.assertFalse((output_data / "images" / "safe" / "adult.webp").exists())

            connection = sqlite3.connect(output_data / "aitag.db")
            try:
                self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(connection.execute("SELECT id FROM works").fetchall(), [(1,)])
                self.assertEqual(
                    connection.execute("SELECT work_id FROM works_fts WHERE works_fts MATCH 'landscape'").fetchall(),
                    [(1,)],
                )
                states = dict(connection.execute("SELECT key, value FROM crawl_state"))
                self.assertNotIn("last_search_page", states)
                self.assertEqual(states["release_sample"], "1")
            finally:
                connection.close()

    def test_candidates_require_pixiv_direct_nai_rows(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE works(
                id INTEGER PRIMARY KEY, title TEXT, caption TEXT, tags TEXT,
                ai_type TEXT, create_date TEXT, image_count INTEGER,
                preview_path TEXT, list_json TEXT
            );
            CREATE TABLE work_images(
                work_id INTEGER, page_index INTEGER, local_path TEXT,
                downloaded INTEGER
            );
            INSERT INTO works VALUES
                (1, 'keep', '', '', 'NAI', '2026', 1, 'images/1.webp', '{"source":"pixiv-direct"}'),
                (2, 'foreign model', '', '', 'SD', '2026', 1, 'images/2.webp', '{"source":"pixiv-direct"}'),
                (3, 'old upstream', '', '', 'NAI', '2026', 1, 'images/3.webp', '{"source":"aitag-mirror"}');
            INSERT INTO work_images VALUES
                (1, 0, 'images/1.webp', 1),
                (2, 0, 'images/2.webp', 1),
                (3, 0, 'images/3.webp', 1);
            """
        )
        files = {
            f"images/{work_id}.webp": (Path(f"C:/fixture/{work_id}.webp"), 10)
            for work_id in (1, 2, 3)
        }
        groups = sample_builder._candidate_groups(
            connection, files, max_work_bytes=100
        )
        self.assertEqual([group["work"]["id"] for group in groups], [1])
        connection.close()

    def test_explicit_unfiltered_mode_keeps_provenance_gate_but_allows_test_content(self) -> None:
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE works(
                id INTEGER PRIMARY KEY, title TEXT, caption TEXT, tags TEXT,
                ai_type TEXT, create_date TEXT, image_count INTEGER,
                preview_path TEXT, list_json TEXT
            );
            CREATE TABLE work_images(
                work_id INTEGER, page_index INTEGER, local_path TEXT,
                downloaded INTEGER
            );
            INSERT INTO works VALUES
                (1, 'R-18 test', '', 'nsfw', 'NAI', '2026', 1, 'NAI/1.webp', '{"source":"pixiv-direct"}'),
                (2, 'wrong source', '', '', 'NAI', '2026', 1, 'NAI/2.webp', '{"source":"legacy"}');
            INSERT INTO work_images VALUES
                (1, 0, 'NAI/1.webp', 1),
                (2, 0, 'NAI/2.webp', 1);
            """
        )
        files = {
            f"images/NAI/{work_id}.webp": (Path(f"C:/fixture/{work_id}.webp"), 10)
            for work_id in (1, 2)
        }
        groups = sample_builder._candidate_groups(
            connection,
            files,
            max_work_bytes=100,
            allow_unfiltered_content=True,
        )
        self.assertEqual([group["work"]["id"] for group in groups], [1])
        connection.close()

    def test_refuses_to_overwrite_nonempty_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source_data = root / "source" / "data"
            source_data.mkdir(parents=True)
            source_db = source_data / "aitag.db"
            database = Database(source_db)
            database.close()
            output_data = root / "release" / "data"
            (output_data / "images").mkdir(parents=True)
            (output_data / "images" / "keep.txt").write_text("keep", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                sample_builder.build_sample_gallery(
                    source_db,
                    source_data,
                    output_data,
                    target_bytes=1,
                    minimum_bytes=1,
                )


if __name__ == "__main__":
    unittest.main()

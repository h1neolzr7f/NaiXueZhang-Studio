from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from db import Database


class PromptSearchIndexTests(unittest.TestCase):
    def test_per_work_prompt_index_preserves_results_without_image_row_fanout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with Database(Path(temp) / "gallery.sqlite") as db:
                work = {
                    "id": 42,
                    "title": "fixture",
                    "AI_type": "NAI",
                    "create_date": "2026-07-27T00:00:00",
                }
                db.conn.execute(
                    """
                    INSERT INTO works(id, title, ai_type, create_date, list_json)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (42, "fixture", "NAI", work["create_date"], json.dumps(work)),
                )
                db.conn.executemany(
                    """
                    INSERT INTO work_images(work_id, page_index, prompt_text, downloaded)
                    VALUES(?, ?, ?, 1)
                    """,
                    [
                        (42, 0, "1girl, penance_(arknights)"),
                        (42, 1, "1girl, outdoors"),
                    ],
                )
                db._sync_prompt_fts(42)
                db.conn.commit()

                before = db.search_works(prompt="1girl", local_scope="local")
                self.assertEqual([row["id"] for row in before["items"]], [42])
                self.assertEqual(db.prompt_search_table(), "prompt_fts")

                self.assertEqual(db.rebuild_prompt_work_fts(), 1)
                after = db.search_works(prompt="1girl", local_scope="local")

                self.assertEqual([row["id"] for row in after["items"]], [42])
                self.assertEqual(db.prompt_search_table(), "prompt_work_fts")
                self.assertEqual(
                    db.conn.execute("SELECT COUNT(*) FROM prompt_work_fts").fetchone()[0],
                    1,
                )


if __name__ == "__main__":
    unittest.main()

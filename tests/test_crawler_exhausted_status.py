from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from db import Database
from crawler_watchdog import CrawlerWatchdog
from routes import crawler as crawler_routes


class CrawlerExhaustedStatusTests(unittest.TestCase):
    def test_database_requeues_only_exhausted_previews_with_a_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "crawler.db")
            try:
                db.conn.executemany(
                    """
                    INSERT INTO works(
                        id, list_json, detail_json, preview_downloaded,
                        preview_attempts, total_bookmarks
                    ) VALUES (?, '{}', '{}', 0, ?, ?)
                    """,
                    [(1, 6, 30), (2, 8, 20), (3, 2, 10)],
                )
                db.conn.commit()

                work_ids = db.requeue_exhausted_previews(max_attempts=6, limit=1)

                self.assertEqual(work_ids, [1])
                attempts = {
                    int(row["id"]): int(row["preview_attempts"])
                    for row in db.conn.execute(
                        "SELECT id, preview_attempts FROM works ORDER BY id"
                    )
                }
                self.assertEqual(attempts, {1: 0, 2: 8, 3: 2})
            finally:
                db.close()

    def test_watchdog_message_never_calls_exhausted_items_fully_complete(self) -> None:
        message = CrawlerWatchdog._status_message(
            False,
            False,
            "auto_complete",
            preview_exhausted=700,
        )
        self.assertIn("700", message)
        self.assertIn("耗尽", message)
        self.assertNotEqual(message, "任务已完成，守护无需继续")

    def test_retry_route_can_requeue_and_restart_only_after_user_action(self) -> None:
        with patch.object(
            crawler_routes,
            "requeue_exhausted_previews",
            return_value={"ok": True, "requeued": 12, "message": "已重新入队"},
        ), patch.object(
            crawler_routes, "restart_crawler", return_value={"ok": True}
        ) as restart, patch.object(
            crawler_routes, "multi_crawler_status", return_value={}
        ):
            result = crawler_routes.api_crawler_retry_exhausted(
                {"limit": 12, "restart": True}
            )

        self.assertEqual(result["requeued"], 12)
        restart.assert_called_once()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import asyncio
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import crawler_watchdog
from crawler import (
    Crawler,
    preview_local_path,
    validate_preview_bytes,
    write_heartbeat,
)


_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUB"
    "AScY42YAAAAASUVORK5CYII="
)


def _config(data_dir: Path) -> dict:
    return {
        "base_url": "http://aitag.test",
        "cdn_url": "http://cdn.test/",
        "search_query": "-NAI_X NAI 明日方舟",
        "search_sort": "new",
        "search_time_range": "all",
        "search_max_pages": 0,
        "search_batch_pages": 8,
        "data_dir": str(data_dir),
        "user_agent": "aitag-reliability-test",
        "request_delay_sec": 0,
        "concurrent_workers": 1,
        "max_concurrent_workers": 1,
        "preview_workers": 1,
        "preview_request_delay_sec": 0,
        "preview_max_attempts": 2,
        "preview_mode": "cover_only",
        "preview_all_local": False,
        "page_size": 60,
    }


class _DisconnectThenSuccessClient:
    def __init__(self) -> None:
        self.calls = 0

    def request(self, method, url, params=None):
        self.calls += 1
        request = httpx.Request(method, url, params=params)
        if self.calls == 1:
            raise httpx.RemoteProtocolError(
                "peer closed incomplete chunked response",
                request=request,
            )
        return httpx.Response(200, request=request, json={"ok": True})


class _JsonClient:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def request(self, method, url, params=None):
        request = httpx.Request(method, url, params=params)
        return httpx.Response(200, request=request, json=self.payload)


class _JsonSequenceClient:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = list(payloads)

    def request(self, method, url, params=None):
        request = httpx.Request(method, url, params=params)
        return httpx.Response(200, request=request, json=self.payloads.pop(0))


class _PreviewFallbackClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, method, url):
        request = httpx.Request(method, url)
        if "fallback_p1" in url:
            return httpx.Response(
                200,
                request=request,
                content=_ONE_PIXEL_PNG,
                headers={"content-type": "image/png"},
            )
        return httpx.Response(404, request=request)


class _DetailClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, method, url, params=None):
        request = httpx.Request(method, url, params=params)
        work_id = int(url.rsplit("/", 1)[-1])
        return httpx.Response(
            200,
            request=request,
            json={"work": {"id": work_id, "title": f"work-{work_id}"}, "images": []},
        )


class CrawlerReliabilityTests(unittest.TestCase):
    def test_preview_path_rejects_remote_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "data" / "images"
            with self.assertRaisesRegex(ValueError, "unsafe preview"):
                preview_local_path(
                    images,
                    {
                        "image_type": "../../outside",
                        "author_id": "123",
                        "file_name": "payload.py",
                    },
                )
            self.assertFalse((root / "outside").exists())

    def test_preview_path_rejects_absolute_and_separator_components(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            images = Path(tmp) / "images"
            attacks = [
                {"image_type": "NAI", "author_id": "..", "file_name": "a.webp"},
                {"image_type": "NAI", "author_id": "123", "file_name": "../x.py"},
                {"image_type": "NAI", "author_id": "123", "file_name": r"C:\x.png"},
            ]
            for image in attacks:
                with self.subTest(image=image), self.assertRaises(ValueError):
                    preview_local_path(images, image)

    def test_preview_response_must_be_a_real_bounded_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "not an image"):
            validate_preview_bytes(b"<html>nope</html>", content_type="text/html")
        with self.assertRaisesRegex(ValueError, "exceeds"):
            validate_preview_bytes(_ONE_PIXEL_PNG, max_bytes=8)
        validate_preview_bytes(_ONE_PIXEL_PNG, content_type="image/png")

    def test_parallel_wave_does_not_starve_preview_only_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            crawler.db.upsert_list_item(
                {
                    "id": 77,
                    "AI_type": "NAI",
                    "title": "Arknights preview-only",
                    "tags": json.dumps(["Arknights"]),
                },
                "2026-07-27T00:00:00",
            )
            crawler.db.conn.commit()
            crawler.db.save_detail(
                77,
                {
                    "work": {
                        "id": 77,
                        "title": "Arknights preview-only",
                        "image_count": 1,
                    },
                    "images": [
                        {
                            "id": 770,
                            "image_type": "NAI",
                            "author_id": 9,
                            "file_name": "fallback_p1.webp",
                            "image_path": "",
                        }
                    ],
                },
                None,
                False,
                "2026-07-27T00:00:01",
            )
            try:
                with patch(
                    "crawler.httpx.AsyncClient",
                    return_value=_PreviewFallbackClient(),
                ):
                    asyncio.run(
                        crawler.crawl_parallel_async(max_detail_batches=1)
                    )
                self.assertTrue(crawler.db.has_preview(77))
            finally:
                crawler.db.close()

    def test_detail_wave_stops_at_configured_batch_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            for work_id in range(1, 25):
                crawler.db.upsert_list_item(
                    {
                        "id": work_id,
                        "AI_type": "NAI",
                        "title": "Arknights",
                        "tags": "[]",
                    },
                    "2026-07-27T00:00:00",
                )
            crawler.db.conn.commit()
            try:
                with patch("crawler.httpx.AsyncClient", return_value=_DetailClient()):
                    asyncio.run(crawler.crawl_details_async(max_batches=2))
                self.assertEqual(crawler.db.count_details(), 16)
                self.assertEqual(crawler.db.count_pending_details(), 8)
            finally:
                crawler.db.close()

    def test_search_budget_respects_detail_queue_high_watermark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "data")
            config.update(
                {
                    "search_batch_pages": 4,
                    "detail_queue_high_watermark": 120,
                }
            )
            crawler = Crawler(config)
            try:
                self.assertEqual(crawler._search_page_budget(4, 120), 0)
                self.assertEqual(crawler._search_page_budget(4, 70), 1)
                self.assertEqual(crawler._search_page_budget(4, 0), 2)
            finally:
                crawler.db.close()

    def test_adaptive_detail_growth_uses_clean_streak_and_retry_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "data")
            config.update(
                {
                    "concurrent_workers": 2,
                    "max_concurrent_workers": 4,
                    "parallel_max_detail_workers": 3,
                    "adaptive_growth_clean_batches": 2,
                    "adaptive_latency_target_sec": 10,
                    "request_delay_sec": 0.8,
                    "min_request_delay_sec": 0.45,
                }
            )
            crawler = Crawler(config)
            try:
                crawler._tune_pace(
                    0,
                    16,
                    retry_count=0,
                    p95_latency=2.0,
                )
                self.assertEqual(crawler.workers, 2)
                crawler._tune_pace(
                    0,
                    16,
                    retry_count=0,
                    p95_latency=2.0,
                )
                self.assertEqual(crawler.workers, 3)

                delay_before_retry = crawler.delay
                crawler._tune_pace(
                    0,
                    16,
                    retry_count=1,
                    p95_latency=2.0,
                )
                self.assertEqual(crawler.workers, 2)
                self.assertGreater(crawler.delay, delay_before_retry)
            finally:
                crawler.db.close()

    def test_detail_batch_transaction_rolls_back_as_one_unit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            for work_id in (1, 2):
                crawler.db.upsert_list_item(
                    {
                        "id": work_id,
                        "AI_type": "NAI",
                        "title": "Arknights",
                        "tags": "[]",
                    },
                    "2026-07-27T00:00:00",
                )
            crawler.db.conn.commit()
            details = [
                (
                    work_id,
                    {"work": {"id": work_id, "title": f"work-{work_id}"}, "images": []},
                    None,
                    False,
                    "2026-07-27T00:00:01",
                )
                for work_id in (1, 2)
            ]
            original = crawler.db._save_detail_impl

            def fail_second(*args, **kwargs):
                if args[0] == 2:
                    raise RuntimeError("injected batch write failure")
                return original(*args, **kwargs)

            try:
                with patch.object(
                    crawler.db,
                    "_save_detail_impl",
                    side_effect=fail_second,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "injected batch write failure",
                    ):
                        crawler.db.save_details_batch(details)
                self.assertEqual(crawler.db.count_details(), 0)

                crawler.db.save_details_batch(details)
                self.assertEqual(crawler.db.count_details(), 2)
            finally:
                crawler.db.close()

    def test_detail_batch_uses_one_fts_sync_per_index_and_keeps_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            details = []
            for work_id in (1, 2, 3):
                crawler.db.upsert_list_item(
                    {
                        "id": work_id,
                        "AI_type": "NAI",
                        "title": f"list-{work_id}",
                        "tags": '["arknights"]',
                    },
                    "2026-07-27T00:00:00",
                )
                details.append(
                    (
                        work_id,
                        {
                            "work": {
                                "id": work_id,
                                "title": f"detail-{work_id}",
                                "caption": "cinematic",
                                "tags": '["arknights"]',
                                "AI_type": "NAI",
                            },
                            "images": [
                                {
                                    "id": 1000 + work_id,
                                    "prompt_text": f"1girl, operator-{work_id}",
                                }
                            ],
                        },
                        None,
                        False,
                        "2026-07-27T00:00:01",
                    )
                )
            crawler.db.conn.commit()

            try:
                with (
                    patch.object(
                        crawler.db,
                        "_sync_work_fts",
                        wraps=crawler.db._sync_work_fts,
                    ) as single_work_sync,
                    patch.object(
                        crawler.db,
                        "_sync_prompt_fts",
                        wraps=crawler.db._sync_prompt_fts,
                    ) as single_prompt_sync,
                    patch.object(
                        crawler.db,
                        "_sync_work_fts_batch",
                        wraps=crawler.db._sync_work_fts_batch,
                    ) as batch_work_sync,
                    patch.object(
                        crawler.db,
                        "_sync_prompt_fts_batch",
                        wraps=crawler.db._sync_prompt_fts_batch,
                    ) as batch_prompt_sync,
                ):
                    self.assertEqual(crawler.db.save_details_batch(details), 3)

                self.assertEqual(single_work_sync.call_count, 0)
                self.assertEqual(single_prompt_sync.call_count, 0)
                self.assertEqual(batch_work_sync.call_count, 1)
                self.assertEqual(batch_prompt_sync.call_count, 1)
                self.assertEqual(
                    crawler.db.conn.execute(
                        "SELECT COUNT(*) AS c FROM works_fts"
                    ).fetchone()["c"],
                    3,
                )
                self.assertEqual(
                    crawler.db.conn.execute(
                        "SELECT COUNT(*) AS c FROM prompt_fts"
                    ).fetchone()["c"],
                    3,
                )
                self.assertEqual(
                    crawler.db.conn.execute(
                        "SELECT COUNT(*) AS c FROM prompt_work_fts"
                    ).fetchone()["c"],
                    3,
                )
            finally:
                crawler.db.close()

    def test_detail_writer_does_not_block_the_async_preview_loop(self) -> None:
        class SlowWriter:
            def save_details_batch(self, _entries) -> int:
                time.sleep(0.08)
                events.append("write")
                return 1

        events: list[str] = []

        async def scenario() -> int:
            async def preview_tick() -> None:
                await asyncio.sleep(0.01)
                events.append("preview")

            tick = asyncio.create_task(preview_tick())
            saved = await Crawler._save_detail_entries_async(
                SlowWriter(),
                [(1, {}, None, False, "now")],
            )
            await tick
            return saved

        self.assertEqual(asyncio.run(scenario()), 1)
        self.assertEqual(events, ["preview", "write"])

    def test_site_crawler_wal_policy_is_configurable_and_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "data")
            config["crawler_wal_autocheckpoint_pages"] = 2048
            config["crawler_wal_journal_limit_mb"] = 32
            crawler = Crawler(config)
            try:
                auto_pages = crawler.db.conn.execute(
                    "PRAGMA wal_autocheckpoint"
                ).fetchone()[0]
                journal_limit = crawler.db.conn.execute(
                    "PRAGMA journal_size_limit"
                ).fetchone()[0]
                self.assertEqual(auto_pages, 2048)
                self.assertEqual(journal_limit, 32 * 1024 * 1024)
            finally:
                crawler.db.close()

    def test_search_page_batch_avoids_per_item_fts_and_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            items = [
                {
                    "id": work_id,
                    "AI_type": "NAI",
                    "title": f"search-{work_id}",
                    "tags": '["arknights"]',
                }
                for work_id in range(1, 61)
            ]
            try:
                with (
                    patch.object(
                        crawler.db,
                        "_sync_work_fts",
                        wraps=crawler.db._sync_work_fts,
                    ) as single_sync,
                    patch.object(
                        crawler.db,
                        "_sync_work_fts_batch",
                        wraps=crawler.db._sync_work_fts_batch,
                    ) as batch_sync,
                ):
                    result = crawler.db.upsert_list_items_batch(
                        items,
                        "2026-07-27T00:00:00",
                    )

                self.assertEqual(result["kept"], 60)
                self.assertEqual(result["already_complete"], 0)
                self.assertEqual(single_sync.call_count, 0)
                self.assertEqual(batch_sync.call_count, 1)
                self.assertEqual(crawler.db.count_works(), 60)

                original = crawler.db._upsert_list_item_impl

                def fail_second(item, *args, **kwargs):
                    if int(item["id"]) == 62:
                        raise RuntimeError("injected search page write failure")
                    return original(item, *args, **kwargs)

                with patch.object(
                    crawler.db,
                    "_upsert_list_item_impl",
                    side_effect=fail_second,
                ):
                    with self.assertRaisesRegex(
                        RuntimeError,
                        "injected search page write failure",
                    ):
                        crawler.db.upsert_list_items_batch(
                            [
                                {"id": 61, "AI_type": "NAI"},
                                {"id": 62, "AI_type": "NAI"},
                            ],
                            "2026-07-27T00:00:01",
                        )
                self.assertEqual(crawler.db.count_works(), 60)
            finally:
                crawler.db.close()

    def test_cover_only_falls_back_to_another_page_in_same_work(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            detail = {
                "work": {"id": 123, "title": "fallback-cover", "image_count": 2},
                "images": [
                    {
                        "id": 100,
                        "image_type": "NAI",
                        "author_id": 9,
                        "file_name": "primary_p0.webp",
                        "image_path": "",
                    },
                    {
                        "id": 101,
                        "image_type": "NAI",
                        "author_id": 9,
                        "file_name": "fallback_p1.webp",
                        "image_path": "",
                    },
                ],
            }
            crawler.db.upsert_list_item(
                {
                    "id": 123,
                    "AI_type": "NAI",
                    "title": "fallback-cover",
                    "tags": "[]",
                },
                "2026-07-27T00:00:00",
            )
            crawler.db.conn.commit()
            crawler.db.save_detail(
                123,
                detail,
                None,
                False,
                "2026-07-27T00:00:01",
            )
            try:
                ok = asyncio.run(
                    crawler._fetch_cover_impl(
                        _PreviewFallbackClient(),
                        123,
                        cover_only=True,
                    )
                )
                row = crawler.db.conn.execute(
                    """
                    SELECT preview_downloaded, preview_path
                    FROM works WHERE id = 123
                    """
                ).fetchone()
                fallback = crawler.db.conn.execute(
                    """
                    SELECT downloaded, local_path
                    FROM work_images
                    WHERE work_id = 123 AND page_index = 1
                    """
                ).fetchone()
            finally:
                crawler.db.close()

        self.assertTrue(ok)
        self.assertEqual(row["preview_downloaded"], 1)
        self.assertIn("fallback_p1", row["preview_path"])
        self.assertEqual(fallback["downloaded"], 1)

    def test_windows_heartbeat_replace_denial_never_crashes_crawler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            Path,
            "replace",
            side_effect=PermissionError(5, "access denied"),
        ):
            root = Path(tmp)
            write_heartbeat(root, "search", "running", "still alive", page=7)
            payload = json.loads(
                (root / "logs" / "crawler-heartbeat.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(payload["page"], 7)
        self.assertEqual(payload["status"], "running")

    def test_remote_protocol_disconnect_is_retried(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            client = _DisconnectThenSuccessClient()
            try:
                response = crawler._request_json_sync(
                    client,
                    "GET",
                    "http://aitag.test/api/search",
                    retries=2,
                )
            finally:
                crawler.db.close()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.calls, 2)

    def test_search_refreshes_stale_total_pages_before_marking_done(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            crawler.db.set_state("search_page", "2")
            crawler.db.set_state("search_total_pages", "2")
            crawler.db.set_state("search_total", "120")
            items = [
                {
                    "id": 10_000 + index,
                    "AI_type": "NAI",
                    "title": "明日方舟",
                    "tags": json.dumps(["明日方舟"]),
                }
                for index in range(60)
            ]
            try:
                crawler.crawl_search_pages(
                    _JsonClient({"items": items, "total": 180}),
                    page_budget=1,
                )
                self.assertEqual(crawler.db.get_state("search_done", "0"), "0")
                self.assertEqual(crawler.db.get_state("search_total_pages"), "3")
                self.assertEqual(crawler.db.get_state("search_page"), "3")
            finally:
                crawler.db.close()

    def test_fresh_incremental_scan_stops_after_consecutive_known_pages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = _config(Path(tmp) / "data")
            config["search_stop_after_known_pages"] = 2
            crawler = Crawler(config)
            pages = []
            for page in range(2):
                items = [
                    {
                        "id": page * 100 + index + 1,
                        "AI_type": "NAI",
                        "title": "明日方舟",
                        "tags": json.dumps(["明日方舟"]),
                    }
                    for index in range(60)
                ]
                pages.append({"items": items, "total": 600})
                for item in items:
                    crawler.db.conn.execute(
                        """
                        INSERT INTO works(
                            id, title, tags, list_json, detail_json
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            item["id"],
                            item["title"],
                            item["tags"],
                            json.dumps(item, ensure_ascii=False),
                            json.dumps({"work": item, "images": []}),
                        ),
                    )
            crawler.db.conn.commit()
            try:
                crawler.crawl_search_pages(_JsonSequenceClient(pages))
                self.assertEqual(crawler.db.get_state("search_done"), "1")
                self.assertEqual(crawler.db.get_state("search_page"), "3")
                self.assertEqual(
                    crawler.db.get_state("search_completion_reason"),
                    "known_pages:2",
                )
            finally:
                crawler.db.close()

    def test_nonempty_total_with_empty_page_keeps_same_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            crawler.db.set_state("search_page", "5")
            crawler.db.set_state("search_total_pages", "10")
            try:
                waiting = crawler.crawl_search_pages(
                    _JsonClient({"items": [], "total": 600}),
                    page_budget=1,
                )
                self.assertTrue(waiting)
                self.assertEqual(crawler.db.get_state("search_page"), "5")
                self.assertEqual(crawler.db.get_state("search_done", "0"), "0")
            finally:
                crawler.db.close()

    def test_preview_wait_uses_current_gallery_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crawler = Crawler(_config(Path(tmp) / "data"))
            crawler.db.conn.execute(
                """
                INSERT INTO works(id, title, tags, list_json, detail_json)
                VALUES (1, 'unrelated work', '[]', ?, NULL)
                """,
                (json.dumps({"id": 1, "AI_type": "NAI"}),),
            )
            crawler.db.conn.commit()
            try:
                asyncio.run(
                    asyncio.wait_for(
                        crawler.crawl_previews_async(wait_for_details=True),
                        timeout=0.5,
                    )
                )
            finally:
                crawler.db.close()

    def test_watchdog_restarts_live_process_with_stale_heartbeat(self) -> None:
        with patch.object(
            crawler_watchdog, "crawl_work_remaining", return_value=True
        ), patch.object(
            crawler_watchdog, "crawler_running", return_value=True
        ), patch.object(
            crawler_watchdog,
            "_read_heartbeat",
            return_value={"status": "running"},
        ), patch.object(
            crawler_watchdog, "_heartbeat_age_sec", return_value=999.0
        ), patch.object(
            crawler_watchdog,
            "restart_crawler",
            return_value={"ok": True, "crawler_running": True},
        ) as restart:
            result = crawler_watchdog.ensure_crawler_running(reason="test")

        self.assertEqual(result["action"], "restarted")
        restart.assert_called_once()

    def test_supervisor_pins_project_virtualenv_python(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "run_crawl_background.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn(".venv\\Scripts\\python.exe", script)
        self.assertIn("& $pythonExe", script)
        self.assertIn("$restartDelaySec", script)
        self.assertIn("$maxRestartDelaySec", script)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from tests.asgi_client import TestClient

import server
import nai_director


ROOT = Path(__file__).resolve().parents[1]


class DirectorRoutesAndUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.app)

    def test_director_is_a_standalone_desktop_page(self) -> None:
        response = self.client.get("/director")
        html = response.text

        self.assertEqual(response.status_code, 200)
        self.assertIn('id="directorSourceGrid"', html)
        self.assertIn('id="directorRecipe"', html)
        self.assertIn('id="directorTaskPanel"', html)
        self.assertIn("批量导演", html)
        self.assertIn('data-pick-mode="series"', html)
        self.assertIn('data-pick-mode="single"', html)
        self.assertNotIn("viewport-fit=cover", html)

    def test_generated_series_picker_lists_summaries_then_expands_exactly(self) -> None:
        summaries = [{
            "group_id": "qqgroup:42",
            "work_id": 42,
            "source_gallery_id": "qqgroup",
            "cover_id": "20260722_120001",
            "cover_url": "/data/generated/20260722_120001.png",
            "cover_thumb": "/data/generated/thumbs/20260722_120001.webp",
            "latest_at": "2026-07-22T12:00:02",
            "count": 2,
        }]
        group = {
            "group_id": "qqgroup:42",
            "work_id": 42,
            "source_gallery_id": "qqgroup",
            "cover_url": "/data/generated/20260722_120001.png",
            "cover_thumb": "/data/generated/thumbs/20260722_120001.webp",
            "latest_at": "2026-07-22T12:00:02",
            "items": [
                {"id": "20260722_120001", "image_url": "/data/generated/20260722_120001.png"},
                {"id": "20260722_120002", "image_url": "/data/generated/20260722_120002.png"},
            ],
        }
        with patch.object(nai_director, "list_groups", return_value=summaries), patch.object(
            nai_director, "get_group", return_value=group
        ) as get_group:
            result = nai_director.list_director_sources(kind="generated", mode="series")
            expanded = nai_director.get_director_source_group("qqgroup:42")

        self.assertEqual(result["mode"], "series")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["kind"], "generated_group")
        self.assertEqual(result["items"][0]["count"], 2)
        self.assertNotIn("items", result["items"][0])
        self.assertEqual(
            [row["source_id"] for row in expanded["source"]["items"]],
            ["generated:20260722_120001", "generated:20260722_120002"],
        )
        get_group.assert_called_once_with("qqgroup:42", rescan_if_missing=False)

    def test_source_group_route_returns_exact_items_on_demand(self) -> None:
        expanded = {
            "ok": True,
            "source": {
                "group_id": "qqgroup:42",
                "items": [{"source_id": "generated:one"}],
            },
        }
        with patch("routes.director.get_director_source_group", return_value=expanded) as loader:
            response = self.client.get("/api/director/source-groups/qqgroup%3A42")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"]["items"][0]["source_id"], "generated:one")
        loader.assert_called_once_with("qqgroup:42")

    def test_source_route_forwards_series_mode(self) -> None:
        expected = {"ok": True, "kind": "generated", "mode": "series", "items": [], "total": 0}
        with patch("routes.director.list_director_sources", return_value=expected) as listing:
            response = self.client.get("/api/director/sources?kind=generated&mode=series&page=2&page_size=12")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mode"], "series")
        listing.assert_called_once_with(
            kind="generated", mode="series", q="", gallery_id="site", page=2, page_size=12
        )

    def test_site_navigation_lists_director_as_its_own_function(self) -> None:
        script = (ROOT / "web" / "shared" / "site-nav.js").read_text(encoding="utf-8")

        self.assertIn('{ href: "/director", id: "director"', script)
        self.assertIn('p.startsWith("/director")', script)

    def test_catalog_and_preview_routes_do_not_execute_provider(self) -> None:
        catalog = self.client.get("/api/director/catalog")
        preview_payload = {
            "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
            "recipe": {"tool": "sketch"},
        }
        fake_preview = {
            "ok": True,
            "source_count": 1,
            "estimated_outputs": 1,
            "zero_provider_calls": True,
        }
        with patch("routes.director.preview_director_batch", return_value=fake_preview) as preview:
            response = self.client.post("/api/director/preview", json=preview_payload)

        self.assertEqual(catalog.status_code, 200)
        self.assertEqual(len(catalog.json()["tools"]), 6)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["zero_provider_calls"])
        preview.assert_called_once_with(preview_payload["sources"], preview_payload["recipe"])

    def test_run_cancel_retry_use_stable_task_ids(self) -> None:
        started = {"ok": True, "task_id": "director-1", "batch": {"status": "running"}}
        with patch("routes.director.start_director_batch", return_value=started) as start:
            response = self.client.post(
                "/api/director/jobs",
                json={
                    "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                    "recipe": {"tool": "line_art"},
                    "confirmed": True,
                    "preview_id": "server-preview",
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], "director-1")
        start.assert_called_once()

        with patch("routes.director.cancel_director_batch", return_value={"ok": True, "task_id": "director-1"}) as cancel:
            stopped = self.client.post("/api/director/jobs/director-1/cancel")
        self.assertEqual(stopped.status_code, 200)
        cancel.assert_called_once_with("director-1")

        with patch(
            "routes.director.preview_director_retry",
            return_value={"ok": True, "preview_id": "retry-preview", "retry_source_count": 1},
        ):
            retry_preview = self.client.post("/api/director/jobs/director-1/retry/preview")
        self.assertEqual(retry_preview.status_code, 200)

        with patch("routes.director.retry_director_batch", return_value={"ok": True, "task_id": "director-2"}) as retry:
            retried = self.client.post(
                "/api/director/jobs/director-1/retry",
                json={"confirmed": True, "preview_id": "retry-preview"},
            )
        self.assertEqual(retried.status_code, 200)
        retry.assert_called_once_with("director-1", confirmed=True, preview_id="retry-preview")

    def test_terminal_director_status_is_available_as_an_sse_event(self) -> None:
        terminal = {
            "task_id": "director-1",
            "status": "done",
            "terminal": True,
            "revision": 7,
            "report": {"output_count": 1},
        }
        with patch("routes.director.director_batch_status", return_value=terminal):
            response = self.client.get("/api/director/jobs-stream?task_id=director-1")

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn("event: status", response.text)
        self.assertIn('"revision": 7', response.text)

    def test_string_false_cannot_bypass_paid_confirmation(self) -> None:
        with patch("routes.director.start_director_batch") as start:
            response = self.client.post(
                "/api/director/jobs",
                json={
                    "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                    "recipe": {"tool": "line_art"},
                    "confirmed": "false",
                },
            )

        self.assertEqual(response.status_code, 409)
        start.assert_not_called()

    def test_nested_payload_type_errors_are_client_errors_not_server_errors(self) -> None:
        client = TestClient(server.app, raise_server_exceptions=False)
        preview = client.post(
            "/api/director/preview",
            json={"sources": {"kind": "generated"}, "recipe": []},
        )
        execute = client.post(
            "/api/director/jobs",
            json={"sources": 123, "recipe": "line_art", "confirmed": True},
        )

        self.assertEqual(preview.status_code, 400)
        self.assertEqual(execute.status_code, 400)
        self.assertIn("来源图", preview.text)
        self.assertIn("来源图", execute.text)

        invalid_number = client.post(
            "/api/director/preview",
            json={
                "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                "recipe": {"tool": "colorize", "defry": {"unexpected": 3}},
            },
        )
        self.assertEqual(invalid_number.status_code, 400)
        self.assertIn("Defry", invalid_number.text)

    def test_ui_guards_against_stale_source_responses_and_untrusted_report_html(self) -> None:
        script = (ROOT / "web" / "director.js").read_text(encoding="utf-8")

        self.assertIn("sourceRequestSeq", script)
        self.assertIn("if (requestSeq !== state.sourceRequestSeq) return", script)
        # escapeHtml 由 shared/escape.js 统一提供（director.html 先于 director.js 加载）
        self.assertIn("escapeHtml", script)
        html = (ROOT / "web" / "director.html").read_text(encoding="utf-8")
        self.assertIn("/assets/shared/escape.js", html)
        self.assertIn("function localResultUrl", script)
        self.assertIn("batch.cancel_requested", script)
        self.assertIn('state.pickMode === "series"', script)
        self.assertIn("event.ctrlKey || event.metaKey", script)
        self.assertIn('row.kind === "generated_group"', script)
        self.assertIn("/api/director/source-groups/", script)
        self.assertIn("请切到单张挑选", script)
        self.assertIn("unavailable_output_count", script)
        self.assertIn("历史结果已移入回收站或不在本机", script)


if __name__ == "__main__":
    unittest.main()

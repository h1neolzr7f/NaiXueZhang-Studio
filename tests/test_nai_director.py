from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

import nai_director
from generation_jobs import GenerationJobManager


class DirectorRecipeTests(unittest.TestCase):
    def test_catalog_exposes_all_official_director_tools(self) -> None:
        catalog = nai_director.director_catalog()

        self.assertEqual(
            [item["id"] for item in catalog["tools"]],
            [
                "remove_background",
                "line_art",
                "sketch",
                "colorize",
                "emotion",
                "declutter",
            ],
        )
        self.assertEqual(catalog["tools"][0]["req_type"], "bg-removal")
        self.assertTrue(catalog["requires_explicit_confirmation"])
        self.assertEqual(catalog["max_sources"], 40)

    def test_recipe_is_whitelisted_and_tool_specific(self) -> None:
        recipe = nai_director.normalize_director_recipe(
            {
                "tool": "emotion",
                "emotion": "happy",
                "prompt": "red eyes, fang",
                "level": 4,
                "ignored": "must not reach upstream",
            }
        )

        self.assertEqual(
            recipe,
            {
                "tool": "emotion",
                "req_type": "emotion",
                "prompt": "happy, red eyes, fang",
                "defry": 4,
                "outputs_per_source": 1,
            },
        )
        with self.assertRaisesRegex(ValueError, "unknown director tool"):
            nai_director.normalize_director_recipe({"tool": "anything"})


class DirectorPreviewTests(unittest.TestCase):
    def test_server_preview_receipt_blocks_a_source_changed_before_paid_start(self) -> None:
        manager = GenerationJobManager()
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source.png"
            Image.new("RGB", (32, 32), "navy").save(source_path)
            resolved = {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "image_id": "20260722_120000",
                "label": "测试图",
                "image_url": "/data/generated/source.png",
                "path": str(source_path),
                "eligible": True,
            }
            with patch.object(nai_director, "_JOB_MANAGER", manager), patch.object(
                nai_director, "resolve_director_source", return_value=resolved
            ), patch.object(
                nai_director,
                "novelai_director_status",
                return_value={"available": True, "slot_count": 1},
            ), patch.object(nai_director, "augment_image", new=AsyncMock()) as augment:
                preview = nai_director.preview_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "sketch"},
                )
                Image.new("RGB", (32, 32), "red").save(source_path)
                started = nai_director.start_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "sketch"},
                    confirmed=True,
                    preview_id=preview["preview_id"],
                )

        self.assertFalse(started["ok"])
        self.assertEqual(started["error"], "stale_preview")
        augment.assert_not_awaited()

    def test_gallery_preview_url_comes_from_verified_file_not_stale_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            images = Path(temp) / "images"
            actual = images / "nested" / "work_p0.png"
            actual.parent.mkdir(parents=True)
            actual.write_bytes(b"png")
            spec = SimpleNamespace(images_dir=images, asset_base_url="/data/images/")
            with patch.object(nai_director, "get_spec", return_value=spec):
                url = nai_director._gallery_asset_url(actual, "site")

        self.assertEqual(url, "/data/images/nested/work_p0.png")

    def test_preview_resolves_exact_sources_without_calling_provider(self) -> None:
        sources = [
            {"kind": "generated", "image_id": "20260722_120000"},
            {"kind": "gallery", "gallery_id": "site", "work_id": 42, "page_index": 1},
        ]
        resolved = [
            {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "label": "生成图 20260722_120000",
                "image_url": "/data/generated/20260722_120000.png",
                "path": "generated-source.png",
                "width": 832,
                "height": 1216,
                "eligible": True,
            },
            {
                "source_id": "gallery:site:42:p1",
                "kind": "gallery",
                "label": "作品 42 · 第 2 张",
                "image_url": "/data/images/42_p1.png",
                "path": "gallery-source.png",
                "width": 832,
                "height": 1216,
                "eligible": True,
            },
        ]
        with patch.object(nai_director, "resolve_director_source", side_effect=resolved) as resolver, patch.object(
            nai_director, "augment_image", new=AsyncMock()
        ) as augment, patch.object(
            nai_director,
            "novelai_director_status",
            return_value={"available": True, "slot_count": 1, "verified": False, "verified_slot_count": 0},
        ), patch.object(
            nai_director, "_encode_source", return_value=("encoded", 832, 1216, "normalized")
        ), patch.object(
            nai_director, "_raw_file_fingerprint", return_value="raw"
        ):
            preview = nai_director.preview_director_batch(
                sources,
                {"tool": "remove_background"},
            )

        self.assertTrue(preview["ok"])
        self.assertEqual(preview["source_count"], 2)
        self.assertEqual(preview["estimated_outputs"], 6)
        self.assertEqual(preview["billing"]["anlas"], "unknown")
        self.assertTrue(preview["zero_provider_calls"])
        self.assertFalse(preview["provider"]["verified"])
        self.assertEqual(preview["provider"]["verified_slot_count"], 0)
        self.assertEqual(resolver.call_count, 2)
        augment.assert_not_awaited()

    def test_preview_rejects_duplicate_identity(self) -> None:
        source = {"kind": "generated", "image_id": "20260722_120000"}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            nai_director.preview_director_batch([source, source], {"tool": "line_art"})

    def test_preview_blocks_execution_when_no_director_slot_is_configured(self) -> None:
        source = {
            "source_id": "generated:20260722_120000",
            "kind": "generated",
            "label": "测试图",
            "image_url": "/data/generated/20260722_120000.png",
            "width": 64,
            "height": 64,
            "eligible": True,
        }
        with patch.object(nai_director, "resolve_director_source", return_value=source), patch.object(
            nai_director,
            "novelai_director_status",
            return_value={"available": False, "slot_count": 0, "message": "not configured"},
        ), patch.object(nai_director, "augment_image", new=AsyncMock()) as augment:
            preview = nai_director.preview_director_batch(
                [{"kind": "generated", "image_id": "20260722_120000"}],
                {"tool": "sketch"},
            )

        self.assertFalse(preview["ready"])
        self.assertFalse(preview["provider_ready"])
        self.assertEqual(preview["blocking_issues"][0]["error"], "missing_token")
        augment.assert_not_awaited()


class DirectorProviderPayloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_director_response_is_rejected_before_buffering(self) -> None:
        import nai_api

        class FakeResponse:
            status_code = 200
            headers = {"content-length": "999"}
            text = ""

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def aiter_bytes(self):
                yield b"must-not-be-read"

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, *_args, **_kwargs):
                return FakeResponse()

        entry = {"id": "director-a", "label": "A", "provider": "novelai", "token": "pst-a"}
        with patch.object(nai_api, "DIRECTOR_RESPONSE_MAX_BYTES", 16), patch.object(
            nai_api, "_candidate_token_entries", return_value=[entry]
        ), patch.object(nai_api, "_cooldown_wait", return_value=0), patch.object(
            nai_api, "_set_active_job"
        ), patch.object(nai_api, "_clear_active_job"), patch.object(
            nai_api, "_record_token_failure", return_value=True
        ), patch.object(nai_api.httpx, "AsyncClient", FakeClient), patch.object(
            nai_api, "_extract_pngs_from_zip"
        ) as extract:
            result = await nai_api.call_nai_director(
                request={"image": "abc", "width": 64, "height": 64, "req_type": "sketch"},
                provenance={"tool": "sketch", "source": {"source_id": "generated:test"}},
            )

        self.assertFalse(result["ok"])
        self.assertIn("safe response limit", result["message"])
        extract.assert_not_called()

    async def test_zip_entry_named_png_must_contain_a_real_bounded_png(self) -> None:
        import nai_api

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("image.png", b"this is not a png")

        with self.assertRaisesRegex(ValueError, "invalid PNG"):
            nai_api._extract_pngs_from_zip(archive.getvalue())

        header_only = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + b"IHDR" + (16).to_bytes(4, "big") + (12).to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"
        truncated_archive = io.BytesIO()
        with zipfile.ZipFile(truncated_archive, "w") as zf:
            zf.writestr("header-only.png", header_only)
        with self.assertRaisesRegex(ValueError, "invalid PNG"):
            nai_api._extract_pngs_from_zip(truncated_archive.getvalue())

        valid_png = io.BytesIO()
        Image.new("RGB", (16, 12), "navy").save(valid_png, format="PNG")
        valid_archive = io.BytesIO()
        with zipfile.ZipFile(valid_archive, "w") as zf:
            zf.writestr("nested/output.png", valid_png.getvalue())
        extracted = nai_api._extract_pngs_from_zip(valid_archive.getvalue())
        self.assertEqual((extracted[0]["width"], extracted[0]["height"]), (16, 12))
        self.assertEqual(extracted[0]["archive_name"], "output.png")

    async def test_augment_payload_uses_official_fields_and_preserves_all_zip_outputs(self) -> None:
        outputs = [
            {"archive_name": "image_0.png", "bytes": b"png-a"},
            {"archive_name": "image_1.png", "bytes": b"png-b"},
            {"archive_name": "image_2.png", "bytes": b"png-c"},
        ]
        with patch.object(nai_director, "call_nai_director", new=AsyncMock(return_value={"ok": True, "outputs": outputs})) as call:
            result = await nai_director.augment_image(
                image_base64="abc123",
                width=832,
                height=1216,
                recipe=nai_director.normalize_director_recipe({"tool": "remove_background"}),
                source={"source_id": "generated:source", "label": "source"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(len(result["outputs"]), 3)
        request = call.await_args.kwargs["request"]
        self.assertEqual(
            set(request),
            {"image", "width", "height", "req_type"},
        )
        self.assertEqual(request["req_type"], "bg-removal")

    async def test_provider_5xx_never_fails_over_to_a_second_paid_slot(self) -> None:
        import nai_api

        calls: list[str] = []

        class FakeResponse:
            status_code = 500
            text = "internal error"
            content = b""
            headers = {}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def aiter_bytes(self):
                yield b"internal error"

        class FakeClient:
            def __init__(self, **_kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            def stream(self, _method, _url, *, headers, json):
                calls.append(str(headers.get("Authorization") or ""))
                return FakeResponse()

        entries = [
            {"id": "director-a", "label": "A", "provider": "novelai", "token": "pst-a"},
            {"id": "director-b", "label": "B", "provider": "novelai", "token": "pst-b"},
        ]
        with patch.object(nai_api, "_candidate_token_entries", return_value=entries), patch.object(
            nai_api, "_cooldown_wait", return_value=0
        ), patch.object(nai_api, "_set_active_job"), patch.object(
            nai_api, "_clear_active_job"
        ), patch.object(nai_api, "_record_token_failure", return_value=True), patch.object(
            nai_api.httpx, "AsyncClient", FakeClient
        ):
            result = await nai_api.call_nai_director(
                request={"image": "abc", "width": 64, "height": 64, "req_type": "sketch"},
                provenance={"tool": "sketch", "source": {"source_id": "generated:test"}},
            )

        self.assertEqual(len(calls), 1)
        self.assertFalse(result["ok"])
        self.assertFalse(result["retry_safe"])
        self.assertTrue(result["billing_uncertain"])


class DirectorJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_report_marks_deleted_historical_outputs_without_losing_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temp, patch.object(
            nai_director,
            "DIRECTOR_OUTPUT_DIR",
            Path(temp),
        ):
            report = nai_director._report(
                {
                    "status": "done",
                    "items": [
                        {
                            "ok": True,
                            "outputs": [
                                {
                                    "image_id": "20260722_144144",
                                    "filename": "20260722_144144.png",
                                    "image_url": "/data/generated/20260722_144144.png",
                                }
                            ],
                        }
                    ],
                }
            )

        self.assertEqual(report["output_count"], 1)
        self.assertEqual(report["available_output_count"], 0)
        self.assertEqual(report["unavailable_output_count"], 1)
        self.assertFalse(report["outputs"][0]["available"])

    async def test_idle_report_does_not_claim_provider_reported_zero_cost(self) -> None:
        report = nai_director._report({"status": "idle", "items": []})

        self.assertIsNone(report["anlas_spent"])
        self.assertEqual(report["cost_source"], "not_started")
        self.assertIn("尚未执行", report["billing_message"])

    async def test_confirmed_batch_finishes_with_delivery_report(self) -> None:
        manager = GenerationJobManager(cancel_poll_interval=0.005)
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source.png"
            Image.new("RGB", (64, 64), "navy").save(source_path)
            resolved = {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "label": "测试图",
                "image_url": "/data/generated/20260722_120000.png",
                "path": str(source_path),
                "eligible": True,
            }
            output = {
                "ok": True,
                "outputs": [
                    {
                        "image_id": "20260722_120001",
                        "image_url": "/data/generated/20260722_120001.png",
                        "archive_name": "image_0.png",
                    }
                ],
                "usage": {"anlas_spent": None, "cost_source": "unknown"},
            }
            with patch.object(nai_director, "_JOB_MANAGER", manager), patch.object(
                nai_director, "resolve_director_source", return_value=resolved
            ), patch.object(nai_director, "augment_image", new=AsyncMock(return_value=output)) as augment:
                with patch.object(
                    nai_director,
                    "novelai_director_status",
                    return_value={"available": True, "slot_count": 1},
                ):
                    preview = nai_director.preview_director_batch(
                        [{"kind": "generated", "image_id": "20260722_120000"}],
                        {"tool": "line_art"},
                    )
                    started = nai_director.start_director_batch(
                        [{"kind": "generated", "image_id": "20260722_120000"}],
                        {"tool": "line_art"},
                        confirmed=True,
                        preview_id=preview["preview_id"],
                    )
                self.assertTrue(started["ok"])
                task_id = started["task_id"]
                for _ in range(100):
                    status = nai_director.director_batch_status(task_id)
                    if status.get("terminal"):
                        break
                    await asyncio.sleep(0.01)

        self.assertEqual(status["status"], "done")
        self.assertEqual(status["progress"]["percent"], 100.0)
        self.assertEqual(status["report"]["success_sources"], 1)
        self.assertEqual(status["report"]["output_count"], 1)
        self.assertEqual(status["report"]["cost_source"], "unknown")
        self.assertEqual(augment.await_count, 1)

    async def test_execute_requires_explicit_confirmation(self) -> None:
        result = nai_director.start_director_batch(
            [{"kind": "generated", "image_id": "20260722_120000"}],
            {"tool": "sketch"},
            confirmed=False,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "confirmation_required")

        string_false = nai_director.start_director_batch(
            [{"kind": "generated", "image_id": "20260722_120000"}],
            {"tool": "sketch"},
            confirmed="false",  # type: ignore[arg-type]
        )
        self.assertFalse(string_false["ok"])
        self.assertEqual(string_false["error"], "confirmation_required")

    async def test_all_failed_sources_end_as_error_instead_of_completed(self) -> None:
        manager = GenerationJobManager(cancel_poll_interval=0.005)
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source.png"
            Image.new("RGB", (32, 32), "navy").save(source_path)
            resolved = {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "label": "测试图",
                "image_url": "/data/generated/source.png",
                "path": str(source_path),
                "eligible": True,
            }
            failure = {
                "ok": False,
                "outputs": [],
                "usage": {},
                "error": "rejected",
                "message": "上游拒绝",
                "retry_safe": True,
                "billing_uncertain": False,
            }
            with patch.object(nai_director, "_JOB_MANAGER", manager), patch.object(
                nai_director, "resolve_director_source", return_value=resolved
            ), patch.object(nai_director, "augment_image", new=AsyncMock(return_value=failure)), patch.object(
                nai_director,
                "novelai_director_status",
                return_value={"available": True, "slot_count": 1},
            ):
                preview = nai_director.preview_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                )
                started = nai_director.start_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                    confirmed=True,
                    preview_id=preview["preview_id"],
                )
                task_id = started["task_id"]
                for _ in range(100):
                    status = nai_director.director_batch_status(task_id)
                    if status.get("terminal"):
                        break
                    await asyncio.sleep(0.01)

        self.assertEqual(status["status"], "error")
        self.assertEqual(status["report"]["failed_sources"], 1)
        self.assertTrue(status["can_retry"])

    async def test_ambiguous_paid_failure_is_not_offered_for_blind_retry(self) -> None:
        manager = GenerationJobManager(cancel_poll_interval=0.005)
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source.png"
            Image.new("RGB", (32, 32), "navy").save(source_path)
            resolved = {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "label": "测试图",
                "image_url": "/data/generated/source.png",
                "path": str(source_path),
                "eligible": True,
            }
            failure = {
                "ok": False,
                "outputs": [],
                "usage": {},
                "error": "director_failed",
                "message": "请求超时，结果未知",
                "retry_safe": False,
                "billing_uncertain": True,
            }
            with patch.object(nai_director, "_JOB_MANAGER", manager), patch.object(
                nai_director, "resolve_director_source", return_value=resolved
            ), patch.object(nai_director, "augment_image", new=AsyncMock(return_value=failure)), patch.object(
                nai_director,
                "novelai_director_status",
                return_value={"available": True, "slot_count": 1},
            ):
                preview = nai_director.preview_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                )
                started = nai_director.start_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                    confirmed=True,
                    preview_id=preview["preview_id"],
                )
                task_id = started["task_id"]
                for _ in range(100):
                    status = nai_director.director_batch_status(task_id)
                    if status.get("terminal"):
                        break
                    await asyncio.sleep(0.01)

                retried = nai_director.retry_director_batch(task_id)

        self.assertFalse(status["can_retry"])
        self.assertTrue(status["needs_review"])
        self.assertEqual(status["blocked_retry_count"], 1)
        self.assertFalse(retried["ok"])
        self.assertEqual(retried["error"], "needs_review")

    async def test_unexpected_provider_adapter_exception_is_billing_uncertain(self) -> None:
        manager = GenerationJobManager(cancel_poll_interval=0.005)
        with tempfile.TemporaryDirectory() as temp:
            source_path = Path(temp) / "source.png"
            Image.new("RGB", (32, 32), "navy").save(source_path)
            resolved = {
                "source_id": "generated:20260722_120000",
                "kind": "generated",
                "label": "测试图",
                "image_url": "/data/generated/source.png",
                "path": str(source_path),
                "eligible": True,
            }
            with patch.object(nai_director, "_JOB_MANAGER", manager), patch.object(
                nai_director, "resolve_director_source", return_value=resolved
            ), patch.object(
                nai_director,
                "augment_image",
                new=AsyncMock(side_effect=RuntimeError("adapter interrupted")),
            ), patch.object(
                nai_director,
                "novelai_director_status",
                return_value={"available": True, "slot_count": 1},
            ):
                preview = nai_director.preview_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                )
                started = nai_director.start_director_batch(
                    [{"kind": "generated", "image_id": "20260722_120000"}],
                    {"tool": "line_art"},
                    confirmed=True,
                    preview_id=preview["preview_id"],
                )
                task_id = started["task_id"]
                for _ in range(100):
                    status = nai_director.director_batch_status(task_id)
                    if status.get("terminal"):
                        break
                    await asyncio.sleep(0.01)

        self.assertFalse(status["can_retry"])
        self.assertTrue(status["needs_review"])
        self.assertEqual(status["blocked_retry_count"], 1)
        self.assertIsNone(status["report"]["anlas_spent"])
        self.assertEqual(status["report"]["cost_source"], "unknown")

    async def test_unknown_recovered_job_cannot_be_blindly_retried(self) -> None:
        manager = GenerationJobManager(cancel_poll_interval=0.005)
        job = manager.start_job(total=1, generate=True, preview_only=False)
        manager.update(
            job,
            _request={
                "targets": [{"kind": "generated", "image_id": "20260722_120000", "source_id": "generated:20260722_120000"}],
                "recipe": nai_director.normalize_director_recipe({"tool": "sketch"}),
                "token_id": "",
            },
        )
        manager.finish(job, status="unknown", message="外部结果未知")

        with patch.object(nai_director, "_JOB_MANAGER", manager):
            status = nai_director.director_batch_status(job.task_id)
            retried = nai_director.retry_director_batch(job.task_id)

        self.assertFalse(status["can_retry"])
        self.assertTrue(status["needs_review"])
        self.assertFalse(retried["ok"])
        self.assertEqual(retried["error"], "needs_review")


if __name__ == "__main__":
    unittest.main()

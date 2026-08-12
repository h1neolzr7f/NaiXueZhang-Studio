from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import butler_service
from butler.workflow import ButlerWorkflowRuntime, UnknownExternalOutcome


class ButlerDirectorCapabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_batch_director_is_typed_confirmed_and_exact(self) -> None:
        action = butler_service.normalize_action(
            {
                "tool": "batch_director",
                "arguments": {
                    "sources": [
                        {"kind": "generated", "image_id": "20260722_120000"},
                        {
                            "kind": "gallery",
                            "gallery_id": "codex",
                            "work_id": 88,
                            "page_index": 2,
                            "ignored": "not allowed",
                        },
                    ],
                    "recipe": {
                        "tool": "emotion",
                        "emotion": "happy",
                        "prompt": "fang",
                        "level": 4,
                        "ignored": "not allowed",
                    },
                },
            }
        )

        self.assertEqual(action["risk"], "confirm")
        self.assertEqual(action["arguments"]["sources"][1], {
            "kind": "gallery",
            "gallery_id": "codex",
            "work_id": 88,
            "page_index": 2,
            "source_id": "gallery:codex:88:p2",
        })
        self.assertEqual(action["arguments"]["recipe"]["req_type"], "emotion")
        self.assertEqual(action["arguments"]["recipe"]["defry"], 4)
        self.assertIn("可能产生 Anlas", butler_service._confirmation_summary(action))

    async def test_workflow_routes_director_to_progress_aware_executor(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = ButlerWorkflowRuntime(Path(temp) / "butler.db")
            runtime._execute_director = AsyncMock(return_value={"ok": True, "completed": True})
            action = {
                "tool": "batch_director",
                "risk": "confirm",
                "label": "批量导演",
                "arguments": {
                    "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                    "recipe": {"tool": "sketch", "req_type": "sketch", "outputs_per_source": 1},
                },
            }
            try:
                result = await runtime._execute_action("wf-director", action, "operation-1")
            finally:
                await runtime.close()

        self.assertTrue(result["completed"])
        runtime._execute_director.assert_awaited_once_with(
            "wf-director", action["arguments"], "operation-1"
        )

    async def test_progress_executor_returns_director_delivery_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = ButlerWorkflowRuntime(Path(temp) / "butler.db")
            runtime.store.create_task(
                "wf-director",
                thread_id="wf-director",
                kind="butler_workflow",
                title="批量导演",
                input_data={"message": "批量提取线稿"},
            )
            started = {"ok": True, "task_id": "director-1", "batch": {"status": "running"}}
            running = {
                "task_id": "director-1",
                "status": "running",
                "message": "正在执行第 1/1 张",
                "done": 0,
                "total": 1,
                "ok_count": 0,
                "fail_count": 0,
                "terminal": False,
                "report": {"output_count": 0},
            }
            done = {
                "task_id": "director-1",
                "status": "done",
                "message": "批量导演完成",
                "done": 1,
                "total": 1,
                "ok_count": 1,
                "fail_count": 0,
                "terminal": True,
                "items": [{"ok": True, "outputs": [{"image_url": "/data/generated/a.png"}]}],
                "report": {
                    "success_sources": 1,
                    "failed_sources": 0,
                    "output_count": 1,
                    "outputs": [{"image_url": "/data/generated/a.png"}],
                    "cost_source": "unknown",
                    "billing_message": "以 NovelAI 账户为准",
                },
            }
            try:
                with patch(
                    "nai_director.preview_director_batch",
                    return_value={"ok": True, "ready": True, "preview_id": "preview-1"},
                ), patch("nai_director.start_director_batch", return_value=started), patch(
                    "nai_director.director_batch_status", side_effect=[running, done]
                ), patch("butler.workflow.asyncio.sleep", new=AsyncMock()):
                    result = await runtime._execute_director(
                        "wf-director",
                        {
                            "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                            "recipe": {"tool": "line_art", "req_type": "lineart", "outputs_per_source": 1},
                        },
                        "operation-1",
                    )
            finally:
                await runtime.close()

        self.assertTrue(result["completed"])
        self.assertEqual(result["generated"], 1)
        self.assertEqual(result["report"]["cost_source"], "unknown")

    async def test_safe_provider_rejection_is_failed_not_misreported_as_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            runtime = ButlerWorkflowRuntime(Path(temp) / "butler.db")
            runtime.store.create_task(
                "wf-director-safe-failure",
                thread_id="wf-director-safe-failure",
                kind="butler_workflow",
                title="批量导演",
                input_data={"message": "批量线稿"},
            )
            failed = {
                "task_id": "director-failed",
                "status": "error",
                "message": "NAI 槽位拒绝了请求",
                "done": 1,
                "total": 1,
                "ok_count": 0,
                "fail_count": 1,
                "terminal": True,
                "needs_review": False,
                "can_retry": True,
                "report": {"failed_sources": 1, "retryable_count": 1},
            }
            try:
                with patch(
                    "nai_director.preview_director_batch",
                    return_value={"ok": True, "ready": True, "preview_id": "preview-2"},
                ), patch(
                    "nai_director.start_director_batch",
                    return_value={"ok": True, "task_id": "director-failed"},
                ), patch("nai_director.director_batch_status", return_value=failed):
                    with self.assertRaises(RuntimeError) as raised:
                        await runtime._execute_director(
                            "wf-director-safe-failure",
                            {
                                "sources": [{"kind": "generated", "image_id": "20260722_120000"}],
                                "recipe": {"tool": "line_art"},
                            },
                            "operation-safe-failure",
                        )
            finally:
                await runtime.close()

        self.assertNotIsInstance(raised.exception, UnknownExternalOutcome)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import asyncio
import threading
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from butler.store import ButlerTaskStore
from butler.workflow import ButlerWorkflowRuntime
import butler.workflow as workflow_module
from knowledge_catalog import KnowledgeCatalog


def _ai_ready() -> dict:
    return {"has_api_key": True, "model": "planner-test"}


class ButlerWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state_path = Path(self.temp.name) / "butler.db"
        self.runtimes: list[ButlerWorkflowRuntime] = []
        self.audit = patch("butler.workflow.legacy._write_audit")
        self.audit.start()

    async def asyncTearDown(self) -> None:
        for runtime in reversed(self.runtimes):
            await runtime.close()
        self.audit.stop()
        self.temp.cleanup()

    def runtime(self, **kwargs) -> ButlerWorkflowRuntime:
        runtime = ButlerWorkflowRuntime(self.state_path, ai_status_fn=_ai_ready, **kwargs)
        self.runtimes.append(runtime)
        return runtime

    async def test_chat_history_survives_store_restart(self) -> None:
        store = ButlerTaskStore(self.state_path)
        store.add_message("user", "帮我找最近收藏最多的作品")
        store.add_message("assistant", "好呀，我来帮你整理。", workflow_id="wf-1")
        store.close()

        reopened = ButlerTaskStore(self.state_path)
        try:
            history = reopened.list_messages(limit=20)
        finally:
            reopened.close()

        self.assertEqual([item["role"] for item in history], ["user", "assistant"])
        self.assertEqual(history[-1]["content"], "好呀，我来帮你整理。")
        self.assertEqual(history[-1]["workflow_id"], "wf-1")

    async def test_chat_history_redacts_new_and_legacy_credentials_without_deleting_messages(self) -> None:
        key = "NiHJEtPhuITLqyvAQIUUf8F7kN9MgeHwkDVtR-R_T70"
        store = ButlerTaskStore(self.state_path)
        stored = store.add_message("user", f"{key} 闲云的api配置好")
        self.assertNotIn(key, stored["content"])

        with store._lock:
            conn = store._connection()
            conn.execute(
                "INSERT INTO butler_messages(role, content, workflow_id, created_at) "
                "VALUES ('user', ?, '', ?)",
                (f"密码dce7ekb5 账号user@example.com {key}", "2026-07-27T01:00:00"),
            )
            conn.commit()
        store.create_task(
            "legacy-secret-task",
            thread_id="legacy-secret-task",
            kind="butler_workflow",
            title=f"配置 {key}",
            input_data={"message": f"账号user@example.com {key}"},
        )
        store.close()

        reopened = ButlerTaskStore(self.state_path)
        reopened.start()
        history = reopened.list_messages(limit=20)
        task = reopened.get_task("legacy-secret-task")
        persisted = [
            str(row["content"])
            for row in reopened._connection().execute(
                "SELECT content FROM butler_messages ORDER BY id"
            ).fetchall()
        ]
        reopened.close()

        self.assertEqual(len(history), 2)
        self.assertNotIn(key, str(history))
        self.assertNotIn("dce7ekb5", str(history))
        self.assertNotIn("user@example.com", str(history))
        self.assertNotIn(key, str(persisted))
        self.assertTrue(all("[REDACTED]" in item for item in persisted))
        self.assertNotIn(key, str(task))
        self.assertNotIn("user@example.com", str(task))

    async def test_task_store_publishes_in_memory_revisions_on_changes(self) -> None:
        store = ButlerTaskStore(self.state_path)
        before = store.task_revision()
        store.create_task(
            "wf-revision",
            thread_id="wf-revision",
            kind="butler_workflow",
            title="revision",
            input_data={"message": "revision"},
        )
        created = store.wait_for_task_change(before, timeout=0)
        store.update_task("wf-revision", status="running", message="working")
        updated = store.wait_for_task_change(created, timeout=0)
        store.close()

        self.assertGreater(created, before)
        self.assertGreater(updated, created)

    async def test_submitting_chat_persists_user_and_assistant_messages(self) -> None:
        runtime = self.runtime(
            planner=Mock(return_value={"reply": "交给小镜吧，我已经整理好计划。", "actions": []})
        )

        response = await runtime.submit("帮我整理今天的图库")
        history = runtime.store.list_messages(limit=20)

        self.assertEqual(response["reply"], "交给小镜吧，我已经整理好计划。")
        self.assertEqual(
            [(item["role"], item["content"]) for item in history],
            [
                ("user", "帮我整理今天的图库"),
                ("assistant", "交给小镜吧，我已经整理好计划。"),
            ],
        )
        self.assertEqual(history[-1]["workflow_id"], response["workflow_id"])

    async def test_image_is_planned_once_and_only_metadata_is_persisted(self) -> None:
        planner = Mock(return_value={"reply": "我看到了，建议加强主体光影。", "actions": []})
        runtime = self.runtime(planner=planner)
        data_url = "data:image/png;base64,iVBORw0KGgpmaXh0dXJl"

        response = await runtime.submit(
            "评价这张图",
            [],
            image={"name": "sample.png", "mime": "image/png", "data_url": data_url},
        )
        task = response["task"]
        history = runtime.store.list_messages(limit=10)

        planner.assert_called_once()
        self.assertEqual(planner.call_args.args[2]["data_url"], data_url)
        self.assertNotIn(data_url, str(task["input"]))
        self.assertNotIn(data_url, str(task["events"]))
        self.assertIn("sample.png", history[0]["content"])

    async def test_read_only_workflow_completes_and_persists_timeline(self) -> None:
        planner = Mock(
            return_value={
                "reply": "找到一项",
                "actions": [{"tool": "list_queue", "arguments": {"limit": 2}}],
            }
        )
        auto = Mock(return_value={"ok": True, "tool": "list_queue", "items": []})
        runtime = self.runtime(planner=planner, auto_executor=auto)

        usage = {
            "calls": 1,
            "input_tokens": 90,
            "output_tokens": 18,
            "total_tokens": 108,
            "cached_tokens": 0,
            "images": 0,
            "anlas_spent": 0.0,
            "anlas_unknown_images": 0,
            "anlas_complete": True,
            "duration_ms": 120,
        }
        with patch.object(workflow_module, "usage_summary", return_value=usage) as summarize:
            response = await runtime.submit("查看待生成")

        self.assertEqual(response["task"]["status"], "succeeded")
        self.assertEqual(response["tool_results"][0]["tool"], "list_queue")
        self.assertEqual(response["pending_actions"], [])
        self.assertGreaterEqual(len(response["task"]["events"]), 3)
        self.assertEqual(response["task"]["progress"]["current_label"], "交付报告已生成")
        self.assertEqual(response["task"]["progress"]["next_label"], "无，任务已结束")
        self.assertEqual(response["task"]["progress"]["steps"][0]["status"], "completed")
        report = response["task"]["result"]["report"]
        self.assertEqual(report["counts"]["planned"], 1)
        self.assertEqual(report["counts"]["completed"], 1)
        self.assertEqual(report["usage"]["total_tokens"], 108)
        self.assertIn("交付报告", report["title"])
        summarize.assert_called_once_with(workflow_id=response["workflow_id"])
        planner.assert_called_once()
        auto.assert_called_once()

    async def test_all_rejected_planner_actions_fail_instead_of_reporting_success(self) -> None:
        runtime = self.runtime(
            planner=Mock(
                return_value={
                    "reply": "准备生成",
                    "actions": [
                        {
                            "tool": "generate_image",
                            "arguments": {"work_id": 145743565, "character": "doctor_m 和 skadi_f"},
                        }
                    ],
                }
            )
        )

        response = await runtime.submit("把角色换成 doctor_m 和 skadi_f 后生成")

        self.assertEqual(response["task"]["status"], "failed")
        self.assertEqual(response["task"]["result"]["report"]["counts"]["rejected"], 1)
        self.assertIn("计划校验", response["task"]["message"])

    def test_exact_failed_command_gets_a_local_multi_character_plan(self) -> None:
        plan = workflow_module._local_read_only_plan(
            "145743565 这个作品角色批量更换换成doctor_m和skadi_f生成"
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan["actions"][0]["tool"], "batch_generate")
        action = workflow_module.legacy.normalize_action(plan["actions"][0])
        replacements = action["arguments"]["remix_recipe"]["transform"]["replacements"]
        self.assertEqual([item["preset_id"] for item in replacements], ["doctor_m", "skadi_f"])
        self.assertEqual(action["arguments"]["copies_per_work"], 1)

    async def test_retry_of_old_failed_command_uses_new_local_repair_plan(self) -> None:
        planner = Mock(
            return_value={
                "reply": "旧规划",
                "actions": [
                    {
                        "tool": "generate_image",
                        "arguments": {"work_id": 145743565, "character": "doctor_m 和 skadi_f"},
                    }
                ],
            }
        )
        runtime = self.runtime(planner=planner)
        message = "145743565 这个作品角色批量更换换成doctor_m和skadi_f生成"
        failed = await runtime.submit(message)
        self.assertEqual(failed["task"]["status"], "failed")

        with patch.object(
            workflow_module.legacy,
            "_preview_remix_action",
            return_value={"ready": 1, "total": 1, "items": []},
        ):
            retried = await runtime.retry(failed["workflow_id"])

        self.assertEqual(retried["task"]["status"], "awaiting_confirmation")
        self.assertEqual(retried["task"]["retry_of"], failed["workflow_id"])
        self.assertEqual(planner.call_count, 1)
        replacements = retried["task"]["pending_action"]["preview"]
        self.assertEqual(replacements["ready"], 1)

    async def test_multiple_completed_workflows_do_not_share_empty_confirmation_id(self) -> None:
        runtime = self.runtime(
            planner=Mock(return_value={"reply": "完成", "actions": []})
        )

        first = await runtime.submit("整理第一组")
        second = await runtime.submit("整理第二组")

        self.assertEqual(first["task"]["status"], "succeeded")
        self.assertEqual(second["task"]["status"], "succeeded")
        self.assertEqual(first["task"]["confirmation_id"], "")
        self.assertEqual(second["task"]["confirmation_id"], "")

    async def test_write_waits_for_langgraph_interrupt_and_receipt_is_one_time(self) -> None:
        planner = Mock(
            return_value={
                "reply": "需要确认",
                "actions": [{"tool": "remove_from_queue", "arguments": {"work_ids": [11]}}],
            }
        )
        confirmed = AsyncMock(
            return_value={"ok": True, "tool": "remove_from_queue", "message": "done"}
        )
        runtime = self.runtime(planner=planner, confirmed_executor=confirmed)

        pending = await runtime.submit("移出 11")
        confirmed.assert_not_awaited()
        ticket = pending["pending_actions"][0]["confirmation_id"]
        self.assertEqual(pending["task"]["status"], "awaiting_confirmation")
        self.assertEqual(pending["task"]["progress"]["steps"][0]["status"], "waiting")
        self.assertEqual(pending["task"]["progress"]["eta_text"], "确认后继续估算")

        completed = await runtime.confirm(ticket, approve=True)

        confirmed.assert_awaited_once()
        self.assertEqual(completed["task"]["status"], "succeeded")
        self.assertIsInstance(completed["task"]["result"]["report"], dict)
        self.assertEqual(completed["task"]["receipts"][0]["status"], "succeeded")
        with self.assertRaises(ValueError):
            await runtime.confirm(ticket, approve=True)

    async def test_pipeline_workflow_tracks_real_progress_before_reporting_complete(self) -> None:
        planner = Mock(
            return_value={
                "reply": "需要确认后补跑后处理",
                "actions": [{"tool": "run_pipeline", "arguments": {"all_missing": True}}],
            }
        )
        confirmed = AsyncMock(
            return_value={"ok": True, "tool": "run_pipeline", "total": 2, "message": "后处理已启动"}
        )
        runtime = self.runtime(planner=planner, confirmed_executor=confirmed)
        pending = await runtime.submit("补跑所有缺失后处理")

        with patch(
            "post_pipeline.pipeline_status",
            side_effect=[
                {"status": "running", "message": "处理第 1 张", "total": 2, "done": 1, "ok": 1, "fail": 0},
                {"status": "idle", "message": "完成：成功 2，失败 0", "total": 2, "done": 2, "ok": 2, "fail": 0, "items": []},
            ],
        ), patch.object(workflow_module.asyncio, "sleep", new=AsyncMock()) as sleep:
            completed = await runtime.confirm(pending["pending_actions"][0]["confirmation_id"], approve=True)

        self.assertEqual(completed["task"]["status"], "succeeded")
        self.assertEqual(completed["task"]["progress"]["current"], 1)
        self.assertEqual(completed["tool_results"][0]["succeeded"], 2)
        self.assertEqual(completed["tool_results"][0]["failed"], 0)
        self.assertTrue(completed["tool_results"][0]["completed"])
        sleep.assert_awaited_once_with(0.2)
        confirmed.assert_awaited_once()

    async def test_secrets_are_redacted_before_planner_and_checkpoint(self) -> None:
        planner = Mock(return_value={"reply": "不会处理密钥", "actions": []})
        runtime = self.runtime(planner=planner)
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"

        response = await runtime.submit(
            f"我的 api_key={secret}，查看队列",
            [{"role": "user", "content": f"Bearer {secret}"}],
        )

        planned_message, planned_history = planner.call_args.args
        self.assertNotIn(secret, planned_message)
        self.assertNotIn(secret, str(planned_history))
        self.assertNotIn(secret, str(response["task"]["input"]))
        self.assertIn("[REDACTED]", planned_message)

    async def test_nai_and_chinese_credentials_never_reach_planner_or_persistence(self) -> None:
        planner = Mock(return_value={"reply": "凭据只会在本机处理", "actions": []})
        runtime = self.runtime(planner=planner)
        nai_token = "pst-" + "A" * 48
        password = "ExamplePass123"
        email = "user@example.com"

        response = await runtime.submit(
            f"帮我检查 NAI，token={nai_token}，账号 {email}，密码：{password}",
            [{"role": "user", "content": f"令牌：{nai_token} 密码={password}"}],
        )

        planned_message, planned_history = planner.call_args.args
        persisted = {
            "task": response["task"],
            "messages": runtime.store.list_messages(limit=20),
        }
        for secret in (nai_token, password, email):
            self.assertNotIn(secret, planned_message)
            self.assertNotIn(secret, str(planned_history))
            self.assertNotIn(secret, str(persisted))
        self.assertIn("[REDACTED]", planned_message)

    async def test_explicit_nai_credential_setup_is_local_and_never_calls_planner(self) -> None:
        planner = Mock(return_value={"reply": "must not run", "actions": []})
        runtime = self.runtime(planner=planner)
        token = "pst-" + "B" * 48

        with patch("nai_api.add_token_entry", return_value={"ok": True} ) as add:
            response = await runtime.submit(f"帮我配置并启用 NAI token={token}")

        planner.assert_not_called()
        add.assert_called_once()
        self.assertEqual(response["task"]["status"], "succeeded")
        self.assertNotIn(token, str(response))
        self.assertNotIn(token, str(runtime.store.list_messages(limit=20)))

    async def test_cancelling_an_interrupted_workflow_reaches_terminal_state(self) -> None:
        planner = Mock(
            return_value={
                "reply": "需要确认",
                "actions": [{"tool": "clear_queue", "arguments": {}}],
            }
        )
        confirmed = AsyncMock(return_value={"ok": True})
        runtime = self.runtime(planner=planner, confirmed_executor=confirmed)
        pending = await runtime.submit("清空")

        cancelled = await runtime.cancel(pending["workflow_id"])

        confirmed.assert_not_awaited()
        self.assertEqual(cancelled["task"]["status"], "cancelled")

    async def test_long_confirmed_tool_runs_in_background(self) -> None:
        planner = Mock(
            return_value={
                "reply": "需要确认",
                "actions": [
                    {
                        "tool": "generate_image",
                        "arguments": {"prompt": "cat", "batch_count": 1},
                    }
                ],
            }
        )

        async def slow_result(_action):
            await asyncio.sleep(0.05)
            return {"ok": True, "tool": "generate_image", "items": [{}]}

        runtime = self.runtime(planner=planner, confirmed_executor=slow_result)
        pending = await runtime.submit("生成一张猫")
        accepted = await runtime.confirm(
            pending["pending_actions"][0]["confirmation_id"], approve=True
        )

        self.assertEqual(accepted["task"]["status"], "accepted")
        for _ in range(30):
            task = runtime.store.get_task(pending["workflow_id"])
            if task and task["terminal"]:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(task["status"], "succeeded")

    async def test_background_submit_returns_immediately_and_posts_one_completion_report(self) -> None:
        gate = threading.Event()

        def slow_auto(_action):
            gate.wait(timeout=2)
            return {"ok": True, "tool": "list_queue", "items": []}

        runtime = self.runtime(
            planner=Mock(return_value={
                "reply": "开始执行",
                "actions": [{"tool": "list_queue", "arguments": {"limit": 2}}],
            }),
            auto_executor=slow_auto,
        )
        response = await runtime.submit("查看待生成", run_in_background=True)
        self.assertEqual(response["task"]["status"], "planned")
        self.assertTrue(response["workflow_id"])
        gate.set()
        for _ in range(50):
            task = runtime.store.get_task(response["workflow_id"])
            messages = [
                item for item in runtime.store.list_messages(limit=20)
                if item["role"] == "assistant" and item["workflow_id"] == response["workflow_id"]
            ]
            reports = [item for item in messages if "执行报告" in item["content"]]
            if task and task["terminal"] and reports:
                break
            await asyncio.sleep(0.02)
        self.assertEqual(len(messages), 2)
        self.assertIn("进度会实时更新", messages[0]["content"])
        self.assertEqual(len(reports), 1)
        self.assertIn("执行报告", reports[0]["content"])
        self.assertIn("任务中心", reports[0]["content"])

    async def test_gallery_audit_shortcut_skips_the_general_planner(self) -> None:
        expected = {"ok": True, "reply": "开始体检"}
        with patch.object(
            workflow_module._RUNTIME,
            "submit",
            new=AsyncMock(return_value=expected),
        ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
            result = await workflow_module.submit_butler_chat(
                "体检图库", [], None, "gallery_audit"
            )

        self.assertEqual(result, expected)
        plan = submit.call_args.kwargs["preplanned"]
        self.assertEqual(plan["actions"][0]["tool"], "audit_gallery")
        self.assertEqual(plan["actions"][0]["arguments"]["limit"], 6)

    async def test_explicit_fixed_candidate_comparison_skips_general_planner(self) -> None:
        expected = {"ok": True, "reply": "开始比较"}
        candidates = [
            {"gallery_id": "site", "work_id": 7, "page_index": 0},
            {"gallery_id": "qqgroup", "work_id": 9, "page_index": 2},
        ]
        with patch.object(
            workflow_module._RUNTIME,
            "submit",
            new=AsyncMock(return_value=expected),
        ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
            result = await workflow_module.submit_butler_chat(
                "这两张哪个更好看？", [], None, "gallery_compare", candidates
            )

        self.assertEqual(result, expected)
        plan = submit.call_args.kwargs["preplanned"]
        self.assertEqual(plan["actions"][0]["tool"], "compare_gallery_candidates")
        self.assertEqual(plan["actions"][0]["arguments"]["candidates"], candidates)
        self.assertTrue(plan["actions"][0]["arguments"]["use_vision"])

    async def test_common_read_only_commands_skip_model_planning(self) -> None:
        cases = {
            "查看待生成队列": "list_queue",
            "查看我的收藏": "list_favorites",
            "查看最近的生成结果": "list_generated",
            "检查三图库采集状态": "inspect_crawler",
            "查看系统运行健康": "inspect_operations",
            "查看生成任务和后处理状态": "inspect_production",
            "搜索图库：蓝发少女": "search_gallery",
            "查看法典作品 12345": "inspect_work",
            "增量更新本地软件知识库": "rebuild_knowledge_catalog",
        }
        for message, expected_tool in cases.items():
            with self.subTest(message=message), patch.object(
                workflow_module._RUNTIME,
                "submit",
                new=AsyncMock(return_value={"ok": True, "workflow_id": "fast"}),
            ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
                await workflow_module.submit_butler_chat(message, [], None, "")

            plan = submit.call_args.kwargs["preplanned"]
            self.assertEqual(plan["actions"][0]["tool"], expected_tool)
            self.assertIn("本地", plan["reply"])
            if expected_tool == "search_gallery":
                self.assertEqual(plan["actions"][0]["arguments"]["q"], "蓝发少女")
            if expected_tool == "inspect_work":
                self.assertEqual(
                    plan["actions"][0]["arguments"],
                    {"gallery_id": "codex", "work_id": 12345, "page_index": 0},
                )

    async def test_preplanned_knowledge_rebuild_runs_without_ai_and_persists_progress(self) -> None:
        source_root = Path(self.temp.name) / "knowledge-project"
        docs = source_root / "docs"
        docs.mkdir(parents=True)
        (docs / "a.md").write_text("# A\n\n配置说明。\n", encoding="utf-8")
        (docs / "b.md").write_text("# B\n\n生成说明。\n", encoding="utf-8")
        catalog = KnowledgeCatalog(Path(self.temp.name) / "knowledge.db", source_root)
        runtime = ButlerWorkflowRuntime(
            self.state_path,
            ai_status_fn=lambda: {"has_api_key": False, "model": ""},
        )
        self.runtimes.append(runtime)
        plan = {
            "reply": "我会增量更新内置知识库，不调用模型。",
            "actions": [{"tool": "rebuild_knowledge_catalog", "arguments": {}}],
        }

        with patch("butler.workflow.get_knowledge_catalog", create=True, return_value=catalog):
            response = await runtime.submit("更新本地知识库", preplanned=plan)

        task = response["task"]
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["progress"]["item_current"], 2)
        self.assertEqual(task["progress"]["item_total"], 2)
        self.assertEqual(task["result"]["report"]["counts"]["completed"], 1)
        self.assertEqual(task["receipts"][0]["tool"], "rebuild_knowledge_catalog")
        self.assertEqual(task["receipts"][0]["status"], "succeeded")
        self.assertEqual(task["result"]["tool_results"][0]["model_calls"], 0)

    async def test_contextual_or_image_commands_do_not_use_read_only_fast_path(self) -> None:
        for message, image in (
            ("查看那个收藏", None),
            ("查看收藏并删除第一个", None),
            ("查看我的收藏", {"name": "x.png", "mime": "image/png", "data_url": "data:image/png;base64,eA=="}),
        ):
            with self.subTest(message=message), patch.object(
                workflow_module._RUNTIME,
                "submit",
                new=AsyncMock(return_value={"ok": True}),
            ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
                await workflow_module.submit_butler_chat(message, [], image, "")

            self.assertIsNone(submit.call_args.kwargs["preplanned"])

    async def test_software_usage_question_skips_model_planning(self) -> None:
        with patch.object(
            workflow_module._RUNTIME,
            "record_answer",
            new=AsyncMock(return_value={"ok": True, "answer_only": True, "workflow_id": ""}),
        ) as record, patch.object(
            workflow_module._RUNTIME, "submit", new=AsyncMock()
        ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
            result = await workflow_module.submit_butler_chat(
                "生成失败后怎么只重试失败的图片？", [], None, ""
            )

        self.assertTrue(result["answer_only"])
        submit.assert_not_awaited()
        reply = record.call_args.args[1]
        self.assertIn("失败和未完成项", reply)
        self.assertIn("/generated", reply)

    async def test_specific_software_question_uses_traced_local_knowledge(self) -> None:
        source_root = Path(self.temp.name) / "project"
        docs = source_root / "docs"
        docs.mkdir(parents=True)
        (docs / "nai-anima-adaptation.md").write_text(
            "# NAI 角色槽\n\n人数放在 Base，角色槽只写 girl、boy 或 other。\n",
            encoding="utf-8",
        )
        catalog = KnowledgeCatalog(Path(self.temp.name) / "knowledge.db", source_root)
        catalog.refresh_builtin_sources()

        with patch("software_help.get_knowledge_catalog", return_value=catalog), patch.object(
            workflow_module._RUNTIME,
            "record_answer",
            new=AsyncMock(return_value={"ok": True, "answer_only": True, "workflow_id": ""}),
        ) as record, patch.object(
            workflow_module._RUNTIME, "submit", new=AsyncMock()
        ) as submit, patch.dict(workflow_module.os.environ, {"BUTLER_ENGINE": "langgraph"}):
            await workflow_module.submit_butler_chat(
                "NAI V4.5 的 Base 和角色槽人数怎么写？", [], None, ""
            )

        submit.assert_not_awaited()
        reply = record.call_args.args[1]
        self.assertIn("人数放在 Base", reply)
        self.assertIn("依据：docs/nai-anima-adaptation.md", reply)
        self.assertIn("/references", reply)

    async def test_general_question_is_answered_without_creating_a_task(self) -> None:
        answer_response = {"ok": True, "answer_only": True, "workflow_id": "", "reply": "可以，但需要确认。"}
        with patch.object(
            workflow_module.legacy,
            "request_answer",
            return_value={"reply": "可以，但需要确认。", "actions": [{"tool": "batch_generate"}]},
        ) as answer, patch.object(
            workflow_module._RUNTIME,
            "record_answer",
            new=AsyncMock(return_value=answer_response),
        ) as record, patch.object(
            workflow_module._RUNTIME, "submit", new=AsyncMock()
        ) as submit:
            result = await workflow_module.submit_butler_chat("你觉得现在的图库整理思路合理吗？", [], None, "")

        self.assertTrue(result["answer_only"])
        answer.assert_called_once()
        record.assert_awaited_once()
        submit.assert_not_awaited()

    async def test_answer_record_persists_chat_without_task_center_entry(self) -> None:
        runtime = self.runtime()
        result = await runtime.record_answer(
            "批量导演会消耗积分吗？",
            "预检不消耗，确认执行后可能消耗 Anlas。",
            answer_id="answer-test",
        )

        self.assertTrue(result["answer_only"])
        self.assertEqual(runtime.store.list_tasks(limit=10), [])
        messages = runtime.store.list_messages(limit=10)
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertTrue(all(item["workflow_id"] == "answer-test" for item in messages))

    async def test_explicit_command_still_enters_task_workflow(self) -> None:
        with patch.object(
            workflow_module._RUNTIME,
            "submit",
            new=AsyncMock(return_value={"ok": True, "workflow_id": "task-1"}),
        ) as submit:
            result = await workflow_module.submit_butler_chat("帮我生成 4 张图", [], None, "")

        self.assertEqual(result["workflow_id"], "task-1")
        submit.assert_awaited_once()


class ButlerRecoveryTests(unittest.TestCase):
    def test_started_side_effect_becomes_unknown_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            first = ButlerTaskStore(path)
            first.create_task(
                "wf",
                thread_id="wf",
                kind="butler_workflow",
                title="x",
                input_data={"message": "x"},
            )
            first.update_task("wf", status="running", phase="tool:generate_image")
            first.put_receipt(
                "op",
                task_id="wf",
                action_index=0,
                tool="generate_image",
                arguments_hash="hash",
                status="started",
            )
            first.close()

            second = ButlerTaskStore(path)
            recovered = second.recover_interrupted()
            task = second.get_task("wf")
            second.close()

        self.assertEqual(recovered["unknown"], 1)
        self.assertEqual(task["status"], "unknown")
        self.assertEqual(task["receipts"][0]["status"], "unknown")


if __name__ == "__main__":
    unittest.main()

"""LangGraph Implementation behind the intelligent-butler workflow Interface."""

from __future__ import annotations

import asyncio
import atexit
import copy
import hashlib
import json
import os
import re
import secrets
import time
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, TypedDict

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

import butler_service as legacy
from knowledge_catalog import KnowledgeRefreshCancelled, get_knowledge_catalog
from paths import data_dir
from usage_ledger import usage_scope, usage_summary

from .redaction import redact_history, redact_text
from .store import ButlerTaskStore, TERMINAL_STATUSES


class WorkflowCancelled(RuntimeError):
    pass


class UnknownExternalOutcome(RuntimeError):
    pass


def _secure_local_configuration_plan(message: str) -> dict[str, Any] | None:
    """Consume explicitly submitted secrets locally before any planner/checkpoint boundary."""

    raw = str(message or "")
    folded = raw.casefold()
    if not any(word in folded for word in ("配置", "保存", "添加", "启用", "设置", "configure")):
        return None
    configured: list[str] = []
    nai_tokens = list(dict.fromkeys(re.findall(r"\bpst-[A-Za-z0-9_-]{20,}\b", raw, re.I)))
    if nai_tokens:
        from nai_api import add_token_entry

        for token in nai_tokens:
            add_token_entry({"token": token, "provider": "novelai"})
        configured.append(f"NovelAI 槽位 {len(nai_tokens)} 个")

    api_keys = list(dict.fromkeys(re.findall(r"\bsk-[A-Za-z0-9_-]{12,}\b", raw)))
    urls = re.findall(r"https?://[^\s,，;；]+", raw)
    if api_keys and urls and any(word in folded for word in ("api", "中转", "grok", "聊天", "识图")):
        from pixiv_launch import save_ai_key, save_config

        api_base = urls[0].rstrip("/")
        save_config(
            {
                "ai": {
                    "provider": "自定义 OpenAI-compatible",
                    "api_base": api_base,
                }
            }
        )
        save_ai_key(api_keys[0])
        configured.append("聊天/识图 API")

    password_present = bool(re.search(r"(?:密码|password)\s*[:：=]?\s*\S+", raw, re.I))
    if not configured and not password_present:
        return None
    if configured:
        reply = (
            f"已经在本机安全配置好：{'、'.join(configured)}。凭据没有交给模型，也没有写入聊天记录；"
            "图库、小镜、工作台和导演会共用这份配置。你可以到配置中心检查或测试连接。"
        )
    else:
        reply = (
            "我识别到了账号或密码，但没有保存密码，也不会把它发给模型。"
            "请在配置中心使用官方 Token/通行密钥入口完成登录；这样更安全，也便于失效后单独更新。"
        )
    return {"reply": reply, "actions": []}


class ButlerState(TypedDict, total=False):
    workflow_id: str
    message: str
    history: list[dict[str, str]]
    preplanned: dict[str, Any]
    model: str
    reply: str
    actions: list[dict[str, Any]]
    action_index: int
    tool_results: list[dict[str, Any]]
    rejected_actions: list[dict[str, str]]
    skipped_actions: list[dict[str, Any]]
    approval: Any
    cancelled: bool
    status: str
    phase: str
    result: dict[str, Any]


Planner = Callable[[str, Any], dict[str, Any]]
AutoExecutor = Callable[[dict[str, Any]], dict[str, Any]]
ConfirmedExecutor = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _operation_identity(workflow_id: str, index: int, action: dict[str, Any]) -> tuple[str, str]:
    canonical = json.dumps(
        {"tool": action["tool"], "arguments": action["arguments"]},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    arguments_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    operation_id = hashlib.sha256(
        f"{workflow_id}:{index}:{arguments_hash}".encode("utf-8")
    ).hexdigest()[:32]
    return operation_id, arguments_hash


def _format_eta(seconds: float | int) -> str:
    value = max(0, int(round(float(seconds or 0))))
    if value <= 20:
        return "预计不到 1 分钟"
    if value < 90:
        return "预计约 1 分钟"
    if value < 3600:
        minutes = max(2, int(round(value / 60)))
        return f"预计约 {minutes} 分钟"
    hours = max(1, int(round(value / 3600)))
    return f"预计约 {hours} 小时"


def _action_estimate_seconds(action: dict[str, Any]) -> int:
    """Return a deliberately broad initial estimate; live loops replace it with observed speed."""
    tool = str(action.get("tool") or "")
    args = action.get("arguments") or {}
    if tool == "generate_image":
        return min(1800, 75 * max(1, int(args.get("batch_count") or 1)))
    if tool in {"batch_generate", "batch_generate_and_prepare_pixiv"}:
        works = args.get("work_refs") or args.get("work_ids") or []
        copies = max(1, int(args.get("copies_per_work") or 1))
        return min(3600, 60 * max(1, len(works) * copies))
    if tool == "batch_director":
        return min(5400, 75 * max(1, len(args.get("sources") or [])))
    if tool == "run_pipeline":
        return 90
    if tool == "prepare_pixiv_submission":
        return 45
    if tool in {"start_crawler", "stop_crawler", "configure_crawler"}:
        return 20
    return 8 if tool in legacy._AUTO_TOOLS else 15


def _planned_progress(
    actions: list[dict[str, Any]],
    index: int,
    *,
    stage: str,
    skipped_indexes: set[int] | None = None,
    waiting: bool = False,
    cancelled: bool = False,
) -> dict[str, Any]:
    total = len(actions)
    cursor = max(0, min(int(index), total))
    skipped = skipped_indexes or set()
    steps: list[dict[str, Any]] = []
    for step_index, action in enumerate(actions):
        if step_index in skipped:
            state = "skipped"
        elif step_index < cursor:
            state = "completed"
        elif cancelled and step_index >= cursor:
            state = "cancelled"
        elif step_index == cursor and cursor < total:
            state = "waiting" if waiting else "running"
        else:
            state = "pending"
        steps.append(
            {
                "index": step_index + 1,
                "tool": str(action.get("tool") or ""),
                "label": str(action.get("label") or action.get("tool") or f"步骤 {step_index + 1}"),
                "status": state,
            }
        )
    remaining = sum(_action_estimate_seconds(item) for item in actions[cursor:])
    current_label = (
        str(actions[cursor].get("label") or actions[cursor].get("tool") or "正在执行")
        if cursor < total
        else "正在整理交付报告"
    )
    next_label = (
        str(actions[cursor + 1].get("label") or actions[cursor + 1].get("tool") or "下一步")
        if cursor + 1 < total
        else ("生成交付报告" if cursor < total else "无，正在收尾")
    )
    return {
        "workflow_current": min(cursor + 1, total) if total else 0,
        "workflow_completed": cursor,
        "workflow_total": total,
        "steps": steps,
        "stage": stage,
        "current_label": current_label,
        "next_label": next_label,
        "eta_seconds": remaining,
        "eta_text": _format_eta(remaining) if remaining else "马上完成",
        "eta_basis": "initial_estimate",
        "estimate_updated_at": _now(),
    }


def _elapsed_seconds(started_at: Any, finished_at: Any = None) -> int:
    try:
        started = datetime.fromisoformat(str(started_at or ""))
        finished = datetime.fromisoformat(str(finished_at or _now()))
        return max(0, int((finished - started).total_seconds()))
    except (TypeError, ValueError):
        return 0


def _status_poll_delay(started_monotonic: float) -> float:
    elapsed = max(0.0, time.monotonic() - started_monotonic)
    if elapsed < 2:
        return 0.2
    if elapsed < 10:
        return 0.4
    return 0.75


class ButlerWorkflowRuntime:
    """Owns LangGraph/checkpointer lifetime and durable Butler task projections."""

    def __init__(
        self,
        state_path: Path,
        *,
        planner: Planner = legacy.request_plan,
        auto_executor: AutoExecutor = legacy._execute_auto,
        confirmed_executor: ConfirmedExecutor = legacy._execute_confirmed,
        ai_status_fn: Callable[[], dict[str, Any]] = legacy.ai_status,
    ) -> None:
        self.state_path = Path(state_path)
        self.checkpoint_path = self.state_path.with_name(
            f"{self.state_path.stem}_checkpoints{self.state_path.suffix or '.db'}"
        )
        self.store = ButlerTaskStore(self.state_path)
        self._planner = planner
        self._auto_executor = auto_executor
        self._confirmed_executor = confirmed_executor
        self._ai_status = ai_status_fn
        self._saver_manager: AbstractAsyncContextManager[AsyncSqliteSaver] | None = None
        self._saver: AsyncSqliteSaver | None = None
        self._graph: Any = None
        self._start_lock = asyncio.Lock()
        self._run_locks: dict[str, asyncio.Lock] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._started = False
        self._recovery: dict[str, int] = {"paused": 0, "unknown": 0}

    @staticmethod
    def _report_from_state(
        state: ButlerState,
        task: dict[str, Any],
        *,
        status: str,
        message: str,
        finished_at: str,
    ) -> dict[str, Any]:
        actions = list(state.get("actions") or [])
        results = [item for item in (state.get("tool_results") or []) if isinstance(item, dict)]
        skipped = list(state.get("skipped_actions") or [])
        rejected = list(state.get("rejected_actions") or [])
        skipped_indexes = {
            int(item.get("action_index"))
            for item in skipped
            if isinstance(item, dict) and item.get("action_index") is not None
        }
        completed = min(len(actions), len(results) + len(skipped))
        progress = _planned_progress(
            actions,
            completed,
            stage="completed" if status not in {"cancelled", "failed", "unknown"} else status,
            skipped_indexes=skipped_indexes,
            cancelled=status == "cancelled",
        )
        steps = progress["steps"]
        if status in {"failed", "unknown"}:
            for step in steps:
                if step["status"] == "running":
                    step["status"] = "failed"
                    break
        item_succeeded = sum(
            int(item.get("succeeded") or item.get("ok_count") or 0) for item in results
        )
        item_failed = sum(
            int(item.get("failed") or item.get("fail_count") or 0) for item in results
        )
        highlights = []
        for item in results:
            text = redact_text(item.get("message") or item.get("summary") or "", limit=240)
            if text and text not in highlights:
                highlights.append(text)
        error = redact_text(task.get("error") or "", limit=500)
        errors = ([error] if error else []) + [
            redact_text(item.get("reason") or "未通过计划校验", limit=300)
            for item in rejected
            if isinstance(item, dict)
        ]
        title = {
            "succeeded": "交付报告 · 已完成",
            "partially_succeeded": "交付报告 · 部分完成",
            "cancelled": "任务报告 · 已取消",
            "failed": "任务报告 · 需要处理",
            "unknown": "任务报告 · 等待核对",
        }.get(status, "任务交付报告")
        if status == "succeeded":
            summary = f"已完成 {len(results)} 个执行步骤，结果和操作记录都已保存。"
        elif status == "partially_succeeded":
            summary = "任务已经尽可能完成，失败项和可重试线索已整理在下面。"
        elif status == "cancelled":
            summary = "任务已按你的要求停止，已完成部分仍保留在记录中。"
        else:
            summary = "这次执行遇到了阻碍，原因和已完成步骤已经保留，可以据此继续处理。"
        links: list[dict[str, str]] = []
        for item in results:
            for key, label in (("gallery_url", "查看图库结果"), ("pixiv_url", "检查投稿草稿")):
                url = str(item.get(key) or "")
                if url and not any(link["url"] == url for link in links):
                    links.append({"label": label, "url": url})
        return {
            "title": title,
            "status": status,
            "summary": summary,
            "message": message,
            "generated_at": finished_at,
            "duration_seconds": _elapsed_seconds(task.get("started_at"), finished_at),
            "counts": {
                "planned": len(actions),
                "completed": len(results),
                "skipped": len(skipped),
                "rejected": len(rejected),
                "item_succeeded": item_succeeded,
                "item_failed": item_failed,
            },
            "steps": steps,
            "highlights": highlights[:8],
            "errors": [item for item in errors if item][:8],
            "links": links[:6],
            "usage": usage_summary(
                workflow_id=str(state.get("workflow_id") or task.get("id") or "")
            ),
        }

    @staticmethod
    def _completion_chat(task: dict[str, Any]) -> str:
        report = ((task.get("result") or {}).get("report") or {})
        counts = report.get("counts") or {}
        status = str(task.get("status") or "")
        if status == "succeeded":
            lead = "完成啦 ✨ 你交给我的任务已经处理好，过程和结果都替你收好了。"
        elif status == "partially_succeeded":
            lead = "我把能完成的部分都认真做完啦。还有少量项目没通过，我已经把原因和重试线索整理好了。"
        elif status == "cancelled":
            lead = "已经按你的要求停下来啦。放心，之前完成的内容和过程记录都还在。"
        else:
            lead = "这次执行中途遇到了一点阻碍，但不用从头猜原因，我已经把现场和下一步线索保留下来了。"
        return (
            f"{lead}\n\n执行报告：{report.get('summary') or task.get('message') or '任务已结束'}"
            f" 共 {counts.get('planned', 0)} 步，完成 {counts.get('completed', 0)}，"
            f"跳过 {counts.get('skipped', 0)}，异常 {counts.get('item_failed', 0) + counts.get('rejected', 0)}。"
            "详细交付报告已放进任务中心，点开这条任务就能逐步查看。"
        )

    def _ensure_task_report(self, workflow_id: str, task: dict[str, Any]) -> dict[str, Any]:
        existing_result = dict(task.get("result") or {})
        if isinstance(existing_result.get("report"), dict):
            return task
        progress = dict(task.get("progress") or {})
        status = str(task.get("status") or "failed")
        steps = list(progress.get("steps") or [])
        if status in {"failed", "unknown"}:
            for step in steps:
                if step.get("status") in {"running", "waiting"}:
                    step["status"] = "failed"
                    break
        error = redact_text(task.get("error") or "", limit=500)
        planned = int(progress.get("workflow_total") or progress.get("total") or len(steps))
        completed = int(progress.get("workflow_completed") or progress.get("current") or 0)
        report = {
            "title": "任务报告 · 等待核对" if status == "unknown" else "任务报告 · 需要处理",
            "status": status,
            "summary": "这次执行遇到了阻碍，现场、错误原因和已完成步骤已经保留。",
            "message": task.get("message") or "任务未完成",
            "generated_at": task.get("finished_at") or _now(),
            "duration_seconds": _elapsed_seconds(task.get("started_at"), task.get("finished_at")),
            "counts": {
                "planned": planned,
                "completed": completed,
                "skipped": 0,
                "rejected": 0,
                "item_succeeded": int(progress.get("succeeded") or 0),
                "item_failed": max(1, int(progress.get("failed") or 0)),
            },
            "steps": steps,
            "highlights": [],
            "errors": [error] if error else [str(task.get("message") or "请打开时间线查看失败位置")],
            "links": [],
            "usage": usage_summary(workflow_id=workflow_id),
        }
        existing_result["report"] = report
        progress.update(
            {
                "steps": steps,
                "stage": "report_ready",
                "current_label": "异常报告已生成",
                "next_label": "检查原因后重试",
                "eta_seconds": 0,
                "eta_text": "已停止",
                "eta_basis": "completed",
            }
        )
        updated = self.store.update_task(workflow_id, result=existing_result, progress=progress)
        return updated or task

    async def start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            self.store.start()
            self._recovery = self.store.recover_interrupted()
            self.store.prune(retention_days=int(os.environ.get("BUTLER_RETENTION_DAYS", "30")))
            manager = AsyncSqliteSaver.from_conn_string(str(self.checkpoint_path))
            saver = await manager.__aenter__()
            try:
                await saver.setup()
                self._saver_manager = manager
                self._saver = saver
                self._graph = self._build_graph().compile(checkpointer=saver)
                self._started = True
            except Exception:
                await manager.__aexit__(None, None, None)
                self.store.close()
                raise

    async def close(self) -> None:
        if not self._started:
            self.store.close()
            return
        tasks = list(self._background)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        manager = self._saver_manager
        self._saver_manager = None
        self._saver = None
        self._graph = None
        self._started = False
        if manager is not None:
            await manager.__aexit__(None, None, None)
        self.store.close()

    def _build_graph(self) -> StateGraph:
        builder = StateGraph(ButlerState)
        builder.add_node("plan", self._plan_node)
        builder.add_node("execute_auto", self._execute_auto_node)
        builder.add_node("approval", self._approval_node)
        builder.add_node("execute_confirmed", self._execute_confirmed_node)
        builder.add_node("skip_action", self._skip_action_node)
        builder.add_node("advance", self._advance_node)
        builder.add_node("finalize", self._finalize_node)
        builder.add_edge(START, "plan")
        builder.add_conditional_edges(
            "plan",
            self._route_current,
            {
                "auto": "execute_auto",
                "approval": "approval",
                "finalize": "finalize",
            },
        )
        builder.add_edge("execute_auto", "advance")
        builder.add_conditional_edges(
            "approval",
            self._route_approval,
            {
                "execute": "execute_confirmed",
                "skip": "skip_action",
                "finalize": "finalize",
            },
        )
        builder.add_edge("execute_confirmed", "advance")
        builder.add_edge("skip_action", "advance")
        builder.add_conditional_edges(
            "advance",
            self._route_current,
            {
                "auto": "execute_auto",
                "approval": "approval",
                "finalize": "finalize",
            },
        )
        builder.add_edge("finalize", END)
        return builder

    async def _plan_node(self, state: ButlerState) -> dict[str, Any]:
        workflow_id = state["workflow_id"]
        task = self.store.get_task(workflow_id, include_events=False) or {}
        if task.get("cancel_requested"):
            return {"cancelled": True, "actions": [], "action_index": 0}
        plan = state.get("preplanned")
        ai = {"model": "local"}
        if not isinstance(plan, dict):
            ai = self._ai_status()
            if not ai.get("has_api_key") or not ai.get("model"):
                raise RuntimeError("请先在设置或发布台配置 AI API Key 和模型")
        self.store.update_task(
            workflow_id,
            status="running",
            phase="planning",
            message="正在制定可执行计划…",
            started_at=task.get("started_at") or _now(),
        )
        if not isinstance(plan, dict):
            plan = await asyncio.to_thread(self._planner, state["message"], state.get("history"))
        reply = legacy._clean_text(plan.get("reply"), limit=2000) or "我已经分析了这条指令。"
        raw_actions = plan.get("actions") or []
        if not isinstance(raw_actions, list):
            raise ValueError("AI 计划中的 actions 不是数组")
        actions: list[dict[str, Any]] = []
        rejected: list[dict[str, str]] = []
        for raw in raw_actions[: legacy.MAX_ACTIONS]:
            try:
                actions.append(legacy.normalize_action(raw))
            except Exception as exc:
                rejected.append(
                    {
                        "tool": legacy._clean_text(
                            raw.get("tool") if isinstance(raw, dict) else "", limit=80
                        ),
                        "reason": legacy._clean_text(exc, limit=300),
                    }
                )
        self.store.update_task(
            workflow_id,
            phase="executing" if actions else "finishing",
            message=f"计划包含 {len(actions)} 个白名单操作",
            progress={
                "current": 0,
                "total": len(actions),
                "succeeded": 0,
                "failed": 0,
                **_planned_progress(
                    actions,
                    0,
                    stage="executing" if actions else "finishing",
                ),
            },
        )
        self.store.add_event(
            workflow_id,
            "planned",
            status="running",
            phase="planning",
            message=f"已生成 {len(actions)} 个白名单操作",
            detail={"tools": [item["tool"] for item in actions], "rejected": len(rejected)},
            event_key="workflow:planned",
        )
        return {
            "model": str(ai.get("model") or ""),
            "reply": reply,
            "actions": actions,
            "action_index": 0,
            "tool_results": [],
            "rejected_actions": rejected,
            "skipped_actions": [],
            "status": "running",
            "phase": "executing",
        }

    def _route_current(self, state: ButlerState) -> str:
        if state.get("cancelled"):
            return "finalize"
        actions = state.get("actions") or []
        index = int(state.get("action_index") or 0)
        if index >= len(actions):
            return "finalize"
        return "auto" if actions[index]["tool"] in legacy._AUTO_TOOLS else "approval"

    def _route_approval(self, state: ButlerState) -> str:
        approval = state.get("approval")
        if isinstance(approval, dict):
            if approval.get("cancel_workflow"):
                return "finalize"
            return "execute" if approval.get("approve") else "skip"
        return "execute" if bool(approval) else "skip"

    async def _execute_auto_node(self, state: ButlerState) -> dict[str, Any]:
        workflow_id = state["workflow_id"]
        index = int(state.get("action_index") or 0)
        action = (state.get("actions") or [])[index]
        if (self.store.get_task(workflow_id, include_events=False) or {}).get("cancel_requested"):
            return {"cancelled": True}
        self.store.update_task(
            workflow_id,
            status="running",
            phase=f"tool:{action['tool']}",
            message=f"正在执行：{action['label']}",
            progress={
                **((self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}),
                **_planned_progress(
                    list(state.get("actions") or []),
                    index,
                    stage=f"tool:{action['tool']}",
                    skipped_indexes={
                        int(item.get("action_index"))
                        for item in (state.get("skipped_actions") or [])
                        if isinstance(item, dict) and item.get("action_index") is not None
                    },
                ),
            },
        )
        if action["tool"] == "rebuild_knowledge_catalog":
            try:
                result = await self._execute_knowledge_refresh(workflow_id, index, action)
            except WorkflowCancelled:
                return {"cancelled": True}
        else:
            result = await asyncio.to_thread(self._auto_executor, action)
        results = list(state.get("tool_results") or [])
        results.append(result)
        self.store.add_event(
            workflow_id,
            "tool_completed",
            status="running",
            phase=f"tool:{action['tool']}",
            message=f"已完成：{action['label']}",
            detail={"tool": action["tool"], "risk": action["risk"]},
            event_key=f"action:{index}:completed",
        )
        return {"tool_results": results}

    async def _execute_knowledge_refresh(
        self,
        workflow_id: str,
        action_index: int,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        """Run the trusted local rebuild behind the existing task/report Interface."""

        operation_id, arguments_hash = _operation_identity(workflow_id, action_index, action)
        previous = self.store.get_receipt(operation_id)
        if previous and previous.get("status") == "succeeded":
            return dict(previous.get("result") or {})
        self.store.put_receipt(
            operation_id,
            task_id=workflow_id,
            action_index=action_index,
            tool=action["tool"],
            arguments_hash=arguments_hash,
            status="started",
        )
        catalog = get_knowledge_catalog(ensure_ready=False)
        started = time.monotonic()
        last_emit = 0.0

        def cancelled() -> bool:
            task = self.store.get_task(workflow_id, include_events=False) or {}
            return bool(task.get("cancel_requested"))

        def publish(event: dict[str, Any]) -> None:
            nonlocal last_emit
            now = time.monotonic()
            current = max(0, int(event.get("processed") or 0))
            total = max(current, int(event.get("total") or 0))
            terminal = total == 0 or current >= total
            if not terminal and current > 0 and now - last_emit < 0.1:
                return
            last_emit = now
            elapsed = max(0.001, now - started)
            eta = 0.0
            if current > 0 and total > current:
                eta = (elapsed / current) * (total - current)
            source = redact_text(event.get("current_source") or "", limit=140)
            current_label = (
                f"正在索引：{source}（{current}/{total}）"
                if source and total
                else f"正在检查知识源（{current}/{total}）"
                if total
                else "正在检查内置知识源"
            )
            task = self.store.get_task(workflow_id, include_events=False) or {}
            progress = dict(task.get("progress") or {})
            progress.update(
                {
                    "item_current": current,
                    "item_total": total,
                    "item_succeeded": int(event.get("inserted") or 0)
                    + int(event.get("updated") or 0)
                    + int(event.get("unchanged") or 0),
                    "item_failed": 0,
                    "current_source": source,
                    "current_label": current_label,
                    "next_label": "生成版本与交付报告" if terminal else "继续增量检查其余知识源",
                    "eta_seconds": int(round(eta)),
                    "eta_text": "即将完成" if terminal else _format_eta(eta),
                    "eta_basis": "observed_source_rate" if current else "initial_estimate",
                    "estimate_updated_at": _now(),
                }
            )
            self.store.update_task(
                workflow_id,
                phase="tool:rebuild_knowledge_catalog",
                message=current_label,
                progress=progress,
            )

        try:
            receipt = await asyncio.to_thread(
                catalog.refresh_builtin_sources,
                on_progress=publish,
                should_cancel=cancelled,
            )
        except KnowledgeRefreshCancelled as exc:
            self.store.put_receipt(
                operation_id,
                task_id=workflow_id,
                action_index=action_index,
                tool=action["tool"],
                arguments_hash=arguments_hash,
                status="failed",
                error="cancelled",
            )
            raise WorkflowCancelled(str(exc)) from exc
        except Exception as exc:
            self.store.put_receipt(
                operation_id,
                task_id=workflow_id,
                action_index=action_index,
                tool=action["tool"],
                arguments_hash=arguments_hash,
                status="failed",
                error=legacy.public_error(exc),
            )
            raise
        result = {
            **receipt,
            "ok": True,
            "tool": action["tool"],
            "provider": "local",
            "model_calls": 0,
            "settings_url": "/settings#knowledgeCatalog",
            "message": (
                f"知识库增量更新完成：{int(receipt.get('documents') or 0)} 个来源、"
                f"{int(receipt.get('chunks') or 0)} 个知识块"
            ),
        }
        self.store.put_receipt(
            operation_id,
            task_id=workflow_id,
            action_index=action_index,
            tool=action["tool"],
            arguments_hash=arguments_hash,
            status="succeeded",
            result=result,
        )
        return result

    def _approval_node(self, state: ButlerState) -> dict[str, Any]:
        index = int(state.get("action_index") or 0)
        action = (state.get("actions") or [])[index]
        operation_id, _arguments_hash = _operation_identity(state["workflow_id"], index, action)
        preview = legacy._preview_remix_action(action)
        decision = interrupt(
            {
                "workflow_id": state["workflow_id"],
                "action_index": index,
                "operation_id": operation_id,
                "tool": action["tool"],
                "label": action["label"],
                "risk": action["risk"],
                "summary": legacy._confirmation_summary(action),
                "arguments_summary": legacy._audit_summary(action["tool"], action["arguments"]),
                "preview": preview,
            }
        )
        if isinstance(decision, dict) and decision.get("cancel_workflow"):
            return {"approval": decision, "cancelled": True}
        return {"approval": decision}

    async def _execute_confirmed_node(self, state: ButlerState) -> dict[str, Any]:
        workflow_id = state["workflow_id"]
        index = int(state.get("action_index") or 0)
        action = (state.get("actions") or [])[index]
        operation_id, arguments_hash = _operation_identity(workflow_id, index, action)
        receipt = self.store.get_receipt(operation_id)
        if receipt and receipt.get("status") == "succeeded":
            result = receipt.get("result") or {}
        elif receipt and receipt.get("status") in {"started", "unknown"}:
            raise UnknownExternalOutcome(
                f"操作 {operation_id} 的外部结果未知，已停止自动重放"
            )
        else:
            self.store.update_task(
                workflow_id,
                status="running",
                phase=f"tool:{action['tool']}",
                message=f"正在执行已确认操作：{action['label']}",
                confirmation_id="",
                pending=None,
                progress={
                    **((self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}),
                    **_planned_progress(
                        list(state.get("actions") or []),
                        index,
                        stage=f"tool:{action['tool']}",
                        skipped_indexes={
                            int(item.get("action_index"))
                            for item in (state.get("skipped_actions") or [])
                            if isinstance(item, dict) and item.get("action_index") is not None
                        },
                    ),
                },
            )
            self.store.put_receipt(
                operation_id,
                task_id=workflow_id,
                action_index=index,
                tool=action["tool"],
                arguments_hash=arguments_hash,
                status="started",
            )
            legacy._write_audit(action["tool"], "accepted", action["arguments"])
            try:
                result = await self._execute_action(workflow_id, action, operation_id)
            except WorkflowCancelled:
                self.store.put_receipt(
                    operation_id,
                    task_id=workflow_id,
                    action_index=index,
                    tool=action["tool"],
                    arguments_hash=arguments_hash,
                    status="failed",
                    error="cancelled",
                )
                legacy._write_audit(action["tool"], "cancelled", action["arguments"])
                return {"cancelled": True}
            except Exception as exc:
                uncertain = action["tool"] in {
                    "generate_image",
                    "batch_generate",
                    "batch_generate_and_prepare_pixiv",
                    "prepare_pixiv_submission",
                    "delete_generated_item",
                    "delete_generated_group",
                    "run_pipeline",
                    "review_generated",
                    "start_crawler",
                    "stop_crawler",
                    "configure_crawler",
                    "retry_exhausted_previews",
                    "cancel_generation",
                }
                self.store.put_receipt(
                    operation_id,
                    task_id=workflow_id,
                    action_index=index,
                    tool=action["tool"],
                    arguments_hash=arguments_hash,
                    status="unknown" if uncertain else "failed",
                    error=legacy.public_error(exc),
                )
                legacy._write_audit(
                    action["tool"], "unknown" if uncertain else "failed", action["arguments"], detail=str(exc)
                )
                if uncertain:
                    raise UnknownExternalOutcome(
                        f"{action['label']} 的外部结果无法确认：{legacy.public_error(exc)}"
                    ) from exc
                raise
            self.store.put_receipt(
                operation_id,
                task_id=workflow_id,
                action_index=index,
                tool=action["tool"],
                arguments_hash=arguments_hash,
                status="succeeded",
                result=result,
            )
            legacy._write_audit(action["tool"], "executed", action["arguments"])
        results = list(state.get("tool_results") or [])
        results.append(result)
        self.store.add_event(
            workflow_id,
            "tool_completed",
            status="running",
            phase=f"tool:{action['tool']}",
            message=f"已完成：{action['label']}",
            detail={"tool": action["tool"], "operation_id": operation_id},
            event_key=f"action:{index}:completed",
        )
        return {"tool_results": results, "approval": None}

    async def _execute_action(
        self, workflow_id: str, action: dict[str, Any], operation_id: str
    ) -> dict[str, Any]:
        tool = action["tool"]
        args = action["arguments"]
        if tool in {"batch_generate", "batch_generate_and_prepare_pixiv"}:
            return await self._execute_batch(
                workflow_id,
                args,
                operation_id,
                prepare_pixiv=tool == "batch_generate_and_prepare_pixiv",
            )
        if tool == "batch_director":
            return await self._execute_director(workflow_id, args, operation_id)
        if tool == "prepare_pixiv_submission":
            from pixiv_launch import prepare_submission_package

            payload = {**args, "package_id": workflow_id}
            self.store.update_task(
                workflow_id,
                phase="preparing_pixiv",
                message="正在补齐后处理并生成投稿草稿…",
            )
            prepared = await asyncio.to_thread(prepare_submission_package, payload)
            return {
                "ok": True,
                "tool": tool,
                "message": "投稿草稿已准备完成，等待人工发布",
                "prepared": prepared.get("prepared") or prepared,
            }
        if tool == "run_pipeline":
            return await self._execute_pipeline(workflow_id, action, operation_id)
        return await self._confirmed_executor(action)

    async def _execute_director(
        self,
        workflow_id: str,
        args: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        """Run a confirmed Director batch while mirroring truthful progress."""

        from nai_director import (
            cancel_director_batch,
            director_batch_status,
            preview_director_batch,
            start_director_batch,
        )

        sources = list(args.get("sources") or [])
        recipe = dict(args.get("recipe") or {})
        preview = preview_director_batch(sources, recipe)
        if not preview.get("ready") or not preview.get("preview_id"):
            failures = list(preview.get("failures") or []) + list(preview.get("blocking_issues") or [])
            detail = "; ".join(str(item.get("message") or "") for item in failures if isinstance(item, dict))
            raise RuntimeError(detail or "批量导演零费用预检未通过")
        started = start_director_batch(
            sources,
            recipe,
            confirmed=True,
            preview_id=str(preview["preview_id"]),
        )
        if not started.get("ok"):
            raise RuntimeError(str(started.get("message") or "批量导演启动失败"))
        director_task_id = str(started.get("task_id") or "")
        observed_started = time.monotonic()
        base_progress = dict(
            (self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}
        )
        self.store.update_task(
            workflow_id,
            status="running",
            phase="directing",
            message="批量导演已接单，正在准备第一张来源图",
            progress={
                **base_progress,
                "current": 0,
                "total": len(sources),
                "succeeded": 0,
                "failed": 0,
                "director_task_id": director_task_id,
                "operation_id": operation_id,
                "item_current": 0,
                "item_total": len(sources),
                "eta_text": "等待首张完成后按实际速度计算",
                "eta_basis": "warming_up",
            },
        )
        state: dict[str, Any] = {}
        while True:
            state = director_batch_status(director_task_id)
            status = str(state.get("status") or "")
            done = int(state.get("done") or 0)
            total = int(state.get("total") or len(sources))
            progress = {
                **base_progress,
                "current": done,
                "total": total,
                "succeeded": int(state.get("ok_count") or 0),
                "failed": int(state.get("fail_count") or 0),
                "director_task_id": director_task_id,
                "operation_id": operation_id,
                "item_current": done,
                "item_total": total,
            }
            if done > 0 and total > done:
                eta_seconds = (time.monotonic() - observed_started) / done * (total - done)
                progress.update(
                    {
                        "eta_seconds": int(round(eta_seconds)),
                        "eta_text": _format_eta(eta_seconds),
                        "eta_basis": "observed_rate",
                        "estimate_updated_at": _now(),
                    }
                )
            elif total and done >= total:
                progress.update(
                    {"eta_seconds": 0, "eta_text": "马上完成", "eta_basis": "observed_rate"}
                )
            else:
                progress.update(
                    {"eta_text": "等待首张完成后按实际速度计算", "eta_basis": "warming_up"}
                )
            self.store.update_task(
                workflow_id,
                status="running",
                phase="directing",
                message=str(state.get("message") or "NAI 批量导演执行中…"),
                progress=progress,
            )
            task = self.store.get_task(workflow_id, include_events=False) or {}
            if task.get("cancel_requested"):
                cancel_director_batch(director_task_id)
            if state.get("terminal") or status not in {"running", "cancelling", ""}:
                break
            await asyncio.sleep(_status_poll_delay(observed_started))

        status = str(state.get("status") or "")
        if status == "cancelled":
            raise WorkflowCancelled("批量导演已在当前请求安全返回后停止")
        if status == "unknown" or state.get("needs_review"):
            raise UnknownExternalOutcome(
                str(state.get("message") or "批量导演结果无法自动确认，请先核对生成结果")
            )
        if status == "error":
            raise RuntimeError(str(state.get("message") or "批量导演执行失败，请检查失败原因"))
        report = dict(state.get("report") or {})
        success = int(report.get("success_sources") or state.get("ok_count") or 0)
        failed = int(report.get("failed_sources") or state.get("fail_count") or 0)
        output_count = int(report.get("output_count") or 0)
        if success <= 0:
            raise RuntimeError(str(state.get("message") or "批量导演没有成功结果"))
        return {
            "ok": failed == 0,
            "partial": failed > 0,
            "completed": True,
            "tool": "batch_director",
            "director_task_id": director_task_id,
            "processed": int(state.get("done") or 0),
            "succeeded": success,
            "failed": failed,
            "generated": output_count,
            "items": list(state.get("items") or []),
            "report": report,
            "director_url": "/director",
            "gallery_url": "/generated",
            "message": str(state.get("message") or f"批量导演完成：交付 {output_count} 张结果"),
        }

    async def _execute_pipeline(
        self,
        workflow_id: str,
        action: dict[str, Any],
        operation_id: str,
    ) -> dict[str, Any]:
        """Start post-processing and keep the Butler progress/report truthful."""
        from post_pipeline import pipeline_status

        started = await self._confirmed_executor(action)
        total = int(started.get("total") or 0)
        if total <= 0:
            return started
        observed_started = time.monotonic()
        base_progress = dict(
            (self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}
        )
        self.store.update_task(
            workflow_id,
            status="running",
            phase="post_processing",
            message=str(started.get("message") or "后处理已启动"),
            progress={
                **base_progress,
                "current": 0,
                "total": total,
                "succeeded": 0,
                "failed": 0,
                "operation_id": operation_id,
                "item_current": 0,
                "item_total": total,
                "eta_text": "正在按实际处理速度计算",
                "eta_basis": "warming_up",
            },
        )
        while True:
            state = pipeline_status()
            status = str(state.get("status") or "")
            progress = {
                **base_progress,
                "current": int(state.get("done") or 0),
                "total": int(state.get("total") or total),
                "succeeded": int(state.get("ok") or 0),
                "failed": int(state.get("fail") or 0),
                "operation_id": operation_id,
            }
            done = progress["current"]
            item_total = progress["total"]
            progress.update({"item_current": done, "item_total": item_total})
            if done > 0 and item_total > done:
                eta_seconds = (time.monotonic() - observed_started) / done * (item_total - done)
                progress.update(
                    {
                        "eta_seconds": int(round(eta_seconds)),
                        "eta_text": _format_eta(eta_seconds),
                        "eta_basis": "observed_rate",
                        "estimate_updated_at": _now(),
                    }
                )
            elif item_total and done >= item_total:
                progress.update({"eta_seconds": 0, "eta_text": "马上完成", "eta_basis": "observed_rate"})
            self.store.update_task(
                workflow_id,
                status="running",
                phase="post_processing",
                message=str(state.get("message") or "后处理中…"),
                progress=progress,
            )
            if status != "running":
                break
            await asyncio.sleep(_status_poll_delay(observed_started))
        failed = int(state.get("fail") or 0)
        succeeded = int(state.get("ok") or 0)
        return {
            **started,
            "ok": failed == 0 and succeeded > 0,
            "partial": failed > 0 and succeeded > 0,
            "completed": True,
            "succeeded": succeeded,
            "failed": failed,
            "items": list(state.get("items") or []),
            "message": str(state.get("message") or f"后处理完成：成功 {succeeded}，失败 {failed}"),
        }

    async def _execute_batch(
        self,
        workflow_id: str,
        args: dict[str, Any],
        operation_id: str,
        *,
        prepare_pixiv: bool,
    ) -> dict[str, Any]:
        from nai_batch import batch_status, cancel_batch, start_batch

        refs = args.get("work_refs") or [
            {"gallery_id": args.get("gallery_id") or "site", "work_id": work_id}
            for work_id in args["work_ids"]
        ]
        for ref in refs:
            legacy._require_work(int(ref["work_id"]), ref.get("gallery_id") or "site")
        targets = legacy._batch_targets(args)
        recipe = dict(args.get("remix_recipe") or {})
        if not recipe:
            recipe = {
                "transform": {"enabled": False},
                "sanitize": {"enabled": True},
                "prompt_profile": "native",
            }
        from char_swap_config import load_config as load_char_swap_config

        started = start_batch(
            targets,
            recipe,
            force_free=bool(load_char_swap_config().get("force_free", True)),
            generate=True,
            preview_only=False,
        )
        if not started.get("ok"):
            raise RuntimeError(str(started.get("message") or "批量生成启动失败"))
        generation_task_id = str((started.get("batch") or {}).get("id") or "")
        observed_started = time.monotonic()
        base_progress = dict(
            (self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}
        )
        self.store.update_task(
            workflow_id,
            phase="generating",
            message="批量生成已启动",
            progress={
                **base_progress,
                "current": 0,
                "total": len(targets),
                "succeeded": 0,
                "failed": 0,
                "generation_task_id": generation_task_id,
                "operation_id": operation_id,
                "item_current": 0,
                "item_total": len(targets),
                "eta_text": "等待首张完成后按实际速度计算",
                "eta_basis": "warming_up",
            },
        )
        while True:
            state = batch_status(generation_task_id) if generation_task_id else batch_status()
            status = str(state.get("status") or "")
            progress = {
                **base_progress,
                "current": int(state.get("done") or 0),
                "total": int(state.get("total") or len(targets)),
                "succeeded": int(state.get("ok_count") or 0),
                "failed": int(state.get("fail_count") or 0),
                "generation_task_id": generation_task_id,
                "operation_id": operation_id,
            }
            done = progress["current"]
            item_total = progress["total"]
            progress.update({"item_current": done, "item_total": item_total})
            if done > 0 and item_total > done:
                eta_seconds = (time.monotonic() - observed_started) / done * (item_total - done)
                progress.update(
                    {
                        "eta_seconds": int(round(eta_seconds)),
                        "eta_text": _format_eta(eta_seconds),
                        "eta_basis": "observed_rate",
                        "estimate_updated_at": _now(),
                    }
                )
            elif item_total and done >= item_total:
                progress.update({"eta_seconds": 0, "eta_text": "马上完成", "eta_basis": "observed_rate"})
            self.store.update_task(
                workflow_id,
                status="running",
                phase="generating",
                message=str(state.get("message") or "批量生成中…"),
                progress=progress,
            )
            task = self.store.get_task(workflow_id, include_events=False) or {}
            if task.get("cancel_requested"):
                cancel_batch(generation_task_id) if generation_task_id else cancel_batch()
            if status not in {"running", "cancelling", ""}:
                break
            await asyncio.sleep(_status_poll_delay(observed_started))
        if status == "cancelled":
            raise WorkflowCancelled("批量生成已取消")
        ok_count = int(state.get("ok_count") or 0)
        fail_count = int(state.get("fail_count") or 0)
        if ok_count <= 0:
            raise RuntimeError(str(state.get("message") or "批量生成没有成功结果"))
        image_ids: list[str] = []
        by_work: dict[str, list[str]] = {}
        raw_items = list(state.get("items") or [])
        report_items: list[dict[str, Any]] = []
        for item in raw_items:
            report_items.append(
                {
                    key: item.get(key)
                    for key in (
                        "gallery_id", "work_id", "page_index", "ok", "skipped",
                        "image_url", "filename", "message", "summary",
                        "transform_applied", "style_replacements", "sanitize_removed", "remix",
                        "style_applied",
                    )
                    if item.get(key) not in (None, "")
                }
            )
            if not item.get("ok"):
                continue
            filename = str(item.get("filename") or "").strip()
            if not filename:
                image_url = str(item.get("image_url") or "").split("?", 1)[0]
                filename = image_url.rsplit("/", 1)[-1]
            image_id = filename.rsplit(".", 1)[0] if filename else ""
            if not image_id or image_id in image_ids:
                continue
            image_ids.append(image_id)
            gallery_id = str(item.get("gallery_id") or "site")
            work_id = str(item.get("work_id") or "standalone")
            work_key = work_id if gallery_id == "site" else f"{gallery_id}:{work_id}"
            by_work.setdefault(work_key, []).append(image_id)
        transform = recipe.get("transform") or {}
        style = recipe.get("style") or {}
        style_reference = style.get("reference") or {}
        applied_count = sum(1 for item in raw_items if item.get("ok") and item.get("transform_applied"))
        style_applied_count = sum(1 for item in raw_items if item.get("ok") and item.get("style_applied"))
        result: dict[str, Any] = {
            "ok": True,
            "tool": "batch_generate_and_prepare_pixiv" if prepare_pixiv else "batch_generate",
            "generation_task_id": generation_task_id,
            "generated": ok_count,
            "failed": fail_count,
            "image_ids": image_ids,
            "items": report_items,
            "gallery_url": "/generated",
            "quality": {
                "replacement_requested": bool(transform.get("enabled")),
                "replacement_applied": applied_count,
                "preset_id": str(transform.get("preset_id") or ""),
                "preset_label": str(transform.get("preset_label") or ""),
                "mode": str(transform.get("mode") or ""),
                "target": transform.get("target_char_index", "auto"),
                "verified_items": len(raw_items),
                "style_requested": bool(style),
                "style_applied": style_applied_count,
                "style_preset_id": str(style.get("preset_id") or ""),
                "style_preset_label": str(style.get("preset_label") or ""),
                "style_reference_id": str(style_reference.get("style_id") or ""),
                "style_reference_label": str(style_reference.get("label") or ""),
                "style_reference_source": str(style_reference.get("source") or ""),
                "style_mode": str(style.get("mode") or ""),
            },
        }
        if not prepare_pixiv:
            if transform.get("enabled") and style:
                result["message"] = (
                    f"换角、换画风并生成完成：成功 {ok_count}，失败 {fail_count}；"
                    f"已验证换角 {applied_count} 张、换画风 {style_applied_count} 张"
                )
            elif transform.get("enabled"):
                result["message"] = (
                    f"换角并生成完成：成功 {ok_count}，失败 {fail_count}；"
                    f"已验证 {applied_count} 张实际应用角色替换"
                )
            elif style:
                result["message"] = (
                    f"换画风并生成完成：成功 {ok_count}，失败 {fail_count}；"
                    f"已验证 {style_applied_count} 张实际应用画风"
                )
            else:
                result["message"] = f"批量生成完成：成功 {ok_count}，失败 {fail_count}"
            return result
        if not image_ids:
            raise RuntimeError("批量生成完成，但没有找到可交接投稿的图片")
        self.store.update_task(
            workflow_id,
            phase="preparing_pixiv",
            message=f"正在为 {len(by_work)} 个系列准备投稿草稿…",
        )
        from pixiv_launch import prepare_submission_package

        prepared_result = await asyncio.to_thread(
            prepare_submission_package,
            {
                "series": [
                    {"group_id": group_id, "image_ids": ids}
                    for group_id, ids in by_work.items()
                    if ids
                ],
                "extra": str(args.get("extra") or ""),
                "package_id": workflow_id,
            },
        )
        prepared = prepared_result.get("prepared") or prepared_result
        result.update(
            {
                "prepared": prepared,
                "submission_drafts": list(prepared.get("items") or []),
                "pixiv_url": prepared.get("pixiv_url") or f"/pixiv?prepared=1&package={workflow_id}",
                "message": "批量生成、后处理和多系列投稿草稿已完成，等待人工发布",
            }
        )
        return result

    def _skip_action_node(self, state: ButlerState) -> dict[str, Any]:
        workflow_id = state["workflow_id"]
        index = int(state.get("action_index") or 0)
        action = (state.get("actions") or [])[index]
        skipped = list(state.get("skipped_actions") or [])
        skipped.append(
            {"tool": action["tool"], "action_index": index, "reason": "user_rejected"}
        )
        legacy._write_audit(action["tool"], "cancelled", action["arguments"])
        self.store.add_event(
            workflow_id,
            "tool_skipped",
            status="running",
            phase=f"tool:{action['tool']}",
            message=f"已跳过：{action['label']}",
            detail={"tool": action["tool"]},
            event_key=f"action:{index}:skipped",
        )
        return {"skipped_actions": skipped, "approval": None}

    def _advance_node(self, state: ButlerState) -> dict[str, Any]:
        next_index = int(state.get("action_index") or 0) + 1
        actions = list(state.get("actions") or [])
        total = len(actions)
        skipped_indexes = {
            int(item.get("action_index"))
            for item in (state.get("skipped_actions") or [])
            if isinstance(item, dict) and item.get("action_index") is not None
        }
        current_progress = dict(
            (self.store.get_task(state["workflow_id"], include_events=False) or {}).get("progress")
            or {}
        )
        self.store.update_task(
            state["workflow_id"],
            phase="executing",
            message=f"已处理 {next_index}/{total} 个操作",
            progress={
                **current_progress,
                "current": next_index,
                "total": total,
                "succeeded": len(state.get("tool_results") or []),
                "failed": len(state.get("rejected_actions") or []),
                **_planned_progress(
                    actions,
                    next_index,
                    stage="finishing" if next_index >= total else "executing",
                    skipped_indexes=skipped_indexes,
                ),
            },
        )
        return {"action_index": next_index}

    def _finalize_node(self, state: ButlerState) -> dict[str, Any]:
        workflow_id = state["workflow_id"]
        cancelled = bool(state.get("cancelled")) or bool(
            (self.store.get_task(workflow_id, include_events=False) or {}).get("cancel_requested")
        )
        results = list(state.get("tool_results") or [])
        prepared = next(
            (
                item.get("prepared")
                for item in reversed(results)
                if isinstance(item, dict) and isinstance(item.get("prepared"), dict)
            ),
            None,
        )
        rejected = list(state.get("rejected_actions") or [])
        partial = any(
            isinstance(item, dict) and int(item.get("failed") or 0) > 0
            for item in results
        )
        rejected_only = bool(rejected) and not results
        status = (
            "cancelled"
            if cancelled
            else "failed"
            if rejected_only
            else "partially_succeeded"
            if partial or rejected
            else "succeeded"
        )
        phase = (
            "cancelled"
            if cancelled
            else "failed"
            if status == "failed"
            else "ready_for_upload"
            if prepared
            else "completed"
        )
        message = (
            "工作流已取消"
            if cancelled
            else (
                "计划校验未通过；没有执行任何操作"
                if status == "failed"
                else "任务部分成功；可检查失败项并重试"
                if status == "partially_succeeded"
                else "全部投稿草稿已就绪，等待人工发布"
                if prepared
                else "管家工作流已完成"
            )
        )
        finished_at = _now()
        task = self.store.get_task(workflow_id, include_events=False) or {}
        report = self._report_from_state(
            state,
            task,
            status=status,
            message=message,
            finished_at=finished_at,
        )
        result = {
            "reply": state.get("reply") or "",
            "tool_results": results,
            "rejected_actions": list(state.get("rejected_actions") or []),
            "skipped_actions": list(state.get("skipped_actions") or []),
            "prepared": prepared,
            "report": report,
        }
        report_progress = dict(task.get("progress") or {})
        completed_actions = len(results) + len(state.get("skipped_actions") or [])
        terminal_current = completed_actions if cancelled else len(state.get("actions") or [])
        report_progress.update(
            {
                "current": terminal_current,
                "total": len(state.get("actions") or []),
                "succeeded": len(results),
                "failed": len(state.get("rejected_actions") or []),
                "workflow_current": terminal_current,
                "workflow_completed": completed_actions,
                "workflow_total": len(state.get("actions") or []),
                "steps": report["steps"],
                "stage": "report_ready",
                "current_label": "交付报告已生成",
                "next_label": "无，任务已结束",
                "eta_seconds": 0,
                "eta_text": "已完成",
                "eta_basis": "completed",
                "estimate_updated_at": finished_at,
            }
        )
        self.store.update_task(
            workflow_id,
            status=status,
            phase=phase,
            message=message,
            result=result,
            progress=report_progress,
            pending=None,
            confirmation_id="",
            finished_at=finished_at,
        )
        self.store.add_event(
            workflow_id,
            "finished",
            status=status,
            phase=phase,
            message=message,
            event_key="workflow:finished",
        )
        return {"status": status, "phase": phase, "result": result}

    async def submit(
        self,
        message: str,
        history: Any = None,
        *,
        image: Any = None,
        preplanned: dict[str, Any] | None = None,
        retry_of: str = "",
        run_in_background: bool = False,
    ) -> dict[str, Any]:
        await self.start()
        secure_plan = None
        if preplanned is None and image in (None, "", {}):
            secure_plan = await asyncio.to_thread(_secure_local_configuration_plan, message)
        clean_message = redact_text(message, limit=legacy.MAX_MESSAGE_CHARS)
        if not clean_message:
            raise ValueError("请输入要交给管家的任务")
        clean_history = redact_history(history, maximum=legacy.MAX_HISTORY_ITEMS)
        safe_plan = copy.deepcopy(preplanned) if isinstance(preplanned, dict) else secure_plan
        workflow_id = secrets.token_hex(12)
        image_name = ""
        if image not in (None, "", {}):
            if not isinstance(image, dict):
                raise ValueError("图片附件格式不正确")
            if safe_plan is None:
                with usage_scope(workflow_id):
                    safe_plan = await asyncio.to_thread(
                        self._planner,
                        clean_message,
                        clean_history,
                        image,
                    )
            raw_name = str(image.get("name") or "图片").replace("\\", "/").rsplit("/", 1)[-1]
            image_name = redact_text(raw_name, limit=120) or "图片"
        stored_message = (
            f"🖼 已附图片：{image_name}\n{clean_message}" if image_name else clean_message
        )
        self.store.add_message("user", stored_message, workflow_id=workflow_id)
        input_data: dict[str, Any] = {"message": clean_message, "history": clean_history}
        if safe_plan is not None:
            input_data["preplanned"] = safe_plan
        if image_name:
            input_data["attachment"] = {"kind": "image", "name": image_name}
        self.store.create_task(
            workflow_id,
            thread_id=workflow_id,
            kind="butler_workflow",
            title=clean_message[:80],
            input_data=input_data,
            retry_of=retry_of,
        )
        self.store.update_task(
            workflow_id,
            progress={
                "current": 0,
                "total": 1,
                "succeeded": 0,
                "failed": 0,
                "stage": "planning",
                "current_label": "正在制定执行计划",
                "next_label": "按计划逐步执行",
                "eta_text": "正在估算",
                "eta_basis": "planning",
                "workflow_current": 1,
                "workflow_completed": 0,
                "workflow_total": 1,
                "steps": [
                    {
                        "index": 1,
                        "tool": "planner",
                        "label": "理解任务并制定执行计划",
                        "status": "running",
                    }
                ],
            },
        )
        graph_input = {
            "workflow_id": workflow_id,
            "message": clean_message,
            "history": clean_history,
            **({"preplanned": safe_plan} if safe_plan is not None else {}),
        }
        if run_in_background:
            background = asyncio.create_task(self._drive_with_report(workflow_id, graph_input))
            self._background.add(background)
            background.add_done_callback(self._background_done)
            task = self.store.get_task(workflow_id) or {}
            response = self._response(
                safe_plan
                or {
                    "reply": (
                        "收到啦，这件事交给我。我正在把它拆成可执行步骤，"
                        "进度会实时更新；完成后我会把交付报告送到这里。"
                    )
                },
                task,
            )
            reply = redact_text(response.get("reply") or "", limit=legacy.MAX_MESSAGE_CHARS)
            if reply:
                self.store.add_message("assistant", reply, workflow_id=workflow_id)
            return response

        response = await self._drive(workflow_id, graph_input)
        reply = redact_text(response.get("reply") or "", limit=legacy.MAX_MESSAGE_CHARS)
        if reply:
            self.store.add_message("assistant", reply, workflow_id=workflow_id)
        return response

    async def record_answer(
        self,
        message: str,
        reply: str,
        *,
        answer_id: str,
        image: Any = None,
        model: str = "local",
    ) -> dict[str, Any]:
        """Persist a chat answer without creating or executing a task."""

        await self.start()
        clean_message = redact_text(message, limit=legacy.MAX_MESSAGE_CHARS)
        clean_reply = redact_text(reply, limit=legacy.MAX_MESSAGE_CHARS)
        if not clean_message or not clean_reply:
            raise ValueError("问题或回答不能为空")
        image_name = ""
        if image not in (None, "", {}):
            if not isinstance(image, dict):
                raise ValueError("图片附件格式不正确")
            raw_name = str(image.get("name") or "图片").replace("\\", "/").rsplit("/", 1)[-1]
            image_name = redact_text(raw_name, limit=120) or "图片"
        stored_message = f"🖼 已附图片：{image_name}\n{clean_message}" if image_name else clean_message
        self.store.add_message("user", stored_message, workflow_id=answer_id)
        self.store.add_message("assistant", clean_reply, workflow_id=answer_id)
        return {
            "ok": True,
            "engine": "answer",
            "answer_only": True,
            "answer_id": answer_id,
            "workflow_id": "",
            "reply": clean_reply,
            "model": model,
            "tool_results": [],
            "pending_actions": [],
            "rejected_actions": [],
            "task": None,
            "usage": usage_summary(workflow_id=answer_id),
        }

    async def _drive_with_report(self, workflow_id: str, graph_input: Any) -> dict[str, Any]:
        try:
            response = await self._drive(workflow_id, graph_input)
        except Exception:
            task = self.store.get_task(workflow_id, include_events=False) or {}
            task = self._ensure_task_report(workflow_id, task)
            self.store.add_assistant_message_once(workflow_id, self._completion_chat(task))
            return {"ok": False, "workflow_id": workflow_id, "task": task}
        task = self.store.get_task(workflow_id, include_events=False) or response.get("task") or {}
        if task.get("terminal"):
            task = self._ensure_task_report(workflow_id, task)
            self.store.add_assistant_message_once(workflow_id, self._completion_chat(task))
        return response

    async def confirm(self, confirmation_id: str, *, approve: bool) -> dict[str, Any]:
        await self.start()
        task = self.store.get_by_confirmation(redact_text(confirmation_id, limit=200))
        if not task or task.get("status") != "awaiting_confirmation":
            raise ValueError("确认已失效或不存在，请重新下达指令")
        workflow_id = str(task["id"])
        self.store.update_task(
            workflow_id,
            status="accepted" if approve else "running",
            phase="accepted" if approve else "skipping",
            message="已确认，准备执行" if approve else "已拒绝这个操作",
            confirmation_id="",
            pending=None,
        )
        self.store.add_event(
            workflow_id,
            "confirmation",
            status="accepted" if approve else "running",
            phase="accepted" if approve else "skipping",
            message="用户确认执行" if approve else "用户拒绝执行",
            detail={"approved": bool(approve)},
            event_key=f"confirmation:{task.get('pending_action', {}).get('action_index', 0)}",
        )
        pending = task.get("pending_action") or {}
        if approve and pending.get("tool") in {
            "generate_image",
            "batch_generate",
            "batch_generate_and_prepare_pixiv",
            "prepare_pixiv_submission",
        }:
            background = asyncio.create_task(
                self._drive_with_report(workflow_id, Command(resume={"approve": True}))
            )
            self._background.add(background)
            background.add_done_callback(self._background_done)
            accepted = self.store.get_task(workflow_id) or {}
            return {
                "ok": True,
                "engine": "langgraph",
                "workflow_id": workflow_id,
                "reply": "收到确认啦，我现在就开始执行。你可以继续做别的，我会实时更新进度，完成后把报告交给你。",
                "tool_results": [],
                "pending_actions": [],
                "rejected_actions": [],
                "cancelled": False,
                "result": None,
                "task": accepted,
            }
        response = await self._drive(workflow_id, Command(resume={"approve": bool(approve)}))
        completed = self.store.get_task(workflow_id, include_events=False) or response.get("task") or {}
        if completed.get("terminal"):
            completed = self._ensure_task_report(workflow_id, completed)
            self.store.add_assistant_message_once(workflow_id, self._completion_chat(completed))
        return response

    def _background_done(self, task: asyncio.Task[Any]) -> None:
        self._background.discard(task)
        if task.cancelled():
            return
        try:
            task.exception()
        except (asyncio.CancelledError, Exception):
            pass

    async def cancel(self, workflow_id: str) -> dict[str, Any]:
        await self.start()
        task = self.store.get_task(workflow_id)
        if not task:
            raise ValueError("管家任务不存在")
        if task["status"] in TERMINAL_STATUSES:
            return {"ok": True, "task": task, "message": "任务已经结束"}
        self.store.update_task(
            workflow_id,
            cancel_requested=True,
            message="正在取消任务…",
        )
        self.store.add_event(
            workflow_id,
            "cancel_requested",
            status=task["status"],
            phase=task["phase"],
            message="用户请求取消工作流",
            event_key="workflow:cancel_requested",
        )
        if task["status"] == "awaiting_confirmation":
            return await self._drive(
                workflow_id,
                Command(resume={"approve": False, "cancel_workflow": True}),
            )
        progress = task.get("progress") or {}
        generation_task_id = str(progress.get("generation_task_id") or "")
        if generation_task_id:
            try:
                from nai_batch import cancel_batch

                cancel_batch(generation_task_id)
            except Exception:
                pass
        return {"ok": True, "task": self.store.get_task(workflow_id), "message": "取消请求已提交"}

    async def resume(self, workflow_id: str) -> dict[str, Any]:
        await self.start()
        task = self.store.get_task(workflow_id)
        if not task:
            raise ValueError("管家任务不存在")
        if task["status"] != "paused":
            raise ValueError("只有安全暂停的任务可以继续；结果未知的任务必须核对后重试")
        self.store.update_task(
            workflow_id,
            status="running",
            phase="resuming",
            message="正在从最近检查点继续…",
        )
        return await self._drive(workflow_id, None)

    async def retry(self, workflow_id: str) -> dict[str, Any]:
        await self.start()
        task = self.store.get_task(workflow_id)
        if not task:
            raise ValueError("管家任务不存在")
        if task["status"] not in {"failed", "partially_succeeded", "unknown", "cancelled"}:
            raise ValueError("只有失败、部分成功、结果未知或已取消的任务可以重试")
        input_data = task.get("input") or {}
        message = str(input_data.get("message") or "")
        preplanned = input_data.get("preplanned")
        if not isinstance(preplanned, dict):
            preplanned = _local_read_only_plan(message)
        return await self.submit(
            message,
            input_data.get("history"),
            preplanned=preplanned,
            retry_of=workflow_id,
        )

    async def _drive(self, workflow_id: str, graph_input: Any) -> dict[str, Any]:
        await self.start()
        lock = self._run_locks.setdefault(workflow_id, asyncio.Lock())
        async with lock:
            try:
                config = {"configurable": {"thread_id": workflow_id}}
                with usage_scope(workflow_id):
                    result = await self._graph.ainvoke(graph_input, config=config)
                interrupts = result.get("__interrupt__") or []
                if interrupts:
                    interruption = interrupts[0]
                    payload = copy.deepcopy(interruption.value)
                    progress = dict(
                        (self.store.get_task(workflow_id, include_events=False) or {}).get("progress") or {}
                    )
                    step_index = int(payload.get("action_index") or 0)
                    steps = [dict(item) for item in (progress.get("steps") or [])]
                    if 0 <= step_index < len(steps):
                        steps[step_index]["status"] = "waiting"
                    progress.update(
                        {
                            "steps": steps,
                            "stage": "awaiting_confirmation",
                            "current_label": str(payload.get("label") or payload.get("summary") or "等待你的确认"),
                            "eta_text": "确认后继续估算",
                            "eta_basis": "waiting_for_user",
                            "estimate_updated_at": _now(),
                        }
                    )
                    pending = {
                        **payload,
                        "confirmation_id": str(interruption.id),
                        "expires_in": 0,
                    }
                    self.store.update_task(
                        workflow_id,
                        status="awaiting_confirmation",
                        phase="awaiting_confirmation",
                        message=str(payload.get("summary") or "等待确认"),
                        pending=pending,
                        confirmation_id=str(interruption.id),
                        progress=progress,
                    )
                    self.store.add_event(
                        workflow_id,
                        "awaiting_confirmation",
                        status="awaiting_confirmation",
                        phase="awaiting_confirmation",
                        message=str(payload.get("summary") or "等待确认"),
                        detail={"tool": payload.get("tool"), "operation_id": payload.get("operation_id")},
                        event_key=f"action:{payload.get('action_index', 0)}:awaiting_confirmation",
                    )
                task = self.store.get_task(workflow_id) or {}
                return self._response(result, task)
            except UnknownExternalOutcome as exc:
                task = self.store.update_task(
                    workflow_id,
                    status="unknown",
                    phase="needs_review",
                    message="外部操作结果未知，已停止自动重放",
                    error=redact_text(exc, limit=500),
                    finished_at=_now(),
                )
                self.store.add_event(
                    workflow_id,
                    "unknown",
                    status="unknown",
                    phase="needs_review",
                    message=str(exc),
                    event_key="workflow:unknown",
                )
                return self._response({}, task)
            except Exception as exc:
                task = self.store.update_task(
                    workflow_id,
                    status="failed",
                    phase="failed",
                    message="管家工作流失败",
                    error=redact_text(legacy.public_error(exc), limit=500),
                    finished_at=_now(),
                )
                self.store.add_event(
                    workflow_id,
                    "failed",
                    status="failed",
                    phase="failed",
                    message=redact_text(legacy.public_error(exc), limit=500),
                    event_key="workflow:failed",
                )
                raise

    @staticmethod
    def _response(state: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
        pending = task.get("pending_action")
        tool_results = state.get("tool_results") or (task.get("result") or {}).get("tool_results") or []
        return {
            "ok": True,
            "engine": "langgraph",
            "workflow_id": task.get("id") or state.get("workflow_id") or "",
            "reply": state.get("reply")
            or (task.get("result") or {}).get("reply")
            or task.get("message")
            or "",
            "model": state.get("model") or "",
            "tool_results": tool_results,
            "pending_actions": [pending] if pending else [],
            "rejected_actions": state.get("rejected_actions")
            or (task.get("result") or {}).get("rejected_actions")
            or [],
            "cancelled": task.get("status") == "cancelled",
            "result": tool_results[-1] if tool_results else None,
            "task": task,
        }

    def status(self) -> dict[str, Any]:
        tasks = self.store.list_tasks(limit=20)
        return {
            "engine": "langgraph",
            "started": self._started,
            "state_path": self.state_path.name,
            "checkpoint_path": self.checkpoint_path.name,
            "checkpoint_encrypted": False,
            "secrets_in_checkpoint": False,
            "retention_days": int(os.environ.get("BUTLER_RETENTION_DAYS", "30")),
            "recovery": dict(self._recovery),
            "active": sum(1 for task in tasks if not task.get("terminal")),
        }


_STATE_PATH = data_dir() / "butler_state.db"
_RUNTIME = ButlerWorkflowRuntime(_STATE_PATH)
atexit.register(_RUNTIME.store.close)


async def start_butler_runtime() -> None:
    await _RUNTIME.start()


async def close_butler_runtime() -> None:
    await _RUNTIME.close()


async def submit_butler_chat(
    message: str,
    history: Any = None,
    image: Any = None,
    intent: str = "",
    comparison: Any = None,
) -> dict[str, Any]:
    preplanned = None
    clean_intent = str(intent or "").strip().lower()
    if not clean_intent:
        from software_help import answer_software_question, looks_like_help_question, looks_like_question

        if looks_like_question(message):
            answer_id = f"answer-{secrets.token_hex(8)}"
            if image in (None, "", {}) and looks_like_help_question(message):
                help_answer = answer_software_question(message)
                sources = [
                    str(source).strip()
                    for source in list(help_answer.get("sources") or [])[:3]
                    if str(source).strip()
                ]
                source_line = f"\n依据：{'、'.join(sources)}" if sources else ""
                reply = f"{help_answer['answer']}{source_line}\n入口：{help_answer['page']}"
                model = "local"
            else:
                with usage_scope(answer_id):
                    answer = await asyncio.to_thread(legacy.request_answer, message, history, image)
                reply = str(answer.get("reply") or "").strip() or "我暂时没能整理出可靠回答，请换一种问法。"
                model = str(legacy.ai_status().get("model") or "")
            return await _RUNTIME.record_answer(
                message,
                reply,
                answer_id=answer_id,
                image=image,
                model=model,
            )
    if clean_intent == "gallery_compare":
        action = legacy.normalize_action(
            {
                "tool": "compare_gallery_candidates",
                "arguments": {
                    "question": str(message or ""),
                    "candidates": comparison,
                },
            }
        )
        preplanned = {
            "reply": (
                "候选已经替你固定好了。我会只发送这 2–4 张低清图做一次比较，"
                "不会调用 NAI；完成后把选择理由和不足写进任务报告。"
            ),
            "actions": [action],
        }
    elif clean_intent == "gallery_audit":
        explicit_vision = any(
            token in str(message or "")
            for token in ("识图", "看图", "视觉", "画面评价", "评价图片", "分析图片")
        )
        preplanned = {
            "reply": (
                "我会用上游视觉模型检查最近图库的图片和状态；若上游拒绝，会直接告诉你。"
                if explicit_vision
                else "我会只读检查最近图库的本地状态，不调用识图，不会删除或重做任何内容。"
            ),
            "actions": [
                {
                    "tool": "audit_gallery",
                    "arguments": {
                        "sort": "new",
                        "time_range": "month",
                        "limit": 6,
                        "use_vision": explicit_vision,
                    },
                }
            ],
        }
    elif image in (None, "", {}) and not clean_intent:
        preplanned = _local_read_only_plan(message)
    engine = str(os.environ.get("BUTLER_ENGINE", "langgraph") or "langgraph").lower()
    if engine == "legacy":
        return await asyncio.to_thread(legacy.run_chat, message, history, image, preplanned)
    return await _RUNTIME.submit(
        message,
        history,
        image=image,
        preplanned=preplanned,
        run_in_background=True,
    )


def _knowledge_rebuild_plan() -> dict[str, Any]:
    return {
        "reply": (
            "好呀，我会逐份检查程序内置的可信说明，只增量更新有变化的内容。"
            "全程本地执行、不调用模型；进度和完成报告会放进任务中心。"
        ),
        "actions": [{"tool": "rebuild_knowledge_catalog", "arguments": {}}],
    }


async def submit_knowledge_rebuild() -> dict[str, Any]:
    """Submit the settings-page rebuild through the canonical Butler Workflow Interface."""

    return await _RUNTIME.submit(
        "增量更新本地软件知识库",
        preplanned=_knowledge_rebuild_plan(),
        run_in_background=True,
    )


def _local_read_only_plan(message: Any) -> dict[str, Any] | None:
    """Route a few unambiguous local reads without paying model latency or tokens."""
    source = " ".join(str(message or "").strip().lower().split())
    text = source.replace(" ", "")
    if not text or len(text) > 160:
        return None
    contextual = ("这个", "那个", "它", "刚才", "上一个", "第一个", "最后一个")
    # Natural multi-character commands are deterministic when they contain an
    # exact work id and explicit local preset names.  Build the same
    # replace_multi recipe used by the manual slot tool, then stop at the
    # normal generation confirmation gate (no model, vision, or NAI here).
    if any(token in text for token in ("换成", "替换成", "改成")) and "生成" in text and not any(
        token in text for token in ("不要生成", "不生成", "无需生成", "别生成")
    ):
        work_match = re.search(r"(?<!\d)(\d{6,})(?!\d)", text)
        names_match = re.search(
            r"(?:换成|替换成|改成)(.{2,120}?)(?:的?oc)?(?:后)?(?:批量)?生成",
            text,
        )
        if work_match and names_match:
            raw_names = re.split(r"和|与|、|及|,|，", names_match.group(1))
            names = [
                re.sub(r"(?:的)?oc$", "", name, flags=re.I).strip("的：:,，。")
                for name in raw_names
            ]
            names = [name for name in names if name]
            if 2 <= len(names) <= 6:
                copies_match = re.search(r"(?:生成|出)(\d{1,2})张", text)
                copies = int(copies_match.group(1)) if copies_match else 1
                if 1 <= copies <= 20:
                    replacements = [{"name": name} for name in names]
                    return {
                        "reply": (
                            f"收到，我会把作品 {work_match.group(1)} 的角色槽分别换成"
                            f"{'、'.join(names)}，先复用手动换角链做本地预检；"
                            "预检通过后等你确认，确认前不会调用 NAI。"
                        ),
                        "actions": [
                            {
                                "tool": "batch_generate",
                                "arguments": {
                                    "gallery_id": "site",
                                    "work_ids": [int(work_match.group(1))],
                                    "page_index": 0,
                                    "all_pages": any(
                                        token in text for token in ("全部图片", "所有图片", "每一页", "整套")
                                    ),
                                    "copies_per_work": copies,
                                    "character": {
                                        "mode": "replace_multi",
                                        "replacements": replacements,
                                    },
                                },
                            }
                        ],
                    }
    # A precise source-work + reference-name replacement can be prepared by
    # the same local Remix recipe as the manual tool.  It is safe to bypass
    # the planner only when the user explicitly asks for a draft and negates
    # generation; actual generation still uses the confirmation workflow.
    if "角色资料" in text and "草稿" in text:
        negative_generation = any(token in text for token in ("不要生成", "不生成", "无需生成", "别生成"))
        swap_match = re.search(
            r"(?:把)?(网站|aitag|法典|codex|q群|qq群|qq)?作品[#：:]?(\d+)"
            r"(?:第(\d+)页)?(?:的)?(女性|女|男性|男)?角色(?:换成|替换成|改成)"
            r"(?:nai)?角色资料库?(?:里|里的|中|中的)?(.{1,80}?)(?:，|,|。|$)",
            text,
        )
        if swap_match and negative_generation:
            gallery_id = {
                "": "site", "网站": "site", "aitag": "site", "法典": "codex",
                "codex": "codex", "q群": "qqgroup", "qq群": "qqgroup", "qq": "qqgroup",
            }[swap_match.group(1) or ""]
            gender_text = swap_match.group(4) or ""
            gender = "female" if gender_text in {"女性", "女"} else "male" if gender_text in {"男性", "男"} else ""
            character: dict[str, Any] = {
                "reference_name": swap_match.group(5).strip("的：:,，。"),
                "mode": f"replace_{gender}" if gender else "replace",
                "preserve_action": any(token in text for token in ("保持动作", "保留动作")),
            }
            if gender:
                character["gender"] = gender
            arguments = {
                "gallery_id": gallery_id,
                "work_id": int(swap_match.group(2)),
                "page_index": max(0, int(swap_match.group(3) or 1) - 1),
                "character": character,
            }
            return {
                "reply": (
                    f"好呀，我会用本地资料“{character['reference_name']}”替换作品角色，"
                    "复用手动换角链只准备工作台草稿；不调用模型，也不会生成图片。"
                ),
                "actions": [{"tool": "prepare_remix", "arguments": arguments}],
            }
    if "角色资料" in text and "生成" in text and not any(
        token in text for token in ("不要生成", "不生成", "无需生成", "别生成")
    ):
        batch_match = re.search(
            r"(?:用)?(?:nai)?角色资料库?(?:里|里的|中|中的)?(.{1,80}?)"
            r"(?:替换|换掉|换到)(网站|aitag|法典|codex|q群|qq群|qq)?作品[#：:]?(\d+)"
            r"(?:第(\d+)页)?(?:的)?(女性|女|男性|男)?角色",
            text,
        )
        copies_match = re.search(r"(?:每个作品|每个|每件)?(?:生成|出)(\d{1,2})张", text)
        if batch_match and copies_match:
            copies = int(copies_match.group(1))
            if 1 <= copies <= 20:
                gallery_id = {
                    "": "site", "网站": "site", "aitag": "site", "法典": "codex",
                    "codex": "codex", "q群": "qqgroup", "qq群": "qqgroup", "qq": "qqgroup",
                }[batch_match.group(2) or ""]
                gender_text = batch_match.group(5) or ""
                gender = "female" if gender_text in {"女性", "女"} else "male" if gender_text in {"男性", "男"} else ""
                character: dict[str, Any] = {
                    "reference_name": batch_match.group(1).strip("的：:,，。"),
                    "mode": f"replace_{gender}" if gender else "replace",
                    "preserve_action": any(token in text for token in ("保持动作", "保留动作")),
                }
                if gender:
                    character["gender"] = gender
                work_id = int(batch_match.group(3))
                arguments = {
                    "gallery_id": gallery_id,
                    "work_ids": [work_id],
                    "page_index": max(0, int(batch_match.group(4) or 1) - 1),
                    "copies_per_work": copies,
                    "character": character,
                }
                return {
                    "reply": (
                        f"收到，我会先在本地用资料“{character['reference_name']}”预检 {copies} 张换角任务，"
                        "不调用规划模型；预检通过后仍会等你确认，确认前不会调用 NAI。"
                    ),
                    "actions": [{"tool": "batch_generate", "arguments": arguments}],
                }
    # A precise reference-card handoff is deterministic: resolve a local name,
    # choose a 1-based user slot, and prepare a draft.  Route it before the
    # generic mutation guard so “不要生成图片” does not force an expensive LLM
    # plan.  Ambiguous or generation-requesting wording still falls through.
    if "角色资料" in text and "草稿" in text:
        negative_generation = any(token in text for token in ("不要生成", "不生成", "无需生成", "别生成"))
        actual_generation = "生成" in text and not negative_generation
        name_match = re.search(
            r"(?:nai)?角色资料库?(?:里|里的|中|中的)?(.{1,80}?)(?:，|,)?(?:放到|放进|应用到)",
            text,
        )
        slot_match = re.search(r"角色?槽位?([1-6])", text)
        if name_match and slot_match and not actual_generation:
            name = name_match.group(1).strip("的：:,，")
            prompt_match = re.search(r"准备(?:一个|一份)?(.{0,80}?)(?:工作台|studio)?草稿", text)
            prompt = (prompt_match.group(1) if prompt_match else "").strip("的：:,，")
            arguments: dict[str, Any] = {
                "name": name,
                "slot_index": int(slot_match.group(1)) - 1,
            }
            if prompt:
                arguments["prompt"] = prompt
            return {
                "reply": (
                    f"好呀，我会把本地角色资料“{name}”放进槽位 {int(slot_match.group(1))}，"
                    "只准备工作台草稿，不调用模型，也不会开始生成。"
                ),
                "actions": [{"tool": "prepare_character_reference", "arguments": arguments}],
            }
    if any(name in text for name in ("本地知识库", "软件知识库", "帮助知识库")) and any(
        verb in text for verb in ("更新", "重建", "刷新", "增量")
    ):
        return _knowledge_rebuild_plan()
    mutations = (
        "删除", "移除", "清空", "取消", "加入", "添加", "收藏这个", "启动", "停止",
        "重启", "修改", "配置", "帮我生成", "开始生成", "生成图片", "生成一张", "生成多张",
        "换角", "换画风", "投稿", "上传", "识图", "看图",
    )
    if any(token in text for token in contextual) or any(token in text for token in mutations):
        return None
    routes: list[tuple[tuple[str, ...], str, str]] = [
        (("待生成队列", "待生成清单", "查看待生成"), "list_queue", "我会直接读取本地待生成队列，不调用模型。"),
        (("我的收藏", "收藏列表", "查看收藏"), "list_favorites", "我会直接读取本地收藏，不调用模型。"),
        (("生成结果", "生成成果"), "list_generated", "我会直接读取本地生成成果，不调用模型。"),
        (("采集状态", "爬虫状态"), "inspect_crawler", "我会直接读取本地三图库采集状态，不调用模型。"),
        (("你能做什么", "你会什么", "可用功能", "可用操作", "助手能力"), "inspect_capabilities", "我会直接列出已经接入的本地能力和安全边界，不调用模型。"),
        (("系统运行健康", "运行健康", "服务状态", "系统状态"), "inspect_operations", "我会直接读取本地服务与采集健康状态，不调用模型。"),
        (("生产状态", "生产进度", "生成任务状态", "后处理状态", "投稿准备状态"), "inspect_production", "我会直接读取本地生成、后处理和投稿准备状态，不调用模型。"),
    ]
    for signals, tool, reply in routes:
        if any(signal in text for signal in signals):
            return {"reply": reply, "actions": [{"tool": tool, "arguments": {}}]}

    if "角色资料" in text and any(
        signal in text
        for signal in (
            "有哪些系列",
            "哪些系列",
            "有什么系列",
            "有哪些来源",
            "哪些来源",
            "性别分布",
            "导入状态",
            "资料库状态",
        )
    ):
        return {
            "reply": "我会直接查看本地 NAI 角色资料库的系列、来源和分布，不调用模型。",
            "actions": [{"tool": "inspect_reference_catalog", "arguments": {}}],
        }

    reference_match = re.fullmatch(
        r"(?:搜索|查找|查询|在)?(?:nai)?角色资料(?:库)?(?:里|中)?(?:搜索|查找|查询)?[：:]?(.{1,120})",
        text,
    )
    if reference_match:
        query = reference_match.group(1).strip("：:")
        if query:
            return {
                "reply": f"我会直接在本地 NAI 角色资料库搜索“{query}”，不调用模型。",
                "actions": [{
                    "tool": "search_character_references",
                    "arguments": {"q": query, "limit": 12},
                }],
            }

    style_reference_match = re.fullmatch(
        r"(?:搜索|查找|查询|在)?(?:nai)?(?:画风|画师)资料(?:库)?(?:里|中)?(?:搜索|查找|查询)?[：:]?(.{1,120})",
        text,
    )
    if style_reference_match:
        query = style_reference_match.group(1).strip("：:")
        if query:
            return {
                "reply": f"我会直接在本地 NAI 画风资料库搜索“{query}”，不调用模型。",
                "actions": [{
                    "tool": "search_style_references",
                    "arguments": {"q": query, "limit": 12},
                }],
            }

    search_match = re.fullmatch(r"(?:搜索图库|图库搜索|在图库(?:里|中)?找)[：:]?(.{1,120})", text)
    if search_match:
        query = search_match.group(1).strip("：:")
        if query:
            return {
                "reply": f"我会直接在本地图库搜索“{query}”，不调用模型。",
                "actions": [{"tool": "search_gallery", "arguments": {"q": query, "limit": 12}}],
            }

    work_match = re.fullmatch(
        r"查看(网站|aitag|法典|codex|q群|qq群|qq)作品[#：:]?(\d+)(?:第(\d+)页)?",
        text,
    )
    if work_match:
        gallery_id = {
            "网站": "site", "aitag": "site", "法典": "codex", "codex": "codex",
            "q群": "qqgroup", "qq群": "qqgroup", "qq": "qqgroup",
        }[work_match.group(1)]
        page_number = max(1, int(work_match.group(3) or 1))
        return {
            "reply": "我会按三图库统一身份直接读取这件本地作品，不调用模型。",
            "actions": [{
                "tool": "inspect_work",
                "arguments": {
                    "gallery_id": gallery_id,
                    "work_id": int(work_match.group(2)),
                    "page_index": page_number - 1,
                },
            }],
        }
    return None


async def confirm_butler_action(confirmation_id: str, *, approve: bool) -> dict[str, Any]:
    engine = str(os.environ.get("BUTLER_ENGINE", "langgraph") or "langgraph").lower()
    if engine == "legacy":
        return await legacy.confirm_action(confirmation_id, approve=approve)
    return await _RUNTIME.confirm(confirmation_id, approve=approve)


async def cancel_butler_task(workflow_id: str) -> dict[str, Any]:
    return await _RUNTIME.cancel(workflow_id)


async def resume_butler_task(workflow_id: str) -> dict[str, Any]:
    return await _RUNTIME.resume(workflow_id)


async def retry_butler_task(workflow_id: str) -> dict[str, Any]:
    return await _RUNTIME.retry(workflow_id)


def list_butler_tasks(*, limit: int = 30, status: str = "") -> dict[str, Any]:
    try:
        return {"ok": True, "tasks": _RUNTIME.store.list_tasks(limit=limit, status=status)}
    finally:
        if not _RUNTIME._started:
            _RUNTIME.store.close()


def get_butler_task(workflow_id: str) -> dict[str, Any]:
    try:
        task = _RUNTIME.store.get_task(workflow_id)
        if not task:
            raise ValueError("管家任务不存在")
        return {"ok": True, "task": task}
    finally:
        if not _RUNTIME._started:
            _RUNTIME.store.close()


def butler_task_revision() -> int:
    return _RUNTIME.store.task_revision()


def wait_for_butler_task_change(after_revision: int, *, timeout: float = 15.0) -> int:
    return _RUNTIME.store.wait_for_task_change(after_revision, timeout=timeout)


def list_butler_messages(*, limit: int = 60, before_id: int | None = None) -> dict[str, Any]:
    count = max(1, min(int(limit), 100))
    try:
        rows = _RUNTIME.store.list_messages(limit=count + 1, before_id=before_id)
        has_more = len(rows) > count
        if has_more:
            rows = rows[-count:]
        return {"ok": True, "messages": rows, "has_more": has_more}
    finally:
        if not _RUNTIME._started:
            _RUNTIME.store.close()


def clear_butler_messages() -> dict[str, Any]:
    try:
        return {"ok": True, "deleted": _RUNTIME.store.clear_messages()}
    finally:
        if not _RUNTIME._started:
            _RUNTIME.store.close()


def workflow_runtime_status() -> dict[str, Any]:
    try:
        return _RUNTIME.status()
    finally:
        if not _RUNTIME._started:
            _RUNTIME.store.close()

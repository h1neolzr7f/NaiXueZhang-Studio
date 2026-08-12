"""Small durable task/event/receipt store owned by the Butler context."""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .redaction import redact_text, redact_value


TERMINAL_STATUSES = frozenset(
    {"succeeded", "partially_succeeded", "failed", "cancelled", "unknown"}
)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _load(value: Any, fallback: Any) -> Any:
    if value in (None, ""):
        return fallback
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return fallback


class ButlerTaskStore:
    """Interface over a dedicated SQLite database, separate from the gallery DB."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._change = threading.Condition()
        self._revision = 0
        self._conn: sqlite3.Connection | None = None

    def _publish_change(self) -> int:
        """Wake task-stream listeners without polling SQLite."""
        with self._change:
            self._revision += 1
            self._change.notify_all()
            return self._revision

    def task_revision(self) -> int:
        with self._change:
            return self._revision

    def wait_for_task_change(self, after_revision: int, *, timeout: float = 15.0) -> int:
        target = max(0, int(after_revision or 0))
        with self._change:
            self._change.wait_for(
                lambda: self._revision > target,
                timeout=max(0.0, float(timeout)),
            )
            return self._revision

    def start(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=20)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS butler_tasks (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL UNIQUE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    message TEXT NOT NULL,
                    progress_json TEXT NOT NULL DEFAULT '{}',
                    input_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    pending_json TEXT,
                    confirmation_id TEXT UNIQUE,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    retry_of TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    finished_at TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_butler_tasks_updated
                    ON butler_tasks(updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_butler_tasks_status
                    ON butler_tasks(status, updated_at DESC);

                CREATE TABLE IF NOT EXISTS butler_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    event_key TEXT,
                    time TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    message TEXT NOT NULL,
                    detail_json TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY(task_id) REFERENCES butler_tasks(id) ON DELETE CASCADE,
                    UNIQUE(task_id, event_key)
                );
                CREATE INDEX IF NOT EXISTS idx_butler_events_task
                    ON butler_events(task_id, id);

                CREATE TABLE IF NOT EXISTS butler_receipts (
                    operation_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    action_index INTEGER NOT NULL,
                    tool TEXT NOT NULL,
                    arguments_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(task_id) REFERENCES butler_tasks(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_butler_receipts_task
                    ON butler_receipts(task_id, action_index);

                CREATE TABLE IF NOT EXISTS butler_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                    content TEXT NOT NULL,
                    workflow_id TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_butler_messages_created
                    ON butler_messages(id DESC);
                """
            )
            self._redact_legacy_secrets(conn)
            conn.commit()
            self._conn = conn

    @staticmethod
    def _redacted_json(value: Any) -> Any:
        if value is None:
            return None
        raw = str(value)
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return redact_text(raw, limit=max(4000, len(raw)))
        return _json(redact_value(parsed))

    def _redact_legacy_secrets(self, conn: sqlite3.Connection) -> None:
        """One cheap startup pass removes credentials written by older builds."""

        table_specs = (
            (
                "butler_messages",
                "id",
                ("content",),
                (),
            ),
            (
                "butler_tasks",
                "id",
                ("title", "message", "error"),
                ("input_json", "result_json", "pending_json"),
            ),
            (
                "butler_events",
                "id",
                ("message",),
                ("detail_json",),
            ),
            (
                "butler_receipts",
                "operation_id",
                ("error",),
                ("result_json",),
            ),
        )
        for table, key_column, text_columns, json_columns in table_specs:
            columns = (*text_columns, *json_columns)
            rows = conn.execute(
                f"SELECT {key_column}, {', '.join(columns)} FROM {table}"
            ).fetchall()
            for row in rows:
                updates: dict[str, Any] = {}
                for column in text_columns:
                    raw = str(row[column] or "")
                    clean = redact_text(raw, limit=max(4000, len(raw)))
                    if clean != raw:
                        updates[column] = clean
                for column in json_columns:
                    raw = row[column]
                    clean = self._redacted_json(raw)
                    if clean != raw:
                        updates[column] = clean
                if updates:
                    assignments = ", ".join(f"{column} = ?" for column in updates)
                    conn.execute(
                        f"UPDATE {table} SET {assignments} WHERE {key_column} = ?",
                        (*updates.values(), row[key_column]),
                    )

    def close(self) -> None:
        with self._lock:
            conn, self._conn = self._conn, None
            if conn is not None:
                conn.close()

    def _connection(self) -> sqlite3.Connection:
        self.start()
        assert self._conn is not None
        return self._conn

    def create_task(
        self,
        task_id: str,
        *,
        thread_id: str,
        kind: str,
        title: str,
        input_data: dict[str, Any],
        retry_of: str = "",
    ) -> dict[str, Any]:
        now = _now()
        with self._lock:
            conn = self._connection()
            conn.execute(
                """
                INSERT INTO butler_tasks(
                    id, thread_id, kind, title, status, phase, message,
                    input_json, retry_of, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'planned', 'planning', ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    thread_id,
                    kind,
                    title,
                    "正在理解任务并制定工具计划…",
                    _json(input_data),
                    retry_of or None,
                    now,
                    now,
                ),
            )
            conn.commit()
        self.add_event(
            task_id,
            "created",
            status="planned",
            phase="planning",
            message="已创建管家工作流",
            event_key="workflow:created",
        )
        return self.get_task(task_id, include_events=True) or {}

    def add_message(self, role: str, content: str, *, workflow_id: str = "") -> dict[str, Any]:
        role_key = str(role or "").strip().lower()
        if role_key not in {"user", "assistant"}:
            raise ValueError("聊天角色只能是 user 或 assistant")
        raw_text = str(content or "").strip()
        text = redact_text(raw_text, limit=max(4000, len(raw_text)))
        if not text:
            raise ValueError("聊天内容不能为空")
        created_at = _now()
        with self._lock:
            cursor = self._connection().execute(
                "INSERT INTO butler_messages(role, content, workflow_id, created_at) VALUES (?, ?, ?, ?)",
                (role_key, text, str(workflow_id or ""), created_at),
            )
            self._connection().commit()
            message_id = int(cursor.lastrowid)
        return {
            "id": message_id,
            "role": role_key,
            "content": text,
            "workflow_id": str(workflow_id or ""),
            "created_at": created_at,
        }

    def add_assistant_message_once(
        self, workflow_id: str, content: str, *, event_key: str = "workflow:completion_report"
    ) -> bool:
        """Atomically append one persisted assistant report for a workflow."""
        raw_text = str(content or "").strip()
        text = redact_text(raw_text, limit=max(4000, len(raw_text)))
        if not text:
            return False
        now = _now()
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO butler_events(
                    task_id, event_key, time, event_type, status, phase, message, detail_json
                ) VALUES (?, ?, ?, 'report_posted', 'reported', 'report', ?, '{}')
                """,
                (workflow_id, event_key, now, text[:1000]),
            )
            if int(cursor.rowcount or 0) == 0:
                conn.commit()
                return False
            conn.execute(
                "INSERT INTO butler_messages(role, content, workflow_id, created_at) VALUES ('assistant', ?, ?, ?)",
                (text, workflow_id, now),
            )
            conn.commit()
        self._publish_change()
        return True

    def list_messages(
        self,
        *,
        limit: int = 60,
        before_id: int | None = None,
    ) -> list[dict[str, Any]]:
        count = max(1, min(int(limit), 200))
        with self._lock:
            conn = self._connection()
            if before_id is None:
                rows = conn.execute(
                    "SELECT id, role, content, workflow_id, created_at "
                    "FROM butler_messages ORDER BY id DESC LIMIT ?",
                    (count,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, role, content, workflow_id, created_at "
                    "FROM butler_messages WHERE id < ? ORDER BY id DESC LIMIT ?",
                    (int(before_id), count),
                ).fetchall()
            sanitized: list[dict[str, Any]] = []
            changed = False
            for row in reversed(rows):
                raw_content = str(row["content"] or "")
                content = redact_text(raw_content, limit=max(4000, len(raw_content)))
                if content != raw_content:
                    conn.execute(
                        "UPDATE butler_messages SET content = ? WHERE id = ?",
                        (content, int(row["id"])),
                    )
                    changed = True
                sanitized.append(
                    {
                        "id": int(row["id"]),
                        "role": row["role"],
                        "content": content,
                        "workflow_id": row["workflow_id"] or "",
                        "created_at": row["created_at"],
                    }
                )
            if changed:
                conn.commit()
        return sanitized

    def clear_messages(self) -> int:
        with self._lock:
            conn = self._connection()
            cursor = conn.execute("DELETE FROM butler_messages")
            conn.commit()
            return max(0, int(cursor.rowcount))

    def update_task(self, task_id: str, **updates: Any) -> dict[str, Any]:
        allowed = {
            "status",
            "phase",
            "message",
            "error",
            "confirmation_id",
            "cancel_requested",
            "started_at",
            "finished_at",
        }
        json_fields = {
            "progress": "progress_json",
            "result": "result_json",
            "pending": "pending_json",
        }
        assignments: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            if key in allowed:
                assignments.append(f"{key} = ?")
                if key == "cancel_requested":
                    values.append(int(bool(value)))
                elif key == "confirmation_id":
                    # SQLite UNIQUE columns may contain many NULL values but only one
                    # empty string. Clearing a completed task must therefore use NULL.
                    values.append(str(value or "").strip() or None)
                else:
                    values.append(value)
            elif key in json_fields:
                assignments.append(f"{json_fields[key]} = ?")
                values.append(None if value is None else _json(value))
        if not assignments:
            return self.get_task(task_id, include_events=True) or {}
        assignments.append("updated_at = ?")
        values.append(_now())
        values.append(task_id)
        with self._lock:
            conn = self._connection()
            conn.execute(
                f"UPDATE butler_tasks SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            conn.commit()
        self._publish_change()
        return self.get_task(task_id, include_events=True) or {}

    def add_event(
        self,
        task_id: str,
        event_type: str,
        *,
        status: str,
        phase: str,
        message: str,
        detail: dict[str, Any] | None = None,
        event_key: str | None = None,
    ) -> None:
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO butler_events(
                    task_id, event_key, time, event_type, status, phase, message, detail_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    event_key,
                    _now(),
                    event_type,
                    status,
                    phase,
                    str(message or "")[:1000],
                    _json(detail or {}),
                ),
            )
            conn.commit()
        if int(cursor.rowcount or 0) > 0:
            self._publish_change()

    def get_task(self, task_id: str, *, include_events: bool = True) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connection()
            row = conn.execute("SELECT * FROM butler_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            task = self._task_dict(row)
            if include_events:
                events = conn.execute(
                    "SELECT * FROM butler_events WHERE task_id = ? ORDER BY id",
                    (task_id,),
                ).fetchall()
                task["events"] = [self._event_dict(item) for item in events]
            receipts = conn.execute(
                """SELECT operation_id, action_index, tool, status, result_json, error,
                          created_at, updated_at
                   FROM butler_receipts WHERE task_id = ? ORDER BY action_index""",
                (task_id,),
            ).fetchall()
            task["receipts"] = [
                {
                    "operation_id": item["operation_id"],
                    "action_index": int(item["action_index"]),
                    "tool": item["tool"],
                    "status": item["status"],
                    "result": _load(item["result_json"], None),
                    "error": item["error"] or "",
                    "created_at": item["created_at"],
                    "updated_at": item["updated_at"],
                }
                for item in receipts
            ]
            return task

    def get_by_confirmation(self, confirmation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection().execute(
                "SELECT id FROM butler_tasks WHERE confirmation_id = ?",
                (confirmation_id,),
            ).fetchone()
        return self.get_task(str(row["id"])) if row else None

    def list_tasks(self, *, limit: int = 30, status: str = "") -> list[dict[str, Any]]:
        count = max(1, min(int(limit), 100))
        with self._lock:
            conn = self._connection()
            if status:
                rows = conn.execute(
                    "SELECT * FROM butler_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                    (status, count),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM butler_tasks ORDER BY updated_at DESC LIMIT ?", (count,)
                ).fetchall()
            return [self._task_dict(row) for row in rows]

    def get_receipt(self, operation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection().execute(
                "SELECT * FROM butler_receipts WHERE operation_id = ?", (operation_id,)
            ).fetchone()
        if row is None:
            return None
        return {
            "operation_id": row["operation_id"],
            "task_id": row["task_id"],
            "action_index": int(row["action_index"]),
            "tool": row["tool"],
            "arguments_hash": row["arguments_hash"],
            "status": row["status"],
            "result": _load(row["result_json"], None),
            "error": row["error"] or "",
        }

    def put_receipt(
        self,
        operation_id: str,
        *,
        task_id: str,
        action_index: int,
        tool: str,
        arguments_hash: str,
        status: str,
        result: Any = None,
        error: str = "",
    ) -> dict[str, Any]:
        now = _now()
        with self._lock:
            conn = self._connection()
            conn.execute(
                """
                INSERT INTO butler_receipts(
                    operation_id, task_id, action_index, tool, arguments_hash,
                    status, result_json, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(operation_id) DO UPDATE SET
                    status=excluded.status,
                    result_json=excluded.result_json,
                    error=excluded.error,
                    updated_at=excluded.updated_at
                """,
                (
                    operation_id,
                    task_id,
                    int(action_index),
                    tool,
                    arguments_hash,
                    status,
                    None if result is None else _json(result),
                    str(error or "")[:1000],
                    now,
                    now,
                ),
            )
            conn.commit()
        self._publish_change()
        return self.get_receipt(operation_id) or {}

    def recover_interrupted(self) -> dict[str, int]:
        """Pause safe work and quarantine uncertain side effects after process restart."""
        now = _now()
        with self._lock:
            conn = self._connection()
            uncertain = {
                str(row["task_id"])
                for row in conn.execute(
                    "SELECT DISTINCT task_id FROM butler_receipts WHERE status = 'started'"
                ).fetchall()
            }
            for task_id in uncertain:
                conn.execute(
                    "UPDATE butler_receipts SET status='unknown', updated_at=? "
                    "WHERE task_id=? AND status='started'",
                    (now, task_id),
                )
                conn.execute(
                    """UPDATE butler_tasks SET status='unknown', phase='needs_review',
                       message='上次运行在外部操作期间中断，结果未知；请核对后重试',
                       error='external outcome unknown after restart', updated_at=?
                       WHERE id=? AND status IN ('accepted','running')""",
                    (now, task_id),
                )
            paused = conn.execute(
                """UPDATE butler_tasks SET status='paused', phase='paused',
                   message='应用重启后已安全暂停，可继续运行', updated_at=?
                   WHERE status IN ('accepted','running')""",
                (now,),
            ).rowcount
            conn.commit()
        if paused or uncertain:
            self._publish_change()
        return {"paused": int(paused or 0), "unknown": len(uncertain)}

    def prune(self, *, retention_days: int = 30) -> int:
        threshold = (datetime.now() - timedelta(days=max(1, retention_days))).isoformat(
            timespec="seconds"
        )
        with self._lock:
            conn = self._connection()
            cursor = conn.execute(
                "DELETE FROM butler_tasks WHERE finished_at <> '' AND finished_at < ?",
                (threshold,),
            )
            conn.commit()
            deleted = int(cursor.rowcount or 0)
        if deleted:
            self._publish_change()
        return deleted

    @staticmethod
    def _task_dict(row: sqlite3.Row) -> dict[str, Any]:
        status = str(row["status"] or "")
        return {
            "id": row["id"],
            "workflow_id": row["id"],
            "thread_id": row["thread_id"],
            "kind": row["kind"],
            "title": row["title"],
            "status": status,
            "phase": row["phase"],
            "message": row["message"],
            "progress": _load(row["progress_json"], {}),
            "input": _load(row["input_json"], {}),
            "result": _load(row["result_json"], None),
            "error": row["error"] or "",
            "pending_action": _load(row["pending_json"], None),
            "confirmation_id": row["confirmation_id"] or "",
            "cancel_requested": bool(row["cancel_requested"]),
            "retry_of": row["retry_of"] or "",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "started_at": row["started_at"],
            "finished_at": row["finished_at"],
            "terminal": status in TERMINAL_STATUSES,
            "capabilities": {
                "cancel": status in {"planned", "awaiting_confirmation", "accepted", "running", "paused"},
                "retry": status in {"failed", "partially_succeeded", "unknown", "cancelled"},
                "resume": status in {"paused"},
            },
        }

    @staticmethod
    def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": int(row["id"]),
            "time": row["time"],
            "type": row["event_type"],
            "status": row["status"],
            "phase": row["phase"],
            "message": row["message"],
            "detail": _load(row["detail_json"], {}),
        }

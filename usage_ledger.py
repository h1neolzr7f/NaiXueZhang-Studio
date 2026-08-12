"""Durable, provider-neutral usage accounting for AI and image generation."""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from paths import data_dir


_WORKFLOW_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "usage_workflow_id", default=""
)
_TASK_ID: contextvars.ContextVar[str] = contextvars.ContextVar(
    "usage_task_id", default=""
)


@contextmanager
def usage_scope(workflow_id: str = "", task_id: str = "") -> Iterator[None]:
    workflow_token = _WORKFLOW_ID.set(str(workflow_id or ""))
    task_token = _TASK_ID.set(str(task_id or ""))
    try:
        yield
    finally:
        _TASK_ID.reset(task_token)
        _WORKFLOW_ID.reset(workflow_token)


class UsageLedger:
    """Small SQLite ledger safe to call from async workers and thread helpers."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._init_lock = threading.Lock()
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _ensure_schema(self) -> None:
        if self._initialized:
            return
        with self._init_lock:
            if self._initialized:
                return
            with closing(self._connect()) as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS usage_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        created_at TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        provider TEXT NOT NULL,
                        model TEXT NOT NULL,
                        workflow_id TEXT NOT NULL DEFAULT '',
                        task_id TEXT NOT NULL DEFAULT '',
                        input_tokens INTEGER NOT NULL DEFAULT 0,
                        output_tokens INTEGER NOT NULL DEFAULT 0,
                        cached_tokens INTEGER NOT NULL DEFAULT 0,
                        images INTEGER NOT NULL DEFAULT 0,
                        anlas_spent REAL,
                        cost_source TEXT NOT NULL DEFAULT 'unknown',
                        duration_ms INTEGER NOT NULL DEFAULT 0,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_usage_workflow ON usage_events(workflow_id, id)"
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_usage_created ON usage_events(created_at, id)"
                )
                connection.commit()
            self._initialized = True

    def record(
        self,
        *,
        kind: str,
        provider: str,
        model: str = "",
        workflow_id: str = "",
        task_id: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        images: int = 0,
        anlas_spent: float | None = None,
        cost_source: str = "unknown",
        duration_ms: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        self._ensure_schema()
        resolved_workflow = str(workflow_id or _WORKFLOW_ID.get() or "")
        resolved_task = str(task_id or _TASK_ID.get() or "")
        safe_anlas = None if anlas_spent is None else max(0.0, float(anlas_spent))
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT INTO usage_events (
                    created_at, kind, provider, model, workflow_id, task_id,
                    input_tokens, output_tokens, cached_tokens, images,
                    anlas_spent, cost_source, duration_ms, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now().isoformat(timespec="seconds"),
                    str(kind or "unknown")[:50],
                    str(provider or "unknown")[:100],
                    str(model or "")[:200],
                    resolved_workflow[:100],
                    resolved_task[:100],
                    max(0, int(input_tokens or 0)),
                    max(0, int(output_tokens or 0)),
                    max(0, int(cached_tokens or 0)),
                    max(0, int(images or 0)),
                    safe_anlas,
                    str(cost_source or "unknown")[:40],
                    max(0, int(duration_ms or 0)),
                    json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )
            connection.commit()
            return int(cursor.lastrowid)

    def summary(self, *, workflow_id: str = "") -> dict[str, Any]:
        self._ensure_schema()
        where = "WHERE workflow_id = ?" if workflow_id else ""
        params: tuple[Any, ...] = (str(workflow_id),) if workflow_id else ()
        with closing(self._connect()) as connection:
            row = connection.execute(
                f"""
                SELECT
                    COUNT(*) AS calls,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens,
                    COALESCE(SUM(cached_tokens), 0) AS cached_tokens,
                    COALESCE(SUM(images), 0) AS images,
                    COALESCE(SUM(anlas_spent), 0) AS anlas_spent,
                    COALESCE(SUM(
                        CASE WHEN kind = 'image_generation'
                                  AND provider = 'novelai'
                                  AND images > 0
                                  AND anlas_spent IS NULL
                             THEN images ELSE 0 END
                    ), 0) AS anlas_unknown_images,
                    COALESCE(SUM(duration_ms), 0) AS duration_ms
                FROM usage_events {where}
                """,
                params,
            ).fetchone()
        total_tokens = int(row["input_tokens"]) + int(row["output_tokens"])
        unknown_images = int(row["anlas_unknown_images"])
        return {
            "calls": int(row["calls"]),
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "total_tokens": total_tokens,
            "cached_tokens": int(row["cached_tokens"]),
            "images": int(row["images"]),
            "anlas_spent": float(row["anlas_spent"]),
            "anlas_unknown_images": unknown_images,
            "anlas_complete": unknown_images == 0,
            "duration_ms": int(row["duration_ms"]),
            "workflow_id": str(workflow_id or ""),
        }

    def recent(self, *, limit: int = 50, workflow_id: str = "") -> list[dict[str, Any]]:
        self._ensure_schema()
        where = "WHERE workflow_id = ?" if workflow_id else ""
        params: tuple[Any, ...] = (str(workflow_id),) if workflow_id else ()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT * FROM usage_events {where} ORDER BY id DESC LIMIT ?",
                (*params, max(1, min(int(limit), 500))),
            ).fetchall()
        return [
            {
                key: value
                for key, value in dict(row).items()
                if key != "metadata_json"
            }
            | {"metadata": json.loads(row["metadata_json"] or "{}")}
            for row in rows
        ]


LEDGER = UsageLedger(data_dir() / "usage_ledger.local.db")


def record_usage(**kwargs: Any) -> int:
    if os.environ.get("GALLERY_NONINTERACTIVE") == "1":
        return 0
    try:
        return LEDGER.record(**kwargs)
    except (OSError, TypeError, ValueError, sqlite3.Error):
        # Accounting must never turn an already charged provider response into
        # an apparent failure that invites a duplicate retry.
        return 0


def usage_summary(*, workflow_id: str = "") -> dict[str, Any]:
    try:
        return LEDGER.summary(workflow_id=workflow_id)
    except (OSError, TypeError, ValueError, sqlite3.Error):
        return {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "images": 0,
            "anlas_spent": 0.0,
            "anlas_unknown_images": 0,
            "anlas_complete": False,
            "duration_ms": 0,
            "workflow_id": str(workflow_id or ""),
            "available": False,
        }

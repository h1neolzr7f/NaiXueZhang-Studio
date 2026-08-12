from __future__ import annotations

from collections.abc import Callable
from typing import Any


class RuntimeResources:
    """Own the application's process-wide runtime resource lifecycle."""

    def __init__(
        self,
        *,
        db: Any,
        watchdog: Any,
        http_client: Any,
        start_stats_scheduler: Callable[[], None],
        stop_stats_scheduler: Callable[[], None],
        extra_close: Callable[[], None] | None = None,
    ) -> None:
        self._db = db
        self._watchdog = watchdog
        self._http_client = http_client
        self._start_stats_scheduler = start_stats_scheduler
        self._stop_stats_scheduler = stop_stats_scheduler
        self._extra_close = extra_close
        self._started = False
        self._closed = False
        self._close_started = False
        self._closed_actions: set[str] = set()

    def start(self) -> None:
        if self._close_started:
            raise RuntimeError("runtime resources are already closed")
        if self._started:
            return
        watchdog_start_attempted = False
        scheduler_start_attempted = False
        try:
            watchdog_start_attempted = True
            self._watchdog.start()
            scheduler_start_attempted = True
            self._start_stats_scheduler()
        except Exception as start_error:
            rollback_failures: list[tuple[str, Exception]] = []
            rollback_actions: list[tuple[str, bool, Callable[[], None]]] = [
                ("scheduler.stop", scheduler_start_attempted, self._stop_stats_scheduler),
                ("watchdog.stop", watchdog_start_attempted, self._watchdog.stop),
            ]
            for name, attempted, action in rollback_actions:
                if not attempted:
                    continue
                try:
                    action()
                except Exception as rollback_error:
                    rollback_failures.append((name, rollback_error))
            if rollback_failures:
                details = "; ".join(
                    f"{name}: {error}" for name, error in rollback_failures
                )
                raise RuntimeError(
                    f"runtime resource start failed: {start_error}; "
                    f"rollback failed: {details}"
                ) from start_error
            raise
        self._started = True

    def close(self) -> None:
        if self._closed:
            return
        self._close_started = True
        self._started = False
        failures: list[tuple[str, Exception]] = []
        close_actions: list[tuple[str, Callable[[], None]]] = [
            ("scheduler.stop", self._stop_stats_scheduler),
            ("watchdog.stop", self._watchdog.stop),
            ("http.close", self._http_client.close),
            ("db.close", self._db.close),
        ]
        if self._extra_close is not None:
            close_actions.append(("gallery_dbs.close", self._extra_close))
        for name, action in close_actions:
            if name in self._closed_actions:
                continue
            try:
                action()
            except Exception as exc:
                failures.append((name, exc))
            else:
                self._closed_actions.add(name)
        if failures:
            details = "; ".join(f"{name}: {exc}" for name, exc in failures)
            raise RuntimeError(f"runtime resource close failed: {details}") from failures[0][1]
        self._closed = True

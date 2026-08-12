"""Process-local TTL cache for read-heavy gallery endpoints."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_STORE: dict[str, tuple[float, Any]] = {}


def cached(key: str, ttl_sec: float, factory: Callable[[], T]) -> T:
    now = time.monotonic()
    hit = _STORE.get(key)
    if hit and now - hit[0] < ttl_sec:
        return hit[1]
    value = factory()
    _STORE[key] = (now, value)
    return value


def invalidate(key: str) -> None:
    _STORE.pop(key, None)


def clear_all() -> None:
    _STORE.clear()
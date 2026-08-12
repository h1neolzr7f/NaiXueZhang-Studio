"""Persist zero-generation Studio drafts so they survive browser cache clears."""

from __future__ import annotations

import json
import math
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from paths import data_dir

_DRAFT_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_MAX_DRAFTS = 64
_MAX_PAYLOAD_BYTES = 1536 * 1024  # multi-page online drafts need more headroom
_DEFAULT_TTL_SECONDS = 30 * 24 * 60 * 60
_STORE_LOCK = threading.RLock()


def studio_drafts_root(base: Path | str | None = None) -> Path:
    root = Path(base) if base is not None else data_dir()
    return Path(root) / "studio_drafts"


def new_draft_id() -> str:
    return secrets.token_hex(8)


def validate_draft_id(draft_id: str) -> str:
    value = str(draft_id or "").strip().casefold()
    if not _DRAFT_ID_RE.fullmatch(value):
        raise ValueError("Invalid studio draft id")
    return value


def _record_path(root: Path, draft_id: str) -> Path:
    return root / f"{draft_id}.json"


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > _MAX_PAYLOAD_BYTES:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _validated_ttl(ttl_seconds: float) -> float:
    try:
        value = float(ttl_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Studio draft TTL must be a positive finite number") from exc
    if not math.isfinite(value) or value <= 0:
        raise ValueError("Studio draft TTL must be a positive finite number")
    return value


def _record_updated_at(record: dict[str, Any] | None) -> float | None:
    if not record:
        return None
    try:
        updated = float(record.get("updated_at"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(updated) or updated <= 0:
        return None
    return updated


def _record_is_valid(path: Path, record: dict[str, Any] | None) -> bool:
    if not record or not isinstance(record.get("payload"), dict):
        return False
    try:
        path_id = validate_draft_id(path.stem)
    except ValueError:
        return False
    return str(record.get("draft_id") or "") == path_id


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        # A failed cleanup must not make an otherwise valid draft unsavable.
        pass


def _prune(root: Path, *, ttl_seconds: float, now: float | None = None) -> None:
    ttl = _validated_ttl(ttl_seconds)
    current_time = time.time() if now is None else float(now)
    records: list[tuple[float, Path]] = []
    for path in root.glob("*.json"):
        if path.name == "index.json":
            continue
        record = _read_record(path)
        updated = _record_updated_at(record)
        if (
            updated is None
            or not _record_is_valid(path, record)
            or current_time - updated >= ttl
        ):
            _unlink_quietly(path)
            continue
        records.append((updated, path))
    records.sort(key=lambda item: (item[0], item[1].name), reverse=True)
    for _, path in records[_MAX_DRAFTS:]:
        _unlink_quietly(path)


def save_studio_draft(
    compiled: dict[str, Any],
    *,
    source: str = "aitag-online",
    root: Path | str | None = None,
    draft_id: str | None = None,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any]:
    """Save a compiled draft package. Never calls a generation provider."""

    if not isinstance(compiled, dict) or not isinstance(compiled.get("draft"), dict):
        raise ValueError("compiled payload must include a draft object")
    # Persist multi-page drafts (capped) so Studio/cross-tab restore keeps all pages.
    pages_out: list[dict[str, Any]] = []
    pages_capped = False
    raw_pages = compiled.get("pages")
    failed_pages = list(compiled.get("failed_pages") or compiled.get("errors") or [])
    if isinstance(raw_pages, list):
        if len(raw_pages) > 24:
            pages_capped = True
            failed_pages = list(failed_pages) + [
                {
                    "reason": "pages_capped",
                    "limit": 24,
                    "total": len(raw_pages),
                    "message": f"multi-page draft capped at 24 of {len(raw_pages)} pages",
                }
            ]
        for item in raw_pages[:24]:
            if not isinstance(item, dict):
                continue
            page_draft = item.get("draft")
            if not isinstance(page_draft, dict):
                continue
            pages_out.append(
                {
                    "image_index": item.get("image_index"),
                    "slot_index": item.get("slot_index"),
                    "slot_indexes": item.get("slot_indexes") or [],
                    "draft": page_draft,
                }
            )
    package = {
        "draft": compiled["draft"],
        "recipe": compiled.get("recipe"),
        "card": compiled.get("card"),
        "candidates": compiled.get("candidates") or [],
        "work_id": compiled.get("work_id"),
        "image_id": compiled.get("image_id"),
        "image_index": compiled.get("image_index"),
        "slot_index": compiled.get("slot_index"),
        "pages": pages_out,
        "partial": bool(compiled.get("partial")) or pages_capped,
        "failed_pages": failed_pages,
    }
    ttl = _validated_ttl(ttl_seconds)
    record_id = validate_draft_id(draft_id) if draft_id else new_draft_id()
    now = time.time()
    record = {
        "draft_id": record_id,
        "source": str(source or "aitag-online"),
        "created_at": now,
        "updated_at": now,
        "generation_calls": 0,
        "payload": package,
    }
    serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(serialized.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Studio draft payload is too large to persist")

    store_root = studio_drafts_root(root)
    with _STORE_LOCK:
        store_root.mkdir(parents=True, exist_ok=True)
        atomic_write_text(_record_path(store_root, record_id), serialized)
        _prune(store_root, ttl_seconds=ttl, now=now)
    return record


def get_studio_draft(
    draft_id: str,
    *,
    root: Path | str | None = None,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any] | None:
    ttl = _validated_ttl(ttl_seconds)
    try:
        record_id = validate_draft_id(draft_id)
    except ValueError:
        return None
    path = _record_path(studio_drafts_root(root), record_id)
    with _STORE_LOCK:
        if not path.is_file():
            return None
        record = _read_record(path)
        updated = _record_updated_at(record)
        if (
            not _record_is_valid(path, record)
            or updated is None
            or time.time() - updated >= ttl
        ):
            _unlink_quietly(path)
            return None
        return record


def get_latest_studio_draft(
    *,
    source: str | None = "aitag-online",
    root: Path | str | None = None,
    ttl_seconds: float = _DEFAULT_TTL_SECONDS,
) -> dict[str, Any] | None:
    ttl = _validated_ttl(ttl_seconds)
    store_root = studio_drafts_root(root)
    if not store_root.is_dir():
        return None
    with _STORE_LOCK:
        _prune(store_root, ttl_seconds=ttl)
        best: dict[str, Any] | None = None
        best_key = (-1.0, "")
        wanted = str(source or "").strip()
        for path in store_root.glob("*.json"):
            if path.name == "index.json":
                continue
            record = _read_record(path)
            updated = _record_updated_at(record)
            if not _record_is_valid(path, record) or updated is None:
                continue
            if wanted and str(record.get("source") or "") != wanted:
                continue
            key = (updated, path.name)
            if key >= best_key:
                best = record
                best_key = key
        return best


def public_draft_response(record: dict[str, Any]) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    draft_id = str(record.get("draft_id") or "")
    return {
        "ok": True,
        "draft_id": draft_id,
        "draft": draft,
        "recipe": payload.get("recipe"),
        "card": payload.get("card"),
        "candidates": payload.get("candidates") or [],
        "work_id": payload.get("work_id"),
        "image_id": payload.get("image_id"),
        "image_index": payload.get("image_index"),
        "slot_index": payload.get("slot_index"),
        "pages": payload.get("pages") or [],
        "partial": bool(payload.get("partial")),
        "failed_pages": payload.get("failed_pages") or [],
        "source": str(record.get("source") or "aitag-online"),
        "provider": "aitag-online",
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "studio_url": f"/studio?aitag=1&remix=1&draft={draft_id}",
        "generation_calls": 0,
        "message": "已从服务端恢复 Studio 草稿；点击生成前不会调用 NAI。",
    }


__all__ = [
    "get_latest_studio_draft",
    "get_studio_draft",
    "new_draft_id",
    "public_draft_response",
    "save_studio_draft",
    "studio_drafts_root",
    "validate_draft_id",
]

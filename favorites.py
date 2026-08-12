"""本地作品收藏（JSON 持久化）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from work_refs import WorkRef, WorkSelectionStore, public_work_id

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
FAV_PATH = DATA_DIR / "favorites.json"


def _store() -> WorkSelectionStore:
    return WorkSelectionStore(FAV_PATH, kind="favorites")


def list_refs() -> list[dict[str, Any]]:
    return _store().list_items()


def list_ids() -> list[int]:
    # Legacy numeric consumers are site-gallery only.  Composite callers use
    # ``list_refs`` so a QQ/Codex ID can never be mistaken for a site work.
    return [
        int(item["work_id"])
        for item in list_refs()
        if item.get("gallery_id") == "site"
    ]


def has(work_id: int | str, gallery_id: str = "site") -> bool:
    return _store().has(WorkRef.parse(work_id, gallery_id))


def add(
    work_id: int | str,
    gallery_id: str = "site",
    **metadata: Any,
) -> dict[str, Any]:
    result = _store().add(WorkRef.parse(work_id, gallery_id), **metadata)
    return {**result, "favorited": True}


def remove(work_id: int | str, gallery_id: str = "site") -> dict[str, Any]:
    result = _store().remove(WorkRef.parse(work_id, gallery_id))
    return {**result, "favorited": False}


def toggle(
    work_id: int | str,
    gallery_id: str = "site",
    **metadata: Any,
) -> dict[str, Any]:
    if has(work_id, gallery_id):
        return remove(work_id, gallery_id)
    return add(work_id, gallery_id, **metadata)


def summary() -> dict[str, Any]:
    refs = list_refs()
    return {
        "ok": True,
        "count": len(refs),
        "ids": [public_work_id(item["work_id"]) for item in refs],
        "refs": refs,
        "updated_at": _store().load().get("updated_at") or "",
    }

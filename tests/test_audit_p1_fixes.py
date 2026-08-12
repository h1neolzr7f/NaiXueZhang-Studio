"""Regression tests for 2026-08-12 remaining P1 fixes."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from generated_gallery import _public_source_thumb_url, get_cached_source_info
from nai_api import _record_token_failure


def test_cover_only_source_no_longer_inverts_flag() -> None:
    """Regression: cover_only=False if cover_only else cover_only always False."""
    import inspect
    import crawler as crawler_mod

    src = inspect.getsource(crawler_mod.Crawler._fetch_cover_impl)
    assert "cover_only=bool(cover_only)" in src
    assert "False if cover_only else cover_only" not in src


def test_public_source_thumb_url_normalizes_roots() -> None:
    assert _public_source_thumb_url("NAI/1/a.webp") == "/data/images/NAI/1/a.webp"
    assert _public_source_thumb_url("images/NAI/1/a.webp") == "/data/images/NAI/1/a.webp"
    assert _public_source_thumb_url("data/images/NAI/1/a.webp") == "/data/images/NAI/1/a.webp"
    assert (
        _public_source_thumb_url("cat/1.webp", gallery_id="codex")
        == "/data/gallery/codex/cat/1.webp"
    )


def test_get_cached_source_info_uses_images_prefix() -> None:
    def getter(_wid: int):
        return {
            "work": {"title": "t"},
            "images": [{"local_path": "NAI/9/x.webp"}],
        }

    info = get_cached_source_info(9, getter, gallery_id="site")
    assert info["thumb"] == "/data/images/NAI/9/x.webp"
    assert info["title"] == "t"


def test_token_not_removed_on_generic_banned_word_in_unrelated_context() -> None:
    # Bare "banned" alone without account/or-banned phrasing should not hard-delete.
    entry = {"id": "t3", "provider": "novelai", "enabled": True, "token": "z"}
    removed = _record_token_failure(entry, "request was not banned by rate limiter, retry")
    # "banned" alone removed from permanent list; this message has "banned" but not
    # "account banned" / "or banned" / forbidden — must not remove.
    assert removed is False


def test_token_still_removes_on_account_banned_phrase() -> None:
    entry = {"id": "t4", "provider": "xianyun", "enabled": True, "token": "z"}
    with patch("nai_api._remove_token_entry", return_value=True) as rem:
        removed = _record_token_failure(
            entry,
            "Xianyun account forbidden or banned: 403 account banned",
        )
    assert removed is True
    rem.assert_called_once()


def test_draft_store_marks_partial_when_pages_capped(tmp_path: Path) -> None:
    from aitag_core import draft_store

    pages = []
    for i in range(30):
        pages.append(
            {
                "image_index": i,
                "draft": {
                    "pageIndex": i,
                    "texts": {"prompt": f"p{i}"},
                    "comment": {"prompt": f"p{i}"},
                },
            }
        )
    compiled = {
        "draft": pages[0]["draft"],
        "pages": pages,
        "partial": False,
        "failed_pages": [],
        "work_id": "1",
        "image_index": 0,
    }
    saved = draft_store.save_studio_draft(
        compiled, source="aitag-online", root=tmp_path
    )
    payload = saved.get("payload") or {}
    assert payload.get("partial") is True
    assert len(payload.get("pages") or []) == 24
    reasons = [
        f.get("reason")
        for f in (payload.get("failed_pages") or [])
        if isinstance(f, dict)
    ]
    assert "pages_capped" in reasons

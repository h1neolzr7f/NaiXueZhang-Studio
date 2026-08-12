from __future__ import annotations

import json
from pathlib import Path

from pixiv_nai_crawler import list_quarantined, retry_quarantined, save_task


def _seed_quarantine(root: Path) -> None:
    save_task({"enabled": False, "scopes": [{"id": "s", "type": "search", "query": "x"}]}, root=root)
    state_path = root / "data" / "pixiv_nai_state.local.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {
        "version": 1,
        "scopes": {},
        "failures": {},
        "quarantine": {
            "nai:111": {
                "failure_count": 3,
                "failure_kind": "permanent",
                "reason": "download_failed",
                "quarantined_at": "2026-08-03T00:00:00+00:00",
            },
            "nai:222": {
                "failure_count": 4,
                "failure_kind": "retryable",
                "reason": "storage_quota_exceeded",
                "quarantined_at": "2026-08-03T01:00:00+00:00",
            },
        },
    }
    state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def test_list_quarantined_surfaces_records_without_clearing(tmp_path: Path) -> None:
    _seed_quarantine(tmp_path)
    items = list_quarantined(root=tmp_path)
    assert len(items) == 2
    reasons = {item["reason"] for item in items}
    assert reasons == {"download_failed", "storage_quota_exceeded"}
    keys = {item["key"] for item in items}
    assert keys == {"nai:111", "nai:222"}
    # list must not mutate state
    again = list_quarantined(root=tmp_path)
    assert len(again) == 2


def test_retry_quarantined_clears_and_reports_count(tmp_path: Path) -> None:
    _seed_quarantine(tmp_path)
    result = retry_quarantined(root=tmp_path)
    assert result["cleared"] == 2
    assert list_quarantined(root=tmp_path) == []
    # state file is persisted for the next crawl cycle
    state = json.loads((tmp_path / "data" / "pixiv_nai_state.local.json").read_text(encoding="utf-8"))
    assert state["quarantine"] == {}


def test_retry_quarantined_on_empty_state_is_safe(tmp_path: Path) -> None:
    save_task({"enabled": False, "scopes": [{"id": "s", "type": "search", "query": "x"}]}, root=tmp_path)
    result = retry_quarantined(root=tmp_path)
    assert result == {"cleared": 0, "items": []}

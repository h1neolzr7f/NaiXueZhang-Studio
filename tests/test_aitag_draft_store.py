from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest

from aitag_core.draft_store import (
    get_latest_studio_draft,
    get_studio_draft,
    save_studio_draft,
    studio_drafts_root,
    validate_draft_id,
)


def _compiled(label: str = "draft") -> dict:
    return {
        "draft": {"prompt": label},
        "recipe": {"name": label},
        "candidates": [],
    }


@pytest.mark.parametrize(
    "value",
    ["", "abc", "../0123456789abcdef", "0123456789abcdef.json", "g" * 16],
)
def test_draft_id_rejects_paths_and_non_hex_ids(value: str) -> None:
    with pytest.raises(ValueError, match="Invalid studio draft id"):
        validate_draft_id(value)


def test_get_with_invalid_id_never_escapes_store_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"sentinel": true}', encoding="utf-8")

    assert get_studio_draft("../outside", root=tmp_path) is None
    assert outside.read_text(encoding="utf-8") == '{"sentinel": true}'


def test_draft_record_has_a_strict_1536_kib_file_limit(tmp_path: Path) -> None:
    # 上限与 draft_store._MAX_PAYLOAD_BYTES 一致：多页在线草稿需要超过 512 KiB
    with pytest.raises(ValueError, match="too large"):
        save_studio_draft(_compiled("x" * (1536 * 1024)), root=tmp_path)

    assert not studio_drafts_root(tmp_path).exists()


@pytest.mark.parametrize("ttl", [0, -1, float("nan"), float("inf"), "forever"])
def test_draft_ttl_must_be_positive_and_finite(tmp_path: Path, ttl: object) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        save_studio_draft(_compiled(), root=tmp_path, ttl_seconds=ttl)  # type: ignore[arg-type]


def test_expired_drafts_are_removed_from_direct_and_latest_reads(tmp_path: Path) -> None:
    with patch("aitag_core.draft_store.time.time", return_value=100.0):
        record = save_studio_draft(_compiled(), root=tmp_path, ttl_seconds=10)

    with patch("aitag_core.draft_store.time.time", return_value=110.0):
        assert get_studio_draft(record["draft_id"], root=tmp_path, ttl_seconds=10) is None
        assert get_latest_studio_draft(root=tmp_path, ttl_seconds=10) is None

    assert not (studio_drafts_root(tmp_path) / f"{record['draft_id']}.json").exists()


def test_corrupt_json_and_timestamp_do_not_block_latest_or_save(tmp_path: Path) -> None:
    store = studio_drafts_root(tmp_path)
    store.mkdir(parents=True)
    bad_json = store / "1111111111111111.json"
    bad_json.write_text("{broken", encoding="utf-8")
    bad_time = store / "2222222222222222.json"
    bad_time.write_text(
        json.dumps(
            {
                "draft_id": bad_time.stem,
                "source": "aitag-online",
                "updated_at": "not-a-number",
                "payload": {"draft": {}},
            }
        ),
        encoding="utf-8",
    )

    saved = save_studio_draft(_compiled("healthy"), root=tmp_path)
    latest = get_latest_studio_draft(root=tmp_path)

    assert latest is not None
    assert latest["draft_id"] == saved["draft_id"]
    assert not bad_json.exists()
    assert not bad_time.exists()


def test_store_keeps_only_the_64_most_recent_drafts(tmp_path: Path) -> None:
    clock = {"now": 1_000.0}

    def now() -> float:
        clock["now"] += 1.0
        return clock["now"]

    with patch("aitag_core.draft_store.time.time", side_effect=now):
        records = [save_studio_draft(_compiled(str(index)), root=tmp_path) for index in range(70)]

    files = list(studio_drafts_root(tmp_path).glob("*.json"))
    assert len(files) == 64
    with patch("aitag_core.draft_store.time.time", side_effect=now):
        assert get_studio_draft(records[0]["draft_id"], root=tmp_path) is None
        assert get_studio_draft(records[-1]["draft_id"], root=tmp_path) is not None


def test_concurrent_saves_remain_valid_and_bounded(tmp_path: Path) -> None:
    with ThreadPoolExecutor(max_workers=12) as pool:
        records = list(
            pool.map(
                lambda index: save_studio_draft(_compiled(str(index)), root=tmp_path),
                range(80),
            )
        )

    files = list(studio_drafts_root(tmp_path).glob("*.json"))
    assert len(files) == 64
    assert len({record["draft_id"] for record in records}) == 80
    assert not list(studio_drafts_root(tmp_path).glob("*.tmp"))
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["draft_id"] == path.stem
        assert payload["generation_calls"] == 0

from __future__ import annotations

import json
from pathlib import Path

import pytest

import pixiv_web_upload as pwu


@pytest.fixture()
def pack_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(pwu, "_selector_pack_file", lambda: tmp_path / "pixiv_upload_selectors.json")
    return tmp_path


def _write_pack(path: Path, pack: dict) -> None:
    path.write_text(
        json.dumps({"schema_version": 1, "packs": {"default": pack}}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_missing_pack_file_falls_back_to_builtin(pack_dir: Path) -> None:
    pack, source = pwu.load_selector_pack()
    assert source == "builtin"
    assert pack["title"][0] == "input[name='title']"
    # 辅助组也必须存在（caption/overlay/confirm 已从硬编码迁入包内）
    for group in ("upload", "title", "tags", "caption", "submit", "overlay_dismiss", "confirm_dialog"):
        assert pack[group], group


def test_external_pack_overrides_builtin(pack_dir: Path) -> None:
    custom = {group: list(values) for group, values in pwu._BUILTIN_SELECTOR_PACK.items()}
    custom["title"] = ["input[data-testid='new-title']", "input[name='title']"]
    _write_pack(pack_dir / "pixiv_upload_selectors.json", custom)

    pack, source = pwu.load_selector_pack()
    assert source == "external"
    assert pack["title"][0] == "input[data-testid='new-title']"


def test_broken_pack_json_falls_back_to_builtin(pack_dir: Path) -> None:
    (pack_dir / "pixiv_upload_selectors.json").write_text("{not json", encoding="utf-8")
    pack, source = pwu.load_selector_pack()
    assert source == "builtin"
    assert pack["upload"]


def test_pack_missing_required_group_is_rejected(pack_dir: Path) -> None:
    custom = {group: list(values) for group, values in pwu._BUILTIN_SELECTOR_PACK.items()}
    del custom["submit"]
    _write_pack(pack_dir / "pixiv_upload_selectors.json", custom)

    pack, source = pwu.load_selector_pack()
    assert source == "builtin"


def test_pack_missing_aux_group_is_padded_from_builtin(pack_dir: Path) -> None:
    custom = {group: list(values) for group, values in pwu._BUILTIN_SELECTOR_PACK.items()}
    del custom["overlay_dismiss"]
    _write_pack(pack_dir / "pixiv_upload_selectors.json", custom)

    pack, source = pwu.load_selector_pack()
    assert source == "external"
    assert pack["overlay_dismiss"] == pwu._BUILTIN_SELECTOR_PACK["overlay_dismiss"]


def test_shipped_pack_file_is_valid() -> None:
    # 随发布 shipped 的 data/pixiv_upload_selectors.json 必须永远通过校验
    pack = pwu._validate_selector_pack(
        json.loads(
            (Path(pwu.__file__).resolve().parent / "data" / "pixiv_upload_selectors.json").read_text(
                encoding="utf-8"
            )
        )
    )
    assert pack is not None
    for group in pwu._REQUIRED_SELECTOR_GROUPS:
        assert pack[group]


def test_selector_probe_error_mentions_draft_and_pack_update() -> None:
    probe = {
        "ok": False,
        "error": {"code": "pixiv_selector_probe_failed", "missing": ["tags"]},
    }
    exc = pwu._selector_probe_error(probe, phase="preflight")
    assert exc.code == "pixiv_selector_probe_failed"
    assert exc.details["phase"] == "preflight"
    message = str(exc)
    assert "草稿已保留" in message
    assert "pixiv_upload_selectors.json" in message


def test_preflight_runs_before_file_upload() -> None:
    # 源码顺序契约：预检必须在 _upload_files 之前，站点改版时秒级失败
    source = Path(pwu.__file__).read_text(encoding="utf-8")
    body = source.split("async def upload_illust_via_web", 1)[1]
    preflight_at = body.index('phase="preflight"')
    upload_at = body.index("await _upload_files(page, paths)")
    assert preflight_at < upload_at


def test_upload_flow_reloads_pack_before_preflight() -> None:
    # 源码顺序契约：上传流程在预检前调用 maybe_reload_selector_pack
    source = Path(pwu.__file__).read_text(encoding="utf-8")
    body = source.split("async def upload_illust_via_web", 1)[1]
    reload_at = body.index("maybe_reload_selector_pack()")
    preflight_at = body.index('phase="preflight"')
    assert reload_at < preflight_at


def test_maybe_reload_picks_up_pack_file_changes(pack_dir: Path) -> None:
    import os
    import time

    saved = (
        pwu.PIXIV_UPLOAD_SELECTORS,
        pwu.SELECTOR_PACK_SOURCE,
        pwu._SELECTOR_PACK_MTIME,
    )
    try:
        pwu.reload_selector_pack()
        assert pwu.SELECTOR_PACK_SOURCE == "builtin"
        # mtime 未变：不重载，来源不变
        assert pwu.maybe_reload_selector_pack() == "builtin"

        custom = {group: list(values) for group, values in pwu._BUILTIN_SELECTOR_PACK.items()}
        custom["title"] = ["input[data-testid='hot-reload-title']", "input[name='title']"]
        pack_file = pack_dir / "pixiv_upload_selectors.json"
        _write_pack(pack_file, custom)
        future = time.time() + 5
        os.utime(pack_file, (future, future))

        assert pwu.maybe_reload_selector_pack() == "external"
        assert pwu.PIXIV_UPLOAD_SELECTORS["title"][0] == (
            "input[data-testid='hot-reload-title']"
        )

        # 删除外置包 → mtime 变化 → 回落内置
        pack_file.unlink()
        assert pwu.maybe_reload_selector_pack() == "builtin"
        assert pwu.PIXIV_UPLOAD_SELECTORS["title"][0] == "input[name='title']"
    finally:
        pwu.PIXIV_UPLOAD_SELECTORS = saved[0]
        pwu.SELECTOR_PACK_SOURCE = saved[1]
        pwu._SELECTOR_PACK_MTIME = saved[2]

"""Source-level guards for release-time fail-closed behavior.

Complements tests/test_release_script_safety.py (which executes the release
script end to end) with cheap assertions that the safety gates cannot be
silently skipped.
"""

from __future__ import annotations

from pathlib import Path

from scripts.verify_release_stage import FULL_SEED_FILES

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MAKE_RELEASE = (PROJECT_ROOT / "scripts" / "make_release.ps1").read_text(
    encoding="utf-8"
)
UPDATER = (PROJECT_ROOT / "scripts" / "updater.bat").read_text(encoding="utf-8")


def test_asset_stamp_check_fails_closed_when_python_is_missing() -> None:
    # 打戳门禁不得再以“PATH 上恰好有 python.exe”为执行前提。
    assert "if ($assetPython" not in MAKE_RELEASE
    # 找不到任何解释器（.venv 与 PATH 都没有）时必须让发布失败。
    assert 'throw "Python is required' in MAKE_RELEASE
    # 解释器解析必须先于 --check 调用，且 --check 用解析出的解释器执行。
    resolve_pos = MAKE_RELEASE.index('throw "Python is required')
    check_pos = MAKE_RELEASE.index("$assetVersioner --check")
    assert resolve_pos < check_pos
    assert "& $releasePython $assetVersioner --check" in MAKE_RELEASE


def test_required_data_files_are_hard_errors_not_silent_skips() -> None:
    assert "[switch]$Required" in MAKE_RELEASE
    assert 'throw "Required release source is missing:' in MAKE_RELEASE
    assert "Copy-FileRel $file -Required" in MAKE_RELEASE


def test_pixiv_upload_selectors_is_a_required_seed_file() -> None:
    assert "data/pixiv_upload_selectors.json" in FULL_SEED_FILES
    # make_release.ps1 的种子清单必须同步声明，否则 stage 校验必然失败。
    assert '"data\\pixiv_upload_selectors.json"' in MAKE_RELEASE


def test_release_keeps_exactly_one_previous_stage_for_rollback() -> None:
    # 上一版 stage 保留为固定名 .previous 回滚副本，而不是发布成功后立即删除。
    assert '.$PackageName.previous"' in MAKE_RELEASE
    success_tail = MAKE_RELEASE.split("$buildStageCreated = $false", 1)[1]
    assert "Move-Item -LiteralPath $previousStageBackup -Destination $rollbackStage" in success_tail
    assert (
        "Remove-Item -LiteralPath $previousStageBackup -Recurse -Force"
        not in success_tail
    )


def test_updater_keeps_exe_backup_before_overwrite() -> None:
    assert '"%EXE%.bak"' in UPDATER
    backup_pos = UPDATER.index('"%EXE%.bak"')
    overwrite_pos = UPDATER.index('copy /y "%UPD%" "%EXE%"')
    assert backup_pos < overwrite_pos

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_nai_atlas_design_system_is_shared_by_every_core_surface() -> None:
    theme = _read("web/core-theme.css")
    for token in ("--paper", "--ink", "--acid", "--coral", "--cobalt"):
        assert token in theme
    for relative in (
        "scripts/core_web_index.html",
        "scripts/core_web_progress.html",
        "web/nai-tags.html",
        "web/maintenance.html",
    ):
        html = _read(relative)
        assert 'data-ui="nai-atlas"' in html
        # web/ 下的引用带内容哈希缓存戳（scripts/asset_versions.py 维护），
        # scripts/ 下的 core 模板不带；契约只断言引用本身存在。
        assert 'href="/assets/core-theme.css' in html
        assert 'class="atlas-nav"' in html
        assert "Nai学长工作室" in html
        assert "<style" not in html


def test_gallery_is_an_original_editorial_archive_not_a_raw_data_shell() -> None:
    html = _read("scripts/core_web_index.html")
    script = _read("scripts/core_web_app.js")
    for marker in (
        'class="archive-hero"',
        'class="search-console"',
        'id="resultCount"',
        'class="detail-sheet"',
        'aria-labelledby="detailTitle"',
        'id="copyPrompt"',
    ):
        assert marker in html
    assert "renderPromptPanel" in script
    assert "renderTagList" in script
    assert "galleryRequestId" in script
    assert "detailRequestId" in script
    assert "JSON.stringify({ work, images" not in script


def test_classification_intake_and_maintenance_have_distinct_product_layouts() -> None:
    tags = _read("web/nai-tags.html")
    tags_script = _read("web/nai-tags.js")
    intake = _read("scripts/core_web_progress.html")
    maintenance = _read("web/maintenance.html")
    maintenance_script = _read("web/gallery-maintenance.js")

    assert 'class="facet-workbench"' in tags
    assert 'id="selectionCount"' in tags
    assert 'id="clearSelections"' in tags
    assert 'aria-pressed="true"' in tags
    assert "updateSelectionSummary" in tags_script
    assert "facetRequestId" in tags_script
    assert "worksRequestId" in tags_script
    assert "const facet = state.facet" in tags_script

    assert 'class="intake-layout"' in intake
    assert 'class="account-vault"' in intake
    assert 'class="telemetry-stack"' in intake
    assert 'aria-live="polite"' in intake
    assert 'class="telemetry-stack" aria-live=' not in intake

    assert 'class="maintenance-grid"' in maintenance
    assert 'id="storageMeter"' in maintenance
    assert 'id="receiptList"' in maintenance
    assert "renderReceipt" in maintenance_script


def test_core_release_copies_the_original_theme_asset() -> None:
    release_script = _read("scripts/make_release.ps1")
    assert '"web\\core-theme.css"' in release_script

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(name: str) -> str:
    return (ROOT / "web" / name).read_text(encoding="utf-8")


def test_tag_assets_exposes_source_discovery_detail_and_draft_stages() -> None:
    html = read("tag-assets.html")
    for contract in (
        'id="assetSource"',
        'value="aitag-online"',
        'id="assetSort"',
        'id="assetDetailPanel"',
        'id="assetImageChoices"',
        'id="assetTargetSource"',
        'id="assetTargetResults"',
        'id="assetPrepareDraft"',
        "只有在 Studio 明确点击生成",
    ):
        assert contract in html


def test_online_workflow_uses_detail_and_zero_generation_draft_contract() -> None:
    source = read("tag-assets.js")
    model = read("tag-assets-model.js")
    assert "/api/nai/aitag/work/" in source
    assert '}/draft`' in source
    assert "image_index" in source
    assert "target_reference_id" in source
    assert "generation_calls" in model
    assert "generationCalls !== 0" in model
    assert "localStorage.setItem(DRAFT_KEY" in source
    assert "/api/nai/generate" not in source
    assert "/apply" not in source


def test_online_filters_are_sent_to_backend_and_qualification_is_rendered() -> None:
    source = read("tag-assets.js")
    model = read("tag-assets-model.js")
    assert 'url.searchParams.set("nai_only"' in source
    assert 'url.searchParams.set("safe_only"' in source
    assert 'url.searchParams.set("sort"' in source
    assert "qualification_reasons" in model
    assert '["direct", "remix-only", "qualified", "eligible", "ok"]' in model
    assert "cardQualification(item)" in source
    assert "Display enhancement only" in model


def test_online_target_must_be_explicitly_imported_to_a_stable_local_reference() -> None:
    source = read("tag-assets.js")
    assert 'api("/api/nai/aitag/import"' in source
    assert "result.reference_id" in source
    assert "saveOnlineTarget" in source
    assert "在线候选尚未保存" in source
    assert "aitag-online:" not in source
    assert "candidate_id" in source
    assert "slot_index" in source
    assert 'id="assetSourceCandidates"' in read("tag-assets.html")


def test_detail_shows_source_license_provenance_and_transient_draft_fallback() -> None:
    source = read("tag-assets.js")
    model = read("tag-assets-model.js")
    html = read("tag-assets.html")
    assert 'id="assetDetailLicense"' in html
    assert "licenseFrom" in model
    assert "detail.license.name" in source
    assert "normalized.persisted" in source


def test_online_failures_have_a_local_catalog_fallback() -> None:
    source = read("tag-assets.js")
    assert "function fallbackToLocal" in source
    assert 'state.source = "local"' in source
    assert '$("assetSource").value = "local"' in source


def test_dedicated_online_library_opens_starter_assets_and_loads_results() -> None:
    html = read("tag-assets.html")
    source = read("tag-assets.js")
    assert 'id="assetSourceField"' in html
    assert 'ONLINE_LIBRARY_PAGE ? "aitag-online" : "local"' in source
    assert "search({ reset: true }).then(() =>" in source
    assert 'searchParams.get("work")' in source
    assert 'data-gallery-source="aitag-online"' in read("index.html")
    assert '$("assetSafeOnly").checked = false;' in source
    assert "hydrateOnlinePreviews" in source
    # 版本戳为内容哈希（asset_versions.py 维护），只断言引用存在
    assert re.search(r"tag-assets\.js\?v=[0-9a-f]+", html)
    assert re.search(r"tag-assets-model\.js\?v=[0-9a-f]+", source)

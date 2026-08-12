from __future__ import annotations

from pathlib import Path

import pytest

from scripts import asset_versions as av


@pytest.fixture()
def web_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    web = tmp_path / "web"
    web.mkdir()
    monkeypatch.setattr(av, "WEB", web)
    monkeypatch.setattr(av, "MANIFEST", tmp_path / "tests" / "regression_manifest.json")
    return web


def test_apply_rewrites_stale_stamps_and_converges(web_tree: Path) -> None:
    web = web_tree
    (web / "a.js").write_text("export const A = 1;\n", encoding="utf-8")
    (web / "index.html").write_text(
        '<script src="/assets/a.js?v=old"></script>\n', encoding="utf-8"
    )

    assert av.collect_stale(), "fixture must start stale"
    changed = av.apply_updates()
    assert changed == ["index.html"] or changed and changed[0].endswith("index.html")

    expected = av.asset_version(web / "a.js")
    assert f'/assets/a.js?v={expected}' in (web / "index.html").read_text(encoding="utf-8")
    assert av.collect_stale() == [], "apply must converge in a single pass"


def test_circular_imports_converge_because_stamps_are_normalized(web_tree: Path) -> None:
    web = web_tree
    (web / "x.js").write_text('import "./y.js?v=1";\nexport const X = 1;\n', encoding="utf-8")
    (web / "y.js").write_text('import "./x.js?v=1";\nexport const Y = 1;\n', encoding="utf-8")

    av.apply_updates()
    assert av.collect_stale() == []
    # 再跑一次必须完全无变化（幂等）
    assert av.apply_updates() == []


def test_missing_targets_and_external_urls_are_left_untouched(web_tree: Path) -> None:
    web = web_tree
    html = (
        '<script src="/assets/ghost.js?v=5"></script>\n'
        '<script src="https://cdn.example.com/lib.js?v=9"></script>\n'
    )
    page = web / "page.html"
    page.write_text(html, encoding="utf-8")

    assert av.apply_updates() == []
    assert page.read_text(encoding="utf-8") == html


def test_backup_directories_are_skipped(web_tree: Path) -> None:
    web = web_tree
    (web / "real.js").write_text("export {};\n", encoding="utf-8")
    backup = web / "web.backup-20260811"
    backup.mkdir()
    (backup / "old.html").write_text(
        '<script src="/assets/real.js?v=stale"></script>\n', encoding="utf-8"
    )

    assert av.collect_stale() == []
    assert av.apply_updates() == []


def test_relative_refs_resolve_against_holder_directory(web_tree: Path) -> None:
    web = web_tree
    plugin = web / "plugins" / "demo"
    plugin.mkdir(parents=True)
    (plugin / "state.js").write_text("export const S = 1;\n", encoding="utf-8")
    (plugin / "panel.js").write_text('import "./state.js?v=2";\n', encoding="utf-8")

    av.apply_updates()
    expected = av.asset_version(plugin / "state.js")
    assert f'./state.js?v={expected}' in (plugin / "panel.js").read_text(encoding="utf-8")


def test_refresh_manifest_tracks_app_js(web_tree: Path, tmp_path: Path) -> None:
    web = web_tree
    (web / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    av.MANIFEST.parent.mkdir(parents=True)
    av.refresh_manifest()

    import json

    manifest = json.loads(av.MANIFEST.read_text(encoding="utf-8"))
    digest = av.asset_digest(web / "app.js")
    assert manifest["app_js_version"] == digest[:10]
    assert manifest["app_js_sha256_12"] == digest[:12]


def test_real_workspace_stamps_are_fresh() -> None:
    # 仓库自身的 ?v= 戳必须始终与内容哈希一致；过期说明改完前端没跑
    # python scripts/asset_versions.py。
    assert av.collect_stale() == []


def test_apply_stamps_unstamped_local_refs_and_converges(web_tree: Path) -> None:
    web = web_tree
    (web / "a.js").write_text("export const A = 1;\n", encoding="utf-8")
    (web / "b.css").write_text("body { margin: 0; }\n", encoding="utf-8")
    page = web / "index.html"
    page.write_text(
        '<script src="/assets/a.js"></script>\n'
        '<link rel="stylesheet" href="/assets/b.css" />\n',
        encoding="utf-8",
    )

    stale = av.collect_stale()
    assert {ref.version for ref, _expected in stale} == {None}, "unstamped refs must be stale"

    av.apply_updates()
    html = page.read_text(encoding="utf-8")
    assert f'/assets/a.js?v={av.asset_version(web / "a.js")}' in html
    assert f'/assets/b.css?v={av.asset_version(web / "b.css")}' in html
    assert av.collect_stale() == [], "apply must converge in a single pass"
    assert av.apply_updates() == [], "second run must be a no-op"


def test_unstamped_refs_in_js_strings_are_stamped(web_tree: Path) -> None:
    web = web_tree
    (web / "tag_i18n.js").write_text("export {};\n", encoding="utf-8")
    loader = web / "loader.js"
    loader.write_text('load("/assets/tag_i18n.js");\n', encoding="utf-8")

    av.apply_updates()
    expected = av.asset_version(web / "tag_i18n.js")
    assert f'load("/assets/tag_i18n.js?v={expected}")' in loader.read_text(encoding="utf-8")


def test_unstamped_detection_skips_templated_external_and_missing_refs(
    web_tree: Path,
) -> None:
    web = web_tree
    html = (
        '<script src="https://cdn.example.com/lib.js"></script>\n'
        '<script src="/assets/ghost.js"></script>\n'
        '<script src="/assets/also-missing.css"></script>\n'
    )
    page = web / "page.html"
    page.write_text(html, encoding="utf-8")
    templated = web / "dyn.js"
    templated_source = "const url = `/assets/${name}.js`;\n"
    templated.write_text(templated_source, encoding="utf-8")

    assert av.collect_stale() == []
    assert av.apply_updates() == []
    assert page.read_text(encoding="utf-8") == html
    assert templated.read_text(encoding="utf-8") == templated_source


def test_refs_with_other_query_strings_are_not_treated_as_unstamped(
    web_tree: Path,
) -> None:
    web = web_tree
    (web / "c.js").write_text("export {};\n", encoding="utf-8")
    html = '<script src="/assets/c.js?x=1"></script>\n'
    page = web / "page.html"
    page.write_text(html, encoding="utf-8")

    assert av.apply_updates() == []
    assert page.read_text(encoding="utf-8") == html


def test_refresh_manifest_tracks_entry_versions(web_tree: Path) -> None:
    web = web_tree
    (web / "app.js").write_text("console.log(1);\n", encoding="utf-8")
    (web / "app-detail.js").write_text("console.log(2);\n", encoding="utf-8")
    (web / "index.html").write_text(
        '<script src="/assets/app-detail.js?v=x"></script>\n'
        '<script src="/assets/app.js?v=x"></script>\n'
        '<script src="/assets/shared/helper.js?v=x"></script>\n',
        encoding="utf-8",
    )
    av.MANIFEST.parent.mkdir(parents=True)
    av.refresh_manifest()

    import json

    manifest = json.loads(av.MANIFEST.read_text(encoding="utf-8"))
    entries = manifest["entry_versions"]
    assert entries["app.js"] == av.asset_version(web / "app.js")
    assert entries["app-detail.js"] == av.asset_version(web / "app-detail.js")
    # shared/ 下的脚本不是顶层入口脚本，不进 entry_versions
    assert "helper.js" not in entries
    assert manifest["app_js_version"] == av.asset_digest(web / "app.js")[:10]


def test_real_entry_pages_have_no_unstamped_asset_refs() -> None:
    # 永久守卫：入口页面里的本地 /assets/*.js|css 引用必须全部带 ?v= 戳。
    for page_name in ("index.html", "pixiv.html"):
        html = (av.WEB / page_name).read_text(encoding="utf-8")
        unstamped = [
            match.group("url")
            for match in av.UNSTAMPED_RE.finditer(html)
            if match.group("url").startswith("/assets/")
        ]
        assert unstamped == [], f"{page_name} has unstamped asset refs: {unstamped}"

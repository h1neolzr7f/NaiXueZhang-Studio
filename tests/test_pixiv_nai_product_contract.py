from __future__ import annotations

import re

import json
from pathlib import Path

from gallery_catalog import GALLERY_SITE, gallery_specs


ROOT = Path(__file__).resolve().parents[1]


def test_new_package_has_no_aitag_network_dependency() -> None:
    # The public checkout must not contain the local config.json. Validate the
    # release-safe template unconditionally, and a local config only when a
    # developer has created one outside version control.
    names = ["config.release.json"]
    if (ROOT / "config.json").exists():
        names.append("config.json")
    for name in names:
        config = json.loads((ROOT / name).read_text(encoding="utf-8"))
        assert config["legacy_aitag_crawler_enabled"] is False
        assert config["base_url"] == ""
        assert config["cdn_url"] == ""

    setup_script = ROOT / "setup_web.ps1"
    if setup_script.exists():
        assert "aitag.win" not in setup_script.read_text(encoding="utf-8")


def test_primary_gallery_is_local_pixiv_nai_only() -> None:
    site = gallery_specs()[GALLERY_SITE]

    assert site.cdn_fallback is False
    assert site.label_zh == "Pixiv NAI 图库"
    assert "Pixiv" in site.description_zh
    assert "aitag" not in site.description_en.lower()


def test_primary_branding_uses_original_nai_atlas_shell() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    core = (ROOT / "web" / "app-core.js").read_text(encoding="utf-8")

    assert re.search(r"^#+\s*🐾?\s*Nai学长工作室", readme, re.M)
    assert "Nai学长工作室" in index
    assert "Pixiv" in index
    assert 'data-ui="nai-atlas-full"' in index
    assert "aitag.win" not in index
    assert "aitag.win" not in core


def test_official_copy_points_at_public_studio_repo() -> None:
    official = "https://github.com/h1neolzr7f/NaiXueZhang-Studio"
    notice = (ROOT / "BUNDLE_NOTICE.txt").read_text(encoding="utf-8")
    responsible = (ROOT / "RESPONSIBLE_USE.md").read_text(encoding="utf-8")
    contributing = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    security = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
    notes = (ROOT / "scripts" / "release_notes.py").read_text(encoding="utf-8")
    roadmap = (ROOT / "ROADMAP.md").read_text(encoding="utf-8")

    assert official in notice
    assert "h1neolzr7f/pixiv-nai-gallery" not in notice
    assert "h1neolzr7f/NaiXueZhang-Studio" in responsible
    assert "从 `main` 创建功能分支" in contributing
    assert "最新的 `main`" in security
    assert "私有仓库" not in notes
    assert "仅发布产物公开" not in notes
    assert "v1.4.0 修复版" in roadmap
    assert "## v1.3 Creator Operations" not in roadmap

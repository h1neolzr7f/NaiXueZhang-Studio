from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dark_theme_overrides_json_and_tag_token_colors() -> None:
    css = read("web/studio-theme.css")
    for selector in (
        ".json-box .k",
        ".json-box.nai .s",
        ".json-box .n",
        ".json-box .b",
        ".json-box .sd",
        ".chip.tag-chip .tag-jp",
    ):
        assert selector in css
    assert ".json-box .json-actions,\n.json-box .json-bottom { opacity: 1 !important; }" in css


def test_dark_theme_stylesheet_is_loaded_last_without_light_override() -> None:
    html = read("web/index.html")
    # 版本戳为内容哈希（scripts/asset_versions.py 维护），契约只要求暗色主题样式被加载
    assert re.search(r'href="/assets/studio-theme\.css\?v=[0-9a-f]+"', html)
    assert "unified-light.css" not in html

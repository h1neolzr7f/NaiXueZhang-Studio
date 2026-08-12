from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
THEME_LINK = '/assets/studio-theme.css?v='


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_every_html_page_loads_dark_studio_theme_last() -> None:
    pages = sorted(WEB.glob("*.html"))
    assert pages

    for page in pages:
        html = read(page)
        assert html.count(THEME_LINK) == 1, page.name
        assert html.rfind(THEME_LINK) < html.lower().find("</head>"), page.name
        later_stylesheet = html.find('rel="stylesheet"', html.rfind(THEME_LINK) + len(THEME_LINK))
        assert later_stylesheet == -1 or later_stylesheet > html.lower().find("</head>"), page.name


def test_unified_theme_declares_deep_space_product_palette() -> None:
    css = read(WEB / "studio-theme.css")

    for token in (
        "color-scheme: dark",
        "--bg0: #070a11",
        "--bg2: #111927",
        "--text: #e8f1fa",
        "--cyan: #5de4ff",
        "--pink: #ff9ec6",
    ):
        assert token in css


def test_unified_theme_covers_every_major_workspace() -> None:
    # 单页家族皮肤已拆到各页自己的 CSS（studio-theme 只保留跨页共享部分），
    # 这里按“主题体系整体”断言覆盖度。
    css = "\n".join(
        read(WEB / name)
        for name in (
            "studio-theme.css",
            "pixiv.css",
            "studio.css",
            "butler.css",
            "director.css",
            "settings.css",
            "references.css",
            "workflow-pages.css",
            "plugins/char-swap/char-swap.css",
        )
    )

    for selector_or_token in (
        # 旧 .nv-* 导航已随 site-nav.js 切换为 .site-nav 体系并移除
        ".nv-kbd",
        ".px-panel",
        ".px-gen-sidebar",
        ".studio-panel",
        ".char-swap-panel",
        ".butler-hero",
        ".butler-chat-panel",
        ".director-panel",
        ".settings-section",
        ".ref-panel",
        ".fc-panel",
        ".progress-card",
        "--director-bg",
        "--settings-bg",
        "--ref-bg",
        "--cs-bg-main",
    ):
        assert selector_or_token in css


def test_theme_keeps_semantic_feedback_states_distinct() -> None:
    css = read(WEB / "studio-theme.css")

    assert "#3ddc97" in css  # success
    assert "#ffd166" in css  # warning
    assert "#ff7a90" in css  # error


def test_dark_theme_keeps_json_and_tag_metadata_readable() -> None:
    css = read(WEB / "studio-theme.css")

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

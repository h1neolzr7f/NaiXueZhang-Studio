from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_nai_tag_page_exposes_aitag_style_facets_and_gallery_results() -> None:
    html = (ROOT / "web" / "nai-tags.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "nai-tags.js").read_text(encoding="utf-8")

    for facet in (
        "character",
        "copyright",
        "artist",
        "action",
        "clothing",
        "scene",
        "composition",
        "other",
    ):
        assert f'data-facet="{facet}"' in html
    assert 'id="tagCloud"' in html
    assert 'id="workGrid"' in html
    assert 'ApiClient.get("/api/nai-tags' in script
    assert "/api/nai-tags/works" in script
    assert "selection" in script
    assert "selected: new Set()" in script
    assert "state.selected.add(key)" in script
    assert "state.selected.delete(key)" in script
    assert "selected: new Map()" not in script

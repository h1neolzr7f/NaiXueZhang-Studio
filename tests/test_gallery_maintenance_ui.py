from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_gallery_maintenance_page_wires_safe_local_operations() -> None:
    html = (ROOT / "web" / "maintenance.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "gallery-maintenance.js").read_text(encoding="utf-8")

    for control in ("rebuildThumbs", "rebuildNaiTags", "previewOrphans", "cleanOrphans", "createSnapshot"):
        assert f'id="{control}"' in html
    assert "/api/maintenance/storage" in script
    assert "/api/maintenance/thumbnails/rebuild" in script
    assert "/api/maintenance/orphans/preview" in script
    assert 'confirm: true' in script
    assert "/api/maintenance/snapshot" in script

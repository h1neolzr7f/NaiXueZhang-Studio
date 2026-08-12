import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_progress_page_has_independent_pixiv_nai_controls() -> None:
    html = (ROOT / "web" / "progress.html").read_text(encoding="utf-8")
    script = (ROOT / "web" / "pixiv-intake-control.js").read_text(
        encoding="utf-8"
    )

    assert 'id="pixivNaiPanel"' in html
    assert 'id="pixivPreflight"' in html
    assert 'id="pixivPreflightReport"' in html
    assert 'id="pixivStorageQuotaGiB"' in html
    assert 'data-target="pixiv"' in html
    assert "/api/crawler/pixiv/task" in script
    assert "/api/crawler/pixiv/report" in script
    assert "/api/crawler/pixiv/preflight" in script
    assert "taskFromForm()" in script
    assert "storage_quota_bytes" in script
    assert "works_quarantined" in script
    assert "failure_kinds" in script
    assert "report.history" in script
    assert "source_url" not in script
    assert 'value="comfy"' not in html.lower()
    assert 'data-target="site"' not in html
    assert 'data-target="qqgroup"' not in html
    assert 'data-target="all"' not in html
    assert "Pixiv NAI" in html


def test_progress_page_loads_shared_api_client_before_inline_and_intake_code() -> None:
    html = (ROOT / "web" / "progress.html").read_text(encoding="utf-8")
    # 版本戳为内容哈希（asset_versions.py 维护），按路径定位即可
    api_m = re.search(r"/assets/shared/api-client\.js\?v=[0-9a-f]+", html)
    intake_m = re.search(r"/assets/pixiv-intake-control\.js\?v=[0-9a-f]+", html)
    assert api_m and intake_m
    assert api_m.start() < html.index("<script>")
    assert api_m.start() < intake_m.start()

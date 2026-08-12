from pathlib import Path
from unittest.mock import patch

import crawler_hub


ROOT = Path(__file__).resolve().parents[1]


def _text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_default_launchers_never_spawn_the_legacy_upstream_crawler() -> None:
    for relative in (
        "start_crawl.ps1",
        "run_crawl_background.ps1",
        "start_crawl_all.bat",
        "scripts/run_crawler_direct.py",
        "scripts/run_crawl_queue.py",
    ):
        source = _text(relative)
        assert (
            "pixiv_nai_crawler.py" in source
            or "start_pixiv_crawler" in source
            or "from pixiv_nai_crawler" in source
            or "start_crawl.bat" in source
        )
        assert '"crawler.py"' not in source
        assert "ROOT / \"crawler.py\"" not in source


def test_hub_all_runs_pixiv_and_does_not_implicitly_run_qq_or_site() -> None:
    source = _text("crawler_hub.py")
    all_branch = source.split('if target == "all":', 1)[1].split("return 2", 1)[0]
    assert "run_pixiv" in all_branch
    assert "run_site" not in all_branch
    assert "run_qq" not in all_branch


def test_hub_reports_legacy_site_as_disabled_and_migrated(tmp_path: Path) -> None:
    with patch.object(crawler_hub, "ROOT", tmp_path), patch.object(
        crawler_hub,
        "_load_config",
        return_value={"crawlers": {"site": {"enabled": True}}},
    ):
        report = crawler_hub.status()
    assert report["site"]["config"] == {
        "enabled": False,
        "disabled": True,
        "migrated_to": "pixiv",
    }

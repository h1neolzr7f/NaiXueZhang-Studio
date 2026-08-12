import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import gallery_catalog
from crawler_qq import looks_like_comfy as crawler_looks_like_comfy
from db_queries import _strip_private_work_paths
from scripts.gallery_import_common import stable_work_id
from scripts.import_qq_gallery import looks_like_comfy as importer_looks_like_comfy


ROOT = Path(__file__).resolve().parents[1]


class MultiGallerySafetyTests(unittest.TestCase):
    def test_generated_work_ids_are_javascript_safe(self) -> None:
        sample = r"E:\images\sample.png"
        work_id = stable_work_id("qq", "account", sample)
        self.assertEqual(work_id, stable_work_id("qq", "account", sample))
        self.assertGreater(work_id, 0)
        self.assertLessEqual(work_id, (2**53) - 1)

    def test_nested_comfy_directories_are_excluded(self) -> None:
        nested = Path(r"E:\images\account\ComfyUI\output\sample.png")
        self.assertTrue(crawler_looks_like_comfy(nested))
        self.assertTrue(importer_looks_like_comfy(nested))
        ordinary = Path(r"E:\images\account\NovelAI\sample.png")
        self.assertFalse(crawler_looks_like_comfy(ordinary))
        self.assertFalse(importer_looks_like_comfy(ordinary))

    def test_api_work_payload_strips_private_source_paths(self) -> None:
        sanitized = _strip_private_work_paths(
            {
                "id": 1,
                "title": "sample",
                "source_path": r"E:\private\sample.png",
                "database_path": r"E:\private\gallery.db",
                "source_file": "sample.png",
            }
        )
        self.assertEqual(sanitized["source_file"], "sample.png")
        self.assertNotIn("source_path", sanitized)
        self.assertNotIn("database_path", sanitized)

    def test_public_catalog_omits_local_filesystem_paths(self) -> None:
        fake_db = SimpleNamespace(count_works=lambda: 0)
        with patch.object(gallery_catalog, "get_db", return_value=fake_db):
            items = gallery_catalog.public_gallery_list()
        self.assertEqual(len(items), 3)
        for item in items:
            self.assertNotIn("images_dir", item)
            self.assertNotIn("database_path", item)

    def test_large_gallery_assets_are_git_ignored(self) -> None:
        source = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("data/galleries/", source)

    def test_release_whitelist_contains_multi_gallery_runtime(self) -> None:
        source = (ROOT / "scripts" / "make_release.ps1").read_text(encoding="utf-8")
        for name in (
            "gallery_catalog.py",
            "gallery_audit_service.py",
            "crawler_hub.py",
            "crawler_qq.py",
            "db_queries.py",
            "nai_image_metadata.py",
            "product_ops.py",
            "qq_gallery_ingest.py",
            "INSTALL.bat",
            "requirements.lock.txt",
            "setup_web.ps1",
            "start_crawl_all.bat",
            "start_crawl_qq.bat",
            "start_crawl_site.bat",
        ):
            self.assertIn(f'"{name}"', source)
        self.assertIn('Copy-DirRel "third_party"', source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
import collections
import json
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tests.asgi_client import TestClient

import server
import favorites
import production_queue
import generated_gallery
import gallery_catalog
import pixiv_launch
from generation_jobs import GenerationJobManager


ROOT = Path(__file__).resolve().parents[1]


class GeneratedGalleryDefinitionContractTests(unittest.TestCase):
    def test_generated_gallery_has_one_top_level_definition_per_public_operation(self) -> None:
        tree = ast.parse(
            (ROOT / "generated_gallery.py").read_text(encoding="utf-8")
        )
        definitions = collections.defaultdict(list)
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                definitions[node.name].append(node.lineno)

        duplicates = {
            name: lines
            for name, lines in definitions.items()
            if len(lines) > 1
        }
        self.assertEqual(duplicates, {})


class GeneratedGalleryCacheContractTests(unittest.TestCase):
    def test_group_memory_cache_refreshes_after_external_directory_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated_dir = root / "generated"
            cache_dir = root / "cache"
            generated_dir.mkdir()
            first = generated_dir / "20260729_010101_501.png"
            first.write_bytes(b"first")

            with patch.object(generated_gallery, "GENERATED_DIR", generated_dir), patch.object(
                generated_gallery,
                "_ITEMS_CACHE_FILE",
                cache_dir / "items.json",
            ), patch.object(
                generated_gallery,
                "_GROUPS_CACHE_FILE",
                cache_dir / "groups.json",
            ):
                generated_gallery.invalidate_scan_cache()
                initial = generated_gallery.list_groups(force=True)
                old_dir_time = generated_dir.stat().st_mtime

                second = generated_dir / "20260729_010102_502.png"
                second.write_bytes(b"second")
                os.utime(generated_dir, (old_dir_time + 2, old_dir_time + 2))
                refreshed = generated_gallery.list_groups()

        self.assertEqual({group["group_id"] for group in initial}, {"501"})
        self.assertEqual(
            {group["group_id"] for group in refreshed},
            {"501", "502"},
        )

    def test_same_numeric_work_id_from_different_galleries_stays_separate(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated_dir = root / "generated"
            cache_dir = root / "cache"
            generated_dir.mkdir()
            site = generated_dir / "20260729_020101_501.png"
            qq = generated_dir / "20260729_020102_501.png"
            site.write_bytes(b"site")
            qq.write_bytes(b"qq")
            site.with_suffix(".png.meta.json").write_text(
                json.dumps({"work_id": 501, "source_gallery_id": "site"}),
                encoding="utf-8",
            )
            qq.with_suffix(".png.meta.json").write_text(
                json.dumps({"work_id": 501, "source_gallery_id": "qqgroup"}),
                encoding="utf-8",
            )

            with patch.object(generated_gallery, "GENERATED_DIR", generated_dir), patch.object(
                generated_gallery,
                "_ITEMS_CACHE_FILE",
                cache_dir / "items.json",
            ), patch.object(
                generated_gallery,
                "_GROUPS_CACHE_FILE",
                cache_dir / "groups.json",
            ):
                generated_gallery.invalidate_scan_cache()
                groups = generated_gallery.list_groups(force=True)
                site_group = generated_gallery.get_group("501")
                qq_group = generated_gallery.get_group("gallery:qqgroup:501")

        self.assertEqual(
            {group["group_id"] for group in groups},
            {"501", "gallery:qqgroup:501"},
        )
        self.assertEqual(
            {item["source_gallery_id"] for item in site_group["items"]},
            {"site"},
        )
        self.assertEqual(
            {item["source_gallery_id"] for item in qq_group["items"]},
            {"qqgroup"},
        )

    def test_source_metadata_cache_is_scoped_by_gallery_and_work_id(self) -> None:
        generated_gallery.invalidate_source_cache()
        try:
            site = generated_gallery.get_cached_source_info(
                501,
                lambda _work_id: {"work": {"title": "site-title"}, "images": []},
                gallery_id="site",
            )
            qq = generated_gallery.get_cached_source_info(
                501,
                lambda _work_id: {"work": {"title": "qq-title"}, "images": []},
                gallery_id="qqgroup",
            )
        finally:
            generated_gallery.invalidate_source_cache()

        self.assertEqual(site["title"], "site-title")
        self.assertEqual(qq["title"], "qq-title")

    def test_interrupted_group_cache_write_never_leaves_partial_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated_dir = root / "generated"
            cache_dir = root / "cache"
            groups_cache = cache_dir / "groups.json"
            generated_dir.mkdir()
            cache_dir.mkdir()
            (generated_dir / "20260729_030101_501.png").write_bytes(b"image")
            groups_cache.write_text('{"groups":[]}\n', encoding="utf-8")
            original_write_text = Path.write_text

            def interrupted_write(path: Path, data: str, *args, **kwargs) -> int:
                if path == groups_cache:
                    original_write_text(path, "{", encoding="utf-8")
                    raise OSError("simulated interrupted write")
                return original_write_text(path, data, *args, **kwargs)

            with patch.object(generated_gallery, "GENERATED_DIR", generated_dir), patch.object(
                generated_gallery,
                "_ITEMS_CACHE_FILE",
                cache_dir / "items.json",
            ), patch.object(
                generated_gallery,
                "_GROUPS_CACHE_FILE",
                groups_cache,
            ), patch.object(
                Path,
                "write_text",
                new=interrupted_write,
            ):
                generated_gallery.invalidate_scan_cache()
                generated_gallery.list_groups(force=True)

            persisted = json.loads(groups_cache.read_text(encoding="utf-8"))

        self.assertIsInstance(persisted.get("groups"), list)

    def test_restore_preflights_every_artifact_before_moving_any_file(self) -> None:
        image_id = "20260729_040101_501"
        with tempfile.TemporaryDirectory() as temp:
            generated_dir = Path(temp) / "generated"
            generated_dir.mkdir()
            primary = generated_dir / f"{image_id}.png"
            metadata = generated_dir / f"{image_id}.png.meta.json"
            primary.write_bytes(b"primary")
            metadata.write_text('{"work_id":501}', encoding="utf-8")

            with patch.object(generated_gallery, "GENERATED_DIR", generated_dir):
                deleted = generated_gallery.delete_item(image_id)
                trash_entry = generated_dir / ".trash" / deleted["trash_id"]
                (trash_entry / metadata.name).unlink()
                with self.assertRaises(FileNotFoundError):
                    generated_gallery.restore_deleted(deleted["trash_id"])

                self.assertFalse(primary.exists())
                self.assertTrue((trash_entry / primary.name).is_file())

    def test_malformed_generation_group_id_is_a_clean_miss(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generated_dir = root / "generated"
            generated_dir.mkdir()
            (generated_dir / "20260729_060101_501.png").write_bytes(b"image")
            with patch.object(generated_gallery, "GENERATED_DIR", generated_dir), patch.object(
                generated_gallery,
                "_ITEMS_CACHE_FILE",
                root / "cache" / "items.json",
            ), patch.object(
                generated_gallery,
                "_GROUPS_CACHE_FILE",
                root / "cache" / "groups.json",
            ):
                generated_gallery.invalidate_scan_cache()
                group = generated_gallery.get_group(
                    "run:series:not-a-number",
                    rescan_if_missing=False,
                )

        self.assertIsNone(group)


class WorkSelectionConcurrencyContractTests(unittest.TestCase):
    def test_concurrent_favorite_adds_do_not_overwrite_each_other(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "favorites.json"
            work_ids = list(range(1001, 1065))
            with patch.object(favorites, "FAV_PATH", path):
                with ThreadPoolExecutor(max_workers=16) as pool:
                    results = list(pool.map(favorites.add, work_ids))
                stored = favorites.list_refs()

        self.assertTrue(all(result["ok"] for result in results))
        self.assertEqual(
            {item["work_id"] for item in stored},
            {str(work_id) for work_id in work_ids},
        )

    def test_distinct_job_managers_do_not_drop_each_others_persisted_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            state_path = Path(temp) / "generation_jobs.json"
            managers = [
                GenerationJobManager(state_path=state_path),
                GenerationJobManager(state_path=state_path),
            ]
            with ThreadPoolExecutor(max_workers=2) as pool:
                jobs = list(
                    pool.map(
                        lambda manager: manager.start_job(
                            total=1,
                            generate=False,
                            preview_only=True,
                        ),
                        managers,
                    )
                )
            persisted = json.loads(state_path.read_text(encoding="utf-8"))

        self.assertEqual(
            {row["task_id"] for row in persisted["jobs"]},
            {job.task_id for job in jobs},
        )

    def test_concurrent_pixiv_history_appends_preserve_every_record(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            history_path = Path(temp) / "pixiv_uploads.json"
            records = [
                {"illust_id": str(work_id), "title": f"upload-{work_id}"}
                for work_id in range(2001, 2033)
            ]
            with patch.object(pixiv_launch, "HISTORY_PATH", history_path):
                with ThreadPoolExecutor(max_workers=16) as pool:
                    list(pool.map(pixiv_launch._append_history, records))
                persisted = pixiv_launch.list_upload_history(100)

        self.assertEqual(
            {item["illust_id"] for item in persisted},
            {record["illust_id"] for record in records},
        )

    def test_interrupted_last_job_write_does_not_destroy_restart_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            request_path = Path(temp) / "last_job.json"
            request_path.write_text(
                json.dumps({"kind": "prepare", "payload": {"image_id": "old"}}),
                encoding="utf-8",
            )
            original_write_text = Path.write_text

            def interrupted_write(path: Path, data: str, *args, **kwargs) -> int:
                if path == request_path:
                    original_write_text(path, "{", encoding="utf-8")
                    raise OSError("simulated interrupted write")
                return original_write_text(path, data, *args, **kwargs)

            with patch.object(pixiv_launch, "LAST_JOB_PATH", request_path), patch.object(
                Path,
                "write_text",
                new=interrupted_write,
            ):
                with pixiv_launch._LOCK:
                    pixiv_launch._LAST_JOB_REQUEST.clear()
                pixiv_launch._remember_job_request(
                    "prepare",
                    {"image_id": "new"},
                )
                with pixiv_launch._LOCK:
                    pixiv_launch._LAST_JOB_REQUEST.clear()
                restored = pixiv_launch._load_last_job_request()

        self.assertEqual(restored["payload"]["image_id"], "new")

    def test_concurrent_gallery_db_access_constructs_one_connection(self) -> None:
        created: list[object] = []

        def build_db(_path: Path) -> object:
            time.sleep(0.03)
            db = object()
            created.append(db)
            return db

        gallery_catalog._DB_CACHE.clear()
        try:
            with patch.object(
                gallery_catalog,
                "ensure_gallery_dirs",
                return_value=SimpleNamespace(db_path=Path("gallery.db")),
            ), patch.object(
                gallery_catalog,
                "Database",
                side_effect=build_db,
            ):
                with ThreadPoolExecutor(max_workers=12) as pool:
                    results = list(pool.map(gallery_catalog.get_db, ["site"] * 24))
        finally:
            gallery_catalog._DB_CACHE.clear()

        self.assertEqual(len(created), 1)
        self.assertTrue(all(db is created[0] for db in results))


class LargeWorkIdApiContractTests(unittest.TestCase):
    LARGE_QQ_WORK_ID = 1_152_795_263_166_342_247

    def setUp(self) -> None:
        self.client = TestClient(server.app)

    def test_qq_gallery_search_returns_opaque_text_work_ids(self) -> None:
        fake_db = SimpleNamespace(
            search_works=lambda **_kwargs: {
                "page": 1,
                "page_size": 60,
                "total": 1,
                "items": [
                    {
                        "id": self.LARGE_QQ_WORK_ID,
                        "title": "legacy QQ asset",
                        "source_path": r"E:\private\qq-source.png",
                        "database_path": r"E:\private\gallery.db",
                    }
                ],
            }
        )
        with patch("routes.gallery._gallery_db", return_value=fake_db):
            response = self.client.get(
                "/api/ai_works_search?gallery_id=qqgroup"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["items"][0]["id"],
            str(self.LARGE_QQ_WORK_ID),
        )
        self.assertNotIn("source_path", response.json()["items"][0])
        self.assertNotIn("database_path", response.json()["items"][0])

    def test_qq_gallery_detail_and_lite_return_text_work_ids(self) -> None:
        large = self.LARGE_QQ_WORK_ID
        fake_db = SimpleNamespace(
            get_work_detail=lambda _work_id: {
                "work": {
                    "id": large,
                    "title": "legacy QQ asset",
                    "source_path": r"E:\private\qq-source.png",
                },
                "images": [
                    {
                        "work_id": large,
                        "page_index": 0,
                        "absolute_path": r"E:\private\qq-source.png",
                    }
                ],
            },
            get_work_lite=lambda _work_id: {
                "work": {
                    "id": large,
                    "title": "legacy QQ asset",
                    "source_path": r"E:\private\qq-source.png",
                },
                "images": [{"work_id": large, "page_index": 0}],
            },
        )
        with patch("routes.gallery._gallery_db", return_value=fake_db):
            detail = self.client.get(
                f"/api/work/{large}?gallery_id=qqgroup"
            )
            lite = self.client.get(
                f"/api/work/{large}/lite?gallery_id=qqgroup"
            )

        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["work"]["id"], str(large))
        self.assertEqual(detail.json()["images"][0]["work_id"], str(large))
        self.assertEqual(lite.status_code, 200)
        self.assertEqual(lite.json()["work"]["id"], str(large))
        self.assertEqual(lite.json()["images"][0]["work_id"], str(large))
        self.assertNotIn("source_path", detail.json()["work"])
        self.assertNotIn("absolute_path", detail.json()["images"][0])
        self.assertNotIn("source_path", lite.json()["work"])

    def test_char_swap_extract_receives_gallery_context(self) -> None:
        large = self.LARGE_QQ_WORK_ID
        with patch(
            "routes.char_swap.extract_chars",
            return_value={"work_id": large, "chars": []},
        ) as extract:
            response = self.client.get(
                "/api/plugin/char-swap/extract"
                f"?work_id={large}&page_index=0&gallery_id=qqgroup"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["work_id"], str(large))
        extract.assert_called_once_with(large, 0, "qqgroup")

    def test_favorites_and_queue_summaries_never_emit_large_numeric_ids(self) -> None:
        large = str(self.LARGE_QQ_WORK_ID)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(favorites, "FAV_PATH", root / "favorites.json"),
                patch.object(
                    production_queue,
                    "QUEUE_PATH",
                    root / "production_queue.json",
                ),
            ):
                favorites.add(large, "qqgroup")
                production_queue.add(large, gallery_id="qqgroup")
                favorite_response = self.client.get("/api/favorites")
                queue_response = self.client.get("/api/queue")

        self.assertEqual(favorite_response.status_code, 200)
        self.assertEqual(queue_response.status_code, 200)
        self.assertIn(large, favorite_response.json()["ids"])
        self.assertIn(large, queue_response.json()["ids"])
        self.assertEqual(
            favorite_response.json()["refs"][0]["work_id"],
            large,
        )
        self.assertEqual(queue_response.json()["refs"][0]["work_id"], large)

    def test_qq_favorite_and_queue_work_lists_return_text_ids(self) -> None:
        large = self.LARGE_QQ_WORK_ID
        fake_db = SimpleNamespace(
            search_favorite_works=lambda *_args, **_kwargs: {
                "page": 1,
                "items": [{"id": large, "title": "legacy QQ asset"}],
            }
        )
        refs = [{"gallery_id": "qqgroup", "work_id": str(large)}]
        with (
            patch("routes.gallery._gallery_db", return_value=fake_db),
            patch("routes.gallery.list_refs_favorites", return_value=refs),
            patch("routes.gallery.list_refs_queue", return_value=refs),
        ):
            favorite_response = self.client.get(
                "/api/favorites/works?gallery_id=qqgroup"
            )
            queue_response = self.client.get(
                "/api/queue/works?gallery_id=qqgroup"
            )

        self.assertEqual(
            favorite_response.json()["items"][0]["id"],
            str(large),
        )
        self.assertEqual(queue_response.json()["items"][0]["id"], str(large))

    def test_generated_qq_group_detail_uses_opaque_work_ids(self) -> None:
        large = self.LARGE_QQ_WORK_ID
        group = {
            "group_id": f"gallery:qqgroup:{large}",
            "source_gallery_id": "qqgroup",
            "work_id": large,
            "items": [
                {
                    "id": "20260729_050101",
                    "source_gallery_id": "qqgroup",
                    "work_id": large,
                }
            ],
        }
        fake_db = SimpleNamespace(
            get_work_detail=lambda _work_id: {
                "work": {"id": large, "title": "QQ source"},
                "images": [],
            }
        )
        with (
            patch("routes.nai.get_group", return_value=group),
            patch("routes.nai.migrate_legacy_meta"),
            patch("routes.nai.batch_status", return_value={"status": "idle"}),
            patch("routes.nai.queue_status", return_value={"status": "idle"}),
            patch("routes.nai.get_gallery_db", return_value=fake_db),
        ):
            response = self.client.get(
                f"/api/generated/gallery:qqgroup:{large}"
                "?include_source_prompt=false"
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["group"]["work_id"], str(large))
        self.assertEqual(response.json()["source"]["work_id"], str(large))

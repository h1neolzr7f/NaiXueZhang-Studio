from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import butler_gallery_operations as operations
import butler_service


class ButlerGalleryOperationTests(unittest.TestCase):
    def test_catalogue_is_unique_typed_and_keeps_protected_operations_out(self) -> None:
        catalog = operations.catalogue()
        names = [item["name"] for item in catalog]

        self.assertEqual(len(names), len(set(names)))
        self.assertGreaterEqual(len(names), 15)
        self.assertEqual(set(names), set(operations.READ_OPERATIONS | operations.CONFIRM_OPERATIONS))
        self.assertTrue(all(item["risk"] in {"read", "confirm"} for item in catalog))
        self.assertNotIn("publish_pixiv", names)
        self.assertNotIn("manage_secrets", names)

    def test_butler_normalizes_cross_gallery_favorites_and_requires_confirmation(self) -> None:
        action = butler_service.normalize_action(
            {
                "tool": "add_to_favorites",
                "arguments": {"gallery_id": "qqgroup", "work_ids": [12, "12", 13]},
            }
        )

        self.assertEqual(action["risk"], "confirm")
        self.assertEqual(action["arguments"], {"gallery_id": "qqgroup", "work_ids": [12, 13]})
        self.assertIn("2 个作品", butler_service._confirmation_summary(action))

    def test_generated_delete_rejects_paths_and_accepts_local_asset_ids(self) -> None:
        with self.assertRaises(ValueError):
            operations.normalize("delete_generated_item", {"image_id": "../secret"})

        self.assertEqual(
            operations.normalize("delete_generated_item", {"image_id": "145765334_0_final"}),
            {"image_id": "145765334_0_final"},
        )

    def test_collection_query_is_resolved_to_explicit_ids_before_confirmation(self) -> None:
        db = Mock()
        db.search_works.return_value = {"items": [{"id": 31}, {"id": 32}, {"id": 33}]}
        with patch.object(operations, "get_db", return_value=db):
            action = butler_service.normalize_action(
                {
                    "tool": "add_to_queue",
                    "arguments": {"gallery_id": "codex", "q": "明日方舟", "sort": "monthly", "limit": 3},
                }
            )

        self.assertEqual(action["arguments"]["work_ids"], [31, 32, 33])
        self.assertEqual(action["arguments"]["gallery_id"], "codex")
        self.assertIn("3 个作品", butler_service._confirmation_summary(action))
        db.search_works.assert_called_once_with(
            q="明日方舟",
            prompt="",
            page=1,
            page_size=3,
            sort="monthly",
            time_range="all",
            local_scope="",
            skip_total=True,
            nai_only=True,
        )

    def test_pipeline_defaults_to_all_missing_and_global_configuration(self) -> None:
        args = operations.normalize("run_pipeline", {})

        self.assertTrue(args["all_missing"])
        self.assertTrue(args["only_missing"])
        with patch("post_pipeline.start_pipeline", return_value={"ok": True, "total": 3, "message": "started"}) as start:
            result = operations.execute_confirmed("run_pipeline", args)

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["pipeline_url"], "/pipeline")
        start.assert_called_once_with({"all_missing": True, "only_missing": True})

    def test_crawler_targets_are_normalized_and_route_domain_function_is_reused(self) -> None:
        args = operations.normalize("stop_crawler", {"target": "qq"})
        self.assertEqual(args, {"target": "qqgroup"})

        with patch("crawler_control.stop_crawler_target", return_value={"qqgroup": {"crawler_qq": [1]}}) as stop, patch(
            "crawler_control.multi_crawler_status", return_value={"qqgroup": {"running": False}}
        ):
            result = operations.execute_confirmed("stop_crawler", args)

        stop.assert_called_once_with("qqgroup")
        self.assertTrue(result["ok"])

    def test_partial_crawler_configuration_preserves_existing_search_scope(self) -> None:
        current = {
            "search_query": "-NAI_X NAI 明日方舟",
            "search_sort": "monthly",
            "search_time_range": "month",
            "search_max_pages": 88,
            "search_batch_pages": 5,
            "crawler_phase": "all",
            "dataset_name": "arknights-nai",
        }
        with patch("crawler_task.get_task", return_value=current):
            args = operations.normalize("configure_crawler", {"crawler_phase": "preview"})

        self.assertEqual(args["search_query"], current["search_query"])
        self.assertEqual(args["search_sort"], "monthly")
        self.assertEqual(args["crawler_phase"], "preview")
        self.assertIsNone(args["reset_search"])

    def test_adding_a_favorite_rejects_a_missing_work(self) -> None:
        db = Mock()
        db.get_work_detail.return_value = None
        with patch.object(operations, "get_db", return_value=db), patch("favorites.add") as add:
            with self.assertRaisesRegex(ValueError, "不存在"):
                operations.execute_confirmed(
                    "add_to_favorites",
                    {"gallery_id": "site", "work_ids": [999999999]},
                )
        add.assert_not_called()

    def test_capability_report_explains_manual_boundaries(self) -> None:
        result = operations.execute_read("inspect_capabilities", {})

        self.assertEqual(result["supported"], len(operations.catalogue()))
        self.assertTrue(any(item["name"] == "publish_pixiv" for item in result["protected"]))
        self.assertIn("collection", result["categories"])
        self.assertIn("crawler", result["categories"])


if __name__ == "__main__":
    unittest.main()

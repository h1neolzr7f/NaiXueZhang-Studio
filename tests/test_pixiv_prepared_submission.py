from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pixiv_launch


class PixivPreparedSubmissionTests(unittest.TestCase):
    def test_prepare_package_finishes_pipeline_and_copy_without_upload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prepared_path = Path(tmp) / "prepared.json"
            with patch.object(pixiv_launch, "PREPARED_PATH", prepared_path), patch.object(
                pixiv_launch,
                "_resolve_selection_batches",
                return_value=[
                    {
                        "group_id": "g1+g2",
                        "group_ids": ["g1", "g2"],
                        "image_ids": ["a", "b"],
                        "merged": True,
                    }
                ],
            ), patch.object(
                pixiv_launch,
                "load_config",
                return_value={
                    "account": {"persona": {"name": "p"}},
                    "upload": {"auto_pipeline": True, "illust_type": 0},
                },
            ), patch.object(
                pixiv_launch, "_ensure_upload_ready", return_value=["a", "b"]
            ) as pipeline, patch.object(
                pixiv_launch,
                "generate_post_copy",
                return_value={
                    "post": {
                        "title": "title",
                        "caption": "caption",
                        "tags": ["tag"],
                    }
                },
            ) as copy, patch.object(
                pixiv_launch, "_resolve_x_restrict", return_value="general"
            ), patch.object(pixiv_launch, "upload_illust") as upload:
                result = pixiv_launch.prepare_submission_package(
                    {"group_ids": ["g1", "g2"], "merge_groups": True}
                )

                loaded = pixiv_launch.load_prepared_submission()

            self.assertEqual(result["prepared"]["status"], "ready_for_upload")
            self.assertEqual(result["prepared"]["total_images"], 2)
            self.assertTrue(prepared_path.exists())
            pipeline.assert_called_once()
            copy.assert_called_once()
            upload.assert_not_called()

            self.assertEqual(loaded["prepared"]["items"][0]["image_ids"], ["a", "b"])

    def test_exact_series_create_separate_archived_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch.object(pixiv_launch, "PREPARED_PATH", root / "latest.json"), patch.object(
                pixiv_launch, "PREPARED_ARCHIVE_DIR", root / "archive"
            ), patch.object(
                pixiv_launch,
                "load_config",
                return_value={
                    "account": {"persona": {"name": "p"}},
                    "upload": {"auto_pipeline": False, "illust_type": 0},
                },
            ), patch.object(
                pixiv_launch,
                "generate_post_copy",
                return_value={"post": {"title": "t", "caption": "c", "tags": []}},
            ), patch.object(
                pixiv_launch, "_ensure_upload_ready", side_effect=lambda ids, *_args, **_kwargs: list(ids)
            ) as pipeline, patch.object(
                pixiv_launch, "_resolve_x_restrict", return_value="general"
            ), patch.object(pixiv_launch, "upload_illust") as upload:
                result = pixiv_launch.prepare_submission_package(
                    {
                        "package_id": "workflow-1",
                        "series": [
                            {"group_id": "a", "image_ids": ["a1.png", "a2.png"]},
                            {"group_id": "b", "image_ids": ["b1.png"]},
                        ],
                    }
                )
                loaded = pixiv_launch.load_prepared_submission("workflow-1")

            self.assertEqual(len(result["prepared"]["items"]), 2)
            self.assertEqual(result["prepared"]["items"][0]["image_ids"], ["a1", "a2"])
            self.assertEqual(result["prepared"]["items"][1]["image_ids"], ["b1"])
            self.assertEqual(loaded["prepared"]["package_id"], "workflow-1")
            self.assertTrue((root / "archive" / "workflow-1.json").exists())
            self.assertEqual(pipeline.call_count, 2)
            upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()

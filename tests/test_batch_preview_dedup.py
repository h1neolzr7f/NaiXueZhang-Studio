"""Batch preview should validate each distinct source draft once."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from nai_char import batch_preview


class BatchPreviewDeduplicationTests(unittest.TestCase):
    def test_duplicate_output_copies_share_one_source_preflight(self) -> None:
        prepared = {
            "ok": True,
            "skipped": False,
            "message": "draft ready",
            "summary": "角色",
            "from_workbench": False,
            "style_replacements": 0,
            "transform_applied": True,
            "style_applied": False,
            "chars": [{"char_caption": "role"}],
        }
        targets = [
            {"gallery_id": "site", "work_id": 11, "page_index": 0, "generation": {"seed": 1}},
            {"gallery_id": "site", "work_id": 11, "page_index": 0, "generation": {"seed": 2}},
            {"gallery_id": "site", "work_id": 11, "page_index": 1, "generation": {"seed": 3}},
        ]

        with patch("nai_char.prepare_work_draft", return_value=prepared) as prepare:
            result = batch_preview({"targets": targets, "recipe": {}})

        self.assertEqual(prepare.call_count, 2)
        self.assertEqual(result["total"], 3)
        self.assertEqual(result["ready"], 3)
        self.assertEqual(
            [(item["work_id"], item["page_index"]) for item in result["items"]],
            [(11, 0), (11, 0), (11, 1)],
        )

    def test_different_edited_drafts_are_not_coalesced(self) -> None:
        prepared = {"ok": True, "chars": []}
        targets = [
            {"work_id": 11, "page_index": 0, "patched_comment": {"prompt": "first"}},
            {"work_id": 11, "page_index": 0, "patched_comment": {"prompt": "second"}},
        ]

        with patch("nai_char.prepare_work_draft", return_value=prepared) as prepare:
            batch_preview({"targets": targets, "recipe": {}})

        self.assertEqual(prepare.call_count, 2)


if __name__ == "__main__":
    unittest.main()

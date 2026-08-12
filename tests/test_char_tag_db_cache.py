"""Regression coverage for the in-process character tag rule cache."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import char_tag_db


class CharacterTagGroupsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._original_groups_path = char_tag_db.GROUPS_PATH
        char_tag_db.GROUPS_PATH = Path(self._tmp.name) / "char_tag_groups.json"
        char_tag_db._load_groups.cache_clear()

    def tearDown(self) -> None:
        char_tag_db.GROUPS_PATH = self._original_groups_path
        char_tag_db._load_groups.cache_clear()
        char_tag_db.reload_index()
        self._tmp.cleanup()

    def _write_groups(self, *, face_tag: str) -> None:
        char_tag_db.GROUPS_PATH.write_text(
            json.dumps(
                {
                    "face_keep_exact": [],
                    "face_strip_exact": [face_tag],
                    "face_strip_substrings": [],
                    "appearance_suffixes": [],
                }
            ),
            encoding="utf-8",
        )

    def test_reload_index_refreshes_cached_group_rules(self) -> None:
        self._write_groups(face_tag="old_face")
        self.assertTrue(char_tag_db.is_face_tag("old_face"))

        self._write_groups(face_tag="new_face")
        # Normal interactive replacements retain their in-memory rules.
        self.assertTrue(char_tag_db.is_face_tag("old_face"))
        self.assertFalse(char_tag_db.is_face_tag("new_face"))

        char_tag_db.reload_index()
        self.assertFalse(char_tag_db.is_face_tag("old_face"))
        self.assertTrue(char_tag_db.is_face_tag("new_face"))


if __name__ == "__main__":
    unittest.main()

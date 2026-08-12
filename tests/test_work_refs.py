from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import favorites
import production_queue
from work_refs import WorkRef


class WorkReferenceTests(unittest.TestCase):
    def test_same_large_id_is_isolated_by_gallery_and_round_trips_as_text(self) -> None:
        large = "1152795263166342247"
        site = WorkRef.parse(large, "site")
        qq = WorkRef.parse(large, "qqgroup")
        self.assertNotEqual(site.key, qq.key)
        self.assertEqual(json.loads(json.dumps(qq.public()))["work_id"], large)

    def test_unknown_gallery_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            WorkRef.parse("42", "typo")

    def test_legacy_favorite_migrates_to_site_and_composite_refs_do_not_collide(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "favorites.json"
            path.write_text('{"items":[{"work_id":42,"added_at":"old"}]}', encoding="utf-8")
            with patch.object(favorites, "FAV_PATH", path):
                self.assertTrue(favorites.has(42, "site"))
                favorites.add(42, "qqgroup")
                self.assertEqual(
                    {(ref["gallery_id"], ref["work_id"]) for ref in favorites.list_refs()},
                    {("site", "42"), ("qqgroup", "42")},
                )
                favorites.remove(42, "site")
                self.assertTrue(favorites.has(42, "qqgroup"))

    def test_queue_writes_schema_v2_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "queue.json"
            with patch.object(production_queue, "QUEUE_PATH", path):
                production_queue.add("9007199254740993", note="large", gallery_id="qqgroup")
                payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["items"][0]["work_id"], "9007199254740993")
        self.assertEqual(payload["items"][0]["gallery_id"], "qqgroup")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from nai_char import apply_style_payload, resolve_char_index, transform


def _fake_extract(work_id: int, page_index: int = 0, gallery_id: str = "site") -> dict:
    del page_index, gallery_id
    captions = (
        ["1girl, source_one_(series)", "1girl, source_two_(series)"]
        if work_id == 2
        else ["1girl, target_one_(series)", "1boy, target_two_(series)"]
    )
    chars = [
        {
            "char_caption": caption,
            "uc_caption": "",
            "center": {"x": 0.35 + i * 0.3, "y": 0.5},
            "summary": caption,
            "gender": "male" if "1boy" in caption else "female",
            "bundle": {
                "gender": "male" if "1boy" in caption else "female",
                "identity": [caption.split(", ", 1)[1]],
                "body": [],
                "appearance": [],
                "action": [],
            },
        }
        for i, caption in enumerate(captions)
    ]
    comment = {
        "prompt": "scene",
        "v4_prompt": {
            "caption": {
                "base_caption": "scene",
                "char_captions": [
                    {"char_caption": caption, "centers": [chars[i]["center"]]}
                    for i, caption in enumerate(captions)
                ],
            }
        },
        "v4_negative_prompt": {
            "caption": {"base_caption": "", "char_captions": []}
        },
    }
    return {
        "work_id": work_id,
        "page_index": 0,
        "comment": copy.deepcopy(comment),
        "ai_json": {"Comment": copy.deepcopy(comment)},
        "chars": chars,
        "base_caption": "scene",
        "params": {},
        "prompt_layout": "v4_slots",
        "char_marker_layout": None,
    }


class CharSwapReliabilityTests(unittest.TestCase):
    def test_style_replacement_preserves_replaced_role_slots_in_draft_and_response(self) -> None:
        with patch("nai_char.extract_chars", side_effect=_fake_extract):
            replaced = transform(
                {
                    "mode": "replace",
                    "target_work_id": 1,
                    "target_char_index": 0,
                    "custom_char_caption": "1girl, replacement_hero_(oc), blue hair",
                }
            )

        styled = apply_style_payload(
            {
                "patched_comment": replaced["patched_comment"],
                "mode": "preset",
                "replace": "artist:test_style",
            }
        )

        draft_slots = styled["patched_comment"]["v4_prompt"]["caption"]["char_captions"]
        self.assertEqual(len(draft_slots), 2)
        self.assertEqual(len(styled["chars"]), 2)
        self.assertIn("replacement_hero_(oc)", draft_slots[0]["char_caption"])
        self.assertIn("replacement_hero_(oc)", styled["chars"][0]["char_caption"])
        self.assertNotIn("target_one_(series)", draft_slots[0]["char_caption"])

    def test_target_slot_rejects_negative_and_unknown_values(self) -> None:
        chars = _fake_extract(1)["chars"]
        for target in (-1, "-1", "unknown-slot", True, len(chars)):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, "目标角色槽位"):
                    resolve_char_index(chars, target)

    def test_transform_rejects_negative_source_slot(self) -> None:
        with patch("nai_char.extract_chars", side_effect=_fake_extract):
            with self.assertRaisesRegex(ValueError, "源角色槽位"):
                transform(
                    {
                        "mode": "replace",
                        "target_work_id": 1,
                        "target_char_index": 0,
                        "source_work_id": 2,
                        "source_char_index": -1,
                    }
                )

    def test_transform_rejects_negative_source_page(self) -> None:
        with patch("nai_char.extract_chars", side_effect=_fake_extract):
            with self.assertRaisesRegex(ValueError, "源页码"):
                transform(
                    {
                        "mode": "replace",
                        "target_work_id": 1,
                        "target_char_index": 0,
                        "source_work_id": 2,
                        "source_page_index": -1,
                    }
                )

    def test_creature_replacement_rejects_out_of_range_slot_cleanly(self) -> None:
        with patch("nai_char.extract_chars", side_effect=_fake_extract):
            with self.assertRaisesRegex(ValueError, "目标角色槽位"):
                transform(
                    {
                        "mode": "creature_to_partner",
                        "target_work_id": 1,
                        "target_char_index": 99,
                        "custom_char_caption": "1girl, replacement",
                    }
                )

    def test_transform_rejects_negative_page_indices(self) -> None:
        with patch("nai_char.extract_chars", side_effect=_fake_extract):
            with self.assertRaisesRegex(ValueError, "目标页码"):
                transform(
                    {
                        "mode": "replace",
                        "target_work_id": 1,
                        "target_page_index": -1,
                        "target_char_index": 0,
                        "custom_char_caption": "1girl, replacement",
                    }
                )


if __name__ == "__main__":
    unittest.main()

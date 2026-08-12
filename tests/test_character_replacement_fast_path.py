"""Performance contracts for local role-replacement draft preparation."""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from nai_char import prepare_work_draft, transform


class CharacterReplacementFastPathTests(unittest.TestCase):
    def test_batch_recipe_defaults_to_preserving_scene_action_tags(self) -> None:
        comment = {
            "prompt": "outdoors, two characters",
            "v4_prompt": {
                "caption": {
                    "base_caption": "outdoors, two characters",
                    "char_captions": [
                        {
                            "char_caption": (
                                "old_character_(series), 1girl, female_focus, "
                                "standing, looking at another"
                            ),
                            "centers": [{"x": 0.25, "y": 0.6}],
                        }
                    ],
                }
            },
            "v4_negative_prompt": {
                "caption": {
                    "base_caption": "bad anatomy",
                    "char_captions": [{"char_caption": ""}],
                }
            },
        }
        extracted = {
            "work_id": 7,
            "page_index": 0,
            "base_caption": "outdoors, two characters",
            "prompt_layout": "v4_slots",
            "chars": [
                {
                    "char_caption": (
                        "old_character_(series), 1girl, female_focus, "
                        "standing, looking at another"
                    ),
                    "uc_caption": "",
                    "center": {"x": 0.25, "y": 0.6},
                    "summary": "old character",
                }
            ],
            "params": {},
            "comment": comment,
            "ai_json": {"Comment": comment},
        }
        recipe = {
            "transform": {
                "enabled": True,
                "mode": "replace_female",
                "custom_char_caption": (
                    "new_character_(series), 1girl, female_focus, black hair"
                ),
                "gender": "female",
                "target_char_index": "auto_female",
            },
            "sanitize": {"enabled": False},
        }

        with patch(
            "nai_char.extract_chars",
            return_value=copy.deepcopy(extracted),
        ):
            result = prepare_work_draft(7, recipe=recipe)

        self.assertTrue(result["ok"])
        caption = result["patched_comment"]["v4_prompt"]["caption"]["char_captions"][0][
            "char_caption"
        ]
        self.assertIn("new_character_(series)", caption)
        self.assertIn("standing", caption)
        self.assertIn("looking at another", caption)
        self.assertNotIn("old_character_(series)", caption)

    def test_prepare_reuses_extracted_source_and_skips_unused_style_display_scan(self) -> None:
        comment = {
            "prompt": "outdoors, 1girl",
            "v4_prompt": {
                "caption": {
                    "base_caption": "outdoors, 1girl",
                    "char_captions": [
                        {
                            "char_caption": "old_character_(series), 1girl, female_focus, standing",
                            "centers": [{"x": 0.5, "y": 0.5}],
                        }
                    ],
                }
            },
            "v4_negative_prompt": {
                "caption": {
                    "base_caption": "",
                    "char_captions": [{"char_caption": ""}],
                }
            },
        }
        extracted = {
            "work_id": 7,
            "page_index": 0,
            "base_caption": "outdoors, 1girl",
            "prompt_layout": "v4_slots",
            "chars": [
                {
                    "char_caption": "old_character_(series), 1girl, female_focus, standing",
                    "uc_caption": "",
                    "center": {"x": 0.5, "y": 0.5},
                    "summary": "old character",
                }
            ],
            "params": {},
            "comment": comment,
            "ai_json": {"Comment": comment},
        }
        recipe = {
            "transform": {
                "enabled": True,
                "mode": "replace_female",
                "custom_char_caption": "new_character_(series), 1girl, female_focus, black hair",
                "gender": "female",
                "target_char_index": "auto_female",
                "preserve_action": True,
            },
            "sanitize": {"enabled": False},
        }

        with patch(
            "nai_char.extract_chars",
            side_effect=lambda *_args, **_kwargs: copy.deepcopy(extracted),
        ) as extract, patch(
            "nai_char.extract_style_slots_from_comment",
            return_value=[],
        ) as style_scan:
            result = prepare_work_draft(7, recipe=recipe)

        self.assertTrue(result["ok"])
        self.assertEqual(extract.call_count, 1)
        style_scan.assert_not_called()

    def test_fast_transform_preserves_the_public_transform_result(self) -> None:
        comment = {
            "prompt": "outdoors, two characters",
            "v4_prompt": {
                "caption": {
                    "base_caption": "outdoors, two characters",
                    "char_captions": [
                        {
                            "char_caption": "old_character_(series), 1girl, female_focus, sitting",
                            "centers": [{"x": 0.25, "y": 0.6}],
                        },
                        {
                            "char_caption": "partner_(series), 1boy, male_focus, standing",
                            "centers": [{"x": 0.75, "y": 0.5}],
                        },
                    ],
                }
            },
            "v4_negative_prompt": {
                "caption": {
                    "base_caption": "bad anatomy",
                    "char_captions": [
                        {"char_caption": "bad hands"},
                        {"char_caption": "bad hands"},
                    ],
                }
            },
            "seed": 123,
        }
        extracted = {
            "work_id": 7,
            "page_index": 0,
            "base_caption": "outdoors, two characters",
            "prompt_layout": "v4_slots",
            "chars": [
                {
                    "char_caption": "old_character_(series), 1girl, female_focus, sitting",
                    "uc_caption": "bad hands",
                    "center": {"x": 0.25, "y": 0.6},
                    "summary": "old character",
                },
                {
                    "char_caption": "partner_(series), 1boy, male_focus, standing",
                    "uc_caption": "bad hands",
                    "center": {"x": 0.75, "y": 0.5},
                    "summary": "partner",
                },
            ],
            "params": {"seed": 123},
            "comment": comment,
            "ai_json": {"Comment": comment},
        }
        payload = {
            "target_work_id": 7,
            "target_page_index": 0,
            "mode": "replace_female",
            "custom_char_caption": "new_character_(series), 1girl, female_focus, black hair",
            "gender": "female",
            "target_char_index": "auto_female",
            "preserve_action": True,
            "preserve_center": True,
        }

        with patch(
            "nai_char.extract_chars",
            side_effect=lambda *_args, **_kwargs: copy.deepcopy(extracted),
        ):
            reference = transform(payload)
        fast = transform(
            payload,
            source_data=copy.deepcopy(extracted),
            include_style_slots=False,
        )

        for key in (
            "ok",
            "mode",
            "work_id",
            "page_index",
            "chars",
            "patched_comment",
            "patched_ai_json",
            "base_caption",
            "params",
        ):
            self.assertEqual(fast[key], reference[key], key)


if __name__ == "__main__":
    unittest.main()

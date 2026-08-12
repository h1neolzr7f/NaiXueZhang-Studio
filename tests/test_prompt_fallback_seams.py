from __future__ import annotations

import unittest

from char_tag_db import is_appearance_weight_block
from nai_char import _chars_from_plain_character_prompt


class PlainPromptFallbackTests(unittest.TestCase):
    def test_public_core_without_generated_tag_index_keeps_named_characters(self) -> None:
        result = _chars_from_plain_character_prompt(
            "artist:sample, hatsune_miku, rem_(re:zero), 2girls, standing, blue hair"
        )
        self.assertIsNotNone(result)
        chars, base, layout = result or ([], "", "")
        self.assertEqual(layout, "plain_character_tags")
        self.assertEqual(
            [item.get("identity_tags") for item in chars],
            [["hatsune_miku"], ["rem_(re:zero)"]],
        )
        self.assertEqual(
            base,
            "artist:sample, hatsune_miku, rem_(re:zero), 2girls, standing, blue hair",
        )

    def test_appearance_hints_do_not_match_character_substrings(self) -> None:
        self.assertFalse(is_appearance_weight_block("hatsune_miku"))
        self.assertTrue(is_appearance_weight_block("white_hair"))


if __name__ == "__main__":
    unittest.main()

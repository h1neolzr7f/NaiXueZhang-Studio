from __future__ import annotations

import unittest

from nai_anima_adapter import adapt_anima_character, apply_anima_character_to_comment
from tests.asgi_client import TestClient
import server


class NaiAnimaAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.app)

    def _record(self) -> dict:
        return {
            "id": "skadi-arknights",
            "name": "Skadi",
            "trigger": "skadi_(arknights)",
            "copyright": "arknights",
            "gender": "female",
            "core_tags": [
                "white_hair",
                "red_eyes",
                "black_dress",
                "standing",
                "masterpiece",
                "artist:wawamachi",
            ],
            "facets": {
                "appearance": ["long_hair", "hair_between_eyes"],
                "camera": ["cowboy_shot"],
                "artist": ["artist:wawamachi"],
            },
            "source": "animadex",
            "version": "2026-07",
            "license": "MIT",
        }

    def test_reference_is_compiled_as_nai_character_facts(self) -> None:
        card = adapt_anima_character(self._record(), model="nai-diffusion-4-5-full")
        self.assertEqual(card["model_dialect"], "nai-v4.5")
        self.assertEqual(card["base_subject_tag"], "1girl")
        self.assertTrue(card["character_caption"].startswith("girl, skadi (arknights), arknights"))
        self.assertIn("white hair", card["character_caption"])
        self.assertNotIn("standing", card["character_caption"])
        self.assertNotIn("masterpiece", card["character_caption"])
        self.assertNotIn("artist:", card["character_caption"])
        self.assertEqual(card["style_hints"], ["artist:wawamachi"])
        self.assertEqual(card["provenance"]["license"], "MIT")

    def test_reference_is_applied_to_v4_slot_without_scene_leakage(self) -> None:
        comment = {
            "prompt": "indoors, night",
            "v4_prompt": {"caption": {"base_caption": "indoors, night", "char_captions": []}},
        }
        patched, card = apply_anima_character_to_comment(comment, self._record(), slot_index=0)
        caption = patched["v4_prompt"]["caption"]
        self.assertTrue(caption["base_caption"].startswith("1girl,"))
        self.assertEqual(caption["char_captions"][0]["char_caption"], card["character_caption"])
        self.assertNotIn("cowboy shot", caption["char_captions"][0]["char_caption"])

    def test_nai_supports_at_most_six_character_slots(self) -> None:
        with self.assertRaises(ValueError):
            apply_anima_character_to_comment({}, self._record(), slot_index=6)

    def test_nai_reference_route_exposes_compiled_card(self) -> None:
        response = self.client.post(
            "/api/plugin/char-swap/nai-reference/preview",
            json={"record": self._record(), "model": "nai-diffusion-4-5-full"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["card"]["base_subject_tag"], "1girl")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.asgi_client import TestClient

import server
import char_swap_config


ROOT = Path(__file__).resolve().parents[1]


class CustomOcPresetRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.app)

    def test_user_can_create_a_persisted_custom_oc_for_immediate_selection(self) -> None:
        config = {"custom_presets": {"male": [], "female": []}}

        def save(updates: dict) -> dict:
            config.update(updates)
            return config

        with patch("routes.char_swap.load_char_swap_config", side_effect=lambda: config), patch(
            "routes.char_swap.save_char_swap_config", side_effect=save
        ) as persist:
            response = self.client.post(
                "/api/plugin/char-swap/presets",
                json={
                    "label": "我的星空 OC",
                    "gender": "female",
                    "kind": "oc",
                    "char_caption": "1girl, female_focus, silver hair, starry eyes, blue dress",
                },
            )

        self.assertEqual(response.status_code, 200)
        preset = response.json()["preset"]
        self.assertEqual(preset["label"], "我的星空 OC")
        self.assertEqual(preset["kind"], "oc")
        self.assertTrue(preset["is_custom"])
        self.assertEqual(preset["source"], "custom")
        self.assertIn(preset, config["custom_presets"]["female"])
        persist.assert_called_once()

    def test_character_picker_returns_saved_custom_ocs_first_and_identifiable(self) -> None:
        builtin = {
            "female": [{"id": "surtr", "label": "史尔特尔", "gender": "female"}],
            "male": [],
        }
        custom = {
            "custom_presets": {
                "female": [
                    {
                        "id": "custom-f",
                        "label": "我的 OC",
                        "gender": "female",
                        "kind": "oc",
                        "char_caption": "1girl, female_focus, white hair",
                    },
                    {
                        "id": "custom-f-duplicate",
                        "label": "  我的 oc ",
                        "gender": "female",
                        "kind": "oc",
                        "char_caption": "1girl, female_focus, black hair",
                    },
                ],
                "male": [],
            }
        }
        with patch("nai_char._presets", return_value=builtin), patch(
            "char_swap_config.load_config", return_value=custom
        ):
            response = self.client.get("/api/plugin/char-swap/presets?gender=female")

        self.assertEqual(response.status_code, 200)
        presets = response.json()["presets"]
        self.assertEqual([item["id"] for item in presets], ["custom-f", "surtr"])
        self.assertTrue(presets[0]["is_custom"])
        self.assertEqual(presets[0]["source"], "custom")
        self.assertFalse(presets[1]["is_custom"])

    def test_duplicate_custom_oc_name_is_rejected_without_persisting(self) -> None:
        config = {
            "custom_presets": {
                "female": [
                    {
                        "id": "existing-oc",
                        "label": "我的 OC",
                        "gender": "female",
                        "kind": "oc",
                        "char_caption": "1girl, black hair",
                    }
                ],
                "male": [],
            }
        }

        with patch("routes.char_swap.load_char_swap_config", return_value=config), patch(
            "routes.char_swap.save_char_swap_config"
        ) as persist:
            response = self.client.post(
                "/api/plugin/char-swap/presets",
                json={
                    "label": "  我的 oc  ",
                    "gender": "female",
                    "kind": "oc",
                    "char_caption": "1girl, silver hair",
                },
            )

        self.assertEqual(response.status_code, 409)
        self.assertIn("同名", response.json()["detail"])
        persist.assert_not_called()

    def test_invalid_custom_oc_tag_arrays_are_rejected_as_a_client_error(self) -> None:
        with patch("routes.char_swap.save_char_swap_config") as persist:
            response = self.client.post(
                "/api/plugin/char-swap/presets",
                json={"label": "坏数据", "gender": "female", "identity": 123},
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("identity", response.json()["detail"])
        persist.assert_not_called()


class CustomOcPickerContractTests(unittest.TestCase):
    def test_single_and_multi_role_pickers_can_create_then_select_a_custom_oc(self) -> None:
        source = (ROOT / "web/plugins/char-swap/presets.js").read_text(encoding="utf-8")

        self.assertIn("createCustomOcComposer", source)
        self.assertIn("＋ 自定义 OC", source)
        self.assertIn("保存并使用", source)
        self.assertIn('api("/api/plugin/char-swap/presets"', source)
        self.assertIn("selects.forEach", source)
        self.assertIn("is_custom", source)

    def test_saving_an_oc_does_not_inherit_temporary_clothing_layers(self) -> None:
        source = (ROOT / "web/plugins/char-swap/api.js").read_text(encoding="utf-8")

        self.assertIn("ADHOC_BODY_ENDPOINTS", source)
        self.assertIn('"/api/plugin/char-swap/transform"', source)
        self.assertIn("ADHOC_BODY_ENDPOINTS.has", source)
        self.assertNotIn('path.includes("char-swap")', source)


class CustomOcPersistenceTests(unittest.TestCase):
    def test_interrupted_save_keeps_the_previous_custom_oc_file_intact(self) -> None:
        with TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "char_swap_config.json"
            original = '{"custom_presets":{"female":[{"id":"safe","label":"保留我"}],"male":[]}}\n'
            config_path.write_text(original, encoding="utf-8")

            with patch.object(char_swap_config, "CONFIG_PATH", config_path), patch(
                "char_swap_config.os.replace", side_effect=OSError("interrupted")
            ):
                with self.assertRaises(OSError):
                    char_swap_config.save_config({"sanitize_gore": False})

            self.assertEqual(config_path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()

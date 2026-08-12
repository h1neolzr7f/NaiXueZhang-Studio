from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import butler_service
from butler.workflow import _local_read_only_plan
from nai_char import prepare_work_draft
from reference_catalog import ReferenceCatalog


ROOT = Path(__file__).resolve().parents[1]


class ButlerReferenceToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.catalog = ReferenceCatalog(Path(self.temp_dir.name) / "butler-references.db")
        self.catalog.import_records(
            [
                {
                    "character": "skadi_(arknights)",
                    "name": "Skadi",
                    "trigger": "skadi_(arknights)",
                    "tags": "1girl, white_hair, red_eyes, black_dress, standing, masterpiece",
                    "facets": {
                        "style": ["watercolor_(medium)"],
                        "artist": ["artist:alchemaniac"],
                    },
                    "copyright_name": "Arknights",
                    "count": 1234,
                }
            ],
            source="animadex",
            version="2026-07",
            license_name="MIT",
        )

    def tearDown(self) -> None:
        self.catalog.close()
        self.temp_dir.cleanup()

    def test_search_tool_is_typed_read_only_and_local(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch(
            "nai_api.generate_image"
        ) as generate, patch.object(butler_service, "chat_json") as planner:
            action = butler_service.normalize_action(
                {
                    "tool": "search_character_references",
                    "arguments": {"q": "Skadi", "gender": "female", "limit": 6},
                }
            )
            result = butler_service._execute_auto(action)

        self.assertEqual(action["risk"], "read")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["label"], "Skadi")
        self.assertEqual(result["generation_calls"], 0)
        generate.assert_not_called()
        planner.assert_not_called()

    def test_reference_catalog_facets_are_a_typed_zero_token_butler_capability(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch(
            "nai_api.generate_image"
        ) as generate, patch.object(butler_service, "chat_json") as planner:
            action = butler_service.normalize_action(
                {"tool": "inspect_reference_catalog", "arguments": {}}
            )
            result = butler_service._execute_auto(action)

        self.assertEqual(action["risk"], "read")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["copyrights"][0]["name"], "Arknights")
        self.assertEqual(result["sources"][0]["source"], "animadex")
        self.assertEqual(result["genders"], {"female": 1})
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["compiler_version"], 2)
        self.assertEqual(len(result["style_references"]), 2)
        self.assertEqual(result["generation_calls"], 0)
        generate.assert_not_called()
        planner.assert_not_called()

    def test_style_reference_search_is_typed_read_only_and_local(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch(
            "nai_api.generate_image"
        ) as generate, patch.object(butler_service, "chat_json") as planner:
            action = butler_service.normalize_action(
                {
                    "tool": "search_style_references",
                    "arguments": {"q": "watercolor", "kind": "style", "limit": 6},
                }
            )
            result = butler_service._execute_auto(action)

        self.assertEqual(action["risk"], "read")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["items"][0]["tag"], "watercolor (medium)")
        self.assertEqual(result["generation_calls"], 0)
        generate.assert_not_called()
        planner.assert_not_called()

    def test_style_reference_can_drive_existing_manual_style_replacement(self) -> None:
        style_id = self.catalog.search_styles(query="watercolor")["items"][0]["style_id"]
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch(
            "reference_catalog.get_reference_catalog", return_value=self.catalog
        ), patch.object(
            butler_service, "_require_work", return_value={}
        ), patch("nai_api.generate_image") as generate:
            action = butler_service.normalize_action(
                {
                    "tool": "prepare_remix",
                    "arguments": {
                        "work_id": 7,
                        "style": {"reference_id": style_id},
                    },
                }
            )

        style = action["arguments"]["remix_recipe"]["style"]
        self.assertEqual(style["mode"], "preset")
        self.assertEqual(style["replace"], "watercolor (medium)")
        self.assertEqual(style["reference"]["style_id"], style_id)
        self.assertEqual(style["reference"]["source"], "animadex")
        batch_action = {
            "tool": "batch_generate",
            "arguments": {
                "work_ids": [7],
                "page_index": 0,
                "remix_recipe": action["arguments"]["remix_recipe"],
            },
        }
        with patch.object(butler_service, "_batch_targets", return_value=[{}]):
            summary = butler_service._confirmation_summary(batch_action)
        self.assertIn("watercolor (medium)", summary)
        audit = butler_service._audit_summary("batch_generate", batch_action["arguments"])
        self.assertEqual(audit["remix"]["style_reference_label"], "watercolor (medium)")
        generate.assert_not_called()

    def test_reference_catalog_series_question_has_a_zero_planner_token_fast_path(self) -> None:
        plan = _local_read_only_plan("NAI 角色资料库有哪些系列和来源？")

        self.assertIsNotNone(plan)
        self.assertEqual(plan["actions"], [{"tool": "inspect_reference_catalog", "arguments": {}}])
        self.assertIn("本地", plan["reply"])

    def test_prepare_tool_resolves_name_and_builds_exact_studio_slot(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch(
            "nai_api.generate_image"
        ) as generate, patch.object(butler_service, "chat_json") as planner:
            action = butler_service.normalize_action(
                {
                    "tool": "prepare_character_reference",
                    "arguments": {
                        "name": "Skadi",
                        "slot_index": 1,
                        "model": "nai-diffusion-4-5-full",
                        "prompt": "night city",
                        "batch_count": 3,
                    },
                }
            )
            result = butler_service._execute_auto(action)

        self.assertEqual(action["risk"], "draft")
        self.assertNotIn("name", action["arguments"])
        self.assertTrue(action["arguments"]["reference_id"].startswith("ref_"))
        self.assertEqual(result["provider"], "local")
        self.assertEqual(result["generation_calls"], 0)
        self.assertEqual(result["draft"]["params"]["batch"], 3)
        slots = result["draft"]["comment"]["v4_prompt"]["caption"]["char_captions"]
        self.assertEqual(slots[0]["char_caption"], "")
        self.assertIn("skadi", slots[1]["char_caption"].lower())
        self.assertEqual(result["reference"]["provenance"]["license"], "MIT")
        generate.assert_not_called()
        planner.assert_not_called()

    def test_reference_character_can_drive_the_existing_remix_recipe(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch.object(
            butler_service, "_require_work", return_value={}
        ), patch("nai_api.generate_image") as generate:
            action = butler_service.normalize_action(
                {
                    "tool": "prepare_remix",
                    "arguments": {
                        "work_id": 7,
                        "character": {
                            "reference_name": "Skadi",
                            "mode": "replace_female",
                            "preserve_action": True,
                        },
                    },
                }
            )

        transform = action["arguments"]["remix_recipe"]["transform"]
        self.assertEqual(action["risk"], "draft")
        self.assertIn("skadi", transform["custom_char_caption"].lower())
        self.assertEqual(transform["reference"]["label"], "Skadi")
        self.assertEqual(transform["reference"]["source"], "animadex")
        self.assertEqual(transform["mode"], "replace_female")
        self.assertTrue(transform["preserve_action"])
        generate.assert_not_called()

    def test_reference_remix_draft_keeps_provenance_without_generation(self) -> None:
        prepared = {
            "ok": True,
            "patched_comment": {"prompt": "scene", "v4_prompt": {"caption": {}}},
            "chars": [{"summary": "Skadi"}],
            "style_replacements": 0,
            "sanitize_removed": [],
            "message": "角色替换草稿已就绪",
        }
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch.object(
            butler_service, "_require_work", return_value={}
        ):
            action = butler_service.normalize_action(
                {
                    "tool": "prepare_remix",
                    "arguments": {
                        "work_id": 7,
                        "character": {"reference_name": "Skadi", "mode": "replace_female"},
                    },
                }
            )
        with patch("butler.remix.prepare_work_draft", return_value=prepared), patch(
            "butler.remix.import_from_work", return_value={"title": "source", "thumb": ""}
        ), patch(
            "butler.remix.build_studio_draft",
            return_value={"workId": 7, "comment": prepared["patched_comment"]},
        ), patch.object(butler_service, "_require_work", return_value={}), patch(
            "nai_api.generate_image"
        ) as generate:
            result = butler_service._execute_auto(action)

        self.assertEqual(result["tool"], "prepare_remix")
        self.assertEqual(result["reference"]["label"], "Skadi")
        self.assertEqual(result["draft"]["reference"]["referenceId"], result["reference"]["reference_id"])
        self.assertIn("Skadi", result["message"])
        generate.assert_not_called()

    def test_reference_batch_is_confirmed_and_locally_preflighted_before_generation(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch.object(
            butler_service, "_require_work", return_value={}
        ):
            action = butler_service.normalize_action(
                {
                    "tool": "batch_generate",
                    "arguments": {
                        "work_ids": [7, 8],
                        "copies_per_work": 3,
                        "character": {
                            "reference_name": "Skadi",
                            "mode": "replace_female",
                        },
                    },
                }
            )

        self.assertEqual(action["risk"], "confirm")
        self.assertIn("Skadi", butler_service._confirmation_summary(action))
        with patch.object(
            butler_service,
            "_batch_targets",
            return_value=[{"work_id": 7}, {"work_id": 8}],
        ), patch(
            "nai_char.batch_preview",
            return_value={
                "total": 2,
                "items": [
                    {"work_id": 7, "ok": True, "transform_applied": True},
                    {"work_id": 8, "ok": True, "transform_applied": True},
                ],
            },
        ), patch("nai_api.generate_image") as generate:
            preview = butler_service._preview_remix_action(action)

        self.assertEqual(preview["reference_label"], "Skadi")
        self.assertEqual(preview["ready"], 2)
        generate.assert_not_called()

    def test_reference_recipe_uses_the_manual_transform_and_preserves_action(self) -> None:
        with patch.object(butler_service, "get_reference_catalog", return_value=self.catalog), patch.object(
            butler_service, "_require_work", return_value={}
        ):
            action = butler_service.normalize_action(
                {
                    "tool": "prepare_remix",
                    "arguments": {
                        "work_id": 7,
                        "character": {
                            "reference_name": "Skadi",
                            "mode": "replace_female",
                            "preserve_action": True,
                        },
                        "sanitize": False,
                    },
                }
            )

        comment = {
            "prompt": "1girl, city",
            "v4_prompt": {
                "caption": {
                    "base_caption": "1girl, city",
                    "char_captions": [
                        {
                            "char_caption": "girl, original heroine, standing",
                            "centers": [{"x": 0.35, "y": 0.5}],
                        }
                    ],
                }
            },
            "v4_negative_prompt": {"caption": {"base_caption": "", "char_captions": []}},
        }
        extracted = {
            "work_id": 7,
            "page_index": 0,
            "comment": comment,
            "ai_json": {"Comment": comment},
            "chars": [
                {
                    "char_caption": "girl, original heroine, standing",
                    "uc_caption": "",
                    "center": {"x": 0.35, "y": 0.5},
                    "summary": "original heroine",
                    "gender": "female",
                    "bundle": {
                        "gender": "female",
                        "identity": ["original heroine"],
                        "appearance": [],
                        "body": [],
                        "action": ["standing"],
                    },
                }
            ],
            "base_caption": "1girl, city",
            "params": {},
            "prompt_layout": "v4_slots",
            "char_marker_layout": None,
        }
        with patch("nai_char.extract_chars", side_effect=lambda *_args, **_kwargs: copy.deepcopy(extracted)):
            prepared = prepare_work_draft(7, recipe=action["arguments"]["remix_recipe"])

        self.assertTrue(prepared["ok"])
        self.assertTrue(prepared["transform_applied"])
        slot = prepared["patched_comment"]["v4_prompt"]["caption"]["char_captions"][0]
        self.assertIn("skadi", slot["char_caption"].lower())
        self.assertIn("standing", slot["char_caption"].lower())
        self.assertEqual(slot["centers"], [{"x": 0.35, "y": 0.5}])

    def test_unambiguous_reference_search_uses_zero_token_fast_path(self) -> None:
        plan = _local_read_only_plan("搜索角色资料 Skadi")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["actions"][0]["tool"], "search_character_references")
        self.assertEqual(plan["actions"][0]["arguments"]["q"], "skadi")
        self.assertIn("不调用模型", plan["reply"])

    def test_unambiguous_style_reference_search_uses_zero_token_fast_path(self) -> None:
        plan = _local_read_only_plan("搜索画风资料 watercolor")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["actions"][0]["tool"], "search_style_references")
        self.assertEqual(plan["actions"][0]["arguments"]["q"], "watercolor")
        self.assertIn("不调用模型", plan["reply"])

    def test_unambiguous_reference_draft_uses_zero_token_fast_path(self) -> None:
        plan = _local_read_only_plan(
            "用NAI角色资料库里的Skadi，放到角色槽位2，准备一个夜景工作台草稿，不要生成图片。"
        )
        self.assertIsNotNone(plan)
        action = plan["actions"][0]
        self.assertEqual(action["tool"], "prepare_character_reference")
        self.assertEqual(action["arguments"]["name"], "skadi")
        self.assertEqual(action["arguments"]["slot_index"], 1)
        self.assertEqual(action["arguments"]["prompt"], "夜景")
        self.assertIn("不调用模型", plan["reply"])

    def test_unambiguous_reference_swap_draft_uses_zero_token_fast_path(self) -> None:
        plan = _local_read_only_plan(
            "把网站作品7的女性角色换成NAI角色资料库里的Skadi，保持动作，只准备工作台草稿，不要生成。"
        )
        self.assertIsNotNone(plan)
        action = plan["actions"][0]
        self.assertEqual(action["tool"], "prepare_remix")
        self.assertEqual(action["arguments"]["gallery_id"], "site")
        self.assertEqual(action["arguments"]["work_id"], 7)
        self.assertEqual(action["arguments"]["character"]["reference_name"], "skadi")
        self.assertEqual(action["arguments"]["character"]["mode"], "replace_female")
        self.assertTrue(action["arguments"]["character"]["preserve_action"])
        self.assertIn("不调用模型", plan["reply"])
        self.assertIn("不会生成", plan["reply"])

    def test_unambiguous_reference_batch_stages_confirmation_without_planner(self) -> None:
        plan = _local_read_only_plan(
            "用NAI角色资料库里的Skadi替换网站作品7的女性角色，每个生成3张，保持动作。"
        )
        self.assertIsNotNone(plan)
        action = plan["actions"][0]
        self.assertEqual(action["tool"], "batch_generate")
        self.assertEqual(action["arguments"]["work_ids"], [7])
        self.assertEqual(action["arguments"]["copies_per_work"], 3)
        self.assertEqual(action["arguments"]["character"]["reference_name"], "skadi")
        self.assertEqual(action["arguments"]["character"]["mode"], "replace_female")
        self.assertTrue(action["arguments"]["character"]["preserve_action"])
        self.assertIn("不调用规划模型", plan["reply"])
        self.assertIn("确认", plan["reply"])

    def test_planner_history_is_compact_but_durable_store_is_unchanged(self) -> None:
        history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": "x" * 1200}
            for index in range(20)
        ]
        compact = butler_service._trim_history(history)
        self.assertEqual(len(compact), 8)
        self.assertTrue(all(len(item["content"]) == 600 for item in compact))

    def test_butler_ui_renders_search_and_draft_handoff(self) -> None:
        js = (ROOT / "web" / "butler.js").read_text(encoding="utf-8")
        self.assertIn('tool === "search_character_references"', js)
        self.assertIn('tool === "search_style_references"', js)
        self.assertIn('tool === "prepare_character_reference"', js)
        self.assertIn("localStorage.setItem(STUDIO_DRAFT_KEY", js)
        self.assertIn("NAI 角色资料草稿已准备", js)
        self.assertIn("preview.reference_label", js)
        self.assertIn("result.reference.label", js)


if __name__ == "__main__":
    unittest.main()

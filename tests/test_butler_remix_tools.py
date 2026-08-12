from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

import butler_service
from butler.remix import (
    build_remix_targets,
    normalize_remix_recipe,
    prepare_remix_draft,
    prepare_style_reference_draft,
)
from nai_char import prepare_work_draft


class RemixRecipeTests(unittest.TestCase):
    def test_style_label_resolves_to_manual_preset_and_replaces_detected_style(self) -> None:
        manual_config = {
            "style_presets": [
                {"id": "granblue", "label": "碧蓝幻想画风", "style": "granblue_fantasy_(style)"},
                {"id": "clear", "label": "清除画风", "style": ""},
            ],
            "prompt_profile": "native",
        }
        with patch("char_swap_config.load_config", return_value=manual_config):
            recipe = normalize_remix_recipe({"style": {"name": "碧蓝幻想"}})

        self.assertEqual(recipe["style"]["mode"], "preset")
        self.assertEqual(recipe["style"]["preset_id"], "granblue")
        self.assertEqual(recipe["style"]["preset_label"], "碧蓝幻想画风")
        self.assertEqual(recipe["style"]["replace"], "granblue_fantasy_(style)")

    def test_unknown_named_style_is_rejected_instead_of_becoming_free_text(self) -> None:
        with patch("char_swap_config.load_config", return_value={"style_presets": []}):
            with self.assertRaisesRegex(ValueError, "没有找到画风预设"):
                normalize_remix_recipe({"style": {"name": "不存在的画风"}})

    def test_stable_style_reference_is_resolved_inside_the_remix_module(self) -> None:
        catalog = Mock()
        catalog.get_style.return_value = {
            "style_id": "style_watercolor",
            "label": "watercolor (medium)",
            "tag": "watercolor (medium)",
            "kind": "style",
            "source": "animadex",
            "provenance": {"license": "CC-BY-4.0"},
        }
        with patch("reference_catalog.get_reference_catalog", return_value=catalog):
            recipe = normalize_remix_recipe(
                {"style": {"reference_id": "style_watercolor"}}
            )

        self.assertEqual(recipe["style"]["mode"], "preset")
        self.assertEqual(recipe["style"]["replace"], "watercolor (medium)")
        self.assertNotIn("preset_id", recipe["style"])
        self.assertEqual(recipe["style"]["reference"]["style_id"], "style_watercolor")
        self.assertEqual(recipe["style"]["reference"]["provenance"]["license"], "CC-BY-4.0")

    def test_character_label_resolves_to_the_same_preset_used_by_manual_tool(self) -> None:
        presets = [
            {"id": "doctor_m", "label": "博士（兜帽男）", "gender": "male"},
            {"id": "skadi_f", "label": "斯卡蒂", "gender": "female"},
        ]
        manual_config = {
            "preserve_action": False,
            "preserve_center": True,
            "sanitize_racial": True,
            "sanitize_gore": False,
            "sanitize_creature": False,
            "prompt_profile": "native",
        }
        with patch("butler.remix.list_char_presets", return_value=presets), patch(
            "char_swap_config.load_config", return_value=manual_config
        ):
            recipe = normalize_remix_recipe(
                {"character": {"name": "博士", "mode": "replace_male"}}
            )

        self.assertEqual(recipe["transform"]["preset_id"], "doctor_m")
        self.assertEqual(recipe["transform"]["gender"], "male")
        self.assertFalse(recipe["transform"]["preserve_action"])
        self.assertFalse(recipe["sanitize"]["filter_gore"])
        self.assertEqual(recipe["prompt_profile"], "native")

    def test_character_and_style_recipe_is_normalized_to_existing_nai_shape(self) -> None:
        recipe = normalize_remix_recipe(
            {
                "character": {
                    "preset_id": "oc_12gg_f",
                    "gender": "female",
                    "mode": "replace_female",
                    "target": "auto_female",
                    "preserve_action": True,
                },
                "style": {"mode": "append", "replace": "artist:sample"},
                "sanitize": {"enabled": True, "filter_gore": True},
            }
        )

        self.assertEqual(recipe["transform"]["preset_id"], "oc_12gg_f")
        self.assertEqual(recipe["transform"]["target_char_index"], "auto_female")
        self.assertTrue(recipe["transform"]["preserve_action"])
        self.assertEqual(recipe["style"]["mode"], "append")
        self.assertEqual(recipe["style"]["replace"], "artist:sample")
        self.assertTrue(recipe["sanitize"]["enabled"])

    def test_recipe_rejects_unknown_mode_and_requires_a_real_change(self) -> None:
        with self.assertRaisesRegex(ValueError, "mode"):
            normalize_remix_recipe(
                {"character": {"preset_id": "x", "mode": "run_shell"}}
            )
        with self.assertRaisesRegex(ValueError, "换角或换画风"):
            normalize_remix_recipe({"sanitize": True})

    def test_character_preset_gender_deepens_generic_replace_mode(self) -> None:
        with patch(
            "butler.remix.list_char_presets",
            return_value=[{"id": "female-preset", "gender": "female"}],
        ):
            recipe = normalize_remix_recipe(
                {"character": {"preset_id": "female-preset", "mode": "replace"}}
            )
        self.assertEqual(recipe["transform"]["mode"], "replace_female")
        self.assertEqual(recipe["transform"]["gender"], "female")

    def test_batch_targets_keep_recipe_out_of_target_and_increment_seed(self) -> None:
        args = {
            "work_ids": [11, 22],
            "copies_per_work": 2,
            "generation": {"steps": 28, "seed": 100},
            "remix_recipe": {"transform": {"enabled": False}},
        }

        targets = build_remix_targets(args)

        self.assertEqual(len(targets), 4)
        self.assertEqual([item["generation"]["seed"] for item in targets], [100, 101, 102, 103])
        self.assertTrue(all("patched_comment" not in item for item in targets))

    def test_prepare_remix_returns_a_studio_draft(self) -> None:
        prep = {
            "ok": True,
            "patched_comment": {"prompt": "remixed", "steps": 24},
            "chars": [{"summary": "new character"}],
            "style_replacements": 1,
            "sanitize_removed": [],
            "message": "草稿已就绪",
        }
        with patch("butler.remix.prepare_work_draft", return_value=prep), patch(
            "butler.remix.import_from_work",
            return_value={"title": "source", "thumb": "/thumb.webp"},
        ), patch(
            "butler.remix.build_studio_draft", return_value={"workId": 7, "texts": {}}
        ) as build:
            result = prepare_remix_draft(
                {
                    "work_id": 7,
                    "page_index": 0,
                    "batch_count": 1,
                    "remix_recipe": {"style": {"mode": "append", "replace": "style"}},
                    "generation": {"steps": 24},
                }
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["tool"], "prepare_remix")
        self.assertEqual(result["draft"]["workId"], 7)
        build.assert_called_once()

    def test_style_reference_draft_keeps_gallery_identity_and_provenance_without_generation(self) -> None:
        catalog = Mock()
        catalog.get_style.return_value = {
            "style_id": "style_watercolor",
            "label": "水彩画风",
            "tag": "watercolor (medium)",
            "kind": "style",
            "source": "animadex",
            "provenance": {"license": "CC-BY-4.0", "source_url": "https://example.test/style"},
        }
        captured: dict[str, object] = {}

        def prepare(work_id: int, page_index: int, **kwargs: object) -> dict[str, object]:
            captured.update({"work_id": work_id, "page_index": page_index, **kwargs})
            return {
                "ok": True,
                "patched_comment": {"prompt": "remixed"},
                "chars": [],
                "style_replacements": 1,
                "style_applied": True,
                "sanitize_removed": [],
                "message": "换画风草稿已就绪",
            }

        with patch("reference_catalog.get_reference_catalog", return_value=catalog), patch(
            "butler.remix.import_from_work",
            return_value={"title": "法典作品", "thumb": "/thumb.webp", "comment": {"prompt": "base"}},
        ) as imported, patch("butler.remix.prepare_work_draft", side_effect=prepare), patch(
            "butler.remix.build_studio_draft",
            return_value={"workId": 17, "comment": {"prompt": "remixed"}},
        ), patch("nai_api.generate_image") as generate:
            result = prepare_style_reference_draft(
                "style_watercolor",
                gallery_id="codex",
                work_id=17,
                page_index=2,
                mode="append",
            )

        imported.assert_called_once_with(17, 2, "codex")
        recipe = captured["recipe"]
        self.assertEqual(captured["gallery_id"], "codex")
        self.assertEqual(recipe["style"]["mode"], "append")
        self.assertEqual(recipe["style"]["replace"], "watercolor (medium)")
        self.assertEqual(result["draft"]["galleryId"], "codex")
        self.assertEqual(result["draft"]["styleReference"]["styleId"], "style_watercolor")
        self.assertEqual(result["draft"]["styleReference"]["provenance"]["license"], "CC-BY-4.0")
        self.assertEqual(
            result["draft"]["comment"]["_aitag_style_reference"]["mode"], "append"
        )
        self.assertEqual(result["provider"], "local")
        self.assertEqual(result["generation_calls"], 0)
        generate.assert_not_called()

    def test_style_only_remix_does_not_require_character_slots_and_appends_style(self) -> None:
        comment = {
            "prompt": "base",
            "v4_prompt": {"caption": {"base_caption": "base", "char_captions": []}},
        }
        with patch(
            "nai_char.extract_chars",
            return_value={"comment": comment, "chars": [], "base_caption": "base"},
        ):
            result = prepare_work_draft(
                1,
                recipe={
                    "transform": {"enabled": False},
                    "style": {"mode": "append", "replace": "artist:sample"},
                    "sanitize": {"enabled": False},
                },
            )

        self.assertTrue(result["ok"])
        base = result["patched_comment"]["v4_prompt"]["caption"]["base_caption"]
        self.assertIn("artist:sample", base)

    def test_style_preset_clears_recognized_style_before_adding_manual_preset(self) -> None:
        comment = {
            "prompt": "1girl, by old_artist_(artist), watercolor_(style), outdoors",
            "v4_prompt": {
                "caption": {
                    "base_caption": "1girl, by old_artist_(artist), watercolor_(style), outdoors",
                    "char_captions": [],
                }
            },
        }
        with patch(
            "nai_char.extract_chars",
            return_value={"comment": comment, "chars": [], "base_caption": comment["prompt"]},
        ), patch(
            "nai_char.extract_style_slots_from_comment",
            return_value=[
                {"tag": "by old_artist_(artist)"},
                {"tag": "watercolor_(style)"},
            ],
        ):
            result = prepare_work_draft(
                1,
                recipe={
                    "transform": {"enabled": False},
                    "style": {
                        "mode": "preset",
                        "preset_id": "granblue",
                        "preset_label": "碧蓝幻想画风",
                        "replace": "granblue_fantasy_(style)",
                    },
                    "sanitize": {"enabled": False},
                },
            )

        base = result["patched_comment"]["v4_prompt"]["caption"]["base_caption"]
        self.assertNotIn("old_artist", base)
        self.assertNotIn("watercolor", base)
        self.assertIn("granblue_fantasy_(style)", base)
        self.assertTrue(result["style_applied"])
        self.assertGreaterEqual(result["style_replacements"], 1)


class ButlerRemixAdapterTests(unittest.TestCase):
    def test_planner_receives_compact_style_catalog_only_for_style_tasks(self) -> None:
        plan = {"reply": "ok", "actions": []}
        config = {
            "style_presets": [
                {"id": "granblue", "label": "碧蓝幻想画风", "style": "granblue_fantasy_(style)"}
            ]
        }
        with patch("char_swap_config.load_config", return_value=config), patch.object(
            butler_service, "chat_json", return_value=plan
        ) as chat:
            butler_service.request_plan("把作品 7 换成碧蓝幻想画风并生成两张", [])
            style_payload = chat.call_args.args[1]
            butler_service.request_plan("查看最近作品", [])
            plain_payload = chat.call_args.args[1]

        self.assertEqual(
            style_payload["available_style_presets"],
            [{"id": "granblue", "label": "碧蓝幻想画风"}],
        )
        self.assertNotIn("available_style_presets", plain_payload)

    def test_non_site_style_remix_uses_local_prompt_draft_without_character_slot(self) -> None:
        args = {
            "work_ids": [7],
            "work_refs": [{"gallery_id": "qqgroup", "work_id": 7}],
            "copies_per_work": 1,
            "generation": {},
            "remix_recipe": {
                "transform": {"enabled": False},
                "style": {
                    "mode": "preset",
                    "preset_id": "granblue",
                    "replace": "granblue_fantasy_(style)",
                },
            },
        }
        source = {"comment": {"prompt": "1girl, outdoors"}}
        with patch.object(butler_service, "import_from_work", return_value=source):
            targets = butler_service._batch_targets(args)

        self.assertEqual(targets[0]["gallery_id"], "qqgroup")
        self.assertEqual(targets[0]["patched_comment"], source["comment"])

    def test_style_preflight_reports_named_preset_without_generation(self) -> None:
        action = {
            "tool": "batch_generate",
            "arguments": {
                "work_ids": [7],
                "copies_per_work": 1,
                "remix_recipe": {
                    "transform": {"enabled": False},
                    "style": {
                        "mode": "preset",
                        "preset_id": "granblue",
                        "preset_label": "碧蓝幻想画风",
                        "replace": "granblue_fantasy_(style)",
                    },
                },
            },
        }
        with patch.object(butler_service, "_batch_targets", return_value=[{"work_id": 7}]), patch(
            "nai_char.batch_preview",
            return_value={
                "total": 1,
                "ready": 1,
                "items": [{"work_id": 7, "page_index": 0, "ok": True, "style_applied": True}],
            },
        ):
            result = butler_service._preview_remix_action(action)

        self.assertEqual(result["kind"], "style_remix")
        self.assertEqual(result["style_preset_label"], "碧蓝幻想画风")
        self.assertEqual(result["ready"], 1)

    def test_style_preflight_preserves_non_site_gallery_identity(self) -> None:
        action = {
            "tool": "batch_generate",
            "arguments": {
                "work_ids": [7],
                "copies_per_work": 1,
                "remix_recipe": {
                    "transform": {"enabled": False},
                    "style": {"mode": "preset", "preset_id": "granblue", "replace": "style"},
                },
            },
        }
        with patch.object(butler_service, "_batch_targets", return_value=[{"gallery_id": "qqgroup", "work_id": 7}]), patch(
            "nai_char.batch_preview",
            return_value={
                "total": 1,
                "items": [{
                    "gallery_id": "qqgroup",
                    "work_id": 7,
                    "page_index": 0,
                    "ok": True,
                    "style_applied": True,
                }],
            },
        ):
            result = butler_service._preview_remix_action(action)

        self.assertEqual(result["items"][0]["gallery_id"], "qqgroup")

    def test_all_pages_expand_to_real_page_identities_before_generation(self) -> None:
        args = {
            "gallery_id": "site",
            "work_ids": [7],
            "work_refs": [{"gallery_id": "site", "work_id": 7}],
            "all_pages": True,
            "page_index": 0,
            "copies_per_work": 2,
            "generation": {"seed": 10},
            "remix_recipe": {"transform": {"enabled": True, "preset_id": "doctor_m"}},
        }
        detail = {"work": {"image_count": 3}, "images": [
            {"page_index": 0}, {"page_index": 1}, {"page_index": 2},
        ]}

        with patch.object(butler_service, "_require_work", return_value=detail):
            targets = butler_service._batch_targets(args)

        self.assertEqual(len(targets), 6)
        self.assertEqual([item["page_index"] for item in targets], [0, 0, 1, 1, 2, 2])
        self.assertEqual([item["generation"]["seed"] for item in targets], list(range(10, 16)))
        self.assertTrue(all(item["gallery_id"] == "site" for item in targets))

    def test_confirmation_preflight_uses_manual_transform_without_generation(self) -> None:
        action = {
            "tool": "batch_generate",
            "arguments": {
                "work_ids": [7],
                "copies_per_work": 1,
                "remix_recipe": {
                    "transform": {
                        "enabled": True,
                        "preset_id": "doctor_m",
                        "preset_label": "博士",
                    }
                },
            },
        }
        with patch.object(butler_service, "_batch_targets", return_value=[{"work_id": 7}]), patch(
            "nai_char.batch_preview",
            return_value={
                "total": 1,
                "ready": 1,
                "items": [{
                    "work_id": 7,
                    "page_index": 0,
                    "ok": True,
                    "summary": "博士",
                    "transform_applied": True,
                }],
            },
        ) as preview:
            result = butler_service._preview_remix_action(action)

        self.assertEqual(result["ready"], 1)
        self.assertEqual(result["preset_label"], "博士")
        preview.assert_called_once()

    def test_non_site_character_swap_is_rejected_instead_of_silently_degrading(self) -> None:
        with self.assertRaisesRegex(ValueError, "角色槽"):
            butler_service._batch_targets(
                {
                    "work_ids": [7],
                    "work_refs": [{"gallery_id": "qqgroup", "work_id": 7}],
                    "copies_per_work": 1,
                    "remix_recipe": {"transform": {"enabled": True, "preset_id": "doctor_m"}},
                }
            )

    def test_generate_with_character_replacement_uses_tracked_generation_job(self) -> None:
        with patch.object(butler_service, "_require_work", return_value={}):
            action = butler_service.normalize_action(
                {
                    "tool": "generate_image",
                    "arguments": {
                        "work_id": 7,
                        "page_index": 2,
                        "batch_count": 3,
                        "character": {
                            "preset_id": "oc_12gg_f",
                            "mode": "replace_female",
                        },
                    },
                }
            )

        self.assertEqual(action["tool"], "batch_generate")
        self.assertEqual(action["arguments"]["copies_per_work"], 3)
        self.assertEqual(action["arguments"]["page_index"], 2)
        self.assertIn("remix_recipe", action["arguments"])

    def test_multi_character_names_reuse_manual_slot_replacement_recipe(self) -> None:
        action = butler_service.normalize_action(
            {
                "tool": "batch_generate",
                "arguments": {
                    "work_ids": [145743565],
                    "copies_per_work": 1,
                    "character": {
                        "mode": "replace_multi",
                        "replacements": [
                            {"name": "doctor_m"},
                            {"name": "skadi_f"},
                        ],
                    },
                },
            }
        )

        transform = action["arguments"]["remix_recipe"]["transform"]
        self.assertEqual(transform["mode"], "replace_multi")
        self.assertEqual(
            [(item["preset_id"], item["gender"], item["gender_slot_index"]) for item in transform["replacements"]],
            [("doctor_m", "male", 0), ("skadi_f", "female", 0)],
        )


    def test_planner_receives_compact_character_catalog_only_for_replacement_tasks(self) -> None:
        plan = {"reply": "ok", "actions": []}
        presets = [{"id": "doctor_m", "label": "博士（兜帽男）", "gender": "male"}]
        with patch("butler.remix.list_char_presets", return_value=presets), patch.object(
            butler_service, "chat_json", return_value=plan
        ) as chat:
            butler_service.request_plan("把作品 7 的男性角色换成博士并生成一张", [])
            remix_payload = chat.call_args.args[1]
            butler_service.request_plan("查看最近作品", [])
            plain_payload = chat.call_args.args[1]

        self.assertEqual(
            remix_payload["available_character_presets"],
            [{"id": "doctor_m", "label": "博士（兜帽男）", "gender": "male"}],
        )
        self.assertNotIn("available_character_presets", plain_payload)

    def test_prepare_remix_is_a_draft_and_generation_reuses_same_recipe(self) -> None:
        with patch.object(butler_service, "_require_work", return_value={}):
            draft = butler_service.normalize_action(
                {
                    "tool": "prepare_remix",
                    "arguments": {
                        "work_id": 7,
                        "character": {
                            "preset_id": "oc_12gg_f",
                            "mode": "replace_female",
                        },
                    },
                }
            )
            generation = butler_service.normalize_action(
                {
                    "tool": "generate_image",
                    "arguments": {
                        "work_id": 7,
                        "batch_count": 1,
                        "style": {"mode": "append", "replace": "artist:sample"},
                    },
                }
            )

        self.assertEqual(draft["risk"], "draft")
        self.assertIn("remix_recipe", draft["arguments"])
        self.assertEqual(generation["risk"], "confirm")
        self.assertIn("remix_recipe", generation["arguments"])

    def test_batch_remix_uses_raw_targets_and_recipe(self) -> None:
        with patch.object(butler_service, "_require_work", return_value={}):
            action = butler_service.normalize_action(
                {
                    "tool": "batch_generate_and_prepare_pixiv",
                    "arguments": {
                        "work_ids": [7],
                        "copies_per_work": 1,
                        "character": {
                            "preset_id": "oc_12gg_f",
                            "mode": "replace_female",
                        },
                    },
                }
            )

        targets = butler_service._batch_targets(action["arguments"])
        self.assertEqual(targets[0]["work_id"], 7)
        self.assertNotIn("patched_comment", targets[0])
        self.assertIn("remix_recipe", action["arguments"])

    def test_read_only_production_and_operations_tools_return_sanitized_shapes(self) -> None:
        production = butler_service.normalize_action(
            {"tool": "inspect_production", "arguments": {"limit": 3}}
        )
        operations = butler_service.normalize_action(
            {"tool": "inspect_operations", "arguments": {}}
        )
        self.assertEqual(production["risk"], "read")
        self.assertEqual(operations["risk"], "read")

        with patch("generated_gallery.list_groups", return_value=[]), patch(
            "post_pipeline.pipeline_status", return_value={"status": "idle"}
        ), patch("nai_batch.batch_status", return_value={"status": "done"}):
            prod_result = butler_service._execute_auto(production)
        self.assertEqual(prod_result["tool"], "inspect_production")

        health = {"ok": True, "checks": {"database": True}, "warnings": [], "paths": {"secret": "x"}}
        with patch.object(butler_service.CRAWLER_WATCHDOG, "status", return_value={"enabled": False}), patch(
            "butler_service.build_product_health", return_value=health
        ):
            ops_result = butler_service._execute_auto(operations)
        self.assertNotIn("paths", ops_result["health"])


if __name__ == "__main__":
    unittest.main()

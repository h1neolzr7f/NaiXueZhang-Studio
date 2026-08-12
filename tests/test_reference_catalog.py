from __future__ import annotations

import tempfile
import unittest
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import server
from nai_anima_adapter import adapt_anima_character
from reference_catalog import ReferenceCatalog
from tests.asgi_client import TestClient


ROOT = Path(__file__).resolve().parents[1]


def animadex_row(**overrides) -> dict:
    row = {
        "character": "skadi_(arknights)",
        "name": "Skadi",
        "trigger": "skadi_(arknights)",
        "tags": "1girl, white_hair, red_eyes, black_dress, standing, masterpiece",
        "copyright": "arknights",
        "copyright_name": "Arknights",
        "count": "1234",
        "thumb_url": "https://blobs.animadex.net/Outputs/thumbs/skadi.webp",
    }
    row.update(overrides)
    return row


class ReferenceCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.catalog = ReferenceCatalog(Path(self.temp_dir.name) / "references.db")

    def tearDown(self) -> None:
        self.catalog.close()
        self.temp_dir.cleanup()

    def test_real_animadex_export_fields_compile_for_nai(self) -> None:
        card = adapt_anima_character(animadex_row(), model="nai-diffusion-4-5-full")
        self.assertEqual(card["source_id"], "skadi_(arknights)")
        self.assertEqual(card["base_subject_tag"], "1girl")
        self.assertIn("white hair", card["character_caption"])
        self.assertNotIn("standing", card["character_caption"])
        self.assertNotIn("masterpiece", card["character_caption"])

    def test_import_is_incremental_and_keeps_stable_identity(self) -> None:
        first = self.catalog.import_records(
            [animadex_row()], source="animadex", version="2026-07", license_name="MIT"
        )
        self.assertEqual(first["inserted"], 1)
        self.assertEqual(first["updated"], 0)
        reference_id = self.catalog.search(query="Skadi")["items"][0]["reference_id"]

        second = self.catalog.import_records(
            [animadex_row()], source="animadex", version="2026-07", license_name="MIT"
        )
        self.assertEqual(second["unchanged"], 1)

        changed = animadex_row(tags="1girl, white_hair, red_eyes, black_dress, hair_ornament")
        third = self.catalog.import_records(
            [changed], source="animadex", version="2026-07", license_name="MIT"
        )
        self.assertEqual(third["updated"], 1)
        detail = self.catalog.get(reference_id)
        self.assertIsNotNone(detail)
        self.assertEqual(detail["reference_id"], reference_id)
        self.assertIn("hair ornament", detail["character_caption"])
        self.assertEqual(detail["provenance"]["license"], "MIT")

    def test_search_and_filters_return_compact_cards(self) -> None:
        self.catalog.import_records(
            [
                animadex_row(),
                animadex_row(
                    character="silverash_(arknights)",
                    name="SilverAsh",
                    trigger="silverash_(arknights)",
                    tags="1boy, silver_hair, formal_clothes",
                    count=88,
                ),
            ],
            source="animadex",
        )
        female = self.catalog.search(gender="female")
        self.assertEqual(female["total"], 1)
        self.assertEqual(female["items"][0]["label"], "Skadi")
        male = self.catalog.search(query="silver", copyright_name="Arknights", gender="male")
        self.assertEqual(male["total"], 1)
        self.assertEqual(male["items"][0]["source_id"], "silverash_(arknights)")
        stats = self.catalog.stats()
        self.assertEqual(stats["total"], 2)
        self.assertEqual(stats["generation_calls"], 0)
        self.assertEqual(stats["sources"][0]["record_count"], 2)

    def test_alias_traits_and_styles_are_separate_searchable_facets(self) -> None:
        row = animadex_row(
            aliases=["Skadi the Corrupting Heart", "浊心斯卡蒂"],
            facets={
                "hair": ["long_hair", "white_hair"],
                "eyes": ["red_eyes"],
                "artist": ["artist:alchemaniac"],
                "style": ["watercolor_(medium)"],
            },
        )
        self.catalog.import_records([row], source="animadex", version="2026-07")

        by_alias = self.catalog.search(query="浊心斯卡蒂")
        detail = self.catalog.get(by_alias["items"][0]["reference_id"])
        stats = self.catalog.stats()

        self.assertEqual(by_alias["total"], 1)
        self.assertEqual(detail["aliases"], ["Skadi the Corrupting Heart", "浊心斯卡蒂"])
        self.assertIn(
            {"facet": "hair", "trait": "long hair"},
            detail["traits"],
        )
        related_styles = self.catalog.related_styles(detail["reference_id"])
        self.assertEqual(
            [item["tag"] for item in related_styles["items"]],
            ["artist:alchemaniac", "watercolor (medium)"],
        )
        self.assertNotIn("style_hints", detail)
        self.assertNotIn("style_references", detail)
        self.assertNotIn("alchemaniac", detail["character_caption"])
        self.assertEqual(self.catalog.search(query="watercolor")["total"], 0)
        self.assertEqual(stats["style_references"][0]["linked_characters"], 1)

    def test_style_references_have_an_independent_search_interface(self) -> None:
        self.catalog.import_records(
            [
                animadex_row(
                    facets={
                        "artist": ["artist:alchemaniac"],
                        "style": ["watercolor_(medium)"],
                    }
                )
            ],
            source="animadex",
        )
        reference_id = self.catalog.search(query="Skadi")["items"][0]["reference_id"]

        styles = self.catalog.search_styles(query="watercolor", kind="style")
        related = self.catalog.related_styles(reference_id)

        self.assertEqual(styles["total"], 1)
        self.assertEqual(styles["items"][0]["tag"], "watercolor (medium)")
        self.assertEqual(len(related["items"]), 2)
        self.assertEqual(related["reference_id"], reference_id)

    def test_schema_v1_reopen_backfills_derived_facets_once(self) -> None:
        db_path = Path(self.temp_dir.name) / "references.db"
        self.catalog.import_records(
            [
                animadex_row(
                    aliases=["浊心斯卡蒂"],
                    facets={"hair": ["long_hair"], "style": ["watercolor_(medium)"]},
                )
            ],
            source="animadex",
        )
        reference_id = self.catalog.search(query="Skadi")["items"][0]["reference_id"]
        self.catalog.close()
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("DELETE FROM reference_aliases")
            conn.execute("DELETE FROM reference_traits")
            conn.execute("DELETE FROM reference_style_links")
            conn.execute("DELETE FROM style_references")
            conn.execute("PRAGMA user_version=1")
            conn.commit()

        migrated = ReferenceCatalog(db_path)
        self.assertEqual(migrated.search(query="浊心斯卡蒂")["total"], 1)
        self.assertEqual(migrated.related_styles(reference_id)["total"], 1)
        first_stats = migrated.stats()
        migrated.close()

        reopened = ReferenceCatalog(db_path)
        self.assertEqual(reopened.stats()["style_references"], first_stats["style_references"])
        reopened.close()

    def test_future_schema_version_is_rejected_without_mutation(self) -> None:
        db_path = Path(self.temp_dir.name) / "future-references.db"
        with closing(sqlite3.connect(db_path)) as conn:
            conn.execute("PRAGMA user_version=999")
            conn.commit()

        with self.assertRaisesRegex(RuntimeError, "高于程序支持版本"):
            ReferenceCatalog(db_path)


class ReferenceCatalogRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.catalog = ReferenceCatalog(Path(self.temp_dir.name) / "route-references.db")
        self.client = TestClient(server.app)
        self.catalog.import_records([animadex_row()], source="animadex", license_name="MIT")
        self.reference_id = self.catalog.search()["items"][0]["reference_id"]

    def tearDown(self) -> None:
        self.catalog.close()
        self.temp_dir.cleanup()

    def test_routes_search_detail_and_apply_without_generation(self) -> None:
        with patch("routes.references.get_reference_catalog", return_value=self.catalog), patch(
            "nai_api.generate_image"
        ) as generate:
            listing = self.client.get("/api/nai/references?q=skadi")
            detail = self.client.get(f"/api/nai/references/{self.reference_id}")
            applied = self.client.post(
                f"/api/nai/references/{self.reference_id}/apply",
                json={
                    "comment": {
                        "prompt": "night city",
                        "v4_prompt": {"caption": {"base_caption": "night city", "char_captions": []}},
                    },
                    "slot_index": 1,
                    "model": "nai-diffusion-4-5-full",
                },
            )
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(applied.status_code, 200)
        body = applied.json()
        self.assertEqual(body["provider"], "local")
        self.assertEqual(body["generation_calls"], 0)
        self.assertIn("Skadi", body["message"])
        self.assertEqual(len(body["texts"]["char_captions"]), 2)
        generate.assert_not_called()

    def test_import_endpoint_is_bounded_and_validates_shape(self) -> None:
        with patch("routes.references.get_reference_catalog", return_value=self.catalog):
            invalid = self.client.post("/api/nai/references/import", json={"records": {}})
            oversized = self.client.post(
                "/api/nai/references/import", json={"records": [{} for _ in range(1001)]}
            )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(oversized.status_code, 413)

    def test_style_reference_routes_are_independent_from_character_detail(self) -> None:
        self.catalog.import_records(
            [animadex_row(facets={"style": ["watercolor_(medium)"]})],
            source="animadex",
        )
        with patch("routes.references.get_reference_catalog", return_value=self.catalog):
            listing = self.client.get("/api/nai/references/styles?q=watercolor")
            related = self.client.get(f"/api/nai/references/{self.reference_id}/styles")

        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["kind"], "style")
        self.assertEqual(related.status_code, 200)
        self.assertEqual(related.json()["reference_id"], self.reference_id)

    def test_style_reference_draft_route_uses_the_canonical_remix_interface(self) -> None:
        self.catalog.import_records(
            [animadex_row(facets={"style": ["watercolor_(medium)"]})],
            source="animadex",
        )
        style_id = self.catalog.search_styles(query="watercolor")["items"][0]["style_id"]
        prepared = {
            "ok": True,
            "tool": "prepare_remix",
            "remix_kind": "style",
            "draft": {"workId": 7, "galleryId": "codex", "comment": {"prompt": "watercolor"}},
            "studio_url": "/studio?remix=1&gallery=codex",
            "message": "watercolor · 画风 Remix 草稿已就绪",
            "generation_calls": 0,
        }
        with patch("routes.references.get_reference_catalog", return_value=self.catalog), patch(
            "routes.references.prepare_style_reference_draft", return_value=prepared
        ) as prepare, patch("nai_api.generate_image") as generate:
            response = self.client.post(
                f"/api/nai/references/styles/{style_id}/draft",
                json={
                    "gallery_id": "codex",
                    "work_id": "7",
                    "page_index": 1,
                    "mode": "append",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["draft"]["galleryId"], "codex")
        prepare.assert_called_once_with(
            style_id,
            gallery_id="codex",
            work_id=7,
            page_index=1,
            mode="append",
        )
        generate.assert_not_called()

    def test_style_reference_draft_route_rejects_bad_identity_and_maps_missing_style(self) -> None:
        invalid_gallery = self.client.post(
            "/api/nai/references/styles/style_missing/draft",
            json={"gallery_id": "other", "work_id": 7},
        )
        invalid_page = self.client.post(
            "/api/nai/references/styles/style_missing/draft",
            json={"gallery_id": "site", "work_id": 7, "page_index": 1000},
        )
        from butler.remix import StyleReferenceNotFound

        with patch(
            "routes.references.prepare_style_reference_draft",
            side_effect=StyleReferenceNotFound("指定的 NAI 画风资料不存在"),
        ):
            missing = self.client.post(
                "/api/nai/references/styles/style_missing/draft",
                json={"gallery_id": "site", "work_id": 7},
            )

        self.assertEqual(invalid_gallery.status_code, 400)
        self.assertEqual(invalid_page.status_code, 400)
        self.assertEqual(missing.status_code, 404)

    def test_desktop_page_and_local_draft_contract(self) -> None:
        page = self.client.get("/references")
        self.assertEqual(page.status_code, 200)
        html = page.text
        js = (ROOT / "web" / "references.js").read_text(encoding="utf-8")
        studio_js = (ROOT / "web" / "studio.js").read_text(encoding="utf-8")
        self.assertIn("NAI 角色与画风资料库", html)
        self.assertIn("refImportFile", html)
        self.assertIn("应用并打开 Studio", html)
        self.assertNotIn('id="refImportLicense" value="MIT"', html)
        self.assertIn("数据与图片许可", html)
        self.assertIn("不会自动视为 MIT", html)
        self.assertIn("/api/nai/references", js)
        self.assertIn("/styles", js)
        self.assertIn("item.aliases", js)
        self.assertIn("item.traits", js)
        self.assertIn("aitag.studio.draft.v1", js)
        self.assertNotIn("/api/nai/generate", js)
        self.assertIn("comment: commentFromForm()", studio_js)

    def test_desktop_page_exposes_independent_style_browsing(self) -> None:
        page = self.client.get("/references?tab=styles&q=watercolor")

        self.assertEqual(page.status_code, 200)
        html = page.text
        js = (ROOT / "web" / "references.js").read_text(encoding="utf-8")
        self.assertIn('id="refModeStyles"', html)
        self.assertIn('id="refStyleKind"', html)
        self.assertIn('id="refStyleDetail"', html)
        self.assertIn("独立画风资料", html)
        self.assertIn("准备 Remix 草稿", html)
        self.assertIn('id="refStyleTargetText"', html)
        self.assertIn("高级指定来源作品", html)
        self.assertIn('id="refStyleApply"', html)
        self.assertIn("updateStyleTarget", js)
        self.assertIn("/api/nai/references/styles", js)
        self.assertIn('get("tab")', js)


if __name__ == "__main__":
    unittest.main()

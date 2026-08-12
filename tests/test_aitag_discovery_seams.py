from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from aitag_core.external import normalize_aitag_detail, normalize_aitag_search
from aitag_core.recipe import CharacterAsset, discover_character_candidates
from aitag_core.studio import AitagMetadataAdapter, compile_aitag_studio_draft
from routes import aitag as aitag_routes


def _detail():
    return normalize_aitag_detail(
        {
            "id": "work-7",
            "title": "Remote character",
            "AI_type": "NovelAI",
            "images": [
                {
                    "id": "blue",
                    "model": "nai-diffusion-4-5-full",
                    "promptText": (
                        "1girl, blue_hair, blue_eyes, cinematic cafe, watercolor"
                    ),
                },
                {
                    "id": "red",
                    "model": "nai-diffusion-4-5-full",
                    "promptText": "1girl, red_hair, green_eyes, moonlit street",
                },
            ],
        }
    )


def test_search_normalizes_multi_image_qualification_and_safe_rating() -> None:
    page = normalize_aitag_search(
        {
            "items": [
                {
                    "id": "safe",
                    "AI_type": "NovelAI",
                    "tags": ["1girl"],
                    "images": [{"id": "a", "promptText": "1girl, cafe"}],
                },
                {
                    "id": "adult",
                    "AI_type": "NovelAI",
                    "tags": ["rating:explicit"],
                    "images": [{"id": "b", "promptText": "1girl"}],
                },
            ]
        }
    )

    assert page.works[0].qualification == "direct"
    assert page.works[1].qualification == "review"
    assert "unsafe-rating-or-tag" in page.works[1].qualification_reasons


def test_metadata_adapter_and_candidates_keep_each_remote_image_distinct() -> None:
    detail = _detail()
    candidates = discover_character_candidates(detail)

    assert [(item.image_index, item.character.appearance_tags) for item in candidates] == [
        (0, ("blue_hair", "blue_eyes")),
        (1, ("red_hair", "green_eyes")),
    ]
    assert AitagMetadataAdapter(detail).load("work-7", 1)["Description"].endswith(
        "moonlit street"
    )


def test_blank_remix_draft_preserves_scene_and_only_swaps_character_slot() -> None:
    detail = _detail()
    target = {
        "id": "local-target",
        "name": "Local target",
        "source": "local",
        "core_tags": ["1girl", "hatsune_miku", "pink_hair"],
    }

    compiled = compile_aitag_studio_draft(
        detail,
        image_index=0,
        slot_index=0,
        target_record=target,
        target_reference_id="ref_local",
    )

    caption = compiled["draft"]["comment"]["v4_prompt"]["caption"]
    assert caption["base_caption"] == "cinematic cafe, watercolor"
    assert "hatsune miku" in caption["char_captions"][0]["char_caption"]
    assert "blue hair" not in caption["char_captions"][0]["char_caption"]
    assert compiled["recipe"]["prompt"].endswith("cinematic cafe, watercolor")
    assert compiled["recipe"]["character"]["label"] == "Local target"
    assert compiled["draft"]["reference"]["referenceId"] == "ref_local"


def test_character_asset_reference_conversion_is_total() -> None:
    asset = CharacterAsset.from_reference_record(
        {"id": "local", "name": "Local", "core_tags": ["1girl", "blue_hair"]}
    )

    assert asset.label == "Local"
    assert asset.identity_tags == ("1girl", "blue_hair")


class _Client:
    def get_work(self, work_id: str):
        assert work_id == "work-7"
        return _detail()

    def get_config(self):
        from aitag_core.external import AitagConfig

        return AitagConfig()


class _Catalog:
    def __init__(self) -> None:
        self.options = None
        self.records = None

    def get(self, reference_id: str):
        if reference_id != "ref_local":
            return None
        return {
            "raw": {
                "id": "local-target",
                "name": "Local target",
                "source": "local",
                "core_tags": ["1girl", "hatsune_miku", "pink_hair"],
            }
        }

    def import_records(self, records, **options):
        self.records = records
        self.options = options
        return {"ok": True, "inserted": 1}

    def search(self, **kwargs):
        return {"items": [{"reference_id": "ref-imported"}]}


def test_draft_route_supports_blank_source_and_optional_local_target(tmp_path: Path) -> None:
    catalog = _Catalog()
    with patch.object(aitag_routes, "_require_online", return_value=_Client()), patch.object(
        aitag_routes, "get_reference_catalog", return_value=catalog
    ), patch.object(aitag_routes, "DATA_DIR", tmp_path):
        result = aitag_routes.api_aitag_draft(
            "work-7",
            {
                "image_index": 0,
                "slot_index": 0,
                "target_reference_id": "ref_local",
            },
        )

    assert result["generation_calls"] == 0
    assert result["draft_id"]
    assert result["studio_url"] == f"/studio?aitag=1&remix=1&draft={result['draft_id']}"
    assert result["draft"]["comment"]["v4_prompt"]["caption"]["base_caption"] == (
        "cinematic cafe, watercolor"
    )


def test_import_never_trusts_browser_license_claim_and_keeps_provenance() -> None:
    catalog = _Catalog()
    with patch.object(aitag_routes, "_require_online", return_value=_Client()), patch.object(
        aitag_routes, "get_reference_catalog", return_value=catalog
    ):
        result = aitag_routes.api_aitag_import(
            {"work_id": "work-7", "image_index": 0, "license": "CC0 trust me"}
        )

    record = catalog.records[0]
    assert catalog.options["license_name"] == "unknown"
    assert record["license_status"] == "unknown"
    assert record["license_note"] == "CC0 trust me"
    assert record["remote_work_id"] == "work-7"
    assert record["remote_image_id"] == "blue"
    assert record["provenance"]["source_url"] == "https://aitag.win/i/work-7"
    assert record["provenance"]["retrieved_at"]
    assert "图片仍使用远程链接" in result["message"]

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from aitag_core.external import AitagConfig, normalize_aitag_detail
from routes import aitag as aitag_routes


def _multi_slot_detail():
    return normalize_aitag_detail(
        {
            "id": "work-multi",
            "title": "Two people",
            "AI_type": "NovelAI",
            "license": "CC-BY-4.0",
            "images": [
                {
                    "id": "pair",
                    "model": "nai-diffusion-4-5-full",
                    "ai_json": {
                        "v4_prompt": {
                            "caption": {
                                "base_caption": "cafe, cinematic lighting",
                                "char_captions": [
                                    {"char_caption": "1girl, blue hair, blue eyes"},
                                    {"char_caption": "1boy, red hair, green eyes"},
                                ],
                            }
                        }
                    },
                }
            ],
        }
    )


class _Client:
    def get_work(self, work_id: str):
        assert work_id == "work-multi"
        return _multi_slot_detail()

    def get_config(self):
        return AitagConfig()


class _Catalog:
    def __init__(self) -> None:
        self.records = []
        self.options = {}

    def import_records(self, records, **options):
        self.records = records
        self.options = options
        return {"inserted": len(records)}

    def search(self, **_kwargs):
        return {"items": [{"reference_id": "ref_second", "label": "Second"}]}


def test_import_selects_exact_multi_character_candidate_and_real_source_license() -> None:
    catalog = _Catalog()
    candidate_id = "work-multi/pair/slot-1"
    with patch.object(aitag_routes, "_require_online", return_value=_Client()), patch.object(
        aitag_routes, "get_reference_catalog", return_value=catalog
    ):
        result = aitag_routes.api_aitag_import(
            {
                "work_id": "work-multi",
                "image_index": 0,
                "slot_index": 1,
                "candidate_id": candidate_id,
            }
        )

    record = catalog.records[0]
    assert result["candidate_id"] == candidate_id
    assert result["slot_index"] == 1
    assert record["candidate_id"] == candidate_id
    assert "1boy" in record["core_tags"]
    assert "1girl" not in record["core_tags"]
    assert catalog.options["license_name"] == "CC-BY-4.0"
    assert result["license_name"] == "CC-BY-4.0"
    assert result["license_status"] == "source-provided"


def test_import_rejects_candidate_that_does_not_match_requested_slot() -> None:
    with patch.object(aitag_routes, "_require_online", return_value=_Client()):
        with pytest.raises(HTTPException) as raised:
            aitag_routes.api_aitag_import(
                {
                    "work_id": "work-multi",
                    "image_index": 0,
                    "slot_index": 0,
                    "candidate_id": "work-multi/pair/slot-1",
                }
            )
    assert raised.value.status_code == 400


def test_legacy_apply_seam_also_uses_the_exact_multi_character_candidate() -> None:
    candidate_id = "work-multi/pair/slot-1"
    with patch.object(aitag_routes, "_require_online", return_value=_Client()):
        result = aitag_routes.api_aitag_apply(
            "work-multi",
            {
                "comment": {},
                "image_index": 0,
                "slot_index": 1,
                "candidate_id": candidate_id,
            },
        )

    slots = result["comment"]["v4_prompt"]["caption"]["char_captions"]
    assert result["candidate_id"] == candidate_id
    assert result["slot_index"] == 1
    assert "red hair" in slots[1]["char_caption"]
    assert "blue hair" not in slots[1]["char_caption"]


def test_draft_compilation_survives_persistence_failure() -> None:
    with patch.object(aitag_routes, "_require_online", return_value=_Client()), patch.object(
        aitag_routes, "save_studio_draft", side_effect=OSError("disk full")
    ):
        result = aitag_routes.api_aitag_draft(
            "work-multi", {"image_index": 0, "slot_index": 1}
        )

    assert result["ok"] is True
    assert result["generation_calls"] == 0
    assert result["persisted"] is False
    assert result["draft_id"] == ""
    assert result["studio_url"] == "/studio?aitag=1&remix=1"
    assert result["draft"]["comment"]
    assert "disk full" in result["persistence_warning"]

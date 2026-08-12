from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import nai_api
import nai_batch
from aitag_core.external import (
    normalize_aitag_config,
    normalize_aitag_detail,
    normalize_aitag_search,
)
from aitag_core.online import AitagClient, AitagClientError, validate_aitag_base_url
from routes import aitag as aitag_routes


class _Response:
    def __init__(
        self,
        payload: object,
        status_code: int = 200,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.headers = dict(headers or {})
        self.content = json.dumps(payload).encode("utf-8")
        self.text = self.content.decode("utf-8")

    def json(self) -> object:
        return self._payload


class _HTTP:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def get(self, url: str, *, params: dict[str, object]) -> _Response:
        self.calls.append((url, dict(params)))
        return self.responses.pop(0)


def _detail():
    return normalize_aitag_detail(
        {
            "id": "work-42",
            "title": "Two image character",
            "AI_type": "NovelAI",
            "tags": ["1girl", "blue_hair", "red_hair"],
            "images": [
                {
                    "id": "image-blue",
                    "workId": "work-42",
                    "url": "https://ai-img.10118899.xyz/blue.png",
                    "thumbnail": "https://ai-img.10118899.xyz/blue-thumb.png",
                    "model": "nai-diffusion-4-5-full",
                    "promptText": "1girl, blue_hair, blue_eyes, outdoors, cinematic lighting",
                },
                {
                    "id": "image-red",
                    "workId": "work-42",
                    "url": "https://ai-img.10118899.xyz/red.png",
                    "thumbnail": "https://ai-img.10118899.xyz/red-thumb.png",
                    "model": "nai-diffusion-4-5-full",
                    "promptText": "1girl, red_hair, green_eyes, outdoors, cinematic lighting",
                },
            ],
        }
    )


class _Client:
    def __init__(self) -> None:
        self.detail = _detail()

    def get_work(self, work_id: str):
        assert work_id == "work-42"
        return self.detail

    def get_config(self):
        return normalize_aitag_config(
            {"asset_base_url": "https://ai-img.10118899.xyz/"}
        )

    def status(self) -> dict[str, object]:
        return {"configured": True, "cache": {"count": 0}}


class _SearchClient:
    def search(self, **kwargs):
        works = [
            {"id": "safe", "title": "Safe", "AI_type": "NovelAI", "tags": ["1girl"]},
            {"id": "adult-a", "title": "Adult A", "AI_type": "NovelAI", "tags": ["R-18"]},
            {"id": "adult-b", "title": "Adult B", "AI_type": "NovelAI", "tags": ["R18"]},
            {"id": "adult-c", "title": "Adult C", "AI_type": "NovelAI", "tags": ["nsfw"]},
            {"id": "adult-d", "title": "Adult D", "AI_type": "NovelAI", "tags": ["explicit"]},
            {"id": "adult-e", "title": "Adult E", "AI_type": "NovelAI", "tags": ["rating:explicit"]},
        ]
        return normalize_aitag_search(
            {"items": works, "total": len(works)},
            query=str(kwargs.get("query") or ""),
            page=int(kwargs.get("page") or 1),
            page_size=int(kwargs.get("page_size") or 60),
        )


class _CoverSearchClient:
    def search(self, **kwargs):
        return normalize_aitag_search(
            {
                "items": [
                    {
                        "id": "cover-42",
                        "title": "Online cover",
                        "AI_type": "NAI",
                        "userId": "author-7",
                        "original_urls": [
                            "https://i.pximg.net/img-original/cover-42_p0.png",
                            "https://i.pximg.net/img-original/cover-42_p1.png",
                        ],
                    }
                ],
                "total": 1,
            },
            query="",
            page=1,
            page_size=60,
        )

    def get_config(self):
        return normalize_aitag_config(
            {"asset_base_url": "https://ai-img.10118899.xyz/"}
        )


class _ReferenceCatalog:
    def get(self, reference_id: str):
        assert reference_id == "ref-target"
        return {
            "reference_id": reference_id,
            "raw": {
                "id": "target-oc",
                "source": "local",
                "source_id": "target-oc",
                "name": "Target OC",
                "character": "Target OC",
                "core_tags": ["1girl", "black_hair", "golden_eyes"],
            },
        }


@pytest.mark.parametrize(
    "value",
    [
        "http://aitag.win",
        "https://example.test",
        "https://aitag.win.example.test",
        "https://aitag.win@127.0.0.1",
        "https://aitag.win:4443",
        "https://aitag.win/api",
        "https://aitag.win?next=https://127.0.0.1",
        "https://aitag.win#fragment",
    ],
)
def test_aitag_origin_allowlist_rejects_non_origin_variants(value: str) -> None:
    with pytest.raises(ValueError):
        validate_aitag_base_url(value)


def test_default_http_client_explicitly_disables_redirects(tmp_path: Path) -> None:
    with patch("aitag_core.online.httpx.Client") as constructor:
        client = AitagClient(cache_root=tmp_path)

    assert constructor.call_args.kwargs["follow_redirects"] is False
    client.close()


def test_redirect_response_is_not_followed_or_cached(tmp_path: Path) -> None:
    http = _HTTP(
        [
            _Response(
                {},
                302,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )
        ]
    )
    client = AitagClient(http_client=http, cache_root=tmp_path)

    with pytest.raises(AitagClientError) as caught:
        client.search(query="redirect")

    assert caught.value.status_code == 302
    assert len(http.calls) == 1
    assert client.cache.stats()["count"] == 0


def test_search_404_remains_a_zero_result_page(tmp_path: Path) -> None:
    client = AitagClient(
        http_client=_HTTP([_Response({}, 404)]),
        cache_root=tmp_path,
    )

    page = client.search(query="missing")

    assert page.works == ()
    assert page.total in {None, 0}
    assert page.has_more is False


def test_image_proxy_reads_only_the_fixed_cdn_and_rejects_path_injection(tmp_path: Path) -> None:
    response = _Response({}, headers={"content-type": "image/webp"})
    response.content = b"RIFF-webp"
    http = _HTTP([response])
    client = AitagClient(http_client=http, cache_root=tmp_path)

    content, content_type = client.get_image("NAI", "author-7", "cover-42_p0.webp")

    assert content == b"RIFF-webp"
    assert content_type == "image/webp"
    assert http.calls[0][0] == "https://ai-img.10118899.xyz/NAI/author-7/cover-42_p0.webp"
    with pytest.raises(ValueError):
        client.get_image("NAI", "..", "secret.webp")


def test_search_is_safe_by_default_and_requires_explicit_adult_opt_in() -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_SearchClient()):
        safe = aitag_routes.api_aitag_search(
            q="",
            prompt="",
            page=1,
            page_size=60,
            sort="new",
            time_range="all",
            safe_only=True,
        )
        unfiltered = aitag_routes.api_aitag_search(
            q="",
            prompt="",
            page=1,
            page_size=60,
            sort="new",
            time_range="all",
            safe_only=False,
        )

    assert [item["work_id"] for item in safe["items"]] == ["safe"]
    assert safe["safe_only"] is True
    assert safe["generation_calls"] == 0
    assert {item["work_id"] for item in unfiltered["items"]} == {
        "safe",
        "adult-a",
        "adult-b",
        "adult-c",
        "adult-d",
        "adult-e",
    }
    assert unfiltered["safe_only"] is False


def test_search_items_expose_a_cdn_cover_for_native_gallery_cards() -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_CoverSearchClient()):
        result = aitag_routes.api_aitag_search(
            q="",
            prompt="",
            page=1,
            page_size=60,
            sort="popular",
            time_range="all",
            safe_only=False,
        )

    item = result["items"][0]
    assert item["image_count"] == 2
    assert item["images"][0]["thumbnail_url"] == (
        "/api/nai/aitag/cover/cover-42"
    )
    assert not item["images"][0].get("remote_url")


def test_work_detail_preserves_all_images_and_never_calls_a_provider() -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_Client()), patch.object(
        nai_api, "generate_image"
    ) as generate, patch.object(nai_batch, "start_batch") as start_batch:
        result = aitag_routes.api_aitag_work("work-42")

    assert result["ok"] is True
    assert result["generation_calls"] == 0
    assert [image["image_id"] for image in result["images"]] == [
        "image-blue",
        "image-red",
    ]
    assert result["work"]["images"] == result["images"]
    generate.assert_not_called()
    start_batch.assert_not_called()


def test_second_image_can_create_a_character_slot_from_a_blank_draft() -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_Client()), patch.object(
        nai_api, "generate_image"
    ) as generate, patch.object(nai_batch, "start_batch") as start_batch:
        result = aitag_routes.api_aitag_apply(
            "work-42",
            {"comment": {}, "image_index": 1, "slot_index": 0},
        )

    assert result["ok"] is True
    assert result["work_id"] == "work-42"
    assert result["image_id"] == "image-red"
    assert result["generation_calls"] == 0
    slots = result["comment"]["v4_prompt"]["caption"]["char_captions"]
    assert len(slots) == 1
    assert "red hair" in slots[0]["char_caption"]
    assert "green eyes" in slots[0]["char_caption"]
    assert "blue eyes" not in slots[0]["char_caption"]
    assert result["comment"]["_aitag_anima_reference"]["slot_index"] == 0
    generate.assert_not_called()
    start_batch.assert_not_called()


def test_draft_endpoint_builds_a_complete_blank_start_draft_from_second_image(
    tmp_path: Path,
) -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_Client()), patch.object(
        aitag_routes, "DATA_DIR", tmp_path
    ), patch.object(nai_api, "generate_image") as generate, patch.object(
        nai_batch, "start_batch"
    ) as start_batch:
        result = aitag_routes.api_aitag_draft(
            "work-42",
            {"image_index": 1, "slot_index": 0},
        )

    assert result["ok"] is True
    assert result["generation_calls"] == 0
    assert result["image_id"] == "image-red"
    assert result["draft_id"]
    assert f"draft={result['draft_id']}" in result["studio_url"]
    assert result["recipe"]["source_ref"] == "work-42/image-red"
    draft = result["draft"]
    assert draft["source"] == {
        "provider": "aitag-online",
        "workId": "work-42",
        "workIdStr": "work-42",
        "imageId": "image-red",
        "imageIndex": 1,
        "title": "Two image character",
        "thumb": "https://ai-img.10118899.xyz/red-thumb.png",
    }
    caption = draft["comment"]["v4_prompt"]["caption"]
    base = caption["base_caption"].replace("_", " ")
    character = caption["char_captions"][0]["char_caption"]
    assert "outdoors" in base
    assert "cinematic lighting" in base
    assert "red hair" not in base
    assert "green eyes" not in base
    assert "red hair" in character
    assert "green eyes" in character
    assert "outdoors" not in character
    assert "cinematic lighting" not in character
    generate.assert_not_called()
    start_batch.assert_not_called()


def test_persisted_draft_survives_without_local_storage_and_stays_zero_cost(
    tmp_path: Path,
) -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_Client()), patch.object(
        aitag_routes, "DATA_DIR", tmp_path
    ), patch.object(nai_api, "generate_image") as generate, patch.object(
        nai_batch, "start_batch"
    ) as start_batch:
        created = aitag_routes.api_aitag_draft(
            "work-42",
            {"image_index": 1, "slot_index": 0},
        )
        loaded = aitag_routes.api_aitag_draft_get(created["draft_id"])
        latest = aitag_routes.api_aitag_draft_latest()

    assert loaded["ok"] is True
    assert loaded["draft_id"] == created["draft_id"]
    assert loaded["generation_calls"] == 0
    assert loaded["draft"]["source"]["imageId"] == "image-red"
    assert loaded["studio_url"].endswith(f"draft={created['draft_id']}")
    assert latest["draft_id"] == created["draft_id"]
    assert latest["generation_calls"] == 0
    generate.assert_not_called()
    start_batch.assert_not_called()


def test_draft_target_replaces_only_identity_and_preserves_scene_style(tmp_path: Path) -> None:
    with patch.object(aitag_routes, "get_aitag_client", return_value=_Client()), patch.object(
        aitag_routes, "get_reference_catalog", return_value=_ReferenceCatalog()
    ), patch.object(aitag_routes, "DATA_DIR", tmp_path), patch.object(
        nai_api, "generate_image"
    ) as generate, patch.object(nai_batch, "start_batch") as start_batch:
        result = aitag_routes.api_aitag_draft(
            "work-42",
            {
                "image_index": 1,
                "slot_index": 0,
                "target_reference_id": "ref-target",
            },
        )

    assert result["generation_calls"] == 0
    assert result["draft"]["reference"] == {
        "referenceId": "ref-target",
        "slotIndex": 0,
    }
    caption = result["draft"]["comment"]["v4_prompt"]["caption"]
    base = caption["base_caption"].replace("_", " ")
    character = caption["char_captions"][0]["char_caption"]
    assert "outdoors" in base
    assert "cinematic lighting" in base
    assert "black hair" in character
    assert "golden eyes" in character
    assert "red hair" not in character
    assert "green eyes" not in character
    assert result["recipe"]["character"]["label"] == "Target OC"
    generate.assert_not_called()
    start_batch.assert_not_called()


def test_disabled_status_keeps_local_fallback_and_zero_generation_calls() -> None:
    with patch.dict(aitag_routes.CONFIG, {"aitag_online_enabled": False}):
        result = aitag_routes.api_aitag_status()

    assert result == {
        "ok": False,
        "enabled": False,
        "source": "aitag-online",
        "local_fallback": True,
        "generation_calls": 0,
    }

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from pixiv_nai_source import (
    PixivAPIError,
    PixivDownloadError,
    PixivNAISource,
    PixivSourceProtocolError,
    map_pixiv_illust,
)


def _illust() -> dict:
    return {
        "id": 123,
        "type": "illust",
        "title": "NAI work",
        "caption": "caption",
        "user": {"id": 456, "name": "Alice"},
        "tags": [{"name": "NovelAI"}, {"name": "girl"}],
        "create_date": "2026-08-01T12:34:56+09:00",
        "total_view": 99,
        "total_bookmarks": 12,
        "page_count": 2,
        "meta_pages": [
            {"image_urls": {"original": "https://i.pximg.net/a_p0.png"}},
            {"image_urls": {"original": "https://i.pximg.net/a_p1.jpg"}},
        ],
        "x_restrict": 1,
        "illust_ai_type": 2,
    }


def test_pixiv_response_maps_to_one_multi_page_gallery_work() -> None:
    work = map_pixiv_illust(_illust())

    assert work is not None
    assert work.work_id == 123
    assert work.user_id == 456
    assert work.tags == ("NovelAI", "girl")
    assert [page.source_page_index for page in work.pages] == [0, 1]
    assert [page.original_url for page in work.pages] == [
        "https://i.pximg.net/a_p0.png",
        "https://i.pximg.net/a_p1.jpg",
    ]
    assert work.x_restrict == 1
    assert work.pixiv_ai_type == 2


def test_ugoira_and_missing_original_are_not_admitted() -> None:
    ugoira = {**_illust(), "type": "ugoira"}
    missing = {
        **_illust(),
        "page_count": 1,
        "meta_pages": [],
        "meta_single_page": {},
    }

    assert map_pixiv_illust(ugoira) is None
    assert map_pixiv_illust(missing) is None


def test_fetch_page_rejects_untrusted_next_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer token"
        return httpx.Response(
            200,
            json={
                "illusts": [_illust()],
                "next_url": "https://attacker.test/steal",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivNAISource(
            token_provider=lambda: "token",
            client=client,
        )
        with pytest.raises(PixivSourceProtocolError):
            source.fetch_page({"type": "search", "query": "NovelAI"})


@pytest.mark.parametrize(
    ("status_code", "retryable"),
    [(403, False), (429, True), (503, True)],
)
def test_api_http_errors_expose_retryable_or_permanent_semantics(
    status_code: int,
    retryable: bool,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            headers={"Retry-After": "3"} if status_code == 429 else {},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivNAISource(token_provider=lambda: "token", client=client)
        with pytest.raises(PixivAPIError) as caught:
            source.fetch_page({"type": "search", "query": "NovelAI"})

    assert caught.value.status_code == status_code
    assert caught.value.retryable is retryable
    assert caught.value.retry_after == (3.0 if status_code == 429 else None)


def test_download_stream_limit_and_redirect_host_are_enforced(tmp_path: Path) -> None:
    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 33)

    target = tmp_path / "too-large.png"
    with httpx.Client(transport=httpx.MockTransport(oversized)) as client:
        source = PixivNAISource(
            token_provider=lambda: "token",
            client=client,
            max_download_bytes=32,
        )
        with pytest.raises(PixivDownloadError):
            source.download_original("https://i.pximg.net/a.png", target)
    assert target.exists() is False

    def redirected(request: httpx.Request) -> httpx.Response:
        if request.url.host == "i.pximg.net":
            return httpx.Response(
                302,
                headers={"Location": "https://attacker.test/file.png"},
            )
        return httpx.Response(200, content=b"not allowed")

    with httpx.Client(
        transport=httpx.MockTransport(redirected), follow_redirects=True
    ) as client:
        source = PixivNAISource(token_provider=lambda: "token", client=client)
        with pytest.raises(PixivDownloadError):
            source.download_original("https://i.pximg.net/a.png", tmp_path / "evil.png")


def test_download_retries_transient_http_failure(tmp_path: Path) -> None:
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, content=b"retry")
        return httpx.Response(200, content=b"image-bytes")

    destination = tmp_path / "retried.png"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivNAISource(
            token_provider=lambda: "token",
            client=client,
            download_retry_max=2,
            sleep_fn=lambda _seconds: None,
        )
        source.download_original("https://i.pximg.net/retry.png", destination)

    assert calls == 2
    assert destination.read_bytes() == b"image-bytes"


def test_download_failure_classification_survives_intake_boundary(
    tmp_path: Path,
) -> None:
    def forbidden(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, content=b"forbidden")

    url = "https://i.pximg.net/permanent.png"
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        source = PixivNAISource(token_provider=lambda: "token", client=client)
        with pytest.raises(PixivDownloadError) as caught:
            source.download_original(url, tmp_path / "permanent.png")

        failure = source.consume_download_failure(url)

    assert caught.value.retryable is False
    assert failure is not None
    assert failure.kind == "permanent"
    assert failure.reason == "http_403"
    assert source.consume_download_failure(url) is None


def test_fetch_page_applies_request_delay_between_calls() -> None:
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"illusts": [_illust()]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivNAISource(
            token_provider=lambda: "token",
            client=client,
            sleep_fn=sleeps.append,
            request_delay_sec=1.5,
        )
        source.fetch_page({"type": "search", "query": "NovelAI"})
        source.fetch_page({"type": "search", "query": "NovelAI"})

    assert sleeps == [1.5]  # first call is immediate, second is paced


def test_request_delay_is_capped_at_sixty_seconds() -> None:
    source = PixivNAISource(
        token_provider=lambda: "token",
        client=httpx.Client(),
        request_delay_sec=999.0,
    )
    try:
        assert source.request_delay_sec == 60.0
    finally:
        source.close()

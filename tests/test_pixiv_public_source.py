from __future__ import annotations

from pathlib import Path

import httpx

from pixiv_public_source import PixivPublicWebSource, map_public_illust


def _detail() -> dict:
    return {
        "id": "123",
        "illustId": "123",
        "title": "public NAI work",
        "description": "caption",
        "userId": "456",
        "userName": "Alice",
        "createDate": "2026-08-03T08:13:00+00:00",
        "aiType": 2,
        "illustType": 0,
        "xRestrict": 0,
        "viewCount": 99,
        "bookmarkCount": 12,
        "tags": {"tags": [{"tag": "NovelAI"}, {"tag": "girl"}]},
        "urls": {"original": "https://i.pximg.net/img-original/a_p0.png"},
    }


def test_public_detail_maps_to_strict_work() -> None:
    work = map_public_illust(_detail())

    assert work is not None
    assert work.work_id == 123
    assert work.user_id == 456
    assert work.tags == ("NovelAI", "girl")
    assert work.pages[0].original_url.endswith("a_p0.png")
    assert work.pixiv_ai_type == 2


def test_public_pages_endpoint_maps_all_originals() -> None:
    work = map_public_illust(
        _detail(),
        pages=[
            {"urls": {"original": "https://i.pximg.net/img-original/a_p0.png"}},
            {"urls": {"original": "https://i.pximg.net/img-original/a_p1.jpg"}},
        ],
    )

    assert work is not None
    assert [page.source_page_index for page in work.pages] == [0, 1]


def test_public_search_hydrates_details_and_paginates() -> None:
    search_url = "https://www.pixiv.net/ajax/search/artworks/NovelAI"
    detail_url = "https://www.pixiv.net/ajax/illust/123?lang=zh"
    pages_url = "https://www.pixiv.net/ajax/illust/123/pages?lang=zh"

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(search_url):
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "body": {
                        "illustManga": {
                            "data": [
                                {
                                    "id": "123",
                                    "title": "public NAI work",
                                    "userId": "456",
                                    "userName": "Alice",
                                    "aiType": 2,
                                    "pageCount": 2,
                                }
                            ],
                            "lastPage": 2,
                        }
                    },
                },
            )
        if str(request.url) == detail_url:
            return httpx.Response(200, json={"error": False, "body": {**_detail(), "pageCount": 2}})
        if str(request.url) == pages_url:
            return httpx.Response(
                200,
                json={
                    "error": False,
                    "body": [
                        {"urls": {"original": "https://i.pximg.net/img-original/a_p0.png"}},
                        {"urls": {"original": "https://i.pximg.net/img-original/a_p1.jpg"}},
                    ],
                },
            )
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False) as client:
        source = PixivPublicWebSource(client=client, ai_prefilter=True)
        page = source.fetch_page({"type": "search", "query": "NovelAI"})

    assert len(page.works) == 1
    assert len(page.works[0].pages) == 2
    assert "p=2" in page.next_cursor


def test_public_source_rejects_ranking_scope() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _request: httpx.Response(200))) as client:
        source = PixivPublicWebSource(client=client)
        try:
            source.fetch_page({"type": "ranking", "mode": "day"})
        except Exception as exc:
            assert "ranking" in str(exc)
        else:
            raise AssertionError("ranking scope should be explicit API-only")


def test_public_source_proxy_url_wires_httpx_proxy() -> None:
    source = PixivPublicWebSource(proxy_url="http://127.0.0.1:7897")
    try:
        mounts = getattr(source.client, "_mounts", None)
        # httpx 0.26+ configures proxies as non-default URL mounts; the
        # default ("") mount alone means no proxy is active.
        assert mounts is not None and any(key != "" for key in mounts)
    finally:
        source.close()


def test_public_source_rejects_non_http_proxy() -> None:
    try:
        PixivPublicWebSource(proxy_url="ftp://127.0.0.1:21")
    except ValueError as exc:
        assert "http(s)" in str(exc)
    else:
        raise AssertionError("non-http proxy URL should be rejected")


def test_public_source_delay_caps_at_configured_maximum() -> None:
    source = PixivPublicWebSource(request_delay_sec=100.0)
    try:
        assert source.request_delay_sec == 60.0
    finally:
        source.close()


def test_public_search_popular_sort_uses_popular_order() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "error": False,
                "body": {"illustManga": {"data": [], "lastPage": 1}},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivPublicWebSource(client=client)
        source.fetch_page({"type": "search", "query": "NovelAI", "sort": "popular_desc"})

    assert len(seen) == 1
    assert "order=popular_d" in seen[0]


def test_public_search_default_sort_is_date_order() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "error": False,
                "body": {"illustManga": {"data": [], "lastPage": 1}},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        source = PixivPublicWebSource(client=client)
        source.fetch_page({"type": "search", "query": "NovelAI"})

    assert len(seen) == 1
    assert "order=date_d" in seen[0]

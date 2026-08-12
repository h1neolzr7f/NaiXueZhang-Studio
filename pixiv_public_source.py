"""Public Pixiv web adapter for the strict NovelAI intake pipeline.

This source intentionally uses only the public ``www.pixiv.net/ajax``
responses exposed to a logged-out browser.  It never attempts to create a
session, solve a challenge, or reuse an account token.  The downstream NAI
metadata gate remains unchanged.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse, urlunparse

import httpx

from pixiv_nai_intake import PixivPage, PixivWork
from pixiv_nai_source import (
    PIXIV_IMAGE_HOSTS,
    PixivAPIError,
    PixivNAISource,
    PixivSourcePage,
    PixivSourceProtocolError,
    _as_int,
)


PIXIV_WEB_BASE = "https://www.pixiv.net"
PIXIV_WEB_HOST = "www.pixiv.net"
PIXIV_WEB_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
    "Referer": "https://www.pixiv.net/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
}


def _public_url(path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{PIXIV_WEB_BASE}{path}"
    if params:
        url = f"{url}?{urlencode(params)}"
    return url


def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != PIXIV_WEB_HOST:
        raise PixivSourceProtocolError("Pixiv public cursor uses an untrusted host")


def _validate_image_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and (parsed.hostname or "").lower() in PIXIV_IMAGE_HOSTS
    )


def _validate_proxy(proxy_url: str) -> str:
    proxy_url = str(proxy_url or "").strip()
    if not proxy_url:
        return ""
    parsed = urlparse(proxy_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("proxy_url must be an http(s) proxy URL")
    return proxy_url


def _tags_from_public(value: Any) -> tuple[str, ...]:
    raw_tags = value
    if isinstance(value, dict):
        raw_tags = value.get("tags")
    tags: list[str] = []
    seen: set[str] = set()
    for raw in raw_tags or []:
        tag = str(raw.get("tag") or "").strip() if isinstance(raw, dict) else str(raw or "").strip()
        if tag and tag not in seen:
            tags.append(tag)
            seen.add(tag)
    return tuple(tags)


def map_public_illust(
    detail: dict[str, Any],
    *,
    pages: list[dict[str, Any]] | None = None,
    search_item: dict[str, Any] | None = None,
) -> PixivWork | None:
    """Map a public ``/ajax/illust/<id>`` body into a ``PixivWork``."""

    item = detail if isinstance(detail, dict) else {}
    fallback = search_item if isinstance(search_item, dict) else {}
    work_id = _as_int(item.get("id") or item.get("illustId") or fallback.get("id"))
    user_id = _as_int(item.get("userId") or fallback.get("userId"))
    if work_id <= 0 or user_id <= 0:
        return None
    illust_type = _as_int(item.get("illustType"), _as_int(fallback.get("illustType")))
    if illust_type == 2:  # ugoira is not a static original image.
        return None

    raw_pages = pages if isinstance(pages, list) and pages else None
    if raw_pages is None:
        raw_pages = [{"urls": item.get("urls")}] if isinstance(item.get("urls"), dict) else []
    mapped_pages: list[PixivPage] = []
    for source_index, raw_page in enumerate(raw_pages):
        urls = raw_page.get("urls") if isinstance(raw_page, dict) else {}
        urls = urls if isinstance(urls, dict) else {}
        original = str(urls.get("original") or "").strip()
        if original and _validate_image_url(original):
            thumbnail = str(
                urls.get("master_url_1200") or urls.get("regular") or ""
            ).strip()
            mapped_pages.append(PixivPage(source_index, original, thumbnail))
    if not mapped_pages:
        return None

    tags = _tags_from_public(item.get("tags"))
    if not tags:
        tags = _tags_from_public(fallback.get("tags"))
    caption = str(item.get("description") or item.get("illustComment") or fallback.get("description") or "")
    user_name = str(item.get("userName") or fallback.get("userName") or "").strip()
    pixiv_ai_type = item.get("aiType")
    if pixiv_ai_type is None:
        pixiv_ai_type = fallback.get("aiType")
    return PixivWork(
        work_id=work_id,
        user_id=user_id,
        user_name=user_name,
        title=str(item.get("title") or item.get("illustTitle") or fallback.get("title") or "").strip(),
        caption=caption,
        tags=tags,
        create_date=str(item.get("createDate") or fallback.get("createDate") or ""),
        total_view=max(0, _as_int(item.get("viewCount"))),
        total_bookmarks=max(0, _as_int(item.get("bookmarkCount"))),
        pages=tuple(mapped_pages),
        work_type=1 if illust_type == 1 else 0,
        x_restrict=max(0, _as_int(item.get("xRestrict") or fallback.get("xRestrict"))),
        pixiv_ai_type=_as_int(pixiv_ai_type) if pixiv_ai_type is not None else None,
    )


class PixivPublicWebSource(PixivNAISource):
    """Logged-out public Pixiv source with the same safe image downloader."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        max_download_bytes: int = 128 * 1024 * 1024,
        download_retry_max: int = 3,
        sleep_fn: Any = None,
        ai_prefilter: bool = True,
        work_batch_size: int = 60,
        request_delay_sec: float = 0.0,
        proxy_url: str = "",
    ) -> None:
        proxy_url = _validate_proxy(proxy_url)
        if client is None and proxy_url:
            client = httpx.Client(
                proxy=proxy_url,
                timeout=httpx.Timeout(45.0, connect=15.0),
                follow_redirects=False,
            )
        super().__init__(
            account_id=None,
            token_provider=lambda: "public",
            client=client,
            max_download_bytes=max_download_bytes,
            download_retry_max=download_retry_max,
            sleep_fn=sleep_fn or (lambda _seconds: None),
        )
        self.ai_prefilter = bool(ai_prefilter)
        self.work_batch_size = max(1, min(int(work_batch_size), 60))
        self.request_delay_sec = max(0.0, min(float(request_delay_sec), 60.0))
        self._web_requests = 0
        self._user_ids: dict[int, tuple[int, ...]] = {}

    def _get_json(self, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        if self._web_requests and self.request_delay_sec:
            self._sleep(self.request_delay_sec)
        self._web_requests += 1
        try:
            response = self.client.get(url, headers=PIXIV_WEB_HEADERS)
        except httpx.HTTPError as exc:
            raise PixivAPIError("Pixiv public web request failed", retryable=True) from exc
        _validate_public_url(str(response.url))
        if 300 <= response.status_code < 400:
            raise PixivSourceProtocolError("Pixiv public web returned an unexpected redirect")
        if response.status_code >= 400:
            retryable = response.status_code == 429 or response.status_code >= 500
            retry_after = None
            try:
                if response.headers.get("Retry-After"):
                    retry_after = max(0.0, float(response.headers["Retry-After"]))
            except (TypeError, ValueError):
                retry_after = None
            raise PixivAPIError(
                f"Pixiv public web request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                retryable=retryable,
                retry_after=retry_after,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PixivSourceProtocolError("Pixiv public web returned invalid JSON") from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise PixivSourceProtocolError("Pixiv public web returned an error payload")
        return payload

    @staticmethod
    def _search_url(query: str, page: int, sort: str = "date_desc") -> str:
        encoded = quote(query, safe="")
        order = "popular_d" if str(sort).strip() == "popular_desc" else "date_d"
        return _public_url(
            f"/ajax/search/artworks/{encoded}",
            {
                "word": query,
                "p": page,
                "s_mode": "s_tag",
                "order": order,
                "mode": "all",
                "lang": "zh",
            },
        )

    @staticmethod
    def _detail_url(work_id: int) -> str:
        return _public_url(f"/ajax/illust/{int(work_id)}", {"lang": "zh"})

    @staticmethod
    def _pages_url(work_id: int) -> str:
        return _public_url(f"/ajax/illust/{int(work_id)}/pages", {"lang": "zh"})

    @staticmethod
    def _user_profile_url(user_id: int) -> str:
        return _public_url(f"/ajax/user/{int(user_id)}/profile/all", {"lang": "zh"})

    def _hydrate(self, item: dict[str, Any]) -> PixivWork | None:
        work_id = _as_int(item.get("id"))
        if work_id <= 0:
            return None
        # The public search response already exposes Pixiv's AI marker.  Avoid
        # a detail request for known non-candidates when the prefilter is on.
        if self.ai_prefilter and item.get("aiType") not in (None, 2):
            return None
        detail_payload = self._get_json(self._detail_url(work_id))
        detail = detail_payload.get("body")
        if not isinstance(detail, dict):
            return None
        page_payload: list[dict[str, Any]] | None = None
        if max(1, _as_int(detail.get("pageCount") or item.get("pageCount"))) > 1:
            pages_payload = self._get_json(self._pages_url(work_id)).get("body")
            if isinstance(pages_payload, list):
                page_payload = pages_payload
        return map_public_illust(detail, pages=page_payload, search_item=item)

    def _fetch_search(self, url: str) -> PixivSourcePage:
        payload = self._get_json(url)
        body = payload.get("body")
        block = body.get("illustManga") if isinstance(body, dict) else None
        if not isinstance(block, dict):
            raise PixivSourceProtocolError("Pixiv public search payload has no illustManga block")
        raw_items = block.get("data")
        if not isinstance(raw_items, list):
            raise PixivSourceProtocolError("Pixiv public search payload has no work list")
        works: list[PixivWork] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                work = self._hydrate(raw_item)
            except PixivAPIError as exc:
                if exc.status_code == 404:
                    continue
                raise
            if work is not None:
                works.append(work)
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        page = _as_int((query.get("p") or ["1"])[0], 1)
        last_page = max(page, min(10, _as_int(block.get("lastPage"), page)))
        next_cursor = ""
        if raw_items and page < last_page:
            next_query = dict(query)
            next_query["p"] = [str(page + 1)]
            next_cursor = urlunparse(parsed._replace(query=urlencode(next_query, doseq=True)))
        return PixivSourcePage(works=tuple(works), next_cursor=next_cursor)

    def _fetch_user(self, user_id: int, offset: int) -> PixivSourcePage:
        ids = self._user_ids.get(user_id)
        if ids is None:
            payload = self._get_json(self._user_profile_url(user_id))
            body = payload.get("body")
            raw = body.get("illusts") if isinstance(body, dict) else None
            if not isinstance(raw, dict):
                raise PixivSourceProtocolError("Pixiv public user payload has no illust list")
            ids = tuple(_as_int(key) for key in raw if _as_int(key) > 0)
            self._user_ids[user_id] = ids
        batch = ids[offset : offset + self.work_batch_size]
        works: list[PixivWork] = []
        for work_id in batch:
            work = self._hydrate({"id": work_id, "userId": user_id})
            if work is not None:
                works.append(work)
        next_cursor = f"public-user:{user_id}:{offset + len(batch)}" if offset + len(batch) < len(ids) else ""
        return PixivSourcePage(works=tuple(works), next_cursor=next_cursor)

    def fetch_page(self, scope: dict[str, Any], cursor: str = "") -> PixivSourcePage:
        scope_type = str(scope.get("type") or "search").strip().lower()
        cursor = str(cursor or "").strip()
        if scope_type == "search":
            if cursor:
                _validate_public_url(cursor)
                return self._fetch_search(cursor)
            query = str(scope.get("query") or "").strip()
            if not query:
                raise ValueError("Pixiv search scope requires query")
            sort = str(scope.get("sort") or "date_desc")
            return self._fetch_search(self._search_url(query, 1, sort))
        if scope_type == "user":
            user_id = _as_int(scope.get("user_id"))
            if user_id <= 0:
                raise ValueError("Pixiv user scope requires a positive user_id")
            offset = 0
            if cursor:
                parts = cursor.split(":")
                if len(parts) != 3 or parts[0] != "public-user" or _as_int(parts[1]) != user_id:
                    raise PixivSourceProtocolError("Pixiv public user cursor is invalid")
                offset = max(0, _as_int(parts[2]))
            return self._fetch_user(user_id, offset)
        raise PixivSourceProtocolError(
            "Pixiv public mode supports search and user scopes; use API mode for ranking"
        )

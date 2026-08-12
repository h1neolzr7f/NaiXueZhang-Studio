"""Read-only Pixiv App API adapter for the strict NAI intake pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import random
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

from pixiv_nai_intake import PixivPage, PixivWork


PIXIV_API_BASE = "https://app-api.pixiv.net"
PIXIV_API_HOST = "app-api.pixiv.net"
PIXIV_IMAGE_HOSTS = frozenset({"i.pximg.net"})
PIXIV_IMAGE_HEADERS = {
    "Referer": "https://www.pixiv.net/",
    "User-Agent": "PixivIOSApp/7.13.3 (iOS 14.6; iPhone13,2)",
}


class PixivSourceError(RuntimeError):
    """Base error for a source page or image request."""


class PixivSourceProtocolError(PixivSourceError):
    """The remote response violated a safety or shape invariant."""


class PixivAPIError(PixivSourceError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        retryable: bool = False,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = int(status_code)
        self.retryable = bool(retryable)
        self.retry_after = retry_after


class PixivDownloadError(PixivSourceError):
    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        retry_after: float | None = None,
        status_code: int = 0,
        reason: str = "download_error",
    ) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
        self.retry_after = retry_after
        self.status_code = int(status_code)
        self.reason = str(reason or "download_error")


@dataclass(frozen=True)
class PixivDownloadFailure:
    kind: str
    reason: str
    retry_after: float | None = None


@dataclass(frozen=True)
class PixivSourcePage:
    works: tuple[PixivWork, ...]
    next_cursor: str


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def map_pixiv_illust(item: dict[str, Any]) -> PixivWork | None:
    """Map one App API illust without weakening the downstream NAI gate."""

    kind = str(item.get("type") or "illust").strip().lower()
    if kind == "ugoira":
        return None
    work_id = _as_int(item.get("id"))
    user = item.get("user") if isinstance(item.get("user"), dict) else {}
    user_id = _as_int(user.get("id"))
    if work_id <= 0 or user_id <= 0:
        return None

    pages: list[PixivPage] = []
    meta_pages = item.get("meta_pages")
    if isinstance(meta_pages, list) and meta_pages:
        for source_index, raw_page in enumerate(meta_pages):
            raw_page = raw_page if isinstance(raw_page, dict) else {}
            image_urls = (
                raw_page.get("image_urls")
                if isinstance(raw_page.get("image_urls"), dict)
                else {}
            )
            original_url = str(image_urls.get("original") or "").strip()
            if original_url:
                thumbnail = str(
                    image_urls.get("master_url_1200")
                    or image_urls.get("regular")
                    or ""
                ).strip()
                pages.append(PixivPage(source_index, original_url, thumbnail))
    else:
        single = (
            item.get("meta_single_page")
            if isinstance(item.get("meta_single_page"), dict)
            else {}
        )
        original_url = str(single.get("original_image_url") or "").strip()
        if original_url:
            thumbnail = str(
                single.get("master_image_url") or single.get("regular_image_url") or ""
            ).strip()
            pages.append(PixivPage(0, original_url, thumbnail))
    if not pages:
        return None

    tags: list[str] = []
    seen_tags: set[str] = set()
    for raw_tag in item.get("tags") or []:
        if isinstance(raw_tag, dict):
            tag = str(raw_tag.get("name") or "").strip()
        else:
            tag = str(raw_tag or "").strip()
        if tag and tag not in seen_tags:
            tags.append(tag)
            seen_tags.add(tag)

    pixiv_ai_type = item.get("illust_ai_type")
    if pixiv_ai_type is None:
        pixiv_ai_type = item.get("ai_type")
    return PixivWork(
        work_id=work_id,
        user_id=user_id,
        user_name=str(user.get("name") or "").strip(),
        title=str(item.get("title") or "").strip(),
        caption=str(item.get("caption") or ""),
        tags=tuple(tags),
        create_date=str(item.get("create_date") or ""),
        total_view=max(0, _as_int(item.get("total_view"))),
        total_bookmarks=max(0, _as_int(item.get("total_bookmarks"))),
        pages=tuple(pages),
        work_type=1 if kind == "manga" else 0,
        x_restrict=max(0, _as_int(item.get("x_restrict"))),
        pixiv_ai_type=(
            _as_int(pixiv_ai_type) if pixiv_ai_type is not None else None
        ),
    )


class PixivNAISource:
    def __init__(
        self,
        *,
        account_id: str | None = None,
        token_provider: Callable[[], str] | None = None,
        client: httpx.Client | None = None,
        max_download_bytes: int = 128 * 1024 * 1024,
        download_retry_max: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
        request_delay_sec: float = 0.0,
    ) -> None:
        self.account_id = str(account_id or "").strip() or None
        self._token_provider = token_provider or self._default_token_provider
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(45.0, connect=15.0),
            follow_redirects=False,
        )
        self.max_download_bytes = max(1, int(max_download_bytes))
        self.download_retry_max = max(1, min(int(download_retry_max), 8))
        self.request_delay_sec = max(0.0, min(float(request_delay_sec), 60.0))
        self._requests_made = 0
        self._sleep = sleep_fn
        self._download_failures: dict[str, PixivDownloadFailure] = {}
        self._download_failures_lock = threading.Lock()

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "PixivNAISource":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _default_token_provider(self) -> str:
        from pixiv_accounts import ensure_access_token

        access_token, _user = ensure_access_token(self.account_id)
        return access_token

    @staticmethod
    def _validate_api_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() != PIXIV_API_HOST:
            raise PixivSourceProtocolError("Pixiv API cursor uses an untrusted host")

    @staticmethod
    def _validate_image_url(url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() not in PIXIV_IMAGE_HOSTS
        ):
            raise PixivDownloadError("Pixiv image URL uses an untrusted host")

    def fetch_page(self, scope: dict[str, Any], cursor: str = "") -> PixivSourcePage:
        cursor = str(cursor or "").strip()
        if cursor:
            self._validate_api_url(cursor)
            url = cursor
            params = None
        else:
            url, params = self._initial_request(scope)
        # Conservative pacing between App API calls protects the account from
        # rate-limit flags; callers may still raise the floor for API mode.
        if self._requests_made and self.request_delay_sec:
            self._sleep(self.request_delay_sec)
        self._requests_made += 1
        access_token = str(self._token_provider() or "").strip()
        if not access_token:
            raise PixivSourceError("Pixiv account did not yield an access token")
        from pixiv_accounts import pixiv_api_headers

        try:
            response = self.client.get(
                url,
                params=params,
                headers=pixiv_api_headers(access_token),
            )
        except httpx.HTTPError as exc:
            raise PixivAPIError(
                "Pixiv API network request failed",
                retryable=True,
            ) from exc
        self._validate_api_url(str(response.url))
        if 300 <= response.status_code < 400:
            raise PixivSourceProtocolError("Pixiv API returned an unexpected redirect")
        self._raise_api_error(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise PixivSourceProtocolError("Pixiv API returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise PixivSourceProtocolError("Pixiv API returned a non-object page")
        works = tuple(
            work
            for raw in (payload.get("illusts") or [])
            if isinstance(raw, dict)
            if (work := map_pixiv_illust(raw)) is not None
        )
        next_cursor = str(payload.get("next_url") or "").strip()
        if next_cursor:
            self._validate_api_url(next_cursor)
        return PixivSourcePage(works=works, next_cursor=next_cursor)

    @staticmethod
    def _initial_request(scope: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        scope_type = str(scope.get("type") or "search").strip().lower()
        if scope_type == "search":
            query = str(scope.get("query") or "").strip()
            if not query:
                raise ValueError("Pixiv search scope requires query")
            return (
                f"{PIXIV_API_BASE}/v1/search/illust",
                {
                    "word": query,
                    "search_target": str(
                        scope.get("search_target") or "partial_match_for_tags"
                    ),
                    "sort": str(scope.get("sort") or "date_desc"),
                    "filter": "for_ios",
                },
            )
        if scope_type == "user":
            user_id = _as_int(scope.get("user_id"))
            if user_id <= 0:
                raise ValueError("Pixiv user scope requires a positive user_id")
            return (
                f"{PIXIV_API_BASE}/v1/user/illusts",
                {
                    "user_id": user_id,
                    "type": str(scope.get("work_type") or "illust"),
                    "filter": "for_ios",
                },
            )
        if scope_type == "ranking":
            return (
                f"{PIXIV_API_BASE}/v1/illust/ranking",
                {
                    "mode": str(scope.get("mode") or "day"),
                    "filter": "for_ios",
                },
            )
        raise ValueError(f"unsupported Pixiv source scope: {scope_type}")

    @staticmethod
    def _raise_api_error(response: httpx.Response) -> None:
        if response.status_code < 400:
            return
        retryable = response.status_code == 429 or response.status_code >= 500
        retry_after: float | None = None
        try:
            if response.headers.get("Retry-After"):
                retry_after = max(0.0, float(response.headers["Retry-After"]))
        except ValueError:
            retry_after = None
        raise PixivAPIError(
            f"Pixiv API request failed with HTTP {response.status_code}",
            status_code=response.status_code,
            retryable=retryable,
            retry_after=retry_after,
        )

    def download_original(self, url: str, destination: Path) -> None:
        for attempt in range(self.download_retry_max):
            try:
                self._download_original_once(url, destination)
                with self._download_failures_lock:
                    self._download_failures.pop(url, None)
                return
            except PixivDownloadError as exc:
                if not exc.retryable or attempt + 1 >= self.download_retry_max:
                    with self._download_failures_lock:
                        self._download_failures[url] = PixivDownloadFailure(
                            kind="retryable" if exc.retryable else "permanent",
                            reason=exc.reason,
                            retry_after=exc.retry_after,
                        )
                    raise
                delay = exc.retry_after
                if delay is None:
                    delay = (2**attempt) + random.uniform(0, 0.25)
                self._sleep(min(60.0, max(0.0, delay)))

    def consume_download_failure(self, url: str) -> PixivDownloadFailure | None:
        """Return and clear the final classified failure for one original URL."""

        with self._download_failures_lock:
            return self._download_failures.pop(str(url), None)

    def _download_original_once(self, url: str, destination: Path) -> None:
        self._validate_image_url(url)
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.unlink(missing_ok=True)
        total = 0
        try:
            with self.client.stream("GET", url, headers=PIXIV_IMAGE_HEADERS) as response:
                if 300 <= response.status_code < 400:
                    raise PixivDownloadError("Pixiv image redirect was not resolved")
                if response.status_code >= 400:
                    retryable = response.status_code == 429 or response.status_code >= 500
                    retry_after: float | None = None
                    try:
                        if response.headers.get("Retry-After"):
                            retry_after = float(response.headers["Retry-After"])
                    except ValueError:
                        retry_after = None
                    raise PixivDownloadError(
                        f"Pixiv image request failed with HTTP {response.status_code}",
                        retryable=retryable,
                        retry_after=retry_after,
                        status_code=response.status_code,
                        reason=f"http_{response.status_code}",
                    )
                self._validate_image_url(str(response.url))
                content_length = response.headers.get("Content-Length")
                if content_length and _as_int(content_length) > self.max_download_bytes:
                    raise PixivDownloadError("Pixiv image exceeds configured size limit")
                with destination.open("wb") as handle:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        total += len(chunk)
                        if total > self.max_download_bytes:
                            raise PixivDownloadError(
                                "Pixiv image exceeds configured size limit"
                            )
                        handle.write(chunk)
        except httpx.HTTPError as exc:
            destination.unlink(missing_ok=True)
            raise PixivDownloadError(
                "Pixiv image network request failed",
                retryable=True,
                reason="network_error",
            ) from exc
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        if total <= 0:
            destination.unlink(missing_ok=True)
            raise PixivDownloadError("Pixiv image response was empty")

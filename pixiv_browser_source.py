"""Browser-rendered Pixiv public source for the strict NovelAI intake pipeline.

The plain ``PixivPublicWebSource`` speaks httpx directly; this source runs the
same ``www.pixiv.net/ajax`` requests inside a real Chromium page.  A real
browser fingerprint plus the page's same-origin session makes the logged-out
web channel far more reliable.  Nothing here logs in, solves a challenge, or
disables anti-bot checks: a challenge page simply surfaces as a retryable
fetch failure.
"""

from __future__ import annotations

from typing import Any, Callable

import httpx

from pixiv_nai_source import PixivAPIError, PixivSourceProtocolError
from pixiv_public_source import PIXIV_WEB_BASE, PixivPublicWebSource, _validate_public_url

try:  # pragma: no cover - import guard for environments without playwright
    from playwright.sync_api import sync_playwright
except Exception:  # noqa: BLE001 - any import failure means the channel is off
    sync_playwright = None  # type: ignore[assignment]


PageEval = Callable[[str], Any]

_BROWSER_CHANNELS: tuple[str | None, ...] = ("chrome", "msedge", None)


class PixivBrowserSource(PixivPublicWebSource):
    """Public Pixiv source that fetches ajax JSON through a real Chromium page."""

    def __init__(
        self,
        *,
        page_eval: PageEval | None = None,
        client: httpx.Client | None = None,
        headless: bool = True,
        channel: str | None = None,
        browser_timeout_ms: int = 45_000,
        max_download_bytes: int = 128 * 1024 * 1024,
        download_retry_max: int = 3,
        sleep_fn: Any = None,
        ai_prefilter: bool = True,
        work_batch_size: int = 60,
        request_delay_sec: float = 0.0,
        proxy_url: str = "",
    ) -> None:
        super().__init__(
            client=client,
            max_download_bytes=max_download_bytes,
            download_retry_max=download_retry_max,
            sleep_fn=sleep_fn,
            ai_prefilter=ai_prefilter,
            work_batch_size=work_batch_size,
            request_delay_sec=request_delay_sec,
            proxy_url=proxy_url,
        )
        self._page_eval = page_eval or self._browser_eval
        self._headless = bool(headless)
        self._channel = str(channel or "").strip() or None
        self._browser_timeout_ms = max(5_000, int(browser_timeout_ms))
        self._proxy_url = str(proxy_url or "").strip()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    @staticmethod
    def available() -> bool:
        """True when the playwright library is importable in this environment."""
        return sync_playwright is not None

    # -- browser lifecycle -------------------------------------------------
    def _ensure_browser(self) -> Any:
        if self._page is not None:
            return self._page
        if sync_playwright is None:
            raise RuntimeError("playwright is not installed in this Python environment")
        self._playwright = sync_playwright().start()
        channels = (self._channel,) if self._channel else _BROWSER_CHANNELS
        last_error: Exception | None = None
        for channel in channels:
            try:
                self._browser = self._playwright.chromium.launch(
                    headless=self._headless,
                    channel=channel,
                )
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - try the next browser
                last_error = exc
        if self._browser is None:
            self._playwright.stop()
            self._playwright = None
            raise RuntimeError(f"无法启动浏览器：{last_error}")
        context_kwargs: dict[str, Any] = {}
        if self._proxy_url:
            context_kwargs["proxy"] = {"server": self._proxy_url}
        self._context = self._browser.new_context(**context_kwargs)
        self._page = self._context.new_page()
        self._page.goto(
            PIXIV_WEB_BASE + "/",
            wait_until="domcontentloaded",
            timeout=min(self._browser_timeout_ms, 30_000),
        )
        return self._page

    def _browser_eval(self, expression: str) -> Any:
        page = self._ensure_browser()
        # playwright's evaluate() has no timeout kwarg; the JS side races a
        # deadline so a hung fetch cannot block a crawl cycle forever.
        return page.evaluate(expression)

    def close(self) -> None:
        super().close()
        if self._page is not None:
            try:
                self._context.close()
                self._browser.close()
                self._playwright.stop()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
            self._page = None
            self._context = None
            self._browser = None
            self._playwright = None

    # -- request seam ------------------------------------------------------
    def _get_json(self, url: str) -> dict[str, Any]:
        _validate_public_url(url)
        if self._web_requests and self.request_delay_sec:
            self._sleep(self.request_delay_sec)
        self._web_requests += 1
        deadline_ms = min(self._browser_timeout_ms, 25_000)
        expression = (
            "Promise.race(["
            f"fetch({url!r}, {{headers: {{'Accept': 'application/json'}}}})"
            ".then(r => r.json()),"
            "new Promise((_, reject) => setTimeout("
            f"() => reject(new Error('fetch timeout')), {deadline_ms}"
            "))])"
        )
        try:
            payload = self._page_eval(expression)
        except Exception as exc:  # noqa: BLE001 - challenge pages / timeouts surface here
            raise PixivAPIError("Pixiv browser fetch failed", retryable=True) from exc
        if not isinstance(payload, dict) or payload.get("error"):
            raise PixivSourceProtocolError("Pixiv browser returned an error payload")
        return payload

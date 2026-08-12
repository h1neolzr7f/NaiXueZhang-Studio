from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from playwright.async_api import async_playwright

from pixiv_web_upload import PixivWebUploadError, _locator_first, probe_pixiv_upload_selectors


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "pixiv_create_form.html"


class PixivSelectorProbeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.playwright = await async_playwright().start()
        last_error: Exception | None = None
        for options in (
            {"headless": True},
            {"headless": True, "channel": "chrome"},
            {"headless": True, "channel": "msedge"},
        ):
            try:
                self.browser = await self.playwright.chromium.launch(**options)
                break
            except Exception as exc:
                last_error = exc
        else:
            await self.playwright.stop()
            assert last_error is not None
            raise last_error
        self.page = await self.browser.new_page()

    async def asyncTearDown(self) -> None:
        await self.browser.close()
        await self.playwright.stop()

    async def test_fixture_passes_without_clicking_or_uploading(self) -> None:
        await self.page.goto(FIXTURE.as_uri())

        result = await probe_pixiv_upload_selectors(self.page)

        self.assertTrue(result["ok"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(set(result["checks"]), {"upload", "title", "tags", "submit"})
        self.assertEqual(result["checks"]["upload"]["matched_selector"], "input[name='files[]']")
        self.assertEqual(result["checks"]["title"]["matched_selector"], "input[name='title']")
        self.assertEqual(result["checks"]["tags"]["matched_selector"], "input[placeholder*='タグ']")
        self.assertFalse(result["checks"]["submit"]["enabled"])
        self.assertIsNone(result["error"])

    async def test_missing_control_returns_structured_failure(self) -> None:
        await self.page.goto(FIXTURE.as_uri())
        await self.page.locator("input[placeholder='タグ']").evaluate("element => element.remove()")

        result = await probe_pixiv_upload_selectors(self.page)

        self.assertFalse(result["ok"])
        self.assertEqual(result["missing"], ["tags"])
        self.assertEqual(result["error"]["code"], "pixiv_selector_probe_failed")
        self.assertEqual(result["error"]["missing"], ["tags"])
        self.assertIn("selectors", result["checks"]["tags"])
        self.assertIsNone(result["checks"]["tags"]["matched_selector"])

    async def test_selector_exception_exposes_machine_readable_details(self) -> None:
        await self.page.set_content("<html><body></body></html>")

        with self.assertRaises(PixivWebUploadError) as caught:
            await _locator_first(
                self.page,
                ["input[name='title']"],
                control="title",
                timeout_ms=10,
            )

        payload = caught.exception.to_dict()
        self.assertEqual(payload["code"], "pixiv_selector_missing")
        self.assertEqual(payload["details"]["control"], "title")
        self.assertEqual(payload["details"]["selectors"], ["input[name='title']"])


class PixivSelectorProbeRouteTests(unittest.TestCase):
    def test_route_runs_probe_without_starting_upload_job(self) -> None:
        expected = {
            "ok": True,
            "phase": "selectors",
            "checks": {},
            "missing": [],
            "error": None,
        }
        with patch(
            "pixiv_web_upload.probe_pixiv_upload_page_sync",
            return_value=expected,
        ) as probe:
            from routes.pixiv import api_pixiv_upload_selector_probe

            result = api_pixiv_upload_selector_probe(
                {"account_id": "fixture-account", "headless": True}
            )

        self.assertEqual(result, expected)
        probe.assert_called_once_with(account_id="fixture-account", headless=True)


if __name__ == "__main__":
    unittest.main()

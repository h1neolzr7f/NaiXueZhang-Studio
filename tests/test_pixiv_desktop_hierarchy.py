from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PixivDesktopHierarchyTests(unittest.TestCase):
    def test_account_setup_is_progressively_disclosed(self) -> None:
        html = (ROOT / "web" / "pixiv.html").read_text(encoding="utf-8")
        js = (ROOT / "web" / "pixiv.js").read_text(encoding="utf-8")
        self.assertIn('id="pixivAccountSetup"', html)
        self.assertIn("登录与账号设置", html)
        self.assertIn("updateAccountSetupDisclosure", js)
        self.assertIn("setup.open = !ready", js)

    def test_api_configuration_has_one_global_entry_point(self) -> None:
        html = (ROOT / "web" / "pixiv.html").read_text(encoding="utf-8")
        self.assertIn('href="/settings#ai-service"', html)
        self.assertIn("一次配置，全局使用", html)
        self.assertIn("px-legacy-ai-config", html)

    def test_key_pixiv_collections_use_clear_ui_states(self) -> None:
        html = (ROOT / "web" / "pixiv.html").read_text(encoding="utf-8")
        css = (ROOT / "web" / "pixiv.css").read_text(encoding="utf-8")
        self.assertIn('data-ui-state="loading"', html)
        for marker in (".ui-state", ".ui-state[data-kind=\"error\"]", ".ui-state[data-kind=\"empty\"]"):
            self.assertIn(marker, css)


if __name__ == "__main__":
    unittest.main()

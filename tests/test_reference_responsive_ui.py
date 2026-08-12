from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ReferenceResponsiveUiTests(unittest.TestCase):
    def test_reference_workspace_has_mobile_layout_and_no_fixed_body_width(self) -> None:
        css = (ROOT / "web" / "references.css").read_text(encoding="utf-8")
        self.assertNotIn("min-width: 1180px", css)
        self.assertIn("@media (max-width: 1120px)", css)
        self.assertIn("flex-direction: column", css)
        self.assertIn("@media (max-width: 600px)", css)
        self.assertIn("grid-template-columns: minmax(0, 1fr)", css)

    def test_reference_page_uses_current_responsive_assets(self) -> None:
        html = (ROOT / "web" / "references.html").read_text(encoding="utf-8")
        # 版本戳为内容哈希（asset_versions.py 维护），只断言资源被加载
        self.assertRegex(html, r"/assets/references\.css\?v=[0-9a-f]+")
        self.assertRegex(html, r"/assets/shared/site-nav\.css\?v=[0-9a-f]+")
        self.assertRegex(html, r"/assets/shared/site-nav\.js\?v=[0-9a-f]+")


if __name__ == "__main__":
    unittest.main()

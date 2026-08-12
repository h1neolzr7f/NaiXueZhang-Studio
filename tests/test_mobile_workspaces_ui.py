from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MobileWorkspacesUiTests(unittest.TestCase):
    def test_butler_stacks_primary_chat_without_fixed_page_width(self) -> None:
        css = (ROOT / "web" / "butler.css").read_text(encoding="utf-8")
        html = (ROOT / "web" / "butler.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 900px)", css)
        self.assertIn(".butler-body {\n    min-width: 0;", css)
        self.assertIn(".butler-chat-panel {\n    min-width: 0;", css)
        self.assertIn("order: 1", css)
        self.assertRegex(html, r"/assets/butler\.css\?v=[0-9a-f]+")

    def test_director_stacks_panels_and_contains_step_overflow(self) -> None:
        css = (ROOT / "web" / "director.css").read_text(encoding="utf-8")
        html = (ROOT / "web" / "director.html").read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 980px)", css)
        self.assertIn("body.director-body {\n    min-width: 0;", css)
        self.assertIn(".director-steps {\n    overflow-x: auto;", css)
        self.assertIn(".director-layout {\n    grid-template-columns: minmax(0, 1fr);", css)
        self.assertRegex(html, r"/assets/director\.css\?v=[0-9a-f]+")


if __name__ == "__main__":
    unittest.main()

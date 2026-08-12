from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SetupWebSafetyTests(unittest.TestCase):
    def test_upstream_web_refresh_is_permanently_disabled(self) -> None:
        script = (ROOT / "setup_web.ps1").read_text(encoding="utf-8")
        self.assertIn("throw", script)
        self.assertIn("never downloads another site's UI", script)
        self.assertNotIn("Invoke-WebRequest", script)
        self.assertNotIn("aitag.win", script)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path

from product_ops import build_verification_plan


ROOT = Path(__file__).resolve().parents[1]


class PortabilityContractTests(unittest.TestCase):
    def test_verification_commands_use_current_project_root(self) -> None:
        commands = "\n".join(build_verification_plan()["commands"])
        self.assertIn(str(ROOT), commands)
        self.assertNotIn(r"E:\aitag-mirror", commands)

    def test_support_scripts_are_project_relative_and_do_not_use_curl(self) -> None:
        build_bat = (ROOT / "build_char_tag_db.bat").read_text(encoding="utf-8-sig")
        setup_ps1 = (ROOT / "setup_web.ps1").read_text(encoding="utf-8-sig")
        self.assertIn('cd /d "%~dp0"', build_bat)
        self.assertNotIn(r"E:\aitag-mirror", build_bat)
        self.assertNotIn(r"E:\aitag-mirror", setup_ps1)
        self.assertNotIn("curl.exe", setup_ps1)
        self.assertNotIn("Invoke-WebRequest", setup_ps1)
        self.assertIn("never downloads another site's UI", setup_ps1)

    def test_machine_preferences_and_duplicate_assets_are_excluded(self) -> None:
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8-sig")
        self.assertIn("data/user_prefs.json", gitignore.splitlines())
        self.assertFalse(
            (ROOT / "web" / "shared" / "shared").exists(),
            "nested duplicate web/shared/shared must not shadow canonical assets",
        )

    def test_reproducible_dependency_lock_is_present(self) -> None:
        lock = (ROOT / "requirements.lock.txt").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(lock), 20)
        self.assertTrue(all("==" in line for line in lock if line and not line.startswith("#")))
        for package in (
            "aiosqlite",
            "fastapi",
            "httpx",
            "langgraph",
            "langgraph-checkpoint-sqlite",
            "pillow",
            "playwright",
            "uvicorn",
        ):
            self.assertTrue(
                any(line.lower().startswith(f"{package}==") for line in lock),
                f"{package} must be pinned in requirements.lock.txt",
            )


if __name__ == "__main__":
    unittest.main()

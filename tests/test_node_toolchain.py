from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class NodeToolchainTests(unittest.TestCase):
    def test_node_toolchain_is_declared_and_locked(self) -> None:
        package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
        lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertTrue(package["private"])
        self.assertEqual(package["engines"]["node"], ">=20")
        self.assertEqual(package["devDependencies"]["playwright"], "1.60.0")
        self.assertEqual(lock["lockfileVersion"], 3)

    def test_char_swap_probe_uses_the_project_dependency_not_codex_private_cache(self) -> None:
        source = (ROOT / "scripts" / "probe_char_swap_ui.js").read_text(encoding="utf-8")
        self.assertIn('require("playwright")', source)
        self.assertIn("npm install", source)
        self.assertNotIn("codex-runtimes", source)

    def test_verify_enforces_the_declared_node_major_version(self) -> None:
        source = (ROOT / "scripts" / "verify.ps1").read_text(encoding="utf-8-sig")
        self.assertIn("process.versions.node.split", source)
        self.assertIn("Node.js 20 or newer", source)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from butler_templates import ButlerTemplateStore


class ButlerTemplateStoreTests(unittest.TestCase):
    def test_saved_template_survives_restart_and_redacts_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "templates.json"
            first = ButlerTemplateStore(path)
            saved = first.save(
                label="我的批量任务",
                prompt="把待生成队列每个出 3 张，key=sk-secret-value",
            )

            templates = ButlerTemplateStore(path).list_all()

        restored = next(item for item in templates if item["id"] == saved["id"])
        self.assertEqual(restored["label"], "我的批量任务")
        self.assertNotIn("sk-secret-value", restored["prompt"])
        self.assertIn("[REDACTED]", restored["prompt"])
        self.assertTrue(restored["deletable"])


if __name__ == "__main__":
    unittest.main()

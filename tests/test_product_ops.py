from __future__ import annotations

import gc
import sqlite3
import tempfile
import unittest
import warnings
from pathlib import Path

from product_ops import PRODUCT_STRATEGY, build_product_health, build_verification_plan


class ProductOpsTests(unittest.TestCase):
    def test_strategy_has_positioning_and_roadmap(self) -> None:
        self.assertIn("本地优先", PRODUCT_STRATEGY["positioning"])
        phases = [item["phase"] for item in PRODUCT_STRATEGY["roadmap"]]
        self.assertEqual(phases[:4], ["P0", "P1", "P2", "P3"])

    def test_health_snapshot_is_read_only_shape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text("{}", encoding="utf-8")
            (root / "web").mkdir()
            (root / "data" / "images").mkdir(parents=True)
            (root / "data" / "generated").mkdir(parents=True)
            (root / "start_gallery.bat").write_text("", encoding="utf-8")
            (root / "start_gallery.ps1").write_text("", encoding="utf-8")
            health = build_product_health(
                {"data_dir": str(root / "data"), "web_dir": str(root / "web")},
                root,
            )
        self.assertIn("checks", health)
        self.assertIn("data", health)
        self.assertTrue(health["checks"]["web_dir"])
        self.assertTrue(health["checks"]["images_dir"])
        self.assertIn("fastapi", health["dependencies"])

    def test_health_snapshot_closes_its_sqlite_connection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data_dir = root / "data"
            data_dir.mkdir()
            db_path = data_dir / "aitag.db"
            connection = sqlite3.connect(db_path)
            connection.execute("create table works(id integer primary key)")
            connection.commit()
            connection.close()

            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", ResourceWarning)
                build_product_health({"data_dir": str(data_dir)}, root)
                gc.collect()

            leaked = [
                warning
                for warning in caught
                if "unclosed database" in str(warning.message)
            ]
            self.assertEqual(leaked, [])

    def test_verification_plan_contains_ops_endpoint(self) -> None:
        plan = build_verification_plan()
        self.assertTrue(any("/ops" in url for url in plan["manual_urls"]))
        self.assertTrue(any("/api/product/health" in url for url in plan["manual_urls"]))


if __name__ == "__main__":
    unittest.main()

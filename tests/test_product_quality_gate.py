from __future__ import annotations

import unittest

from scripts.product_quality_gate import collect_findings
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class ProductQualityGateTests(unittest.TestCase):
    def test_gate_has_no_p0_after_dashboard_work(self) -> None:
        result = collect_findings(ROOT)
        self.assertEqual(result["p0"], [])

    def test_gate_tracks_remaining_work_by_severity(self) -> None:
        result = collect_findings(ROOT)
        self.assertIn("p1", result)
        self.assertIn("p2", result)
        self.assertEqual(result["counts"]["p1"], 0)
        self.assertEqual(result["counts"]["p2"], len(result["p2"]))
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()

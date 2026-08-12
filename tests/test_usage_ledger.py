from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from usage_ledger import UsageLedger, usage_scope


class UsageLedgerTests(unittest.TestCase):
    def test_usage_is_persisted_and_summarized_without_inventing_unknown_cost(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "usage.db"
            ledger = UsageLedger(path)
            with usage_scope("workflow-7"):
                ledger.record(
                    kind="llm",
                    provider="relay",
                    model="grok-4.5",
                    input_tokens=120,
                    output_tokens=30,
                )
                ledger.record(
                    kind="image_generation",
                    provider="novelai",
                    model="nai-diffusion-4-5-full",
                    images=1,
                    anlas_spent=None,
                    cost_source="unknown",
                )

            summary = UsageLedger(path).summary(workflow_id="workflow-7")

        self.assertEqual(summary["calls"], 2)
        self.assertEqual(summary["input_tokens"], 120)
        self.assertEqual(summary["output_tokens"], 30)
        self.assertEqual(summary["total_tokens"], 150)
        self.assertEqual(summary["images"], 1)
        self.assertEqual(summary["anlas_spent"], 0)
        self.assertEqual(summary["anlas_unknown_images"], 1)
        self.assertFalse(summary["anlas_complete"])


if __name__ == "__main__":
    unittest.main()

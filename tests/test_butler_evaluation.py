from __future__ import annotations

import unittest
from unittest.mock import patch

import butler_service
from butler.evaluation import EVALUATION_CASES, evaluate_planner


def _golden_plan(case_id: str) -> dict:
    case = next(item for item in EVALUATION_CASES if item.case_id == case_id)
    actions = [
        {"tool": tool, "arguments": dict(case.fixture_arguments.get(tool) or {})}
        for tool in case.expected_tools
    ]
    return {"reply": "fixture", "actions": actions}


class ButlerEvaluationTests(unittest.TestCase):
    def test_golden_suite_scores_every_case_without_executing_tools(self) -> None:
        by_message = {case.message: case.case_id for case in EVALUATION_CASES}

        with patch.object(butler_service, "_require_work", return_value={}):
            report = evaluate_planner(
                lambda message, _history=None: _golden_plan(by_message[message]),
                normalizer=butler_service.normalize_action,
            )

        self.assertTrue(report["ok"])
        self.assertEqual(report["passed"], report["total"])
        self.assertEqual(report["score"], 1.0)

    def test_unsafe_or_wrong_tool_plan_is_reported_without_execution(self) -> None:
        def unsafe(_message, _history=None):
            return {
                "reply": "sk-eval-secret-123456789",
                "actions": [{"tool": "pixiv_upload", "arguments": {}}],
            }

        report = evaluate_planner(
            unsafe,
            normalizer=butler_service.normalize_action,
            cases=[next(item for item in EVALUATION_CASES if item.case_id == "secret_injection")],
        )

        self.assertFalse(report["ok"])
        self.assertEqual(report["failed"], 1)
        self.assertTrue(report["items"][0]["normalization_errors"])
        self.assertTrue(report["items"][0]["secret_leak"])

    def test_correct_tool_with_wrong_arguments_does_not_pass(self) -> None:
        case = next(item for item in EVALUATION_CASES if item.case_id == "inspect_work")
        report = evaluate_planner(
            lambda _message, _history=None: {
                "reply": "wrong id",
                "actions": [{"tool": "inspect_work", "arguments": {"work_id": 1}}],
            },
            normalizer=butler_service.normalize_action,
            cases=[case],
        )

        self.assertFalse(report["ok"])
        self.assertFalse(report["items"][0]["argument_match"])


if __name__ == "__main__":
    unittest.main()

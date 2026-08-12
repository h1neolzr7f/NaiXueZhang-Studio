from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import butler_service
from butler.evaluation import EVALUATION_CASES, evaluate_planner


def _fixture_planner(message: str, _history=None) -> dict:
    case = next(item for item in EVALUATION_CASES if item.message == message)
    return {
        "reply": "fixture",
        "actions": [
            {
                "tool": tool,
                "arguments": dict(case.fixture_arguments.get(tool) or {}),
            }
            for tool in case.expected_tools
        ],
    }


def _live_planner(message: str, history=None) -> dict:
    return butler_service.request_plan(message, history)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate Butler plans without executing any tool."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Call the configured planner; still never executes tools.",
    )
    parser.add_argument("--output", help="Optional JSON report path.")
    args = parser.parse_args()

    planner = _live_planner if args.live else _fixture_planner
    report = evaluate_planner(planner, normalizer=butler_service.normalize_action)
    report["mode"] = "live" if args.live else "fixture"
    payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        path = Path(args.output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SameOriginApiClientTests(unittest.TestCase):
    def test_frontend_requests_share_the_timeout_and_error_handling_module(self) -> None:
        web = ROOT / "web"
        offenders: list[str] = []
        for path in [*web.rglob("*.js"), *web.rglob("*.html")]:
            if path == web / "shared" / "api-client.js":
                continue
            if web / "vendor" in path.parents:
                continue
            source = path.read_text(encoding="utf-8")
            if re.search(r"\bfetch\s*\(", source):
                offenders.append(str(path.relative_to(ROOT)))
        self.assertEqual(
            offenders,
            [],
            "same-origin browser requests must use window.ApiClient for shared timeouts and errors",
        )


if __name__ == "__main__":
    unittest.main()

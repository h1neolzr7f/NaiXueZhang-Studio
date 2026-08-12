# -*- coding: utf-8 -*-
from __future__ import annotations

import time
import unittest

import nai_api


class TransientCircuitBreakerTests(unittest.TestCase):
    """瞬时故障熔断按 provider 分离：NAI 15s，闲云 45s。"""

    def setUp(self) -> None:
        nai_api._TOKEN_FAILURES.clear()

    def _record(self, provider: str, message: str = "request too frequent"):
        entry = {"id": f"slot-{provider}", "provider": provider, "token": "x"}
        nai_api._record_token_failure(entry, message)
        return entry

    def test_nai_transient_break_is_short(self) -> None:
        entry = self._record("novelai")
        state = nai_api._TOKEN_FAILURES["slot-novelai"]
        remaining = state["disabled_until"] - time.time()
        self.assertLess(remaining, 16.0)
        self.assertGreater(remaining, 10.0)

    def test_xianyun_transient_break_keeps_45s(self) -> None:
        entry = self._record("xianyun")
        state = nai_api._TOKEN_FAILURES["slot-xianyun"]
        remaining = state["disabled_until"] - time.time()
        self.assertLess(remaining, 46.0)
        self.assertGreater(remaining, 40.0)

    def test_disabled_until_respects_provider(self) -> None:
        self._record("novelai")
        self._record("xianyun")
        nai_until = nai_api._TOKEN_FAILURES["slot-novelai"]["disabled_until"]
        xy_until = nai_api._TOKEN_FAILURES["slot-xianyun"]["disabled_until"]
        self.assertLess(nai_until, xy_until)


if __name__ == "__main__":
    unittest.main()

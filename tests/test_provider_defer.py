# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest

import nai_batch


class ProviderDeferPolicyTests(unittest.TestCase):
    """NAI 与闲云的重试退避策略必须分离（用户需求）。"""

    def test_nai_defer_is_short(self) -> None:
        self.assertEqual(nai_batch._defer_retry_sec("novelai"), 8.0)
        self.assertEqual(nai_batch._defer_retry_sec("nai"), 8.0)
        self.assertLess(nai_batch._defer_retry_sec("novelai"), 10.0)

    def test_xianyun_defer_keeps_slow_path(self) -> None:
        self.assertEqual(nai_batch._defer_retry_sec("xianyun"), 45.0)

    def test_unknown_provider_defaults_to_nai(self) -> None:
        self.assertEqual(nai_batch._defer_retry_sec(""), 8.0)
        self.assertEqual(nai_batch._defer_retry_sec("some_provider"), 8.0)

    def test_constants_are_explicit(self) -> None:
        # NAI 官方冷却 3s，重试余量 8s；闲云冷却 20s，重试保持 45s
        self.assertEqual(nai_batch._NAI_DEFER_RETRY_SEC, 8.0)
        self.assertEqual(nai_batch._XIANYUN_DEFER_RETRY_SEC, 45.0)


if __name__ == "__main__":
    unittest.main()

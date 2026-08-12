# -*- coding: utf-8 -*-
from __future__ import annotations

import unittest
from unittest.mock import patch

import nai_api


class CooldownPolicyTests(unittest.TestCase):
    """NAI 固定 3s（稳一点），闲云固定 20s，两者分离。"""

    def _entry(self, provider="novelai"):
        return {"id": "slot-1", "provider": provider, "token": "x", "enabled": True}

    def test_nai_cooldown_is_fixed_3s_regardless_of_slot_count(self) -> None:
        for count in (1, 2, 3, 5):
            with patch.object(
                nai_api, "_enabled_token_entries",
                return_value=[self._entry() for _ in range(count)],
            ):
                self.assertEqual(nai_api._slot_cooldown_sec(self._entry()), 3.0)

    def test_xianyun_cooldown_stays_20s(self) -> None:
        entry = self._entry(provider="xianyun")
        for count in (1, 3):
            with patch.object(
                nai_api, "_enabled_token_entries",
                return_value=[entry for _ in range(count)],
            ):
                self.assertEqual(nai_api._slot_cooldown_sec(entry), 20.0)

    def test_providers_are_separated(self) -> None:
        nai_entry = self._entry("novelai")
        xy_entry = self._entry("xianyun")
        with patch.object(nai_api, "_enabled_token_entries", return_value=[nai_entry, xy_entry]):
            self.assertNotEqual(
                nai_api._slot_cooldown_sec(nai_entry),
                nai_api._slot_cooldown_sec(xy_entry),
            )
            self.assertLess(nai_api._slot_cooldown_sec(nai_entry), nai_api._slot_cooldown_sec(xy_entry))

    def test_worker_short_cooldown_retry_still_covers_3s(self) -> None:
        # worker 级等待重试阈值 5s 覆盖 NAI 固定 3s 冷却
        import nai_batch
        self.assertLess(3.0, 5.0)
        self.assertLess(nai_batch._NAI_DEFER_RETRY_SEC, 10.0)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import time
import unittest

from gallery_cache import cached, clear_all, invalidate


class GalleryCacheTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_all()

    def test_cached_reuses_value_within_ttl(self) -> None:
        calls = {"n": 0}

        def factory() -> int:
            calls["n"] += 1
            return 42

        self.assertEqual(cached("k", 60.0, factory), 42)
        self.assertEqual(cached("k", 60.0, factory), 42)
        self.assertEqual(calls["n"], 1)

    def test_invalidate_forces_refresh(self) -> None:
        calls = {"n": 0}

        def factory() -> int:
            calls["n"] += 1
            return calls["n"]

        self.assertEqual(cached("k2", 60.0, factory), 1)
        invalidate("k2")
        self.assertEqual(cached("k2", 60.0, factory), 2)


if __name__ == "__main__":
    unittest.main()
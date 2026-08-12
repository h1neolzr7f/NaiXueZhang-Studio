from __future__ import annotations

import math

import pytest

from aitag_core.storage.http_cache import DiskResponseCache


@pytest.mark.parametrize(
    ("ttl", "limit"),
    [(0, 1024), (-1, 1024), (math.inf, 1024), (math.nan, 1024), (60, 0), (60, -1)],
)
def test_cache_rejects_unbounded_or_non_expiring_configuration(tmp_path, ttl, limit) -> None:
    with pytest.raises(ValueError):
        DiskResponseCache(tmp_path, ttl_seconds=ttl, max_bytes=limit)


def test_cache_always_expires_and_enforces_byte_ceiling(tmp_path) -> None:
    cache = DiskResponseCache(tmp_path, ttl_seconds=10, max_bytes=4)
    url = "https://aitag.win/api/search"

    cache.put(url, b"1234", now=100)
    assert cache.get(url, now=109) == b"1234"
    assert cache.get(url, now=111) is None

    with pytest.raises(ValueError, match="size limit"):
        cache.put(url, b"12345", now=112)

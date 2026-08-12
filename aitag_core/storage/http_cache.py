"""Bounded HTTPS response cache for optional external discovery adapters."""

from __future__ import annotations

import hashlib
import math
import os
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse


class CacheUrlError(ValueError):
    """Raised when a cache key is not an absolute HTTPS URL."""


class DiskResponseCache:
    """Small atomic byte cache with TTL and a byte-size ceiling.

    The cache is deliberately content-addressed by URL and stores no source
    index.  It is safe to clear or rebuild; a failed cache read is equivalent
    to a miss and never blocks local gallery work.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        ttl_seconds: float = 600.0,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.root = Path(root)
        ttl = float(ttl_seconds)
        size_limit = int(max_bytes)
        if not math.isfinite(ttl) or ttl <= 0:
            raise ValueError("cache ttl_seconds must be a positive finite number")
        if size_limit <= 0:
            raise ValueError("cache max_bytes must be positive")
        self.ttl_seconds = ttl
        self.max_bytes = size_limit
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _key(url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        if parsed.scheme.casefold() != "https" or not parsed.netloc:
            raise CacheUrlError("external discovery cache requires an absolute HTTPS URL")
        return hashlib.sha256(parsed.geturl().encode("utf-8")).hexdigest()

    def _path(self, url: str) -> Path:
        return self.root / f"{self._key(url)}.bin"

    def get(self, url: str, *, now: float | None = None) -> bytes | None:
        path = self._path(url)
        try:
            stat = path.stat()
            current = time.time() if now is None else float(now)
            if current - stat.st_mtime > self.ttl_seconds:
                path.unlink(missing_ok=True)
                return None
            return path.read_bytes()
        except (OSError, ValueError):
            return None

    def put(self, url: str, payload: bytes, *, now: float | None = None) -> Path:
        path = self._path(url)
        if len(payload) > self.max_bytes:
            raise ValueError("response exceeds cache size limit")
        stamp = time.time() if now is None else float(now)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(bytes(payload))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
            os.utime(path, (stamp, stamp))
        finally:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass
        self.prune()
        return path

    def prune(self) -> int:
        try:
            entries = [path for path in self.root.glob("*.bin") if path.is_file()]
        except OSError:
            return 0
        total = 0
        sized: list[tuple[float, int, Path]] = []
        for path in entries:
            try:
                stat = path.stat()
            except OSError:
                continue
            total += stat.st_size
            sized.append((stat.st_mtime, stat.st_size, path))
        removed = 0
        for _, size, path in sorted(sized):
            if total <= self.max_bytes:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1
        return removed

    def stats(self) -> dict[str, int]:
        count = 0
        total = 0
        try:
            entries = self.root.glob("*.bin")
            for path in entries:
                try:
                    total += path.stat().st_size
                    count += 1
                except OSError:
                    continue
        except OSError:
            pass
        return {"count": count, "bytes": total, "max_bytes": self.max_bytes}

    def clear(self) -> int:
        removed = 0
        try:
            entries = self.root.glob("*.bin")
            for path in entries:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        except OSError:
            pass
        return removed

    def clear_url(self, url: str) -> bool:
        """Remove one cached response without touching other sources."""

        try:
            path = self._path(url)
            path.unlink(missing_ok=True)
            return True
        except (OSError, ValueError):
            return False


__all__ = ["CacheUrlError", "DiskResponseCache"]

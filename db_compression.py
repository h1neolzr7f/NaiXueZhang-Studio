"""Transparent zlib compression for large JSON columns in the site database.

Stored values keep a ``Z1:`` prefix followed by zlib-compressed UTF-8 JSON
bytes, so plain-text legacy rows (no prefix) keep working without migration.
Only columns that are never consumed by SQL string functions (LIKE /
json_extract) may use this; see DB_COMPRESSIBLE_COLUMNS in db.py.
"""

from __future__ import annotations

import zlib
from typing import Any

_PREFIX = b"Z1:"


def compress_text(text: str) -> bytes:
    """Compress a JSON string into a prefixed BLOB."""
    if text is None:
        return text  # type: ignore[return-value]
    return _PREFIX + zlib.compress(str(text).encode("utf-8"), 6)


def decompress_if_needed(value: Any) -> Any:
    """Return plain text for legacy rows and decoded text for compressed rows."""
    if isinstance(value, (bytes, bytearray)):
        if bytes(value).startswith(_PREFIX):
            try:
                return zlib.decompress(bytes(value)[len(_PREFIX) :]).decode("utf-8")
            except zlib.error:
                return value
        # SQLite may return BLOB back as bytes for TEXT columns.
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return value
    return value


def is_compressed(value: Any) -> bool:
    return isinstance(value, (bytes, bytearray)) and bytes(value).startswith(_PREFIX)

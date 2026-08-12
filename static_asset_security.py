"""Allow-list oriented protections for files exposed from the web directory."""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles


_PRIVATE_SUFFIXES = {
    ".bak",
    ".db",
    ".dump",
    ".env",
    ".key",
    ".log",
    ".old",
    ".pem",
    ".pfx",
    ".sql",
    ".sqlite",
    ".sqlite3",
}
_PRIVATE_NAME = re.compile(
    r"(?:^|[._-])(?:credential|credentials|password|secret|secrets|token|tokens)"
    r"(?:[._-]|$)",
    re.IGNORECASE,
)


def is_disallowed_web_asset(raw_path: str) -> bool:
    """Return True for backup, log, database, or credential-like paths."""

    parts = [part for part in str(raw_path or "").replace("\\", "/").split("/") if part]
    if not parts:
        return True
    for part in parts:
        lowered = part.lower()
        if lowered.startswith("."):
            return True
        # Timestamped deployment backups use names such as app.js.bak-20260811,
        # whose final pathlib suffix is not simply ".bak".
        if ".bak" in lowered or ".old" in lowered:
            return True
        if any(suffix in _PRIVATE_SUFFIXES for suffix in Path(lowered).suffixes):
            return True
        if _PRIVATE_NAME.search(lowered):
            return True
    return False


class SafeStaticFiles(StaticFiles):
    """StaticFiles variant that never publishes operational/private artifacts."""

    async def get_response(self, path: str, scope):  # type: ignore[no-untyped-def]
        if is_disallowed_web_asset(path):
            raise HTTPException(status_code=404, detail="not found")
        return await super().get_response(path, scope)

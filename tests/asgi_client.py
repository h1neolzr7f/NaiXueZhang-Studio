"""Test-only Starlette client compatibility for the installed HTTPX runtime.

Starlette 1.x prefers the future ``httpx2`` package name but retains a fully
compatible ``httpx`` fallback that emits a deprecation warning at import time.
The project intentionally does not add a second HTTP client dependency just
for tests, so expose the installed module under the preferred import name
before importing Starlette's client.
"""

from __future__ import annotations

import sys

try:
    import httpx2  # type: ignore[import-not-found]  # noqa: F401
except ModuleNotFoundError:
    import httpx

    sys.modules.setdefault("httpx2", httpx)

from starlette.testclient import TestClient

# The server rejects write ops without a matching X-Session-Token. TestClient
# requests must carry the token the app generated for this process, so patch
# the client's default headers at import time.
def _session_token_header() -> dict[str, str]:
    try:
        import server as app_server

        return {"X-Session-Token": app_server.SESSION_TOKEN}
    except Exception:
        return {}


_TEST_TOKEN_HEADER = _session_token_header()
if _TEST_TOKEN_HEADER:
    _orig_init = TestClient.__init__

    def __init__(self, *args, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        headers.update(_TEST_TOKEN_HEADER)
        _orig_init(self, *args, headers=headers, **kwargs)

    TestClient.__init__ = __init__

__all__ = ["TestClient"]

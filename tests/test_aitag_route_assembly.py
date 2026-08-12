from __future__ import annotations

from collections import Counter

import server


def test_aitag_online_routes_are_assembled_once_with_expected_methods() -> None:
    registrations = Counter(
        (method, route.path)
        for route in server.app.router.routes
        for method in (getattr(route, "methods", set()) or set())
        if str(getattr(route, "path", "")).startswith("/api/nai/aitag/")
    )

    for contract in (
        ("GET", "/api/nai/aitag/status"),
        ("GET", "/api/nai/aitag/search"),
        ("GET", "/api/nai/aitag/work/{work_id}"),
        ("POST", "/api/nai/aitag/import"),
        ("POST", "/api/nai/aitag/work/{work_id}/apply"),
        ("POST", "/api/nai/aitag/work/{work_id}/draft"),
        ("POST", "/api/nai/aitag/drafts/latest/restore"),
        ("GET", "/api/nai/aitag/drafts/{draft_id}"),
        ("POST", "/api/nai/aitag/cache/clear"),
    ):
        assert registrations[contract] == 1

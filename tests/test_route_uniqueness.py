from __future__ import annotations

from collections import defaultdict

import server


def test_http_method_and_path_pairs_are_registered_once() -> None:
    registrations: dict[tuple[tuple[str, ...], str], list[str]] = defaultdict(list)
    for route in server.app.router.routes:
        path = getattr(route, "path", "")
        methods = tuple(sorted(getattr(route, "methods", set()) or set()))
        if not path or not methods:
            continue
        registrations[(methods, path)].append(str(getattr(route, "name", "")))

    duplicates = {
        f"{','.join(methods)} {path}": names
        for (methods, path), names in registrations.items()
        if len(names) > 1
    }
    assert duplicates == {}


def test_dynamic_routes_do_not_shadow_later_static_routes() -> None:
    routes = [
        route
        for route in server.app.router.routes
        if getattr(route, "methods", None) and getattr(route, "path_regex", None)
    ]
    shadows: list[str] = []
    for index, dynamic in enumerate(routes):
        if "{" not in str(getattr(dynamic, "path", "")):
            continue
        for static in routes[index + 1 :]:
            static_path = str(getattr(static, "path", ""))
            if "{" in static_path or not (dynamic.methods & static.methods):
                continue
            if dynamic.path_regex.fullmatch(static_path):
                shadows.append(f"{dynamic.path} shadows {static_path}")

    assert shadows == []

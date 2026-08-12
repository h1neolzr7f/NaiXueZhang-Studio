from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import httpx
from fastapi import FastAPI

from routes import gallery as gallery_routes
from static_asset_security import SafeStaticFiles, is_disallowed_web_asset
from tests.asgi_client import TestClient


def _gallery_client() -> TestClient:
    app = FastAPI()
    app.include_router(gallery_routes.router)
    return TestClient(app)


def test_data_images_uses_existing_sibling_extension_over_http(tmp_path: Path) -> None:
    image = tmp_path / "images" / "NAI" / "7" / "sample.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png-sibling")

    with patch.object(gallery_routes, "DATA_DIR", tmp_path), patch.object(
        gallery_routes, "CDN_URL", ""
    ):
        response = _gallery_client().get("/data/images/NAI/7/sample.webp")

    assert response.status_code == 200
    assert response.content == b"png-sibling"
    assert response.headers["content-type"].startswith("image/png")


def test_data_images_missing_and_cdn_behaviour_over_http(tmp_path: Path) -> None:
    http = Mock()
    http.get.side_effect = [
        httpx.Response(200, content=b"cdn-image", headers={"content-type": "image/webp"}),
        httpx.Response(503, content=b"upstream unavailable"),
        httpx.ConnectError("offline"),
    ]
    gallery_routes._CDN_MISS_CACHE.clear()
    with patch.object(gallery_routes, "DATA_DIR", tmp_path), patch.object(
        gallery_routes, "CDN_URL", "https://cdn.example.test/base"
    ), patch.object(gallery_routes, "_CDN_CLIENT", http):
        client = _gallery_client()
        success = client.get("/data/images/cdn-success.webp")
        upstream_error = client.get("/data/images/cdn-503.webp")
        network_error = client.get("/data/images/cdn-offline.webp")

    assert success.status_code == 200
    assert success.content == b"cdn-image"
    assert upstream_error.status_code == 404
    assert network_error.status_code == 404
    assert [call.args[0] for call in http.get.call_args_list] == [
        "https://cdn.example.test/base/cdn-success.webp",
        "https://cdn.example.test/base/cdn-503.webp",
        "https://cdn.example.test/base/cdn-offline.webp",
    ]


def test_data_images_missing_without_cdn_is_404(tmp_path: Path) -> None:
    http = Mock()
    with patch.object(gallery_routes, "DATA_DIR", tmp_path), patch.object(
        gallery_routes, "CDN_URL", ""
    ), patch.object(gallery_routes, "_CDN_CLIENT", http):
        response = _gallery_client().get("/data/images/missing.webp")

    assert response.status_code == 404
    http.get.assert_not_called()


def test_sensitive_asset_names_are_rejected_by_root_and_static_routes(
    tmp_path: Path,
) -> None:
    (tmp_path / "app.js").write_text("public", encoding="utf-8")
    blocked = [
        "app.js.bak-20260811",
        "app.old.js",
        "debug.log",
        "gallery.sqlite3",
        ".env",
        "account-secrets.json",
    ]
    for name in blocked:
        (tmp_path / name).write_text("private", encoding="utf-8")
        assert is_disallowed_web_asset(name)

    static_app = FastAPI()
    static_app.mount("/assets", SafeStaticFiles(directory=str(tmp_path)), name="assets")
    static_client = TestClient(static_app)
    assert static_client.get("/assets/app.js").status_code == 200
    for name in blocked:
        assert static_client.get(f"/assets/{name}").status_code == 404

    with patch.object(gallery_routes, "WEB_DIR", tmp_path):
        root_client = _gallery_client()
        assert root_client.get("/app.js").status_code == 200
        for name in blocked:
            assert root_client.get(f"/{name}").status_code == 404


def test_config_and_storage_open_never_expose_absolute_server_paths(
    tmp_path: Path,
) -> None:
    private_root = tmp_path / "private-project"
    private_root.mkdir()
    private_paths = {
        "project_root": str(private_root),
        "data_dir": str(private_root / "data"),
        "images_dir": str(private_root / "data" / "images"),
        "database_path": str(private_root / "data" / "aitag.db"),
        "generated_dir": str(private_root / "data" / "generated"),
    }
    with patch.object(
        gallery_routes, "cached", side_effect=lambda _key, _ttl, builder: builder()
    ), patch.object(
        gallery_routes.DB, "list_rank_calendar", return_value={"years": [], "months": []}
    ), patch.object(gallery_routes, "load_prefs", return_value={}):
        config_response = _gallery_client().get("/api/config")

    assert config_response.status_code == 200
    assert "storage_paths" not in config_response.json()
    assert str(private_root) not in config_response.text

    with patch.object(
        gallery_routes, "storage_paths", return_value=private_paths
    ), patch.object(gallery_routes.sys, "platform", "linux"):
        open_response = _gallery_client().post("/api/storage/open?target=root")

    assert open_response.status_code == 200
    assert open_response.json()["target"] == "root"
    assert "path" not in open_response.json()
    assert str(private_root) not in open_response.text

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from routes.maintenance import build_router


def test_gallery_maintenance_routes_expose_storage_without_private_paths(tmp_path) -> None:
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    app = FastAPI()
    app.include_router(build_router(data))

    with TestClient(app) as client:
        response = client.get("/api/maintenance/storage")

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["storage"]["asset_bytes"] == 0
    assert str(tmp_path) not in response.text


def test_migrate_webp_rejects_invalid_or_negative_limit(tmp_path) -> None:
    data = tmp_path / "data"
    (data / "images").mkdir(parents=True)
    app = FastAPI()
    app.include_router(build_router(data))
    with TestClient(app) as client:
        for invalid_limit in ("many", -1, 1.5, True, {}, []):
            response = client.post(
                "/api/maintenance/originals/migrate-webp",
                json={"limit": invalid_limit},
            )
            assert response.status_code == 422
            assert response.json()["detail"] == "limit must be a non-negative integer"

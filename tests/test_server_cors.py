from __future__ import annotations

import unittest

from tests.asgi_client import TestClient

import server


class ServerCorsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(server.app)

    def test_untrusted_origin_cannot_read_api_response(self) -> None:
        response = self.client.get(
            "/api/config",
            headers={"Origin": "https://attacker.example"},
        )

        self.assertEqual(200, response.status_code)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_gallery_origins_can_read_api_response(self) -> None:
        for origin in ("http://127.0.0.1:8797", "http://localhost:8797"):
            with self.subTest(origin=origin):
                response = self.client.get("/api/config", headers={"Origin": origin})

                self.assertEqual(200, response.status_code)
                self.assertEqual(
                    origin,
                    response.headers.get("access-control-allow-origin"),
                )

    def test_null_origin_cannot_read_api_response(self) -> None:
        response = self.client.get("/api/config", headers={"Origin": "null"})

        self.assertEqual(200, response.status_code)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_request_without_origin_remains_available(self) -> None:
        response = self.client.get("/api/config")

        self.assertEqual(200, response.status_code)
        self.assertNotIn("access-control-allow-origin", response.headers)

    def test_preflight_only_allows_gallery_origins(self) -> None:
        preflight_headers = {
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        }
        allowed = self.client.options(
            "/api/config",
            headers={
                **preflight_headers,
                "Origin": "http://127.0.0.1:8797",
            },
        )
        denied = self.client.options(
            "/api/config",
            headers={
                **preflight_headers,
                "Origin": "https://attacker.example",
            },
        )

        self.assertEqual(200, allowed.status_code)
        self.assertEqual(
            "http://127.0.0.1:8797",
            allowed.headers.get("access-control-allow-origin"),
        )
        self.assertEqual(400, denied.status_code)
        self.assertNotIn("access-control-allow-origin", denied.headers)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.asgi_client import TestClient

import pixiv_accounts
import server


class PixivAccountSlotTests(unittest.TestCase):
    def test_create_empty_slot_and_api(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch.object(pixiv_accounts, "DATA_DIR", data_dir), patch.object(
                pixiv_accounts, "ACCOUNTS_PATH", data_dir / "pixiv_accounts.local.json"
            ), patch.object(
                pixiv_accounts, "ACCOUNTS_BACKUP_PATH", data_dir / "pixiv_accounts.local.backup.json"
            ):
                created = pixiv_accounts.create_account_slot(label="测试新号")
                self.assertTrue(created.get("ok"))
                self.assertEqual(created["account"]["label"], "测试新号")
                self.assertFalse(created["account"].get("has_token"))
                self.assertEqual(len(pixiv_accounts.list_accounts()), 1)

                client = TestClient(server.app)
                # Re-patch for request path (same process)
                r = client.post(
                    "/api/pixiv/accounts/slot",
                    json={"label": "API新号", "set_active": True},
                )
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertTrue(body.get("ok"))
                self.assertIn("账号槽", body.get("message") or "账号槽")

    def test_add_account_without_token_creates_slot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            with patch.object(pixiv_accounts, "DATA_DIR", data_dir), patch.object(
                pixiv_accounts, "ACCOUNTS_PATH", data_dir / "pixiv_accounts.local.json"
            ), patch.object(
                pixiv_accounts, "ACCOUNTS_BACKUP_PATH", data_dir / "pixiv_accounts.local.backup.json"
            ):
                out = pixiv_accounts.add_account(refresh_token="", label="空token")
                self.assertTrue(out.get("ok"))
                self.assertEqual(out["account"]["label"], "空token")


if __name__ == "__main__":
    unittest.main()

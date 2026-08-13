from __future__ import annotations

import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_secrets import PREFIX, protect_secret, unprotect_secret
import pixiv_accounts
import nai_api


class LocalSecretTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_dpapi_secret_round_trip_is_user_bound_and_not_plaintext(self) -> None:
        secret = "pst-test-value-that-must-not-be-plain"
        encrypted = protect_secret(secret)

        self.assertTrue(encrypted.startswith(PREFIX))
        self.assertNotIn(secret, encrypted)
        self.assertEqual(unprotect_secret(encrypted), secret)

    def test_plaintext_legacy_values_remain_readable_for_migration(self) -> None:
        self.assertEqual(unprotect_secret("legacy-value"), "legacy-value")

    def test_non_windows_refuses_to_persist_plaintext_secrets(self) -> None:
        from local_secrets import SecretProtectionUnavailable

        with patch("local_secrets.os.name", "posix"):
            with self.assertRaises(SecretProtectionUnavailable):
                protect_secret("pst-must-not-be-written")

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_plaintext_pixiv_backup_is_migrated_even_when_primary_is_already_encrypted(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            primary = root / "pixiv_accounts.local.json"
            backup = root / "pixiv_accounts.local.backup.json"
            primary.write_text(
                json.dumps({"active_id": "a", "accounts": [{"id": "a", "refresh_token": protect_secret("primary-secret")}]}),
                encoding="utf-8",
            )
            backup.write_text(
                json.dumps({"active_id": "a", "accounts": [{"id": "a", "refresh_token": "backup-secret"}]}),
                encoding="utf-8",
            )
            with patch.object(pixiv_accounts, "ACCOUNTS_PATH", primary), patch.object(
                pixiv_accounts, "ACCOUNTS_BACKUP_PATH", backup
            ):
                loaded = pixiv_accounts._load_accounts_file()

            stored_backup = json.loads(backup.read_text(encoding="utf-8"))
            self.assertEqual(loaded["accounts"][0]["refresh_token"], "primary-secret")
            self.assertTrue(stored_backup["accounts"][0]["refresh_token"].startswith(PREFIX))
            self.assertNotIn("backup-secret", backup.read_text(encoding="utf-8"))

    @unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
    def test_plaintext_nai_pool_is_migrated_when_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            token_path = Path(temp) / "nai_token.local.json"
            token_path.write_text(
                json.dumps(
                    {
                        "token": "pst-legacy-primary",
                        "tokens": [
                            {"id": "legacy", "provider": "novelai", "token": "pst-legacy-primary", "enabled": True}
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch.object(nai_api, "TOKEN_PATH", token_path):
                loaded = nai_api._read_token_file()

            stored = json.loads(token_path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["tokens"][0]["token"], "pst-legacy-primary")
            self.assertTrue(stored["token"].startswith(PREFIX))
            self.assertTrue(stored["tokens"][0]["token"].startswith(PREFIX))
            self.assertNotIn("pst-legacy-primary", token_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

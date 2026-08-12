import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import nai_api
from nai_char import MAX_FREE_PIXELS, build_generate_payload


class NaiAccountSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._failures = dict(nai_api._TOKEN_FAILURES)
        self._validations = dict(nai_api._TOKEN_VALIDATIONS)
        nai_api._TOKEN_FAILURES.clear()
        nai_api._TOKEN_VALIDATIONS.clear()

    def tearDown(self) -> None:
        nai_api._TOKEN_FAILURES.clear()
        nai_api._TOKEN_FAILURES.update(self._failures)
        nai_api._TOKEN_VALIDATIONS.clear()
        nai_api._TOKEN_VALIDATIONS.update(self._validations)

    def test_pool_check_recognizes_paper_account_without_removing_token(self) -> None:
        entry = {
            "id": "nai-paper",
            "label": "NAI Paper",
            "provider": "novelai",
            "token": "secret",
            "enabled": True,
        }
        with patch.object(
            nai_api,
            "_token_check_request",
            side_effect=[
                (400, "{}"),
                (404, ""),
                (200, '{"emailVerified":true}'),
            ],
        ) as request, patch.object(nai_api, "_remove_token_entry") as remove:
            result = nai_api._check_one_token_entry(entry, remove_bad=True)

        self.assertTrue(result["ok"])
        self.assertEqual(result["tier"], 0)
        self.assertEqual(result["plan"], "paper")
        self.assertTrue(result["free_confirmed"])
        self.assertEqual(request.call_count, 3)
        remove.assert_not_called()

    def test_pool_check_preserves_persistent_generation_token_when_account_status_is_hidden(self) -> None:
        entry = {
            "id": "nai-persistent",
            "label": "NAI persistent",
            "provider": "novelai",
            "token": "secret",
            "enabled": True,
        }
        with patch.object(
            nai_api,
            "_token_check_request",
            side_effect=[
                (400, "{}"),
                (404, ""),
                (401, ""),
            ],
        ), patch.object(nai_api, "_remove_token_entry") as remove:
            result = nai_api._check_one_token_entry(entry, remove_bad=True)

        self.assertFalse(result["ok"])
        self.assertFalse(result["removed"])
        self.assertFalse(result["account_status_available"])
        self.assertIn("preserved", result["message"].lower())
        remove.assert_not_called()

    def test_automatic_pool_uses_free_nai_and_xianyun_without_paid_nai(self) -> None:
        xianyun = {
            "id": "xy",
            "label": "Xianyun",
            "provider": "xianyun",
            "token": "xy-secret",
            "enabled": True,
        }
        paid = {
            "id": "nai-paid",
            "label": "NAI paid",
            "provider": "novelai",
            "token": "paid-secret",
            "enabled": True,
        }
        free = {
            "id": "nai-free",
            "label": "NAI free",
            "provider": "novelai",
            "token": "free-secret",
            "enabled": True,
        }
        opus = {
            "id": "nai-opus",
            "label": "NAI Opus",
            "provider": "novelai",
            "token": "opus-secret",
            "enabled": True,
        }
        old_validations = dict(nai_api._TOKEN_VALIDATIONS)
        old_cursor = nai_api._TOKEN_CURSOR
        try:
            nai_api._TOKEN_VALIDATIONS.clear()
            nai_api._TOKEN_VALIDATIONS.update(
                {
                    "nai-paid": {"ok": True, "tier": 1, "is_opus": False},
                    "nai-free": {"ok": True, "tier": 0, "is_opus": False},
                    "nai-opus": {"ok": True, "tier": 3, "is_opus": True},
                }
            )
            nai_api._TOKEN_CURSOR = 0
            with patch.object(
                nai_api,
                "_enabled_token_entries",
                return_value=[xianyun, paid, free, opus],
            ):
                candidates = nai_api._candidate_token_entries()
                first = nai_api._next_token_entry()
                second = nai_api._next_token_entry()

                self.assertEqual(nai_api.generation_concurrency(), 3)
                self.assertEqual(nai_api.generation_concurrency_for_batch(2), 2)
                self.assertEqual(nai_api.generation_concurrency_for_batch(5), 3)
                self.assertEqual(
                    [entry["id"] for entry in candidates],
                    ["nai-free", "nai-opus", "xy"],
                )
                self.assertEqual(
                    [first["id"], second["id"]],
                    ["nai-free", "nai-opus"],
                )
        finally:
            nai_api._TOKEN_VALIDATIONS.clear()
            nai_api._TOKEN_VALIDATIONS.update(old_validations)
            nai_api._TOKEN_CURSOR = old_cursor

    def test_free_payload_enforces_pixel_budget_and_rejects_image_inputs(self) -> None:
        oversized = build_generate_payload(
            {"prompt": "test", "width": 1216, "height": 1216, "steps": 28},
            force_free=True,
        )
        self.assertLessEqual(oversized["width"] * oversized["height"], MAX_FREE_PIXELS)
        self.assertTrue(oversized["resized_for_free"])

        referenced = build_generate_payload(
            {
                "prompt": "test",
                "width": 832,
                "height": 1216,
                "steps": 28,
                "reference_image_multiple": ["data:image/png;base64,test"],
            },
            force_free=True,
        )
        self.assertFalse(referenced["free_eligible"])

    def test_paper_account_uses_information_endpoint_as_token_fallback(self) -> None:
        entry = {"id": "slot", "label": "NAI #1", "provider": "novelai", "token": "secret"}
        subscription = MagicMock(status_code=400)
        image = MagicMock(status_code=404)
        information = MagicMock(status_code=200, content=b"{}")
        information.json.return_value = {"emailVerified": True}
        client = MagicMock()
        client.get.side_effect = [subscription, image, information]
        client_context = MagicMock()
        client_context.__enter__.return_value = client
        client_context.__exit__.return_value = False

        with patch.object(nai_api, "_select_token_entry", return_value=entry), patch.object(
            nai_api.httpx, "Client", return_value=client_context
        ):
            result = nai_api.get_subscription()

        self.assertTrue(result["ok"])
        self.assertEqual(result["plan"], "paper")
        self.assertFalse(result["membership_active"])
        self.assertFalse(result["is_opus"])
        self.assertTrue(result["email_verified"])
        self.assertEqual(client.get.call_count, 3)

    def test_persistent_generation_token_is_not_mislabeled_invalid(self) -> None:
        entry = {"id": "slot", "label": "NAI #1", "provider": "novelai", "token": "secret"}
        subscription = MagicMock(status_code=400)
        image = MagicMock(status_code=404)
        information = MagicMock(status_code=401, content=b"")
        client = MagicMock()
        client.get.side_effect = [subscription, image, information]
        client_context = MagicMock()
        client_context.__enter__.return_value = client
        client_context.__exit__.return_value = False

        with patch.object(nai_api, "_select_token_entry", return_value=entry), patch.object(
            nai_api.httpx, "Client", return_value=client_context
        ):
            result = nai_api.get_subscription()

        self.assertTrue(result["ok"])
        self.assertTrue(result["generation_token_configured"])
        self.assertFalse(result["account_status_available"])
        self.assertIsNone(result["token_valid"])
        self.assertIsNone(result["anlas_total"])

    def test_generation_body_uses_subscription_channel(self) -> None:
        """Member accounts (Opus/Scroll/Tablet) always use the subscription
        channel: shared trial drops v4 char_caption features (hair color etc.).
        The member free tier is enforced by size/step clamping, not by the
        shared-trial flag."""
        entry = {"id": "slot", "label": "NAI #1", "provider": "novelai", "token": "secret"}
        send = AsyncMock(return_value={"ok": False, "error": "probe", "message": "probe"})
        with patch.object(nai_api, "_pick_available_token", return_value=(entry, "", 0.0, "novelai")), patch.object(
            nai_api, "_candidate_token_entries", return_value=[entry]
        ), patch.object(nai_api, "_generate_image_with_entry", send):
            asyncio.run(
                nai_api.generate_image(
                    {"Source": "NovelAI Diffusion V4.5", "prompt": "test"},
                    force_free=True,
                    generation_series_id="batch-task-1",
                )
            )

        body = send.await_args.args[4]
        self.assertFalse(body["use_new_shared_trial"])
        self.assertEqual(body["parameters"]["n_samples"], 1)
        self.assertEqual(send.await_args.kwargs["generation_series_id"], "batch-task-1")

    def test_ambiguous_generation_failure_never_fails_over_to_another_token(self) -> None:
        first = {"id": "slot-1", "label": "NAI #1", "provider": "novelai", "token": "a"}
        second = {"id": "slot-2", "label": "NAI #2", "provider": "novelai", "token": "b"}
        send = AsyncMock(
            return_value={
                "ok": False,
                "error": "billing_uncertain",
                "message": "response interrupted",
                "billing_uncertain": True,
                "retry_safe": False,
            }
        )
        with patch.object(
            nai_api,
            "_pick_available_token",
            return_value=(first, "", 0.0, "novelai"),
        ), patch.object(
            nai_api, "_candidate_token_entries", return_value=[first, second]
        ), patch.object(nai_api, "_generate_image_with_entry", send):
            result = asyncio.run(
                nai_api.generate_image(
                    {"Source": "NovelAI Diffusion V4.5", "prompt": "test"}
                )
            )

        self.assertEqual(result["error"], "billing_uncertain")
        self.assertEqual(send.await_count, 1)

    def test_read_timeout_after_request_is_marked_billing_uncertain(self) -> None:
        entry = {"id": "slot", "label": "NAI #1", "provider": "novelai", "token": "a"}
        client_context = MagicMock()
        client_context.__aenter__ = AsyncMock(return_value=MagicMock())
        client_context.__aexit__ = AsyncMock(return_value=False)
        with patch.object(
            nai_api.httpx, "AsyncClient", return_value=client_context
        ), patch.object(
            nai_api,
            "_generate_novelai_png",
            AsyncMock(side_effect=nai_api.httpx.ReadTimeout("response interrupted")),
        ), patch.object(nai_api, "_record_token_failure", return_value=True):
            result = asyncio.run(
                nai_api._generate_image_with_entry(
                    entry,
                    {"prompt": "test"},
                    {},
                    {"model": "nai", "free_eligible": True},
                    {"input": "test", "parameters": {}},
                    work_id=1,
                    wait_for_slot=False,
                )
            )

        self.assertEqual(result["error"], "billing_uncertain")
        self.assertTrue(result["request_attempted"])
        self.assertTrue(result["billing_uncertain"])
        self.assertFalse(result["retry_safe"])
        self.assertFalse(result["fallback_available"])

    def test_402_quarantines_account_without_disabling_credential(self) -> None:
        entry = {
            "id": "nai-paper",
            "label": "NAI Paper",
            "provider": "novelai",
            "token": "secret",
            "enabled": True,
        }
        message = (
            'NAI API error 402: {"statusCode":402,"message":"Not enough Anlas '
            'and out of trial image generations. Required: 20, Available: 19"}'
        )

        with patch.object(nai_api, "_disable_token_entry") as disable:
            handled = nai_api._record_token_failure(entry, message)

        self.assertTrue(handled)
        self.assertTrue(nai_api._is_token_temporarily_disabled(entry))
        self.assertEqual(
            nai_api._TOKEN_FAILURES["nai-paper"]["reason"],
            "quota_exhausted",
        )
        self.assertTrue(nai_api._TOKEN_VALIDATIONS["nai-paper"]["quota_exhausted"])
        disable.assert_not_called()

    def test_empty_timeout_exception_gets_actionable_message(self) -> None:
        self.assertEqual(
            nai_api._exception_message(TimeoutError()),
            "TimeoutError",
        )

    def test_successful_pool_check_reactivates_quota_quarantined_account(self) -> None:
        entry = {
            "id": "nai-paper",
            "label": "NAI Paper",
            "provider": "novelai",
            "token": "secret",
            "enabled": True,
        }
        nai_api._TOKEN_FAILURES["nai-paper"] = {
            "reason": "quota_exhausted",
            "disabled_until": 99_999_999_999.0,
        }
        with patch.object(nai_api, "_normalize_token_entries", return_value=[entry]), patch.object(
            nai_api,
            "_check_one_token_entry",
            return_value={
                "id": "nai-paper",
                "ok": True,
                "message": "NovelAI Paper account verified",
                "tier": 0,
                "is_opus": False,
            },
        ), patch.object(nai_api, "token_status", return_value={"tokens": []}):
            result = nai_api.check_token_pool()

        self.assertTrue(result["ok"])
        self.assertNotIn("nai-paper", nai_api._TOKEN_FAILURES)
        self.assertFalse(nai_api._TOKEN_VALIDATIONS["nai-paper"]["quota_exhausted"])

    def test_successful_generation_records_anlas_as_unknown_when_provider_does_not_report_it(self) -> None:
        entry = {"id": "slot", "label": "NAI #1", "provider": "novelai", "token": "secret"}
        send = AsyncMock(
            return_value={
                "ok": True,
                "provider": "novelai",
                "model": "nai-diffusion-4-5-full",
                "billing_free_confirmed": False,
                "free_eligible": True,
                "filename": "generated.png",
            }
        )
        with patch.object(nai_api, "_pick_available_token", return_value=(entry, "", 0.0, "novelai")), patch.object(
            nai_api, "_candidate_token_entries", return_value=[entry]
        ), patch.object(nai_api, "_generate_image_with_entry", send), patch.object(
            nai_api, "record_usage"
        ) as record_usage:
            result = asyncio.run(
                nai_api.generate_image(
                    {"Source": "NovelAI Diffusion V4.5", "prompt": "test"},
                    force_free=True,
                )
            )

        self.assertTrue(result["ok"])
        self.assertIsNone(result["usage"]["anlas_spent"])
        self.assertEqual(result["usage"]["cost_source"], "unknown")
        recorded = record_usage.call_args.kwargs
        self.assertEqual(recorded["images"], 1)
        self.assertIsNone(recorded["anlas_spent"])


if __name__ == "__main__":
    unittest.main()

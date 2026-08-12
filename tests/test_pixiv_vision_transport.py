from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

import pixiv_ai_transport
import pixiv_launch


class PixivVisionTransportTests(unittest.TestCase):
    def test_chat_completion_accepts_openai_content_parts(self) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": [{"type": "text", "text": "{\"ok\":true}"}]}}],
            "usage": {},
        }
        client = MagicMock()
        client.post.return_value = response
        context = MagicMock()
        context.__enter__.return_value = client
        context.__exit__.return_value = False
        env = {
            "api_key": "test-key",
            "model": "relay-model",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30,
            "max_tokens": 1000,
        }

        with patch.object(pixiv_ai_transport.httpx, "Client", return_value=context), patch.object(
            pixiv_ai_transport, "record_usage"
        ):
            text = pixiv_launch._chat_completion(env, "system", {"task": "plan"})

        self.assertEqual(text, '{"ok":true}')

    def test_chat_completion_reports_missing_content_as_retryable_value_error(self) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"role": "assistant"}}],
            "usage": {},
        }
        client = MagicMock()
        client.post.return_value = response
        context = MagicMock()
        context.__enter__.return_value = client
        context.__exit__.return_value = False
        env = {
            "api_key": "test-key",
            "model": "relay-model",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30,
            "max_tokens": 1000,
        }

        with patch.object(pixiv_ai_transport.httpx, "Client", return_value=context), patch.object(
            pixiv_ai_transport, "record_usage"
        ):
            with self.assertRaisesRegex(ValueError, "没有返回可用文本"):
                pixiv_launch._chat_completion(env, "system", {"task": "plan"})

    def test_vision_health_check_uses_four_tiny_low_detail_images(self) -> None:
        with patch.object(
            pixiv_ai_transport,
            "_ai_env",
            return_value={
                "api_key": "test-key",
                "model": "vision-model",
                "api_base": "https://api.openai.com/v1",
                "timeout": 30,
                "max_tokens": 1000,
            },
        ), patch.object(pixiv_ai_transport, "load_config", return_value={}), patch.object(
            pixiv_ai_transport,
            "_chat_completion",
            return_value='{"vision_confirmed":true,"description":"test square"}',
        ) as completion:
            result = pixiv_launch.test_ai_vision_connection()

        self.assertTrue(result["ok"])
        self.assertTrue(result["vision_confirmed"])
        kwargs = completion.call_args.kwargs
        self.assertEqual(len(kwargs["image_data_urls"]), 4)
        self.assertTrue(all(url.startswith("data:image/png;base64,") for url in kwargs["image_data_urls"]))
        self.assertEqual(kwargs["image_detail"], "low")
        self.assertEqual(kwargs["max_tokens_override"], 160)
        self.assertEqual(kwargs["temperature_override"], 0.0)
        self.assertTrue(kwargs["json_mode"])

    def test_chat_completion_places_multiple_images_after_one_text_part(self) -> None:
        response = MagicMock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "total_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 12},
            },
        }
        client = MagicMock()
        client.post.return_value = response
        context = MagicMock()
        context.__enter__.return_value = client
        context.__exit__.return_value = False
        env = {
            "api_key": "test-key",
            "model": "grok-4.5",
            "api_base": "https://api.openai.com/v1",
            "timeout": 30,
            "max_tokens": 1000,
        }
        with patch.object(pixiv_ai_transport.httpx, "Client", return_value=context), patch.object(
            pixiv_ai_transport, "record_usage"
        ) as record_usage:
            pixiv_launch._chat_completion(
                env,
                "system",
                {"task": "audit"},
                image_data_urls=["data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB"],
                image_detail="low",
                max_tokens_override=900,
                temperature_override=0.2,
                json_mode=True,
            )

        request = client.post.call_args.kwargs["json"]
        content = request["messages"][1]["content"]
        self.assertEqual(content[0]["type"], "text")
        self.assertEqual([part["type"] for part in content[1:]], ["image_url", "image_url"])
        self.assertEqual(content[1]["image_url"]["url"], "data:image/jpeg;base64,AAA")
        self.assertEqual(content[1]["image_url"]["detail"], "low")
        self.assertEqual(request["max_tokens"], 900)
        self.assertEqual(request["temperature"], 0.2)
        self.assertEqual(request["reasoning_effort"], "low")
        self.assertEqual(request["response_format"], {"type": "json_object"})
        record_usage.assert_called_once()
        usage = record_usage.call_args.kwargs
        self.assertEqual(usage["kind"], "llm")
        self.assertEqual(usage["input_tokens"], 80)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["cached_tokens"], 12)


if __name__ == "__main__":
    unittest.main()

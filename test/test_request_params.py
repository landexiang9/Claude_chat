"""The editor's effective parameters must be the same parameters sent by SDKs."""
import json
import queue
import threading
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import httpx
import httpx2
from claude_chat.request_params import generation_params, preview_generation_params, validate_request_params
from claude_chat.services.config_service import ConfigService
from claude_chat.http_router import HttpApiRouter


class RequestParameterTests(unittest.TestCase):
    def test_preview_migration_and_platform_defaults(self):
        config = {"temperature": 0.1, "deepseek_temperature": 0.9, "gemini_temperature": 0.6,
                  "model_configs": {"test": {"custom_params": {"temperature": 0.333, "top_p": 0.8}}}}
        result = preview_generation_params("claude", "test", config)
        self.assertEqual(result["temperature"], 0.333)
        self.assertEqual(result["top_p"], 0.8)
        self.assertEqual(preview_generation_params("deepseek", "other", config)["temperature"], 0.9)
        self.assertEqual(preview_generation_params("gemini", "other", config)["temperature"], 0.6)

    def test_override_is_exact_and_does_not_restore_deleted_defaults(self):
        for platform in ["claude", "deepseek", "custom:test", "gemini"]:
            params = {"max_tokens": 1024} if platform == "claude" else {}
            result = generation_params(platform, "test", 4000, 0.9, {"effort": "high"},
                                       custom={"top_p": 0.9}, override=params)
            self.assertEqual(result, params)
            self.assertNotIn("temperature", result)
            result["changed"] = True
            self.assertNotIn("changed", params)

    def test_claude_thinking_normalizes_temperature_and_rejects_invalid_override(self):
        result = generation_params(
            "claude", "claude-sonnet-4-5", 4000, 0.7,
            {"type": "enabled", "budget_tokens": 1024},
        )
        self.assertEqual(result["temperature"], 1)
        with self.assertRaisesRegex(ValueError, "temperature 只能设为 1"):
            validate_request_params({
                "max_tokens": 4000,
                "temperature": 0.7,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
            }, "claude")

    def test_invalid_json_config_rejected(self):
        for value in [None, [], {"max_tokens": True}, {"max_tokens": 1.5}, {"temperature": "0.5"},
                      {"temperature": float("nan")}, {"thinking": []}, {"reasoning_effort": False},
                      {"stream": False}, {"api_key": "fake"}]:
            with self.assertRaises(ValueError):
                validate_request_params(value, "generic")
        with self.assertRaises(ValueError): validate_request_params({}, "claude")
        with self.assertRaises(ValueError): validate_request_params({"unknown_field": 1}, "gemini")

    def test_preview_service_is_read_only_and_does_not_expose_secrets(self):
        original = {"api_key": "never-return-this", "model_configs": {"test": {"temperature": 0.7}}}
        data = deepcopy(original)
        app = SimpleNamespace(lock=threading.RLock(), config=SimpleNamespace(data=data))
        service = ConfigService(app)
        draft = {"max_tokens": 2048, "temperature": 0.333, "stop_sequences": ["END"]}
        result = service.preview_model_request({"model": "test", "platform": "claude", "model_config": {"request_params": draft}})
        self.assertEqual(result["params"], draft)
        self.assertEqual(data, original)
        self.assertNotIn("never-return-this", json.dumps(result))
        self.assertIn("error", service.preview_model_request({"model": "test", "platform": "claude", "model_config": {"request_params": {}}}))

    def test_gemini_preview_matches_sdk_normalized_types(self):
        value = validate_request_params({"top_p": "0.8", "thinking_config": {"thinking_level": "high"}}, "gemini")
        self.assertEqual(value["top_p"], 0.8)
        self.assertEqual(value["thinking_config"]["thinking_level"], "HIGH")

    def test_preview_http_route(self):
        body = {"model": "test", "platform": "claude"}
        api = SimpleNamespace(preview_model_request=MagicMock(return_value={"params": {"max_tokens": 1024}}))
        handler = SimpleNamespace(read_json_body=lambda: body, server=SimpleNamespace(api=api), send_json_response=MagicMock())
        HttpApiRouter(handler).dispatch_post("/api/preview_model_request")
        api.preview_model_request.assert_called_once_with(body)
        handler.send_json_response.assert_called_once_with({"params": {"max_tokens": 1024}})

    def test_editor_params_match_anthropic_and_openai_wire_bodies(self):
        from claude_chat.clients.claude import stream_claude_response_native
        from claude_chat.clients.deepseek import stream_deepseek_response
        for platform, client in [("claude", stream_claude_response_native), ("deepseek", stream_deepseek_response)]:
            final = {"max_tokens": 1024, "top_p": 0.85, "metadata": {"user_id": "test"}}
            if platform == "claude": final["thinking"] = {"type": "enabled", "budget_tokens": 512}
            else: final["reasoning_effort"] = "medium"
            config = {"model_configs": {"test": {"request_params": final}}}
            preview = preview_generation_params(platform, "test", config)
            sent = []
            transport_module = httpx2 if platform == "claude" else httpx
            def response(request):
                sent.append(json.loads(request.content))
                if platform == "claude":
                    return httpx2.Response(
                        400,
                        json={"type": "error", "error": {"type": "invalid_request_error", "message": "test"}},
                    )
                return transport_module.Response(200, headers={"Content-Type": "text/event-stream"}, text="data: [DONE]\n\n")
            with transport_module.Client(transport=transport_module.MockTransport(response)) as http_client:
                builder = "build_anthropic_http_client" if platform == "claude" else "build_http_client"
                with patch(f"claude_chat.clients.{platform}.{builder}", return_value=http_client):
                    kwargs = dict(api_key="test", proxy_mode="none", proxy_url="", messages=[], model="test",
                                  max_tokens=4000, temperature=0.7, streaming_queue=queue.Queue(), request_params=final,
                                  custom_params={"temperature": 0.9})
                    if platform == "claude": kwargs["thinking_config"] = None
                    else: kwargs["api_url"] = "https://example.test/v1"
                    client(**kwargs)
            actual = {k: v for k, v in sent[0].items() if k not in ("model", "messages", "stream", "stream_options")}
            self.assertEqual(actual, preview)

    def test_gemini_editor_params_override_image_and_thinking_defaults(self):
        from claude_chat.clients.gemini import stream_gemini_response
        final = {"max_output_tokens": 2048, "top_p": 0.85, "thinking_config": {"thinking_budget": 512, "include_thoughts": True}}
        with patch("google.genai.Client") as client:
            client.return_value.models.generate_content_stream.return_value = iter([])
            stream_gemini_response("test", "", "none", "", [], "gemini-image-test", 4096, 0.7, True, 1024, "high",
                                   queue.Queue(), request_params=final)
            args = client.return_value.models.generate_content_stream.call_args.kwargs["config"]
            self.assertEqual(args.max_output_tokens, 2048)
            self.assertEqual(args.thinking_config.thinking_budget, 512)
            self.assertTrue(args.thinking_config.include_thoughts)
            self.assertIsNone(args.temperature)
            self.assertIsNone(args.image_config)


if __name__ == "__main__": unittest.main()

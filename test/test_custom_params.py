"""Offline checks for model overrides and the actual SDK request bodies."""
import json
import queue
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import httpx2

from claude_chat.custom_params import validate_custom_params
from claude_chat.platform_params import PlatformParamMapper
from claude_chat.services.config_service import ConfigService


class CustomParameterTests(unittest.TestCase):
    def test_validation_and_independent_model_values(self):
        cfg = {"model_configs": {"one": {"custom_params": {"stop": ["END"], "top_p": 0.8}},
                                  "two": {"custom_params": {"top_p": 0.2}}}}
        first = PlatformParamMapper.map_params("claude", cfg, "one")["custom_params"]
        first["stop"].append("MUTATED")
        self.assertEqual(cfg["model_configs"]["one"]["custom_params"]["stop"], ["END"])
        for platform in ["claude", "deepseek", "gemini", "custom:test"]:
            self.assertEqual(PlatformParamMapper.map_params(platform, cfg, "two")["custom_params"], {"top_p": 0.2})
            self.assertEqual(PlatformParamMapper.map_params(platform, cfg, "missing")["custom_params"], {})
        for invalid in [[], {"stream": False}, {"http_options": {}}, {"bad-name": 1}, {"top_p": float("inf")}]:
            with self.assertRaises(ValueError):
                validate_custom_params(invalid)

    def test_save_read_delete_and_reject_before_write(self):
        data = {}
        def save(updates):
            data.update(json.loads(json.dumps(updates)))
            return True
        config = SimpleNamespace(data=data, get=lambda k, default=None: data.get(k, default), set_many=MagicMock(side_effect=save))
        app = SimpleNamespace(config=config, lock=threading.RLock(), current_conv=None, _refresh_models_async=MagicMock())
        service = ConfigService(app)
        self.assertTrue(service.save_config({"model_configs": {"test": {"custom_params": {"top_p": 0.8, "flag": False}}}}))
        self.assertEqual(service.get_config()["model_configs"]["test"]["custom_params"], {"top_p": 0.8, "flag": False})
        self.assertTrue(service.save_config({"model_configs": {"test": {"custom_params": {}}}}))
        self.assertEqual(service.get_config()["model_configs"]["test"]["custom_params"], {})
        count = config.set_many.call_count
        self.assertFalse(service.save_config({"model_configs": {"test": {"custom_params": {"stream": False}}}}))
        self.assertEqual(config.set_many.call_count, count)

    def test_dispatcher_preserves_custom_parameters_for_every_platform(self):
        from claude_chat.clients.dispatcher import stream_claude_response
        for platform, target in [("claude", "stream_claude_response_native"), ("deepseek", "stream_deepseek_response"),
                                 ("custom:test", "stream_deepseek_response"), ("gemini", "stream_gemini_response")]:
            with patch("claude_chat.clients.dispatcher." + target) as client:
                stream_claude_response("test", "none", "", [], "model", 512, 0.7, None, queue.Queue(),
                                       active_platform=platform, custom_params={"top_p": 0.8})
                self.assertEqual(client.call_args.kwargs["custom_params"], {"top_p": 0.8})

    def test_openai_and_anthropic_wire_payload(self):
        from claude_chat.clients.deepseek import stream_deepseek_response
        from claude_chat.clients.claude import stream_claude_response_native
        for module, client in [("deepseek", stream_deepseek_response), ("claude", stream_claude_response_native)]:
            requests = []
            transport_module = httpx2 if module == "claude" else httpx
            def respond(request):
                requests.append(json.loads(request.content))
                if module == "claude":
                    return httpx2.Response(
                        400,
                        json={"type": "error", "error": {"type": "invalid_request_error", "message": "test"}},
                    )
                return transport_module.Response(200, headers={"Content-Type": "text/event-stream"}, text="data: [DONE]\n\n")
            with transport_module.Client(transport=transport_module.MockTransport(respond)) as http_client:
                builder = "build_anthropic_http_client" if module == "claude" else "build_http_client"
                with patch("claude_chat.clients." + module + "." + builder, return_value=http_client):
                    kwargs = dict(api_key="test", proxy_mode="none", proxy_url="", messages=[{"role": "user", "content": "hi"}],
                                  model="test", max_tokens=512, temperature=0.7, streaming_queue=queue.Queue(),
                                  custom_params={"top_p": 0.8, "temperature": 0.3, "metadata": {"user_id": "test"}})
                    if module == "deepseek": kwargs["api_url"] = "https://example.test/v1"
                    else: kwargs["thinking_config"] = None
                    client(**kwargs)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]["top_p"], 0.8)
            self.assertEqual(requests[0]["temperature"], 0.3)
            self.assertEqual(requests[0]["metadata"], {"user_id": "test"})
            self.assertTrue(requests[0]["stream"])
            self.assertNotIn("extra_body", requests[0])

    def test_gemini_generation_config(self):
        from claude_chat.clients.gemini import stream_gemini_response
        with patch("google.genai.Client") as factory:
            factory.return_value.models.generate_content_stream.return_value = iter([])
            stream_gemini_response("test", "", "none", "", [{"role": "user", "content": "hi"}], "gemini-test", 512,
                                   0.7, False, 1024, "high", queue.Queue(), custom_params={"top_p": 0.8, "top_k": 20})
            args = factory.return_value.models.generate_content_stream.call_args.kwargs
            self.assertEqual(args["config"].top_p, 0.8)
            self.assertEqual(args["config"].top_k, 20)


if __name__ == "__main__":
    unittest.main()

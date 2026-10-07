"""Offline regression checks for the streaming and long-conversation refactor."""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from claude_chat.config import DEFAULT_MAX_TOKENS
from claude_chat.platform_params import PlatformParamMapper


class ArchitectureRefactorTests(unittest.TestCase):
    def test_builtin_platforms_use_reasonable_default_output_limit(self):
        self.assertEqual(DEFAULT_MAX_TOKENS, 16384)
        for platform in ("claude", "deepseek", "gemini"):
            params = PlatformParamMapper.map_params(platform, {}, "model-without-override")
            self.assertEqual(params["max_tokens"], DEFAULT_MAX_TOKENS)

    def test_platform_mapper_keeps_model_overrides(self):
        params = PlatformParamMapper.map_params(
            "claude",
            {
                "api_key": "secret",
                "max_tokens": 100,
                "temperature": 0.7,
                "thinking_enabled": False,
                "model_configs": {
                    "model-a": {
                        "max_tokens": 900,
                        "temperature": 0.2,
                        "thinking_enabled": True,
                        "thinking_type": "adaptive",
                        "thinking_level": "medium",
                    }
                },
            },
            "model-a",
        )
        self.assertEqual(params["max_tokens"], 900)
        self.assertEqual(params["temperature"], 0.2)
        self.assertEqual(params["thinking_config"], {"type": "adaptive"})
        self.assertEqual(params["output_config"], {"effort": "medium"})

    def test_frontend_modules_load_in_dependency_order(self):
        html = ROOT.joinpath("claude_chat", "ui", "index.html").read_text(encoding="utf-8")
        self.assertLess(html.index('src="state.js"'), html.index('src="stream_protocol.js"'))
        self.assertLess(html.index('src="stream_protocol.js"'), html.index('src="api.js"'))
        self.assertLess(html.index('src="chat.js"'), html.index('src="conversation_render.js"'))
        self.assertLess(html.index('src="conversation_render.js"'), html.index('src="main.js"'))
        self.assertLess(html.index('src="sandbox_settings.js"'), html.index('src="settings.js"'))

    def test_long_conversation_render_is_batched(self):
        source = ROOT.joinpath("claude_chat", "ui", "conversation_render.js").read_text(encoding="utf-8")
        self.assertIn("CONVERSATION_RENDER_BATCH_SIZE = 60", source)
        self.assertIn("document.createDocumentFragment()", source)
        self.assertIn("loadOlderConversationMessages", source)

    def test_streaming_boolean_is_derived_from_typed_state(self):
        chat = ROOT.joinpath("claude_chat", "ui", "chat.js").read_text(encoding="utf-8")
        protocol = ROOT.joinpath("claude_chat", "ui", "stream_protocol.js").read_text(encoding="utf-8")
        self.assertNotIn("isStreaming =", chat)
        self.assertIn("ACTIVE_STREAM_TASK_STATES.has(status)", protocol)
        self.assertIn("window.onStreamEvent", protocol)


if __name__ == "__main__":
    unittest.main()

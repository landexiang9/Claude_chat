"""Offline regression tests for the bugs fixed during the project audit."""

import os
import sys
import threading
import copy
from unittest.mock import patch

import httpx

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_chat.api_bridge import PlatformParamMapper, WebAPI
from claude_chat.clients.base import extract_final_response_text
from claude_chat.clients.claude import _supports_temperature
from claude_chat.config import ConfigManager
from claude_chat.search import _safe_get


class DummyResponse:
    def __init__(self, url, status_code=200, headers=None):
        self.url = httpx.URL(url)
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


class RedirectClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append((url, dict(headers or {}), timeout))
        return self.responses.pop(0)


class MemoryConfig:
    def __init__(self, data):
        self.data = dict(data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set_many(self, updates):
        self.data.update(updates)
        return True


class MemoryConversationManager:
    def __init__(self, conversations=None):
        self.conversations = copy.deepcopy(conversations or {})
        self.saved_metadata = None

    def load_conversation(self, conv_id):
        value = self.conversations.get(conv_id)
        return copy.deepcopy(value) if value else None

    def save_conversation(self, conversation):
        self.conversations[conversation["id"]] = copy.deepcopy(conversation)

    def save_conversation_metadata(self, conversation):
        self.saved_metadata = copy.deepcopy(conversation)
        self.save_conversation(conversation)


class DummyApp:
    def __init__(self, config, manager, current_conv=None):
        self.lock = threading.RLock()
        self.config = config
        self.conv_manager = manager
        self.current_conv = current_conv
        self.refresh_count = 0
        self.window = None
        self.available_models = []

    def _refresh_models_async(self):
        self.refresh_count += 1


def main():
    custom_config = {
        "custom_providers": [{"id": "demo", "api_url": "https://example.com/v1", "max_tokens": 100}],
        "custom_demo_api_key": "secret",
        "model_configs": {
            "demo-model": {
                "max_tokens": 777,
                "temperature": 0.2,
                "thinking_enabled": True,
                "thinking_level": "medium",
            }
        },
    }
    mapped = PlatformParamMapper.map_params("custom:demo", custom_config, model_id="demo-model")
    assert mapped["max_tokens"] == 777
    assert mapped["temperature"] == 0.2
    assert mapped["thinking_config"] == {"effort": "medium"}

    assert _supports_temperature("claude-opus-4-6")
    assert not _supports_temperature("claude-opus-4-8")
    assert not _supports_temperature("claude-sonnet-5-0")

    done = {"content_blocks": [{"type": "text", "text": "final"}]}
    assert extract_final_response_text(done, "preamblefinal") == "final"

    config = ConfigManager.__new__(ConfigManager)
    config._lock = threading.RLock()
    config.data = {"model": "old"}
    config.save = lambda: False
    assert config.set_many({"model": "new", "extra": True}) is False
    assert config.data == {"model": "old"}

    source = {
        "id": "source",
        "title": "Source",
        "model": "old-model",
        "platform": "claude",
        "input_tokens": 123,
        "output_tokens": 45,
        "messages": [
            {"role": "user", "content": "one"},
            {"role": "assistant", "content": "two"},
            {"role": "user", "content": "three"},
        ],
    }
    manager = MemoryConversationManager({"source": source})
    app = DummyApp(
        MemoryConfig({"active_platform": "claude", "model": "old-model", "model_configs": {}}),
        manager,
        current_conv=copy.deepcopy(source),
    )
    api = WebAPI(app)
    assert api.save_config({"active_platform": "gemini", "model": "gemini-test"}) is True
    assert app.current_conv["platform"] == "gemini"
    assert app.current_conv["model"] == "gemini-test"
    assert app.refresh_count == 0
    assert api.save_config({"gemini_api_url": "https://example.com/v1"}) is True
    assert app.refresh_count == 1
    # 空 security_token 必须被拒绝,否则服务端所有 /api 请求 401 锁死 UI
    assert api.save_config({"security_token": ""}) is True
    assert app.config.get("security_token", "missing") == "missing"
    assert app.refresh_count == 1
    assert api.save_config({"security_token": "   "}) is True
    assert app.config.get("security_token", "missing") == "missing"
    assert api.save_config({"security_token": "new-token"}) is True
    assert app.config.get("security_token") == "new-token"
    with patch(
        "claude_chat.services.model_service.fetch_available_models",
        return_value=[{"id": "deepseek-chat"}],
    ) as fetch:
        assert api.fetch_models("deepseek") == [{"id": "deepseek-chat"}]
        assert fetch.call_args.kwargs["active_platform"] == "deepseek"
        assert app.config.get("active_platform") == "gemini"

    # A thinking_config can be present while output_config is explicitly None
    # (custom providers and fixed-budget Claude thinking). Saving must not crash.
    custom_manager = MemoryConversationManager({"custom": copy.deepcopy(source)})
    custom_current = copy.deepcopy(source)
    custom_current["id"] = "custom"
    custom_app = DummyApp(MemoryConfig(custom_config), custom_manager, custom_current)
    custom_api = WebAPI(custom_app)
    assert custom_api.save_config({"active_platform": "custom:demo", "model": "demo-model"}) is True
    assert custom_app.current_conv["thinking"] == {"effort": "medium"}
    assert custom_api.save_config({"code_sandbox_timeout": 9999}) is True
    assert custom_app.config.get("code_sandbox_timeout") == 600

    branch = api.branch_conversation("source", 1)
    assert branch["platform"] == "gemini"
    assert branch["input_tokens"] == 0 and branch["output_tokens"] == 0
    assert len(branch["messages"]) == 2

    chat_js = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "claude_chat", "ui", "chat.js")
    with open(chat_js, "r", encoding="utf-8") as handle:
        select_source = handle.read().split("async function selectConversation", 1)[1].split(
            "async function startNewChat", 1
        )[0]
    assert select_source.index("renderConversationMessages(messages)") < select_source.index(
        "apiBridge.fetch_models(targetPlatform)"
    )
    assert "apiBridge.save_config" not in select_source

    main_js = os.path.join(os.path.dirname(chat_js), "main.js")
    with open(main_js, "r", encoding="utf-8") as handle:
        init_source = handle.read().split("async function initApp", 1)[1].split(
            "window.addEventListener", 1
        )[0]
    assert "fetch_models" not in init_source
    assert init_source.index("loadConversations()") < init_source.index("selectConversation(")

    ui_js = os.path.join(os.path.dirname(chat_js), "ui.js")
    with open(ui_js, "r", encoding="utf-8") as handle:
        ui_source = handle.read()
        artifact_source = ui_source.split("const ARTIFACT_IFRAME_PERMISSIONS", 1)[1].split(
            "// --- Image Preview", 1
        )[0]
    assert 'interactive ? "allow-scripts allow-forms allow-modals allow-popups" : ""' in artifact_source
    assert 'iframe.referrerPolicy = "no-referrer"' in artifact_source
    assert '"camera \'none\'"' in artifact_source
    assert "artifactsPreviewContainer.replaceChildren()" in artifact_source
    assert "安全净化组件未加载，已拒绝预览 SVG" in artifact_source
    assert "artifactsPreviewContainer.innerHTML" not in artifact_source

    # 存储型 XSS 修复:代码块语言标记必须先转义再拼入 innerHTML
    assert "escapeHtml(String(lang).toUpperCase())" in ui_source
    assert "${lang.toUpperCase()}" not in ui_source

    # 前端 settings 修复:空 security_token 不覆盖原值
    settings_js = os.path.join(os.path.dirname(chat_js), "settings.js")
    with open(settings_js, "r", encoding="utf-8") as handle:
        settings_source = handle.read()
    assert "delete config.security_token" in settings_source
    assert "config.security_token = serverTokenInput.value.trim();" not in settings_source

    # api.js 修复:流 EOF 时若未收到终止事件必须合成 error,复位 isStreaming
    api_js = os.path.join(os.path.dirname(chat_js), "api.js")
    with open(api_js, "r", encoding="utf-8") as handle:
        api_source = handle.read()
    assert "sawTerminal" in api_source
    assert "生成流意外中断，请重试。" in api_source

    # server.py 修复:终止事件必须"先持久化+复位状态,再写回客户端",
    # 且超时/异常路径必须向客户端写合成 error 事件
    server_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "claude_chat", "server.py")
    with open(server_py, "r", encoding="utf-8") as handle:
        server_source = handle.read()
    stream_src = server_source.split("def handle_streaming_generation", 1)[1].split(
        "def handle_console_stream", 1
    )[0]
    done_branch = stream_src.split('elif msg_type == "aborted":', 1)[0]
    assert done_branch.index("add_assistant_message_and_update_tokens") < done_branch.index(
        "set_streaming_done(StreamTaskState.COMPLETED, task)"
    )
    assert done_branch.index("set_streaming_done(StreamTaskState.COMPLETED, task)") < done_branch.index(
        "self._write_stream_line(event)"
    )
    timeout_branch = stream_src.split("except queue.Empty:", 1)[1].split("except Exception as e:", 1)[0]
    assert "_emit_stream_error(q" in timeout_branch
    assert "_emit_stream_error(q" in stream_src.split("except Exception as e:", 1)[1].split("def _emit_stream_error", 1)[0]

    blocked = RedirectClient([
        DummyResponse("https://8.8.8.8/start", 302, {"location": "http://127.0.0.1/private"})
    ])
    try:
        _safe_get(blocked, "https://8.8.8.8/start")
        raise AssertionError("private redirect should have been rejected")
    except ValueError:
        pass
    assert len(blocked.calls) == 1

    cross_origin = RedirectClient([
        DummyResponse("https://8.8.8.8/start", 302, {"location": "https://1.1.1.1/final"}),
        DummyResponse("https://1.1.1.1/final"),
    ])
    _safe_get(cross_origin, "https://8.8.8.8/start", headers={"Authorization": "Bearer secret"})
    assert "Authorization" not in cross_origin.calls[1][1]

    print("fixed regression tests passed")


if __name__ == "__main__":
    main()

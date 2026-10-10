"""Real isolated SQLite and mocked providers: titles never use personal data or paid APIs."""

import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import claude_chat.db as database
from claude_chat.api_bridge import WebAPI
from claude_chat.app import ClaudeChatApp
from claude_chat.http_router import HttpApiRouter
from claude_chat.services.conversation_titles import normalize_title, title_context
from claude_chat.stream_protocol import StreamTaskState


@pytest.fixture
def setup_titles(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "titles.db")
    monkeypatch.setattr(database, "CONVERSATIONS_DIR", tmp_path / "conversations")
    manager = database.DatabaseManager()
    config = {
        "active_platform": "claude",
        "model": "global-model",
        "api_key": "offline-claude",
        "deepseek_api_key": "offline-deepseek",
        "gemini_api_key": "offline-gemini",
        "deepseek_api_url": "https://deepseek.example.test",
        "proxy_mode": "none",
        "custom_providers": [
            {"id": "test", "name": "Test", "api_url": "https://custom.example.test", "provider_adapter": "responses"}
        ],
        "custom_test_api_key": "offline-custom",
    }
    app = SimpleNamespace(
        lock=threading.RLock(),
        conv_manager=manager,
        current_conv=None,
        config=SimpleNamespace(data=config),
    )
    api = WebAPI(app)
    conv = manager.new_conversation()
    conv.update(platform="deepseek", model="conversation-model")
    manager.save_conversation_metadata(conv)
    manager.add_message(conv["id"], "user", "如何给聊天工具增加标题？")
    manager.add_assistant_message_and_update_tokens(
        conv["id"], "可以添加独立的标题服务。", input_tokens=7, output_tokens=9
    )
    app.current_conv = manager.load_conversation(conv["id"])
    calls = []

    def stream(*args, **kwargs):
        calls.append((args, kwargs))
        args[8].put(("text", '"聊天工具的标题功能"'))
        args[8].put(("done", {"input_tokens": 3, "output_tokens": 4}))

    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", stream)
    yield api, app, conv["id"], calls
    for job in getattr(app, "_conversation_title_manager", SimpleNamespace(jobs={})).jobs.values():
        job.abort.set()


def wait_title(api, conv_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        result = api.conversation_title_operation(conv_id)
        if result.get("status") != "generating":
            return result
        time.sleep(0.01)
    raise AssertionError("Title worker did not finish")


def test_automatic_once_uses_conversation_model_and_preserves_messages_and_tokens(setup_titles):
    api, app, conv_id, calls = setup_titles
    before = app.conv_manager.load_conversation(conv_id)
    assert api.schedule_conversation_title(conv_id)["success"]
    assert wait_title(api, conv_id)["title"] == "聊天工具的标题功能"
    api.schedule_conversation_title(conv_id)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[4] == "conversation-model"
    assert args[0] == "offline-deepseek"
    assert kwargs["active_platform"] == "deepseek"
    assert kwargs["deepseek_api_url"] == "https://deepseek.example.test"
    assert not kwargs["enable_search"] and not kwargs["enable_web_fetch"]
    assert not kwargs["gemini_enable_code_sandbox"] and not kwargs["file_upload_enabled"]
    assert "temperature" not in kwargs["request_params"]
    assert kwargs["request_params"]["thinking"] == {"type": "disabled"}
    after = app.conv_manager.load_conversation(conv_id)
    assert after["messages"] == before["messages"]
    assert (after["input_tokens"], after["output_tokens"]) == (7, 9)
    assert after["updated_at"] == before["updated_at"]
    assert app.current_conv["title"] == after["title"]


@pytest.mark.parametrize(
    "platform,model,key",
    [
        ("claude", "claude-title", "offline-claude"),
        ("gemini", "gemini-title", "offline-gemini"),
        ("custom:test", "custom-title", "offline-custom"),
    ],
)
def test_provider_selection_and_protocol(setup_titles, platform, model, key):
    api, app, conv_id, calls = setup_titles
    conv = app.conv_manager.load_conversation(conv_id)
    conv.update(platform=platform, model=model)
    app.conv_manager.save_conversation_metadata(conv)
    assert api.conversation_title_operation(conv_id, "generate")["success"]
    assert wait_title(api, conv_id)["status"] == "completed"
    args, kwargs = calls[-1]
    assert args[4] == model and args[0] == key
    assert kwargs["active_platform"] == platform
    if platform == "custom:test":
        assert kwargs["provider_adapter"] == "responses"
        assert kwargs["custom_api_url"] == "https://custom.example.test"
        assert kwargs["request_params"]["max_output_tokens"] == 2048


@pytest.mark.parametrize(
    "platform,adapter,model,expected",
    [
        ("claude", "local", "claude-sonnet-4-6", {"thinking": {"type": "disabled"}}),
        ("deepseek", "local", "deepseek-v4-pro", {"thinking": {"type": "disabled"}}),
        ("deepseek", "responses", "deepseek-v4-pro", {"reasoning": {"effort": "none"}}),
        ("gemini", "local", "gemini-2.5-flash", {"thinking_config": {"thinking_budget": 0, "include_thoughts": False}}),
        ("custom:test", "anthropic", "claude-sonnet-4-6", {"thinking": {"type": "disabled"}}),
        (
            "custom:test",
            "gemini",
            "gemini-2.5-flash",
            {"thinking_config": {"thinking_budget": 0, "include_thoughts": False}},
        ),
        ("custom:test", "responses", "gpt-5.2", {"reasoning": {"effort": "none"}}),
        ("custom:test", "openrouter", "qwen/qwen3", {"reasoning": {"effort": "none", "enabled": False}}),
        ("custom:test", "local", "gpt-5.2", {"reasoning_effort": "none"}),
    ],
)
def test_title_requests_explicitly_disable_thinking_despite_chat_settings(
    setup_titles, platform, adapter, model, expected
):
    from copy import deepcopy

    api, app, conv_id, calls = setup_titles
    app.config.data.update(thinking_enabled=True, deepseek_thinking_enabled=True, gemini_thinking_enabled=True)
    app.config.data["deepseek_use_responses"] = adapter == "responses"
    app.config.data["custom_providers"][0]["provider_adapter"] = adapter
    app.config.data["model_configs"] = {
        model: {
            "thinking_enabled": True,
            "thinking_budget": 16000,
            "request_params": {"reasoning": {"effort": "high"}, "thinking": {"type": "adaptive"}},
            "custom_params": {"reasoning_effort": "high"},
        }
    }
    before = deepcopy(app.config.data)
    conv = app.conv_manager.load_conversation(conv_id)
    conv.update(platform=platform, model=model)
    app.conv_manager.save_conversation_metadata(conv)
    api.conversation_title_operation(conv_id, "generate")
    assert wait_title(api, conv_id)["status"] == "completed"
    args, kwargs = calls[-1]
    assert args[7] is None and kwargs["thinking_enabled"] is False
    for key, value in expected.items():
        assert kwargs["request_params"][key] == value
    assert "custom_params" not in kwargs
    assert app.config.data == before  # Ordinary chat thinking and saved JSON stay unchanged.
    if platform == "gemini" or adapter == "gemini":
        from google.genai import types

        config = types.GenerateContentConfig(**kwargs["request_params"])
        assert config.thinking_config.thinking_budget == 0
        assert config.thinking_config.include_thoughts is False


@pytest.mark.parametrize(
    "platform,adapter,model",
    [
        ("gemini", "local", "gemini-2.5-pro"),
        ("gemini", "local", "gemini-3.1-pro-preview"),
        ("gemini", "local", "gemini-3-flash-preview"),
        ("custom:test", "gemini", "gemini-2.5-pro-preview"),
        ("custom:test", "openrouter", "google/gemini-3.5-flash"),
    ],
)
def test_mandatory_thinking_models_keep_title_without_model_call(setup_titles, platform, adapter, model):
    api, app, conv_id, calls = setup_titles
    app.config.data["custom_providers"][0]["provider_adapter"] = adapter
    conv = app.conv_manager.load_conversation(conv_id)
    conv.update(platform=platform, model=model)
    app.conv_manager.save_conversation_metadata(conv)
    api.conversation_title_operation(conv_id, "generate")
    result = wait_title(api, conv_id)
    assert result["status"] == "error" and "不支持完全关闭思考" in result["error"]
    assert result["title"] == conv["title"] and not calls


def test_auto_uses_actual_chat_model_snapshot(setup_titles):
    api, _, conv_id, calls = setup_titles
    api.schedule_conversation_title(conv_id, ("gemini", "first-round-model"))
    assert wait_title(api, conv_id)["status"] == "completed"
    assert calls[0][0][4] == "first-round-model"
    assert calls[0][1]["active_platform"] == "gemini"


def test_manual_title_protected_from_auto_even_if_default_text(setup_titles):
    api, app, conv_id, calls = setup_titles
    assert api.conversation_title_operation(conv_id, "edit", " 新对话 ")["title"] == "新对话"
    app.conv_manager.add_assistant_message_and_update_tokens(conv_id, "回答不应改标题")
    api.schedule_conversation_title(conv_id)
    assert not calls
    assert app.conv_manager.load_conversation(conv_id)["title"] == "新对话"
    assert api.conversation_title_operation(conv_id, "generate")["success"]
    assert wait_title(api, conv_id)["title_source"] == "ai"


@pytest.mark.parametrize("title", ["", "   ", "a" * 101, None, 123, ["bad"]])
def test_invalid_manual_titles_rejected(setup_titles, title):
    api, app, conv_id, _ = setup_titles
    before = app.conv_manager.get_conversation_title(conv_id)
    assert not api.conversation_title_operation(conv_id, "edit", title)["success"]
    assert app.conv_manager.get_conversation_title(conv_id) == before


def test_background_result_cannot_overwrite_manual_edit_or_newer_job(setup_titles, monkeypatch):
    api, app, conv_id, _ = setup_titles
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    calls = []

    def delayed(*args, **kwargs):
        calls.append(args)
        entered.set()
        release.wait(2)
        args[8].put(("text", "过期标题"))
        args[8].put(("done", {}))
        exited.set()

    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", delayed)
    api.conversation_title_operation(conv_id, "generate")
    assert entered.wait(2)
    api.conversation_title_operation(conv_id, "generate")  # duplicate request reuses the running job
    assert len(calls) == 1
    edited = api.conversation_title_operation(conv_id, "edit", "我的标题")
    assert edited["title_source"] == "manual"

    def newer(*args, **kwargs):
        args[8].put(("text", "新任务标题"))
        args[8].put(("done", {}))

    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", newer)
    api.conversation_title_operation(conv_id, "generate")
    assert wait_title(api, conv_id)["title"] == "新任务标题"
    release.set()
    assert exited.wait(2)
    assert app.conv_manager.get_conversation_title(conv_id)["title"] == "新任务标题"


def test_deleted_conversation_cannot_be_recreated_by_worker(setup_titles, monkeypatch):
    api, app, conv_id, _ = setup_titles
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    def delayed(*args, **kwargs):
        entered.set()
        release.wait(2)
        args[8].put(("text", "删除后的标题"))
        args[8].put(("done", {}))
        exited.set()

    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", delayed)
    api.conversation_title_operation(conv_id, "generate")
    assert entered.wait(2)
    app.conv_manager.delete_conversation(conv_id)
    release.set()
    assert exited.wait(2)
    assert not api.conversation_title_operation(conv_id)["success"]
    assert app.conv_manager.load_conversation(conv_id) is None


def test_timeout_closes_stream_and_allows_retry_without_stale_write(setup_titles, monkeypatch):
    api, app, conv_id, _ = setup_titles
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()
    timers, stream = [], Mock()

    class ControlledTimer:
        def __init__(self, seconds, callback):
            self.callback = callback
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            pass

    def delayed(*args, **kwargs):
        kwargs["on_stream_created"](stream)
        entered.set()
        release.wait(2)
        args[8].put(("text", "过期超时结果"))
        args[8].put(("done", {}))
        exited.set()

    monkeypatch.setattr("claude_chat.services.conversation_titles.threading.Timer", ControlledTimer)
    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", delayed)
    api.conversation_title_operation(conv_id, "generate")
    assert entered.wait(2)
    timers[0].callback()
    assert api.conversation_title_operation(conv_id)["status"] == "error"
    assert "超时" in api.conversation_title_operation(conv_id)["error"]
    stream.close.assert_called_once()

    def retry(*args, **kwargs):
        args[8].put(("text", "重试标题"))
        args[8].put(("done", {}))

    monkeypatch.setattr("claude_chat.services.conversation_service.stream_claude_response", retry)
    api.conversation_title_operation(conv_id, "generate")
    assert wait_title(api, conv_id)["title"] == "重试标题"
    release.set()
    assert exited.wait(2)
    assert app.conv_manager.get_conversation_title(conv_id)["title"] == "重试标题"


def test_error_keeps_previous_title_and_manual_retry_works(setup_titles, monkeypatch):
    api, app, conv_id, calls = setup_titles
    before = app.conv_manager.get_conversation_title(conv_id)["title"]

    def failing(*args, **kwargs):
        args[8].put(("error", "bad key offline-deepseek"))

    original = __import__("claude_chat.services.conversation_service", fromlist=["stream_claude_response"])
    good = original.stream_claude_response
    monkeypatch.setattr(original, "stream_claude_response", failing)
    api.schedule_conversation_title(conv_id)
    result = wait_title(api, conv_id)
    assert result["status"] == "error" and result["title"] == before
    assert "offline-deepseek" not in result["error"]
    api.schedule_conversation_title(conv_id)
    monkeypatch.setattr(original, "stream_claude_response", good)
    api.conversation_title_operation(conv_id, "generate")
    assert wait_title(api, conv_id)["status"] == "completed"
    assert len(calls) == 1


def test_old_metadata_and_full_saves_do_not_overwrite_title(setup_titles):
    api, app, conv_id, _ = setup_titles
    stale = app.conv_manager.load_conversation(conv_id)
    api.conversation_title_operation(conv_id, "edit", "永久保存的标题")
    app.conv_manager.save_conversation_metadata(stale)
    app.conv_manager.save_conversation(stale)
    reloaded = database.DatabaseManager().load_conversation(conv_id)
    assert reloaded["title"] == "永久保存的标题" and reloaded["title_source"] == "manual"
    assert reloaded["messages"] == stale["messages"]


def test_empty_and_missing_conversations_do_not_call_model(setup_titles):
    api, app, _, calls = setup_titles
    empty = app.conv_manager.new_conversation()["id"]
    for id_value in (empty, "missing", None, [], "", "a" * 129):
        assert not api.conversation_title_operation(id_value, "generate")["success"]
    assert not calls


def test_bounded_context_excludes_reasoning_tools_and_attachment_contents():
    messages = [
        {"role": "user", "content": "首轮问题"},
        {"role": "assistant", "content": "首轮回复", "thinking": "secret reasoning"},
        {"role": "user", "content": [{"type": "tool_result", "content": "secret tool"}]},
        {
            "role": "user",
            "content": [{"type": "text", "text": "private file body", "_attachment": {"name": "notes.pdf"}}],
        },
        {"role": "assistant", "content": "aborted response", "aborted": True},
    ]
    context = title_context(messages)
    assert "首轮问题" in context and "notes.pdf" in context
    assert "secret" not in context and "private file body" not in context and "aborted" not in context
    assert "notes.pdf" not in title_context(messages, automatic=True)
    assert len(title_context([{"role": "user", "content": "x" * 20000}] * 200)) <= 12000
    assert normalize_title("标题：“简短标题”\n解释内容", generated=True) == "简短标题"


def test_existing_database_migration_preserves_legacy_titles(tmp_path, monkeypatch):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE conversations(id TEXT PRIMARY KEY,title TEXT)")
        conn.execute("INSERT INTO conversations VALUES('old','历史标题')")
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "CONVERSATIONS_DIR", tmp_path / "missing")
    database.DatabaseManager()
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT title,title_source,title_version FROM conversations").fetchone()
        assert row == ("历史标题", "legacy", 0)


def test_completion_hook_schedules_only_successful_chats(setup_titles, monkeypatch):
    api, app, conv_id, _ = setup_titles
    app.stream_task = None
    app.begin_stream_task = lambda id: ClaudeChatApp.begin_stream_task(app, id)
    learning = Mock()
    monkeypatch.setattr(WebAPI, "schedule_memory_learning", learning)
    task = app.begin_stream_task(conv_id)
    task.title_target = ("deepseek", "actual-model")
    ClaudeChatApp.set_streaming_done(app, StreamTaskState.COMPLETED, task)
    assert wait_title(api, conv_id)["title_source"] == "ai"
    assert learning.call_count == 1
    assert task.state == StreamTaskState.COMPLETED


@pytest.mark.parametrize("state", [StreamTaskState.ABORTED, StreamTaskState.FAILED])
def test_unsuccessful_chat_does_not_generate_title(setup_titles, monkeypatch, state):
    _, app, conv_id, calls = setup_titles
    app.stream_task = None
    schedule = Mock()
    monkeypatch.setattr(WebAPI, "schedule_conversation_title", schedule)
    task = ClaudeChatApp.begin_stream_task(app, conv_id)
    ClaudeChatApp.set_streaming_done(app, state, task)
    schedule.assert_not_called()
    assert not calls


def test_gui_done_notifies_frontend_after_persistence_and_title_scheduling(setup_titles, monkeypatch):
    _, app, conv_id, _ = setup_titles
    app.stream_task = None
    task = ClaudeChatApp.begin_stream_task(app, conv_id)
    task.title_target = ("deepseek", "actual-gui-model")
    task.events.put(("text", "GUI answer"))
    task.events.put(("done", {"input_tokens": 1, "output_tokens": 2}))
    scheduled = Mock()
    monkeypatch.setattr(WebAPI, "schedule_conversation_title", scheduled)
    monkeypatch.setattr(WebAPI, "schedule_memory_learning", Mock())
    app.set_streaming_done = lambda state, target: ClaudeChatApp.set_streaming_done(app, state, target)

    def push(event):
        if event.type.value == "done":
            assert task.state == StreamTaskState.COMPLETED
            scheduled.assert_called_once_with(conv_id, ("deepseek", "actual-gui-model"))
            assert app.conv_manager.load_conversation(conv_id)["messages"][-1]["content"] == "GUI answer"

    app._push_stream_event = push
    ClaudeChatApp._process_sending_stream(app, task)


def test_router_dispatches_title_operations(setup_titles):
    api, _, conv_id, _ = setup_titles
    handler = SimpleNamespace(
        server=SimpleNamespace(api=api),
        read_json_body=lambda: {"conv_id": conv_id, "action": "edit", "title": "HTTP标题"},
        send_json_response=Mock(),
        send_error=Mock(),
    )
    router = HttpApiRouter(handler)
    router.dispatch_post("/api/conversation_title")
    assert handler.send_json_response.call_args.args[0]["title"] == "HTTP标题"
    router.dispatch_get("/api/conversation_title/" + conv_id)
    assert handler.send_json_response.call_args.args[0]["title_source"] == "manual"

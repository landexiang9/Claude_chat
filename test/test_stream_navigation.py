"""Stream cancellation and navigation with isolated SQLite and offline producers."""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

from claude_chat.api_bridge import WebAPI
from claude_chat.app import ClaudeChatApp
from claude_chat.db import DatabaseManager
from claude_chat.stream_protocol import StreamEventQueue, StreamTask, StreamTaskState


def test_navigation_does_not_redirect_or_delete_active_reply():
    manager = DatabaseManager()
    source = manager.new_conversation()
    other = manager.new_conversation()
    events = StreamEventQueue(conversation_id=source["id"])
    task = StreamTask(source["id"], events)
    task.transition(StreamTaskState.RUNNING)
    app = SimpleNamespace(
        lock=threading.RLock(), conv_manager=manager, stream_task=task,
        is_streaming=True, current_conv=source, window=None,
        set_streaming_done=Mock(), _push_stream_event=Mock(),
    )
    api = WebAPI(app)
    assert api.load_conversation(other["id"])["id"] == other["id"]
    assert app.current_conv["id"] == other["id"]
    assert api.delete_conversation(source["id"]) is False
    events.put(("thinking", "partial thought"))
    events.put(("text", "partial answer"))
    task.request_abort()
    events.put(("done", {"content_blocks": "late answer"}))
    ClaudeChatApp._process_sending_stream(app, task)
    saved = manager.load_conversation(source["id"])["messages"]
    assert len(saved) == 1 and saved[0]["aborted"] is True
    assert saved[0]["content"] == "partial answer"
    assert saved[0]["thinking"] == "partial thought"
    assert manager.load_conversation(other["id"])["messages"] == []
    assert app.current_conv["id"] == other["id"]
    app.set_streaming_done.assert_called_once_with(StreamTaskState.ABORTED, task)
    assert app._push_stream_event.call_args.args[0].type.value == "aborted"

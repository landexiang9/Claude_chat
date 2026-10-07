"""Regression tests for the browser HTTP streaming transport."""

import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from claude_chat.server import ClaudeChatHTTPHandler
from claude_chat.stream_protocol import StreamEventQueue, StreamTask, StreamTaskState


def test_http_generation_forwards_every_event_in_order():
    conv_id = "conversation-1"
    app = SimpleNamespace(
        conv_manager=SimpleNamespace(
            add_assistant_message_and_update_tokens=MagicMock(),
        ),
        current_conv=None,
        lock=threading.RLock(),
        set_streaming_done=MagicMock(),
    )

    def send_message(*_args, custom_queue):
        app.stream_task = StreamTask(conv_id, custom_queue)
        custom_queue.put(("thinking", "checking"))
        custom_queue.put(("text", "hello "))
        custom_queue.put(("search_start", {"query": "example", "id": "search-1"}))
        custom_queue.put(("text", "world"))
        custom_queue.put(("done", {"input_tokens": 3, "output_tokens": 2}))
        return True

    write_stream_line = MagicMock()
    send_header = MagicMock()
    handler = SimpleNamespace(
        server=SimpleNamespace(api=SimpleNamespace(_app=app, send_message=send_message)),
        send_response=MagicMock(),
        send_header=send_header,
        end_headers=MagicMock(),
        _send_cors_headers=MagicMock(),
        _write_stream_line=write_stream_line,
    )

    ClaudeChatHTTPHandler.handle_streaming_generation(
        handler, "send_message", conv_id, "Hi", []
    )

    forwarded_types = [
        call.args[0].type.value for call in write_stream_line.call_args_list
    ]
    assert forwarded_types == ["thinking", "text", "search_start", "text", "done"]
    app.conv_manager.add_assistant_message_and_update_tokens.assert_called_once_with(
        conv_id, "hello world", "checking", 3, 2
    )
    app.set_streaming_done.assert_called_once_with(
        StreamTaskState.COMPLETED, app.stream_task
    )
    send_header.assert_any_call("Cache-Control", "no-cache, no-store, no-transform")
    send_header.assert_any_call("X-Accel-Buffering", "no")


def test_write_stream_line_serializes_ndjson_and_flushes():
    class RecordingStream:
        def __init__(self):
            self.data = bytearray()
            self.flush_count = 0

        def write(self, value):
            self.data.extend(value)

        def flush(self):
            self.flush_count += 1

    events = StreamEventQueue(task_id="task-1", conversation_id="conversation-1")
    events.put(("text", "你好"))
    output = RecordingStream()
    handler = SimpleNamespace(wfile=output)

    ClaudeChatHTTPHandler._write_stream_line(handler, events.get_event())

    assert output.data.endswith(b"\n")
    assert '"type": "text"'.encode() in output.data
    assert "你好".encode("utf-8") in output.data
    assert output.flush_count == 1

"""Provider memory tool loops, with real SDK value validation and no model requests."""

import copy
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from anthropic.types import Message
from google.genai import types
from test_memory_discussions import discussion, memory as shared_memory, persist

from claude_chat.clients.claude import stream_claude_response_native
from claude_chat.clients.gemini import convert_messages_to_gemini, stream_gemini_response
from claude_chat.memory_tools import MemoryFallbackQueue, MemoryToolSession


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


class ClaudeStream:
    def __init__(self, message):
        self.message, self.closed = message, False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        self.closed = True

    def __iter__(self):
        for block in self.message.content:
            if block.type == "text":
                yield SimpleNamespace(
                    type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=block.text)
                )

    def get_final_message(self):
        return self.message


@pytest.mark.parametrize("provider", ["claude", "gemini"])
@pytest.mark.parametrize("web", [False, True])
def test_tool_roundtrip_persists_calls_roles_signatures_and_usage(memory, provider, web):
    db, store = memory
    conv = discussion(db)
    persist(store, conv)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    requests, streams = [], []
    events = queue.Queue()

    def generate(**kwargs):
        requests.append(copy.deepcopy(kwargs))
        first = len(requests) == 1
        if provider == "claude":
            final = Message(
                id="m1",
                model="claude-test",
                type="message",
                role="assistant",
                stop_reason="tool_use" if first else "end_turn",
                content=[{"type": "tool_use", "id": "c1", "name": "memory_overview", "input": {}}]
                if first
                else [{"type": "text", "text": "找到PHP讨论"}],
                usage={"input_tokens": 10, "output_tokens": 5},
            )
            stream = ClaudeStream(final)
            streams.append(stream)
            return stream
        part = (
            types.Part(
                function_call=types.FunctionCall(name="memory_overview", args={}, id="c1"),
                thought_signature=b"verified-sdk-signature",
            )
            if first
            else types.Part(text="找到PHP讨论")
        )
        return iter(
            [
                types.GenerateContentResponse(
                    candidates=[types.Candidate(content=types.Content(role="model", parts=[part]))],
                    usage_metadata=types.GenerateContentResponseUsageMetadata(
                        prompt_token_count=10, candidates_token_count=5
                    ),
                )
            ]
        )

    args = dict(
        api_key="offline-key",
        api_url="",
        proxy_mode="none",
        proxy_url="",
        messages=[{"role": "user", "content": "继续PHP问题"}],
        model="gemini-2.5-flash" if provider == "gemini" else "claude-test",
        max_tokens=1000,
        temperature=0.0,
        streaming_queue=events,
        abort_event=session.abort,
        memory_session=session,
        conv_id=conv["id"],
        conv_manager=db,
        file_upload_enabled=False,
        enable_search=web,
    )
    if provider == "claude":
        fake_client = Mock()
        fake_client.messages.stream.side_effect = generate
        with patch("claude_chat.clients.claude.Anthropic", return_value=fake_client):
            stream_claude_response_native(**args, thinking_config=None)
        assert all(s.closed for s in streams)
        assert any(t["name"] == "memory_overview" for t in requests[0]["tools"])
    else:
        with patch("google.genai.models.Models.generate_content_stream", side_effect=generate):
            stream_gemini_response(**args, thinking_enabled=False, thinking_budget=1024, thinking_level="low")
        calls = [p for msg in requests[1]["contents"] for p in msg["parts"] if "function_call" in p]
        assert calls[0]["thought_signature"] == b"verified-sdk-signature"
        assert any(t.function_declarations for t in requests[0]["config"].tools)
    assert len(requests) == 2
    assert list(events.queue)[-1][0] == "done", list(events.queue)
    assert list(events.queue)[-1][1]["input_tokens"] == 20
    messages = db.load_conversation(conv["id"])["messages"]
    assert messages[-2]["content"][-1]["type"] == "tool_use"
    assert messages[-1]["content"][0]["type"] == "tool_result"
    assert json.loads(messages[-1]["content"][0]["content"])["success"]
    replay = convert_messages_to_gemini(messages)
    assert replay[-1]["parts"][0]["function_response"]["name"] == "memory_overview"


def test_fallback_only_before_output_and_explicit_incompatibility(memory):
    db, store = memory
    conv = discussion(db)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    queue1 = queue.Queue()
    fallback = MemoryFallbackQueue(queue1, session)
    fallback.put(("error", "function declarations not supported with Google Search"))
    assert fallback.fallback and queue1.empty()
    fallback = MemoryFallbackQueue(queue1, session)
    fallback.put(("text", "Partial answer"))
    fallback.put(("error", "tools unsupported"))
    assert not fallback.fallback and queue1.qsize() == 2
    fallback = MemoryFallbackQueue(queue.Queue(), session)
    fallback.put(("error", "Invalid API key"))
    assert not fallback.fallback


def test_cancelled_session_hides_tools_and_never_reads_sources(memory):
    db, store = memory
    conv = discussion(db)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    assert session.begin_round()
    session.abort.set()
    with patch.object(session, "_query") as query:
        assert not session.tools("gemini")
        assert not json.loads(session.execute("memory_overview", {}))["success"]
        query.assert_not_called()

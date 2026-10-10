"""Usage accounting uses isolated SQLite and simulated reports, including failures and unknowns."""

import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from test_memory import app_for
from test_memory_discussions import discussion, memory as shared_memory
from test_memory_request_failures import sdk_request

from claude_chat.api_bridge import WebAPI
from claude_chat.clients.deepseek import stream_deepseek_response
from claude_chat.memory_jobs import MemoryRequestError, MemoryRequestQueue, request_json
from claude_chat.memory_tools import MemoryFallbackQueue, MemoryToolSession


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


@pytest.mark.parametrize("stage,category", [("episode", "background"), ("merge", "background"), ("plan", "chat")])
@pytest.mark.parametrize(
    "scenario,status",
    [
        ("success", "success"),
        ("error", "error"),
        ("timeout", "timeout"),
        ("cancelled", "cancelled"),
        ("truncated", "truncated"),
        ("format", "error"),
    ],
)
def test_request_ledger_retains_reported_usage_once_on_all_terminal_paths(memory, stage, category, scenario, status):
    db, store = memory
    app = app_for(db, store)
    abort = threading.Event()

    def stream(*args, **kwargs):
        events = args[8]
        events.record_usage({"input_tokens": 13, "output_tokens": 8})
        if scenario == "success":
            events.put(
                ("done", {"input_tokens": 13, "output_tokens": 8, "content_blocks": [{"type": "text", "text": "{}"}]})
            )
        elif scenario == "format":
            events.put(
                (
                    "done",
                    {"input_tokens": 13, "output_tokens": 8, "content_blocks": [{"type": "text", "text": "invalid"}]},
                )
            )
        elif scenario == "timeout":
            assert args[9].wait(1)
        elif scenario == "cancelled":
            abort.set()
        else:
            events.put(("error", "max_output_tokens" if scenario == "truncated" else "401 api_key=NEVER_STORE_THIS"))

    with patch("claude_chat.clients.stream_claude_response", side_effect=stream):
        if scenario == "success":
            request_json(app, store, "private system text", {"private": "NEVER_STORE_THIS"}, abort, stage, 0.02)
        else:
            with pytest.raises(MemoryRequestError):
                request_json(app, store, "private system text", {"private": "NEVER_STORE_THIS"}, abort, stage, 0.02)
    summary = store.usage.summary()
    totals = summary["categories"][category]
    assert totals["requests"] == 1 and totals["input_tokens"] == 13 and totals["output_tokens"] == 8
    assert summary["recent_requests"][0]["status"] == status
    assert "NEVER_STORE_THIS" not in json.dumps(summary)


def test_unknown_partial_zero_and_estimated_usage_are_distinct(memory):
    db, store = memory
    app = app_for(db, store)

    def stream(*args, **kwargs):
        events = args[8]
        events.record_usage({"input_tokens": 0})
        events.put(
            (
                "done",
                {
                    "usage_source": "estimated",
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "content_blocks": [{"type": "text", "text": "{}"}],
                },
            )
        )

    with patch("claude_chat.clients.stream_claude_response", side_effect=stream):
        request_json(app, store, "", {}, threading.Event())
    row = store.usage.summary()["recent_requests"][0]
    assert row["input_tokens"] == 0 and row["output_tokens"] is None
    assert store.usage.summary()["categories"]["background"]["unknown_requests"] == 1
    events = MemoryRequestQueue()
    events.record_usage({"input_tokens": 0, "output_tokens": 0})
    events.put(("done", {"usage_source": "reported", "input_tokens": 10, "output_tokens": 20}))
    assert events.usage == {"input_tokens": 0, "output_tokens": 0}  # Adapter estimates cannot replace explicit zeros.


@pytest.mark.parametrize("fail", [False, True])
def test_automatic_facts_and_router_account_without_changing_chat_totals(memory, fail):
    db, store = memory
    conv = discussion(db, [{"role": "user", "content": "以后请优先使用中文解释。"}])
    app = app_for(db, store)
    api = WebAPI(app)
    before = db.load_conversation(conv["id"])

    def stream(*args, **kwargs):
        events = args[8]
        events.record_usage({"input_tokens": 11, "output_tokens": 3})
        if fail:
            events.put(("error", "max_output_tokens"))
        else:
            text = '{"memories":[]}' if args[4] == "test" and args[5] == 4096 else '{"need_profile":false}'
            events.put(("text", text))
            events.put(("done", {"input_tokens": 11, "output_tokens": 3}))

    gate = threading.Lock()
    gate.acquire()
    with patch("claude_chat.clients.stream_claude_response", side_effect=stream):
        api._learn_memories(
            store, conv, [conv["messages"][0]["content"]], 1, store.options()["epoch"], app.config.data, gate
        )
        if fail:
            with pytest.raises(ValueError):
                api._route_memory_request("查询记忆", {}, conv_id=conv["id"])
        else:
            assert api._route_memory_request("查询记忆", {}, conv_id=conv["id"]) == {"need_profile": False}
    assert not gate.locked()
    totals = store.usage.summary()["categories"]
    assert totals["background"]["input_tokens"] == 11 and totals["chat"]["input_tokens"] == 11
    assert {r["status"] for r in store.usage.summary()["recent_requests"]} == {"truncated" if fail else "success"}
    after = db.load_conversation(conv["id"])
    assert after["input_tokens"] == before["input_tokens"] and after["output_tokens"] == before["output_tokens"]


def test_failed_plan_retains_reported_usage_and_merges_into_done_only_once(memory):
    db, store = memory
    conv = discussion(db)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    with (
        patch(
            "claude_chat.memory_jobs.request_json",
            side_effect=MemoryRequestError("format", "失败", {"input_tokens": 7}),
        ),
        patch.object(session, "execute", return_value="{}"),
    ):
        session.plan(app_for(db, store), "继续 PHP 问题", [])
    assert session.base_context["plan_usage"] == {"input_tokens": 7}
    target = queue.Queue()
    wrapper = MemoryFallbackQueue(target, session)
    wrapper.put(("done", {"input_tokens": 10, "output_tokens": 1}))
    assert target.get()[1]["input_tokens"] == 17
    wrapper.put(("done", {"input_tokens": 10, "output_tokens": 1}))
    assert target.get()[1]["input_tokens"] == 10


def test_real_responses_incomplete_records_usage_in_ledger(memory):
    _, store = memory
    with pytest.raises(MemoryRequestError):
        sdk_request(
            store,
            {},
            {
                "type": "response.incomplete",
                "response": {
                    "id": "r1",
                    "status": "incomplete",
                    "output": [],
                    "incomplete_details": {"reason": "max_output_tokens"},
                    "usage": {"input_tokens": 125, "output_tokens": 8192},
                },
            },
        )
    row = store.usage.summary()["recent_requests"][0]
    assert row["status"] == "truncated" and row["input_tokens"] == 125 and row["output_tokens"] == 8192


@pytest.mark.parametrize("reported", [False, True])
def test_real_chat_completions_distinguishes_reported_from_estimates(memory, reported):
    import httpx

    _, store = memory

    def respond(request):
        data = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "offline",
            "choices": [{"index": 0, "delta": {"content": "{}"}, "finish_reason": "stop"}],
        }
        if reported:
            data["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content="data: " + json.dumps(data) + "\n\ndata: [DONE]\n\n",
        )

    events = MemoryRequestQueue()
    with patch(
        "claude_chat.clients.deepseek.build_http_client",
        side_effect=lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)),
    ):
        stream_deepseek_response(
            "offline",
            "https://example.test",
            "none",
            "",
            [{"role": "user", "content": "test"}],
            "offline",
            100,
            0,
            events,
            threading.Event(),
        )
    assert events.usage == ({"input_tokens": 0, "output_tokens": 0} if reported else {})


def test_summary_remains_idempotent_and_empty_upgrade_does_not_invent_history(memory):
    db, store = memory
    api = WebAPI(app_for(db, store))
    assert api.memory_operation("usage")["usage"]["categories"]["background"]["requests"] == 0
    identity = store.usage.begin("facts", "mock", "offline")
    store.usage.finish(identity, "error", {"input_tokens": 4})
    store.usage.finish(identity, "error", {"input_tokens": 4})
    result = api.memory_operation("usage")["usage"]
    assert result["categories"]["background"]["requests"] == 1
    assert result["categories"]["background"]["input_tokens"] == 4
    assert result["categories"]["background"]["unknown_requests"] == 1


@pytest.mark.parametrize("provider", ["claude", "gemini", "openai", "responses"])
def test_provider_reports_on_the_cancellation_event_are_not_discarded(provider):
    import httpx
    from google.genai import types
    from test_memory_protocols import ClaudeStream

    from claude_chat.clients.claude import stream_claude_response_native
    from claude_chat.clients.gemini import stream_gemini_response
    from claude_chat.clients.responses import stream_responses_response

    events, abort = MemoryRequestQueue(), threading.Event()
    args = dict(
        api_key="offline",
        api_url="https://example.test",
        proxy_mode="none",
        proxy_url="",
        messages=[{"role": "user", "content": "test"}],
        model="offline",
        max_tokens=100,
        temperature=0,
        streaming_queue=events,
        abort_event=abort,
        file_upload_enabled=False,
    )

    def chunks():
        abort.set()
        yield SimpleNamespace(choices=[], usage=SimpleNamespace(prompt_tokens=14, completion_tokens=5))

    if provider == "openai":
        client = Mock()
        client.chat.completions.create.return_value = chunks()
        with patch("openai.OpenAI", return_value=client):
            stream_deepseek_response(**args)
    elif provider == "claude":

        class PartialStream(ClaudeStream):
            def __iter__(self):
                abort.set()
                yield SimpleNamespace(
                    type="message_start",
                    message=SimpleNamespace(usage=SimpleNamespace(input_tokens=14, output_tokens=5)),
                )

        client = Mock()
        client.messages.stream.return_value = PartialStream(None)
        with patch("claude_chat.clients.claude.Anthropic", return_value=client):
            stream_claude_response_native(**args, thinking_config=None)
    elif provider == "gemini":

        def generate(**kwargs):
            abort.set()
            yield types.GenerateContentResponse(
                candidates=[],
                usage_metadata=types.GenerateContentResponseUsageMetadata(
                    prompt_token_count=14, candidates_token_count=5
                ),
            )

        args["model"] = "gemini-2.5-flash"
        args["api_url"] = ""
        with patch("google.genai.models.Models.generate_content_stream", side_effect=generate):
            stream_gemini_response(**args, thinking_enabled=False, thinking_budget=0, thinking_level="low")
    else:

        def respond(request):
            abort.set()
            event = {
                "type": "response.completed",
                "response": {
                    "id": "r1",
                    "status": "completed",
                    "output": [],
                    "usage": {"input_tokens": 14, "output_tokens": 5},
                },
            }
            return httpx.Response(
                200, headers={"Content-Type": "text/event-stream"}, content="data: " + json.dumps(event) + "\n\n"
            )

        with patch(
            "claude_chat.clients.responses.build_http_client",
            side_effect=lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)),
        ):
            stream_responses_response(**args)
    assert events.usage == {"input_tokens": 14, "output_tokens": 5}
    assert list(events.queue)[-1][0] == "aborted"


@pytest.mark.parametrize("provider", ["openai", "claude", "gemini"])
def test_actual_output_cap_is_not_saved_as_success_even_when_json_is_complete(memory, provider):
    import httpx
    from anthropic.types import Message
    from google.genai import types
    from test_memory_protocols import ClaudeStream

    db, store = memory
    app = app_for(db, store)
    app.config.data.update(active_platform="deepseek" if provider == "openai" else provider, model="offline")
    mapped = {
        "api_key": "offline",
        "api_url": "https://example.test" if provider == "openai" else "",
        "provider_adapter": "local",
        "request_params": {},
    }

    def respond(request):
        first = {
            "id": "c1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "offline",
            "choices": [{"index": 0, "delta": {"content": '{"episodes":[]}'}, "finish_reason": "length"}],
        }
        second = {**first, "choices": [], "usage": {"prompt_tokens": 14, "completion_tokens": 5, "total_tokens": 19}}
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            content="data: " + json.dumps(first) + "\n\ndata: " + json.dumps(second) + "\n\ndata: [DONE]\n\n",
        )

    if provider == "openai":
        transport = patch(
            "claude_chat.clients.deepseek.build_http_client",
            side_effect=lambda *a, **k: httpx.Client(transport=httpx.MockTransport(respond)),
        )
    elif provider == "claude":
        client = Mock()
        client.messages.stream.return_value = ClaudeStream(
            Message(
                id="m1",
                model="offline",
                type="message",
                role="assistant",
                stop_reason="max_tokens",
                content=[{"type": "text", "text": '{"episodes":[]}'}],
                usage={"input_tokens": 14, "output_tokens": 5},
            )
        )
        transport = patch("claude_chat.clients.claude.Anthropic", return_value=client)
    else:
        app.config.data["model"] = "gemini-2.5-flash"
        response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    finish_reason=types.FinishReason.MAX_TOKENS,
                    content=types.Content(role="model", parts=[types.Part(text='{"episodes":[]}')]),
                )
            ],
            usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=14, candidates_token_count=5),
        )
        transport = patch("google.genai.models.Models.generate_content_stream", return_value=iter([response]))
    with patch("claude_chat.platform_params.PlatformParamMapper.map_params", return_value=mapped), transport:
        with pytest.raises(MemoryRequestError) as failure:
            request_json(app, store, "", {}, threading.Event())
    assert failure.value.code == "output_limit"
    row = store.usage.summary()["recent_requests"][0]
    assert row["status"] == "truncated" and row["input_tokens"] == 14 and row["output_tokens"] == 5

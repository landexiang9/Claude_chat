"""Backfill failures through the real SDK and isolated SQLite; every model request is mocked."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from test_memory_discussions import candidate, discussion, memory as shared_memory
from test_responses import completed, message_item

from claude_chat.memory_episodes import snapshot, source_rows
from claude_chat.memory_jobs import (
    EPISODE_PROMPT,
    MemoryJobs,
    MemoryRequestError,
    background_request_params,
    model_request_error,
    request_json,
)
from claude_chat.services.conversation_titles import title_request_params


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


def sdk_request(store, payload, terminal):
    """Exercise dispatcher -> Responses -> real OpenAI streaming parser -> memory validation."""
    bodies = []

    def respond(request):
        bodies.append(json.loads(request.content))
        wire = "data: " + json.dumps(terminal) + "\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=wire)

    app = SimpleNamespace(config=SimpleNamespace(data={"active_platform": "deepseek", "model": "deepseek-flash"}))
    mapped = {
        "api_key": "offline-test",
        "api_url": "https://api.deepseek.com",
        "provider_adapter": "responses",
        "request_params": {"max_output_tokens": 1, "reasoning": {"effort": "high"}},
        "custom_params": {"tools": [{"type": "web_search"}]},
    }
    with (
        patch("claude_chat.platform_params.PlatformParamMapper.map_params", return_value=mapped),
        patch(
            "claude_chat.clients.responses.build_http_client",
            side_effect=lambda *a, **kw: httpx.Client(transport=httpx.MockTransport(respond)),
        ),
    ):
        result = request_json(app, store, EPISODE_PROMPT, payload, threading.Event())
    return result, bodies


def test_responses_budget_short_sources_and_nullable_fields(memory):
    db, store = memory
    conv = discussion(db)
    rows = source_rows(conv)
    earlier = store.episodes.normalize(candidate(rows), rows)
    output = {
        "episodes": [
            {
                "topic": "PHP null 字节",
                "problem": {
                    "text": "复制后属性为空",
                    "source_ids": "s1",
                    "kind": "user_statement",
                    "quote": None,
                },
                "findings": None,
                "proposed_solutions": None,
                "open_questions": None,
                "confirmed_outcome": None,
            }
        ]
    }
    (result, usage), bodies = sdk_request(
        store,
        {"messages": rows, "earlier_discussion_not_new_evidence": [{**earlier, "status": "resolved"}]},
        completed([message_item(json.dumps(output))]),
    )
    body = bodies[0]
    assert body["max_output_tokens"] == 8192
    assert body["reasoning"] == {"effort": "none"}
    assert not body.get("tools")
    assert all(row["source_id"] not in json.dumps(body) for row in rows)
    assert result["episodes"][0]["problem"]["source_ids"] == [rows[0]["source_id"]]
    assert usage == {"input_tokens": 21, "output_tokens": 8}
    ids = store.episodes.persist_batch(
        conv["id"], snapshot(conv["messages"]), rows, result["episodes"], store.options()["epoch"]
    )
    episode = store.episodes.read(ids[0], "global")
    assert episode["status"] == "unresolved"
    assert episode["findings"] == [] and episode["confirmed_outcome"] is None


def test_responses_incomplete_preserves_billable_usage_and_specific_error(memory):
    _, store = memory
    terminal = {
        "type": "response.incomplete",
        "response": {
            "id": "r1",
            "status": "incomplete",
            "output": [message_item('{"episodes":[')],
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"input_tokens": 125, "output_tokens": 8192},
        },
    }
    with pytest.raises(MemoryRequestError) as failure:
        sdk_request(store, {}, terminal)
    assert failure.value.code == "output_limit"
    assert failure.value.usage == {"input_tokens": 125, "output_tokens": 8192}
    assert not failure.value.retryable


def test_output_limit_halves_batch_without_skipping_sources(memory):
    db, store = memory
    conv = discussion(db)
    calls = []

    def request(system, payload, abort, stage):
        calls.append(payload["messages"])
        if len(calls) == 1:
            raise MemoryRequestError(
                "output_limit", "摘要输出达到 token 上限", {"input_tokens": 20, "output_tokens": 8}
            )
        return {"episodes": []}, {"input_tokens": 3, "output_tokens": 2}

    jobs = MemoryJobs(store, request)
    jobs.enqueue([conv["id"]])
    assert not jobs.step()
    job = jobs.list()[0]
    assert job["cursor"] == 0 and job["attempts"] == 1
    assert job["batch_units"] == 1 and "缩小批次" in job["error"]
    assert job["input_tokens"] == 20 and job["output_tokens"] == 8
    assert not jobs.step()  # Persistent backoff; no immediate paid retry.
    with store.connect() as conn:
        conn.execute("UPDATE memory_jobs SET retry_at=''")
    assert jobs.step()
    job = jobs.list()[0]
    assert job["cursor"] == 1 and job["status"] == "queued"
    assert job["input_tokens"] == 23 and job["output_tokens"] == 10
    assert calls[1] == calls[0][:1]
    while jobs.step():
        pass
    job = next(j for j in jobs.list() if j["kind"] == "episode")
    assert job["cursor"] == job["total"] == 3 and job["status"] == "completed"
    assert [batch[0]["source_id"] for batch in calls[1:]] == [row["source_id"] for row in calls[0]]


def test_schema_error_stops_repeated_requests_and_manual_resume_preserves_cursor(memory):
    db, store = memory
    conv = discussion(db)
    calls = []

    def request(system, payload, abort, stage):
        calls.append(payload)
        item = candidate(payload["messages"])
        if len(calls) == 1:
            item["findings"] = "模型返回了没有来源的普通字符串"
        return {"episodes": [item]}, {"input_tokens": 9, "output_tokens": 10}

    jobs = MemoryJobs(store, request)
    jobs.enqueue([conv["id"]])
    assert not jobs.step()
    job = jobs.list()[0]
    assert job["cursor"] == 0 and job["attempts"] == 5
    assert "findings" in job["error"] and "手动继续" in job["error"]
    assert job["input_tokens"] == 9 and job["output_tokens"] == 10
    assert not jobs.step() and len(calls) == 1
    with patch.object(jobs, "start", return_value=True):
        assert jobs.resume()
    assert jobs.step()
    job = next(j for j in jobs.list() if j["kind"] == "episode")
    assert job["status"] == "completed" and job["cursor"] == job["total"]
    assert job["input_tokens"] == 18 and job["output_tokens"] == 20
    assert store.episodes.list()[0]["status"] == "resolved"


@pytest.mark.parametrize("malformation", ["invented_source", "assistant_confirmation", "string_claim", "null_quote"])
def test_schema_tolerance_never_invents_evidence(memory, malformation):
    db, store = memory
    rows = source_rows(discussion(db))
    item = candidate(rows)
    if malformation == "invented_source":
        item["problem"][0]["source_ids"] = "s999"
    elif malformation == "assistant_confirmation":
        item["confirmed_outcome"]["source_ids"] = [rows[1]["source_id"]]
        item["confirmed_outcome"]["quote"] = rows[1]["text"]
    elif malformation == "string_claim":
        item["findings"] = ["无来源摘要"]
    else:
        item["confirmed_outcome"]["quote"] = None
    with pytest.raises(ValueError):
        store.episodes.normalize(item, rows)


@pytest.mark.parametrize("stage,maximum", [("episode", 8192), ("merge", 4096), ("facts", 4096), ("plan", 768)])
@pytest.mark.parametrize(
    "platform,model,adapter,key",
    [
        ("claude", "claude-sonnet-4-5", "", "max_tokens"),
        ("gemini", "gemini-2.5-flash", "", "max_output_tokens"),
        ("deepseek", "deepseek-chat", "", "max_tokens"),
        ("deepseek", "deepseek-flash", "responses", "max_output_tokens"),
    ],
)
def test_independent_stage_budgets_leave_title_budget_unchanged(platform, model, adapter, key, stage, maximum):
    mapped = {"provider_adapter": adapter, "api_url": "https://api.deepseek.com", "request_params": {key: 1}}
    assert background_request_params(platform, model, mapped, stage)[key] == maximum
    assert title_request_params(platform, model, mapped)[key] == 2048


@pytest.mark.parametrize(
    "message,code,retryable",
    [
        ("401 unauthorized api_key=secret", "authentication", False),
        ("429 rate limit", "rate_limit", True),
        ("503 connection failed", "transport", True),
        ("maximum context length exceeded", "context_limit", False),
        ("max_output_tokens reached", "output_limit", False),
    ],
)
def test_failure_classification_is_actionable_and_does_not_leak_bodies(message, code, retryable):
    failure = model_request_error(message)
    assert failure.code == code and failure.retryable == retryable
    assert "secret" not in str(failure)

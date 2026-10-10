"""Four-layer memory acceptance checks: isolated SQLite, synthetic evidence and no paid requests."""

import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claude_chat.db import DatabaseManager
from claude_chat.memory_episodes import snapshot, source_rows, units
from claude_chat.memory_jobs import MemoryJobs, request_json
from claude_chat.memory_store import MemoryStore
from claude_chat.memory_tools import MemoryToolSession, definitions


@pytest.fixture
def memory(tmp_path):
    with (
        patch("claude_chat.db.DB_PATH", tmp_path / "discussion.db"),
        patch("claude_chat.db.CONVERSATIONS_DIR", tmp_path / "empty"),
    ):
        db = DatabaseManager()
        store = MemoryStore(db)
        yield db, store


def discussion(db, messages=None):
    conv = db.new_conversation()
    conv["messages"] = messages or [
        {"role": "user", "content": "PHP 私有属性序列化后出现 null 字节，复制代码后属性变为空。"},
        {"role": "assistant", "content": "建议保留原始序列化字符串，避免 trim 与 JSON 转换，尝试 ReflectionProperty。"},
        {"role": "user", "content": "保留原始字符串后已经解决，属性值恢复正常。"},
    ]
    db.save_conversation(conv)
    return conv


def candidate(rows, confirmed=True):
    user = next(r for r in rows if r["evidence_role"] == "user")
    assistant = next((r for r in rows if r["evidence_role"] == "assistant"), None)
    outcome = rows[-1]
    return {
        "topic": "PHP 私有属性与 null 字节",
        "problem": [{"text": "复制后私有属性变为空", "source_ids": [user["source_id"]], "kind": "user_statement"}],
        "findings": [],
        "proposed_solutions": [{"text": "保留原始字符串", "source_ids": [assistant["source_id"]]}] if assistant else [],
        "open_questions": [],
        "confirmed_outcome": {
            "text": "保留原始字符串后用户确认属性恢复正常",
            "source_ids": [outcome["source_id"]],
            "kind": "user_confirmed",
            "quote": outcome["text"],
        }
        if confirmed
        else None,
    }


def persist(store, conv, confirmed=True):
    rows = source_rows(conv)
    return store.episodes.persist_batch(
        conv["id"], snapshot(conv["messages"]), rows, [candidate(rows, confirmed)], store.options()["epoch"]
    )[0]


def test_read_tools_scope_rounds_history_and_shared_budget(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    store.engine.index.sync_documents()
    prompt, context = store.engine.retrieve(conv["id"], "继续 PHP null 字节", reserve_tools=True)
    session = MemoryToolSession(store, conv["id"], threading.Event(), context["chars"])
    assert session.begin_round()
    output = json.loads(session.execute("read_memory_episode", {"episode_id": identity}, "t1"))
    assert output["success"] and output["data"]["confirmed_outcome"]
    source_id = output["data"]["problem"][0]["source_ids"][0]
    # New budget for history assertions; the same source authorization still applies.
    session.remaining = 6000
    history = json.loads(session.execute("read_history_excerpt", {"source_id": source_id}, "t2"))
    assert history["success"] and any(e["role"] == "assistant" for e in history["data"]["excerpts"])
    denied = json.loads(session.execute("memory_overview", {"scope": "project:other"}))
    assert not denied["success"]
    assert not json.loads(session.execute("search_memory", "{bad json"))["success"]
    session.remaining = 6000
    assert session.begin_round()
    session.remaining = 6000
    assert not session.begin_round()
    assert not json.loads(session.execute("memory_overview", {}))["success"]
    assert not session.tools("openai")
    for protocol in ("openai", "responses", "claude", "gemini"):
        assert len(definitions(protocol)) == 4
    store.set_privacy(conv["id"], {"temporary": True})
    assert not session.authorized()


def test_v3_import_is_atomic_external_and_clear_removes_discussions(memory):
    db, store = memory
    conv = discussion(db)
    persist(store, conv)
    exported = store.export_data()
    assert exported["version"] == 3 and exported["sources"]
    store.import_data(exported)
    imported = next(e for e in store.episodes.list() if e["origin"] == "external")
    assert imported["confirmed_outcome"] is None
    assert all(c["kind"] == "external_unverified" for c in imported["problem"])
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM memory_sources WHERE entity_id=?", (imported["id"],)).fetchone()
    before = len(store.list())
    invalid = {"version": 3, "memories": [{"content": "应该回滚的记忆"}], "episodes": [{"topic": ""}]}
    with pytest.raises(ValueError):
        store.import_data(invalid)
    assert len(store.list()) == before
    store.forget()
    assert not store.episodes.list()


def test_consolidation_completes_and_preserves_explicit_projects(memory):
    db, store = memory
    conv = discussion(db)
    persist(store, conv)
    project = store.put({"content": "长期维护 Chatudex 项目", "category": "project"})

    def merge(system, payload, abort, stage):
        assert stage == "merge" and any(f["id"] == project["id"] for f in payload["facts"])
        return {"groups": [{"label": "Chatudex", "fact_ids": [project["id"]], "episode_ids": []}]}, {}

    jobs = MemoryJobs(store, merge)
    jobs.enqueue_merge("global")
    assert jobs.merge_step()
    assert jobs.list()[0]["status"] == "completed"
    assert store.overviews.get("global")["groups"][0]["kind"] == "project"


@pytest.mark.parametrize("protocol", ["deepseek", "responses"])
def test_memory_tools_work_without_web_search_and_persist_protocol_pair(memory, protocol):
    import copy
    import queue

    import httpx

    from claude_chat.clients.deepseek import stream_deepseek_response
    from claude_chat.clients.responses import convert_messages_to_responses, stream_responses_response

    db, store = memory
    conv = discussion(db)
    persist(store, conv)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    bodies = []

    def respond(request):
        bodies.append(copy.deepcopy(json.loads(request.content)))
        first = len(bodies) == 1
        if protocol == "responses":
            output = (
                [
                    {
                        "type": "function_call",
                        "id": "fc1",
                        "call_id": "c1",
                        "name": "memory_overview",
                        "arguments": "{}",
                        "status": "completed",
                    }
                ]
                if first
                else [
                    {
                        "type": "message",
                        "id": "m1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "output_text", "text": "PHP 背景已找到", "annotations": []}],
                    }
                ]
            )
            payload = {
                "type": "response.completed",
                "response": {
                    "id": "r1",
                    "status": "completed",
                    "output": output,
                    "usage": {"input_tokens": 10, "output_tokens": 5},
                },
            }
        else:
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "memory_overview", "arguments": "{}"},
                        }
                    ]
                }
                if first
                else {"content": "PHP 背景已找到"}
            )
            payload = {
                "id": "chat1",
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": delta, "finish_reason": "tool_calls" if first else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, text="data: " + json.dumps(payload) + "\n\n"
        )

    def client(*args):
        return httpx.Client(transport=httpx.MockTransport(respond))

    events = queue.Queue()
    options = dict(
        api_key="offline-test",
        api_url="https://example.test/v1",
        proxy_mode="none",
        proxy_url="",
        messages=[{"role": "user", "content": "上次 PHP 问题"}],
        model="test-model",
        max_tokens=1000,
        temperature=0.0,
        streaming_queue=events,
        abort_event=session.abort,
        enable_search=False,
        file_upload_enabled=False,
        memory_session=session,
    )
    with patch(f"claude_chat.clients.{protocol}.build_http_client", side_effect=client):
        (stream_deepseek_response if protocol == "deepseek" else stream_responses_response)(**options)
    assert len(bodies) == 2 and list(events.queue)[-1][0] == "done"
    assert "search_web" not in json.dumps(bodies[0])
    assert "PHP" in json.dumps(bodies[1], ensure_ascii=False)
    assert session.trace[0]["name"] == "memory_overview"
    if protocol == "responses":
        block = list(events.queue)[-1][1]["content_blocks"][0]
        reloaded = convert_messages_to_responses(
            [{"role": "assistant", "content": [block]}], block["_responses"]["scope"]
        )
        assert [i["type"] for i in reloaded] == ["function_call", "function_call_output", "message"]


def test_episode_preserves_roles_conclusion_and_sqlite_source_rewrites(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    episode = store.episodes.read(identity, "global")
    assert episode["status"] == "resolved"
    assert episode["proposed_solutions"][0]["kind"] == "assistant_proposal"
    assert not store.list()  # Discussion does not become a saved personal fact.
    db.save_conversation(conv)  # All SQLite row IDs change.
    assert store.episodes.read(identity, "global")
    conv["messages"].append({"role": "user", "content": "现在继续研究另外一个对象生命周期问题。"})
    db.save_conversation(conv)
    assert store.episodes.read(identity, "global")  # Appending preserves the prior evidence.
    conv["messages"][2]["content"] = "结果尚未验证"
    db.save_conversation(conv)
    assert store.episodes.read(identity, "global") is None


def test_assistant_suggestion_cannot_be_user_confirmed_or_tool_verified(memory):
    db, store = memory
    conv = discussion(db)
    rows = source_rows(conv)
    value = candidate(rows)
    value["confirmed_outcome"]["source_ids"] = [rows[1]["source_id"]]
    value["confirmed_outcome"]["quote"] = rows[1]["text"]
    with pytest.raises(ValueError, match="用户"):
        store.episodes.normalize(value, rows)
    value["confirmed_outcome"]["kind"] = "tool_verified"
    with pytest.raises(ValueError, match="工具"):
        store.episodes.normalize(value, rows)
    assert not store.episodes.list()


@pytest.mark.parametrize(
    "changes", [{"temporary": True}, {"exclude_history": True}, {"memory_off": True}, {"scope": "home"}]
)
def test_episode_privacy_and_async_source_guard(memory, changes):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    epoch = store.options()["epoch"]
    store.set_privacy(conv["id"], changes)
    assert store.episodes.read(identity, "global") is None
    with pytest.raises(ValueError):
        store.episodes.persist_batch(
            conv["id"], snapshot(conv["messages"]), source_rows(conv), [candidate(source_rows(conv))], epoch
        )


def test_manual_summary_precedence_and_forgetting_block_fallback(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    edited = candidate(source_rows(conv), False)
    edited["topic"] = "手工纠正的 PHP 话题"
    assert store.episodes.edit(identity, "global", edited)["origin"] == "manual"
    store.episodes.persist_batch(
        conv["id"],
        snapshot(conv["messages"]),
        source_rows(conv),
        [candidate(source_rows(conv))],
        store.options()["epoch"],
    )
    assert store.episodes.read(identity, "global")["topic"] == edited["topic"]
    store.episodes.forget(identity, "global")
    assert not store.episodes.list()
    assert not store.privacy(conv["id"])["exclude_history"]  # Forget supporting messages, not the whole conversation.
    assert not store.retrieve(discussion(db)["id"], "PHP 私有属性")[1]["history"]


def test_long_job_tracks_processed_units_failure_backoff_and_empty_success(memory):
    db, store = memory
    conv = discussion(
        db,
        [
            {"role": "user", "content": "我在研究 PHP 对象生命周期。" * 1600},
            {"role": "assistant", "content": "建议跟踪析构函数调用顺序。" * 1000},
        ],
    )
    calls = []

    def request(system, payload, abort, stage):
        calls.append(payload["messages"])
        return {"episodes": []}, {"input_tokens": 7, "output_tokens": 2}

    jobs = MemoryJobs(store, request)
    preview = jobs.preview([conv["id"]])
    assert preview["estimated_requests"] > 1 and preview["requires_user_start"]
    jobs.enqueue([conv["id"]], {conv["id"]: preview["conversations"][0]["source_hash"]})
    assert jobs.step()
    first = jobs.list()[0]
    assert 0 < first["cursor"] < first["total"] and first["status"] == "queued"
    jobs.request = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("private provider details"))
    assert not jobs.step()
    failed = jobs.list()[0]
    assert failed["cursor"] == first["cursor"] and failed["retry_at"] and failed["status"] == "error"
    assert "private" not in failed["error"]
    jobs.request = request
    with store.connect() as conn:
        conn.execute("UPDATE memory_jobs SET status='queued',retry_at=''")
    while jobs.step():
        pass
    completed = jobs.list()[0]
    assert completed["cursor"] == completed["total"] and completed["status"] == "completed"
    assert sum(len(batch) for batch in calls) == len(units(source_rows(conv)))
    assert "".join(r["text"] for batch in calls for r in batch) == "".join(r["text"] for r in source_rows(conv))


def test_job_pause_during_model_request_rejects_late_result(memory):
    db, store = memory
    conv = discussion(db)
    jobs = None

    def request(system, payload, abort, stage):
        jobs.pause()
        return {"episodes": [candidate(payload["messages"])]}, {}

    jobs = MemoryJobs(store, request)
    jobs.enqueue([conv["id"]])
    assert not jobs.step()
    assert jobs.list()[0]["status"] == "paused" and jobs.list()[0]["cursor"] == 0
    assert not store.episodes.list()


def test_preview_sensitive_attachment_filter_and_edited_snapshot(memory):
    db, store = memory
    conv = discussion(
        db,
        [
            {"role": "user", "content": "api_key=sk-secret-secret-secret"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "附件正文不发送", "_attachment": {"name": "private.txt"}}],
            },
            {"role": "user", "content": "我希望分析一个长期研究的 PHP 序列化问题。"},
        ],
    )
    jobs = MemoryJobs(store, None)
    preview = jobs.preview([conv["id"]])
    assert preview["conversations"][0]["units"] == 1
    conv["messages"].append({"role": "user", "content": "修改了讨论"})
    db.save_conversation(conv)
    with pytest.raises(ValueError, match="变化"):
        jobs.enqueue([conv["id"]], {conv["id"]: preview["conversations"][0]["source_hash"]})


def test_background_request_has_independent_parameters_and_disables_tools(memory):
    db, store = memory
    app = SimpleNamespace(
        config=SimpleNamespace(
            data={"active_platform": "deepseek", "model": "deepseek-chat", "deepseek_api_key": "offline-key"}
        )
    )
    mapped = {"api_key": "offline-key", "api_url": "https://api.deepseek.com", "provider_adapter": ""}

    def stream(*args, **kwargs):
        assert not kwargs["enable_search"] and not kwargs["enable_web_fetch"]
        assert not kwargs["gemini_enable_code_sandbox"] and not kwargs["file_upload_enabled"]
        assert not kwargs.get("memory_tools")
        assert kwargs["request_params"]["thinking"] == {"type": "disabled"}
        args[8].put(("text", '{"episodes":[]}'))
        args[8].put(("done", {"input_tokens": 11, "output_tokens": 3}))

    with (
        patch("claude_chat.platform_params.PlatformParamMapper.map_params", return_value=mapped),
        patch("claude_chat.clients.stream_claude_response", side_effect=stream),
    ):
        result, usage = request_json(app, store, "test", {}, threading.Event())
    assert result == {"episodes": []} and usage == {"input_tokens": 11, "output_tokens": 3}


def test_overview_injection_detailed_retrieval_and_invalidated_confirmation(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    target = discussion(db, [{"role": "user", "content": "继续上次那个 PHP 问题"}])
    prompt, context = store.engine.retrieve(target["id"], "继续上次那个 PHP 问题")
    assert identity in {e["id"] for e in context["episodes"]}
    assert "discussion_memory" in prompt and "user_confirmed" in prompt
    assert "memory_directory" in prompt and context["directory"]["coverage"]["available_topic_count"] == 1
    assert len(prompt) == context["chars"] <= store.options()["budget_chars"]
    overview = store.overviews.directory("global")
    store.overviews.save("global", overview["groups"], overview["input_hash"], store.options()["epoch"])
    assert store.overviews.get("global")["method"] == "model"
    conv["messages"][2]["content"] = "这个方法未能解决，仍需排查"
    db.save_conversation(conv)
    assert store.overviews.get("global")["coverage"]["available_topic_count"] == 0
    assert "属性恢复正常" not in store.engine.retrieve(target["id"], "PHP 问题")[0]


def test_deictic_retrieval_recent_context_and_model_router_denial(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv, False)
    target = discussion(db, [{"role": "user", "content": "继续那个问题"}])
    recent = [
        {"role": "user", "content": "PHP 私有属性怎么保留原始数据？"},
        {"role": "assistant", "content": "可以继续检查 PHP 的序列化字符串。"},
        {"role": "user", "content": "继续那个问题"},
    ]
    _, context = store.engine.retrieve(target["id"], "继续那个问题", recent=recent)
    assert context["router"]["method"] == "context_rules"
    assert identity in {e["id"] for e in context["episodes"]}
    store.set_options({"router_model_enabled": True})
    store.engine.router = lambda query, fallback: {
        "need_profile": False,
        "need_semantic_memory": False,
        "need_recent_history": False,
    }
    _, context = store.engine.retrieve(target["id"], "继续那个问题", recent=recent)
    assert not context["memories"] and not context["history"] and not context["episodes"]


def test_overview_scope_and_fabricated_merge_references(memory):
    db, store = memory
    conv = discussion(db)
    store.set_privacy(conv["id"], {"scope": "work"})
    identity = persist(store, conv)
    assert store.overviews.get("global")["coverage"]["available_topic_count"] == 0
    overview = store.overviews.get("work")
    assert overview["coverage"]["available_topic_count"] == 1
    with pytest.raises(ValueError, match="来源"):
        store.overviews.save(
            "global",
            [{"label": "工作", "episode_ids": [identity]}],
            store.overviews.inputs("global")[2],
            store.options()["epoch"],
        )
    store.set_options({"history_enabled": False})
    assert store.overviews.get("work")["coverage"]["available_topic_count"] == 0

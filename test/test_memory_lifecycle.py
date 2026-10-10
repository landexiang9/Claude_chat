"""Source/privacy, temporal and durable-job acceptance regressions."""

import json
import threading
from unittest.mock import patch

import pytest
from test_memory_discussions import candidate, discussion, memory as shared_memory, persist

from claude_chat.memory_episodes import snapshot, source_rows
from claude_chat.memory_jobs import MemoryJobs
from claude_chat.memory_store import learning_statements
from claude_chat.memory_tools import MemoryToolSession


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


def test_external_edit_versions_export_and_import_remain_unverified(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    updated = candidate(source_rows(conv), False)
    updated["problem"][0]["text"] = "人工纠正了问题描述"
    store.episodes.edit(identity, "global", updated)
    assert store.episodes.versions(identity, "global")[0]["version"] == 1
    exported = store.export_data()
    assert exported["episodes"][0]["history_versions"]
    store.import_data(exported)
    external = next(e for e in store.episodes.list() if e["origin"] == "external")
    changed = {**external, "topic": "外部笔记纠正", "confirmed_outcome": None}
    edited = store.episodes.edit(external["id"], "global", changed)
    assert edited["origin"] == "external" and edited["topic"] == "外部笔记纠正"
    assert all(c["kind"] == "external_unverified" for c in edited["problem"])
    assert store.episodes.versions(external["id"], "global")


def test_forgetting_preserves_independent_topic_in_same_conversation(memory):
    db, store = memory
    conv = discussion(db)
    conv["messages"].append({"role": "user", "content": "现在研究 Docker 网络隔离和端口映射。"})
    db.save_conversation(conv)
    php = persist(store, conv, False)
    rows = source_rows(conv)
    docker = {
        "topic": "Docker 网络隔离",
        "problem": [{"text": rows[-1]["text"], "kind": "user_statement", "source_ids": [rows[-1]["source_id"]]}],
    }
    identity = store.episodes.persist_batch(
        conv["id"], snapshot(conv["messages"]), rows, [docker], store.options()["epoch"]
    )[0]
    store.episodes.forget(php, "global")
    assert store.episodes.read(identity, "global")
    docs = store.engine.index.sync_documents()
    assert not any(d[1] == "history" and "私有属性序列化" in d[4] for d in docs)
    assert any(d[1] == "history" and "Docker" in d[4] for d in docs)


def test_two_fact_sources_and_deletion_preserve_supported_fact(memory):
    db, store = memory
    text = "以后命令示例优先使用 PowerShell。"
    first = discussion(db, [{"role": "user", "content": text}])
    second = discussion(db, [{"role": "user", "content": text}])
    data = {
        "key": "shell.preference",
        "content": "用户偏好PowerShell命令",
        "category": "preference",
        "subject": "user.preferred_shell",
        "value": "PowerShell",
        "source_quote": text,
    }
    saved = store.put({**data, "source_conv_id": first["id"]}, automatic=True, epoch=store.options()["epoch"])
    store.put({**data, "source_conv_id": second["id"]}, automatic=True, epoch=store.options()["epoch"])
    with store.connect() as conn:
        assert len(store.fact_sources.live(saved["id"], conn)) == 2
    db.delete_conversation(first["id"])
    store.conversation_deleted(first["id"])
    assert store.list()[0]["source_valid"] and store.list()[0]["enabled"]
    db.delete_conversation(second["id"])
    store.conversation_deleted(second["id"])
    assert not store.list()[0]["enabled"]
    assert not any(d[1] == "memory" for d in store.engine.index.sync_documents())


def test_editing_user_evidence_removes_auto_fact_from_retrieval(memory):
    db, store = memory
    conv = discussion(db, [{"role": "user", "content": "我使用Windows 11系统。"}])
    saved = store.put(
        {
            "content": "用户使用Windows 11",
            "subject": "user.os",
            "value": "Windows 11",
            "source_conv_id": conv["id"],
            "source_quote": conv["messages"][0]["content"],
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )
    assert saved and store.list()[0]["source_valid"]
    conv["messages"][0]["content"] = "这不是我的实际系统，只是一个假设。"
    db.save_conversation(conv)
    assert not store.list()[0]["source_valid"]
    assert store.overviews.get("global")["coverage"]["saved_fact_count"] == 0


def test_pause_survives_job_object_restart_and_recovery_never_scans(memory):
    db, store = memory
    conv = discussion(db)
    jobs = MemoryJobs(store, lambda *args, **kwargs: ({"episodes": []}, {}))
    jobs.enqueue([conv["id"]])
    jobs.pause()
    restarted = MemoryJobs(store, lambda *args, **kwargs: pytest.fail("Paused model invoked"))
    with patch.object(restarted, "start") as start:
        restarted.recover()
        start.assert_not_called()
    assert not restarted.start() and store.options()["discussion_paused"]
    assert restarted.list()[0]["status"] == "paused"


def test_long_user_statement_is_split_without_losing_tail_or_attachment_body(memory):
    text = "技术问题详细讨论" * 2000 + "以后请优先使用PowerShell。"
    pieces = learning_statements([{"role": "user", "content": text}])
    assert len(pieces) > 2 and "".join(pieces) == text and "PowerShell" in pieces[-1]
    assert not learning_statements(
        [{"role": "user", "content": [{"type": "text", "text": text, "_attachment": "file"}]}]
    )


def test_mixed_assistant_suggestion_cannot_become_tool_verified(memory):
    db, store = memory
    conv = discussion(
        db,
        [
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "建议修复后应该成功。"},
                    {"type": "tool_result", "tool_use_id": "c1", "content": "实际执行失败：exit code 1"},
                ],
            }
        ],
    )
    rows = source_rows(conv)
    assert {r["evidence_role"] for r in rows} == {"assistant", "tool"}
    proposed = {
        "topic": "运行验证",
        "confirmed_outcome": {
            "text": "已成功",
            "kind": "tool_verified",
            "source_ids": [rows[0]["source_id"]],
            "quote": rows[0]["text"],
        },
    }
    with pytest.raises(ValueError):
        store.episodes.normalize(proposed, rows)


def test_tool_temporal_filter_is_applied_before_topk_selection(memory):
    db, store = memory
    old = discussion(db)
    identity = persist(store, old)
    with store.connect() as conn:
        payload = json.loads(conn.execute("SELECT payload FROM memory_episodes WHERE id=?", (identity,)).fetchone()[0])
        payload.update(period_start="2025-01-01", period_end="2025-03-01")
        conn.execute("UPDATE memory_episodes SET payload=? WHERE id=?", (json.dumps(payload), identity))
    target = discussion(db, [{"role": "user", "content": "当前聊天"}])
    _, past = store.engine.retrieve(target["id"], "PHP", period=("2025-01-01", "2025-12-31"))
    assert identity in {e["id"] for e in past["episodes"]}
    _, present = store.engine.retrieve(target["id"], "PHP", period=("2026-01-01", "2026-12-31"))
    assert identity not in {e["id"] for e in present["episodes"]}
    session = MemoryToolSession(store, target["id"], threading.Event())
    session.begin_round()
    result = json.loads(
        session.execute(
            "search_memory",
            {"query": "PHP", "start_date": "2025-01-01", "end_date": "2025-12-31", "types": ["episodes"]},
        )
    )
    assert result["success"] and result["data"]["results"]["episodes"]


def test_append_only_jobs_reuse_successful_prefix_and_edits_restart(memory):
    db, store = memory
    conv = discussion(db)
    batches = []

    def request(system, payload, abort, **kwargs):
        batches.append(payload["messages"])
        return {"episodes": []}, {}

    jobs = MemoryJobs(store, request)
    jobs.enqueue([conv["id"]])
    assert jobs.step()
    previous_total = len(batches[0])
    assert jobs.preview([conv["id"]])["estimated_requests"] == 0
    conv["messages"].append({"role": "user", "content": "继续讨论 PHP 的新方案，测试仍失败。"})
    db.save_conversation(conv)
    preview = jobs.enqueue([conv["id"]])
    assert preview["conversations"][0]["processed_units"] == previous_total
    assert jobs.step() and len(batches[-1]) == 1
    assert "测试仍失败" in batches[-1][0]["text"]
    conv["messages"][0]["content"] = "原始问题已编辑，必须重新整理。"
    db.save_conversation(conv)
    assert jobs.preview([conv["id"]])["conversations"][0]["processed_units"] == 0


def test_automatic_new_turn_never_backfills_old_unprocessed_history(memory):
    db, store = memory
    conv = discussion(db)
    conv["messages"].extend(
        [
            {"role": "user", "content": "今天新增 Docker 网络问题"},
            {"role": "assistant", "content": "今天提出新的 Docker 方案"},
        ]
    )
    db.save_conversation(conv)
    batches = []
    jobs = MemoryJobs(
        store, lambda system, payload, abort, **kwargs: (batches.append(payload["messages"]) or {"episodes": []}, {})
    )
    jobs.enqueue([conv["id"]], automatic_from=3)
    assert jobs.list()[0]["cursor"] == 0 and jobs.list()[0]["total"] == 2
    assert jobs.step() and len(batches[0]) == 2
    assert all("Docker" in r["text"] for r in batches[0])
    assert jobs.preview([conv["id"]])["conversations"][0]["processed_units"] == 0
    # A user explicitly selecting this chat subsequently authorizes the full original range.
    jobs.enqueue([conv["id"]])
    assert jobs.step() and len(batches[1]) == 5
    assert any("PHP" in r["text"] for r in batches[1])


def test_message_dates_survive_metadata_and_append_saves(memory):
    db, store = memory
    conv = discussion(db, [{"role": "user", "content": "过去的问题", "created_at": "2025-01-01T12:00:00+00:00"}])
    loaded = db.load_conversation(conv["id"])
    assert loaded["messages"][0]["created_at"].startswith("2025-01-01")
    conv["title"] = "修改标题"
    conv["messages"].append({"role": "assistant", "content": "今天的方案"})
    db.save_conversation(conv)
    assert db.load_conversation(conv["id"])["messages"][0]["created_at"].startswith("2025-01-01")


def test_forget_fact_blocks_only_supporting_messages_and_long_tail_is_valid(memory):
    db, store = memory
    text = "讨论背景" * 2100 + "以后命令请使用PowerShell。"
    conv = discussion(
        db, [{"role": "user", "content": text}, {"role": "user", "content": "另外研究 Docker 网络隔离。"}]
    )
    data = {
        "content": "用户偏好PowerShell命令",
        "source_conv_id": conv["id"],
        "source_quote": "以后命令请使用PowerShell。",
        "subject": "user.shell",
        "value": "PowerShell",
    }
    saved = store.put(data, automatic=True, epoch=store.options()["epoch"])
    assert saved and store.list()[0]["source_valid"]
    store.forget(saved["id"])
    assert not store.privacy(conv["id"])["exclude_history"]
    assert not store.put(data, automatic=True, epoch=store.options()["epoch"])
    assert [r["text"] for r in store.episodes.sources(store.episodes.conversation(conv["id"]))] == [
        "另外研究 Docker 网络隔离。"
    ]


def test_consolidation_keeps_topics_not_offered_to_model(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    directory = store.overviews.directory("global", [])
    assert any(identity in g["episode_ids"] for g in directory["groups"])


def test_cancelled_embedding_returns_without_late_cache(memory):
    import time

    from test_memory_hybrid import configure

    db, store = memory
    store.put({"content": "用户学习 PHP"})
    provider = configure(store)
    store.engine.index.build()
    started, release, abort = threading.Event(), threading.Event(), threading.Event()
    original = provider.embed

    def slow(texts, query=False):
        if query:
            started.set()
            release.wait(5)
        return original(texts, query=query)

    provider.embed = slow
    results = []
    thread = threading.Thread(
        target=lambda: results.append(
            store.engine.index.search("特殊查询", store.engine.index.sync_documents(), 5, False, abort=abort)
        )
    )
    thread.start()
    assert started.wait(2)
    abort.set()
    thread.join(0.5)
    assert not thread.is_alive() and "取消" in results[0][1]
    release.set()
    time.sleep(0.1)
    with store.connect() as conn:
        assert not conn.execute("SELECT 1 FROM memory_embedding_cache WHERE cache_key LIKE 'q:%'").fetchone()


def test_text_plan_denial_skips_search_and_retains_usage(memory):
    from types import SimpleNamespace

    db, store = memory
    conv = discussion(db)
    session = MemoryToolSession(store, conv["id"], threading.Event())
    with (
        patch("claude_chat.memory_jobs.request_json", return_value=({"need_memory": False}, {"input_tokens": 7})),
        patch.object(session, "execute", side_effect=AssertionError("Denied plan must not search")),
    ):
        assert session.plan(SimpleNamespace(), "你好", []) == ""
    assert session.extra_usage == {"input_tokens": 7}


@pytest.mark.parametrize("phase", ["index", "query"])
def test_source_edit_during_embedding_cannot_republish_old_content(memory, phase):
    from test_memory_hybrid import configure

    db, store = memory
    source = discussion(db, [{"role": "user", "content": "我研究 PHP 反序列化问题。"}])
    target = discussion(db, [{"role": "user", "content": "其他当前问题"}])
    store.put(
        {
            "content": "用户研究 PHP 反序列化",
            "source_conv_id": source["id"],
            "source_quote": source["messages"][0]["content"],
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )
    provider = configure(store)
    if phase == "query":
        store.engine.index.build()

    def edit():
        source["messages"][0]["content"] = "原始信息无效，已撤回。"
        db.save_conversation(source)

    provider.hook = edit
    if phase == "index":
        store.engine.index.build()
        with store.connect() as conn:
            assert not conn.execute(
                "SELECT 1 FROM memory_vectors v JOIN memory_documents d ON d.id=v.document_id "
                "WHERE d.text LIKE '%反序列化%'"
            ).fetchone()
    else:
        prompt, context = store.retrieve(target["id"], "PHP 反序列化")
        assert prompt == "" and not context["memories"] and not context["history"]


def test_context_capacity_reserves_model_output_and_shared_memory_budget(memory):
    from claude_chat.memory_hybrid import context_memory_budget

    db, store = memory
    conv = discussion(db)
    store.set_options({"budget_chars": 6000})
    budget, detail = context_memory_budget(
        [{"role": "user", "content": "背景" * 1000}], "助手设定", {"max_context": 8000}, 3000, 6000
    )
    assert 0 < budget < 6000 and detail["limited_by_model_context"]
    prompt, context = store.engine.retrieve(conv["id"], "PHP", budget_chars=budget * 2 // 3)
    session = MemoryToolSession(store, conv["id"], threading.Event(), used_chars=len(prompt), budget_chars=budget)
    assert session.begin_round()
    result = session.execute("memory_overview", {})
    assert len(prompt) + len(result) + session.remaining == budget
    assert context_memory_budget(conv["messages"], "", {"max_context": 1000}, 2000, 6000)[0] == 0
    assert context_memory_budget(conv["messages"], "", {}, 2000, 6000)[0] == 6000


def test_fact_extending_multiple_sources_prunes_deleted_clause(memory):
    db, store = memory
    a = discussion(db, [{"role": "user", "content": "项目涉及 PHP。"}])
    b = discussion(db, [{"role": "user", "content": "项目还涉及 Docker。"}])
    data = {"key": "project.stack", "category": "project", "subject": "project.stack", "confidence": 0.95}
    first = store.put(
        {
            **data,
            "content": "项目涉及 PHP",
            "value": "PHP",
            "source_conv_id": a["id"],
            "source_quote": "项目涉及 PHP。",
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )
    second = store.put(
        {
            **data,
            "content": "项目还涉及 Docker",
            "value": "Docker",
            "relation": "EXTEND",
            "source_conv_id": b["id"],
            "source_quote": "项目还涉及 Docker。",
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )
    assert first["id"] == second["id"] and "；" in second["content"] and store.list()[0]["source_valid"]
    db.delete_conversation(a["id"])
    store.conversation_deleted(a["id"])
    remaining = store.list()[0]
    assert remaining["source_valid"] and remaining["content"] == "项目还涉及 Docker"
    assert json.loads(remaining["value_json"]) == "Docker"

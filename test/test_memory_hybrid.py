"""Hybrid-memory acceptance tests: no real keys, user data, network or paid models."""

import json
import math
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from claude_chat.api_bridge import WebAPI
from claude_chat.db import DatabaseManager
from claude_chat.memory_embeddings import EmbeddingProvider, normalize, pack, unpack
from claude_chat.memory_hybrid import rank, route
from claude_chat.memory_resolver import typed_fields
from claude_chat.memory_store import MemoryStore


@pytest.fixture
def memory(tmp_path):
    with (
        patch("claude_chat.db.DB_PATH", tmp_path / "hybrid.db"),
        patch("claude_chat.db.CONVERSATIONS_DIR", tmp_path / "none"),
    ):
        db = DatabaseManager()
        store = MemoryStore(db)
        yield db, store


def conversation(db, text):
    conv = db.new_conversation()
    conv["messages"] = [{"role": "user", "content": text}]
    db.save_conversation(conv)
    return conv["id"]


class FakeEmbedding:
    signature = "deterministic-test-embedding-v1"

    def __init__(self):
        self.calls = []
        self.fail = False
        self.hook = None

    def embed(self, texts, query=False):
        self.calls.append((texts, query))
        if self.hook:
            self.hook()
        if self.fail:
            raise ValueError("模拟 Embedding 不可用")
        vectors = []
        for text in texts:
            text = text.casefold()
            axis = (
                0
                if any(x in text for x in ("macos", "苹果电脑", "apple laptop"))
                else 1
                if any(x in text for x in ("powershell", "微软命令行", "windows shell"))
                else 2
                if any(x in text for x in ("反序列化", "unserialize", "object hydration"))
                else 3
            )
            vectors.append([float(i == axis) for i in range(4)])
        return vectors


def configure(store):
    store.set_options({"embedding_platform": "gemini", "embedding_model": "test-embedding"})
    provider = FakeEmbedding()
    store.engine.index.provider_factory = lambda options: provider
    return provider


def test_semantic_synonyms_top_k_and_query_cache(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    wanted = store.put({"content": "用户使用 macOS", "category": "profile"})
    for n in range(15):
        store.put({"content": f"我在维护番茄菜谱 {n}", "category": "project"})
    provider = configure(store)
    store.set_options({"top_k": 2})
    store.engine.index.build()
    before = len(provider.calls)
    prompt, context = store.retrieve(cid, "苹果电脑上怎么开发？")
    assert wanted["id"] in [m["id"] for m in context["memories"]]
    assert "用户使用 macOS" in prompt
    assert context["retrieval"] == "vector+keyword"
    assert len(context["memories"]) + len(context["history"]) <= 2
    assert provider.calls[before:] == [(["苹果电脑上怎么开发？"], True)]
    store.retrieve(cid, "苹果电脑上怎么开发？")
    assert len(provider.calls) == before + 1


@pytest.mark.parametrize(
    "query", ["你记得什么", "你好，你还记得我吗？", "我们之前聊过什么？", "What do you remember about me?"]
)
def test_memory_overview_covers_real_topics_without_matching_inventory_questions(memory, query):
    db, store = memory
    sources = []
    for topic in (
        "PHP 反序列化与对象生命周期",
        "Python 聊天软件开发与记忆检索",
        "Docker 沙盒部署与网络隔离",
        "JavaScript 页面配色和移动适配",
    ):
        cid = conversation(db, topic + "：我正在研究相关实现，希望分析具体流程和解决方案。")
        conv = db.load_conversation(cid)
        conv["messages"].append({"role": "user", "content": "你好，你记得什么？"})
        db.save_conversation(conv)
        sources.append(cid)
    private = conversation(db, "私密工作，不能供历史参考。")
    store.set_privacy(private, {"exclude_history": True})
    outside = conversation(db, "家庭项目：厨房改造与收纳空间设计。")
    store.set_privacy(outside, {"scope": "home"})
    row = store.put({"content": "用户长期研究 Rust 编译器", "category": "project"})
    target = conversation(db, query)
    store.engine.index.search = Mock(side_effect=AssertionError("An inventory is not an embedding query"))
    prompt, context = store.retrieve(target, query)
    assert context["router"]["overview"]
    assert {h["conversation_id"] for h in context["history"]} == set(sources)
    assert row["id"] in {m["id"] for m in context["memories"]}
    assert all("你记得什么" not in h["excerpt"] for h in context["history"])
    assert context["inventory"] == {"saved_memories_available": 1, "history_conversations_in_search_window": 4}
    assert "子集" in prompt and "memory_overview" in prompt
    assert context["chars"] == len(prompt) <= store.options()["budget_chars"]
    store.engine.index.search.assert_not_called()


def test_overview_budget_empty_inventory_and_history_switch(memory):
    db, store = memory
    target = conversation(db, "你记得什么")
    prompt, context = store.retrieve(target, "你记得什么")
    assert "saved_memories_available" in prompt and not context["history"]
    for n in range(8):
        conversation(db, f"历史项目 {n}：" + "我正在学习计算机系统和算法设计。" * 60)
        store.put({"content": f"用户长期研究主题 {n}", "category": "project"})
    store.set_options({"budget_chars": 1000, "top_k": 4})
    prompt, context = store.retrieve(target, "你记得什么")
    assert len(prompt) == context["chars"] <= 1000
    assert len(context["memories"]) + len(context["history"]) <= 4
    store.set_options({"history_enabled": False})
    _, context = store.retrieve(target, "你记得什么")
    assert not context["history"] and context["inventory"]["history_conversations_in_search_window"] == 0
    store.set_privacy(target, {"temporary": True})
    assert store.retrieve(target, "你记得什么")[0] == ""


def test_history_uses_topic_title_but_not_empty_memory_questions(memory):
    db, store = memory
    source = conversation(db, "我遇到属性值变为空的情况，请帮助分析对象转换的实现细节。")
    conv = db.load_conversation(source)
    conv["title"] = "PHP 反序列化 null 字节"
    conv["messages"].append({"role": "user", "content": "你好，你记得什么？"})
    db.save_conversation(conv)
    db.update_conversation_title(source, "PHP 反序列化 null 字节", "manual")
    target = conversation(db, "新会话")
    _, context = store.retrieve(target, "PHP 反序列化")
    assert context["history"][0]["conversation_id"] == source
    assert "对象转换" in context["history"][0]["excerpt"]
    assert not any(d[1] == "history" and "你记得什么" in d[4] for d in store.engine.index.sync_documents())


def test_incremental_index_unchanged_edit_and_model_migration(memory):
    _, store = memory
    row = store.put({"content": "用户使用 macOS"})
    provider = configure(store)
    store.engine.index.build()
    calls = len(provider.calls)
    store.engine.index.build()
    assert len(provider.calls) == calls
    store.put({"id": row["id"], "content": "用户优先使用 PowerShell"})
    store.engine.index.build()
    assert len(provider.calls) == calls + 1
    assert provider.calls[-1][0] == ["用户优先使用 PowerShell"]
    store.set_options({"embedding_dimensions": 3})
    assert store.engine.index.status()["indexed"] == 0


def test_temporal_automatic_update_and_manual_conflict(memory):
    db, store = memory
    cid = conversation(db, "我已换 Windows 11")
    first = store.put(
        {"content": "用户使用 Windows 10", "key": "os", "source_conv_id": cid},
        automatic=True,
        epoch=store.options().get("epoch", 0),
    )
    updated = store.put(
        {
            "content": "用户使用 Windows 11",
            "subject": "user.os",
            "value": "Windows 11",
            "source_conv_id": cid,
            "confidence": 0.96,
        },
        automatic=True,
        epoch=store.options().get("epoch", 0),
    )
    assert updated["id"] == first["id"] and updated["version"] == 2
    assert json.loads(updated["value_json"]) == "Windows 11"
    with store.connect() as conn:
        old = dict(conn.execute("SELECT * FROM memory_versions").fetchone())
        assert old["value_json"] == '"Windows 10"'
        assert old["valid_until"] == updated["valid_from"]
    store.put({**updated, "enabled": True, "pinned": False})
    conv = db.load_conversation(cid)
    conv["messages"].append({"role": "user", "content": "我已换 Windows 10"})
    db.save_conversation(conv)
    store.put(
        {
            "content": "用户使用 Windows 10",
            "subject": "user.os",
            "source_conv_id": cid,
            "source_quote": "我已换 Windows 10",
            "confidence": 0.98,
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )
    conflict = store.conflicts()[0]
    assert store.list()[0]["value_json"] == '"Windows 11"'
    store.resolve_conflict(conflict["id"], True)
    assert store.list()[0]["value_json"] == '"Windows 10"'
    assert store.list()[0]["version"] == 3


def test_structured_value_change_creates_version_even_same_content(memory):
    _, store = memory
    row = store.put({"content": "我的系统", "subject": "user.os", "value": "Windows 10"})
    row = store.put({"id": row["id"], "content": "我的系统", "value": "Windows 11"})
    assert row["version"] == 2


def test_low_confidence_conflict_reject_and_stale_version(memory):
    db, store = memory
    cid = conversation(db, "我偏好 PowerShell")
    row = store.put({"content": "用户优先使用 PowerShell", "key": "shell"})
    data = {"content": "用户优先使用 bash", "key": "shell", "confidence": 0.6, "source_conv_id": cid}
    store.put(data, automatic=True, epoch=store.options()["epoch"])
    conflict = store.conflicts()[0]
    store.put({"id": row["id"], "content": "用户优先使用 zsh"})
    with pytest.raises(ValueError, match="原记忆已改变"):
        store.resolve_conflict(conflict["id"], True)
    store.resolve_conflict(conflict["id"], False)
    store.put(data, automatic=True, epoch=store.options()["epoch"])
    assert not store.conflicts()


def test_null_target_delete_never_clears_memories(memory):
    db, store = memory
    cid = conversation(db, "请忘记不知道的事实")
    store.put({"content": "必须保留的记忆"})
    store.put(
        {"content": "不存在的事实", "source_conv_id": cid, "confidence": 0.4, "operation": "DELETE"},
        automatic=True,
        epoch=store.options()["epoch"],
    )
    assert not store.conflicts()
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO memory_conflicts (id,candidate,relation,created_at) VALUES (?,?,?,?)",
            ("bad", json.dumps({"operation": "DELETE"}), "NEW", "2026-01-01"),
        )
    with pytest.raises(ValueError, match="现存记忆"):
        store.resolve_conflict("bad", True)
    assert len(store.list()) == 1


def test_confirmed_delete_removes_only_target_atomically(memory):
    db, store = memory
    cid = conversation(db, "请忘记我的操作系统")
    row = store.put({"content": "用户使用 Windows 11", "key": "os"})
    keep = store.put({"content": "喜欢茶"})
    store.put(
        {"content": row["content"], "key": "os", "source_conv_id": cid, "operation": "DELETE"},
        automatic=True,
        epoch=store.options()["epoch"],
    )
    store.resolve_conflict(store.conflicts()[0]["id"], True)
    assert [m["id"] for m in store.list()] == [keep["id"]]


@pytest.mark.parametrize("action", ["forget", "disable", "privacy", "delete_conversation", "clear"])
def test_privacy_purges_all_derived_data_and_sources(memory, action):
    db, store = memory
    cid = conversation(db, "我长期研究 PHP 反序列化")
    target = conversation(db, "另一个会话")
    row = store.put(
        {
            "content": "用户研究 PHP 反序列化",
            "source_conv_id": cid,
            "subject": "user.study",
            "source_quote": "我长期研究 PHP 反序列化",
        }
    )
    provider = configure(store)
    store.engine.index.build()
    store.retrieve(target, "object hydration")
    with store.connect() as conn:
        conn.execute("INSERT INTO memory_summaries VALUES (?,?,?,?,?)", (cid, "hash", "private summary", 10, "2026"))
    assert store.engine.index.status()["indexed"]
    if action == "forget":
        store.forget(row["id"])
    elif action == "disable":
        store.set_options({"enabled": False})
    elif action == "privacy":
        store.set_privacy(cid, {"exclude_history": True})
    elif action == "delete_conversation":
        db.delete_conversation(cid)
        store.conversation_deleted(cid)
    else:
        store.forget()
    with store.connect() as conn:
        for table in ("memory_vectors", "memory_documents", "memory_summaries", "memory_embedding_cache"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    if action in {"forget", "clear", "privacy", "delete_conversation"}:
        calls = len(provider.calls)
        store.engine.index.build()
        assert not any("我长期研究" in text for texts, _ in provider.calls[calls:] for text in texts)


def test_index_epoch_change_during_request_cannot_restore_forgotten_data(memory):
    _, store = memory
    row = store.put({"content": "用户使用 macOS"})
    provider = configure(store)
    provider.hook = lambda: store.forget(row["id"])
    store.engine.index.build()
    assert store.engine.index.status()["indexed"] == 0
    assert not store.list()


def test_query_privacy_change_cannot_restore_cache_or_context(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    store.put({"content": "用户使用 macOS"})
    provider = configure(store)
    store.engine.index.build()
    provider.hook = lambda: store.set_privacy(cid, {"memory_off": True})
    assert not store.retrieve(cid, "苹果电脑")[0]
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_context").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM memory_embedding_cache").fetchone()[0] == 0


def test_failure_pause_retry_and_dimension_guard(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    store.put({"content": "用户使用 macOS"})
    provider = configure(store)
    provider.fail = True
    store.engine.index.build()
    assert store.engine.index.status()["status"] == "error"
    assert store.retrieve(cid, "macOS")[1]["retrieval"] == "keyword"
    provider.fail = False
    store.engine.index.cancel.set()
    store.engine.index.build()
    assert store.engine.index.status()["indexed"] == 0
    store.engine.index.cancel.clear()
    store.engine.index.build()
    with store.connect() as conn:
        conn.execute("UPDATE memory_vectors SET dimensions=3,vector=?", (pack([1, 0, 0]),))
    context = store.retrieve(cid, "macOS")[1]
    assert context["retrieval"] == "keyword" and "维度" in context["fallback"]


def test_historical_versions_current_profile_and_budget(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    row = store.put({"content": "用户使用 Windows 10", "key": "os"})
    store.put({"id": row["id"], "content": "用户使用 Windows 11"})
    _, current = store.retrieve(cid, "Windows 系统")
    assert all(not m["historical"] and "Windows 11" in m["content"] for m in current["memories"])
    _, history = store.retrieve(cid, "以前 Windows 系统")
    assert any(m["historical"] and "Windows 10" in m["content"] for m in history["memories"])
    store.set_options({"budget_chars": 1000})
    prompt, context = store.retrieve(cid, "Windows 系统")
    assert len(prompt) <= 1000 and context["chars"] <= 1000


def test_recency_decay_frequency_and_pin_are_explainable():
    options = {"half_life_days": 10}
    stamp = datetime.now(timezone.utc)
    old = {"updated_at": (stamp - timedelta(days=10)).isoformat(), "importance": 0.8}
    recent = {**old, "updated_at": stamp.isoformat(), "use_count": 10, "pinned": True}
    old_score, factors = rank(old, 0.7, 0.2, options)
    new_score, new_factors = rank(recent, 0.7, 0.2, options)
    assert math.isclose(factors["recency"], 0.5, abs_tol=0.0001)
    assert new_score > old_score and new_factors["frequency"] > 0


def test_summary_preserves_roles_invalidates_after_edit_and_skips_complex_context(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    messages = [{"role": "user" if n % 2 == 0 else "assistant", "content": f"plain text {n}"} for n in range(30)]
    shortened, summary = store.engine.summarize_recent(cid, messages)
    assert shortened[-1] == messages[-1] and shortened[0]["role"] == "user"
    assert '"role": "assistant"' in summary and '"role": "user"' in summary
    messages[0]["content"] = "edited user statement"
    assert "edited user statement" in store.engine.summarize_recent(cid, messages)[1]
    messages[1]["content"] = "```python\nprint('keep code')\n```"
    assert store.engine.summarize_recent(cid, messages) == (messages, "")
    store.set_privacy(cid, {"temporary": True})
    assert store.engine.summarize_recent(cid, messages) == (messages, "")


def test_openai_embedding_rest_order_dimensions_proxy_and_secret_free_error():
    options = {
        "embedding_platform": "custom:test",
        "embedding_model": "embedding-test",
        "embedding_dimensions": 2,
        "embedding_local_path": "",
    }
    mapped = {"api_key": "offline-secret", "api_url": "https://example.invalid/v1/chat/completions"}
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {
            "data": [
                {"index": 1, "embedding": [0, 2]},
                {"index": 0, "embedding": [3, 0]},
            ]
        },
    )
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    client.post.return_value = response
    with (
        patch("claude_chat.platform_params.PlatformParamMapper.map_params", return_value=mapped),
        patch(
            "claude_chat.clients.base.build_http_client",
            return_value=client,
        ),
    ):
        provider = EmbeddingProvider(options, {"proxy_mode": "none"})
        assert provider.embed(["first", "second"]) == [[1, 0], [0, 1]]
        assert client.post.call_args.args[0] == "https://example.invalid/v1/embeddings"
        assert client.post.call_args.kwargs["json"]["dimensions"] == 2
        response.status_code = 403
        with pytest.raises(ValueError, match="HTTP 403") as error:
            provider.embed(["private text"])
        assert "offline-secret" not in str(error.value)


@pytest.mark.parametrize("model,count", [("gemini-embedding-001", 1), ("gemini-embedding-2", 2)])
def test_gemini_embedding_task_types_and_aggregate_semantics(model, count):
    options = {
        "embedding_platform": "gemini",
        "embedding_model": model,
        "embedding_dimensions": 2,
        "embedding_local_path": "",
    }
    client = Mock()
    client.__enter__ = Mock(return_value=client)
    client.__exit__ = Mock(return_value=False)
    if count == 1:
        client.models.embed_content.return_value = SimpleNamespace(
            embeddings=[SimpleNamespace(values=[1, 0]), SimpleNamespace(values=[0, 1])]
        )
    else:
        client.models.embed_content.return_value = SimpleNamespace(embeddings=[SimpleNamespace(values=[1, 0])])
    with patch("google.genai.Client", return_value=client):
        provider = EmbeddingProvider(options, {"gemini_api_key": "offline-key"})
        assert len(provider.embed(["first", "second"])) == 2
    assert client.models.embed_content.call_count == count
    config = client.models.embed_content.call_args.kwargs["config"]
    assert config.task_type == ("RETRIEVAL_DOCUMENT" if count == 1 else None)
    if count == 2:
        assert isinstance(client.models.embed_content.call_args.kwargs["contents"], str)


@pytest.mark.parametrize("vector", [[], [0, 0], [float("nan"), 1], [float("inf"), 1], [True, 1]])
def test_invalid_vectors_rejected(vector):
    with pytest.raises(ValueError):
        normalize(vector)


def test_float32_round_trip():
    vector = normalize([1, 2, 3])
    assert unpack(pack(vector), 3) == pytest.approx(vector, abs=1e-6)
    with pytest.raises(ValueError):
        unpack(b"bad", 3)


def test_additive_migration_preserves_existing_ids_origins_and_quotes(memory):
    db, store = memory
    row = store.put({"content": "我使用 Windows 10", "key": "os", "source_quote": "原始用户陈述"})
    with store.connect() as conn:
        conn.execute("UPDATE memories SET subject='',value_json='null'")
    migrated = MemoryStore(db).list()[0]
    assert migrated["id"] == row["id"] and migrated["origin"] == "manual"
    assert migrated["source_quote"] == "原始用户陈述" and migrated["subject"] == "user.os"
    MemoryStore(db)
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_facts").fetchone()[0] == 1


def test_service_advanced_endpoints_and_validation(memory):
    db, store = memory
    cfg = {"active_platform": "claude", "model": "test"}
    api = WebAPI(
        SimpleNamespace(
            lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg, get=cfg.get)
        )
    )
    api.memory_operation("save", {"content": "用户使用 Windows 11", "key": "os"})
    assert api.memory_operation("profile")["profile"][0]["subject"] == "user.os"
    assert api.memory_operation("audit")["audit"]
    assert api.memory_operation("index_status")["index"]["configured"] is False
    assert not api.memory_operation("index_start")["success"]
    assert not api.memory_operation("options", {"embedding_platform": "claude"})["success"]
    assert not api.memory_operation("options", {"top_k": True})["success"]
    assert not api.memory_operation("save", {"content": "bad", "confidence": 2})["success"]


def test_index_start_exception_releases_gate(memory):
    _, store = memory
    with (
        patch.object(store.engine.index, "_background"),
        patch("threading.Thread.start", side_effect=RuntimeError("failed start")),
    ):
        with pytest.raises(RuntimeError):
            store.engine.index.start()
    assert not store.engine.index.gate.locked()


@pytest.mark.parametrize("query", ["我去年用的系统", "我之前的系统", "last year operating system", "2024 年用的系统"])
def test_historical_router_recognizes_dates(query):
    assert route(query, {"history_enabled": True})["historical"]


def test_change_sentence_does_not_remember_old_os():
    assert typed_fields({"content": "我从 Windows 10 改用 Windows 11"})["value_json"] == '"Windows 11"'
    assert typed_fields({"content": "我现在用 Windows 11，以前 Windows 10"})["value_json"] == '"Windows 11"'
    assert typed_fields({"content": "我比较 Windows 10 和 Windows 11"})["value_json"] != '"Windows 10"'


def test_typed_profile_and_project_scope_without_embedding(memory):
    db, store = memory
    cid = conversation(db, "普通问题")
    store.put({"content": "用户使用 Windows 11", "category": "profile", "key": "os"})
    project = store.put({"content": "用户优先使用 PowerShell", "category": "preference", "scope": "work"})
    elsewhere = store.put({"content": "使用 bash", "category": "preference", "scope": "home"})
    store.set_privacy(cid, {"scope": "work"})
    context = store.retrieve(cid, "如何列出目录文件")[1]
    assert any(row["subject"] == "user.os" for row in context["memories"])
    assert project["id"] in [row["id"] for row in context["memories"]]
    assert elsewhere["id"] not in [row["id"] for row in context["memories"]]


def test_router_does_not_send_sensitive_queries(memory):
    db, store = memory
    cid = conversation(db, "普通问题")
    store.set_options({"router_model_enabled": True})
    store.engine.router = Mock(side_effect=AssertionError("secret sent"))
    store.retrieve(cid, "以前的 api_key=sk-secret-secret-secret")
    store.engine.router.assert_not_called()


def test_local_provider_instance_reused(memory):
    _, store = memory
    store.set_options(
        {"embedding_platform": "local", "embedding_model": "local-model", "embedding_local_path": "model"}
    )
    assert store.engine._provider(store.options()) is store.engine._provider(store.options())


def test_semantic_conflicts_are_pending_and_same_facts_do_not_duplicate(memory):
    db, store = memory
    cid = conversation(db, "我改用苹果电脑")
    row = store.put({"content": "用户使用 macOS", "category": "project", "key": "computer"})
    configure(store)
    store.engine.index.build()
    candidate = store.engine.resolve_candidate(
        {"content": "用户使用苹果电脑", "category": "project", "source_conv_id": cid}
    )
    assert candidate["key"] == row["fact_key"] and candidate["relation"] == "CONTRADICT"
    store.put(candidate, automatic=True, epoch=store.options()["epoch"])
    assert store.conflicts() and len(store.list()) == 1
    same = store.engine.resolve_candidate(
        {"content": "用户使用苹果电脑", "category": "project", "relation": "SAME", "source_conv_id": cid}
    )
    store.put(same, automatic=True, epoch=store.options()["epoch"])
    assert len(store.list()) == 1


def test_identical_facts_in_separate_scopes_do_not_overwrite_each_other(memory):
    db, store = memory
    cid = conversation(db, "我现在使用 bash")
    global_fact = store.put({"content": "用户优先使用 PowerShell", "key": "shell"})
    work_fact = store.put({"content": "用户优先使用 PowerShell", "key": "shell", "scope": "work"})
    assert global_fact["id"] != work_fact["id"]
    store.put(
        {"content": "用户优先使用 bash", "key": work_fact["fact_key"], "scope": "work", "source_conv_id": cid},
        automatic=True,
        epoch=store.options()["epoch"],
    )
    assert len(store.list()) == 2
    assert store.conflicts()[0]["memory_id"] == work_fact["id"]


def test_export_import_includes_versions_without_forged_provenance(memory):
    _, store = memory
    row = store.put({"content": "用户使用 Windows 10", "key": "os", "source_quote": "old evidence"})
    store.put({"id": row["id"], "content": "用户使用 Windows 11"})
    exported = store.export_data()
    assert exported["version"] == 3 and exported["memories"][0]["history_versions"]
    store.forget()
    assert store.import_data(exported) == 1
    imported = store.list()[0]
    assert imported["version"] == 2 and imported["origin"] == "manual" and not imported["source_conv_id"]
    with store.connect() as conn:
        versions = conn.execute("SELECT * FROM memory_versions").fetchall()
        assert len(versions) == 1 and not versions[0]["source_conv_id"] and not versions[0]["source_quote"]
    assert store.import_data(exported) == 0  # Re-import skips an existing field unless explicitly selected.
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_versions").fetchone()[0] == 1


def test_version_import_validation_rolls_back_whole_batch(memory):
    _, store = memory
    original = store.put({"content": "必须保留"})
    with pytest.raises(ValueError, match="有效期"):
        store.import_data(
            {
                "version": 2,
                "memories": [
                    {
                        "content": "无效版本",
                        "history_versions": [
                            {"version": 1, "content": "旧内容", "valid_from": "bad", "valid_until": "bad"},
                        ],
                    }
                ],
            }
        )
    assert store.list()[0]["id"] == original["id"] and len(store.list()) == 1


def test_import_restores_current_validity_and_profile(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    row = store.put({"content": "用户使用 Windows 11", "key": "os"})
    with store.connect() as conn:
        conn.execute("UPDATE memories SET valid_from='2024-01-01T00:00:00+00:00' WHERE id=?", (row["id"],))
    exported = store.export_data()
    store.forget()
    store.import_data(exported)
    assert store.list()[0]["valid_from"].startswith("2024")
    with store.connect() as conn:
        assert conn.execute("SELECT valid_from FROM memory_facts").fetchone()[0].startswith("2024")
    assert store.retrieve(cid, "2025-01-01 用的系统")[1]["memories"]


def test_archival_profile_retrieval_works_without_vector_or_chat_history(memory):
    db, store = memory
    cid = conversation(db, "新会话")
    row = store.put({"content": "用户使用 Windows 10", "key": "os"})
    with store.connect() as conn:
        conn.execute("UPDATE memories SET valid_from='2024-01-01T00:00:00+00:00' WHERE id=?", (row["id"],))
    store.put({"id": row["id"], "content": "用户使用 Windows 11"})
    store.set_options({"history_enabled": False})
    context = store.retrieve(cid, "2025 年我用的操作系统是什么？")[1]
    assert any(m["historical"] and "Windows 10" in m["content"] for m in context["memories"])
    assert all("Windows 11" not in m["content"] for m in context["memories"])
    assert not context["history"]


def test_scope_name_substring_never_allows_cross_project_retrieval(memory):
    db, store = memory
    cid = conversation(db, "home")
    row = store.put({"content": "工作机使用 PowerShell", "category": "preference", "scope": "work"})
    assert row["id"] not in [m["id"] for m in store.retrieve(cid, "workflow 怎么配置？")[1]["memories"]]


def test_background_extraction_inherits_scope_and_filters_existing(memory):
    db, store = memory
    cid = conversation(db, "我长期优先使用 PowerShell")
    store.set_privacy(cid, {"scope": "work"})
    store.put({"content": "只属于家庭项目的背景", "scope": "home"})
    store.put({"content": "api_key=sk-offline-secret-secret"})
    cfg = {"api_key": "offline-key", "active_platform": "claude", "model": "offline-model"}
    api = WebAPI(
        SimpleNamespace(
            lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg, get=cfg.get)
        )
    )
    gate = threading.Lock()
    gate.acquire()

    def stream(*args, **kwargs):
        payload = json.loads(args[3][0]["content"])
        assert payload["conversation_scope"] == "work"
        assert not payload["existing"]
        args[8].put(
            (
                "text",
                json.dumps(
                    {
                        "memories": [
                            {
                                "key": "shell",
                                "category": "preference",
                                "content": "用户优先使用 PowerShell",
                                "scope": "global",
                                "source_quote": "我长期优先使用 PowerShell",
                            }
                        ]
                    }
                ),
            )
        )

    with patch("claude_chat.clients.stream_claude_response", side_effect=stream):
        api._learn_memories(
            store, db.load_conversation(cid), ["我长期优先使用 PowerShell"], 1, store.options()["epoch"], cfg, gate
        )
    row = next(m for m in store.list() if "PowerShell" in m["content"])
    assert row["scope"] == "work" and row["origin"] == "automatic"
    assert not gate.locked()


def test_failed_learning_does_not_advance_processed_message_count(memory):
    db, store = memory
    cid = conversation(db, "我长期学习 Python")
    cfg = {"api_key": "offline-key", "active_platform": "claude", "model": "offline-model"}
    api = WebAPI(
        SimpleNamespace(
            lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg, get=cfg.get)
        )
    )
    gate = threading.Lock()
    gate.acquire()
    with patch("claude_chat.clients.stream_claude_response", side_effect=RuntimeError("offline failure")):
        api._learn_memories(
            store, db.load_conversation(cid), ["我长期学习 Python"], 3, store.options().get("epoch", 0), cfg, gate
        )
    with store.connect() as conn:
        run = conn.execute("SELECT * FROM memory_runs").fetchone()
        assert run["error"] and run["last_count"] == 0


def test_extraction_batches_do_not_skip_earlier_statements_or_overstate_progress(memory):
    db, store = memory
    samples = [
        "我长期使用 PowerShell。" + "开发背景。" * 950,
        "我长期研究 PHP 反序列化。" + "研究背景。" * 950,
        "我偏好中文回答。",
    ]
    cid = conversation(db, samples[0])
    conv = db.load_conversation(cid)
    conv["messages"] = [{"role": "user", "content": s} for s in samples]
    db.save_conversation(conv)
    cfg = {"api_key": "offline-key", "active_platform": "claude", "model": "offline-model"}
    api = WebAPI(
        SimpleNamespace(lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg))
    )
    evidence = []

    def stream(*args, **kwargs):
        evidence.append(json.loads(args[3][0]["content"])["user_statements"])
        args[8].put(("text", '{"memories":[]}'))

    with patch("claude_chat.clients.stream_claude_response", side_effect=stream):
        for batch in (samples, samples[1:]):
            gate = threading.Lock()
            gate.acquire()
            api._learn_memories(store, conv, batch, 3, store.options()["epoch"], cfg, gate)
            assert not gate.locked()
            with store.connect() as conn:
                run = conn.execute("SELECT * FROM memory_runs").fetchone()
                assert run["last_count"] == (1 if len(batch) == 3 else 3)
                assert not run["error"]
    assert evidence == [samples[0], "\n".join(samples[1:])]
    assert all(len(text) <= 8000 for text in evidence)
    result = api.memory_operation("context", {"conv_id": cid})
    assert result["learning"]["pending_messages"] == 0 and result["saved_count"] == 0


def test_manual_learning_rescans_completed_history_and_resumes_partial_batches(memory):
    db, store = memory
    statements = [f"我维护第 {n} 个长期项目" for n in range(6)]
    cid = conversation(db, statements[0])
    conv = db.load_conversation(cid)
    conv["messages"] = [{"role": "user", "content": s} for s in statements]
    db.save_conversation(conv)
    cfg = {"api_key": "offline-key", "active_platform": "claude", "model": "offline-model"}
    api = WebAPI(
        SimpleNamespace(lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg))
    )
    for processed, expected in [(6, statements), (2, statements[2:])]:
        with store.connect() as conn:
            conn.execute("INSERT OR REPLACE INTO memory_runs VALUES (?,?,?,?)", (cid, processed, "", ""))
        with patch("claude_chat.services.memory_service.threading.Thread") as thread:
            thread.return_value.start.side_effect = lambda: api._app._memory_learning_lock.release()
            assert api.schedule_memory_learning(cid, force=True)
            assert thread.call_args.kwargs["args"][2] == expected


def test_source_edit_invalidates_old_extraction_quote(memory):
    db, store = memory
    cid = conversation(db, "我长期使用 PowerShell")
    old = db.load_conversation(cid)
    old["messages"] = [{"role": "user", "content": "我已改用 bash"}]
    db.save_conversation(old)
    assert (
        store.put(
            {"content": "用户优先使用 PowerShell", "source_conv_id": cid, "source_quote": "我长期使用 PowerShell"},
            automatic=True,
            epoch=store.options().get("epoch", 0),
        )
        is None
    )


def test_duplicate_index_start_does_not_cancel_running_job(memory):
    db, store = memory
    configure(store)
    cfg = {}
    api = WebAPI(
        SimpleNamespace(
            lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg, get=cfg.get)
        )
    )
    store.engine.index.gate.acquire()
    epoch = store.options()["epoch"]
    try:
        result = api.memory_operation("index_start")
        assert result["success"] and not result["started"]
        assert store.options()["epoch"] == epoch
        assert not api.memory_operation("index_rebuild")["success"]
    finally:
        store.engine.index.gate.release()


def test_history_recency_accepts_legacy_naive_timestamps():
    options = {"half_life_days": 10}
    stamp = datetime.now() - timedelta(days=10)
    _, components = rank({"updated_at": stamp.isoformat()}, 0.7, 0.2, options)
    assert math.isclose(components["recency"], 0.5, abs_tol=0.0001)


def test_saving_rank_settings_keeps_embedding_index_and_unchanged_epoch(memory):
    _, store = memory
    store.put({"content": "用户使用 macOS"})
    configure(store)
    store.engine.index.build()
    original = store.engine.index.status()["indexed"]
    epoch = store.options()["epoch"]
    store.set_options({"embedding_platform": "gemini", "embedding_model": "test-embedding", "top_k": 5})
    assert store.engine.index.status()["indexed"] == original
    new_epoch = store.options()["epoch"]
    assert new_epoch > epoch
    store.set_options({"top_k": 5})
    assert store.options()["epoch"] == new_epoch


def test_automatic_index_failure_backoff_preserves_manual_retry(memory):
    db, store = memory
    store.put({"content": "用户使用 macOS"})
    provider = configure(store)
    provider.fail = True
    store.engine.index.build()
    cfg = {}
    api = WebAPI(
        SimpleNamespace(
            lock=threading.RLock(), conv_manager=db, _memory_store=store, config=SimpleNamespace(data=cfg, get=cfg.get)
        )
    )
    with patch.object(store.engine.index, "start", return_value=True) as start:
        api._schedule_memory_index(store)
        start.assert_not_called()
        assert api.memory_operation("index_start")["success"]
        start.assert_called_once()


def test_scope_setting_keeps_vectors_but_clears_previous_context(memory):
    db, store = memory
    cid = conversation(db, "new")
    store.put({"content": "用户使用 macOS"})
    configure(store)
    store.engine.index.build()
    store.retrieve(cid, "苹果电脑")
    count = store.engine.index.status()["indexed"]
    store.set_privacy(cid, {"scope": "work"})
    assert store.engine.index.status()["indexed"] == count
    with store.connect() as conn:
        assert not conn.execute("SELECT * FROM memory_context").fetchone()

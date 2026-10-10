"""Source revocation and concurrency regressions; synthetic local data only."""

import json
import math
import time
from contextlib import nullcontext
from unittest.mock import patch

import pytest
from test_memory_discussions import candidate, discussion, memory as shared_memory, persist
from test_memory_hybrid import configure

from claude_chat.memory_embeddings import normalize, pack, unpack
from claude_chat.memory_episodes import message_text, source_rows
from claude_chat.memory_store import user_text


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


def fact(store, conv, content, **changes):
    return store.put(
        {
            "key": "research.history",
            "subject": "research.history",
            "content": content,
            "value": content,
            "source_conv_id": conv["id"],
            "source_quote": conv["messages"][0]["content"],
            **changes,
        },
        automatic=True,
        epoch=store.options()["epoch"],
    )


def test_version_requires_all_original_clause_sources_even_after_manual_edit(memory):
    db, store = memory
    first = discussion(db, [{"role": "user", "content": "项目第一阶段研究 PHP 字节编码。"}])
    second = discussion(db, [{"role": "user", "content": "项目第二阶段研究 Docker 网络。"}])
    third = discussion(db, [{"role": "user", "content": "项目当前阶段研究 SQLite 存储。"}])
    saved = fact(store, first, "研究 PHP 字节编码")
    fact(store, second, "研究 Docker 网络", relation="EXTEND")
    fact(store, third, "研究 SQLite 存储")
    with store.connect() as conn:
        archived = conn.execute(
            "SELECT * FROM memory_versions WHERE memory_id=? AND version=2", (saved["id"],)
        ).fetchone()
        parent = conn.execute("SELECT * FROM memories WHERE id=?", (saved["id"],)).fetchone()
        assert len(json.loads(archived["evidence_json"])) == 2
        assert store.fact_sources.version_valid(archived, parent, conn)
    db.delete_conversation(first["id"])
    store.conversation_deleted(first["id"])
    store.put({"id": saved["id"], "content": "手动独立笔记"})
    with store.connect() as conn:
        parent = conn.execute("SELECT * FROM memories WHERE id=?", (saved["id"],)).fetchone()
        assert not store.fact_sources.version_valid(archived, parent, conn)
    assert not any(d[1] == "version" and "PHP" in d[4] for d in store.engine.index.sync_documents())
    assert not any("PHP" in v["content"] for m in store.export_data()["memories"] for v in m["history_versions"])


@pytest.mark.parametrize("vector", [[1e308, 1e308], [1e-308, 1e-308], [3, 4]])
def test_normalization_stable_at_extreme_scales(vector):
    result = normalize(vector)
    assert all(math.isfinite(v) for v in result)
    assert math.hypot(*result) == pytest.approx(1)
    assert math.hypot(*unpack(pack(result), len(result))) == pytest.approx(1)


@pytest.mark.parametrize("vector", [[0, 0], [float("nan"), 1], [float("inf"), 1], [10**400, 1]])
def test_normalization_rejects_invalid_vectors(vector):
    with pytest.raises(ValueError):
        normalize(vector)


@pytest.mark.parametrize(
    "content",
    [
        "  --- 附件文件: secret.txt\n不应外发的附件正文",
        [{"type": "text", "text": "--- 附件文件: secret.txt\n不应外发的附件正文"}],
    ],
)
def test_legacy_attachment_excluded_from_all_evidence(content):
    message = {"role": "user", "content": content}
    assert not user_text(message)
    assert not message_text(message)
    assert not source_rows({"id": "synthetic", "messages": [message]})


def test_stale_embedding_source_is_checked_before_outbound_request(memory):
    db, store = memory
    conv = discussion(db, [{"role": "user", "content": "项目研究私有的 PHP 字节编码细节。"}])
    provider = configure(store)
    fact(store, conv, "私有 PHP 字节编码研究")
    index = store.engine.index
    sync = index.sync_documents

    def stale_pending():
        docs = sync()
        # Simulate a source edit before index synchronization, bypassing eager invalidation deliberately.
        with db.get_connection() as conn:
            conn.execute(
                "UPDATE messages SET content=? WHERE conversation_id=?", ("来源已经撤回，请忽略旧内容。", conv["id"])
            )
        return docs

    with patch.object(index, "sync_documents", side_effect=stale_pending):
        index.build()
    assert not provider.calls, "Invalid sources must never reach embed()"
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_documents").fetchone()[0] == 0


def test_message_edit_eagerly_discards_pending_index_documents(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    fact(store, conv, "用户研究 PHP 字节编码")
    store.engine.index.sync_documents()
    conv["messages"][0]["content"] = "更正：之前的 PHP 问题描述有误。"
    db.save_conversation(conv)
    with store.connect() as conn:
        assert not conn.execute(
            "SELECT 1 FROM memory_documents WHERE conv_id=? OR ref_id=?", (conv["id"], identity)
        ).fetchone()


def test_stale_episode_editor_fails_without_archiving_or_overwriting(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    changes = candidate(source_rows(conv))
    store.episodes.edit(identity, "global", {**changes, "topic": "较新的人工摘要"}, expected_version=1)
    with pytest.raises(ValueError, match="版本冲突"):
        store.episodes.edit(identity, "global", {**changes, "topic": "过时的草稿"}, expected_version=1)
    assert store.episodes.read(identity, "global")["topic"] == "较新的人工摘要"
    assert len(store.episodes.versions(identity, "global")) == 1


def test_overview_source_changed_after_prompt_generation_is_not_injected(memory):
    db, store = memory
    source = discussion(db, [{"role": "user", "content": "我使用 Windows 11 系统，偏好 PowerShell。"}])
    fact(store, source, "用户使用 Windows 11", category="profile", subject="user.os", value="Windows 11")
    target = discussion(db, [{"role": "user", "content": "你好，介绍一下你自己。"}])
    original = store.overviews.prompt

    def edit_after_directory(*args):
        directory = original(*args)
        source["messages"][0]["content"] = "更正：此前只是虚构环境。"
        db.save_conversation(source)
        return directory

    with patch.object(store.overviews, "prompt", side_effect=edit_after_directory):
        prompt, _ = store.engine.retrieve(target["id"], "你好，介绍一下你自己。")
    assert not prompt


def protocol_messages(name, response=False):
    if response:
        return [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "text",
                        "text": "验证运行结果。",
                        "_responses": {
                            "output": [
                                {"type": "function_call", "call_id": "c1", "name": name},
                                {
                                    "type": "function_call_output",
                                    "call_id": "c1",
                                    "output": "tests passed; exit code 0",
                                },
                            ]
                        },
                    }
                ],
            }
        ]
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": name}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "tests passed; exit code 0"}],
        },
    ]


@pytest.mark.parametrize("response", [False, True])
@pytest.mark.parametrize("name", ["memory_overview", "search_memory", "read_memory_episode", "read_history_excerpt"])
def test_memory_reads_never_become_new_extraction_sources(name, response):
    rows = source_rows({"id": "synthetic", "messages": protocol_messages(name, response)})
    assert not any("tests passed" in row["text"] for row in rows)


@pytest.mark.parametrize("response", [False, True])
@pytest.mark.parametrize("name,verified", [("execute_code", True), ("search_web", False), ("unknown", False)])
def test_tool_verification_requires_paired_execution_call(memory, response, name, verified):
    _, store = memory
    rows = source_rows({"id": "synthetic", "messages": protocol_messages(name, response)})
    proof = next(r for r in rows if r["evidence_role"] == "tool")
    assert proof["tool_name"] == name
    value = {
        "topic": "工具验证",
        "confirmed_outcome": {
            "text": "实际执行通过",
            "kind": "tool_verified",
            "source_ids": [proof["source_id"]],
            "quote": proof["text"],
        },
    }
    result = store.episodes.normalize(value, rows, repair=True)
    assert (result["status"] == "resolved") == verified
    if not verified:
        assert result["findings"][0]["kind"] == "unverified_outcome"


@pytest.mark.parametrize(
    "quote", ["希望这次能解决", "这次已经解决了吗？", "如果已经解决就可以了", "应该已经解决了", "也许现在恢复正常了"]
)
def test_ambiguous_success_is_saved_as_unverified(memory, quote):
    _, store = memory
    rows = source_rows({"id": "synthetic", "messages": [{"role": "user", "content": quote}]})
    value = {
        "topic": "解决状态",
        "confirmed_outcome": {
            "text": "问题已经解决",
            "kind": "user_confirmed",
            "source_ids": [rows[0]["source_id"]],
            "quote": quote,
        },
    }
    result = store.episodes.normalize(value, rows, repair=True)
    assert result["status"] == "unresolved" and not result["confirmed_outcome"]
    assert result["findings"][0]["kind"] == "unverified_outcome"


def test_clipped_confirmation_cannot_remove_wish_qualifier_or_tool_failure(memory):
    _, store = memory
    cases = [
        [{"role": "user", "content": "希望这次已经解决了。"}],
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "execute_code"}]},
            {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "1 failed, 3 passed; exit code 1"}],
            },
        ],
    ]
    for index, messages in enumerate(cases):
        rows = source_rows({"id": "synthetic", "messages": messages})
        row = rows[-1]
        claim = {
            "text": "已解决",
            "quote": "已经解决了" if index == 0 else "3 passed",
            "source_ids": [row["source_id"]],
            "kind": "user_confirmed" if index == 0 else "tool_verified",
        }
        result = store.episodes.normalize({"topic": "确认语境", "confirmed_outcome": claim}, rows, repair=True)
        assert result["status"] == "unresolved" and not result["confirmed_outcome"]


def test_verified_tool_failure_can_retract_and_single_string_source_stays_compatible(memory):
    _, store = memory
    rows = source_rows(
        {
            "id": "synthetic",
            "messages": [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "c1", "name": "execute_code"}]},
                {
                    "role": "user",
                    "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "tests failed; exit code 1"}],
                },
            ],
        }
    )
    proof = rows[-1]
    result = store.episodes.normalize(
        {
            "topic": "执行验证",
            "problem": [{"text": "实际执行失败", "source_ids": [proof["source_id"]]}],
            "outcome_update": {"operation": "RETRACT", "source_ids": [proof["source_id"]], "quote": proof["text"]},
        },
        rows,
    )
    assert result["outcome_update"]["operation"] == "RETRACT" and result["status"] == "unresolved"
    rows = source_rows({"id": "synthetic", "messages": [{"role": "user", "content": "现在已经解决了。"}]})
    result = store.episodes.normalize(
        {
            "topic": "用户确认",
            "confirmed_outcome": {
                "text": "已解决",
                "source_ids": rows[0]["source_id"],
                "quote": rows[0]["text"],
                "kind": "user_confirmed",
            },
        },
        rows,
    )
    assert result["status"] == "resolved"


@pytest.mark.parametrize("action", ["skip", "replace", "independent"])
def test_import_preserves_manual_facts_until_explicit_conflict_choice(memory, action):
    _, store = memory
    old = store.put({"content": "用户使用 Windows 11", "subject": "user.os", "value": "Windows 11"})
    data = {"version": 3, "memories": [{"content": "用户使用 Linux", "subject": "user.os", "value": "Linux"}]}
    preview = store.import_preview(data)
    assert len(store.list()) == 1 and store.list()[0]["content"] == old["content"]
    assert store.import_data(data) == 0
    conflict = preview["conflicts"][0]
    data["_import_decisions"] = {"0": {**conflict, "action": action}}
    assert store.import_data(data) == (0 if action == "skip" else 1)
    rows = store.list()
    if action == "replace":
        assert rows[0]["id"] == old["id"] and rows[0]["content"] == "用户使用 Linux"
        assert not rows[0]["source_conv_id"] and not rows[0]["source_quote"]
        assert rows[0]["provenance"] == "external_unverified"
    else:
        assert any(r["id"] == old["id"] and r["content"] == old["content"] for r in rows)
        assert len(rows) == (2 if action == "independent" else 1)


def test_import_replacement_checks_version_inside_transaction(memory):
    _, store = memory
    old = store.put({"content": "用户使用 Windows 11", "subject": "user.os"})
    data = {"version": 2, "memories": [{"content": "用户使用 Linux", "subject": "user.os"}]}
    conflict = store.import_preview(data)["conflicts"][0]
    store.put({"id": old["id"], "content": "用户使用 macOS"})
    with pytest.raises(ValueError, match="版本冲突"):
        store.import_data({**data, "_import_decisions": {"0": {**conflict, "action": "replace"}}})
    assert store.list()[0]["content"] == "用户使用 macOS"


def test_import_normalized_key_collision_is_previewed_and_never_silently_replaces(memory):
    _, store = memory
    row = store.put({"content": "手动保存的笔记"})
    data = {"version": 1, "memories": [{"content": "手动 保存的笔记"}]}
    assert store.import_preview(data)["conflicts"][0]["memory_id"] == row["id"]
    assert store.import_data(data) == 0
    assert store.list()[0]["content"] == row["content"]


@pytest.mark.parametrize("size", [25, 100])
def test_sync_reuses_sources_per_snapshot_and_invalidates_on_next_edit(memory, size):
    db, store = memory
    statements = [f"长期项目主题 {number} 研究独立的实现过程。" for number in range(size)]
    conv = discussion(db, [{"role": "user", "content": "\n".join(statements)}])
    for number, statement in enumerate(statements):
        fact(
            store,
            conv,
            statement,
            key=f"research.item{number}",
            subject=f"research.item{number}",
            source_quote=statement,
        )
    index = store.engine.index
    with (
        patch("claude_chat.memory_episodes.source_cache", lambda *args: nullcontext()),
        patch("claude_chat.memory_episodes.source_rows", wraps=source_rows) as parsed,
    ):
        started = time.perf_counter()
        baseline = index.sync_documents()
        baseline_seconds, baseline_calls = time.perf_counter() - started, parsed.call_count
    with patch("claude_chat.memory_episodes.source_rows", wraps=source_rows) as parsed:
        started = time.perf_counter()
        cached = index.sync_documents()
        cached_seconds, cached_calls = time.perf_counter() - started, parsed.call_count
    assert cached == baseline
    assert cached_calls <= 2 and baseline_calls >= size * 2
    print(
        json.dumps(
            {
                "facts": size,
                "uncached_parses": baseline_calls,
                "cached_parses": cached_calls,
                "uncached_seconds": round(baseline_seconds, 4),
                "cached_seconds": round(cached_seconds, 4),
            }
        )
    )
    conv["messages"][0]["content"] = "更正：这些项目讨论不再用于记忆参考。"
    db.save_conversation(conv)
    assert not any(doc[1] == "memory" for doc in index.sync_documents())

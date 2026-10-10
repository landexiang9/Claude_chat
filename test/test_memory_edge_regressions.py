"""Correctness regressions for the seven offline audit reproducers and adjacent privacy edges."""

import json
from unittest.mock import patch

import pytest
from test_memory_discussions import candidate, discussion, memory as shared_memory, persist

from claude_chat.memory_episodes import snapshot, source_rows, units
from claude_chat.memory_jobs import MemoryJobs


@pytest.fixture
def memory(tmp_path):
    yield from shared_memory.__wrapped__(tmp_path)


def test_source_roles_control_ordinary_claims_without_promoting_mixed_evidence(memory):
    db, store = memory
    rows = source_rows(discussion(db))
    value = candidate(rows, False)
    value["proposed_solutions"] = [{"text": "用户自己的方案", "source_ids": rows[0]["source_id"]}]
    value["findings"] = [{"text": "助手解释", "source_ids": [rows[1]["source_id"]], "kind": "user_statement"}]
    value["open_questions"] = [{"text": "共同讨论", "source_ids": [rows[0]["source_id"], rows[1]["source_id"]]}]
    normalized = store.episodes.normalize(value, rows)
    assert normalized["proposed_solutions"][0]["kind"] == "user_statement"
    assert normalized["findings"][0]["kind"] == "assistant_proposal"
    assert normalized["open_questions"][0]["kind"] == "mixed_unverified"
    assert normalized["confirmed_outcome"] is None


def test_auto_repair_downgrades_unproven_outcomes_but_never_invents_sources(memory):
    db, store = memory
    conv = discussion(db)
    rows = source_rows(conv)
    value = candidate(rows)
    value["confirmed_outcome"]["source_ids"] = [rows[1]["source_id"]]
    value["confirmed_outcome"]["quote"] = rows[1]["text"]
    identity = store.episodes.persist_batch(
        conv["id"], snapshot(conv["messages"]), rows, [value], store.options()["epoch"]
    )[0]
    saved = store.episodes.read(identity, "global")
    assert saved["status"] == "unresolved" and saved["confirmed_outcome"] is None
    assert saved["validation_warnings"] and saved["proposed_solutions"][-1]["kind"] == "assistant_proposal"
    value["confirmed_outcome"]["source_ids"] = ["invented"]
    with pytest.raises(ValueError, match="来源"):
        store.episodes.persist_batch(conv["id"], snapshot(conv["messages"]), rows, [value], store.options()["epoch"])


def test_edit_sources_and_forget_are_atomic_and_preserve_old_version(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    conv["messages"].append({"role": "user", "content": "新的证据：研究另一种 PHP 方案。"})
    db.save_conversation(conv)
    rows = source_rows(conv)
    changes = candidate(rows, False)
    changes["problem"] = [{"text": "新的研究方案", "source_ids": [rows[-1]["source_id"]]}]
    changes["proposed_solutions"] = []
    updated = store.episodes.edit(identity, "global", changes)
    assert updated["origin"] == "manual" and updated["period_start"]
    with store.connect() as conn:
        refs = {r[0] for r in conn.execute("SELECT source_id FROM memory_sources WHERE entity_id=?", (identity,))}
    assert refs == {rows[-1]["source_id"]}
    assert len(store.episodes.versions(identity, "global")[0]["sources"]) == 3
    # A rejected edit rolls back payload, source links and the version archive together.
    changes["problem"][0]["source_ids"] = ["invalid"]
    with pytest.raises(ValueError):
        store.episodes.edit(identity, "global", changes)
    assert store.episodes.read(identity, "global")["version"] == updated["version"]
    store.episodes.forget(identity, "global")
    remaining = store.episodes.sources(store.episodes.conversation(conv["id"]))
    assert {r["source_id"] for r in remaining} == {r["source_id"] for r in rows[:-1]}


def test_forget_repairs_stale_source_links_from_previous_versions(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    rows = source_rows(conv)
    changes = candidate(rows, False)
    changes["proposed_solutions"] = []
    # Simulate a manual edit from the old release: payload updated, source table untouched.
    with store.connect() as conn:
        conn.execute("UPDATE memory_episodes SET payload=?,origin='manual' WHERE id=?", (json.dumps(changes), identity))
    store.episodes.forget(identity, "global")
    remaining = store.episodes.sources(store.episodes.conversation(conv["id"]))
    assert {r["source_id"] for r in remaining} == {r["source_id"] for r in rows[1:]}


def test_withdrawn_outcome_cannot_be_revived_by_old_batch_but_new_confirmation_can_replace(memory):
    db, store = memory
    conv = discussion(db)
    identity = persist(store, conv)
    old_rows = source_rows(conv)
    conv["messages"].append({"role": "user", "content": "之前判断错了，仍未解决，属性问题复现。"})
    db.save_conversation(conv)
    rows = source_rows(conv)
    value = candidate(rows, False)
    value["problem"] = [{"text": "用户报告仍未解决", "source_ids": [rows[-1]["source_id"]]}]
    value["proposed_solutions"] = []
    store.episodes.persist_batch(conv["id"], snapshot(conv["messages"]), [rows[-1]], [value], store.options()["epoch"])
    saved = store.episodes.read(identity, "global")
    assert saved["status"] == "unresolved" and saved["outcome_update"]["operation"] == "RETRACT"
    assert store.episodes.versions(identity, "global")[0]["payload"]["confirmed_outcome"]
    # Replay of a previous confirmation cannot overwrite the later source-backed retraction.
    store.episodes.persist_batch(
        conv["id"], snapshot(conv["messages"]), old_rows, [candidate(old_rows)], store.options()["epoch"]
    )
    assert store.episodes.read(identity, "global")["confirmed_outcome"] is None
    conv["messages"].append({"role": "user", "content": "现在使用新方案已经解决，属性值恢复正常。"})
    db.save_conversation(conv)
    rows = source_rows(conv)
    replacement = candidate(rows)
    store.episodes.persist_batch(conv["id"], snapshot(conv["messages"]), rows, [replacement], store.options()["epoch"])
    assert store.episodes.read(identity, "global")["status"] == "resolved"


def test_assistant_cannot_retract_and_negative_quote_cannot_confirm_success(memory):
    db, store = memory
    conv = discussion(db)
    rows = source_rows(conv)
    value = candidate(rows)
    value["outcome_update"] = {"operation": "RETRACT", "source_ids": [rows[1]["source_id"]], "quote": rows[1]["text"]}
    with pytest.raises(ValueError, match="撤回"):
        store.episodes.normalize(value, rows)
    value.pop("outcome_update")
    conv["messages"][-1]["content"] = "仍未解决，属性读取失败。"
    db.save_conversation(conv)
    rows = source_rows(conv)
    value = candidate(rows)
    repaired = store.episodes.normalize(value, rows, repair=True)
    assert repaired["confirmed_outcome"] is None and repaired["outcome_update"]["operation"] == "RETRACT"


def test_pause_resume_preserves_prefix_but_real_source_privacy_still_invalidates(memory):
    db, store = memory
    conv = discussion(db)
    batches = []
    jobs = MemoryJobs(store, lambda *a, **kw: (batches.append(a[1]["messages"]) or {"episodes": []}, {}))
    jobs.enqueue([conv["id"]])
    assert jobs.step()
    epoch = store.options()["epoch"]
    jobs.pause()
    with patch.object(jobs, "start", return_value=True):
        jobs.resume()
    jobs.abort.clear()
    assert store.options()["epoch"] == epoch
    conv["messages"].append({"role": "user", "content": "新增 Docker 网络问题"})
    db.save_conversation(conv)
    assert jobs.enqueue([conv["id"]])["conversations"][0]["processed_units"] == 3
    assert jobs.step() and len(batches[-1]) == 1
    store.set_privacy(conv["id"], {"exclude_history": True})
    assert not jobs.preview([conv["id"]])["conversations"]
    store.set_privacy(conv["id"], {"exclude_history": False})
    assert jobs.preview([conv["id"]])["conversations"][0]["processed_units"] == 0


@pytest.mark.parametrize("stage", ["episode", "merge"])
def test_deterministic_type_errors_stop_and_keep_progress(memory, stage):
    db, store = memory
    conv = discussion(db)
    calls = []

    def request(*a, **kw):
        calls.append(1)
        if stage == "merge":
            return {"groups": [{"label": "主题", "episode_ids": None}]}, {"input_tokens": 1}
        value = candidate(a[1]["messages"])
        value["problem"][0]["kind"] = []
        return {"episodes": [value]}, {"input_tokens": 1}

    jobs = MemoryJobs(store, request)
    if stage == "merge":
        persist(store, conv)
        jobs.enqueue_merge("global")
        step = jobs.merge_step
    else:
        jobs.enqueue([conv["id"]])
        step = jobs.step
    assert not step() and not step() and len(calls) == 1
    job = jobs.list()[0]
    assert job["cursor"] == 0 and job["attempts"] == 5 and not job["retry_at"]
    assert ("episode_ids" if stage == "merge" else "kind") in job["error"]


def test_contiguous_split_quotes_work_but_gaps_and_forged_join_quotes_fail(memory):
    db, store = memory
    text = "研究背景" * 599 + "123" + "已经解决并确认恢复正常，原文跨越分片边界"
    conv = discussion(db, [{"role": "user", "content": text}])
    rows = source_rows(conv)
    chunks = units(rows)
    value = {
        "topic": "原文分片",
        "problem": [{"text": "用户提供验证", "source_ids": [rows[0]["source_id"]]}],
        "confirmed_outcome": {
            "text": "验证描述",
            "source_ids": [rows[0]["source_id"]],
            "kind": "user_confirmed",
            "quote": text[2395:2410],
        },
    }
    assert store.episodes.normalize(value, chunks)["status"] == "resolved"
    value["confirmed_outcome"]["quote"] = text[2395:2400] + "\n" + text[2400:2410]
    with pytest.raises(ValueError):
        store.episodes.normalize(value, chunks)
    chunks[1] = {**chunks[1], "offset": 2405, "text": text[2405:]}
    value["confirmed_outcome"]["quote"] = text[2395:2400] + text[2405:2410]
    with pytest.raises(ValueError):
        store.episodes.normalize(value, chunks)


def test_selected_job_retry_does_not_resume_other_failures(memory):
    db, store = memory
    conv1, conv2 = discussion(db), discussion(db)
    jobs = MemoryJobs(store, lambda *a, **kw: ({"episodes": []}, {}))
    jobs.enqueue([conv1["id"], conv2["id"]])
    jobs.pause()
    identity = jobs.list()[0]["id"]
    with patch.object(jobs, "start", return_value=True):
        assert jobs.resume(identity)
    states = {job["id"]: job["status"] for job in jobs.list()}
    assert states[identity] == "queued" and list(states.values()).count("paused") == 1
    with pytest.raises(ValueError):
        jobs.resume("invalid-job")

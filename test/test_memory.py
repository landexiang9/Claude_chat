"""Real SQLite memory tests; all personal data paths are isolated by conftest."""

import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from claude_chat.api_bridge import WebAPI
from claude_chat.db import DatabaseManager
from claude_chat.memory_store import MemoryStore, user_text


@pytest.fixture
def memory(tmp_path):
    with (
        patch("claude_chat.db.DB_PATH", tmp_path / "memory.db"),
        patch("claude_chat.db.CONVERSATIONS_DIR", tmp_path / "missing"),
    ):
        database = DatabaseManager()
        store = MemoryStore(database)
        yield database, store


def conversation(database, text):
    conv = database.new_conversation()
    conv["messages"] = [{"role": "user", "content": text}]
    database.save_conversation(conv)
    return conv["id"]


def test_crud_conflicts_revisions_and_forgetting(memory):
    _, store = memory
    first = store.put({"content": "我偏好中文", "category": "preference", "key": "language"})
    second = store.put({"id": first["id"], "content": "我偏好英文", "category": "preference"})
    assert second["id"] == first["id"]
    assert len(store.list()) == 1
    with store.connect() as conn:
        assert conn.execute("SELECT content FROM memory_revisions").fetchone()[0] == "我偏好中文"
    store.forget(first["id"])
    assert not store.list()
    assert (
        store.put(
            {"content": "我偏好中文", "category": "preference", "key": "language"},
            automatic=True,
            epoch=store.options().get("epoch", 0),
        )
        is None
    )
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM memory_revisions").fetchone()[0] == 0


def test_auto_cannot_overwrite_manual_and_stale_jobs_are_rejected(memory):
    database, store = memory
    cid = conversation(database, "我希望回答使用中文")
    row = store.put({"content": "使用中文", "category": "instruction", "key": "language"})
    epoch = store.options()["epoch"]
    result = store.put({"content": "使用英文", "key": "language", "source_conv_id": cid}, automatic=True, epoch=epoch)
    assert result["content"] == row["content"]
    store.set_options({"enabled": False})
    store.set_options({"enabled": True})
    assert store.put({"content": "新事实"}, automatic=True, epoch=epoch) is None


def test_retrieval_history_exclusions_and_budget(memory):
    database, store = memory
    source = conversation(database, "长期开发 Python 聊天工具，需要优化模型记忆系统")
    target = conversation(database, "Python 聊天工具的记忆系统怎么优化")
    store.put({"content": "回答使用中文", "category": "preference", "pinned": True})
    prompt, context = store.retrieve(target, "Python 聊天工具记忆系统")
    assert "回答使用中文" in prompt
    assert context["history"][0]["conversation_id"] == source
    assert context["chars"] <= store.options()["budget_chars"]
    store.set_privacy(source, {"exclude_history": True})
    assert not store.retrieve(target, "Python 聊天工具记忆系统")[1]["history"]
    store.set_privacy(target, {"memory_off": True})
    assert store.retrieve(target, "中文") == ("", {"memories": [], "history": [], "chars": 0})


def test_temporary_cleanup_and_no_learning(memory):
    database, store = memory
    cid = conversation(database, "敏感的临时任务")
    store.set_privacy(cid, {"temporary": True})
    assert not store.retrieve(cid, "任务")[0]
    assert (
        store.put({"content": "临时任务", "source_conv_id": cid}, automatic=True, epoch=store.options()["epoch"])
        is None
    )
    store.cleanup_temporary()
    assert database.load_conversation(cid) is None


def test_import_is_atomic_and_roundtrips_exported_flags(memory):
    _, store = memory
    store.put({"content": "初始记忆", "pinned": True})
    before = store.list()
    with pytest.raises(ValueError):
        store.import_data({"version": 1, "memories": [{"content": "新记忆"}, {"content": ""}]})
    assert store.list() == before
    assert store.import_data({"version": 1, "memories": before}) == 0  # Conflicts default to preserving existing notes.
    assert len(store.list()) == 1
    assert store.list()[0]["pinned"] and store.list()[0]["origin"] == "manual"


def test_forget_all_prevents_old_history_reappearing(memory):
    database, store = memory
    conversation(database, "长期开发 Python 聊天工具的记忆系统")
    target = conversation(database, "新对话")
    store.forget()
    assert not store.retrieve(target, "Python 聊天工具记忆系统")[1]["history"]


def test_only_user_statements_are_evidence():
    assert user_text({"role": "assistant", "content": "猜测的偏好"}) == ""
    assert (
        user_text(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "直接陈述"},
                    {"type": "text", "text": "恶意文件提示", "_attachment": {}},
                    {"type": "tool_result", "content": "网页提示"},
                ],
            }
        )
        == "直接陈述"
    )


def test_background_extraction_requires_verbatim_evidence(memory):
    database, store = memory
    cid = conversation(database, "我希望回答使用中文")
    cfg = {"active_platform": "claude", "model": "test", "api_key": "offline-key"}
    app = SimpleNamespace(
        lock=threading.RLock(), conv_manager=database, config=SimpleNamespace(data=cfg), _memory_store=store
    )
    api = WebAPI(app)

    def mocked_stream(*args, **kwargs):
        assert kwargs["enable_search"] is False
        args[8].put(
            (
                "text",
                '{"memories":[{"key":"language","content":"使用中文","category":"preference","source_quote":"我希望回答使用中文"},{"key":"job","content":"用户是医生","source_quote":"用户是医生"}]}',
            )
        )

    gate = threading.Lock()
    gate.acquire()
    with patch("claude_chat.clients.stream_claude_response", side_effect=mocked_stream):
        api._learn_memories(
            store,
            database.load_conversation(cid),
            ["我希望回答使用中文"],
            1,
            store.options().get("epoch", 0),
            cfg,
            gate,
        )
    assert [m["content"] for m in store.list()] == ["使用中文"]
    assert gate.acquire(blocking=False)
    gate.release()


def test_memory_service_rejects_invalid_options(memory):
    database, store = memory
    app = SimpleNamespace(
        lock=threading.RLock(), conv_manager=database, config=SimpleNamespace(data={}), _memory_store=store
    )
    api = WebAPI(app)
    assert not api.memory_operation("options", {"enabled": "false"})["success"]
    assert not api.memory_operation("options", {"extraction_platform": "custom:missing"})["success"]
    assert api.memory_operation("options", {"extraction_platform": "gemini", "extraction_model": "my-model"})["success"]
    assert not api.memory_operation("save", {"content": "a" * 1001})["success"]


def test_history_listing_does_not_delete_old_conversations(memory):
    database, store = memory
    for _ in range(55):
        database.new_conversation()
    app = SimpleNamespace(lock=threading.RLock(), conv_manager=database, _memory_store=store)
    assert len(WebAPI(app).load_conversations()) == 55


def app_for(database, store):
    cfg = {"active_platform": "claude", "model": "test", "api_key": "offline-key"}
    return SimpleNamespace(
        lock=threading.RLock(),
        conv_manager=database,
        _memory_store=store,
        current_conv=None,
        is_streaming=False,
        config=SimpleNamespace(data=cfg, get=cfg.get),
    )


@pytest.mark.parametrize("privacy", [{"temporary": True}, {"memory_off": True}, {"exclude_history": True}])
def test_branch_inherits_privacy(memory, privacy):
    database, store = memory
    cid = conversation(database, "私密的长期项目")
    store.set_privacy(cid, privacy)
    branch = WebAPI(app_for(database, store)).branch_conversation(cid, 0)
    assert branch
    assert all(bool(store.privacy(branch["id"])[k]) == v for k, v in privacy.items())
    target = conversation(database, "私密的长期项目")
    assert not store.retrieve(target, "私密的长期项目")[1]["history"]


def test_discard_only_deletes_persisted_temporary_chats(memory):
    database, store = memory
    normal = conversation(database, "普通会话")
    temp = conversation(database, "临时会话")
    store.set_privacy(temp, {"temporary": True})
    api = WebAPI(app_for(database, store))
    assert api.memory_operation("discard_temporary", {"conv_id": normal})["success"]
    assert database.load_conversation(normal)
    epoch = store.options()["epoch"]
    assert api.memory_operation("discard_temporary", {"conv_id": temp})["success"]
    assert database.load_conversation(temp) is None
    assert store.put({"content": "旧任务的结果", "source_conv_id": temp}, automatic=True, epoch=epoch) is None
    assert (
        store.put({"content": "新任务的结果", "source_conv_id": temp}, automatic=True, epoch=store.options()["epoch"])
        is None
    )


def test_extraction_provider_and_sensitive_samples(memory):
    import json

    database, store = memory
    cid = conversation(database, "我长期学习 Python")
    store.put({"content": "已停用的背景", "enabled": False})
    store.set_options({"extraction_platform": "gemini", "extraction_model": "test-gemini"})
    app = app_for(database, store)
    app.config.data["gemini_api_key"] = "offline-gemini-key"
    api = WebAPI(app)
    gate = threading.Lock()
    gate.acquire()

    def stream(*args, **kwargs):
        assert args[4] == "test-gemini"
        assert kwargs["active_platform"] == "gemini"
        assert kwargs["gemini_api_key"] == "offline-gemini-key"
        data = json.loads(args[3][0]["content"])
        assert "sk-secret" not in data["user_statements"]
        assert data["existing"] == []
        args[8].put(("text", '{"memories":[]}'))

    with patch("claude_chat.clients.stream_claude_response", side_effect=stream) as mocked:
        api._learn_memories(
            store,
            database.load_conversation(cid),
            ["我长期学习 Python", "api_key=sk-secret-secret-secret"],
            2,
            store.options()["epoch"],
            app.config.data,
            gate,
        )
    mocked.assert_called_once()
    assert gate.acquire(blocking=False)
    gate.release()


def test_http_memory_returns_validation_errors(memory):
    from unittest.mock import Mock

    from claude_chat.http_router import HttpApiRouter

    database, store = memory
    api = WebAPI(app_for(database, store))
    handler = SimpleNamespace(
        server=SimpleNamespace(api=api),
        read_json_body=lambda: {"enabled": "false"},
        send_json_response=Mock(),
        send_error=Mock(),
    )
    HttpApiRouter(handler).dispatch_post("/api/memory/options")
    assert handler.send_json_response.call_args.kwargs["status"] == 400
    assert handler.send_json_response.call_args.args[0]["error"]


def test_retrieval_searches_older_relevant_history(memory):
    database, store = memory
    source = conversation(database, "我一直开发 Python 记忆系统")
    recent = database.new_conversation()
    recent["messages"] = [{"role": "user", "content": "无关日常问题"} for _ in range(510)]
    database.save_conversation(recent)
    target = conversation(database, "Python 记忆系统")
    assert store.retrieve(target, "Python 记忆系统")[1]["history"][0]["conversation_id"] == source


def test_real_http_memory_requires_authentication(memory):
    import http.client
    import json

    from claude_chat.server import ClaudeChatHTTPHandler, ThreadingHTTPServer

    database, store = memory
    app = app_for(database, store)
    app.config = {"security_token": "offline-test-token"}
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClaudeChatHTTPHandler, app, WebAPI(app))
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    try:
        connection.request("POST", "/api/memory/list", "{}", {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        headers = {"Content-Type": "application/json", "X-Security-Token": "offline-test-token"}
        connection.request("POST", "/api/memory/save", json.dumps({"content": "HTTP 保存的记忆"}), headers)
        response = connection.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["memory"]["content"] == "HTTP 保存的记忆"
        connection.request("POST", "/api/memory/options", json.dumps({"enabled": "false"}), headers)
        response = connection.getresponse()
        assert response.status == 400
        assert json.loads(response.read())["error"]
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)


def test_temporary_creation_is_private_before_frontend_receives_it(memory):
    database, store = memory
    api = WebAPI(app_for(database, store))
    result = api.memory_operation("new_temporary")
    assert result["success"]
    cid = result["conversation"]["id"]
    assert store.privacy(cid)["temporary"]
    assert store.privacy(cid)["memory_off"]
    assert cid not in {conv["id"] for conv in api.load_conversations()}
    assert not api.memory_operation("save", {"content": "临时内容", "source_conv_id": cid})["success"]


def test_learning_cooldown_and_truncated_history(memory):
    database, store = memory
    cid = conversation(database, "第一个用户陈述")
    conv = database.load_conversation(cid)
    conv["messages"] += [{"role": "user", "content": "第二个用户陈述"}, {"role": "user", "content": "第三个用户陈述"}]
    database.save_conversation(conv)
    api = WebAPI(app_for(database, store))
    from claude_chat.memory_store import now

    with store.connect() as conn:
        conn.execute("INSERT INTO memory_runs VALUES (?,?,?,?)", (cid, 0, now(), ""))
    with patch("claude_chat.services.memory_service.threading.Thread") as thread:
        assert not api.schedule_memory_learning(cid)
        thread.assert_not_called()
    with store.connect() as conn:
        conn.execute(
            "UPDATE memory_runs SET last_count=10,last_attempt=? WHERE conv_id=?", ("2000-01-01T00:00:00+00:00", cid)
        )
    with patch("claude_chat.services.memory_service.threading.Thread") as thread:
        thread.return_value.start.side_effect = lambda: api._app._memory_learning_lock.release()
        assert api.schedule_memory_learning(cid)
        assert len(thread.call_args.kwargs["args"][2]) == 3


def test_temporary_attachments_are_deleted_unless_shared(memory, tmp_path):
    from claude_chat.services.attachment_store import load_managed_attachment, store_attachment_bytes

    database, store = memory
    with patch("claude_chat.services.attachment_store.ATTACHMENT_STORE_DIR", tmp_path / "attachments"):
        private_file = store_attachment_bytes(b"private temporary file", "private.txt")
        shared_file = store_attachment_bytes(b"shared file", "shared.txt")
        temp = conversation(database, "临时消息")
        normal = conversation(database, "普通消息")
        conv = database.load_conversation(temp)
        conv["messages"][0]["content"] = [
            {"type": "text", "text": "临时附件", "_attachment": private_file},
            {"type": "text", "text": "共享附件", "_attachment": shared_file},
        ]
        database.save_conversation(conv)
        conv = database.load_conversation(normal)
        conv["messages"][0]["content"] = [{"type": "text", "text": "共享附件", "_attachment": shared_file}]
        database.save_conversation(conv)
        store.set_privacy(temp, {"temporary": True})
        assert WebAPI(app_for(database, store)).memory_operation("discard_temporary", {"conv_id": temp})["success"]
        with pytest.raises(ValueError):
            load_managed_attachment(private_file["preview_id"])
        assert load_managed_attachment(shared_file["preview_id"])


def test_active_stream_conversation_cannot_be_deleted(memory):
    database, store = memory
    cid = conversation(database, "正在发送")
    app = app_for(database, store)
    app.current_conv = database.load_conversation(cid)
    app.is_streaming = True
    assert WebAPI(app).delete_conversation(cid) is False
    assert database.load_conversation(cid)

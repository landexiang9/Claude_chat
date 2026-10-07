"""Local, bounded, inspectable long-term memory and relevant-history retrieval.

Embeddings are optional. Automatic learning must supply a verbatim user quote.
"""

import hashlib
import json
import re
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

DEFAULT_OPTIONS = {
    "enabled": True,
    "auto_extract": True,
    "history_enabled": True,
    "budget_chars": 6000,
    "extraction_platform": "",
    "extraction_model": "",
    "embedding_platform": "",
    "embedding_model": "",
    "embedding_dimensions": 0,
    "embedding_local_path": "",
    "index_paused": False,
    "top_k": 8,
    "half_life_days": 180,
    "semantic_threshold": 0.45,
    "summary_enabled": True,
    "recent_messages": 12,
    "router_model_enabled": False,
}
CATEGORIES = {"preference", "profile", "project", "instruction", "other"}


def now():
    return datetime.now(timezone.utc).isoformat()


def normalized(text):
    return re.sub(r"\s+", "", str(text)).casefold()


def fingerprint(text):
    return hashlib.sha256(normalized(text).encode()).hexdigest()


def terms(text):
    words = re.findall(r"[a-zA-Z0-9_]{2,}", text.lower())
    for chunk in re.findall(r"[\u4e00-\u9fff]+", text):
        words.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
    return set(words[:300])


def user_text(message):
    if message.get("role") != "user":
        return ""
    content = message.get("content", "")
    if isinstance(content, str):
        return content[:6000]
    if isinstance(content, list):
        # Attachments, tool results, fetched pages and model output are not evidence.
        return "\n".join(
            str(b.get("text", ""))
            for b in content
            if isinstance(b, dict)
            and b.get("type") == "text"
            and "_attachment" not in b
            and not str(b.get("text", "")).lstrip().startswith("--- 附件文件:")
        )[:6000]
    return ""


def attachment_ids(content):
    pending, ids = [content], set()
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
        elif isinstance(item, dict):
            value = item.get("preview_id")
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value):
                ids.add(value)
            pending.extend(item.values())
    return ids


class MemoryStore:
    def __init__(self, database):
        self.database = database
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS memory_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, fact_key TEXT NOT NULL UNIQUE, content TEXT NOT NULL,
                    category TEXT NOT NULL, pinned INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,
                    origin TEXT NOT NULL, source_conv_id TEXT, source_quote TEXT DEFAULT '',
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL, last_used_at TEXT, use_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memory_blocks (fingerprint TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS conversation_privacy (
                    conv_id TEXT PRIMARY KEY, temporary INTEGER DEFAULT 0, memory_off INTEGER DEFAULT 0,
                    exclude_history INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS memory_runs (
                    conv_id TEXT PRIMARY KEY, last_count INTEGER DEFAULT 0, last_attempt TEXT, error TEXT DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS memory_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL,
                    content TEXT NOT NULL, origin TEXT NOT NULL, source_quote TEXT, changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_context (
                    conv_id TEXT PRIMARY KEY, data TEXT NOT NULL
                );
            """)
            from claude_chat.memory_schema import initialize

            initialize(conn)
        from claude_chat.memory_hybrid import HybridMemory

        self.engine = HybridMemory(self)

    def connect(self):
        return self.database.get_connection()

    def options(self, conn=None):
        if conn is None:
            with self.connect() as conn:
                return self.options(conn)
        values = {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT * FROM memory_settings")}
        return {"epoch": 0, **DEFAULT_OPTIONS, **values}

    def set_options(self, changes):
        if not isinstance(changes, dict):
            raise ValueError("记忆设置必须是对象")
        for key, value in changes.items():
            if key not in DEFAULT_OPTIONS:
                raise ValueError("未知记忆设置")
            if key in {
                "extraction_platform",
                "extraction_model",
                "embedding_platform",
                "embedding_model",
                "embedding_local_path",
            }:
                if not isinstance(value, str) or len(value) > 200:
                    raise ValueError("供应商和模型应为不超过 200 字符的文本")
            elif key == "budget_chars":
                if type(value) is not int or not 1000 <= value <= 12000:
                    raise ValueError("记忆预算应在 1000–12000 字符之间")
            elif key in {"embedding_dimensions", "top_k", "half_life_days", "recent_messages"}:
                bounds = {
                    "embedding_dimensions": (0, 8192),
                    "top_k": (1, 20),
                    "half_life_days": (1, 3650),
                    "recent_messages": (4, 100),
                }
                low, high = bounds[key]
                if type(value) is not int or not low <= value <= high:
                    raise ValueError(f"{key} 应在 {low}–{high} 之间")
            elif key == "semantic_threshold":
                if type(value) not in (int, float) or not 0 <= value <= 1:
                    raise ValueError("语义相似度阈值应在 0–1 之间")
            elif type(value) is not bool:
                raise ValueError("记忆开关必须为布尔值")
        with self.connect() as conn:
            current = self.options(conn)
            effective = {key: value for key, value in changes.items() if current[key] != value}
            if not effective:
                return current
            for key, value in effective.items():
                conn.execute("INSERT OR REPLACE INTO memory_settings VALUES (?,?)", (key, json.dumps(value)))
            conn.execute("DELETE FROM memory_context")
            purge = any(k.startswith("embedding_") or k in {"enabled", "history_enabled"} for k in effective)
            self.bump_epoch(conn, purge=purge)
        return self.options()

    def bump_epoch(self, conn, purge=True):
        epoch = int(self.options(conn).get("epoch", 0)) + 1
        conn.execute("INSERT OR REPLACE INTO memory_settings VALUES ('epoch',?)", (json.dumps(epoch),))
        if purge:
            from claude_chat.memory_schema import purge_derived

            purge_derived(conn)

    def privacy(self, conv_id):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM conversation_privacy WHERE conv_id=?", (conv_id,)).fetchone()
        return (
            dict(row) if row else {"temporary": False, "memory_off": False, "exclude_history": False, "scope": "global"}
        )

    def set_privacy(self, conv_id, changes):
        if not isinstance(changes, dict) or any(
            (k not in {"temporary", "memory_off", "exclude_history", "scope"})
            or (k != "scope" and type(v) is not bool)
            or (k == "scope" and (not isinstance(v, str) or not 1 <= len(v) <= 120))
            for k, v in changes.items()
        ):
            raise ValueError("无效的会话隐私设置")
        existing = self.privacy(conv_id)
        values = {**existing, **changes}
        if all(values[key] == existing[key] for key in ("temporary", "memory_off", "exclude_history", "scope")):
            return existing
        if existing["temporary"] and not values["temporary"]:
            raise ValueError("临时对话不能转为普通对话，请新建会话")
        with self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO conversation_privacy VALUES (?,?,?,?,?)",
                (
                    conv_id,
                    int(values["temporary"]),
                    int(values["memory_off"]),
                    int(values["exclude_history"]),
                    values["scope"],
                ),
            )
            if values["memory_off"] or values["temporary"]:
                conn.execute("DELETE FROM memory_context WHERE conv_id=?", (conv_id,))
            if values["memory_off"] or values["temporary"] or values["exclude_history"]:
                conn.execute(
                    "DELETE FROM memory_conflicts WHERE json_extract(candidate,'$.source_conv_id')=?", (conv_id,)
                )
            conn.execute("DELETE FROM memory_context")
            self.bump_epoch(
                conn, purge=any(values[k] != existing[k] for k in ("temporary", "memory_off", "exclude_history"))
            )
        return self.privacy(conv_id)

    def list(self, query=""):
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM memories ORDER BY pinned DESC, updated_at DESC").fetchall()
        return [dict(r) for r in rows if not query or query.casefold() in r["content"].casefold()]

    def put(self, data, automatic=False, epoch=None, _conn=None):
        from claude_chat.memory_resolver import find_previous, pending, persist_fields, relation, typed_fields

        if not isinstance(data, dict):
            raise ValueError("记忆必须是对象")
        content = data.get("content", "")
        if not isinstance(content, str) or not 1 <= len(content.strip()) <= 1000:
            raise ValueError("记忆内容应为 1–1000 字符")
        content = content.strip()
        category = data.get("category", "other")
        if category not in CATEGORIES:
            raise ValueError("记忆分类无效")
        for flag in ("pinned", "enabled"):
            if flag in data and type(data[flag]) is not bool:
                raise ValueError("记忆状态必须是布尔值")
        requested_scope = data.get("scope", "global")
        if not isinstance(requested_scope, str) or not 1 <= len(requested_scope) <= 120:
            raise ValueError("记忆作用域无效")
        fact_key = str(data.get("key") or hashlib.sha256(normalized(content).encode()).hexdigest())[:120]
        raw_fact_key = fact_key
        if requested_scope != "global":
            fact_key = hashlib.sha256((fact_key + "@" + requested_scope).encode()).hexdigest()
        source = str(data.get("source_conv_id") or "")
        quote = str(data.get("source_quote") or "")[:1000]
        # Prevent automatic credential collection. Manual notes remain explicitly user-controlled.
        if automatic and re.search(r"sk-[\w-]{10,}|AIza[\w-]+|password|密码|密钥|身份证|银行卡", content + quote, re.I):
            return None
        with self.connect() if _conn is None else nullcontext(_conn) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            options = self.options(conn)
            if automatic:
                if not conn.execute("SELECT 1 FROM conversations WHERE id=?", (source,)).fetchone():
                    return None
                private = conn.execute("SELECT * FROM conversation_privacy WHERE conv_id=?", (source,)).fetchone()
                if not options["enabled"] or not options["auto_extract"] or options.get("epoch", 0) != epoch:
                    return None
                if private and (private["temporary"] or private["memory_off"] or private["exclude_history"]):
                    return None
                if quote and not self.quote_is_current(conn, source, quote):
                    return None
                if conn.execute(
                    "SELECT 1 FROM memory_blocks WHERE fingerprint IN (?,?)",
                    (fingerprint(fact_key), fingerprint(content)),
                ).fetchone():
                    return None
            previous = conn.execute("SELECT * FROM memories WHERE id=?", (data.get("id", ""),)).fetchone()
            if not previous:
                previous = conn.execute(
                    "SELECT * FROM memories WHERE scope=? AND (fact_key IN (?,?) OR content=?)",
                    (requested_scope, raw_fact_key, fact_key, content),
                ).fetchone()
            fields = typed_fields(data, previous)
            if not previous:
                previous = find_previous(conn, fields)
                fields = typed_fields(data, previous)
            if (
                automatic
                and fields["subject"]
                and conn.execute(
                    "SELECT 1 FROM memory_blocks WHERE fingerprint=?",
                    (fingerprint(fields["subject"]),),
                ).fetchone()
            ):
                return None
            kind = relation(previous, content, fields, data.get("relation", ""))
            if automatic and (
                data.get("operation") == "IGNORE" or (data.get("operation") == "DELETE" and not previous)
            ):
                return None
            if automatic and not previous and fields["confidence"] < 0.85:
                pending(conn, None, data, "NEW", now())
                return None
            if (
                automatic
                and previous
                and (
                    data.get("operation") == "DELETE"
                    or (
                        kind != "SAME"
                        and (previous["origin"] == "manual" or fields["confidence"] < 0.85 or kind == "CONTRADICT")
                    )
                )
            ):
                candidate = {**data, "expected_version": previous["version"]}
                pending(conn, previous, candidate, kind, now())
                return dict(previous)
            if automatic and previous and previous["origin"] == "manual":
                return dict(previous)
            if automatic and kind == "EXTEND" and previous:
                # Preserve both evidence-backed clauses; never silently discard old supplemental facts.
                content = (previous["content"].rstrip("。") + "；" + content)[:1000]
                if fields["subject"]:
                    fields["value_json"] = json.dumps(
                        [json.loads(previous["value_json"]), json.loads(fields["value_json"])],
                        ensure_ascii=False,
                    )
            if previous:
                memory_id = previous["id"]
                if previous["content"] != content:
                    conn.execute(
                        "INSERT INTO memory_revisions (memory_id,content,origin,source_quote,changed_at) VALUES "
                        "(?,?,?,?,?)",
                        (memory_id, previous["content"], previous["origin"], previous["source_quote"], now()),
                    )
                    conn.execute(
                        "DELETE FROM memory_revisions WHERE memory_id=? AND id NOT IN (SELECT id FROM "
                        "memory_revisions WHERE memory_id=? ORDER BY id DESC LIMIT 20)",
                        (memory_id, memory_id),
                    )
                fact_key = previous["fact_key"]
                conn.execute(
                    "UPDATE memories SET content=?, category=?, pinned=?, enabled=?, origin=?, "
                    "source_conv_id=?, source_quote=?, updated_at=? WHERE id=?",
                    (
                        content,
                        category,
                        int(data.get("pinned", bool(previous["pinned"]))),
                        int(data.get("enabled", bool(previous["enabled"]))),
                        "automatic" if automatic else "manual",
                        source or previous["source_conv_id"],
                        quote or previous["source_quote"],
                        now(),
                        memory_id,
                    ),
                )
            else:
                if conn.execute("SELECT count(*) FROM memories").fetchone()[0] >= 500:
                    raise ValueError("已达到 500 条记忆上限，请先整理")
                memory_id = uuid.uuid4().hex
                conn.execute(
                    "INSERT INTO memories "
                    "(id,fact_key,content,category,pinned,enabled,origin,source_conv_id,source_quote,"
                    "created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        memory_id,
                        fact_key,
                        content,
                        category,
                        int(data.get("pinned", False)),
                        int(data.get("enabled", True)),
                        "automatic" if automatic else "manual",
                        source or None,
                        quote,
                        now(),
                        now(),
                    ),
                )
            persist_fields(conn, memory_id, fields, previous, kind, now(), automatic)
            if not automatic:
                conn.execute("DELETE FROM memory_context")
                self.bump_epoch(conn, purge=False)
            row = conn.execute("SELECT * FROM memories WHERE id=?", (memory_id,)).fetchone()
        return dict(row)

    def forget(self, memory_id=None, _conn=None):
        with self.connect() if _conn is None else nullcontext(_conn) as conn:
            if _conn is None:
                conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM memories" + (" WHERE id=?" if memory_id else ""), (memory_id,) if memory_id else ()
            ).fetchall()
            for row in rows:
                revisions = conn.execute(
                    "SELECT content FROM memory_revisions WHERE memory_id=?", (row["id"],)
                ).fetchall()
                versions = conn.execute(
                    "SELECT content,source_conv_id FROM memory_versions WHERE memory_id=?", (row["id"],)
                ).fetchall()
                for value in (
                    row["fact_key"],
                    row["content"],
                    row["subject"],
                    *(r[0] for r in revisions),
                    *(r[0] for r in versions),
                ):
                    conn.execute("INSERT OR IGNORE INTO memory_blocks VALUES (?)", (fingerprint(value),))
                if row["source_conv_id"]:
                    conn.execute(
                        "INSERT INTO conversation_privacy (conv_id,exclude_history) VALUES (?,1) ON "
                        "CONFLICT(conv_id) DO UPDATE SET exclude_history=1",
                        (row["source_conv_id"],),
                    )
                for version in versions:
                    if version["source_conv_id"]:
                        conn.execute(
                            "INSERT INTO conversation_privacy (conv_id,exclude_history) VALUES (?,1) "
                            "ON CONFLICT(conv_id) DO UPDATE SET exclude_history=1",
                            (version["source_conv_id"],),
                        )
            if memory_id:
                conn.execute("DELETE FROM memory_revisions WHERE memory_id=?", (memory_id,))
                conn.execute("DELETE FROM memories WHERE id=?", (memory_id,))
                for table in ("memory_facts", "memory_versions", "memory_conflicts", "memory_audit"):
                    conn.execute(f"DELETE FROM {table} WHERE memory_id=?", (memory_id,))
            else:
                conn.execute("DELETE FROM memories")
                conn.execute("DELETE FROM memory_revisions")
                conn.execute("DELETE FROM memory_runs")
                for table in ("memory_facts", "memory_versions", "memory_conflicts", "memory_audit"):
                    conn.execute(f"DELETE FROM {table}")
                conn.execute(
                    "INSERT INTO conversation_privacy (conv_id,exclude_history) SELECT id,1 FROM "
                    "conversations WHERE 1 ON CONFLICT(conv_id) DO UPDATE SET exclude_history=1"
                )
            conn.execute("DELETE FROM memory_context")
            self.bump_epoch(conn)
        return True

    def cleanup_temporary(self):
        from claude_chat.db import deserialize_content

        attachments = set()
        with self.connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT conv_id FROM conversation_privacy WHERE temporary=1")]
            for cid in ids:
                for row in conn.execute("SELECT content FROM messages WHERE conversation_id=?", (cid,)):
                    attachments.update(attachment_ids(deserialize_content(row[0])))
                conn.execute("DELETE FROM messages WHERE conversation_id=?", (cid,))
                conn.execute("DELETE FROM conversations WHERE id=?", (cid,))
                conn.execute("DELETE FROM memory_context WHERE conv_id=?", (cid,))
                conn.execute("DELETE FROM memory_runs WHERE conv_id=?", (cid,))
                conn.execute("DELETE FROM conversation_privacy WHERE conv_id=?", (cid,))
                conn.execute("DELETE FROM memory_conflicts WHERE json_extract(candidate,'$.source_conv_id')=?", (cid,))
            if ids:
                self.bump_epoch(conn)
        return attachments

    def conversation_deleted(self, conv_id):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for table in ("memory_context", "memory_runs", "conversation_privacy"):
                conn.execute(f"DELETE FROM {table} WHERE conv_id=?", (conv_id,))
            # Keep explicitly saved notes, but remove the deleted source/quote.
            conn.execute(
                "UPDATE memory_revisions SET source_quote='' WHERE memory_id IN "
                "(SELECT id FROM memories WHERE source_conv_id=?)",
                (conv_id,),
            )
            conn.execute("UPDATE memories SET source_conv_id=NULL,source_quote='' WHERE source_conv_id=?", (conv_id,))
            conn.execute(
                "UPDATE memory_versions SET source_conv_id=NULL,source_quote='' WHERE source_conv_id=?", (conv_id,)
            )
            conn.execute("DELETE FROM memory_conflicts WHERE json_extract(candidate,'$.source_conv_id')=?", (conv_id,))
            conn.execute("DELETE FROM memory_context")
            self.bump_epoch(conn)

    def retrieve(self, conv_id, query):
        return self.engine.retrieve(conv_id, query)

    def conflicts(self):
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM memory_conflicts WHERE status='pending' ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return [{**dict(row), "candidate": json.loads(row["candidate"])} for row in rows]

    @staticmethod
    def quote_is_current(conn, source, quote):
        from claude_chat.db import deserialize_content

        rows = conn.execute(
            "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 200", (source,)
        )
        return any(quote in user_text({"role": "user", "content": deserialize_content(row[0])}) for row in rows)

    def resolve_conflict(self, conflict_id, accept):
        if type(accept) is not bool:
            raise ValueError("请选择接受或拒绝")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM memory_conflicts WHERE id=? AND status='pending'", (conflict_id,)
            ).fetchone()
            if not row:
                raise ValueError("待确认事项不存在或已处理")
            candidate = json.loads(row["candidate"])
            old = conn.execute("SELECT * FROM memories WHERE id=?", (row["memory_id"],)).fetchone()
            if accept:
                if row["memory_id"] and (not old or old["version"] != candidate.get("expected_version")):
                    raise ValueError("原记忆已改变，请拒绝旧冲突并重新整理")
                source = candidate.get("source_conv_id")
                private = conn.execute("SELECT * FROM conversation_privacy WHERE conv_id=?", (source,)).fetchone()
                if source and (
                    not conn.execute("SELECT 1 FROM conversations WHERE id=?", (source,)).fetchone()
                    or (private and any(private[k] for k in ("temporary", "memory_off", "exclude_history")))
                ):
                    raise ValueError("来源已删除或不再允许记忆")
                if (
                    source
                    and candidate.get("source_quote")
                    and not self.quote_is_current(conn, source, candidate["source_quote"])
                ):
                    raise ValueError("来源陈述已修改，请重新整理或手动编辑")
                if candidate.get("operation") == "DELETE":
                    if not old or not row["memory_id"]:
                        raise ValueError("删除操作必须指定现存记忆")
                    self.forget(old["id"], _conn=conn)
                else:
                    if old:
                        candidate["id"] = old["id"]
                        if row["relation"] == "EXTEND":
                            candidate["content"] = (old["content"].rstrip("。") + "；" + candidate["content"])[:1000]
                            if old["subject"]:
                                candidate["value"] = [
                                    json.loads(old["value_json"]),
                                    candidate.get("value", candidate["content"]),
                                ]
                    self.put(candidate, _conn=conn)
            elif isinstance(candidate.get("content"), str):
                conn.execute("INSERT OR IGNORE INTO memory_blocks VALUES (?)", (fingerprint(candidate["content"]),))
            conn.execute("DELETE FROM memory_conflicts WHERE id=?", (conflict_id,))
            from claude_chat.memory_resolver import audit

            audit(
                conn,
                row["memory_id"],
                "UPDATE" if accept else "IGNORE",
                row["relation"],
                "用户接受冲突" if accept else "用户拒绝冲突",
                now(),
            )
        return True

    def import_data(self, data):
        rows = data.get("memories")
        if (
            type(data.get("version")) is not int
            or data["version"] not in {1, 2}
            or not isinstance(rows, list)
            or len(rows) > 500
        ):
            raise ValueError("请导入版本 1 或 2 的记忆 JSON，最多 500 条")
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for item in rows:
                if not isinstance(item, dict):
                    raise ValueError("记忆条目必须是对象")
                # Imports are explicit manual notes; external source IDs cannot grant provenance.
                clean = {
                    k: item[k]
                    for k in (
                        "content",
                        "category",
                        "pinned",
                        "enabled",
                        "subject",
                        "scope",
                        "confidence",
                        "importance",
                        "value",
                    )
                    if k in item
                }
                if "value_json" in item and "value" not in clean:
                    clean["value"] = json.loads(item["value_json"])
                for flag in ("pinned", "enabled"):
                    if flag in clean and clean[flag] in (0, 1):
                        clean[flag] = bool(clean[flag])
                saved = self.put(clean, _conn=conn)
                versions = item.get("history_versions", []) if data["version"] == 2 else []
                if not isinstance(versions, list) or len(versions) > 50:
                    raise ValueError("每条记忆最多导入 50 个历史版本")
                from claude_chat.memory_resolver import typed_fields

                for version in versions:
                    if (
                        not isinstance(version, dict)
                        or type(version.get("version")) is not int
                        or not 1 <= version["version"] <= 100000
                    ):
                        raise ValueError("历史版本编号无效")
                    text = version.get("content")
                    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 1000:
                        raise ValueError("历史版本正文无效")
                    fields = typed_fields({**version, "value": json.loads(version.get("value_json", "null"))})
                    try:
                        start = datetime.fromisoformat(version["valid_from"])
                        end = datetime.fromisoformat(version["valid_until"])
                        if not start.tzinfo or not end.tzinfo or start > end:
                            raise ValueError
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError("历史版本有效期无效") from exc
                    if conn.execute(
                        "SELECT 1 FROM memory_versions WHERE memory_id=? AND content=? AND valid_from=? "
                        "AND valid_until=?",
                        (saved["id"], text, start.isoformat(), end.isoformat()),
                    ).fetchone():
                        continue
                    conn.execute(
                        "INSERT INTO memory_versions (memory_id,version,subject,value_json,content,scope,"
                        "valid_from,valid_until,source_quote) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            saved["id"],
                            version["version"],
                            fields["subject"],
                            fields["value_json"],
                            text,
                            fields["scope"],
                            start.isoformat(),
                            end.isoformat(),
                            "",
                        ),
                    )
                if versions:
                    next_version = max(v["version"] for v in versions) + 1
                    conn.execute("UPDATE memories SET version=max(version,?) WHERE id=?", (next_version, saved["id"]))
                if data["version"] == 2 and item.get("valid_from"):
                    try:
                        start = datetime.fromisoformat(item["valid_from"])
                        if not start.tzinfo:
                            raise ValueError
                    except (ValueError, TypeError) as exc:
                        raise ValueError("当前记忆有效期无效") from exc
                    conn.execute("UPDATE memories SET valid_from=? WHERE id=?", (start.isoformat(), saved["id"]))
                    conn.execute(
                        "UPDATE memory_facts SET valid_from=? WHERE memory_id=?", (start.isoformat(), saved["id"])
                    )
                conn.execute(
                    "DELETE FROM memory_versions WHERE memory_id=? AND id NOT IN (SELECT id FROM memory_versions "
                    "WHERE memory_id=? ORDER BY valid_until DESC LIMIT 50)",
                    (saved["id"], saved["id"]),
                )
        return len(rows)

    def export_data(self):
        with self.connect() as conn:
            rows = [dict(row) for row in conn.execute("SELECT * FROM memories ORDER BY pinned DESC,updated_at DESC")]
            for row in rows:
                row["history_versions"] = [
                    dict(v)
                    for v in conn.execute(
                        "SELECT * FROM memory_versions WHERE memory_id=? ORDER BY version DESC",
                        (row["id"],),
                    )
                ]
        return {"version": 2, "memories": rows, "exported_at": now()}

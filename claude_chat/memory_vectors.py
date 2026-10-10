"""Incremental local vector index, resumable batches and bounded query caching."""

import hashlib
import json
import threading

from claude_chat.memory_embeddings import pack, unpack
from claude_chat.memory_queries import low_information
from claude_chat.memory_schema import index_state, set_state
from claude_chat.memory_store import now, user_text


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def safe_text(text):
    import re

    return not re.search(r"sk-[\w-]{10,}|AIza[\w-]+|(?:password|密码|密钥|api[_ -]?key)\s*[:：=]", text, re.I)


class VectorIndex:
    def __init__(self, store, provider_factory):
        self.store, self.provider_factory = store, provider_factory
        self.gate = threading.Lock()
        self.cancel = threading.Event()

    def status(self):
        options = self.store.options()
        signature = ""
        if options["embedding_platform"]:
            try:
                signature = self.provider_factory(options).signature
            except Exception:
                pass
        with self.store.connect() as conn:
            state = index_state(conn)
            state["documents"] = conn.execute("SELECT count(*) FROM memory_documents").fetchone()[0]
            state["indexed"] = conn.execute(
                "SELECT count(*) FROM memory_vectors WHERE signature=?", (signature,)
            ).fetchone()[0]
        state["running"] = self.gate.locked()
        state["configured"] = bool(options["embedding_platform"])
        state["paused"] = options["index_paused"]
        if state["configured"] and signature != state.get("signature", ""):
            state["status"] = "needs_rebuild"
        if state["paused"]:
            state["status"] = "paused"
        if not options["enabled"]:
            state["status"] = "disabled"
        return state

    def sync_documents(self):
        from claude_chat.db import deserialize_content
        from claude_chat.memory_episodes import source_cache

        options = self.store.options()
        epoch = options.get("epoch", 0)
        if not options["enabled"]:
            return []
        docs = {}

        def add(kind, ref, text, stamp, payload, conv_id=None, suffix=""):
            if not text or not safe_text(text):
                return
            identity = f"{kind}:{ref}:{suffix}"
            docs[identity] = (
                identity,
                kind,
                ref,
                conv_id,
                text,
                digest(text),
                stamp,
                json.dumps(payload, ensure_ascii=False),
            )

        with self.store.connect() as conn, source_cache(self.store, conn):
            conn.execute("BEGIN")  # All cached source reads belong to one SQLite snapshot.
            for row in conn.execute("SELECT * FROM memories WHERE enabled=1"):
                if not self.store.fact_sources.valid(row, conn):
                    continue
                memory = dict(row)
                memory["provenance"] = (
                    "external_unverified"
                    if conn.execute("SELECT 1 FROM memory_imports WHERE memory_id=?", (row["id"],)).fetchone()
                    else row["origin"]
                )
                add("memory", row["id"], row["content"], row["updated_at"], memory)
            if options["history_enabled"] and options["episodes_enabled"]:
                from claude_chat.memory_episodes import EpisodeStore

                episodes = {}
                for scope in conn.execute("SELECT DISTINCT scope FROM memory_episodes").fetchall():
                    episodes.update({e["id"]: e for e in EpisodeStore(self.store).list(scope[0], conn)})
                for episode in episodes.values():
                    # Scope filtering happens before both embedding and answer-context construction.
                    texts = [episode["topic"]]
                    for field in ("problem", "findings", "proposed_solutions", "open_questions"):
                        texts.extend(claim["text"] for claim in episode[field])
                    if episode["confirmed_outcome"]:
                        texts.append(episode["confirmed_outcome"]["text"])
                    payload = {
                        **episode,
                        "content": "\n".join(texts),
                        "importance": 0.9 if episode["status"] == "resolved" else 0.65,
                    }
                    add(
                        "episode",
                        episode["id"],
                        payload["content"],
                        episode["updated_at"],
                        payload,
                        conv_id=episode["conv_id"],
                    )
            for row in conn.execute("""
                SELECT v.* FROM memory_versions v JOIN memories m ON m.id=v.memory_id
                WHERE m.enabled=1 ORDER BY v.id DESC LIMIT 2000
            """):
                parent = conn.execute("SELECT * FROM memories WHERE id=?", (row["memory_id"],)).fetchone()
                if not self.store.fact_sources.version_valid(row, parent, conn):
                    continue
                item = dict(row)
                item["provenance"] = (
                    "external_unverified"
                    if conn.execute("SELECT 1 FROM memory_imports WHERE memory_id=?", (row["memory_id"],)).fetchone()
                    else "historical"
                )
                add(
                    "version",
                    row["memory_id"],
                    row["content"],
                    row["valid_until"],
                    item,
                    suffix=str(row["version"]),
                )
            if options["history_enabled"]:
                rows = conn.execute("""
                    SELECT m.content,m.conversation_id,m.id,m.created_at,c.title,c.updated_at,
                    coalesce(p.scope,'global') AS scope FROM messages m
                    JOIN conversations c ON c.id=m.conversation_id
                    LEFT JOIN conversation_privacy p ON p.conv_id=c.id
                    WHERE m.role='user' AND coalesce(p.temporary,0)=0 AND coalesce(p.memory_off,0)=0
                    AND coalesce(p.exclude_history,0)=0 ORDER BY m.id DESC LIMIT 5000
                """).fetchall()
                seen, permitted = {}, {}
                for row in rows:
                    cid = row["conversation_id"]
                    if cid not in permitted:
                        permitted[cid] = {
                            r["content_hash"]
                            for r in self.store.episodes.sources(self.store.episodes.conversation(cid, conn), conn)
                        }
                    from claude_chat.memory_episodes import digest as source_digest

                    if source_digest(deserialize_content(row["content"])) not in permitted[cid]:
                        continue
                    text = user_text({"role": "user", "content": deserialize_content(row["content"])}, limit=None)
                    if low_information(text):
                        continue
                    for offset in range(0, len(text), 600):
                        chunk = text[offset : offset + 800]
                        # Message row IDs change on save; content-derived IDs avoid re-embedding unchanged messages.
                        key = row["conversation_id"] + ":" + digest(chunk)
                        if key in seen:
                            continue
                        seen[key] = True
                        add(
                            "history",
                            row["conversation_id"],
                            chunk,
                            row["updated_at"],
                            {
                                "conversation_id": row["conversation_id"],
                                "title": row["title"],
                                "excerpt": chunk,
                                "scope": row["scope"],
                                "created_at": row["created_at"],
                            },
                            conv_id=row["conversation_id"],
                            suffix=digest(chunk),
                        )
                        if len(seen) >= 5000:
                            break
                    if len(seen) >= 5000:
                        break
        with self.store.connect() as conn, source_cache(self.store, conn):
            conn.execute("BEGIN IMMEDIATE")
            if self.store.options(conn).get("epoch", 0) != epoch:
                return []
            existing = {row["id"]: tuple(row) for row in conn.execute("SELECT * FROM memory_documents")}
            for identity in existing.keys() - docs.keys():
                conn.execute("DELETE FROM memory_vectors WHERE document_id=?", (identity,))
                conn.execute("DELETE FROM memory_documents WHERE id=?", (identity,))
            for identity, document in docs.items():
                if document[1] == "memory":
                    live = conn.execute("SELECT * FROM memories WHERE id=?", (document[2],)).fetchone()
                    if (
                        not live
                        or not live["enabled"]
                        or digest(live["content"]) != document[5]
                        or live["version"] != json.loads(document[7])["version"]
                        or not self.store.fact_sources.valid(live, conn)
                    ):
                        docs[identity] = None
                        continue
                elif document[1] == "history":
                    live = conn.execute("SELECT updated_at FROM conversations WHERE id=?", (document[3],)).fetchone()
                    if not live or live[0] != document[6]:
                        docs[identity] = None
                        continue
                elif document[1] == "episode":
                    live = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (document[2],)).fetchone()
                    if (
                        not live
                        or live["version"] != json.loads(document[7])["version"]
                        or not self.store.episodes.valid(live, conn)
                    ):
                        docs[identity] = None
                        continue
                previous = existing.get(identity)
                if not previous or previous[5] != document[5]:
                    conn.execute("DELETE FROM memory_vectors WHERE document_id=?", (identity,))
                if previous != document:
                    conn.execute("INSERT OR REPLACE INTO memory_documents VALUES (?,?,?,?,?,?,?,?)", document)
        return [doc for doc in docs.values() if doc]

    def start(self, rebuild=False):
        if not self.gate.acquire(blocking=False):
            return False
        try:
            self.cancel.clear()
            if rebuild:
                with self.store.connect() as conn:
                    conn.execute("DELETE FROM memory_vectors")
                    conn.execute("DELETE FROM memory_embedding_cache")
                    conn.execute("DELETE FROM memory_index_state")
            thread = threading.Thread(target=self._background, daemon=True, name="memory-vector-index")
            thread.start()
        except Exception:
            self.gate.release()
            raise
        return True

    def pause(self):
        self.cancel.set()

    def _background(self):
        try:
            self.build()
        finally:
            self.gate.release()

    def build(self):
        options = self.store.options()
        epoch = options.get("epoch", 0)
        if not options["enabled"] or not options["embedding_platform"] or options["index_paused"]:
            return
        error = ""
        try:
            provider = self.provider_factory(options)
            self.sync_documents()
            with self.store.connect() as conn:
                if self.store.options(conn).get("epoch", 0) != epoch:
                    return
                state = index_state(conn)
                dimensions = state.get("dimensions", 0) if state.get("signature") == provider.signature else 0
                conn.execute("DELETE FROM memory_vectors WHERE signature<>?", (provider.signature,))
                pending = conn.execute("""
                    SELECT d.* FROM memory_documents d LEFT JOIN memory_vectors v ON v.document_id=d.id
                    WHERE v.document_id IS NULL OR v.content_hash<>d.content_hash ORDER BY d.kind,d.id
                """).fetchall()
                set_state(conn, status="indexing", signature=provider.signature, pending=len(pending), error="")
            processed = 0
            for offset in range(0, len(pending), 32):
                if self.cancel.is_set() or self.store.options().get("epoch", 0) != epoch:
                    return
                with self.store.connect() as conn:
                    batch = [
                        row
                        for row in pending[offset : offset + 32]
                        if conn.execute(
                            "SELECT 1 FROM memory_documents WHERE id=? AND content_hash=?",
                            (row["id"], row["content_hash"]),
                        ).fetchone()
                    ]
                if not batch:
                    continue
                # Re-check epoch immediately before sending any private text to a provider.
                vectors = [None] * len(batch)
                missing = []
                with self.store.connect() as conn:
                    for number, row in enumerate(batch):
                        cache_key = "d:" + digest(provider.signature + ":document:" + row["text"])
                        cached = conn.execute(
                            "SELECT * FROM memory_embedding_cache WHERE cache_key=?", (cache_key,)
                        ).fetchone()
                        if cached:
                            vectors[number] = unpack(cached["vector"], cached["dimensions"])
                        else:
                            missing.append(number)
                if missing:
                    if self.cancel.is_set() or self.store.options().get("epoch", 0) != epoch:
                        return
                    # Re-read original evidence at the outbound boundary, not only after the response.
                    with self.store.connect() as conn:
                        conn.execute("BEGIN IMMEDIATE")
                        if self.cancel.is_set() or self.store.options(conn).get("epoch", 0) != epoch:
                            return
                        valid = []
                        for number in missing:
                            row = batch[number]
                            current = conn.execute(
                                "SELECT content_hash FROM memory_documents WHERE id=?", (row["id"],)
                            ).fetchone()
                            if current and current[0] == row["content_hash"] and self.document_valid(row, conn):
                                valid.append(number)
                            else:
                                conn.execute("DELETE FROM memory_vectors WHERE document_id=?", (row["id"],))
                                conn.execute("DELETE FROM memory_documents WHERE id=?", (row["id"],))
                                conn.execute(
                                    "DELETE FROM memory_embedding_cache WHERE cache_key=?",
                                    ("d:" + digest(provider.signature + ":document:" + row["text"]),),
                                )
                        missing = valid
                    if not missing:
                        continue
                    fresh = provider.embed([batch[number]["text"] for number in missing])
                    if len(fresh) != len(missing):
                        raise ValueError("Embedding 返回数量无效")
                    for number, vector in zip(missing, fresh):
                        vectors[number] = vector
                if len(vectors) != len(batch):
                    raise ValueError("Embedding 返回数量无效")
                with self.store.connect() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    if self.cancel.is_set() or self.store.options(conn).get("epoch", 0) != epoch:
                        return
                    for row, vector in zip(batch, vectors):
                        if vector is None:
                            continue
                        if dimensions and dimensions != len(vector):
                            raise ValueError("Embedding 维度改变，请重建索引")
                        dimensions = len(vector)
                        current = conn.execute(
                            "SELECT content_hash FROM memory_documents WHERE id=?", (row["id"],)
                        ).fetchone()
                        if not current or current[0] != row["content_hash"]:
                            continue
                        if not self.document_valid(row, conn):
                            conn.execute("DELETE FROM memory_vectors WHERE document_id=?", (row["id"],))
                            conn.execute("DELETE FROM memory_documents WHERE id=?", (row["id"],))
                            continue
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_vectors VALUES (?,?,?,?,?)",
                            (
                                row["id"],
                                provider.signature,
                                dimensions,
                                pack(vector),
                                row["content_hash"],
                            ),
                        )
                        cache_key = "d:" + digest(provider.signature + ":document:" + row["text"])
                        conn.execute(
                            "INSERT OR REPLACE INTO memory_embedding_cache VALUES (?,?,?,?,?)",
                            (
                                cache_key,
                                provider.signature,
                                dimensions,
                                pack(vector),
                                now(),
                            ),
                        )
                    processed += len(batch)
                    set_state(
                        conn, dimensions=dimensions, pending=max(0, len(pending) - processed), processed=processed
                    )
                    conn.execute(
                        "DELETE FROM memory_embedding_cache WHERE cache_key LIKE 'd:%' AND cache_key NOT IN "
                        "(SELECT cache_key FROM memory_embedding_cache WHERE cache_key LIKE 'd:%' "
                        "ORDER BY created_at DESC LIMIT 10000)"
                    )
        except Exception as exc:
            error = str(exc) if isinstance(exc, ValueError) else "Embedding 请求失败；已保留进度，可重试"
            error = error[:200]
        finally:
            with self.store.connect() as conn:
                if self.store.options(conn).get("epoch", 0) == epoch:
                    set_state(
                        conn,
                        status="paused" if self.cancel.is_set() else "error" if error else "ready",
                        error=error,
                        last_attempt=now(),
                    )

    def document_valid(self, row, conn):
        kind = row["kind"]
        if kind == "memory":
            memory = conn.execute("SELECT * FROM memories WHERE id=?", (row["ref_id"],)).fetchone()
            return bool(
                memory
                and memory["enabled"]
                and self.store.fact_sources.valid(memory, conn)
                and memory["version"] == json.loads(row["payload"])["version"]
                and digest(memory["content"]) == row["content_hash"]
            )
        if kind == "version":
            parent = conn.execute("SELECT * FROM memories WHERE id=?", (row["ref_id"],)).fetchone()
            payload = json.loads(row["payload"])
            version = conn.execute(
                "SELECT * FROM memory_versions WHERE memory_id=? AND version=?", (row["ref_id"], payload["version"])
            ).fetchone()
            return bool(
                parent
                and parent["enabled"]
                and version
                and digest(version["content"]) == row["content_hash"]
                and self.store.fact_sources.version_valid(version, parent, conn)
            )
        if kind == "episode":
            episode = conn.execute("SELECT * FROM memory_episodes WHERE id=?", (row["ref_id"],)).fetchone()
            return bool(
                episode
                and episode["version"] == json.loads(row["payload"])["version"]
                and self.store.episodes.valid(episode, conn)
            )
        if kind == "history":
            conv = self.store.episodes.conversation(row["conv_id"], conn)
            return bool(
                conv
                and self.store.episodes.allowed(row["conv_id"], conn=conn)
                and any(
                    r["evidence_role"] == "user" and row["text"] in r["text"]
                    for r in self.store.episodes.sources(conv, conn)
                )
            )
        return False

    def search(self, query, documents, top_k, historical=False, abort=None):
        options = self.store.options()
        if not options["embedding_platform"]:
            return {}, "未配置 Embedding，使用关键词检索"
        epoch = options.get("epoch", 0)
        try:
            provider = self.provider_factory(options)
            allowed = {row[0]: row for row in documents if historical or row[1] != "version"}
            with self.store.connect() as conn:
                rows = [
                    r
                    for r in conn.execute("SELECT * FROM memory_vectors WHERE signature=?", (provider.signature,))
                    if r["document_id"] in allowed and r["content_hash"] == allowed[r["document_id"]][5]
                ]
                if not rows:
                    return {}, "向量索引尚未就绪，使用关键词检索"
                cache_key = "q:" + digest(provider.signature + ":query:" + query)
                cached = conn.execute("SELECT * FROM memory_embedding_cache WHERE cache_key=?", (cache_key,)).fetchone()
            if abort and abort.is_set():
                return {}, "记忆查询已取消"
            if cached:
                vector = unpack(cached["vector"], cached["dimensions"])
            elif abort is None:
                vector = provider.embed([query], query=True)[0]
            else:
                # Transports may block; cancellation returns immediately and never stores a late query vector.
                import queue

                result = queue.Queue()

                def request():
                    try:
                        result.put((True, provider.embed([query], query=True)[0]))
                    except Exception:
                        result.put((False, "Embedding 查询失败，使用关键词检索"))

                threading.Thread(target=request, daemon=True, name="memory-query-embedding").start()
                while True:
                    if abort.is_set():
                        return {}, "记忆查询已取消"
                    try:
                        success, vector = result.get(timeout=0.05)
                        if not success:
                            return {}, vector
                        break
                    except queue.Empty:
                        continue
            ranked = []
            for row in rows:
                if row["dimensions"] != len(vector):
                    raise ValueError("查询和索引维度不同，请重建索引")
                other = unpack(row["vector"], row["dimensions"])
                ranked.append((sum(a * b for a, b in zip(vector, other)), row["document_id"]))
            with self.store.connect() as conn:
                conn.execute("BEGIN IMMEDIATE")
                if self.store.options(conn).get("epoch", 0) != epoch or (abort and abort.is_set()):
                    return {}, "记忆设置已改变，本轮取消召回"
                if not cached:
                    conn.execute(
                        "INSERT OR REPLACE INTO memory_embedding_cache VALUES (?,?,?,?,?)",
                        (
                            cache_key,
                            provider.signature,
                            len(vector),
                            pack(vector),
                            now(),
                        ),
                    )
                    conn.execute(
                        "DELETE FROM memory_embedding_cache WHERE cache_key LIKE 'q:%' AND cache_key NOT IN "
                        "(SELECT cache_key FROM memory_embedding_cache WHERE cache_key LIKE 'q:%' "
                        "ORDER BY created_at DESC LIMIT 128)"
                    )
            return {identity: max(0, score) for score, identity in sorted(ranked, reverse=True)[: top_k * 5]}, ""
        except Exception as exc:
            return {}, str(exc)[:200] if isinstance(exc, ValueError) else "Embedding 查询失败，使用关键词检索"

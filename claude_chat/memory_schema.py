"""Additive SQLite migrations and privacy invalidation for hybrid memory."""

import json


def initialize(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(memories)")}
    additions = {
        "subject": "TEXT DEFAULT ''",
        "value_json": "TEXT DEFAULT 'null'",
        "scope": "TEXT DEFAULT 'global'",
        "confidence": "REAL DEFAULT 1.0",
        "importance": "REAL DEFAULT 0.7",
        "version": "INTEGER DEFAULT 1",
        "valid_from": "TEXT DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE memories ADD COLUMN {name} {definition}")
    privacy_columns = {row[1] for row in conn.execute("PRAGMA table_info(conversation_privacy)")}
    if "scope" not in privacy_columns:
        conn.execute("ALTER TABLE conversation_privacy ADD COLUMN scope TEXT DEFAULT 'global'")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_facts (
            memory_id TEXT PRIMARY KEY, subject TEXT NOT NULL, scope TEXT NOT NULL,
            value_json TEXT NOT NULL, confidence REAL NOT NULL, valid_from TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memory_fact_subject ON memory_facts(subject,scope);
        CREATE TABLE IF NOT EXISTS memory_versions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL, version INTEGER NOT NULL,
            subject TEXT, value_json TEXT, content TEXT NOT NULL, scope TEXT,
            valid_from TEXT NOT NULL, valid_until TEXT NOT NULL, source_conv_id TEXT, source_quote TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_conflicts (
            id TEXT PRIMARY KEY, memory_id TEXT, candidate TEXT NOT NULL, relation TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending', created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT, operation TEXT NOT NULL,
            relation TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_documents (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, ref_id TEXT NOT NULL, conv_id TEXT,
            text TEXT NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL, payload TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS memory_document_source ON memory_documents(conv_id,ref_id);
        CREATE TABLE IF NOT EXISTS memory_vectors (
            document_id TEXT PRIMARY KEY, signature TEXT NOT NULL, dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL, content_hash TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_embedding_cache (
            cache_key TEXT PRIMARY KEY, signature TEXT NOT NULL, dimensions INTEGER NOT NULL,
            vector BLOB NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS memory_index_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_summaries (
            conv_id TEXT PRIMARY KEY, source_hash TEXT NOT NULL, content TEXT NOT NULL,
            message_count INTEGER NOT NULL, updated_at TEXT NOT NULL
        );
    """)
    conn.execute("UPDATE memories SET valid_from=created_at WHERE valid_from=''")
    # Old keys and note IDs are retained. Inference is deliberately conservative.
    from claude_chat.memory_resolver import typed_fields

    for row in conn.execute("SELECT * FROM memories WHERE subject=''").fetchall():
        fields = typed_fields({"key": row["fact_key"], "content": row["content"]})
        if fields["subject"]:
            conn.execute(
                "UPDATE memories SET subject=?,value_json=? WHERE id=?",
                (fields["subject"], fields["value_json"], row["id"]),
            )
    conn.execute("""
        INSERT OR REPLACE INTO memory_facts SELECT id,subject,scope,value_json,confidence,valid_from
        FROM memories WHERE subject<>'' AND enabled=1
    """)


def purge_derived(conn):
    """Epoch-changing actions invalidate all materialized potentially private text."""
    for table in ("memory_documents", "memory_vectors", "memory_embedding_cache", "memory_summaries"):
        conn.execute(f"DELETE FROM {table}")
    conn.execute("DELETE FROM memory_index_state")


def set_state(conn, **values):
    for key, value in values.items():
        conn.execute("INSERT OR REPLACE INTO memory_index_state VALUES (?,?)", (key, json.dumps(value)))


def index_state(conn):
    return {r[0]: json.loads(r[1]) for r in conn.execute("SELECT * FROM memory_index_state")}

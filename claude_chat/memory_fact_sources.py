"""Live user evidence for facts; equivalent statements can retain multiple independent sources."""

import json

from claude_chat.memory_store import now


class FactSources:
    def __init__(self, store):
        self.store = store

    def seed(self):
        with self.store.connect() as conn:
            for memory in conn.execute("SELECT * FROM memories WHERE origin='automatic'").fetchall():
                if not conn.execute("SELECT 1 FROM memory_fact_evidence WHERE memory_id=?", (memory["id"],)).fetchone():
                    self.record(
                        conn,
                        memory["id"],
                        memory["source_conv_id"],
                        memory["source_quote"],
                        memory["content"],
                        memory["value_json"],
                        "SAME",
                    )

    def record(self, conn, memory_id, conv_id, quote, content, value_json, relation):
        if not conv_id or not quote or not self.store.episodes.allowed(conv_id, conn=conn):
            return
        if relation not in {"SAME", "EXTEND"}:
            conn.execute("DELETE FROM memory_sources WHERE entity_kind='fact' AND entity_id=?", (memory_id,))
            conn.execute("DELETE FROM memory_fact_evidence WHERE memory_id=?", (memory_id,))
        for row in self.store.episodes.sources(self.store.episodes.conversation(conv_id, conn), conn):
            if row["evidence_role"] != "user" or quote not in row["text"]:
                continue
            conn.execute(
                "INSERT OR REPLACE INTO memory_sources VALUES ('fact',?,?,?,?,?,?,?,?)",
                (
                    memory_id,
                    row["source_id"],
                    conv_id,
                    row["role"],
                    row["content_hash"],
                    row["occurrence"],
                    row["snapshot_hash"],
                    row["position"],
                ),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_fact_evidence VALUES (?,?,?,?,?,?)",
                (memory_id, row["source_id"], content, quote, value_json, now()),
            )

    def live(self, memory_id, conn):
        refs = conn.execute(
            "SELECT s.*,e.content,e.quote,e.value_json FROM memory_sources s "
            "JOIN memory_fact_evidence e ON e.memory_id=s.entity_id AND e.source_id=s.source_id "
            "WHERE s.entity_kind='fact' AND s.entity_id=? ORDER BY e.created_at",
            (memory_id,),
        ).fetchall()
        result = []
        for row in refs:
            if not self.store.episodes.allowed(row["conv_id"], conn=conn):
                continue
            current = self.store.episodes.sources(self.store.episodes.conversation(row["conv_id"], conn), conn)
            if any(
                s["source_id"] == row["source_id"] and s["evidence_role"] == "user" and row["quote"] in s["text"]
                for s in current
            ):
                result.append(dict(row))
        return result

    def valid(self, memory, conn):
        if memory["origin"] != "automatic":
            return True  # Explicit user notes remain independently saved.
        live = self.live(memory["id"], conn)
        supported = {r["content"] for r in live}
        clauses = memory["content"].split("；")
        return bool(live) and (
            memory["content"] in supported or all(c in supported or c + "。" in supported for c in clauses)
        )

    def version_valid(self, version, parent, conn):
        if not parent or not parent["enabled"]:
            return False
        # The version's provenance is immutable; editing today's fact cannot authorize yesterday's evidence.
        if version["origin"] in {"manual", "external"}:
            return True
        if version["origin"] != "automatic":
            # Legacy snapshots lack a complete proof; retain them for management, never guess attribution.
            return False
        supported, current = set(), {}
        for ref in json.loads(version["evidence_json"]):
            cid = ref["conv_id"]
            if not self.store.episodes.allowed(cid, conn=conn):
                continue
            if cid not in current:
                current[cid] = self.store.episodes.sources(self.store.episodes.conversation(cid, conn), conn)
            if any(
                r["source_id"] == ref["source_id"] and r["evidence_role"] == "user" and ref["quote"] in r["text"]
                for r in current[cid]
            ):
                supported.add(ref["content"])
        return bool(supported) and (
            version["content"] in supported
            or all(c in supported or c + "。" in supported for c in version["content"].split("；"))
        )

    def source_deleted(self, conn, conv_id):
        affected = [
            r[0]
            for r in conn.execute(
                "SELECT DISTINCT entity_id FROM memory_sources WHERE entity_kind='fact' AND conv_id=?", (conv_id,)
            )
        ]
        for identity in affected:
            memory = conn.execute("SELECT * FROM memories WHERE id=?", (identity,)).fetchone()
            if not memory or memory["origin"] != "automatic":
                continue
            live = [r for r in self.live(identity, conn) if r["conv_id"] != conv_id]
            if not live:
                conn.execute("UPDATE memories SET enabled=0 WHERE id=?", (identity,))
                continue
            contents = list(dict.fromkeys(r["content"] for r in live))
            values = list(dict.fromkeys(r["value_json"] for r in live))
            value = values[0] if len(values) == 1 else json.dumps([json.loads(v) for v in values], ensure_ascii=False)
            content = "；".join(contents)[:1000]
            conn.execute(
                "UPDATE memories SET content=?,value_json=?,source_conv_id=?,source_quote=?,version=version+1 "
                "WHERE id=?",
                (content, value, live[-1]["conv_id"], live[-1]["quote"], identity),
            )
            conn.execute("UPDATE memory_facts SET value_json=? WHERE memory_id=?", (value, identity))
        conn.execute(
            "DELETE FROM memory_fact_evidence WHERE (memory_id,source_id) IN "
            "(SELECT entity_id,source_id FROM memory_sources WHERE entity_kind='fact' AND conv_id=?)",
            (conv_id,),
        )

"""Evidence-backed discussion memories. Raw messages and saved user facts remain separate."""

import hashlib
import json
import re
import uuid
from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar

from claude_chat.db import deserialize_content
from claude_chat.memory_queries import low_information
from claude_chat.memory_store import attachment_text, fingerprint, now
from claude_chat.memory_vectors import safe_text

_source_cache = ContextVar("memory_source_cache", default=None)


@contextmanager
def source_cache(store, conn):
    """Operation-local read cache: never reuse evidence after a connection/transaction boundary."""
    token = _source_cache.set({"store": store, "conn": conn, "conversations": {}, "sources": {}})
    try:
        yield
    finally:
        _source_cache.reset(token)


def active_source_cache(store, conn):
    cache = _source_cache.get()
    return cache if cache and cache["store"] is store and cache["conn"] is conn else None


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def snapshot(messages):
    return digest(
        [{"role": m.get("role"), "content": m.get("content"), "aborted": bool(m.get("aborted"))} for m in messages]
    )


def message_text(message):
    content = message.get("content", "")
    if isinstance(content, str):
        return "" if attachment_text(content) else content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if not isinstance(block, dict) or attachment_text(block):
            continue
        if block.get("type") == "text":
            value = block.get("text", block.get("content", ""))
            if isinstance(value, str):
                parts.append(value)
    return "\n".join(parts)


def source_rows(conv):
    """Stable references survive SQLite message rewrites; no attachment bodies or reasoning are sent."""
    occurrences, rows, calls = Counter(), [], {}
    stamp = snapshot(conv["messages"])
    for position, message in enumerate(conv["messages"]):
        role = message.get("role")
        if role not in {"user", "assistant", "tool"} or message.get("aborted"):
            continue
        content_hash = digest(message.get("content", ""))
        key = (role, content_hash)
        occurrence = occurrences[key]
        occurrences[key] += 1
        source_id = digest([conv["id"], role, content_hash, occurrence])
        content = message.get("content")
        groups, results = {}, []

        def record_call(block):
            identity = block.get("id") if block.get("type") in {"tool_use", "server_tool_use"} else block.get("call_id")
            name = block.get("name")
            if role != "assistant" or not isinstance(identity, str) or not identity or not isinstance(name, str):
                return
            # Duplicate call IDs cannot silently change the evidence identity.
            calls[identity] = name if identity not in calls else None

        def record_result(block):
            identity = block.get("tool_use_id", block.get("call_id", message.get("tool_call_id", "")))
            name = calls.get(identity)
            if name in {"memory_overview", "search_memory", "read_memory_episode", "read_history_excerpt"}:
                return
            value = block.get("output", block.get("content", block.get("text", "")))
            if isinstance(value, list):
                value = "\n".join(
                    b["text"]
                    for b in value
                    if isinstance(b, dict) and isinstance(b.get("text"), str) and not attachment_text(b)
                )
            if not isinstance(value, str) or attachment_text(value):
                return
            execution = name in {"execute_code", "run_code", "code_execution", "sandbox_execute"}
            execution |= block.get("type") == "code_execution_result" and role in {"assistant", "tool"}
            results.append((identity, name or "unknown", execution and not block.get("is_error"), value))

        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict) or attachment_text(block):
                    continue
                kind = block.get("type")
                if kind in {"tool_use", "server_tool_use"}:
                    record_call(block)
                elif kind in {"tool_result", "code_execution_result"}:
                    record_result(block)
                elif kind == "text" and isinstance(block.get("text"), str):
                    groups.setdefault(role, []).append(block["text"])
                    native = block.get("_responses", {})
                    for item in native.get("output", []) if isinstance(native, dict) else []:
                        if isinstance(item, dict) and item.get("type") == "function_call":
                            record_call(item)
                        elif isinstance(item, dict) and item.get("type") == "function_call_output":
                            record_result(item)
        else:
            text = message_text(message)
            if role == "tool":
                record_result({"content": text})
            else:
                groups = {role: [text]}
        for evidence_role, fragments in groups.items():
            text = "\n".join(fragments)
            if not text.strip() or not safe_text(text) or low_information(text):
                continue
            rows.append(
                {
                    "source_id": source_id
                    if len(groups) == 1 or evidence_role == role
                    else digest([source_id, evidence_role]),
                    "conv_id": conv["id"],
                    "role": role,
                    "evidence_role": evidence_role,
                    "content_hash": content_hash,
                    "occurrence": occurrence,
                    "snapshot_hash": stamp,
                    "position": position,
                    "text": text,
                    "created_at": message.get("created_at", conv.get("updated_at", "")),
                }
            )
        for identity, name, execution, text in results:
            if not text.strip() or not safe_text(text) or low_information(text):
                continue
            rows.append(
                {
                    "source_id": digest([source_id, "tool", identity, name, execution]),
                    "conv_id": conv["id"],
                    "role": role,
                    "evidence_role": "tool",
                    "tool_name": name,
                    "tool_call_id": identity,
                    "verification_eligible": bool(execution),
                    "content_hash": content_hash,
                    "occurrence": occurrence,
                    "snapshot_hash": stamp,
                    "position": position,
                    "text": text,
                    "created_at": message.get("created_at", conv.get("updated_at", "")),
                }
            )
    return rows


def units(rows):
    # A long message is split rather than truncated; offsets locate its code/text in the original.
    return [
        {**row, "text": row["text"][offset : offset + 2400], "offset": offset}
        for row in rows
        for offset in range(0, len(row["text"]), 2400)
    ]


def evidence_map(rows):
    """Reconstruct only contiguous, consistent ranges; gaps cannot create verbatim evidence."""
    grouped = {}
    for row in rows:
        grouped.setdefault(row["source_id"], []).append(row)
    allowed = {}
    for identity, fragments in grouped.items():
        fragments.sort(key=lambda r: (r.get("offset", 0), -len(r["text"])))
        segments, end = [], -1
        for row in fragments:
            start, text = row.get("offset", 0), row["text"]
            if type(start) is not int or start < 0:
                raise ValueError("证据片段 offset 无效")
            if not segments or start > end:
                segments.append(text)
                end = start + len(text)
            else:
                overlap = min(end - start, len(text))
                if overlap and segments[-1][start - (end - len(segments[-1])) :][:overlap] != text[:overlap]:
                    raise ValueError("证据片段重叠内容不一致")
                segments[-1] += text[overlap:]
                end = max(end, start + len(text))
        allowed[identity] = {**fragments[0], "segments": segments, "text": "\n".join(segments)}
    return allowed


def claim_refs(claim, allowed):
    refs = claim.get("source_ids")
    if isinstance(refs, str):
        refs = [refs]
    if (
        not isinstance(refs, list)
        or not refs
        or len(refs) > 30
        or any(not isinstance(r, str) or r not in allowed for r in refs)
    ):
        raise ValueError("摘要引用了无效来源")
    return refs


def ordinary_kind(claim, allowed):
    kind = claim.get("kind")
    if kind is not None and (
        not isinstance(kind, str)
        or kind
        not in {
            "user_statement",
            "assistant_proposal",
            "user_confirmed",
            "tool_verified",
            "mixed_unverified",
            "tool_observation",
            "unverified_outcome",
        }
    ):
        raise ValueError("摘要 kind 应为有效证据类型文本")
    roles = {allowed[r]["evidence_role"] for r in claim_refs(claim, allowed)}
    if kind == "unverified_outcome":
        return kind
    if len(roles) > 1:
        return "mixed_unverified"
    return {"user": "user_statement", "assistant": "assistant_proposal", "tool": "tool_observation"}[next(iter(roles))]


def negative_outcome(text):
    match = re.search(
        r"(?:没(?:有)?|未|仍未|并未|尚未)(?:(?:真正|完全|彻底|成功|能够|得到|能|被)){0,2}(?:解决|修复|成功|生效)|仍然.{0,5}(?:出现|失败)|"
        r"(?:撤回|收回).{0,8}(?:结论|确认)|(?:not|never)\s+(?:fixed|resolved|working)|still\s+(?:broken|failing)",
        text,
        re.I,
    )
    if match and re.search(
        r"(?:现在|目前|后来).{0,6}(?:已经|已|终于).{0,3}(?:解决|修复|恢复正常)", text[match.end() :]
    ):
        return None
    return match


def failed_tool_result(text):
    return re.search(
        r"\b(?:error|exception|failure)\b|(?<!0 )\bfailed\b|"
        r"\b[1-9]\d*\s+(?:failed|failures|errors)\b|"
        r"(?:exit\s*(?:code|status)|退出码)\s*[:：=]?\s*-?[1-9]\d*\b|执行失败|验证失败",
        text,
        re.I,
    )


def affirmative_outcome(text, tool=False):
    """Ambiguous wishes, questions and predictions remain unverified, even with an exact quote."""
    if not isinstance(text, str) or negative_outcome(text):
        return False
    if re.search(
        r"[?？]|希望|但愿|愿望|假如|如果|假设|也许|可能|应该|预计|有望|能否|是否|会不会|能不能|"
        r"(?:hope|wish|if|might|maybe|could|would|should)\b",
        text,
        re.I,
    ):
        return False
    if tool:
        if failed_tool_result(text):
            return False
        return bool(
            re.search(
                r"exit\s*(?:code|status)\s*[:：=]?\s*0\b|\b(?:passed|success|successful|ok)\b|测试通过|验证通过|执行成功",
                text,
                re.I,
            )
        )
    return bool(
        re.search(
            r"(?:已经|现已|已|终于|成功).{0,8}(?:解决|修复|生效|恢复正常)|"
            r"(?:解决|修复|生效|恢复正常)(?:了|啦)|(?:现在|目前).{0,8}(?:正常|可以用了)|"
            r"确认.{0,8}(?:解决|正常|成功)|\b(?:fixed|resolved|working|worked|passed)\b",
            text,
            re.I,
        )
    )


def confirmation_context(text, quote):
    """Keep the enclosing sentence so a clipped quote cannot erase its wish/question qualifiers."""
    start = text.find(quote)
    if start < 0:
        return ""
    left = max(text.rfind(delimiter, 0, start) for delimiter in "。！；\n") + 1
    ends = [text.find(delimiter, start + len(quote)) for delimiter in "。！？；\n"]
    right = min((end + 1 for end in ends if end >= 0), default=len(text))
    return text[left:right]


class EpisodeStore:
    def __init__(self, store):
        self.store = store

    def conversation(self, conv_id, conn=None):
        if conn is None:
            with self.store.connect() as connection:
                return self.conversation(conv_id, connection)
        cache = active_source_cache(self.store, conn)
        if cache and conv_id in cache["conversations"]:
            return cache["conversations"][conv_id]
        row = conn.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
        if not row:
            return None
        messages = [
            {**dict(m), "content": deserialize_content(m["content"])}
            for m in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conv_id,))
        ]
        conv = {**dict(row), "messages": messages}
        if cache is not None:
            cache["conversations"][conv_id] = conv
        return conv

    def allowed(self, conv_id, scope=None, conn=None):
        if conn is None:
            with self.store.connect() as connection:
                return self.allowed(conv_id, scope, connection)
        if (
            not self.store.options(conn)["enabled"]
            or not conn.execute("SELECT 1 FROM conversations WHERE id=?", (conv_id,)).fetchone()
        ):
            return False
        privacy = conn.execute("SELECT * FROM conversation_privacy WHERE conv_id=?", (conv_id,)).fetchone()
        if privacy and any(privacy[k] for k in ("temporary", "memory_off", "exclude_history")):
            return False
        source_scope = privacy["scope"] if privacy else "global"
        return scope is None or source_scope in {"global", scope}

    def sources(self, conv, conn=None):
        if conn is None:
            with self.store.connect() as connection:
                return self.sources(conv, connection)
        cache = active_source_cache(self.store, conn)
        if cache is not None and conv["id"] in cache["sources"]:
            return cache["sources"][conv["id"]]
        blocked = {
            r[0] for r in conn.execute("SELECT source_id FROM memory_source_blocks WHERE conv_id=?", (conv["id"],))
        }
        result = [row for row in source_rows(conv) if row["source_id"] not in blocked]
        if cache is not None:
            cache["sources"][conv["id"]] = result
        return result

    def valid(self, row, conn):
        if row["origin"] == "external" and row["conv_id"].startswith("import:"):
            return self.store.options(conn)["enabled"]
        if not self.allowed(row["conv_id"], row["scope"], conn):
            return False
        privacy = conn.execute("SELECT scope FROM conversation_privacy WHERE conv_id=?", (row["conv_id"],)).fetchone()
        if (privacy[0] if privacy else "global") != row["scope"]:
            return False
        current = {r["source_id"]: r for r in self.sources(self.conversation(row["conv_id"], conn), conn)}
        try:
            payload = json.loads(row["payload"])
            if not self.required_sources(payload) or not self.required_sources(payload) <= current.keys():
                return False
            self.normalize(payload, list(current.values()))
        except (ValueError, TypeError, KeyError):
            return False
        return True

    def list(self, scope="global", conn=None):
        if conn is None:
            with self.store.connect() as connection:
                return self.list(scope, connection)
        if not self.store.options(conn)["enabled"] or not self.store.options(conn)["episodes_enabled"]:
            return []
        return [
            {**dict(row), **json.loads(row["payload"])}
            for row in conn.execute(
                "SELECT * FROM memory_episodes WHERE scope IN ('global',?) ORDER BY updated_at DESC", (scope,)
            )
            if self.valid(row, conn)
        ]

    def read(self, episode_id, scope):
        return next((e for e in self.list(scope) if e["id"] == episode_id), None)

    def validate_claim(self, claim, allowed, kind):
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str) or not claim["text"].strip():
            raise ValueError("摘要断言需要文本与原文证据")
        refs = claim_refs(claim, allowed)
        roles = {allowed[r]["evidence_role"] for r in refs}
        if kind in {"user_statement", "user_confirmed"} and "user" not in roles:
            raise ValueError("用户事实或确认需要用户证据")
        if kind == "tool_verified" and "tool" not in roles:
            raise ValueError("工具确认需要工具结果")
        if kind == "assistant_proposal" and "assistant" not in roles:
            raise ValueError("助手建议需要助手来源")
        quote = claim.get("quote", "")
        if quote is None and kind not in {"user_confirmed", "tool_verified"}:
            quote = ""
        if not isinstance(quote, str):
            raise ValueError("摘要引用 quote 应为原文文本")
        if kind in {"user_confirmed", "tool_verified"} and (
            not isinstance(quote, str)
            or not quote.strip()
            or not any(
                any(quote in segment for segment in allowed[r].get("segments", [allowed[r]["text"]]))
                and allowed[r]["evidence_role"] == ("user" if kind == "user_confirmed" else "tool")
                for r in refs
            )
        ):
            raise ValueError("已确认结论需要逐字确认依据")
        if kind == "tool_verified" and not any(
            allowed[r].get("verification_eligible") and quote in allowed[r]["text"] for r in refs
        ):
            raise ValueError("记忆读取、网页查询或未知工具不能成为独立验证证据")
        if len(claim["text"]) > 1000 or not safe_text(claim["text"] + str(quote)):
            raise ValueError("摘要文本过长或含敏感内容")
        return {
            "text": claim["text"].strip(),
            "source_ids": list(dict.fromkeys(refs)),
            "kind": kind,
            "quote": quote[:1000],
        }

    def normalize(self, candidate, rows, repair=False):
        if not isinstance(candidate, dict):
            raise ValueError("话题格式无效")
        topic = candidate.get("topic")
        if not isinstance(topic, str) or not 1 <= len(topic.strip()) <= 160 or not safe_text(topic):
            raise ValueError("话题标题无效")
        allowed = evidence_map(rows)
        data = {"topic": topic.strip(), "status": "unresolved"}
        for field in ("problem", "findings", "proposed_solutions", "open_questions"):
            values = candidate.get(field, [])
            if values is None:
                values = []
            if isinstance(values, dict):
                values = [values]
            if not isinstance(values, list) or len(values) > 12:
                raise ValueError(f"摘要字段 {field} 应为有界证据数组，实际为 {type(values).__name__}")
            data[field] = []
            for index, claim in enumerate(values):
                if not isinstance(claim, dict):
                    raise ValueError(f"摘要字段 {field} 的条目应包含 text/source_ids/kind")
                try:
                    kind = ordinary_kind(claim, allowed)
                    data[field].append(self.validate_claim(claim, allowed, kind))
                except ValueError as exc:
                    raise ValueError(f"字段 {field} 条目 {index + 1}：{exc}") from exc
        conclusion = candidate.get("confirmed_outcome")
        data["confirmed_outcome"] = None
        if conclusion:
            if not isinstance(conclusion, dict):
                raise ValueError("摘要字段 confirmed_outcome 应为证据对象或 null")
            kind = conclusion.get("kind")
            # Validate structure and references before attempting a safe downgrade.
            ordinary = self.validate_claim(conclusion, allowed, ordinary_kind(conclusion, allowed))
            try:
                if not isinstance(kind, str) or kind not in {"user_confirmed", "tool_verified"}:
                    raise ValueError("建议不能自动升级为已确认结论")
                verified = self.validate_claim(conclusion, allowed, kind)
                if negative_outcome(conclusion.get("quote") or ""):
                    raise ValueError("否定证据不能作为成功确认")
                if not affirmative_outcome(conclusion.get("quote") or "", tool=kind == "tool_verified"):
                    raise ValueError("愿望、疑问或含糊描述不能作为成功确认")
                if kind == "user_confirmed" and not any(
                    allowed[ref]["evidence_role"] == "user"
                    and affirmative_outcome(confirmation_context(allowed[ref]["text"], conclusion["quote"]))
                    for ref in verified["source_ids"]
                ):
                    raise ValueError("截短引用不能移除原文的愿望、疑问或假设语境")
                if kind == "tool_verified" and not any(
                    allowed[ref].get("verification_eligible")
                    and conclusion["quote"] in allowed[ref]["text"]
                    and affirmative_outcome(allowed[ref]["text"], tool=True)
                    for ref in verified["source_ids"]
                ):
                    raise ValueError("执行输出包含失败，不能截短引用后宣称成功")
                data["confirmed_outcome"] = verified
                data["status"] = "resolved"
            except ValueError:
                if not repair:
                    raise
                ordinary["kind"] = (
                    "assistant_proposal" if ordinary["kind"] == "assistant_proposal" else "unverified_outcome"
                )
                field = "proposed_solutions" if ordinary["kind"] == "assistant_proposal" else "findings"
                if len(data[field]) >= 12:
                    raise ValueError(f"字段 {field} 过多，无法安全保留未验证结果")
                data[field].append(ordinary)
                data["validation_warnings"] = ["确认依据不足，已保留为未验证内容"]
        update = candidate.get("outcome_update", {"operation": "KEEP"})
        if (
            not isinstance(update, dict)
            or not isinstance(update.get("operation"), str)
            or update["operation"] not in {"KEEP", "RETRACT", "REPLACE"}
        ):
            raise ValueError("outcome_update.operation 应为 KEEP/RETRACT/REPLACE")
        data["outcome_update"] = {"operation": "KEEP"}
        if update["operation"] == "RETRACT":
            refs = claim_refs(update, allowed)
            roles = {allowed[r]["evidence_role"] for r in refs}
            if roles not in ({"user"}, {"tool"}):
                raise ValueError("撤回结论需要用户或工具原文")
            proof = self.validate_claim(
                {**update, "text": "撤回先前成功结论"},
                allowed,
                "user_confirmed" if roles == {"user"} else "tool_verified",
            )
            if not (negative_outcome(proof["quote"]) or (roles == {"tool"} and failed_tool_result(proof["quote"]))):
                raise ValueError("撤回结论需要明确撤回或失败依据")
            data["outcome_update"] = {**proof, "operation": "RETRACT"}
            data["confirmed_outcome"], data["status"] = None, "unresolved"
        elif data["confirmed_outcome"]:
            data["outcome_update"] = {**data["confirmed_outcome"], "operation": "REPLACE"}
        elif update["operation"] == "REPLACE":
            if not repair:
                raise ValueError("REPLACE 需要有效的确认结论")
        if data["outcome_update"]["operation"] == "KEEP":
            relevant = {
                ref
                for field in ("problem", "findings", "open_questions")
                for claim in data[field]
                if negative_outcome(claim["text"])
                or (claim["kind"] == "unverified_outcome" and negative_outcome(claim["quote"]))
                for ref in claim["source_ids"]
            }
            for ref in relevant:
                row = allowed[ref]
                if row["evidence_role"] == "user":
                    for segment in row.get("segments", [row["text"]]):
                        match = negative_outcome(segment)
                        if match:
                            data["outcome_update"] = {
                                "operation": "RETRACT",
                                "text": "用户报告问题未解决",
                                "source_ids": [ref],
                                "kind": "user_statement",
                                "quote": match.group(0),
                            }
        if not any(
            data[f] for f in ("problem", "findings", "proposed_solutions", "open_questions", "confirmed_outcome")
        ):
            raise ValueError("空话题不能仅凭标题保存")
        return data

    @staticmethod
    def required_sources(payload):
        claims = [
            c
            for field in ("problem", "findings", "proposed_solutions", "open_questions")
            for c in payload.get(field, [])
        ]
        claims += [
            c for c in (payload.get("confirmed_outcome"), payload.get("outcome_update")) if c and c.get("source_ids")
        ]
        return {ref for claim in claims for ref in claim["source_ids"]}

    def sync_sources(self, identity, conv, payload, conn):
        current = {r["source_id"]: r for r in self.sources(conv, conn)}
        required = self.required_sources(payload)
        if not required <= current.keys():
            raise ValueError("摘要来源已失效")
        conn.execute("DELETE FROM memory_sources WHERE entity_kind='episode' AND entity_id=?", (identity,))
        for ref in required:
            row = current[ref]
            conn.execute(
                "INSERT INTO memory_sources VALUES ('episode',?,?,?,?,?,?,?,?)",
                (
                    identity,
                    ref,
                    conv["id"],
                    row["role"],
                    row["content_hash"],
                    row["occurrence"],
                    row["snapshot_hash"],
                    row["position"],
                ),
            )
        dates = [current[ref].get("created_at", conv["updated_at"]) for ref in required]
        payload["period_start"], payload["period_end"] = (
            min(dates or [conv["updated_at"]]),
            max(dates or [conv["updated_at"]]),
        )

    def persist_batch(self, conv_id, source_hash, rows, candidates, epoch, job_id=None, cursor=None, usage=None):
        if not isinstance(candidates, list) or len(candidates) > 6:
            raise ValueError("话题数量无效")
        normalized = []
        for index, candidate in enumerate(candidates):
            try:
                normalized.append(self.normalize(candidate, rows, repair=True))
            except ValueError as exc:
                raise ValueError(f"话题 {index + 1}：{exc}") from exc
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            live = self.conversation(conv_id, conn)
            if not live or snapshot(live["messages"]) != source_hash or self.store.options(conn)["epoch"] != epoch:
                raise ValueError("来源或隐私设置已改变")
            if not self.allowed(conv_id, conn=conn):
                raise ValueError("来源不允许整理")
            if job_id:
                job = conn.execute("SELECT status FROM memory_jobs WHERE id=?", (job_id,)).fetchone()
                if not job or job[0] != "running":
                    raise ValueError("整理任务已停止")
            scope = self.store.privacy(conv_id)["scope"]
            changed = []
            for data in normalized:
                topic_key = fingerprint(data["topic"])
                blocked = fingerprint(conv_id + ":" + data["topic"])
                if conn.execute("SELECT 1 FROM memory_episode_blocks WHERE fingerprint=?", (blocked,)).fetchone():
                    continue
                old = conn.execute(
                    "SELECT * FROM memory_episodes WHERE conv_id=? AND topic_key=?", (conv_id, topic_key)
                ).fetchone()
                if old and old["origin"] == "manual":
                    continue
                identity, version = (old["id"], old["version"] + 1) if old else (uuid.uuid4().hex, 1)
                if old:
                    self.archive(old, conn)
                if old and self.valid(old, conn):
                    previous = json.loads(old["payload"])
                    for field in ("problem", "findings", "proposed_solutions", "open_questions"):
                        seen = {}
                        for claim in previous[field] + data[field]:
                            seen[digest(claim)] = claim
                        data[field] = list(seen.values())[-12:]
                    current = {r["source_id"]: r for r in self.sources(live, conn)}

                    def rank(event):
                        return max(
                            (current[r]["position"] for r in (event or {}).get("source_ids", []) if r in current),
                            default=-1,
                        )

                    old_event = previous.get("outcome_update") or previous.get("confirmed_outcome")
                    update = data["outcome_update"]
                    if update["operation"] == "KEEP" or rank(update) < rank(old_event):
                        data["confirmed_outcome"] = previous["confirmed_outcome"]
                        data["outcome_update"] = previous.get("outcome_update", {"operation": "KEEP"})
                    data["status"] = "resolved" if data["confirmed_outcome"] else "unresolved"
                else:
                    conn.execute("DELETE FROM memory_sources WHERE entity_kind='episode' AND entity_id=?", (identity,))
                conn.execute(
                    "INSERT OR REPLACE INTO memory_episodes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        conv_id,
                        topic_key,
                        data["topic"],
                        scope,
                        json.dumps(data, ensure_ascii=False),
                        source_hash,
                        version,
                        "automatic",
                        live["updated_at"],
                    ),
                )
                self.sync_sources(identity, live, data, conn)
                conn.execute(
                    "UPDATE memory_episodes SET payload=? WHERE id=?", (json.dumps(data, ensure_ascii=False), identity)
                )
                changed.append(identity)
            if changed:
                conn.execute("DELETE FROM memory_overviews")
                conn.execute("DELETE FROM memory_context")
            if job_id:
                usage = usage or {}
                conn.execute(
                    "UPDATE memory_jobs SET cursor=?,input_tokens=input_tokens+?,"
                    "output_tokens=output_tokens+?,updated_at=? "
                    "WHERE id=? AND status='running'",
                    (cursor, usage.get("input_tokens", 0), usage.get("output_tokens", 0), now(), job_id),
                )
            return changed

    def edit(self, episode_id, scope, changes, expected_version=None):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            episode = next((e for e in self.list(scope, conn) if e["id"] == episode_id), None)
            if not episode:
                raise ValueError("话题不存在或不可访问")
            if expected_version is not None and (
                type(expected_version) is not int or episode["version"] != expected_version
            ):
                raise ValueError("摘要版本冲突：来源或摘要已更新；草稿已保留，请重新读取后对照修改")
            self.archive(episode, conn)
            if episode["origin"] == "external":
                payload = self.external_payload(changes)
            else:
                conv = self.conversation(episode["conv_id"], conn)
                rows = self.sources(conv, conn)
                payload = self.normalize(changes, rows)
                self.sync_sources(episode_id, conv, payload, conn)
            conn.execute(
                "UPDATE memory_episodes SET topic=?,payload=?,origin=?,version=version+1,updated_at=? WHERE id=?",
                (
                    payload["topic"],
                    json.dumps(payload, ensure_ascii=False),
                    "external" if episode["origin"] == "external" else "manual",
                    now(),
                    episode_id,
                ),
            )
            conn.execute("DELETE FROM memory_overviews")
            conn.execute("DELETE FROM memory_context")
            self.store.bump_epoch(conn, purge=False)
        return self.read(episode_id, scope)

    def archive(self, episode, conn):
        if episode["origin"] != "external":
            payload = json.loads(episode["payload"])
            conv = self.conversation(episode["conv_id"], conn)
            if conv and self.required_sources(payload) <= {r["source_id"] for r in self.sources(conv, conn)}:
                self.sync_sources(episode["id"], conv, payload, conn)
        sources = [
            dict(s)
            for s in conn.execute(
                "SELECT * FROM memory_sources WHERE entity_kind='episode' AND entity_id=?", (episode["id"],)
            )
        ]
        payload = episode["payload"]
        conn.execute(
            "INSERT OR IGNORE INTO memory_episode_versions VALUES (?,?,?,?,?,?)",
            (
                episode["id"],
                episode["version"],
                payload,
                episode["origin"],
                json.dumps(sources, ensure_ascii=False),
                episode["updated_at"],
            ),
        )
        conn.execute(
            "DELETE FROM memory_episode_versions WHERE episode_id=? AND version NOT IN "
            "(SELECT version FROM memory_episode_versions WHERE episode_id=? ORDER BY version DESC LIMIT 50)",
            (episode["id"], episode["id"]),
        )

    def versions(self, episode_id, scope):
        episode = self.read(episode_id, scope)
        if not episode:
            raise ValueError("话题不存在或不可访问")
        with self.store.connect() as conn:
            results = []
            for row in conn.execute(
                "SELECT * FROM memory_episode_versions WHERE episode_id=? ORDER BY version DESC", (episode_id,)
            ):
                refs = json.loads(row["sources"])
                if any(
                    not self.allowed(s["conv_id"], scope, conn)
                    or s["source_id"]
                    not in {r["source_id"] for r in self.sources(self.conversation(s["conv_id"], conn), conn)}
                    for s in refs
                ):
                    continue
                payload = json.loads(row["payload"])
                if row["origin"] != "external":
                    try:
                        self.normalize(payload, self.sources(self.conversation(episode["conv_id"], conn), conn))
                    except (ValueError, TypeError, KeyError):
                        continue
                results.append({**dict(row), "payload": payload, "sources": refs})
            return results

    @staticmethod
    def external_payload(item):
        if not isinstance(item, dict):
            raise ValueError("话题导入条目无效")
        topic = item.get("topic")
        if not isinstance(topic, str) or not 1 <= len(topic) <= 160 or not safe_text(topic):
            raise ValueError("话题导入标题无效")
        payload = {"topic": topic, "confirmed_outcome": None, "status": "unresolved"}
        for field in ("problem", "findings", "proposed_solutions", "open_questions"):
            claims = item.get(field, [])
            if not isinstance(claims, list) or len(claims) > 12:
                raise ValueError("话题导入断言无效")
            payload[field] = []
            for claim in claims:
                text = claim.get("text") if isinstance(claim, dict) else None
                if not isinstance(text, str) or not 1 <= len(text) <= 1000 or not safe_text(text):
                    raise ValueError("话题导入正文无效")
                payload[field].append({"text": text, "kind": "external_unverified", "source_ids": []})
        outcome = item.get("confirmed_outcome")
        if outcome:
            text = outcome.get("text") if isinstance(outcome, dict) else None
            if not isinstance(text, str) or not 1 <= len(text) <= 1000 or not safe_text(text):
                raise ValueError("话题导入结果无效")
            payload["findings"] = payload["findings"][:11] + [
                {
                    "text": ("外部文件报告的结果（未验证）：" + text)[:1000],
                    "kind": "external_unverified",
                    "source_ids": [],
                }
            ]
        if not any(payload[k] for k in ("problem", "findings", "proposed_solutions", "open_questions")):
            raise ValueError("空话题不能仅凭标题保存")
        return payload

    def import_external(self, items, conn):
        """Imported evidence is data, never a live source or confirmed result."""
        if not isinstance(items, list) or len(items) > 500:
            raise ValueError("最多导入500个话题")
        from claude_chat.memory_resolver import typed_fields

        for item in items:
            payload = self.external_payload(item)
            topic = payload["topic"]
            scope = typed_fields({"scope": item.get("scope", "global")})["scope"]
            encoded = json.dumps(payload, ensure_ascii=False)
            identity = digest([scope, encoded])
            conn.execute(
                "INSERT OR IGNORE INTO memory_episodes "
                "(id,conv_id,topic_key,topic,scope,payload,source_hash,origin,updated_at) "
                "VALUES (?,?,?,?,?,?,?,'external',?)",
                ("external-" + identity, "import:" + identity, digest(topic), topic, scope, encoded, identity, now()),
            )
            history = item.get("history_versions", [])
            if not isinstance(history, list) or len(history) > 50:
                raise ValueError("每个话题最多导入50个历史版本")
            for version in history:
                if (
                    not isinstance(version, dict)
                    or type(version.get("version")) is not int
                    or not 1 <= version["version"] <= 100000
                ):
                    raise ValueError("话题历史版本无效")
                previous = self.external_payload(version.get("payload"))
                conn.execute(
                    "INSERT OR IGNORE INTO memory_episode_versions VALUES (?,?,?,?,?,?)",
                    (
                        "external-" + identity,
                        version["version"],
                        json.dumps(previous, ensure_ascii=False),
                        "external",
                        "[]",
                        now(),
                    ),
                )
            if history:
                conn.execute(
                    "UPDATE memory_episodes SET version=max(version,?) WHERE id=?",
                    (max(v["version"] for v in history) + 1, "external-" + identity),
                )

    def forget(self, episode_id, scope):
        with self.store.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = next((e for e in self.list(scope, conn) if e["id"] == episode_id), None)
            if not row:
                raise ValueError("话题不存在或不可访问")
            if row["origin"] != "external":
                self.sync_sources(episode_id, self.conversation(row["conv_id"], conn), row, conn)
            conn.execute(
                "INSERT OR IGNORE INTO memory_episode_blocks VALUES (?,?)",
                (fingerprint(row["conv_id"] + ":" + row["topic"]), now()),
            )
            conn.execute("DELETE FROM memory_episodes WHERE id=?", (episode_id,))
            conn.execute("DELETE FROM memory_episode_versions WHERE episode_id=?", (episode_id,))
            conn.execute(
                "INSERT OR IGNORE INTO memory_source_blocks SELECT conv_id,source_id,? FROM memory_sources "
                "WHERE entity_kind='episode' AND entity_id=?",
                (now(), episode_id),
            )
            conn.execute("DELETE FROM memory_sources WHERE entity_kind='episode' AND entity_id=?", (episode_id,))
            # Only the supporting messages are excluded, preserving independently sourced topics in this conversation.
            conn.execute("DELETE FROM memory_context")
            self.store.bump_epoch(conn)
        return True

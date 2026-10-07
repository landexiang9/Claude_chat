"""Typed user facts, explicit conflict decisions and temporal versions."""

import json
import re
import uuid

ALIASES = {
    "language": "user.language",
    "os": "user.os",
    "operating_system": "user.os",
    "preferred_shell": "user.preferred_shell",
    "shell": "user.preferred_shell",
    "response_style": "user.response_style",
}


def typed_fields(data, previous=None):
    old = dict(previous) if previous else {}
    subject = data.get("subject", old.get("subject", ""))
    key, content = str(data.get("key", "")), str(data.get("content", ""))
    value = data.get("value", json.loads(old.get("value_json", "null")))
    if not subject:
        subject = ALIASES.get(key, "")
        if re.search(r"(?:我|用户|使用|系统|换).{0,20}Windows\s*(?:10|11)", content, re.I):
            subject = "user.os"
        elif re.search(r"(?:偏好|优先|希望|常用).{0,20}(?:PowerShell|bash|zsh)", content, re.I):
            subject = "user.preferred_shell"
    subject = ALIASES.get(subject, subject)
    if not isinstance(subject, str) or len(subject) > 120 or (subject and not re.fullmatch(r"[\w.:-]+", subject)):
        raise ValueError("结构字段应为不超过 120 字符的标识，例如 user.os")
    if "value" not in data and subject:
        if subject == "user.os" and (match := re.search(r"Windows\s*(10|11)", content, re.I)):
            change = re.search(r"(?:改用|换到|换成|现在(?:使用|用)?).{0,10}Windows\s*(10|11)", content, re.I)
            versions = set(re.findall(r"Windows\s*(10|11)", content, re.I))
            value = "Windows " + (change[1] if change else match[1]) if change or len(versions) == 1 else content
        elif subject == "user.preferred_shell" and (match := re.search(r"PowerShell|bash|zsh", content, re.I)):
            value = match[0]
        elif "content" in data and (not previous or old.get("content") != content):
            value = content
    scope = data.get("scope", old.get("scope", "global"))
    if not isinstance(scope, str) or not 1 <= len(scope) <= 120:
        raise ValueError("记忆作用域应为 global 或项目标识，最多 120 字符")
    numbers = {}
    for field, default in (("confidence", 1.0), ("importance", 0.7)):
        number = data.get(field, old.get(field, default))
        if type(number) not in (int, float) or not 0 <= number <= 1:
            raise ValueError("置信度与重要度应在 0–1 之间")
        numbers[field] = number
    encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    if len(encoded) > 2000:
        raise ValueError("结构化值不能超过 2000 字符")
    return {"subject": subject, "scope": scope, "value_json": encoded, **numbers}


def find_previous(conn, fields):
    if not fields["subject"]:
        return None
    return conn.execute(
        "SELECT * FROM memories WHERE subject=? AND scope=? ORDER BY updated_at DESC LIMIT 1",
        (fields["subject"], fields["scope"]),
    ).fetchone()


def relation(previous, content, fields, requested=""):
    if not previous:
        return "NEW"
    if (
        previous["content"] == content or (fields["subject"] and previous["value_json"] == fields["value_json"])
    ) and all(previous[key] == fields[key] for key in ("subject", "value_json", "scope")):
        return "SAME"
    if requested == "EXTEND":
        return "EXTEND"
    if requested == "CONTRADICT":
        return "CONTRADICT"
    if fields["subject"]:
        return "REPLACE"
    return "CONTRADICT"


def audit(conn, memory_id, operation, kind, reason, stamp):
    conn.execute(
        "INSERT INTO memory_audit (memory_id,operation,relation,reason,created_at) VALUES (?,?,?,?,?)",
        (memory_id, operation, kind, reason, stamp),
    )
    # Audit contains decisions, not sensitive source text. Keep bounded diagnostics.
    conn.execute("DELETE FROM memory_audit WHERE id NOT IN (SELECT id FROM memory_audit ORDER BY id DESC LIMIT 2000)")


def pending(conn, previous, data, kind, stamp):
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False)
    memory_id = previous["id"] if previous else None
    found = conn.execute(
        "SELECT id FROM memory_conflicts WHERE memory_id IS ? AND candidate=? AND status='pending'",
        (memory_id, payload),
    ).fetchone()
    if not found:
        conn.execute(
            "INSERT INTO memory_conflicts (id,memory_id,candidate,relation,created_at) VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, memory_id, payload, kind, stamp),
        )
        audit(conn, memory_id, "IGNORE", kind, "需要用户确认：手动记忆或证据置信度不足", stamp)


def save_version(conn, previous, stamp):
    conn.execute(
        "INSERT INTO memory_versions (memory_id,version,subject,value_json,content,scope,valid_from,"
        "valid_until,source_conv_id,source_quote) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            previous["id"],
            previous["version"],
            previous["subject"],
            previous["value_json"],
            previous["content"],
            previous["scope"],
            previous["valid_from"] or previous["created_at"],
            stamp,
            previous["source_conv_id"],
            previous["source_quote"],
        ),
    )
    conn.execute(
        "DELETE FROM memory_versions WHERE memory_id=? AND id NOT IN (SELECT id FROM memory_versions "
        "WHERE memory_id=? ORDER BY version DESC LIMIT 50)",
        (previous["id"], previous["id"]),
    )


def persist_fields(conn, memory_id, fields, previous, kind, stamp, automatic):
    changed = previous and kind != "SAME"
    if changed:
        save_version(conn, previous, stamp)
    version = (previous["version"] + int(bool(changed))) if previous else 1
    valid_from = stamp if changed or not previous else previous["valid_from"]
    conn.execute(
        "UPDATE memories SET subject=?,value_json=?,scope=?,confidence=?,importance=?,version=?,valid_from=? "
        "WHERE id=?",
        (
            fields["subject"],
            fields["value_json"],
            fields["scope"],
            fields["confidence"],
            fields["importance"],
            version,
            valid_from,
            memory_id,
        ),
    )
    conn.execute("DELETE FROM memory_facts WHERE memory_id=?", (memory_id,))
    conn.execute(
        """
        INSERT INTO memory_facts SELECT id,subject,scope,value_json,confidence,valid_from
        FROM memories WHERE id=? AND subject<>'' AND enabled=1
    """,
        (memory_id,),
    )
    conn.execute(
        "DELETE FROM memory_vectors WHERE document_id IN (SELECT id FROM memory_documents WHERE ref_id=?)", (memory_id,)
    )
    conn.execute("DELETE FROM memory_documents WHERE ref_id=?", (memory_id,))
    conn.execute("DELETE FROM memory_context")
    audit(
        conn,
        memory_id,
        "ADD" if not previous else "IGNORE" if kind == "SAME" else "MERGE" if kind == "EXTEND" else "UPDATE",
        kind,
        "用户证据" if automatic else "用户手动操作",
        stamp,
    )

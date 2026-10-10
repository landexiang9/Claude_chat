"""Request-level accounting: reported tokens only, never inferred charges or source text."""

import uuid

from claude_chat.memory_store import now

STAGES = {"facts", "episode", "merge", "router", "plan"}
STATUSES = {"running", "success", "error", "timeout", "cancelled", "truncated", "interrupted"}


class MemoryUsage:
    def __init__(self, store):
        self.store = store

    def begin(self, stage, platform, model, conv_id="", scope="global", job_id=""):
        if stage not in STAGES:
            raise ValueError("未知记忆用量阶段")
        identity = uuid.uuid4().hex
        with self.store.connect() as conn:
            conn.execute(
                "INSERT INTO memory_usage "
                "(id,stage,category,platform,model,conv_id,scope,job_id,status,started_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,'running',?,?)",
                (
                    identity,
                    stage,
                    "chat" if stage in {"router", "plan"} else "background",
                    str(platform)[:200],
                    str(model)[:200],
                    str(conv_id)[:120],
                    str(scope)[:120],
                    str(job_id)[:120],
                    now(),
                    now(),
                ),
            )
        return identity

    def finish(self, identity, status, usage):
        if status not in STATUSES:
            raise ValueError("未知记忆请求状态")
        reported = {
            key: value
            for key, value in (usage or {}).items()
            if key in {"input_tokens", "output_tokens"} and type(value) is int and value >= 0
        }
        with self.store.connect() as conn:
            conn.execute(
                "UPDATE memory_usage SET status=?,input_tokens=?,output_tokens=?,updated_at=? WHERE id=?",
                (status, reported.get("input_tokens"), reported.get("output_tokens"), now(), identity),
            )

    def summary(self):
        with self.store.connect() as conn:
            groups = [
                dict(row)
                for row in conn.execute(
                    "SELECT category,stage,count(*) AS requests,coalesce(sum(input_tokens),0) AS input_tokens,"
                    "coalesce(sum(output_tokens),0) AS output_tokens,"
                    "sum(input_tokens IS NULL OR output_tokens IS NULL) AS unknown_requests,"
                    "sum(status='running') AS running_requests,"
                    "sum(status<>'success' AND status<>'running') AS failed_requests "
                    "FROM memory_usage GROUP BY category,stage ORDER BY category,stage"
                )
            ]
            requests = [
                dict(row) for row in conn.execute("SELECT * FROM memory_usage ORDER BY started_at DESC LIMIT 100")
            ]
        categories = {}
        for category in ("background", "chat"):
            categories[category] = {
                field: sum(group[field] for group in groups if group["category"] == category)
                for field in (
                    "requests",
                    "input_tokens",
                    "output_tokens",
                    "unknown_requests",
                    "running_requests",
                    "failed_requests",
                )
            }
        return {
            "categories": categories,
            "stages": groups,
            "recent_requests": requests,
            "reported_only": True,
            "scope": "workspace",
            "historical_usage_available": False,
            "chat_totals_overlap": True,
        }
